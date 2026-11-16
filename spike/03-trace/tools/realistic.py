"""Spike step 3: tracing overhead on a *typical* sketch (mostly idle in delay()).

mapdemo's loop() does a little work and delay(10), so at real time it runs
~95-100 times/s; its UART line prints n every 500 ms. For each mode we
measure, after tracing is switched on:
  - loop() rate (from n), i.e. does the sketch still keep real time?
  - QEMU process CPU seconds per wall second (host cost),
  - logged blocks/s and log MB/s,
  - resets/panics.
Run inside the heavy-job lock (timing measurement).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from linemap import LineMap
from paths import PORT_MON, STEP
from qsession import Monitor, QemuSession

N_RE = re.compile(r"\[map\] n=(\d+)")


def cpu_seconds(pid: int) -> float:
    out = subprocess.run(["powershell", "-NoProfile", "-c", f"(Get-Process -Id {pid}).TotalProcessorTime.TotalSeconds"],
                         capture_output=True, text=True).stdout.strip()
    return float(out.replace(",", ".")) if out else float("nan")


def run(build: Path, lm: LineMap, mode: str, window: float, icount: bool) -> dict:
    scratch = Path(os.environ.get("TRACE_SCRATCH", STEP / "out"))
    log = scratch / f"real-{mode}.log"
    log.unlink(missing_ok=True)
    extra = ["-d", "in_asm", "-D", str(log)] if mode != "none" else []
    if mode == "dfilter":
        extra += ["-dfilter", ",".join(f"0x{s:x}..0x{e - 1:x}" for s, e in lm.ranges_for("sketch"))]
    if icount:
        extra += ["-icount", "shift=auto"]
    res = {"mode": mode, "icount": icount}
    with QemuSession(next(build.glob("*.ino.merged.bin")), extra=extra, monitor_port=PORT_MON) as q:
        if not q.wait_for(r"\[map\] n=", 90):
            return {**res, "error": "no start", "panics": q.panics()[:2]}
        time.sleep(2)
        m = Monitor(PORT_MON)
        if mode != "none":
            m.cmd("log in_asm,exec,nochain")
        size0 = log.stat().st_size if log.exists() else 0
        time.sleep(1)
        c0, t0 = cpu_seconds(q.proc.pid), time.perf_counter()
        n0 = len(q.panics())
        while time.perf_counter() - t0 < window and len(q.panics()) == n0:
            time.sleep(0.2)
        c1, t1 = cpu_seconds(q.proc.pid), time.perf_counter()
        size1 = log.stat().st_size if log.exists() else 0
        if mode != "none":
            m.cmd("log none")
        m.close()
        pts = [(t, int(mm.group(1))) for t, ln in q.lines if (mm := N_RE.search(ln))
               and t0 - q.t_connect <= t <= t1 - q.t_connect]
        res["panics"] = q.panics()[n0:n0 + 2]
    dt = t1 - t0
    if len(pts) >= 2:
        res["loop_per_s"] = round((pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0]), 1)
    res["qemu_cpu_s_per_s"] = round((c1 - c0) / dt, 2)
    res["log_MB_per_s"] = round((size1 - size0) / 1e6 / dt, 2)
    if log.exists():
        n = 0
        with open(log, "rb") as f:
            for ln in f:
                if ln.startswith(b"Trace "):
                    n += 1
        res["blocks_logged_per_s_avg"] = round(n / dt)  # includes the 1 s settle, so a slight overestimate
        log.unlink()
    res["window_s"] = round(dt, 1)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build-a-esp32-og-mapdemo")
    ap.add_argument("--window", type=float, default=15)
    a = ap.parse_args()
    build = (STEP / a.build).resolve()
    lm = LineMap(next(build.glob("*.ino.elf")), "esp32", str((STEP / "mapdemo").resolve()))
    results = []
    for icount in (False, True):
        for mode in ("none", "dfilter", "full"):
            for attempt in range(2):
                r = run(build, lm, mode, a.window, icount)
                r["attempt"] = attempt
                print(json.dumps(r), flush=True)
                if "error" not in r and not r["panics"]:
                    break
            results.append(r)
    out = STEP / "results" / f"realistic-{a.build}.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
