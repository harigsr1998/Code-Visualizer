// Spike step 3 benchmark: a fixed amount of CPU work per "chunk", printed over
// UART0. The host timestamps each line, so chunks per wall-clock second measure
// emulation throughput whatever the guest's own clock says (the step 1 sketch
// mostly sleeps in delay(), so its tick rate hides CPU slowdowns).
//
// Core 1 runs loop() (Arduino default). A second task pinned to core 0 does
// the same work, so a trace can show which core executed what.
// Declared-before-use, so it is also valid plain C++ (path B).

#include "work.h"

static volatile uint32_t core0Chunks = 0;

static void core0Task(void *) {
  for (;;) {
    core0Chunks = core0Chunks + 1;
    uint32_t r = workChunk(BENCH_CHUNK_ITERS / 4);
    (void)r;
    vTaskDelay(1);  // let the idle task run so the watchdog stays quiet
  }
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.printf("\n[bench] start, %u iters/chunk, core %d\n", (unsigned)BENCH_CHUNK_ITERS,
                xPortGetCoreID());
  xTaskCreatePinnedToCore(core0Task, "core0work", 4096, nullptr, 1, nullptr, 0);
}

void loop() {
  static uint32_t chunk = 0;
  uint32_t t0 = millis();
  uint32_t r = workChunk(BENCH_CHUNK_ITERS);
  uint32_t dt = millis() - t0;
  ++chunk;
  Serial.printf("[bench] chunk %lu r=%08lx guest_ms=%lu core0=%lu\n", (unsigned long)chunk,
                (unsigned long)r, (unsigned long)dt, (unsigned long)core0Chunks);
}
