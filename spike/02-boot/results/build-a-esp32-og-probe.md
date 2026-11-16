# Probe results: build-a-esp32-og-probe (esp32)

- Image: `probe.ino.merged.bin`, run 2026-11-16 12:31; QEMU runs needed: 6; finished: True
- Extra QEMU args: none; skipped from the start: none
- Spurious 'Cache disabled but cached memory region accessed' panics seen: 5; chip resets seen: 8
- Host CPU load when the run ended: 7%
- Boot (run 1): host wall-clock 1.54 s to `PROBE READY chip=ESP32-D0WDQ6 millis=559 tests=19`
- Boot (run 2): host wall-clock 1.62 s to `PROBE READY chip=ESP32-D0WDQ6 millis=619 tests=19`
- Boot (run 3): host wall-clock 3.91 s to `PROBE READY chip=ESP32-D0WDQ6 millis=2971 tests=19`
- Boot (run 4): host wall-clock 1.58 s to `PROBE READY chip=ESP32-D0WDQ6 millis=609 tests=19`
- Boot (run 5): host wall-clock 1.58 s to `PROBE READY chip=ESP32-D0WDQ6 millis=616 tests=19`
- Boot (run 6): host wall-clock 4.09 s to `PROBE READY chip=ESP32-D0WDQ6 millis=3106 tests=19`
- Boot (run 6): host wall-clock 13.56 s to `PROBE READY chip=ESP32-D0WDQ6 millis=1663 tests=19`
- Boot (run 6): host wall-clock 20.35 s to `PROBE READY chip=ESP32-D0WDQ6 millis=2011 tests=19`

| Test | Status | Detail | Wall s |
|---|---|---|---|
| info | OK | model=ESP32-D0WDQ6 rev=0 cores=2 cpu=240MHz xtal=40MHz apb=80000000Hz flash=4194304B@80000000Hz heap=265064 psram=0 sdk=v5.5.5 | 0 |
| cpufreq | OK | set(80)=ok,read=80 set(160)=ok,read=160 set(240)=ok,read=240 | 0 |
| timing | RAN | delay(2000): millis=2000 micros=2000103 cycles=480026400 (=240.0 cycles/us; real chip at 240 MHz: 240) [crashed once before passing on retry: Guru Meditation Error: Core  / panic'ed (Cache error). ] | 2.01 |
| tasks | OK | task pinned to core 0 ran on 0, task pinned to core 1 ran on 1 | 0.09 |
| gpio | FAIL | out-high->0 out-low->0 pullup->0 pulldown->0 (real chip: 1 0 1 0) | 0 |
| gpio_isr | FAIL | 0 interrupts for 10 self-generated edges | 0.06 |
| adc | CRASH | twice: Guru Meditation Error: Core  / panic'ed (Cache error).  / then: Guru Meditation Error: Core  / panic'ed (Cache error).  | 0.29 |
| pwm_ledc | FAIL | attach=1 write=1 read-duty=0 read-freq=0Hz (set 128, 5000 Hz) | 0 |
| hw_timer | OK | 9 alarms in 1 s (expect 10) | 1.02 |
| uart1 | OK | host received the UART1 text | 0 |
| i2c | FAIL | scanned 112 addresses: ack=1 nack=111 timeout=0 other=0; acked: 0x48 (empty bus on a real chip: all nack) | 0 |
| spi | RAN | transfer(0xA5) returned 0xA5 (MISO floating) | 0 |
| rmt | CRASH | twice: Guru Meditation Error: Core  / panic'ed (Cache error).  / then: Guru Meditation Error: Core  / panic'ed (Cache error).  | 0.51 |
| touch | RAN | touchRead=0 (nothing touching) | 1.98 |
| temp | RAN | temperatureRead=-17.8 C (real chip: roughly 30-60) | 0.03 |
| random | OK | 0x1081ca80 0x95ba174a 0xb8c7ac2d | 0 |
| nvs | OK | runs-before=0 wrote=4 read-back=1 | 0.06 |
| littlefs | OK | read-back='hello from qemu' total=1441792 used=8192 | 0.73 |
| wifi | CRASH | twice: assert failed: esp_phy_enable phy_init.c:336 (phy_module_has_clock_bits(PHY_INIT_MODEM_CLOCK_REQUIRED_BITS)) / then: assert failed: esp_phy_enable phy_init.c:336 (phy_module_has_clock_bits(PHY_INIT_MODEM_CLOCK_REQUIRED_BITS)) | 0.1 |

## ESP-IDF errors and warnings seen (deduplicated, timestamps removed)

```
E touch: touch_sensor_trigger_oneshot_scanning(407): Wait for measurement done timeout
E esp_littlefs: ./managed_components/joltwallet__littlefs/src/littlefs/lfs.c:1383:error: Corrupted dir pair at {0x0, 0x1}
E esp_littlefs: mount failed,  (-84)
E esp_littlefs: Failed to initialize LittleFS
```
