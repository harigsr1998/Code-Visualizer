# Spike log

Running notes, newest at the bottom. Findings get summarised into `REPORT.md` at the end.

## 2026-11-16 — Step 0: version selection

Release facts (checked 2026-11-16):

| Component | Latest stable | Notes |
|---|---|---|
| arduino-esp32 core | **3.3.12** (2026-09-18) | Based on **ESP-IDF v5.5.5**. 4.0.0-RC1 (based on ESP-IDF v6.1) is a pre-release only. |
| ESP-IDF | v5.5.5 (matches the Arduino core) | v6.x exists, but no stable Arduino core supports it yet |
| Espressif QEMU | esp-develop-9.2.2-20261109 (2026-11-09) | Prebuilt Windows archives exist for `qemu-xtensa` and `qemu-riscv32` (x86_64-w64-mingw32) |

**Proposed pin for the spike:**
- arduino-esp32 **3.3.12**
- ESP-IDF **v5.5.5**
- QEMU: the version that ESP-IDF v5.5.5's `idf_tools.py` installs. Also try the latest standalone release if needed.

Revisit when arduino-esp32 4.0 goes stable.

**Product implication:** real users' code will target different Arduino core and ESP-IDF versions. The product will eventually need to manage several toolchain versions side by side, choosing one per project.

Sources:
- https://github.com/espressif/arduino-esp32/releases
- https://github.com/espressif/qemu/releases
- https://docs.espressif.com/projects/arduino-esp32/en/latest/esp-idf_component.html
- https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html

**Arduino-as-component requirements:** add with `idf.py add-dependency "espressif/arduino-esp32^3.3.12"`; it requires `CONFIG_FREERTOS_HZ=1000`.

Boards Manager URL (stable): `https://espressif.github.io/arduino-esp32/package_esp32_index.json`

### Issue found: spaces in the repo path
The repo lives at `D:\MBA\Term 5\AABI\Project\Claude CLI\Development`, and that path contains spaces. ESP-IDF's build system historically does not support spaces in the project path or the IDF path. Options:
1. Move the repo to a path without spaces (e.g. `D:\Dev\Code-Visualizer`).
2. Use a directory junction without spaces that points at the repo (e.g. `C:\cv` → `Development`).
3. Build out-of-tree: copy spike projects into a space-free scratch folder before building.

**Update (2026-11-16):** the user renamed `Claude CLI` → `Claude_CLI`. The path is now `D:\MBA\Term 5\AABI\Project\Claude_CLI\Development`. That's still not space-free (`Term 5`), so this issue stays open. ESP-IDF v5.5 docs confirm it: "the ESP-IDF build system does not support spaces in the paths to either ESP-IDF or to projects" (https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c5/get-started/windows-setup.html). The same page caps the ESP-IDF install path at 90 characters.

**Product implication:** users will have spaces in their paths too (e.g. `C:\Users\John Smith\...`). The app must handle this itself, e.g. by staging builds in a space-free work directory.

Disk space: C: 138 GB free, D: 185 GB free. The toolchains need roughly 4–6 GB in total.

**Resolved (2026-11-16):** the whole project moved to `D:\Coding\Code_Visualizer\Claude_CLI\Development`, which has no spaces (option 1). The product implication above still stands.

## 2026-11-16 — Toolchain check after the move

All verified:
- ESP-IDF v5.5.5 via EIM 0.21.1 (`C:\Espressif\v5.5.5\esp-idf`); `idf.py --version` → v5.5.5.
- Arduino-ESP32 core 3.3.12 in arduino-cli 1.6.0.
- Espressif QEMU 9.2.2 (`esp_develop_9.2.2_20260417`), xtensa + riscv32.

**Quirk:** QEMU installed into `C:\Espressif\tools\tools\` (doubled), because `idf_tools.py` uses `%IDF_TOOLS_PATH%\tools` while EIM puts its tools directly in `C:\Espressif\tools`. It isn't on the PATH that `eim shell` exports. Spike scripts call it by absolute path.

**Product implication:** the app shouldn't rely on PATH to find emulators or toolchains. It should keep an explicit, configurable path per tool and version.

## 2026-11-16 — Step 1: Build

Setup: `01-build/` (see its README). One multi-file sketch: `.ino` + `counter.h/.cpp` + `src/mathutil/fib.*`. Path B compiles the **same files in place** through a 2-line wrapper (`#include <Arduino.h>` + `#include "sketch_multi.ino"`). Every image was boot-tested with `boot-smoke.ps1` (10 s in QEMU, UART0 captured).

### Results: 8/8 build, 8/8 boot and run the sketch in QEMU

