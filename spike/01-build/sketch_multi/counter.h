#pragma once

#include <stdint.h>

// Tiny class so the sketch has a second translation unit with a C++ method.
class Counter {
 public:
  uint32_t next();

 private:
  uint32_t value_ = 0;
};
