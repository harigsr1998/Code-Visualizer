// Spike step 2 (Boot): peripheral probe.
//
// Runs one small test per Arduino API area and reports whether each works
// under Espressif QEMU. Protocol on UART0 (driven by ../probe-run.ps1):
//   chip -> "PROBE READY ..."        host -> "SKIP a,b\n"  (or "SKIP -")
//   chip -> "BEGIN <name>"  then  "RESULT <name> <STATUS> <detail>"
//   chip -> "PROBE DONE"
// STATUS: OK   = behaves like real hardware would
//         FAIL = returned an error or a value real hardware wouldn't give
//         RAN  = API returned, but the value can't be judged without real wiring
//         HANG = no result within the test's timeout
// Each test runs in its own task with a timeout, so a stuck driver shows up as
// HANG instead of stopping the probe. A crash reboots the chip; the host then
// sends a SKIP list and the probe carries on with the remaining tests.
//
// Everything is declared before use, so this is also valid plain C++ (path B).

#include <stdarg.h>

#include <LittleFS.h>
#include <Preferences.h>
#include <SPI.h>
#include <WiFi.h>
#include <Wire.h>

#include "driver/gpio.h"
#include "esp_random.h"

#if CONFIG_IDF_TARGET_ESP32
static const int PIN_GPIO = 18, PIN_ISR = 19, PIN_ADC = 36, PIN_PWM = 25, PIN_RMT = 5, PIN_TOUCH = 4;
static const int PIN_U1_RX = 16, PIN_U1_TX = 17;
#elif CONFIG_IDF_TARGET_ESP32S3
static const int PIN_GPIO = 18, PIN_ISR = 17, PIN_ADC = 1, PIN_PWM = 21, PIN_RMT = 48, PIN_TOUCH = 2;
static const int PIN_U1_RX = 15, PIN_U1_TX = 16;
#else
#error "The probe supports ESP32 and ESP32-S3 only"
#endif

static String fmt(const char* f, ...) {
  char buf[320];
  va_list ap;
  va_start(ap, f);
  vsnprintf(buf, sizeof buf, f, ap);
  va_end(ap);
  return String(buf);
}

// ---------------------------------------------------------------- tests ----

static String t_info() {
  return fmt("OK model=%s rev=%d cores=%d cpu=%luMHz xtal=%luMHz apb=%luHz flash=%luB@%luHz heap=%lu psram=%d sdk=%s",
             ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), (unsigned long)getCpuFrequencyMhz(),
             (unsigned long)getXtalFrequencyMhz(), (unsigned long)getApbFrequency(),
             (unsigned long)ESP.getFlashChipSize(), (unsigned long)ESP.getFlashChipSpeed(),
             (unsigned long)ESP.getFreeHeap(), (int)psramFound(), ESP.getSdkVersion());
}

static String t_cpufreq() {
  String s;
  bool allOk = true;
  const uint32_t want[] = {80, 160, 240};
  for (uint32_t f : want) {
    bool ok = setCpuFrequencyMhz(f);
    uint32_t got = getCpuFrequencyMhz();
    allOk = allOk && ok && got == f;
    s += fmt(" set(%lu)=%s,read=%lu", (unsigned long)f, ok ? "ok" : "fail", (unsigned long)got);
  }
  return String(allOk ? "OK" : "FAIL") + s;
}

static String t_timing() {
  uint32_t m0 = millis();
  unsigned long u0 = micros();
  uint32_t c0 = ESP.getCycleCount();
  delay(2000);
  uint32_t m1 = millis();
  unsigned long u1 = micros();
  uint32_t c1 = ESP.getCycleCount();
  double mhz = (double)(c1 - c0) / (double)(u1 - u0);
  return fmt("RAN delay(2000): millis=%lu micros=%lu cycles=%lu (=%.1f cycles/us; real chip at 240 MHz: 240)",
             (unsigned long)(m1 - m0), (unsigned long)(u1 - u0), (unsigned long)(c1 - c0), mhz);
}