| Build | Clean build | App size | QEMU: sketch output | Reported CPU |
|---|---|---|---|---|
| A esp32 -Os | 49 s | 269,776 B | ✅ | 240 MHz |
| A esp32 -Og | 55 s | 274,880 B | ✅ | 240 MHz |
| A esp32s3 -Os | 64 s | 274,256 B | ✅ | 80 MHz ⚠ |
| A esp32s3 -Og | 48 s | 279,024 B | ✅ | 80 MHz ⚠ |
| B esp32 -Os | 322 s (incl. downloads) | 221,696 B | ✅ | 160 MHz |
| B esp32 -Og | 291 s | 245,712 B | ✅ | 160 MHz |
| B esp32s3 -Os | 346 s (incl. S3 downloads) | 244,576 B | ✅ | 80 MHz ⚠ |
| B esp32s3 -Og | 367 s | 272,672 B | ✅ | 80 MHz ⚠ |

Rebuild after touching one `.cpp` (the everyday edit→run loop): **A 12.8 s, B 10.6 s**. Path B reconfigure (after a CMake change): 20.6 s. Machine: 16 logical cores; ccache on (EIM sets `IDF_CCACHE_ENABLE=1`). All images are 4 MB merged flash images. Path A gets one from the core's own post-build step (`*.ino.merged.bin`); path B gets one from `esptool merge_bin --fill-flash-size 4MB @flash_args`.

### Findings

1. **Both paths work for both chips.** No blocker for step 1.
2. **Flash mode: the Arduino default (QIO) doesn't run in QEMU.** The bootloader logs `qio_mode: Failed to set QIE bit`, then the app asserts in `__esp_system_init_fn_init_flash` and reboot-loops. QEMU's flash model doesn't support the QE bit. **DIO works** (`--board-options FlashMode=dio`; ESP-IDF's default is already DIO).
   - *Product:* the simulated build differs from what most real boards flash (QIO). The options are to show this in the fidelity indicator, or to patch QEMU's flash model. A patch is a QEMU source change (GPLv2 obligations if we distribute it); see the licensing guardrails.
3. **CPU clock differs between paths.** ESP32 runs at 240 MHz on path A: the Arduino board menu's "CPU Frequency" (default 240) is applied by the core at startup. Path B runs at 160 MHz (ESP-IDF's default `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ`). The core's own prebuilt sdkconfig also says 160 for ESP32 and 240 for S3.
   - *Product:* path B must carry the Arduino board-menu settings (CPU freq, flash mode/size, partition scheme, USB-CDC, PSRAM…) into sdkconfig, or the same sketch behaves differently.
4. **ESP32-S3 reports 80 MHz under QEMU on both paths**, although the config asks for 240/160. This looks like a gap in QEMU's S3 clock model. Investigate in step 2. It matters for timing fidelity.
5. **Optimization control.** Path A's `--optimize-for-debug` (`-Og -g3`) only recompiles the sketch and the Arduino core. The ESP-IDF libraries are precompiled with `-Os` (`esp32-libs/3.3.12/sdkconfig`: `CONFIG_COMPILER_OPTIMIZATION_SIZE=y`) with fixed settings. Path B compiles **everything** at the chosen level, with our own sdkconfig.
   - *Product:* "clearest visuals" can only cover the framework internals with path B.
6. **Download size (path B).** arduino-esp32 pulls in every optional Arduino library whatever the sketch uses: 33 components for ESP32, 36 for S3. That's ~600 MB in `managed_components/`, mostly `esp-sr` (speech recognition, 331 MB, S3 only) and `esp-zboss-lib` (Zigbee, 113 MB). There's another ~600 MB in the component cache `%LOCALAPPDATA%\Espressif\ComponentManager`. Path A ships the prebuilt libs inside the core instead (`esp32-libs` 180 MB, `esp32s3-libs` 324 MB).
   - *Product:* investigate trimming unused dependencies, and share one component cache across projects.
7. **Build time.** A clean path B build takes 5–6 min, versus ~1 min for path A. The rebuild loop is the same (~11–13 s).
   - *Product:* build the framework once per (chip, optimization, config) and reuse it across projects, as Arduino does with its prebuilt libs.
8. **`.ino` handling (path B).** The wrapper works only because the sketch declares functions before use. The Arduino IDE also generates prototypes and concatenates multiple `.ino` files.
   - *Product:* path B needs that preprocessing step. Check whether `arduino-cli`'s own preprocessor can be reused.
9. **Licences of the path B components:** mostly Apache-2.0/MIT/ISC. arduino-esp32 is LGPL-2.1, and `esp-zboss-lib` is proprietary (DSR/Espressif). They compile into the *user's* firmware, which is the user's choice, as with the Arduino IDE. They matter only if the product bundles them (e.g. an offline installer).
10. **Smaller things.** There are two esptool versions on the machine: ESP-IDF uses 4.13, and the Arduino core uses v5 syntax (`merge-bin --pad-to-size`). The per-chip lock file must be set with `idf_build_set_property(DEPENDENCIES_LOCK …)`; a plain CMake variable is ignored.

