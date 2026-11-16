"""Spike step 3: capture an execution trace of a sketch and analyse it (app zone).

`-d in_asm` is on from boot (cheap: once per translated block), so every
block's instruction list is known. After the sketch is running, the monitor
switches to `in_asm,exec,nochain` for --seconds, then back. The log is then
analysed by exectrace.py (category shares, per-core split, line hits).
Functional run (no timing claims) unless run inside the heavy-job lock.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from exectrace import TraceStats
from linemap import LineMap
from paths import PORT_MON, STEP
from qsession import Monitor, QemuSession


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, help="build folder (under 03-trace or 01-build)")
    ap.add_argument("--sketch", required=True, help="sketch folder name")
    ap.add_argument("--ready", default=r"\[(spike|map|bench)\]", help="UART regex: sketch is running")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--icount", action="store_true")
    ap.add_argument("--dfilter", choices=["sketch"], default=None)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    build = (STEP / a.build) if (STEP / a.build).exists() else (STEP.parent / "01-build" / a.build)
    sk = (STEP / a.sketch) if (STEP / a.sketch).exists() else (STEP.parent / "01-build" / a.sketch)
    elf = next(build.glob("*.ino.elf"))
    lm = LineMap(elf, "esp32", str(sk.resolve()))
    scratch = Path(os.environ.get("TRACE_SCRATCH", STEP / "out"))
    scratch.mkdir(parents=True, exist_ok=True)
    tag = f"{build.name}{'-icount' if a.icount else ''}{'-dfilter' if a.dfilter else ''}"
    log = scratch / f"trace-{tag}.log"
    extra = ["-d", "in_asm", "-D", str(log)]
    if a.icount:
        extra += ["-icount", "shift=auto"]
    if a.dfilter:
        extra += ["-dfilter", ",".join(f"0x{s:x}..0x{e - 1:x}" for s, e in lm.ranges_for("sketch"))]
    with QemuSession(next(build.glob("*.ino.merged.bin")), extra=extra, monitor_port=PORT_MON) as q:
        if not q.wait_for(a.ready, 90):
            raise SystemExit("sketch didn't start: " + "; ".join(q.panics()[:2]))
        time.sleep(1.0)
        m = Monitor(PORT_MON)
        n0 = len(q.lines)
        t0 = time.perf_counter()
        m.cmd("log in_asm,exec,nochain")
        time.sleep(a.seconds)
        m.cmd("log in_asm")
        on_s = time.perf_counter() - t0
        n1 = len(q.lines)
        m.close()
        uart = [ln for _, ln in q.lines[n0:n1]]
        panics = q.panics()
    ts = TraceStats(lm)
    t = time.perf_counter()
    ts.feed(log)
    parse_s = time.perf_counter() - t
    rep = ts.report()
    rep.update({
        "tag": tag, "trace_on_s": round(on_s, 2), "log_MB": round(log.stat().st_size / 1e6, 1),
        "analyse_s": round(parse_s, 2), "uart_lines_during_trace": len(uart),
        "uart_sample": uart[:3], "panics": panics[:3],
    })
    txt = json.dumps(rep, indent=1)
    print(txt)
    out = STEP / "results" / f"trace-{tag}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(txt)
    if not a.keep:
        log.unlink()


if __name__ == "__main__":
    main()