static volatile int g_coreSeen[2];
static void coreTask(void* p) {
  int idx = (int)(intptr_t)p;
  g_coreSeen[idx] = xPortGetCoreID() + 1;
  vTaskDelete(nullptr);
}
static String t_tasks() {
  g_coreSeen[0] = g_coreSeen[1] = 0;
  xTaskCreatePinnedToCore(coreTask, "core0", 2048, (void*)0, 1, nullptr, 0);
  xTaskCreatePinnedToCore(coreTask, "core1", 2048, (void*)1, 1, nullptr, 1);
  delay(100);
  bool ok = g_coreSeen[0] == 1 && g_coreSeen[1] == 2;
  return fmt("%s task pinned to core 0 ran on %d, task pinned to core 1 ran on %d", ok ? "OK" : "FAIL",
             g_coreSeen[0] - 1, g_coreSeen[1] - 1);
}

static String t_gpio() {
  pinMode(PIN_GPIO, OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_GPIO, GPIO_MODE_INPUT_OUTPUT);  // so the output can be read back
  digitalWrite(PIN_GPIO, HIGH);
  int hi = digitalRead(PIN_GPIO);
  digitalWrite(PIN_GPIO, LOW);
  int lo = digitalRead(PIN_GPIO);
  pinMode(PIN_GPIO, INPUT_PULLUP);
  delay(2);
  int pu = digitalRead(PIN_GPIO);
  pinMode(PIN_GPIO, INPUT_PULLDOWN);
  delay(2);
  int pd = digitalRead(PIN_GPIO);
  bool ok = hi == 1 && lo == 0 && pu == 1 && pd == 0;
  return fmt("%s out-high->%d out-low->%d pullup->%d pulldown->%d (real chip: 1 0 1 0)", ok ? "OK" : "FAIL", hi, lo,
             pu, pd);
}

static volatile uint32_t g_isrCount;
static void IRAM_ATTR gpioIsr(void*) { g_isrCount = g_isrCount + 1; }
static String t_gpio_isr() {
  gpio_config_t c = {};
  c.pin_bit_mask = 1ULL << PIN_ISR;
  c.mode = GPIO_MODE_INPUT_OUTPUT;
  c.intr_type = GPIO_INTR_ANYEDGE;
  gpio_config(&c);
  gpio_set_level((gpio_num_t)PIN_ISR, 0);
  esp_err_t e = gpio_install_isr_service(0);
  if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) return fmt("FAIL gpio_install_isr_service: %s", esp_err_to_name(e));
  g_isrCount = 0;
  gpio_isr_handler_add((gpio_num_t)PIN_ISR, gpioIsr, nullptr);
  for (int i = 0; i < 10; ++i) {  // 10 edges: 1,0,1,0,...
    gpio_set_level((gpio_num_t)PIN_ISR, (i & 1) ? 0 : 1);
    delay(5);
  }
  gpio_isr_handler_remove((gpio_num_t)PIN_ISR);
  uint32_t n = g_isrCount;
  return fmt("%s %lu interrupts for 10 self-generated edges", n == 10 ? "OK" : "FAIL", (unsigned long)n);
}

static String t_adc() {
  int raw = analogRead(PIN_ADC);
  uint32_t mv = analogReadMilliVolts(PIN_ADC);
  return fmt("RAN analogRead=%d analogReadMilliVolts=%lu (pin floating)", raw, (unsigned long)mv);
}

static String t_pwm_ledc() {
  bool att = ledcAttach(PIN_PWM, 5000, 8);
  bool wr = ledcWrite(PIN_PWM, 128);
  uint32_t duty = ledcRead(PIN_PWM);
  uint32_t freq = ledcReadFreq(PIN_PWM);
  ledcDetach(PIN_PWM);
  bool ok = att && wr && duty == 128 && freq >= 4950 && freq <= 5050;
  return fmt("%s attach=%d write=%d read-duty=%lu read-freq=%luHz (set 128, 5000 Hz)", ok ? "OK" : "FAIL", att, wr,
             (unsigned long)duty, (unsigned long)freq);
}