### Tentative recommendation (decide in REPORT.md)
- **Path B as the product's build engine:** full `-Og`/`-Os` control, one engine for both Arduino sketches and ESP-IDF projects (both are v1 formats), and our own sdkconfig. It needs four things: Arduino preprocessing, board-menu → sdkconfig mapping, framework-build caching, and dependency trimming.
- **Keep path A as a reference build** to check that path B behaves the same as the Arduino IDE.

Next: step 2 (Boot). Look closely at the full boot logs, the S3 clock, and which peripherals' init paths break.

## 2026-11-16 — Step 2: Boot (interim; the final write-up follows)

Setup: `02-boot/probe/probe.ino` tests 19 Arduino API areas. `02-boot/probe-run.ps1` drives it through QEMU's UART0 over TCP (UART1 goes to a file). It retries a test once after a chip crash and records CRASH/HANG. Results are in `02-boot/results/`.

### Spurious "Cache disabled but cached memory region accessed" panics (key risk)
- The chip panics at random: during `nvs_flash_init()` at boot (Arduino's `initArduino`), or even with both cores idle in `waiti`. The panicking core prints as `/` (core −1: unknown).
- This happens **on an idle host too**. Load makes it more frequent: under a 16-core build, the step 1 sketch failed 1 of 2 boots.
- Boot-reliability A/B test: step 1 sketch, path A, ESP32 `-Og`, 15 boots × 15 s per mode, idle host:

| QEMU mode | Boots that never reached `setup()` output | Cache panics | Speed (ticks/15 s; real time ≈ 23) |
|---|---|---|---|
| default | **4/15** | 5 | ~23 |
| `-icount shift=auto` | **0/15** | 0 | ~6–7 (**~3.5× slower than real time**) |
| `-icount shift=2` | 15/15 (too slow to finish booting) | 0 | 0 |
| `-accel tcg,thread=single` | 15/15 (stuck after the ROM banner) | 0 | 0 |

- **Interpretation:** it looks like a race between QEMU's two vCPU threads around ESP-IDF's cross-core cache-disable handshake. Deterministic instruction counting removes it. Single-threaded TCG doesn't run this machine at all.
- **Product impact:** default mode is fast but fails ~1 boot in 4; `-icount` is stable but too slow for real-time I/O with real devices.
- **Open, to investigate:**
  - a unicore firmware build (`CONFIG_FREERTOS_UNICORE`, path B only; it changes fidelity);
  - other `-icount` options (`sleep=off`, `align`);
  - Espressif's QEMU issue tracker.
  - A QEMU-side fix would be QEMU-zone (GPLv2) work.

### Probe results so far: path A, ESP32 `-Og`, idle host, default mode
- **OK:** chip info, `setCpuFrequencyMhz` 80/160/240, dual-core task pinning, hardware timer (9 alarms/s; 10 expected, boundary effect), UART1 (the host received the text), `esp_random`, NVS read/write, LittleFS format + read/write.
- **Timing:** `delay(2000)` = 2000 ms, `micros` 2,000,103, 240.0 cycles/µs. In default mode the virtual clock follows host time.
- **FAIL (not emulated):**
  - GPIO input/readback: everything reads 0, including the pull-up.
  - GPIO interrupts: 0 of 10.
  - LEDC readback: duty and frequency read 0.
  - I2C: the bus isn't empty. Address **0x48 ACKs**, so QEMU has a built-in I2C device there (likely a TMP105-class sensor; to confirm).
- **RAN, values not real:**
  - SPI echoes the sent byte (0xA5).
  - `touchRead`=0 after a 2 s "measurement done timeout".
  - `temperatureRead`=−17.8 °C.
  - ADC: see below.
- **CRASH (both attempts, deterministic):**
  - **ADC** (`analogRead`): "Cache error" panic in `adc_oneshot_hal_convert` reading the ADC register.
  - **RMT**: the same panic in `rmt_tx_disable` reading the interrupt-status register.
  - **Wi-Fi** (`WiFi.mode(WIFI_STA)`): `assert failed: esp_phy_enable phy_init.c:336`.
- **Expected log noise:** the LittleFS "Corrupted dir pair / mount failed (-84)" appears on first mount of a blank partition, then the format succeeds (the same happens on real hardware).
- Remaining: probe path A ESP32-S3 and path B ESP32/S3; check whether the `cpufreq` test destabilizes the next test; summarize per peripheral.
