# Spike step 5: Network (worker "network")

Running log, newest at the bottom. The **REPORT** section at the end answers the brief's questions 1–8.

Legend for sources (RULES.md rule 12): **[own test]** = measured here; **[official]** = Espressif/QEMU docs or source;
**[community]** = forum/issue/blog, unverified unless an own test is cited next to it.

## Folder contents

| Path | What | Licence zone |
|---|---|---|
| `idf_http_request/`, `idf_tcp_server/` | Unmodified copies of ESP-IDF v5.5.5 `examples/protocols/http_request` and `examples/protocols/sockets/tcp_server` | Example C code: "Public Domain (or CC0 licensed, at your option)" (file headers). Their `pytest_*.py` files: `SPDX-License-Identifier: Apache-2.0`. Both fine for every zone. |
| `sdkconfig.qemu_eth` | open_eth settings for the IDF examples (same as Espressif's own QEMU CI configs) | config |
| `net_probe/` | Our firmware: outbound HTTP (internet + host), TCP echo/sink/source servers | **firmware zone**: permissive (MIT/Apache-2.0), to be decided |
| `an/` ("arduino net"; short name because of the path limit, see below) | Path B Arduino sketch + `qemu_eth.{h,cpp}` (an Arduino `NetworkInterface` for open_eth) | **firmware zone**: permissive, to be decided |
| `es/` ("ETH stock") | Negative control: unmodified `ETH.begin()` | firmware zone |
| `sdkconfig.arduino_openeth` | `CONFIG_ETH_USE_OPENETH=y` for path B | config |
| `build-idf.ps1`, `run-net.ps1` | Build plain IDF projects; boot an image in QEMU with networking | **app zone** |
| `frame_tap.py` | Frame capture / man-in-the-middle relay (Python stdlib only) | **app zone** (a separate process talking to QEMU over sockets, *not* a QEMU plugin) |
| `host_bench.py` | Host-side latency/throughput bench | **app zone** |
| `host_www/` | File served by the host test server (`python -m http.server`) | — |

Build outputs (`build-*/`) are git-ignored.

## 2026-11-16 — Setup and first findings

### QEMU's network support (question 6) [own test]
`qemu-system-xtensa.exe -netdev help` (Espressif QEMU 9.2.2, `esp_develop_9.2.2_20260417`, Windows build):
`socket`, `stream`, `dgram`, `hubport`, `tap`, `user`.
- NIC model: `-machine esp32 -nic model=help` → **`open_eth`** only; the same for `-machine esp32s3`.
- `-object help` lists the net filters **`filter-mirror`, `filter-redirector`, `filter-dump`, `filter-buffer`, `filter-replay`**.
- `tap` needs a TAP-Windows driver, which isn't installed (not tested; no installs allowed).
- The QEMU binary is one static 72 MB exe; it imports only Windows system DLLs.
- **libslirp v4.9.1 is statically linked**: the binary contains source paths `../libslirp-v4.9.1/src/*.c`.
  - Licence: BSD-3-Clause, with a few files under MIT/Expat [official: Debian copyright file for libslirp 4.9.1, https://sources.debian.org/src/libslirp/4.9.1-1/debian/copyright ; upstream https://gitlab.freedesktop.org/slirp/libslirp]. Permissive. It's part of the QEMU binary, which is GPLv2 anyway; it only matters if we redistribute QEMU (we already owe GPLv2 source for that).

### Question 1: plain ESP-IDF example over open_eth, ESP32 [own test]
- `idf_http_request` built with `sdkconfig.qemu_eth` in **115 s** (clean, ccache warm). The settings are the same as Espressif's own QEMU CI configs (e.g. `examples/protocols/https_x509_bundle/sdkconfig.ci.default_crt_bundle`: `CONFIG_EXAMPLE_CONNECT_ETHERNET=y`, `CONFIG_EXAMPLE_USE_OPENETH=y`, `CONFIG_EXAMPLE_CONNECT_WIFI=n`, `CONFIG_ETH_USE_SPI_ETHERNET=n`) [official].
- Espressif documents `-nic user,model=open_eth` (TCP/UDP only, guest behind NAT, `hostfwd` for inbound) [official: https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/esp32/README.md].
- Boot with `-nic user,model=open_eth -icount shift=auto`, 45 s:
  ```
  I (5269) ethernet_connect: Ethernet Link Up
  I (6279) esp_netif_handlers: example_netif_eth ip: 10.0.2.15, mask: 255.255.255.0, gw: 10.0.2.2
  I (7169) ethernet_connect: Got IPv6 event: ... fe80::5054:ff:fe12:3456 (link-local)
  I (7309) example: DNS lookup succeeded. IP=104.20.23.154
  HTTP/1.1 200 OK
  I (8169) ... fec0::5054:ff:fe12:3456, type: ESP_IP6_ADDR_IS_SITE_LOCAL   (slirp's IPv6 SLAAC)
  ```
  The GET repeats 4 times in 45 s, all `200 OK`. DHCP, DNS, HTTP and IPv6 (link-local + SLAAC) all work.
- Harmless log noise: `esp_eth_ioctl(533): add mac address to filter not supported` / `Failed to add multicast filter for IPv6`. The openeth driver has no multicast filter. Multicast still reaches the chip in slirp, but **slirp doesn't forward multicast to the host network** [community: espp QEMU component readme, https://components.espressif.com/components/espp/qemu/versions/1.4.2/readme, says mDNS needs tap; not verified here]. So mDNS discovery from the LAN won't work through `user`.
- Under `-icount shift=auto` the virtual clock reached ~42 s in 45 s of wall time here, much closer to real time than the 3.5× slowdown step 2 measured. The chip is mostly idle (waiting on the network), and idle time isn't slowed.

### Question 6: frames to a process we write [own test]
`frame_tap.py` (app zone, stdlib only) speaks the framing that all QEMU socket netdevs and net-filter chardevs use: a 4-byte big-endian length, then the raw Ethernet frame.

1. **`filter-mirror` (watch only; slirp still does the NAT)**: `-nic user,model=open_eth,id=n0 -chardev socket,id=m0,host=127.0.0.1,port=5571 -object filter-mirror,id=fm0,netdev=n0,queue=all,outdev=m0`.
   - Result: **54 frames in 30 s**: DHCP discover/offer/request/ack, ARP, IPv6 ND/MLD, the DNS query/answer, the TCP handshake, the HTTP request/response, FIN. That's every frame of the session, in both directions (log: `mirror-test.log`).
   - This is enough for the sequence-diagram view, with zero firmware changes.
2. **`filter-redirector` × 4 (full man-in-the-middle in front of slirp)**: the chip→net and net→chip directions each get a divert filter (`outdev`) and an inject filter (`indev`). Our process decides per frame whether to forward it.
   - **Pass-through:** `HTTP/1.1 200 OK` (33 chip→net frames, 21 net→chip in 32 s).
   - **App drops TCP port 80:** the chip's SYNs never reach slirp, and the example logs `socket connect failed errno=113` (EHOSTUNREACH). 16 frames dropped.
   - **Gotcha (found the hard way):** QEMU walks the filter list in **reverse order for the rx direction** (packets sent *to* the netdev, i.e. chip→net). The first attempt listed rx-divert before rx-inject. Every injected frame went straight back into the divert filter: a loop of **156,064 frames in 32 s**, and slirp never saw a packet. The fix is to declare the **rx inject filter before the rx divert filter**; tx is the normal order. The working command line is in the REPORT.
   - [community] QEMU's commit messages describe the redirector semantics ("redirect filter's net packet to outdev, and redirect indev's packet to filter"), but I found no published example combining `user` with `filter-redirector`. The ordering rule above comes from our own test.
3. **`-netdev stream` (we are the whole network)**: `-netdev stream,id=s0,server=off,addr.type=inet,addr.host=127.0.0.1,addr.port=5576 -net nic,model=open_eth,netdev=s0`.
   - QEMU connected to our process, and we received the chip's DHCP discovers and IPv6 frames (14 frames in 22 s). Nothing answered, so the chip waited for an IP, as expected.
   - With this netdev, the app would need its own DHCP/ARP/NAT/TCP stack (or a userspace stack such as libslirp linked into a *separate* helper process).
4. **`filter-dump`** writes a standard pcap: `-object filter-dump,id=d0,netdev=n0,file=net.pcap` gave 34 packets in 15 s, magic `d4c3b2a1`, link type 1 (Ethernet). It opens in Wireshark with no code (an export feature for free).

### Question 5, path A [own test]
- `esp32-libs/3.3.12/sdkconfig` and `esp32s3-libs/3.3.12/sdkconfig` both have `# CONFIG_ETH_USE_OPENETH is not set`. ESP32 also has EMAC + W5500; S3 has W5500 only.
- The precompiled `libesp_eth.a` (ESP32) has **no openeth symbols at all** (`nm | grep -ci openeth` → 0). Its only MAC constructors are `esp_eth_mac_new_esp32`, `_w5500`, `_dm9051`, `_ksz8851snl`.
- **Confirmed: path A can't use open_eth** without rebuilding the Arduino libs. So in the product, networking under QEMU needs path B.

### Question 5, path B: the Arduino `ETH` class [official: source of arduino-esp32 3.3.12]
- `ETHClass::begin()` (`libraries/Ethernet/src/ETH.cpp` ~233–430) only creates `esp_eth_mac_new_esp32` (internal EMAC) or SPI MACs (W5500/DM9051/KSZ8851). There's no openeth branch, so `ETH.begin()` **can't** drive QEMU's NIC unchanged.
- But `NetworkInterface` (`libraries/Network/src/NetworkInterface.h`) exposes `initNetif()`, `setStatusBits()` and `Network.postEvent()` to subclasses. `an/qemu_eth.cpp` copies ETHClass's sequence with `esp_eth_mac_new_openeth` + `esp_eth_phy_new_dp83848` (as in ESP-IDF's `protocol_examples_common/eth_connect.c`) and registers as `ESP_NETIF_ID_ETH0`.
- Test pending (build queued).

### Windows path limit broke the first path B builds [own test + community]
- **All 3 path B builds failed** (after 214–273 s) with `fatal error: opening dependency file …\1f28b54047efcf5aae11dbab225dc5cb\MatterTemperatureControlledCabinet.cpp.obj.d: No such file or directory`.
- That path is **261 characters**, over Windows' 260 MAX_PATH, and `LongPathsEnabled=0` on this machine. The coordinator's step 1 builds worked because `Development\spike\build-b-esp32-os\…` gives 228. CMake already hashes long object dirs; the worst case is still `MatterTemperatureControlledCabinet.cpp`, which arduino-esp32 compiles whatever the sketch uses.
- **Workaround here:** renamed the sketch folders to `an/` and `es/` and dropped `-BuildTag`, which cuts 13 characters (worst path ≈ 248–250).
- [community] The same `.obj.d` symptom is traced to MAX_PATH at https://flowcode.co.uk/forums/viewtopic.php?p=14990 . An esp32.com thread reports **ccache failing on long paths even with Windows long paths enabled**: https://esp32.com/viewtopic.php?p=56693 (unverified here). Also see https://esp32.com/viewtopic.php?p=124271 (CMAKE_OBJECT_PATH_MAX).
- **Product implication:** user projects can live in deep folders, and the user name is part of the path. The app must build in its own **short staging directory** (e.g. `C:\cvb\<hash>` or under a short `%LOCALAPPDATA%` root) and not depend on `LongPathsEnabled`, which needs admin and reportedly doesn't fix ccache anyway. This adds to the spaces-in-path finding from step 0.
- **Request to the coordinator:** add an `-OutDir` option to `01-build/build.ps1`, so the build folder doesn't have to sit next to the sketch.

### Path B link failure: Arduino's TLS layer needs mbedTLS PSK modes [own test + official source + community]
- With short names, `es` built (289 s), but `an` (both chips, 277/325 s) failed to link: `NetworkClientSecure.cpp.obj: undefined reference to ssl_init(sslclient_context*)`, `start_ssl_client(...)`, `send_ssl_data(...)` …
- Cause, from the source: `libraries/NetworkClientSecure/src/ssl_client.cpp` line 27 compiles to **nothing** (it only emits a `#warning`) unless `MBEDTLS_KEY_EXCHANGE_SOME_PSK_ENABLED` is set. Arduino's precompiled config has `CONFIG_MBEDTLS_PSK_MODES=y` + `KEY_EXCHANGE_PSK/ECDHE_PSK/RSA_PSK=y`; ESP-IDF's defaults have `# CONFIG_MBEDTLS_PSK_MODES is not set`.
  - `HTTPClient` references `NetworkClientSecure` even for plain `http://`, so **any sketch that includes HTTPClient fails to link on path B** unless these are set. Step 1's sketch never used it, which is why this didn't show up earlier.
- [community] The same undefined references are reported in https://github.com/espressif/arduino-esp32/issues/11170 .
- Fix: the 4 PSK lines in `sdkconfig.arduino_openeth`. **They belong in the shared path B defaults** (`01-build/idf_arduino/sdkconfig.defaults`), which is a request to the coordinator.
- **Product implication (strengthens step 1 finding 3):** path B needs Arduino's full sdkconfig as its baseline (diff `esp32-libs/<ver>/sdkconfig` against IDF defaults), not a hand-picked list. Otherwise ordinary sketches break in ways the Arduino IDE never shows.

### Stock `ETH.begin()` control, first attempt [own test]
- `ETH.begin()` with no arguments returned **false after 0 ms** (2/2 runs); `linkUp=0 hasIP=0` forever, no crash.
- That's because `ETH.h`'s argument-less `begin()` only works when the board variant defines `ETH_PHY_TYPE/ADDR/MDC/MDIO/POWER/CLK_MODE`, and path B's generic board doesn't. So it never touched the EMAC.
- Changed it to the explicit LAN8720 call (`ETH.begin(ETH_PHY_LAN8720, 1, 23, 18, -1, ETH_CLOCK_GPIO0_IN)`) and rebuilding.

### Stock `ETH.begin()` with LAN8720 pins: deterministic crash [own test]
- Incremental path B rebuild: 31.8 s. **2 of 2 runs:** `Guru Meditation Error: Core 1 panic'ed (LoadStorePIFAddrError)`, `EXCVADDR: 0x3ff6980c`, then a reboot loop (3 panics per 25 s run).
- addr2line:
  ```
  emac_ll_clock_enable_rmii_input   hal/esp32/include/hal/emac_ll.h:671
   (inlined) emac_esp_config_data_interface  esp_eth/src/mac/esp_eth_mac_esp.c:767
   (inlined) esp_eth_mac_new_esp32           esp_eth_mac_esp.c:848
  ETHClass::begin(...)                       Ethernet/src/ETH.cpp:316
  setup()                                    05-network/es/es.ino:14
  ```
- QEMU's ESP32 machine doesn't map the internal EMAC's registers (0x3ff69000 region), so the first register access faults.
- **Product implication:** an unmodified wired-Ethernet sketch (internal EMAC; SPI W5500 etc. are similar, since no SPI Ethernet chip is modelled either) can't run as-is. Like Wi-Fi (step 6), `ETH.begin(...)` needs a **firmware-zone substitute** that maps onto open_eth, e.g. `qemu_eth.cpp`, linked in place of the EMAC/SPI MAC constructors. The fidelity indicator must show "Ethernet: substituted (QEMU open_eth)".

### Question 1, ESP32-S3 [own test]
- `idf_http_request` for S3: 123 s build. **2 of 2 boots** (`-icount shift=auto`, 40 s each): link up, DHCP 10.0.2.15, DNS, `HTTP/1.1 200 OK`, repeated. S3 works the same as ESP32.

### Questions 2 + 3: net_probe and tcp_server via hostfwd (functional, `-icount`, not inside the lock) [own test]
`probe-session.ps1` starts `python -m http.server 18071 --bind 127.0.0.1`, boots `net_probe` with `hostfwd=tcp:127.0.0.1:18070-:3333,…18072-:3334,…18073-:3335`, then runs `host_bench.py`.

| | ESP32 | ESP32-S3 |
|---|---|---|
| DHCP → IP | 10.0.2.15 / gw 10.0.2.2 / DNS 10.0.2.3, 2.1 s after `example_connect()` | same, 2.1 s |
| GET http://example.com/ (internet) | `HTTP/1.1 200 OK`, 889 B, DNS 88 ms | `HTTP/1.1 200 OK`, 892 B, DNS 63 ms |
| GET http://10.0.2.2:18071/hello.txt (server on the host's 127.0.0.1) | `HTTP/1.0 200 OK` | `HTTP/1.0 200 OK` |
| Inbound echo / sink / source via hostfwd | all accepted | all accepted |
| Unmodified `idf_tcp_server` via `hostfwd=tcp:127.0.0.1:18075-:3333` | echoed "hello from host" | echoed "hello from host" |

- **Fidelity gap:** every inbound connection reaches the chip **from 10.0.2.2** (slirp's NAT), not from the real client's address. Firmware that logs, rate-limits or allow-lists by client IP will see the wrong address. The MITM path (question 6) could fix this later by doing the NAT ourselves.
- The host is `10.0.2.2` inside the chip, so a host service bound to 127.0.0.1 is reachable from the firmware. That's good for local test brokers/servers (e.g. a local MQTT broker), but the firmware has to use `10.0.2.2` instead of `localhost` or the host's LAN IP. The product could offer a "host alias" setting.

### User decision (2026-11-16): inbound = local network only
- The user will accept the Windows Firewall prompt for QEMU, and says future users can too.
- **Inbound access from the internet is out of scope for now. v1 targets the LAN only.**
- Consequences I pointed out to the user:
  - Tick only "Private".
  - The rule is per-exe (all ports of `qemu-system-xtensa.exe`) and per path, so a new QEMU version prompts again.
  - "Cancel" silently creates a block rule.
  - The app should bind `0.0.0.0` only for ports the user explicitly exposes.

### HTTPS / TLS on both chips (checking the community S3 report) [own test]
- `idf_https_request` (ESP-IDF example, `SPDX-License-Identifier: Apache-2.0`, Mbed TLS contributors). First build: 115 s / 118 s.
- **Build-script bug, since fixed:** passing `-DSDKCONFIG_DEFAULTS=…` *replaces* a project's own `sdkconfig.defaults`, so the example's `CONFIG_MBEDTLS_HAVE_TIME_DATE=y`, `CONFIG_LWIP_SNTP_MAX_SERVERS=2` and `SINGLE_APP_LARGE` were missing. `build-idf.ps1` now lists the project's own file first. The same trap applies to the product's build engine: user projects' own `sdkconfig.defaults` must be merged, not replaced.
- With that first build (`-icount`, 60 s, 2 runs per chip):
  - **ESP32: 2/2, S3: 2/2.** TLS connections to `www.howsmyssl.com` gave `HTTP/1.1 200 OK`, 2–3 times per run, on both chips.
  - Every run first hit `esp_netif_sntp_init: Failed initialize SNTP service` → `ESP_ERROR_CHECK failed` → reboot. That's the missing `SNTP_MAX_SERVERS=2` above, a config error, not QEMU.
  - Min free heap: ESP32 ~222 KB, S3 ~302 KB.
- **The community report of S3-only TLS failure under QEMU (esp-idf #15446, https://esp32.com/viewtopic.php?p=144562) did not reproduce** with ESP-IDF v5.5.5 + QEMU 9.2.2 (HTTPS GET; that report was MQTTS). A rebuild with the example's full config (certificate time check via SNTP over UDP) is queued.

### Question 7: performance, inside the heavy-job lock [own test]
`probe-session.ps1 -BenchArgs '-n 200 --upload-bytes 4194304'`, 2 runs per chip × mode, host ↔ chip via `hostfwd` on 127.0.0.1. Full log: `results/perf.txt`.
**8/8 runs completed, no panics** (including 4 default-mode boots).

| Chip / mode | Echo RTT, 32 B, n=200: median (p95, max) ms | Host→chip (sink) MB/s | Chip→host (source) MB/s | Boot→IP | GET example.com total |
|---|---|---|---|---|---|
| ESP32 default | 0.45 / 0.48 (0.97 / 0.66; max 2.1 / 8.3) | 4.87 / 4.38 | 3.27 / 3.08 | 2.0 s | 101 / 62 ms |
| ESP32 icount | 0.59 / 0.59 (1.08 / 1.07) | 4.21 / 4.32 | 2.34 / 2.37 | 2.1 s | 142 / 191 ms |
| S3 default | 0.53 / 0.54 (0.77 / 0.92) | 4.62 / 4.51 | 2.86 / 2.82 | 2.0 s | 59 / 71 ms |
| S3 icount | 0.54 / 0.52 (1.25 / 1.43) | 4.84 / 4.87 | 2.83 / 2.75 | 2.1 s | 281 / 335 ms |

- **Latency:** about 0.5 ms round trip host↔chip, with p95 ≤ 1.5 ms. That's far below anything a protocol timeout cares about.
- **Throughput:** ≈ **35–39 Mbit/s into the chip, 19–26 Mbit/s out**. It matches the community anecdote of ~34 Mbit/s for `-nic user` on a Windows host (https://lists.nongnu.org/archive/html/qemu-discuss/2022-04/msg00012.html). That's the same order as a real ESP32's Wi-Fi TCP throughput (typically ~20–30 Mbit/s on hardware, from Espressif's iperf figures; not measured here). So it's **realistic, not a bottleneck**, for an ESP32 product.
- **Chip→host is slower.** A likely cause (not verified) is ESP-IDF's openeth driver defaults: `CONFIG_ETH_OPENETH_DMA_TX_BUFFER_NUM=1`, RX=4. Window scaling (missing in slirp, per the community) doesn't matter at these rates on loopback.
- `-icount shift=auto` costs little here (the chip is mostly idle waiting on I/O): RTT +0.1 ms, chip→host −25 % on ESP32, nothing measurable on S3.
- "ms" in the chip's own `PROBE` lines is virtual time. Host-side numbers (`host_bench.py`) are wall-clock.

### Community cross-checks (rule 12)
- [community] An esp32.com thread reports **TLS (MQTTS) failing under QEMU on ESP32-S3 only**, working on ESP32/ESP32-C3; filed as esp-idf issue #15446. Status unknown. https://esp32.com/viewtopic.php?p=144562 . **Relevant to the product: many sketches use HTTPS/MQTTS.** To test on S3 here (to do).
- [community] slirp has **no TCP window scaling**, so at most 64 KiB is in flight; it's commonly described as the slowest QEMU networking mode (passt presentation, https://passt.top/everything_open_2023.pdf). One anecdotal report gives ~34 Mbit/s for a Windows host with `-nic user`: https://lists.nongnu.org/archive/html/qemu-discuss/2022-04/msg00012.html . To compare with our own measurement (question 7).
- [community] No source found on whether a Windows QEMU `hostfwd` bound to `0.0.0.0` triggers a Windows Defender Firewall prompt. The QEMU docs say user mode blocks all inbound except `hostfwd` [official: https://qemu.readthedocs.io/en/master/system/devices/net.html].
