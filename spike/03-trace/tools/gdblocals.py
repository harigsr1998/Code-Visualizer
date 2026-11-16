"""Spike step 3: what a debugger can show of sketch variables at -Og vs -Os (app zone).

Runs Espressif's xtensa-esp32-elf-gdb (GPLv3, separate process) in batch mode
against QEMU's gdbstub, stops in inlinable sketch code, and records
`info args`/`info locals`/`bt`. Counts "<optimized out>" values.
Functional run (no timing), so it doesn't need the heavy-job lock.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from paths import GDB, PORT_GDB, STEP
from qsession import QemuSession

POINTS = ["sensorSample", "Blinker::step", "MovingAverage<8u>::add"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build-a-esp32-og-mapdemo")
    a = ap.parse_args()
    build = (STEP / a.build).resolve()
    elf = next(build.glob("*.ino.elf"))
    cmds = ["set pagination off", "set confirm off", "set print pretty off",
            f"target remote 127.0.0.1:{PORT_GDB}"]
    for p in POINTS:
        cmds += [f"echo \\n=== {p} ===\\n", f"break {p}", "continue", "info args", "info locals",
                 "bt 4", "delete"]
    cmds += ["echo \\n=== globals ===\\n", "print g_samples", "print avg", "print blinker", "detach"]
    script = build / "gdblocals.gdb"
    script.write_text("\n".join(cmds) + "\n")
    with QemuSession(next(build.glob("*.ino.merged.bin")), extra=["-gdb", f"tcp:127.0.0.1:{PORT_GDB}"]) as q:
        if not q.wait_for(r"\[map\] n=", 60):
            raise SystemExit("mapdemo didn't start")
        r = subprocess.run([str(GDB), "-q", "-batch", "-x", str(script), str(elf)],
                           capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    # Drop the per-run noise (addresses of breakpoints, thread switch lines).
    print(out)
    n_opt = len(re.findall(r"<optimized out>", out))
    summary = f"\n# {a.build}: '<optimized out>' values: {n_opt}\n"
    print(summary)
    res = STEP / "results" / f"gdblocals-{a.build}.txt"
    res.parent.mkdir(exist_ok=True)
    res.write_text(out + summary)


if __name__ == "__main__":
    main()
