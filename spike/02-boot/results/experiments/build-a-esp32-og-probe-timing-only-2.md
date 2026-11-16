# Probe results: build-a-esp32-og-probe (esp32)

- Image: `probe.ino.merged.bin`, run 2026-11-16 13:21; QEMU runs needed: 1; finished: True
- Extra QEMU args: ``-icount shift=auto``; skipped from the start: info, cpufreq, tasks, gpio, gpio_isr, adc, pwm_ledc, hw_timer, uart1, i2c, spi, rmt, touch, temp, random, nvs, littlefs, wifi
- Spurious 'Cache disabled but cached memory region accessed' panics seen: 0; chip resets seen: 1
- Host CPU load when the run ended: 25%
- Boot (run 1): host wall-clock 3 s to `PROBE READY chip=ESP32-D0WDQ6 millis=287 tests=19`

| Test | Status | Detail | Wall s |
|---|---|---|---|
| timing | RAN | delay(2000): millis=2246 micros=2254319 cycles=541050008 (=240.0 cycles/us; real chip at 240 MHz: 240) | 2.08 |

## ESP-IDF errors and warnings seen (deduplicated, timestamps removed)

```
(none)
```
