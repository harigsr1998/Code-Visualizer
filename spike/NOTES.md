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

**Product implication:** users will have spaces in their paths too (e.g. `C:\Users\John Smith\...`). The app must handle this itself, e.g. by staging builds in a space-free work directory.

Disk space: C: 138 GB free, D: 185 GB free. The toolchains need roughly 4–6 GB in total.
