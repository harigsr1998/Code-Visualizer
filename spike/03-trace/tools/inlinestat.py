"""Spike step 3: how inlining and -Os change address -> sketch-line mapping (app zone).

For every DWARF line-table address inside a sketch function, asks
`addr2line -f -i -C` (binutils, separate process) for the inline chain:
  frames > 1                 -> the address is inlined code,
  innermost frame not sketch -> e.g. an Arduino/IDF header inlined into the
                                sketch: a plain innermost-line lookup would
                                attribute it to the header, not the sketch.
Also reports how many distinct sketch lines own code and how many line-table
rows the sketch has (fewer rows / lines at -Os = coarser stepping).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from linemap import LineMap, classify_path
from paths import STEP, tool


def sketch_functions(lm: LineMap, elf: Path) -> list[tuple[int, int, str]]:
    out = subprocess.run([str(tool("esp32", "nm")), "-n", "-S", "-C", str(elf)],
                         capture_output=True, text=True).stdout
    funcs = []
    for ln in out.splitlines():
        p = ln.split(maxsplit=3)
        if len(p) == 4 and p[2] in "tTwW":
            a, size = int(p[0], 16), int(p[1], 16)
            if size and lm.category(a) == "sketch":
                funcs.append((a, a + size, p[3]))
    return funcs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True)
    ap.add_argument("--sketch", required=True, help="sketch folder name under 03-trace or 01-build")
    a = ap.parse_args()
    build = (STEP / a.build) if (STEP / a.build).exists() else (STEP.parent / "01-build" / a.build)
    sk = (STEP / a.sketch) if (STEP / a.sketch).exists() else (STEP.parent / "01-build" / a.sketch)
    elf = next(build.glob("*.ino.elf"))
    lm = LineMap(elf, "esp32", str(sk.resolve()))
    funcs = sketch_functions(lm, elf)
    addrs = [x for s, e, _ in funcs for x in lm._addrs if s <= x < e]  # line-table rows only
    r = subprocess.run([str(tool("esp32", "addr2line")), "-f", "-i", "-C", "-a", "-e", str(elf)],
                       input="\n".join(f"0x{x:x}" for x in addrs) + "\n", capture_output=True, text=True)
    chains: dict[int, list[tuple[str, str]]] = {}
    cur = None
    lines = r.stdout.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("0x"):
            cur = int(ln, 16)
            chains[cur] = []
            i += 1
            continue
        func, loc = ln, lines[i + 1] if i + 1 < len(lines) else "??:0"
        chains[cur].append((func, loc))
        i += 2
    inlined = sum(1 for c in chains.values() if len(c) > 1)
    foreign_inner = Counter()
    for c in chains.values():
        inner_file = c[0][1].rsplit(":", 1)[0]
        cat = classify_path(inner_file, str(sk.resolve()))
        if cat != "sketch":
            foreign_inner[Path(inner_file.replace("\\", "/")).name] += 1
    sketch_rows = sum(1 for loc in lm._locs if loc and loc.cat == "sketch")
    sketch_lines = {(loc.file, loc.line) for loc in lm._locs if loc and loc.cat == "sketch"}
    inline_funcs = Counter(f for c in chains.values() for f, _ in c[:-1])
    res = {
        "build": a.build,
        "sketch_functions_out_of_line": [f"{n} ({e - s} B)" for s, e, n in funcs],
        "sketch_code_bytes": sum(e - s for s, e, _ in funcs),
        "sketch_line_table_rows": sketch_rows,
        "distinct_sketch_lines_with_code": len(sketch_lines),
        "addrs_checked": len(chains),
        "addrs_inlined": inlined,
        "addrs_innermost_not_sketch": sum(foreign_inner.values()),
        "innermost_not_sketch_files": foreign_inner.most_common(8),
        "inlined_functions": inline_funcs.most_common(10),
    }
    txt = json.dumps(res, indent=1)
    print(txt)
    out = STEP / "results" / f"inline-{a.build}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(txt)


if __name__ == "__main__":
    main()
