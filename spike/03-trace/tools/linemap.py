"""Address -> source file:line mapping from DWARF line tables (app zone).

Precomputes a sorted table once per ELF from `objdump --dwarf=decodedline`
(binutils, GPLv3, run as a separate process), then answers lookups with a
binary search. Each address is also classified by where its source lives:

  sketch   - the user's sketch folder
  arduino  - the Arduino-ESP32 core and its bundled libraries
  idf      - ESP-IDF components (precompiled in path A, from source in path B)
  toolchain- libc / libgcc / libstdc++ shipped with the compiler
  rom      - mask ROM (0x40000000-0x4006ffff on ESP32); no line info, symbols only
  nodebug  - in the app image but no line info
"""

from __future__ import annotations

import bisect
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from paths import ROM_ELF, tool

ROW = re.compile(r"^(\S+)\s+(\d+|-)\s+(0x[0-9a-fA-F]+)(?:\s+(\d+))?(?:\s+x)?\s*$")
ROM_RANGE = {"esp32": (0x40000000, 0x40070000), "esp32s3": (0x40000000, 0x40060000)}


def classify_path(path: str, sketch_dir: str | None) -> str:
    p = path.replace("\\", "/").lower()
    if sketch_dir and p.startswith(sketch_dir.replace("\\", "/").lower().rstrip("/") + "/"):
        return "sketch"
    if "/packages/esp32/hardware/" in p or "/arduino-esp32/" in p or "espressif__arduino-esp32" in p:
        return "arduino"
    if "/esp-idf/components/" in p or "/managed_components/" in p or "/components/" in p:
        return "idf"
    if "crosstool-ng" in p or "/newlib/" in p or "libstdc++" in p or "libgcc" in p or "/xtensa-esp-elf/" in p:
        return "toolchain"
    return "other"


@dataclass(frozen=True)
class Loc:
    file: str  # full path as recorded in DWARF
    line: int
    cat: str

    @property
    def short(self) -> str:
        return f"{Path(self.file.replace(chr(92), '/')).name}:{self.line}"


class LineMap:
    def __init__(self, elf: Path, chip: str = "esp32", sketch_dir: str | None = None):
        self.elf = Path(elf)
        self.chip = chip
        self.sketch_dir = sketch_dir
        self._addrs: list[int] = []
        self._locs: list[Loc | None] = []  # None = end of a sequence (gap)
        self._load()
        self._syms = Symbols(self.elf, chip)
        self._rom = Symbols(ROM_ELF[chip], chip) if ROM_ELF.get(chip) and ROM_ELF[chip].exists() else None

    def _load(self) -> None:
        out = subprocess.run([str(tool(self.chip, "objdump")), "--dwarf=decodedline", str(self.elf)],
                             capture_output=True, text=True, errors="replace", check=True).stdout
        rows: dict[int, Loc | None] = {}
        cur_file = None
        cu_dir = None
        cache: dict[tuple[str, int], Loc] = {}
        cat_cache: dict[str, str] = {}
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if not ln or ln.startswith(("File name", "Contents of")):
                continue
            if ln.endswith(":") and not ROW.match(ln):
                name = ln[4:-1] if ln.startswith("CU: ") else ln[:-1]
                is_abs = name.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", name)
                if is_abs:
                    cur_file = name
                    cu_dir = str(Path(name.replace("\\", "/")).parent)
                else:
                    cur_file = f"{cu_dir}/{name}" if cu_dir else name
                continue
            m = ROW.match(ln)
            if not m or cur_file is None:
                continue
            addr = int(m.group(3), 16)
            if m.group(2) == "-":
                rows.setdefault(addr, None)  # end of sequence: only if nothing starts here
                continue
            line = int(m.group(2))
            cat = cat_cache.get(cur_file)
            if cat is None:
                cat = cat_cache[cur_file] = classify_path(cur_file, self.sketch_dir)
            key = (cur_file, line)
            loc = cache.get(key)
            if loc is None:
                loc = cache[key] = Loc(cur_file, line, cat)
            rows[addr] = loc  # the last row at an address is the one in effect
        for a in sorted(rows):
            self._addrs.append(a)
            self._locs.append(rows[a])

    def lookup(self, addr: int) -> Loc | None:
        i = bisect.bisect_right(self._addrs, addr) - 1
        if i < 0:
            return None
        return self._locs[i]

    def category(self, addr: int) -> str:
        lo, hi = ROM_RANGE.get(self.chip, (0, 0))
        if lo <= addr < hi:
            return "rom"
        loc = self.lookup(addr)
        return loc.cat if loc else "nodebug"

    def symbol(self, addr: int) -> str | None:
        lo, hi = ROM_RANGE.get(self.chip, (0, 0))
        if lo <= addr < hi and self._rom:
            return self._rom.lookup(addr)
        return self._syms.lookup(addr)

    def describe(self, addr: int) -> str:
        cat = self.category(addr)
        loc = self.lookup(addr) if cat != "rom" else None
        sym = self.symbol(addr) or "?"
        where = loc.short if loc else "-"
        return f"{cat:9s} {where:32s} {sym}"

    def ranges_for(self, cat: str) -> list[tuple[int, int]]:
        """Merged [start, end) address ranges whose line info is in category `cat`."""
        out: list[tuple[int, int]] = []
        for i, loc in enumerate(self._locs):
            if loc is None or loc.cat != cat or i + 1 >= len(self._addrs):
                continue
            s, e = self._addrs[i], self._addrs[i + 1]
            if out and out[-1][1] == s:
                out[-1] = (out[-1][0], e)
            else:
                out.append((s, e))
        return out

    def __len__(self) -> int:
        return len(self._addrs)


class Symbols:
    """Function symbols from `nm` (sorted), for ROM and for addresses without line info."""

    def __init__(self, elf: Path, chip: str):
        out = subprocess.run([str(tool(chip, "nm")), "-n", "-S", "-C", str(elf)],
                             capture_output=True, text=True, errors="replace").stdout
        self._a: list[int] = []
        self._n: list[str] = []
        self._e: list[int] = []
        for ln in out.splitlines():
            parts = ln.split(maxsplit=3)
            if len(parts) == 4 and parts[2] in "tTwW":
                a, size, name = int(parts[0], 16), int(parts[1], 16), parts[3]
            elif len(parts) == 3 and parts[1] in "tTwW":
                a, size, name = int(parts[0], 16), 0, parts[2]
            else:
                continue
            self._a.append(a)
            self._n.append(name)
            self._e.append(a + size if size else a + 0x1000)

    def lookup(self, addr: int) -> str | None:
        i = bisect.bisect_right(self._a, addr) - 1
        if i < 0 or addr >= self._e[i]:
            return None
        return self._n[i]


def main() -> None:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="Map addresses to source lines")
    ap.add_argument("elf")
    ap.add_argument("addr", nargs="*")
    ap.add_argument("--chip", default="esp32")
    ap.add_argument("--sketch-dir")
    a = ap.parse_args()
    t0 = time.perf_counter()
    lm = LineMap(Path(a.elf), a.chip, a.sketch_dir)
    t1 = time.perf_counter()
    print(f"loaded {len(lm)} rows in {t1 - t0:.2f}s", file=sys.stderr)
    for s in a.addr:
        print(f"{s}: {lm.describe(int(s, 16))}")


if __name__ == "__main__":
    main()
