"""Spike step 3: emulation overhead of each execution-capture method (app zone).

Runs the bench sketch (03-trace/bench) once per mode. After boot (chunk 3),
the capture method is switched on (QEMU monitor / gdbstub), then chunks of
known work are counted against host wall-clock time.

Run it inside the heavy-job lock (it's a timing measurement):
  run-heavy.ps1 -Who trace -What 'bench' -Job { python tools\\bench.py --build build-a-esp32-og-bench }
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from pathlib import Path

from linemap import LineMap
from paths import PORT_GDB, PORT_MON, STEP
from qsession import Monitor, QemuSession
from rsp import RSP

CHUNK_ITERS = 4_000_000  # bench/work.h BENCH_CHUNK_ITERS; core 0 does 1/4 of that per chunk
CHUNK_RE = re.compile(r"\[bench\] chunk (\d+) r=\w+ guest_ms=(\d+) core0=(\d+)")
SCRATCH = Path(os.environ.get("TRACE_SCRATCH", STEP / "out"))

MODES: dict[str, dict] = {
    "base": {},
    "icount_auto": {"cli": ["-icount", "shift=auto"]},
    "gdb_attached_idle": {"gdb": "idle"},
    "gdb_sample_100hz": {"gdb": "sample", "hz": 100},
    "gdb_sample_max": {"gdb": "sample", "hz": 0},
    "hmp_sample_100hz": {"hmp": "sample", "hz": 100},
    "hmp_sample_max": {"hmp": "sample", "hz": 0},
    "log_nochain_only": {"log": "nochain"},
    "log_in_asm": {"log": "in_asm"},
    "log_exec_chained": {"log": "exec"},
    "trace_exec_tb_chained": {"trace": "exec_tb"},
    "log_exec_nochain": {"log": "exec,nochain"},
    "log_exec_nochain_dfilter_sketch": {"log": "exec,nochain", "dfilter": "sketch"},
    # With -icount the guest clock follows executed instructions, so a slowed CPU
    # doesn't trip the watchdogs (real-time mode resets under heavy logging).
    "icount_gdb_sample_100hz": {"cli": ["-icount", "shift=auto"], "gdb": "sample", "hz": 100},
    "icount_hmp_sample_100hz": {"cli": ["-icount", "shift=auto"], "hmp": "sample", "hz": 100},
    "icount_log_exec_nochain": {"cli": ["-icount", "shift=auto"], "log": "exec,nochain"},
    "icount_log_exec_nochain_dfilter_sketch": {"cli": ["-icount", "shift=auto"], "log": "exec,nochain",
                                               "dfilter": "sketch"},
}
NO_CHUNK_GIVEUP_S = 25  # a mode this slow is measured by the marker TB count instead


def sampler_gdb(g: RSP, hz: float, stop: threading.Event, out: list) -> None:
    period = 1.0 / hz if hz else 0.0
    while not stop.is_set():
        t = time.perf_counter()
        g.halt()
        for i, tid in enumerate(g.threads):
            out.append((i, g.pc(tid)))
        g.cont()
        if period:
            time.sleep(max(0.0, period - (time.perf_counter() - t)))


HMP_PC = re.compile(r"CPU#(\d+)\s+PC=([0-9a-fA-F]+)")


def sampler_hmp(m: Monitor, hz: float, stop: threading.Event, out: list) -> None:
    period = 1.0 / hz if hz else 0.0
    while not stop.is_set():
        t = time.perf_counter()
        for cpu, pc in HMP_PC.findall(m.cmd("info registers -a")):
            out.append((int(cpu), int(pc, 16)))
        if period:
            time.sleep(max(0.0, period - (time.perf_counter() - t)))


def count_exec(log: Path, marker_pc: int) -> dict:
    """Count logged TB executions, and those of the once-per-iteration TB, per CPU."""
    total: dict[int, int] = {}
    marker: dict[int, int] = {}
    pat_exec = re.compile(rb"^Trace (\d+): \S+ \[[0-9a-f]+/([0-9a-f]+)/")
    pat_tb = re.compile(rb"^exec_tb tb:\S+ pc=0x([0-9a-f]+)")
    mk = marker_pc
    with open(log, "rb") as f:
        for ln in f:
            m = pat_exec.match(ln)
            if m:
                cpu, pc = int(m.group(1)), int(m.group(2), 16)
            else:
                m = pat_tb.match(ln)
                if not m:
                    continue
                cpu, pc = -1, int(m.group(1), 16)  # exec_tb doesn't say which CPU
            total[cpu] = total.get(cpu, 0) + 1
            if pc == mk:
                marker[cpu] = marker.get(cpu, 0) + 1
    return {"tb_total": total, "marker": marker}


def run_mode(name: str, cfg: dict, build: Path, lm: LineMap, window: float, marker_pc: int) -> dict:
    image = next(build.glob("*.ino.merged.bin"))
    cli = list(cfg.get("cli", []))
    if cfg.get("gdb"):
        cli += ["-gdb", f"tcp:127.0.0.1:{PORT_GDB}"]
    if cfg.get("dfilter"):
        rng = lm.ranges_for(cfg["dfilter"])
        cli += ["-dfilter", ",".join(f"0x{s:x}..0x{e - 1:x}" for s, e in rng)]
    logf = SCRATCH / f"bench-{name}.log"
    if logf.exists():
        logf.unlink()
    res: dict = {"mode": name, "cli": " ".join(cli)}
    with QemuSession(image, extra=cli, monitor_port=PORT_MON) as q:
        if not q.wait_for(r"\[bench\] chunk 3 ", 120):
            res["error"] = "no chunk 3 within 120 s"
            res["panics"] = q.panics()[:3]
            return res
        mon = Monitor(PORT_MON)
        stop = threading.Event()
        samples: list = []
        thr = None
        g = None
        t_on = time.perf_counter()
        if cfg.get("log") or cfg.get("trace"):
            mon.cmd(f"logfile {logf.as_posix()}")
        if cfg.get("log"):
            mon.cmd(f"log {cfg['log']}")
        if cfg.get("trace"):
            mon.cmd(f"trace-event {cfg['trace']} on")
        if cfg.get("gdb"):
            g = RSP(PORT_GDB)  # attaching halts the chip
            g.cont()
            if cfg["gdb"] == "sample":
                thr = threading.Thread(target=sampler_gdb, args=(g, cfg["hz"], stop, samples), daemon=True)
        if cfg.get("hmp"):
            thr =threading.Thread(target=sampler_hmp, args=(mon, cfg["hz"], stop, samples), daemon=True)
        if thr:
            thr.start()
        on_rel = t_on - q.t_connect
        time.sleep(1.0)  # settle
        t0 = time.perf_counter() - q.t_connect
        n_panics_before = len(q.panics())
        while True:
            now = time.perf_counter() - q.t_connect
            chunks = [(t, CHUNK_RE.search(ln)) for t, ln in q.lines if t >= t0 and CHUNK_RE.search(ln)]
            if (now - t0 >= window and len(chunks) >= 3) or now - t0 > 120 or q.proc.poll() is not None:
                break
            if len(q.panics()) > n_panics_before:
                time.sleep(0.5)
                break
            if now - t0 > NO_CHUNK_GIVEUP_S and len(chunks) < 2:
                break
            time.sleep(0.1)
        stop.set()
        if thr:
            thr.join(timeout=5)
        if cfg.get("log"):
            mon.cmd("log none")
        if cfg.get("trace"):
            mon.cmd(f"trace-event {cfg['trace']} off")
        t_off = time.perf_counter() - q.t_connect
        mon.close()
        if g:
            g.close()
        res["panics"] = q.panics()[:3]
    if len(chunks) >= 2:
        (ta, ma), (tb, mb) = chunks[0], chunks[-1]
        dt = tb - ta
        c1 = int(mb.group(1)) - int(ma.group(1))
        c0 = int(mb.group(3)) - int(ma.group(3))
        res["chunks_per_s"] = round(c1 / dt, 3)
        res["core1_Miter_per_s"] = round(c1 * CHUNK_ITERS / dt / 1e6, 2)
        res["core0_Miter_per_s"] = round(c0 * CHUNK_ITERS / 4 / dt / 1e6, 2)
        gm = [int(m.group(2)) for _, m in chunks]
        res["guest_ms_per_chunk_median"] = sorted(gm)[len(gm) // 2]
        res["window_s"] = round(dt, 2)
    else:
        res["error"] = f"only {len(chunks)} chunk lines in the window"
    if samples:
        dur = t_off - on_rel - 1.0
        per_cpu: dict[int, list[int]] = {}
        for cpu, pc in samples:
            per_cpu.setdefault(cpu, []).append(pc)
        res["samples_per_s"] = round(len(samples) / max(len(per_cpu), 1) / max(dur, 1e-9), 1)
        res["sample_share_in_workChunk"] = {
            cpu: round(sum(1 for pc in pcs if lm.symbol(pc) == "workChunk(unsigned long)") / len(pcs), 3)
            for cpu, pcs in sorted(per_cpu.items())}
        res["sample_top"] = {
            cpu: _top([lm.symbol(pc) or f"{pc:08x}" for pc in pcs]) for cpu, pcs in sorted(per_cpu.items())}
    if logf.exists():
        size = logf.stat().st_size
        res["log_MB"] = round(size / 1e6, 1)
        res["log_MB_per_s"] = round(size / 1e6 / max(t_off - on_rel, 1e-9), 1)
        if cfg.get("log", "").startswith("exec") or cfg.get("trace"):
            cnt = count_exec(logf, marker_pc)
            res.update(cnt)
            on_s = t_off - on_rel
            res["tb_logged_per_s"] = round(sum(cnt["tb_total"].values()) / on_s)
            # Iterations seen in the trace (exact), vs. iterations done by the guest (from chunks).
            res["marker_Miter_per_s"] = {c: round(n / on_s / 1e6, 4) for c, n in cnt["marker"].items()}
        if not os.environ.get("TRACE_KEEP_LOGS"):
            logf.unlink()
    res["log_on_s"] = round(t_off - on_rel, 2)
    return res


def _top(names: list[str], n: int = 3) -> list:
    c: dict[str, int] = {}
    for x in names:
        c[x] = c.get(x, 0) + 1
    return [(k, round(v / len(names), 3)) for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:n]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build-a-esp32-og-bench")
    ap.add_argument("--modes", nargs="*", default=list(MODES))
    ap.add_argument("--window", type=float, default=10.0)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    build = (STEP / a.build).resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    lm = LineMap(next(build.glob("*.ino.elf")), "esp32", str((STEP / "bench").resolve()))
    # The TB that starts right after the loop test runs exactly once per iteration.
    # (Addresses from `objdump -d` of workChunk in each build; see NOTES.md.)
    marker_pc = {"og": 0x400D1778, "os": 0x400D171C}.get(build.name.split("-")[3], 0)
    results = []
    for rep in range(a.repeat):
        for name in a.modes:
            for attempt in range(2):
                r = run_mode(name, MODES[name], build, lm, a.window, marker_pc)
                r["rep"] = rep
                r["attempt"] = attempt
                print(json.dumps(r), flush=True)
                if "error" not in r and not r["panics"]:
                    break
            results.append(r)
    out = Path(a.out) if a.out else STEP / "results" / f"overhead-{build.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
