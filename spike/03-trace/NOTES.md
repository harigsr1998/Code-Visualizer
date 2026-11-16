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
