# Spike step 1: Build

Builds one multi-file Arduino sketch two ways, for ESP32 and ESP32-S3, at `-Os` ("ship") and `-Og` ("clearest visuals"), and produces a 4 MB merged flash image that Espressif QEMU boots.

| Path | Tooling | What gets compiled |
|---|---|---|
| **A** | `arduino-cli` + Arduino-ESP32 core 3.3.12 | Sketch + Arduino core. ESP-IDF comes **precompiled** with the core's fixed settings. |
| **B** | ESP-IDF v5.5.5 + `espressif/arduino-esp32` 3.3.12 as a managed component | **Everything from source** (ESP-IDF, Arduino core, sketch), with our own `sdkconfig`. |

## Layout

```
sketch_multi/          the test sketch (.ino + counter.h/.cpp + src/mathutil/fib.*)
idf_arduino/           path B: ESP-IDF project that compiles ../sketch_multi in place
  main/sketch_main.cpp   #include <Arduino.h> + #include "sketch_multi.ino"
  sdkconfig.defaults     shared settings; .os / .og add the optimization level
  dependencies.lock.*    component versions resolved per chip (committed for reproducibility)
build.ps1              runs the builds and prints time, app size and image size
boot-smoke.ps1         boots an image in QEMU for a few seconds and saves the UART output
build-*/               build output (git-ignored)
```

## Run

```powershell
.\build.ps1                              # all 8 builds
.\build.ps1 -Path A -Chip esp32 -Opt Og  # one build
.\boot-smoke.ps1 -Image build-a-esp32-os\sketch_multi.ino.merged.bin -Chip esp32
.\boot-smoke.ps1 -Image build-b-esp32-os\flash_merged.bin -Chip esp32
```

Path B activates ESP-IDF itself via EIM's PowerShell profile (`C:\Espressif\tools\...`). Findings are in `../NOTES.md`.
