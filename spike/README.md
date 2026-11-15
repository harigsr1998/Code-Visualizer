# Feasibility spike (throwaway)

Everything in this folder is experimental and will **not** become product code. Its purpose is to prove or disprove the riskiest technical assumptions before real development starts.

**Status:** folder created; no code yet. Waiting for the go-ahead to start.

## Questions to answer

1. **Build:** compile a multi-file Arduino-on-ESP32 project for ESP32 and ESP32-S3 into a QEMU-bootable flash image. Compare `arduino-cli` against ESP-IDF with Arduino as a component.
2. **Boot:** does the Arduino runtime boot in Espressif QEMU? What breaks?
3. **Trace:** capture executed code addresses efficiently and map them to `file:line` via DWARF (in `-Og` and `-Os`).
4. **UART bridge:** connect QEMU's UART to a real COM port.
5. **Network:** route the emulated Ethernet to the host network (outbound + port-forwarded inbound); check QEMU socket/stream netdev support.
6. **Wi-Fi:** prototype an `esp_wifi` driver substitute; test whether existing emulated-Wi-Fi forks work with current versions.
7. **Measure:** CPU/RAM use and how close to real-time it runs.

## Planned layout (created as each experiment starts)

```
spike/
  01-build/       build-path experiments
  02-boot/        boot logs and findings
  03-trace/       execution tracing + DWARF mapping
  04-uart/        UART ↔ COM bridge
  05-network/     networking experiments
  06-wifi/        Wi-Fi approaches
  REPORT.md       final findings: go/no-go per question
```