static volatile uint32_t g_timerHits;
static void ARDUINO_ISR_ATTR onTimer() { g_timerHits = g_timerHits + 1; }
static String t_hw_timer() {
  g_timerHits = 0;
  hw_timer_t* t = timerBegin(1000000);
  if (!t) return "FAIL timerBegin returned null";
  timerAttachInterrupt(t, &onTimer);
  timerAlarm(t, 100000, true, 0);  // every 100 ms
  delay(1000);
  timerEnd(t);
  uint32_t n = g_timerHits;
  return fmt("%s %lu alarms in 1 s (expect 10)", (n >= 9 && n <= 11) ? "OK" : "FAIL", (unsigned long)n);
}

static String t_uart1() {
  Serial1.begin(115200, SERIAL_8N1, PIN_U1_RX, PIN_U1_TX);
  size_t n = Serial1.print("probe uart1 says hello\r\n");
  Serial1.flush();
  Serial1.end();
  return fmt("RAN wrote %u bytes, flush returned (host checks the UART1 capture)", (unsigned)n);
}

static String t_i2c() {
  if (!Wire.begin()) return "FAIL Wire.begin";
  Wire.setTimeOut(20);
  int acks = 0, nack = 0, other = 0, timeout = 0;
  String ackList;
  for (uint8_t a = 0x08; a < 0x78; ++a) {
    Wire.beginTransmission(a);
    uint8_t r = Wire.endTransmission();
    if (r == 0) {
      acks++;
      ackList += fmt(" 0x%02X", a);
    } else if (r == 2) nack++;
    else if (r == 5) timeout++;
    else other++;
  }
  Wire.end();
  bool emptyBus = acks == 0 && nack == 112;
  return fmt("%s scanned 112 addresses: ack=%d nack=%d timeout=%d other=%d; acked:%s (empty bus on a real chip: all nack)",
             emptyBus ? "OK" : "FAIL", acks, nack, timeout, other, acks ? ackList.c_str() : " none");
}

static String t_spi() {
  SPI.begin();
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  uint8_t r = SPI.transfer(0xA5);
  SPI.endTransaction();
  SPI.end();
  return fmt("RAN transfer(0xA5) returned 0x%02X (MISO floating)", r);
}

static String t_rmt() {
  if (!rmtInit(PIN_RMT, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 10000000)) return "FAIL rmtInit";
  rmt_data_t sym[8];
  for (rmt_data_t& s : sym) {
    s.level0 = 1;
    s.duration0 = 4;
    s.level1 = 0;
    s.duration1 = 8;
  }
  bool w = rmtWrite(PIN_RMT, sym, 8, 200);
  rmtDeinit(PIN_RMT);
  return fmt("%s rmtWrite(8 symbols, 200 ms timeout)=%d", w ? "OK" : "FAIL", w);
}

static String t_touch() {
  unsigned long v = (unsigned long)touchRead(PIN_TOUCH);
  return fmt("RAN touchRead=%lu (nothing touching)", v);
}

static String t_temp() {
  float c = temperatureRead();
  return fmt("RAN temperatureRead=%.1f C (real chip: roughly 30-60)", c);
}

static String t_random() {
  uint32_t a = esp_random(), b = esp_random(), c = esp_random();
  bool ok = !(a == b && b == c);
  return fmt("%s 0x%08lx 0x%08lx 0x%08lx", ok ? "OK" : "FAIL", (unsigned long)a, (unsigned long)b, (unsigned long)c);
}

static String t_nvs() {
  Preferences p;
  if (!p.begin("probe", false)) return "FAIL Preferences.begin";
  uint32_t before = p.getUInt("runs", 0);
  size_t w = p.putUInt("runs", before + 1);
  uint32_t back = p.getUInt("runs", 0);
  p.end();
  bool ok = w == 4 && back == before + 1;
  return fmt("%s runs-before=%lu wrote=%u read-back=%lu", ok ? "OK" : "FAIL", (unsigned long)before, (unsigned)w,
             (unsigned long)back);
}

static String t_littlefs() {
  if (!LittleFS.begin(true)) return "FAIL LittleFS.begin(formatOnFail=true)";
  File f = LittleFS.open("/probe.txt", "w");
  if (!f) {
    LittleFS.end();
    return "FAIL open for write";
  }
  f.print("hello from qemu");
  f.close();
  f = LittleFS.open("/probe.txt", "r");
  String s = f ? f.readString() : String();
  if (f) f.close();
  size_t total = LittleFS.totalBytes(), used = LittleFS.usedBytes();
  LittleFS.end();
  return fmt("%s read-back='%s' total=%u used=%u", s == "hello from qemu" ? "OK" : "FAIL", s.c_str(),
             (unsigned)total, (unsigned)used);
}

