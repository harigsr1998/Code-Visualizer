"""Spike step 3: GDB-stub costs for "step mode" and variable sampling (app zone).

Uses our own RSP client (rsp.py) against QEMU's gdbstub. Measures:
  - breakpoint hit + continue round trips,
  - instruction single-steps per second,
  - source-line steps (single-step until the DWARF line changes) per second,
    and instructions per line step,
  - memory reads per second (halted), and live sampling of a global
    variable at 100 Hz (halt, read, continue) with the guest slowdown.
Run inside the heavy-job lock (timing measurement).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from linemap import LineMap
from paths import PORT_GDB, STEP, tool
from qsession import QemuSession
from rsp import RSP

LOOP_RE = re.compile(r"\[map\] n=(\d+)")


def sym_addr(elf: Path, name: str) -> int:
    out = subprocess.run([str(tool("esp32", "nm")), "-C", str(elf)], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        parts = ln.split(maxsplit=2)
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    raise KeyError(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build-a-esp32-og-mapdemo")
    ap.add_argument("--extra", nargs="*", default=[])
    a = ap.parse_args()
    build = (STEP / a.build).resolve()
    elf = next(build.glob("*.ino.elf"))
    lm = LineMap(elf, "esp32", str((STEP / "mapdemo").resolve()))
    loop_addr = sym_addr(elf, "loop()")
    g_samples = sym_addr(elf, "g_samples")
    res: dict = {"build": a.build, "extra": " ".join(a.extra)}
    with QemuSession(next(build.glob("*.ino.merged.bin")), extra=["-gdb", f"tcp:127.0.0.1:{PORT_GDB}", *a.extra]) as q:
        if not q.wait_for(r"\[map\] n=", 60):
            raise SystemExit("mapdemo didn't start: " + "; ".join(q.panics()[:2]))
        g = RSP(PORT_GDB)  # attaching halts both vCPUs

        # 1. breakpoint round trips on loop() (runs every ~10 ms of guest time)
        g.bp(loop_addr)
        t = time.perf_counter()
        hits = 0
        tid_hit = None
        for _ in range(30):
            g.cont()
            stop = g.wait_stop()
            m = re.search(r"thread:([0-9a-f]+);", stop)
            tid_hit = m.group(1) if m else tid_hit
            hits += 1
        dt = time.perf_counter() - t
        res["bp_round_trips_per_s"] = round(hits / dt, 1)
        res["bp_hit_thread"] = tid_hit
        res["bp_pc_check"] = f"{g.pc(tid_hit):08x}" if tid_hit else None
        g.unbp(loop_addr)

        # 2. instruction single-step on the core that hit loop()
        tid = tid_hit or g.threads[-1]
        n = 2000
        t = time.perf_counter()
        for _ in range(n):
            g.step(tid)
        dt = time.perf_counter() - t
        res["insn_steps_per_s"] = round(n / dt)

        # 3. source-line steps inside sketch code: step until the line changes
        lines_done = 0
        insns = 0
        trail = []
        t = time.perf_counter()
        pc = g.pc(tid)
        cur = lm.lookup(pc)
        while lines_done < 60 and insns < 200000:
            g.step(tid)
            insns += 1
            pc = g.pc(tid)
            loc = lm.lookup(pc) if lm.category(pc) == "sketch" else None
            if loc and loc != cur:
                cur = loc
                lines_done += 1
                trail.append(loc.short)
        dt = time.perf_counter() - t
        res["line_steps"] = lines_done
        res["line_steps_per_s"] = round(lines_done / dt, 1)
        res["insns_per_line_step"] = round(insns / max(lines_done, 1), 1)
        res["line_trail_first30"] = trail[:30]

        # 4. memory reads while halted
        n = 1000
        t = time.perf_counter()
        for _ in range(n):
            g.u32(g_samples)
        res["mem_reads_per_s_halted"] = round(n / (time.perf_counter() - t))

        # 5. live sampling of g_samples at 100 Hz: halt, read, continue
        g.cont()
        time.sleep(1.0)
        t_start = time.perf_counter()
        vals = []
        while time.perf_counter() - t_start < 5.0:
            t0 = time.perf_counter()
            g.halt()
            vals.append((time.perf_counter() - t_start, g.u32(g_samples)))
            g.cont()
            time.sleep(max(0.0, 0.01 - (time.perf_counter() - t0)))
        span = vals[-1][0] - vals[0][0]
        res["var_samples_per_s"] = round(len(vals) / span, 1)
        res["g_samples_rate_while_sampling_per_s"] = round((vals[-1][1] - vals[0][1]) / span, 1)
        g.halt()
        g.close()  # detach: QEMU resumes the guest
        # Unsampled reference: loop() runs every ~10 ms -> ~100/s at real time.
        time.sleep(0.5)
        a0 = [(tt, int(m.group(1))) for tt, ln in q.lines if (m := LOOP_RE.search(ln))]
        time.sleep(4.0)
        a1 = [(tt, int(m.group(1))) for tt, ln in q.lines if (m := LOOP_RE.search(ln))]
        if len(a1) > len(a0) >= 1:
            (ta, na), (tb, nb) = a0[-1], a1[-1]
            res["loop_rate_after_detach_per_s"] = round((nb - na) / (tb - ta), 1)
        res["panics"] = q.panics()[:3]
    print(json.dumps(res, indent=1))
    out = STEP / "results" / f"gdbstep-{a.build}{'-icount' if a.extra else ''}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
