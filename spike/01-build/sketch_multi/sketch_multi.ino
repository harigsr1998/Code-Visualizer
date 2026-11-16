// Spike test sketch (multi-file): .ino + .h/.cpp next to it + a library-style
// module under src/. It prints over UART0 so the QEMU boot test can see it run.
//
// Everything is declared before use, so this file is also valid plain C++
// (path B includes it from a .cpp, where the Arduino IDE's automatic prototype
// generation doesn't happen).

#include "counter.h"
#include "src/mathutil/fib.h"

#ifndef SPIKE_BUILD_PATH
#define SPIKE_BUILD_PATH "arduino-cli"
#endif

#if defined(__OPTIMIZE_SIZE__)
#define SPIKE_OPT "-Os"
#elif defined(__OPTIMIZE__)
#define SPIKE_OPT "-O1+/-Og"
#else
#define SPIKE_OPT "-O0"
#endif

static Counter ticks;

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.printf("\n[spike] boot ok via %s, sketch built with %s\n", SPIKE_BUILD_PATH, SPIKE_OPT);
  Serial.printf("[spike] chip %s rev %d, %d core(s), %lu MHz\n", ESP.getChipModel(),
                ESP.getChipRevision(), ESP.getChipCores(), (unsigned long)ESP.getCpuFreqMHz());
}

void loop() {
  uint32_t n = ticks.next();
  uint32_t k = n % 30;
  Serial.printf("[spike] tick %lu fib(%lu)=%lu\n", (unsigned long)n, (unsigned long)k,
                (unsigned long)fib(k));
  delay(500);
}
