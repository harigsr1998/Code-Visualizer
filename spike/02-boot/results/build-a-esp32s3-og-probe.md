# Probe results: build-a-esp32s3-og-probe (esp32s3)

- Image: `probe.ino.merged.bin`, run 2026-11-16 13:17; QEMU runs needed: 2; finished: True
- Extra QEMU args: none; skipped from the start: none
- Spurious 'Cache disabled but cached memory region accessed' panics seen: 0; chip resets seen: 2
- Host CPU load when the run ended: 28%
- Boot (run 1): host wall-clock 0.48 s to `PROBE READY chip=ESP32-S3 millis=438 tests=19`
- Boot (run 2): host wall-clock 0.46 s to `PROBE READY chip=ESP32-S3 millis=418 tests=19`

| Test | Status | Detail | Wall s |
|---|---|---|---|
| info | OK | model=ESP32-S3 rev=0 cores=2 cpu=80MHz xtal=40MHz apb=80000000Hz flash=4194304B@80000000Hz heap=298420 psram=0 sdk=v5.5.5 | 0 |
| cpufreq | FAIL | set(80)=ok,read=80 set(160)=ok,read=80 set(240)=ok,read=80 | 0 |
| timing | RAN | delay(2000): millis=2000 micros=1999911 cycles=79998876 (=40.0 cycles/us; real chip at 240 MHz: 240) | 2.02 |
| tasks | OK | task pinned to core 0 ran on 0, task pinned to core 1 ran on 1 | 0.09 |
| gpio | FAIL | out-high->0 out-low->0 pullup->0 pulldown->0 (real chip: 1 0 1 0) | 0 |
| gpio_isr | FAIL | 0 interrupts for 10 self-generated edges | 0.06 |
| adc | CRASH | not run on S3: linking the ADC driver hangs boot before setup() (see adc_min) | 0 |
| pwm_ledc | FAIL | attach=1 write=1 read-duty=0 read-freq=0Hz (set 128, 5000 Hz) | 0 |
| hw_timer | OK | 9 alarms in 1 s (expect 10) | 1.03 |
| uart1 | OK | host received the UART1 text | 0 |
| i2c | FAIL | scanned 112 addresses: ack=0 nack=0 timeout=112 other=0; acked: none (empty bus on a real chip: all nack) | 2.37 |
| spi | RAN | transfer(0xA5) returned 0x00 (MISO floating) | 0.05 |
| rmt | FAIL | rmtWrite(8 symbols, 200 ms timeout)=0 | 0.19 |
| touch | RAN | touchRead=0 (nothing touching) | 2.04 |
| temp | HANG | whole chip silent for 30 s |  |
| random | OK | 0xdba92085 0x62a98897 0x70591002 | 0 |
| nvs | OK | runs-before=0 wrote=4 read-back=1 | 0 |
| littlefs | OK | read-back='hello from qemu' total=1441792 used=8192 | 0.06 |
| wifi | HANG | no result within 25000 ms | 25.05 |

## ESP-IDF errors and warnings seen (deduplicated, timestamps removed)

```
E touch: touch_sensor_trigger_oneshot_scanning(407): Wait for measurement done timeout
E esp_littlefs: ./managed_components/joltwallet__littlefs/src/littlefs/lfs.c:1383:error: Corrupted dir pair at {0x0, 0x1}
E esp_littlefs: mount failed,  (-84)
E esp_littlefs: Failed to initialize LittleFS
```
