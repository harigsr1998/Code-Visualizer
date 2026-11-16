# Spike step 3 — Trace: running log

Worker: trace (branch `spike/trace`). Brief: `Claude_CLI/parallel/brief-trace.md`.
QEMU: Espressif `esp_develop_9.2.2_20260417` (`qemu-system-xtensa.exe`), Windows 11, 16 logical cores.
All timing runs went through `parallel/run-heavy.ps1`.

## Folder contents

| Path | What | Licence zone |
|---|---|---|
| `bench/` | Benchmark sketch: fixed CPU work per chunk on core 1 (`loop()`) and on a task pinned to core 0 | firmware (fixture only) |
| `mapdemo/` | Mapping fixture: static helpers, header-only template, `switch` state machine (things `-Os` inlines) | firmware (fixture only) |
| `tools/paths.py` | Tool paths, ports (5560–5569; `TRACE_PORT_BASE` env var) | app |
| `tools/linemap.py` | DWARF line table (from `objdump --dwarf=decodedline`) → sorted table → bisect lookup; classifies sketch / arduino / idf / toolchain / rom / nodebug | app |
| `tools/qsession.py` | Starts QEMU as a separate process; UART0 over TCP with host timestamps; HMP monitor client | app |
| `tools/rsp.py` | Minimal GDB remote-protocol client (halt, continue, per-vCPU PC, step, breakpoints, memory) | app |
| `tools/bench.py` | Overhead of each capture method (17 modes) | app |
| `tools/exectrace.py` | Analyses an `in_asm,exec,nochain` log: instruction-weighted shares, per-core split, line hits | app |
| `tools/tracerun.py` | Runs a sketch, turns the exec trace on at runtime for N s, analyses it | app |
| `tools/gdbstep.py` | GDB-stub costs: breakpoints, instruction steps, line steps, memory reads, live variable sampling | app |
| `tools/gdblocals.py` | Real `xtensa-esp32-elf-gdb` at breakpoints: args/locals/backtrace at `-Og` vs `-Os` | app |
| `tools/inlinestat.py` | `addr2line -i` over all sketch code: inlined addresses, foreign inlined headers | app |
| `results/` | JSON/text outputs of the runs (committed) | — |

Tools used and their licences (all run as **separate processes**, none linked):
- Espressif QEMU 9.2.2: GPL-2.0 (QEMU zone, separate process).
- binutils `objdump`, `nm`, `addr2line` (crosstool-NG esp-14.2.0): GPL-3.0.
- `xtensa-esp32-elf-gdb` 17.1: GPL-3.0.
- Python 3.13 stdlib: PSF licence. No third-party Python packages.
- Our scripts: app zone (planned AGPLv3); no licence headers added, as instructed.

Raw traces are written to the session scratch folder (`TRACE_SCRATCH`) and deleted after analysis; `*.log` is git-ignored anyway.

## Log

### 1. Is the TCG plugin interface available? No.

```
> qemu-system-xtensa.exe -plugin help
qemu-system-xtensa.exe: -plugin help: plugin interface not enabled in this build
```

- The package has only `bin/qemu-system-xtensa.exe` (71.7 MB) and `qemu-system-xtensaw.exe`, no DLLs, no `qemu-plugin.h`, no `qemu_plugin_api.lib`: a **static** build.
- Espressif's Windows build script ([`configure-win.sh`](https://raw.githubusercontent.com/espressif/qemu/esp-develop/.github/workflows/scripts/configure-win.sh), esp-develop HEAD, read 2026-11-16) configures with `--static --without-default-features` and no `--enable-plugins`. The Linux script ([`configure-native.sh`](https://raw.githubusercontent.com/espressif/qemu/esp-develop/.github/workflows/scripts/configure-native.sh)) is the same minus `--static`.
  - *Inferred, not verified on Linux:* `--without-default-features` also switches off plugins on Linux builds. Upstream `configure` also rejects plugins in a `--static` build (from memory of the upstream source; to confirm when building).
  - Not checked: whether the tagged release `esp-develop-9.2.2-20260417` used exactly these scripts (we read branch HEAD).
