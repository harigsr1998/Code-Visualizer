# Probe results: build-a-esp32-og-probe (esp32)

- Image: `probe.ino.merged.bin`, run 2026-11-16 13:21; QEMU runs needed: 1; finished: True
- Extra QEMU args: ``-icount shift=auto``; skipped from the start: info, tasks, gpio, gpio_isr, adc, pwm_ledc, hw_timer, uart1, i2c, spi, rmt, touch, temp, random, nvs, littlefs, wifi
- Spurious 'Cache disabled but cached memory region accessed' panics seen: 1; chip resets seen: 2
- Host CPU load when the run ended: 4%
- Boot (run 1): host wall-clock 3.06 s to `PROBE READY chip=ESP32-D0WDQ6 millis=308 tests=19`
- Boot (run 1): host wall-clock 15.32 s to `PROBE READY chip=ESP32-D0WDQ6 millis=4274 tests=19`

| Test | Status | Detail | Wall s |
|---|---|---|---|
| cpufreq | OK | set(80)=ok,read=80 set(160)=ok,read=160 set(240)=ok,read=240 | 0 |
| timing | RAN | delay(2000): millis=2044 micros=2044522 cycles=490687330 (=240.0 cycles/us; real chip at 240 MHz: 240) [crashed once before passing on retry: Guru Meditation Error: Core  / panic'ed (Cache error). ] | 1.74 |

## ESP-IDF errors and warnings seen (deduplicated, timestamps removed)

```
(none)
```
