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