- Upstream QEMU supports TCG plugins on Windows since 8.2 (Nov 2023). The build generates `qemu_plugin_api.lib` with `dlltool` from `qemu-plugins.symbols`, and plugins are DLLs linked against it ([patch 27/29](https://patchew.org/QEMU/20231103195956.1998255-1-alex.bennee@linaro.org/20231103195956.1998255-28-alex.bennee@linaro.org), [patch 29/29](https://patchew.org/QEMU/20231103195956.1998255-1-alex.bennee@linaro.org/20231103195956.1998255-30-alex.bennee@linaro.org)). A MinGW link-order problem is reported in [qemu#1972](https://gitlab.com/qemu-project/qemu/-/issues/1972) (community report; status not checked).
- Community: an esp32.com user hit the same error and asked how to enable it ([thread](https://esp32.com/viewtopic.php?p=149685); found by search, the page itself was behind a bot check, so the answer is unknown).
- **What it would take:** build Espressif's QEMU from source with MSYS2/MINGW64, **non-static** (`--enable-plugins`, without `--static`), and ship its DLLs (glib, gcrypt, pixman, SDL2, slirp, …) beside it. See the install request in `parallel/status-trace.md`.

### 2. Built-in capture options that do exist

- `-d help` lists `exec`, `nochain`, `in_asm`, `cpu`, `int`, `trace:PATTERN`, plus `-dfilter <ranges>` (restricts `exec`/`in_asm`/`cpu` logging to address ranges).
- `-trace help`: 4,013 trace events, including `exec_tb`, `exec_tb_exit`, and Espressif-specific ones (`esp32_frc_timer_*`, …). Backend: `log` (writes to the `-D` file, format `exec_tb tb:<host> pc=0x<pc>`, **no CPU index**).
- `-icount [shift=N|auto][,rr=record|replay,rrfile=…]`: record/replay exists (not yet tested here).
- Everything can be switched **at runtime from the HMP monitor**: `logfile <path>`, `log exec,nochain`, `log none`, `trace-event exec_tb on|off`. So the product can trace only while a view needs it.
- HMP `info registers -a` returns every CPU's `PC=` **while the guest runs** (non-stop).
- `-d exec` line format: `Trace <cpu_index>: <host ptr> [<cs_base>/<pc>/<flags>/<cflags>]` — **includes the vCPU index** (0 = PRO_CPU, 1 = APP_CPU).

### 3. Benchmark design note (CPU speed is not modelled)

The step 1 sketch mostly sleeps in `delay()`, so its tick rate hides CPU slowdowns. `bench/` does a fixed xorshift loop (4 M iterations per chunk on core 1, 1 M per chunk on core 0) and the host timestamps each UART line.
- At `-Og` the loop body is ~12 instructions in 2–3 translation blocks per iteration (`objdump -d`): blocks at `0x400d1775` (loop test), `0x400d1778` (body, **exactly once per iteration** — used as the trace-completeness marker), `0x400d176d` / `0x400d178d` + `0x400d1773` (if/else arms). `-Os` marker: `0x400d171c`.
- First run with 200 k iterations/chunk: `guest_ms` 1–2 ms per chunk. **QEMU (default mode) runs this loop far faster than a 240 MHz ESP32**: ~170 M iterations/s ≈ 2 G guest instructions/s on core 1, vs. an estimated ~16 M iterations/s on silicon (~15 cycles/iteration at 240 MHz; an estimate from the instruction count, not measured on hardware). The virtual clock follows host time, but instruction speed isn't throttled.
  - *Product:* CPU-bound timing in default mode is ~10× optimistic. Only `-icount` ties guest time to instructions. Fidelity indicator material; also relevant to step 2's timing work.

### 4. Smoke test of the harness (short windows; superseded by the full run below)

- Full `-d exec,nochain` in **real-time mode**: QEMU logs only ~150 k blocks/s (11.7 MB/s), the guest slows by >1000×, and the chip **resets from the watchdog** (`rst:0x7 (TG0WDT_SYS_RESET)`) on both attempts. Heavy tracing needs `-icount`, so guest time slows with the CPU.
- `-trace exec_tb` without `nochain` (chained): 2.5× slower, but logged only **85 of ~200 M** loop iterations. Without `nochain`, chained blocks bypass the logging, so it's useless for coverage.
- GDB PC sampling at the maximum rate: 2,000–3,100 samples/s, but the guest slows 40–160×, and samples land in critical-section/timer code, not the hot loop: constant halting while the clock follows host time turns the run into interrupt handling.
- HMP `info registers -a` at the maximum rate (~300/s): the chip panicked on **both** attempts (cache error; `spinlock_acquire` assert). Not yet known whether the sampling causes it; re-tested at 100 Hz below.

### 5. Research (rule 12): what docs and community say about these findings

Legend: **[official]** = QEMU/Espressif docs or source; **[community]** = forum/blog/issue, unverified; **[ours]** = our own test here.

- **Watchdog under slow emulation.** [official] Espressif's QEMU README: Timer Group watchdogs are emulated, TG0 WDT is enabled at reset; disable with `-global driver=timer.esp32.timg,property=wdt_disable,value=true`; the RTC WDT is not emulated ([esp-toolchain-docs qemu/esp32 README](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/esp32/README.md), via a mirror in search results). [ours] `-device timer.esp32.timg,help` on our build lists `wdt_disable=<bool> (default: false)`. Disabling it is a fidelity change (a real board would reset), so the product must show it.
- **`nochain` is required for complete `exec` logs and is slow.** [official/community] QEMU developers on qemu-devel: without `nochain` you only get exec tracing for the first block of a chained run; Alex Bennée's notes rate `-d cpu,nochain` "slow" and show `-dfilter` to limit volume ([gist](https://gist.github.com/stsquad/41794ab268013b2eb0d304df14744eaf), [qemu-devel 2016-06](https://lists.gnu.org/archive/html/qemu-devel/2016-06/msg08153.html)). [ours] chained `exec_tb` logged 85 of ~200 M iterations.
- **GDB single-step semantics.** [official] QEMU gdbstub docs: the single-step mask defaults to `ENABLE|NOIRQ|NOTIMER` (no interrupts or timers during a step), changeable with `maintenance packet Qqemu.sstep=<hex>`; in record/replay mode only `ENABLE` is supported ([QEMU docs: gdb](https://qemu.readthedocs.io/en/master/system/gdb.html), [patch](https://patchew.org/QEMU/20211111110604.207376-1-pbonzini@redhat.com/20211111110604.207376-4-pbonzini@redhat.com)).
- **Record/replay.** [official] `-icount shift=auto,rr=record,rrfile=f.bin`, then the same with `rr=replay`; reverse debugging works by snapshot + replay forward; disk changes go to an overlay; the same number of serial ports is required ([QEMU docs: replay](https://qemu.readthedocs.io/en/master/system/replay.html)). Not yet tested on the Espressif machine — see below.
- **icount `shift=auto`.** [official, source/patches] the shift adapts at runtime when the guest falls behind host time ([icount.c](https://qemu.googlesource.com/qemu/+/refs/heads/block/softmmu/icount.c)); `shift=auto` is incompatible with `align=on` and `sleep=on` ([launchpad #1774412](https://bugs.launchpad.net/qemu/+bug/1774412)).
- **ESP-IDF's own QEMU debugging flow.** [official] `idf.py qemu --gdb monitor` + `idf.py gdb` ([ESP-IDF v5.5.5 QEMU guide](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/api-guides/tools/qemu.html)) — i.e. Espressif's supported route for stepping is the gdbstub, the same as our option (c).
- **ESP32 GDB stepping problems** reported by users are on real hardware via OpenOCD, not QEMU ([esp32.com p=148777](https://esp32.com/viewtopic.php?p=148777), [VisualGDB forum](https://sysprogs.com/w/forums/topic/receive-sigint-when-single-stepping-on-esp32/)) [community]; no espressif/qemu issue about gdb stepping or tracing was found by search.

### 6. Overhead benchmark (full run, 2026-11-16 13:42–13:54, inside the lock)

`python tools/bench.py --window 10` on `build-a-esp32-og-bench`, default (real-time) mode unless the mode says `icount`. One QEMU boot per mode; the capture method is switched on after chunk 3; a mode that panics gets one retry. Raw: `results/overhead-build-a-esp32-og-bench.json`.
Throughput = loop iterations per second on core 1 (from host-timestamped chunk lines); the hot loop is ~2.5 translation blocks per iteration, so the base rate is ~420 M blocks/s.

| Mode | Core 1 M iter/s | vs base | Notes |
|---|---|---|---|
| base | **169.3** | 1.00 | core 0: 102.6 |
| `-icount shift=auto` | 24.8 | 0.15 (6.8× slower) | core 0: 67.0 (round-robin vCPU scheduling) |
| gdbstub attached, idle | 153.1 | 0.90 | first attempt hit the known boot cache panic |
| **gdbstub PC sampling, 100 Hz** | **140.4** | **0.83** | 106 samples/s; 89 % (core 1) / 54 % (core 0) of samples in `workChunk` → accurate |
| gdbstub PC sampling, max rate | 0.61 | 0.004 (280× slower) | 1,616 samples/s, but only 4–5 % in `workChunk`: the run becomes interrupt handling → useless |
| HMP `info registers -a`, 100 Hz | 143.3 | 0.85 | 107 samples/s, but only 52 % / 34 % in `workChunk` → **biased (stale PC)** |
| HMP `info registers -a`, max rate | — | — | ~500 samples/s; **chip crashed** (`StoreProhibited`); 3 of 3 runs where sampling ran crashed (incl. smoke test) |
| `-d nochain` alone | 21.6 | 0.13 (7.8× slower) | the price of complete exec logs, before any logging |
| `-d in_asm` | 136.4 | 0.81 | translation-time only |
| `-d exec` (chained) | 43.0 | 0.25 | 106 k blocks/s logged, **marker count 0** → incomplete |
| `-trace exec_tb` (chained) | 63.2 | 0.37 | 120 k blocks/s logged, marker 0 → incomplete; no CPU index |
| **`-d exec,nochain`, all code** | — | — | **TG0 watchdog reset** (2/2); logging ceiling **140–148 k blocks/s** (10.7–11.3 MB/s) vs ~420 M blocks/s needed |
| `-d exec,nochain` + `-dfilter` sketch | ~0.02 | ~0.0001 | ESP-IDF **task watchdog** abort (IDLE0 starved) 2/2; 88–100 k blocks/s (here *all* hot code is sketch code, the worst case) |
| icount + gdbstub 100 Hz | 19.0 | 0.77 of icount base | 106 samples/s; 74 % / 89 % in `workChunk` |
| icount + HMP 100 Hz | 17.1 | 0.69 of icount base | 106 samples/s; 60 % / 86 % |
| icount + `-d exec,nochain`, all code | ~0.015–0.026 (marker) | — | 270–350 k blocks/s logged; TG0 watchdog reset in 1 of 2 runs (`shift=auto` re-syncs to host time, so it doesn't fully protect a slowed guest) |
| icount + `exec,nochain` + `-dfilter` | ~0.03–0.05 (marker) | — | 146–185 k blocks/s; task-watchdog reset 2/2 |

Takeaways:
- **QEMU's text logging tops out at ~0.1–0.35 M blocks/s**: 3–4 orders of magnitude below the emulator's speed on hot code. Full tracing of hot code isn't a live option with the built-in logger, in either mode.
- `nochain` alone costs ~7.8×, even when nothing is logged (`-dfilter` doesn't remove it).
- Chained logging (`exec` or `exec_tb` without `nochain`) misses essentially everything inside loops.
- **gdbstub PC sampling at ~100 Hz is the only cheap and correct "where is each core now" signal** (−17 %).
- HMP `info registers` while running: stale PC (biased), and unsafe at high rates. My hypothesis, not verified in source: dumping Xtensa registers from the monitor thread syncs the windowed register file while the vCPU runs. Product: never poll HMP registers on a running guest.
- Watchdogs fire when tracing slows the guest: the hardware TG0 WDT (QEMU can disable it: `-global driver=timer.esp32.timg,property=wdt_disable,value=true`) and the ESP-IDF task WDT (firmware config only: `CONFIG_ESP_TASK_WDT_*`). Both are fidelity changes.

### 7. Record/replay: not usable on ESP32

```
qemu-system-xtensa.exe: Record/replay: Record/replay is not supported with multiple CPUs
```
- `-machine esp32 -smp 1` is accepted, but the chip prints nothing at all (not even the ROM banner) in 10 s. The ESP32 machine needs its two CPUs.
- So "record cheaply, replay with full tracing offline" isn't available in this QEMU. It would need QEMU work: multi-CPU record/replay is an upstream limitation, so that's a large QEMU-zone project.
- [official] Docs: [QEMU record/replay](https://qemu.readthedocs.io/en/master/system/replay.html). No espressif/qemu issue about rr found by search.

### 8. Spurious cache panic also under `-icount` when the host is saturated (for the coordinator / step 2)

While the network worker's ESP-IDF builds held the host at 100 % CPU (13:55–14:05), mapdemo `-Og` under `-icount shift=auto` panicked with "Cache disabled but cached memory region accessed" in **4 of 8 boots**: 2 of 2 traced runs, then 1 of 3 traced + 1 of 3 untraced. Tracing isn't the cause; host load is the likely factor. Step 2 measured 0 of 15 on an idle host. Rule 4's "use icount for functional tests" holds only on a quiet host.

### 9. Address → source mapping

**Line table.** `objdump --dwarf=decodedline` of the whole ELF takes 0.17 s (147 k text rows → 37,919 unique addresses). Python load + `nm`: 0.39 s. Lookup is a binary search. In C++ (gimli/libdwarf-style direct DWARF parsing) this would be milliseconds. **Precompute once per build; per-address lookup is trivially fast** (Python ~1 M lookups/s; analysis of a 1.26 M-block trace took 1.3 s, including expanding blocks to instructions). Cache per translation block: expand a block's instruction list to lines once (at `in_asm` time), then each execution is just a counter.

**Who owns what (path A, ESP32, idle-ish sketches, icount, 5–10 s windows, instruction-weighted):**

| Build | sketch | Arduino core | ESP-IDF | toolchain libs | ROM | no line info |
|---|---|---|---|---|---|---|
| step 1 sketch `-Og` (5 s) | **0.010 %** | 0.020 % | 95.6 % | 0.20 % | 4.2 % | 0 |
| mapdemo `-Og` (10 s) | 0.029 % | 0.019 % | 96.95 % | 0.18 % | 2.8 % | 0 |
| mapdemo `-Os` (10 s) | 0.007 % | 0.001 % | 95.6 % | 0.001 % | 4.4 % | 0 |

- Unique executed addresses (mapdemo `-Og`): IDF 1,636, toolchain 484, Arduino 108, ROM 93, sketch 87.
- What dominates: `esp_cpu_compare_and_set` (29–33 %, core 0), `xPortEnterCriticalTimeout` (10–15 %), `wdt_hal_config_stage`, tick hooks. That's spinlock spinning plus FreeRTOS ticks. Under icount the two vCPUs run round-robin, so a core spinning on a lock the other core holds burns instructions (an emulation artefact, not real-chip behaviour).
- **Every executed non-ROM address had line info** (`nodebug` 0). In path A the precompiled ESP-IDF libs carry DWARF (`-ggdb -gdwarf-4` from the core's `flags/cpp_flags`), but their file paths are the CI machine's (`/home/runner/work/esp32-arduino-lib-builder/...`), so the source isn't on disk. Showing IDF source in path A needs path substitution to a local ESP-IDF of the matching version (unverified that it's an exact match). Path B compiles IDF from local source, so the paths resolve.
- ROM: no line info; symbols come from Espressif's ROM ELF (`esp-rom-elfs/20241011/esp32_rev300_rom.elf`), e.g. `_ResetVector`, `ets_waiti0`.
- **Product consequence:** the sketch is a tiny fraction of execution. The live overlay must filter to user code (and maybe one level of called library functions); "everything executed" views need aggregation (per function/component), not per-line.

**Inlining and `-Os`** (`tools/inlinestat.py`, `addr2line -f -i` over every sketch line-table address):

| | step 1 `-Og` | step 1 `-Os` | mapdemo `-Og` | mapdemo `-Os` |
|---|---|---|---|---|
| sketch functions as real code | 4 | 4 | 6 | **4** (`sensorSample`, `MovingAverage::add` fully inlined into `loop`) |
| sketch code bytes | 204 | 211 | 394 | 324 |
| line-table rows (sketch) | 32 | 34 | 77 | 69 |
| distinct sketch lines that own code | 22 | 22 | 51 | **42 (−18 %)** |
| line-table addresses inside inlined code | 1/33 | 1/35 | 10/77 (13 %) | **23/69 (33 %)** |
| innermost frame is a non-sketch header | 1 (`Esp.h`) | 1 | 0 | 0 |

- The step 1 sketch is too small to show `-Os` damage (two tiny functions in separate files); hence `mapdemo/`.
- Lines with code at `-Og` but none at `-Os` (mapdemo):
  - `if (v > 200) { v = 200; }` (`mapdemo.ino:20–21`): the clamp is folded into neighbouring instructions and can't be highlighted;
  - `avg.add(s);` (`:37`): the call line owns no code; its inlined body is attributed to `filter.h` lines;
  - the two identical `dwell_ = 0;` lines in different `case` branches (`blinker.cpp:20`, `:26`): no rows of their own at `-Os`, because of **tail merging (cross-jumping)**. Confirmed in `objdump -d -l`: the `Rising` case (`:19`, `state_ = High`) does `movi a8, 2` and then `j 0x400ee722`. The `High` case (`:25`) jumps there too. The shared store at `0x400ee722`–`0x400ee72a` is attributed to **`blinker.cpp:31–32`** (`state_ = Idle; dwell_ = 0;` in `case Falling:`). So when the chip takes the `Rising` branch, a line-table overlay highlights lines in `case Falling:`. **At `-Os` the overlay can point at the wrong branch.** This is the strongest argument for `-Og` in "clearest visuals" mode, and for marking `-Os` views as approximate;
  - function-entry/closing-brace lines of inlined functions.
- Dynamic effect: mapdemo `-Os` trace has hits only in `loop()` and `Blinker::step` (2 functions vs 4 at `-Og`). `scale`'s line 15 is hit inside `loop`.
- **Product rules from this:**
  1. Use the full inline chain (DWARF `DW_TAG_inlined_subroutine`), not just the innermost line. Light up the innermost *sketch* frame and show the call path (e.g. `scale` ← `sensorSample` ← `loop`). The flowchart's function nodes must collect their inlined instances.
  2. Lines without code must render as "optimized away" (fidelity: approximate), not as "never executed".
  3. Default to `-Og` ("clearest visuals"), as already decided; `-Os` is usable but coarser.
- **Path A vs path B:** in path A the sketch and core are at `-Og` and IDF is at `-Os` with full line info. Path B at `-Og` would change only the IDF part of the mapping (finer lines inside IDF); the sketch-side answer is the same. Since the sketch-side answer doesn't change and the product filters to user code, I didn't do a path B build here (it would re-copy ~600 MB).

### 10. Dual core: yes, the trace says which core

- `-d exec` lines carry the vCPU index (`Trace 0:` = PRO_CPU / core 0, `Trace 1:` = APP_CPU / core 1). Verified on the bench: `workChunk` blocks run on both CPUs; the once-per-iteration marker block is counted separately per CPU (`marker` in the overhead JSON). Sketch `loop()` code appears only on CPU 1 (Arduino's `loopTask` runs on core 1) in all sketch traces.
- The gdbstub exposes each vCPU as a thread (`qfThreadInfo` → `m01,02`); `Hg<thread>` + `p0` reads that core's PC, so the sampler is per-core too.
- `-trace exec_tb` does **not** carry the CPU (format `exec_tb tb:… pc=…`), so prefer `-d exec`. `-d tid` (a separate log per vCPU thread) also exists.
- Caveat: under `-icount` both vCPUs share one host thread (round-robin), so per-core *timing* is emulator scheduling, not real concurrency.

### 11. GDB: what a debugger shows of sketch variables (`tools/gdblocals.py`, real `xtensa-esp32-elf-gdb` 17.1)

Results in `results/gdblocals-build-a-esp32-o{g,s}-mapdemo.txt`.
- **`-Os`**: `break sensorSample` resolves to the **inlined** copy. GDB stops at `mapdemo.ino:15` (inside the inlined `scale`) and reports inline frames properly: `#0 sensorSample (n=26)` → `#1 loop ()` (no address = inline frame). Arguments, `this`, and globals (`g_samples`, `avg`, `blinker` with all members) are readable.
- **`-Og`**: at a breakpoint on function entry, `MovingAverage::add` shows **`this=0x0`, which is wrong** (the object is at `0x3ffc1d4c`). Its location info starts after the prologue. A variable view must stop after the prologue, or flag entry-point values as unreliable.
- `<optimized out>`: 1 value in each build (a local not yet computed at the stop point). This small fixture doesn't show worse variable loss at `-Os`; bigger functions will.
- Path A IDF frames point at CI paths (`/home/runner/...`, "No such file or directory"); see §9.

### 12. Tracing overhead on a typical sketch (`tools/realistic.py`, inside the lock, 14:09)

mapdemo `-Og`: `loop()` does a little work plus `delay(10)`. 15 s windows after tracing is switched on. Raw: `results/realistic-build-a-esp32-og-mapdemo.json`.

| Mode | `loop()`/s | QEMU host CPU (cores) | Logged | Result |
|---|---|---|---|---|
| real time, no tracing | 52.8 | 0.06 | — | ok |
| **real time, `in_asm,exec,nochain` + `-dfilter` sketch ranges** | **56.1** | **0.13** | **1,221 blocks/s, 0.09 MB/s** | **ok: keeps real time** |
| real time, `exec,nochain` everything | — | 1.6–1.9 | 150–165 k blocks/s, 12 MB/s | TG0 watchdog reset, 2/2 |
| icount, no tracing | 10.8 | 0.03 | — | ok |
| icount, `-dfilter` sketch | 14.2 | 0.04 | 383 blocks/s | ok |
| icount, everything | 1.5 | 1.0 | 310–360 k blocks/s, 25–27 MB/s | 1 reboot in 2 runs |

- `loop()` rates differ ±10 % between runs (52.8 vs 56.1); the filtered trace's cost is inside that noise.
- **Conclusion:** for a typical sketch that mostly waits, a sketch-only exec trace is essentially free in real time. The bench (all hot code is sketch code) is the opposite extreme: there the logger's ~0.1–0.15 M blocks/s ceiling and `nochain`'s ~7.8× cost make it unusable (§6).

### 13. Live prototype (question 5): `tools/live.py`

```
python tools/live.py --build build-a-esp32-og-mapdemo --sketch mapdemo --seconds 8
```
- QEMU runs in real time with `-d in_asm,exec,nochain -dfilter <sketch ranges> -D <log>`. The script tails the log while QEMU writes it, maps each block's instructions to lines (cached per block), and prints `core N: now executing <file>:<line>` transitions, then per-line hit counts. Output: `results/live-build-a-esp32-og-mapdemo.txt`.
- 14,317 sketch blocks in the window; processing median **1.5 ms per read batch** (Python).
- Consistency check: `loop()` entry (`mapdemo.ino:33`), `MovingAverage::add` entry (`filter.h:8`) and `Blinker::step` entry (`blinker.cpp:8`) each have exactly **711** executions, once per iteration. The clamp `v = 200;` (`:21`) has 153 (only when `v > 200`). The 500 ms print branch (`:40–41`) is rare, as expected.
- Limitations: the trace goes through a text file; a product version would use a pipe or socket. `-dfilter` filters on a block's *start* address, but control returning from a callee starts a new block inside the sketch, so sketch instructions are covered. Inlined header code inside sketch functions is reported at the header line (use the inline chain, §9).

### 14. Live variables without halting: HMP `xp` reads (functional test, 14:40)

- `xp /1wx <addr>` on the monitor while the guest runs reads physical memory without stopping it (ESP32 data RAM addresses are physical, no MMU). Example reply: `000000003ffc1d3c: 0x00000002`.
- Read rate **833–1,660 reads/s** (one connection, synchronous). Values are correct: `g_samples` rose at 33.6/s while the sketch's own counter (UART) rose at 33.7/s, monotonically.
- Stability: across 9 runs (3× max rate, 3× 100 Hz, 3× no reads, 12 s each, host loaded by another session's builds), **no panic while reads were running** in the 4 runs that got that far. The 2 failures were boot cache panics before any read (the known spurious issue), and the 3 no-read controls were clean. The first exploratory run did panic during reads; given these controls I count it as the spurious panic, not as caused by `xp`.
- Caveats: reads aren't atomic with the guest (a multi-word value or struct can be torn); fine for counters and flags, not for consistent snapshots (halt for those). Registers and locals still need the gdbstub (halted) and DWARF location expressions.

### 15. GDB stub costs for step mode (`tools/gdbstep.py`, inside the lock)

mapdemo `-Og`, our own RSP client, breakpoint on `loop()`, stepping on the core that hit it (thread 2 = core 1). Raw: `results/gdbstep-*.json`.

| Measure | `-icount shift=auto` | default (real time) |
|---|---|---|
| breakpoint hit → continue round trips | 1,457 /s | 1,553 /s |
| instruction single-steps | 4,992 /s (0.2 ms each) | 5,938 /s (0.17 ms each) |
| source-line steps (step until the DWARF line changes; steps *into* callees) | 123 /s, 26.6 instructions per line | 151 /s (repeat run; the first run was reset by the watchdog, see below) |
| memory reads while halted (`m`, 4 bytes) | 14,969 /s | 16,662 /s |
| live variable sampling (halt → read → continue) | 97 /s | 96 /s (reading a rebooting chip) |

- **Real-time stepping: one watchdog reset in 2 runs, cause not established.** The first default-mode run reset the chip (`rst:0x7 (TG0WDT_SYS_RESET)`) after breakpoints and 2,000 single-steps. The repeat (15:05, inside the lock) ran everything cleanly: 1,033 breakpoint round trips/s, 6,082 single-steps/s, **151 line steps/s** (26.6 instructions each), 15,212 reads/s, live sampling 97/s. The control with `-global driver=timer.esp32.timg,property=wdt_disable,value=true` was also clean (1,228 / 6,020 / 169 line steps/s). My hypothesis (each gdb round trip ages the watchdog in real time) is **not confirmed**. Treat it as an intermittent risk: for long stepping sessions, prefer `-icount` or disable the emulated watchdog (and show that in the fidelity indicator).
- The line trail is the expected path: `loop` 33 → 35 → `sensorSample` 18–20 → `scale` 15 → 24 → 36–37 → `MovingAverage::add` (`filter.h` 8–16) → `value` 18 → 38 → `Blinker::step` → 39 → 44–45.
- Single-stepping runs with interrupts and timers masked (QEMU's default `NOIRQ|NOTIMER`, §5), so a step never wanders into an ISR.
- One step is 0.2 ms, so an interactive "step" button is instant. Even a "next line" that steps over a call by single-stepping through it (~25 instructions per line here) takes ~5 ms. Stepping over a long call should set a temporary breakpoint at the return address instead (`Z0`, ~0.7 ms round trip).

## REPORT

Espressif QEMU 9.2.2 (esp_develop_9.2.2_20260417), Windows 11, path A builds of the step 1 sketch plus two fixtures (`bench/`, `mapdemo/`), ESP32. Evidence: the sections above and `results/`.

### Q1. Execution-capture options

| Option | Available? | Complete? | Cost | Per core? | Verdict |
|---|---|---|---|---|---|
| (a) TCG plugins `-plugin` | **No**: "plugin interface not enabled in this build"; static build (`--static --without-default-features`, Espressif CI script) | — | — | — | Needs our own non-static QEMU build (§1, install request) |
| (b1) `-d in_asm,exec,nochain` (+ `-dfilter`) | Yes, switchable at runtime via the monitor | **Yes** (with `nochain`) | Logger ceiling ~0.1–0.35 M blocks/s; `nochain` alone 7.8× on CPU-bound code; filtered user-code trace on a typical sketch ≈ free (§6, §12) | **Yes** (`Trace <cpu>`) | **Best available for the live overlay** |
| (b2) `-d exec` / `-trace exec_tb` without `nochain` | Yes | **No** (85 of ~200 M iterations) | 2.7–4× slower | `exec` yes, `exec_tb` no | Useless for coverage |
| (c) gdbstub `-gdb tcp::port` | Yes (`qfThreadInfo` = one thread per core) | Sampling / stepping | 100 Hz PC sampling −17 %; ~5,000 single-steps/s; ~1,450 breakpoint round trips/s | Yes | **Step mode; cheap "where is each core" sampling** |
| (d1) HMP `info registers -a` while running | Yes | — | Stale (biased) PC; crashed the chip 3/3 at high rate | Yes | **Don't use** on a running guest |
| (d2) HMP `xp` memory reads while running | Yes | — | ~850–1,660 reads/s, no halt, no measurable slowdown | n/a | **Live values of globals/statics** |
| (d3) `-icount …,rr=record/replay` | **No**: "not supported with multiple CPUs"; `-smp 1` doesn't run the ESP32 machine | — | — | — | Not available |
| (d4) `-dfilter`, `-d tid`, runtime `log`/`logfile`, `trace-event` | Yes | — | — | — | Building blocks for (b1) |

### Q2. Overhead (core 1 loop throughput, bench `-Og`, real time unless noted; §6)
- No capture: 169 M iterations/s (QEMU runs this loop ~10× faster than a 240 MHz ESP32 would, an estimate; CPU speed isn't modelled in default mode).
- `-icount shift=auto`: 0.15× (6.8× slower). gdbstub attached, idle: 0.90×. gdbstub PC sampling at 100 Hz: **0.83×**. HMP sampling at 100 Hz: 0.85× (but biased). `-d in_asm`: 0.81×. `nochain` alone: **0.13×**.
- Full exec tracing of hot code: thousands of times slower (logger ~150 k blocks/s vs ~420 M blocks/s), and the chip resets from the hardware TG0 watchdog (or ESP-IDF's task watchdog with `-dfilter`).
- Typical sketch (mostly waiting), sketch-only exec trace: no measurable slowdown (56.1 vs 52.8 loop/s), 0.13 host cores, 0.09 MB/s (§12).

### Q3. Address → source mapping (§9)
- **Shares of executed instructions (idle-ish sketches): sketch 0.007–0.03 %, Arduino core 0.001–0.02 %, ESP-IDF 95.6–97 %, toolchain libs ≤0.2 %, ROM 2.8–4.4 %.** Every non-ROM address had line info (`nodebug` 0). Most IDF time is spinlock spinning and FreeRTOS tick handling under emulation.
- **Inlining and `-Os` hurt.** On mapdemo:
  - functions that exist as code: 6 → 4;
  - sketch lines that own code: 51 → 42 (−18 %);
  - line-table addresses inside inlined code: 13 % → 33 %.
  - Worst case, **tail merging makes one `switch` branch execute code attributed to another branch's lines** (confirmed in the disassembly). At `-Os` the overlay can point at the wrong line.
  - The step 1 sketch alone is too small to show any of this (22 = 22 lines).
- **Speed:** full-ELF line table in 0.17 s (`objdump`) / 0.39 s (Python incl. `nm`), 38 k addresses, binary-search lookup. **Precompute the table per build, cache lines per translation block, and count per block at run time.** Live mapping is cheap (Python: 1.26 M blocks analysed in 1.3 s; the live prototype takes 1.5 ms per batch).
- **Path A vs B:** path A's precompiled IDF has DWARF (`-Os`, CI paths → source not on disk without path mapping). Path B would give `-Og` IDF lines and local paths. That doesn't change the sketch-side answer; not built here (it would re-copy ~600 MB).

### Q4. Dual core
**Yes.** `-d exec` prints the vCPU index per block (0 = PRO_CPU, 1 = APP_CPU), and the gdbstub exposes each core as a thread with its own PC. `loop()` code showed up on core 1 only, and the bench's core 0 task on core 0. `-trace exec_tb` lacks the CPU index. Under `-icount` the cores are scheduled round-robin, so per-core timing is emulator scheduling.

### Q5. Working prototype
`tools/live.py` (§13): runs the sketch in real time and prints a live `core 1: now executing mapdemo.ino:NN` / `filter.h:NN` / `blinker.cpp:NN` stream plus per-line hit counts, with consistent counts (711 per-iteration lines). `tools/tracerun.py` does the same as a whole-system capture with category shares.

### Q6. Recommendation

| Product feature | Method for v1 (no QEMU changes) | Why |
|---|---|---|
| **Live overlay** (flowchart / line highlight, hit counts) | `-d in_asm,exec,nochain` + `-dfilter` = user-code address ranges from DWARF, switched on/off at runtime through the QEMU monitor; the app reads the stream (file → later a pipe/socket), caches block → lines, counts per line and per core | Complete for user code, says which core, essentially free for typical sketches in real time |
| Overlay fallback when user code is CPU-hot (log rate > ~50 k blocks/s) | Switch to **gdbstub PC sampling at ~100 Hz** (heat map, −17 %), or to a "slow-motion" mode: `-icount` + the QEMU watchdog disabled | The text logger can't keep up with hot code, and the watchdogs fire. Show it in the fidelity indicator. |
| **Step mode** | gdbstub with our own RSP client (or bundled `xtensa-esp32-elf-gdb`): breakpoints, single-step with IRQs masked, line steps driven by our DWARF line table, step-over via temporary breakpoints | ~5,000 steps/s, ~1,450 breakpoint round trips/s: instant for a person |
| **Variables** | Globals/statics live: HMP `xp` reads while running (~1,000/s, no halt; may tear multi-word values). Consistent snapshots, locals, registers: halt via gdbstub, read memory (15 k reads/s) and evaluate DWARF locations (gdb does this; our own evaluator later) | Locals need halting and DWARF; `this`/args at function entry can be wrong at `-Og` (§11) |
| Build profile | Keep **`-Og` as the default** ("clearest visuals"); mark `-Os` views as approximate | `-Os` loses 18 % of lines, a third of code is inlined, and tail merging can show the wrong branch |

**What's missing, and the cost:**
1. **TCG plugin** (the proper live-trace path: per-block callbacks without `nochain` or text logging, likely far cheaper — *unmeasured*).
   - Cost: build Espressif QEMU ourselves with MSYS2, **non-static**, and ship its DLLs (glib/gcrypt LGPL, etc.), repeated for every Espressif release. Plus a plugin (C) in **its own repo, QEMU zone: GPL-2.0-or-later or MIT, to be decided**, talking to the app only over a socket or shared memory.
   - Licence and redistribution: LGPL/GPL source offers if we bundle QEMU (prefer download on demand).
   - Install request in `parallel/status-trace.md`. Recommended as a separate, bounded follow-up spike once the product shape is clear.
2. **No record/replay** for the dual-core ESP32 machine, so no "record cheaply, trace later" and no reverse debugging. That would be a large QEMU-zone project.
3. **Watchdogs:** when tracing slows the guest, the hardware TG0 WDT can be disabled in QEMU (`timer.esp32.timg` `wdt_disable`). The ESP-IDF task WDT can only be changed in firmware config. Both are fidelity changes to show to the user.
4. **ESP-IDF source for path A** needs path mapping from the CI paths to a matching local ESP-IDF (unverified match). Path B avoids it.
5. **Inline-aware DWARF handling** (inline chains, `DW_TAG_inlined_subroutine`, location lists) is needed in the app's analysis engine. Plan for gimli/libdwarf-class parsing, not just line tables.

**Licences of what this step used:** QEMU GPL-2.0 (separate process); binutils and gdb GPL-3.0 (separate processes; fine next to an AGPLv3 app, with redistribution duties if bundled); Python stdlib (PSF); our scripts are app zone. Nothing was linked; no QEMU-zone code was written (no plugin, since plugins aren't possible with the shipped build).

**Not done / caveats:**
- No path B or ESP32-S3 traces (the S3 shares the QEMU build, so plugin availability is the same; mapping shares weren't measured).
- The plugin speed-up is an expectation, not a measurement.
- `ruff` isn't installed, so no linter ran on the Python tools; they were exercised by running them.
- Silicon cycle counts are estimates.
