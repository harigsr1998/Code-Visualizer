// Spike step 3 mapping fixture: typical sketch patterns that the optimizer
// inlines or reorders at -Os (static helpers, a header-only template, a
// switch-based state machine), to measure how address -> line mapping degrades.
// Declared-before-use, so it is also valid plain C++ (path B).

#include "filter.h"
#include "blinker.h"

static MovingAverage<8> avg;
static Blinker blinker;
static uint32_t lastPrint = 0;
volatile uint32_t g_samples = 0;  // global for variable-read tests

static inline uint32_t scale(uint32_t v) {
  return (v * 3u) / 2u + 7u;
}

static uint32_t sensorSample(uint32_t n) {
  uint32_t v = (n * 2654435761u) >> 24;
  if (v > 200) {
    v = 200;
  }
  return scale(v);
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println("[map] start");
  blinker.begin();
}

void loop() {
  static uint32_t n = 0;
  uint32_t s = sensorSample(n++);
  g_samples = g_samples + 1;
  avg.add(s);
  blinker.step(avg.value());
  if (millis() - lastPrint >= 500) {
    lastPrint = millis();
    Serial.printf("[map] n=%lu avg=%lu state=%d\n", (unsigned long)n,
                  (unsigned long)avg.value(), (int)blinker.state());
  }
  delay(10);
}
