#pragma once
#include <stdint.h>

class Blinker {
 public:
  enum State : uint8_t { Idle, Rising, High, Falling };
  void begin();
  void step(uint32_t level);
  State state() const { return state_; }

 private:
  State state_ = Idle;
  uint32_t dwell_ = 0;
};
