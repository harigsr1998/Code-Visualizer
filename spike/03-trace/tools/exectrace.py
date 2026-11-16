"""Analyse a QEMU `-d in_asm,exec,nochain` log against the ELF's DWARF lines (app zone).

The log is read in order:
  in_asm blocks ("IN:" + "0x<addr>:  <insn>" lines) give each translation
  block's instruction addresses, once per translation;
  exec lines ("Trace <cpu>: <host> [<cs_base>/<pc>/<flags>/<cflags>]") give
  each execution of a block, with the vCPU index (0 = PRO_CPU, 1 = APP_CPU).
Every execution is expanded to its instructions, so shares are instruction-weighted.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from linemap import LineMap

EXEC = re.compile(rb"^Trace (\d+): \S+ \[[0-9a-f]+/([0-9a-f]+)/")
INSN = re.compile(rb"^0x([0-9a-f]+):\s")


class TraceStats:
    def __init__(self, lm: LineMap):
        self.lm = lm
        self.tb_insns: dict[int, tuple[int, ...]] = {}
        self.insn_exec = Counter()  # (cpu, addr) -> executions
        self.tb_exec = 0
        self.unknown_tb = 0

    def feed(self, path: Path) -> None:
        cur: list[int] | None = None
        with open(path, "rb") as f:
            for ln in f:
                if ln.startswith(b"IN:"):
                    cur = []
                    continue
                if cur is not None:
                    m = INSN.match(ln)
                    if m:
                        cur.append(int(m.group(1), 16))
                        continue
                    if cur:
                        self.tb_insns[cur[0]] = tuple(cur)
                    cur = None
                m = EXEC.match(ln)
                if m:
                    cpu, pc = int(m.group(1)), int(m.group(2), 16)
                    self.tb_exec += 1
                    insns = self.tb_insns.get(pc)
                    if insns is None:
                        self.unknown_tb += 1
                        insns = (pc,)
                    for a in insns:
                        self.insn_exec[(cpu, a)] += 1
        if cur:
            self.tb_insns[cur[0]] = tuple(cur)

    def report(self, top: int = 15) -> dict:
        lm = self.lm
        cat_cache: dict[int, str] = {}
        by_cat = Counter()
        by_cpu_cat: dict[int, Counter] = defaultdict(Counter)
        uniq_cat = Counter()
        seen_addr = set()
        line_hits: dict[str, Counter] = defaultdict(Counter)
        func_by_cpu: dict[int, Counter] = defaultdict(Counter)
        for (cpu, a), n in self.insn_exec.items():
            c = cat_cache.get(a)
            if c is None:
                c = cat_cache[a] = lm.category(a)
            by_cat[c] += n
            by_cpu_cat[cpu][c] += n
            if a not in seen_addr:
                seen_addr.add(a)
                uniq_cat[c] += 1
            if c == "sketch":
                loc = lm.lookup(a)
                line_hits[f"cpu{cpu}"][loc.short] += n
                func_by_cpu[cpu][lm.symbol(a) or "?"] += n
        total = sum(by_cat.values()) or 1
        return {
            "tb_executions": self.tb_exec,
            "tb_without_in_asm": self.unknown_tb,
            "translated_tbs": len(self.tb_insns),
            "insn_executions": total,
            "share_by_category": {k: round(v / total, 5) for k, v in by_cat.most_common()},
            "share_by_cpu": {
                cpu: {k: round(v / (sum(cc.values()) or 1), 5) for k, v in cc.most_common()}
                for cpu, cc in sorted(by_cpu_cat.items())},
            "insn_executions_by_cpu": {cpu: sum(cc.values()) for cpu, cc in sorted(by_cpu_cat.items())},
            "unique_executed_addrs_by_category": dict(uniq_cat.most_common()),
            "sketch_line_hits": {k: v.most_common(top) for k, v in sorted(line_hits.items())},
            "sketch_functions_by_cpu": {cpu: v.most_common(top) for cpu, v in sorted(func_by_cpu.items())},
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("log")
    ap.add_argument("--sketch-dir", required=True)
    ap.add_argument("--chip", default="esp32")
    ap.add_argument("--json")
    a = ap.parse_args()
    t0 = time.perf_counter()
    lm = LineMap(Path(a.elf), a.chip, a.sketch_dir)
    t1 = time.perf_counter()
    ts = TraceStats(lm)
    ts.feed(Path(a.log))
    t2 = time.perf_counter()
    rep = ts.report()
    t3 = time.perf_counter()
    rep["timing_s"] = {"load_linemap": round(t1 - t0, 3), "parse_log": round(t2 - t1, 3),
                       "map_and_aggregate": round(t3 - t2, 3)}
    rep["log_MB"] = round(Path(a.log).stat().st_size / 1e6, 1)
    txt = json.dumps(rep, indent=1)
    print(txt)
    if a.json:
        Path(a.json).write_text(txt)


if __name__ == "__main__":
    main()