static String t_wifi() {
  if (!WiFi.mode(WIFI_STA)) return "FAIL WiFi.mode(WIFI_STA) returned false";
  String mac = WiFi.macAddress();
  WiFi.begin("spike-no-such-ap", "password123");
  wl_status_t st = WL_IDLE_STATUS;
  uint32_t t0 = millis();
  while (millis() - t0 < 5000) {
    st = WiFi.status();
    delay(100);
  }
  int n = WiFi.scanNetworks();
  WiFi.mode(WIFI_OFF);
  return fmt("RAN mode=STA mac=%s status-after-5s=%d scanNetworks=%d", mac.c_str(), (int)st, n);
}

// --------------------------------------------------------------- runner ----

struct TestDef {
  const char* name;
  String (*fn)();
  uint32_t timeoutMs;
};

// Riskiest last, so a crash costs as little as possible.
static const TestDef TESTS[] = {
    {"info", t_info, 2000},          {"cpufreq", t_cpufreq, 3000}, {"timing", t_timing, 6000},
    {"tasks", t_tasks, 2000},        {"gpio", t_gpio, 2000},       {"gpio_isr", t_gpio_isr, 2000},
    {"adc", t_adc, 3000},            {"pwm_ledc", t_pwm_ledc, 3000}, {"hw_timer", t_hw_timer, 3000},
    {"uart1", t_uart1, 3000},        {"i2c", t_i2c, 10000},        {"spi", t_spi, 3000},
    {"rmt", t_rmt, 3000},            {"touch", t_touch, 3000},     {"temp", t_temp, 3000},
    {"random", t_random, 2000},      {"nvs", t_nvs, 5000},         {"littlefs", t_littlefs, 60000},
    {"wifi", t_wifi, 25000},
};

struct Job {
  String (*fn)();
  String out;
  SemaphoreHandle_t done;
};

static void jobTask(void* arg) {
  Job* j = static_cast<Job*>(arg);
  j->out = j->fn();
  xSemaphoreGive(j->done);
  vTaskDelete(nullptr);
}

static String runWithTimeout(String (*fn)(), uint32_t timeoutMs) {
  Job* j = new Job{fn, String(), xSemaphoreCreateBinary()};
  if (xTaskCreatePinnedToCore(jobTask, "probe_test", 12288, j, 1, nullptr, ARDUINO_RUNNING_CORE) != pdPASS) {
    vSemaphoreDelete(j->done);
    delete j;
    return "FAIL could not create the test task";
  }
  if (xSemaphoreTake(j->done, pdMS_TO_TICKS(timeoutMs)) != pdTRUE) {
    // The stuck task may still touch j, so it is deliberately leaked.
    return fmt("HANG no result within %lu ms", (unsigned long)timeoutMs);
  }
  String out = j->out;
  vSemaphoreDelete(j->done);
  delete j;
  return out;
}

static String g_skip = ",";
static bool isSkipped(const char* name) { return g_skip.indexOf(String(",") + name + ",") >= 0; }

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(3000);
  delay(50);
  Serial.printf("\nPROBE READY chip=%s millis=%lu tests=%u\n", ESP.getChipModel(), (unsigned long)millis(),
                (unsigned)(sizeof(TESTS) / sizeof(TESTS[0])));
  String line = Serial.readStringUntil('\n');  // the host's SKIP list; none after 3 s = run everything
  line.trim();
  if (line.startsWith("SKIP ")) {
    g_skip = String(",") + line.substring(5) + ",";
    g_skip.replace(" ", "");
  }
  for (const TestDef& t : TESTS) {
    if (isSkipped(t.name)) continue;
    Serial.printf("BEGIN %s\n", t.name);
    Serial.flush();
    String r = runWithTimeout(t.fn, t.timeoutMs);
    Serial.printf("RESULT %s %s\n", t.name, r.c_str());
    Serial.flush();
  }
  Serial.println("PROBE DONE");
}

void loop() { delay(1000); }
