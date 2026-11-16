"""Spike step 3 prototype: live "now executing <file>:<line>" stream (app zone).

Runs a sketch in Espressif QEMU with
    -d in_asm,exec,nochain -dfilter <sketch address ranges> -D <log>
so QEMU logs only blocks that start in sketch code (plus their disassembly
once per translation). This script tails the log while QEMU writes it, maps
every executed block to its source lines (DWARF), and prints the line
transitions with the core that ran them. At the end it prints per-line hit
counts.

  python tools/live.py --build build-a-esp32-og-mapdemo --sketch mapdemo --seconds 10
  python tools/live.py --build build-a-esp32-og --sketch sketch_multi --icount

Limitation: -dfilter matches a block's *start* address, so a sketch block
entered from a call into the middle of nothing is fine, but code inlined
from headers into sketch functions is attributed to the header line
(see NOTES.md, mapping). Blocks that start outside the filter (core/IDF)
aren't logged at all.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from collections import Counter
from pathlib import Path

from linemap import LineMap
from paths import PORT_MON, STEP
from qsession import QemuSession

EXEC = re.compile(r"^Trace (\d+): \S+ \[[0-9a-f]+/([0-9a-f]+)/")
INSN = re.compile(r"^0x([0-9a-f]+):\s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True)
    ap.add_argument("--sketch", required=True)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--icount", action="store_true", help="-icount shift=auto (stable, ~3.5x slower)")
    ap.add_argument("--max-print", type=int, default=60, help="line transitions to print")
    ap.add_argument("--ready", default=r"\[(map|spike|bench)\]", help="UART regex: the sketch is running")
    a = ap.parse_args()
    build = (STEP / a.build) if (STEP / a.build).exists() else (STEP.parent / "01-build" / a.build)
    sk = (STEP / a.sketch) if (STEP / a.sketch).exists() else (STEP.parent / "01-build" / a.sketch)
    elf = next(build.glob("*.ino.elf"))
    lm = LineMap(elf, "esp32", str(sk.resolve()))
    ranges = lm.ranges_for("sketch")
    scratch = Path(os.environ.get("TRACE_SCRATCH", STEP / "out"))
    scratch.mkdir(parents=True, exist_ok=True)
    log = scratch / f"live-{build.name}.log"
    if log.exists():
        log.unlink()
    extra = ["-d", "in_asm,exec,nochain", "-D", str(log),
             "-dfilter", ",".join(f"0x{s:x}..0x{e - 1:x}" for s, e in ranges)]
    if a.icount:
        extra += ["-icount", "shift=auto"]
    print(f"[live] {len(ranges)} sketch address ranges; map table {len(lm)} rows")

    tb_lines: dict[int, list] = {}  # tb pc -> source locations of its instructions, in order
    hits: Counter = Counter()  # (cpu, file:line) -> instruction executions
    printed = 0
    last: dict[int, str] = {}
    n_exec = 0
    lat_ms: list[float] = []
    with QemuSession(next(build.glob("*.ino.merged.bin")), extra=extra, monitor_port=PORT_MON) as q:
        t_end = time.perf_counter() + a.seconds + 30
        while not log.exists() and time.perf_counter() < t_end:
            time.sleep(0.05)
        # Start the window once the sketch is running (boot is slower with nochain);
        # the log is still read from its start, so setup() shows up as backlog.
        q.wait_for(a.ready, 90)
        t_start = time.perf_counter()
        with open(log, "r", errors="replace") as f:
            cur: list[int] | None = None
            buf = ""
            while time.perf_counter() - t_start < a.seconds:
                chunk = f.read(1 << 20)
                if not chunk:
                    time.sleep(0.01)
                    continue
                t_read = time.perf_counter()
                buf += chunk
                lines = buf.split("\n")
                buf = lines.pop()
                for ln in lines:
                    if ln.startswith("IN:"):
                        cur = []
                        continue
                    if cur is not None:
                        m = INSN.match(ln)
                        if m:
                            cur.append(int(m.group(1), 16))
                            continue
                        if cur:
                            tb_lines[cur[0]] = [lm.lookup(x) for x in cur]
                        cur = None
                    m = EXEC.match(ln)
                    if not m:
                        continue
                    cpu, pc = int(m.group(1)), int(m.group(2), 16)
                    n_exec += 1
                    locs = tb_lines.get(pc) or [lm.lookup(pc)]
                    for loc in locs:
                        if loc is None:
                            continue
                        hits[(cpu, loc.short)] += 1
                        if last.get(cpu) != loc.short:
                            last[cpu] = loc.short
                            if printed < a.max_print:
                                printed += 1
                                print(f"[live {t_read - t_start:7.3f}s] core {cpu}: now executing {loc.short}")
                lat_ms.append((time.perf_counter() - t_read) * 1000)
        uart = [ln for _, ln in q.lines if ln.startswith("[")]
    print(f"\n[live] {n_exec} sketch blocks executed in {a.seconds:.0f} s; "
          f"processing per read batch: median {sorted(lat_ms)[len(lat_ms) // 2]:.2f} ms" if lat_ms else "")
    print(f"[live] last UART lines: {uart[-2:]}")
    print("[live] per-line instruction hits:")
    def order(kv):
        (cpu, where), _ = kv
        name, _, line = where.rpartition(":")
        return cpu, name, int(line)

    for (cpu, where), n in sorted(hits.items(), key=order):
        print(f"  core {cpu}  {where:28s} {n}")
    log.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
