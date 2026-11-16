#include "blinker.h"

void Blinker::begin() {
  state_ = Idle;
  dwell_ = 0;
}

void Blinker::step(uint32_t level) {
  ++dwell_;
  switch (state_) {
    case Idle:
      if (level > 120) {
        state_ = Rising;
        dwell_ = 0;
      }
      break;
    case Rising:
      if (dwell_ > 3) {
        state_ = High;
        dwell_ = 0;
      }
      break;
    case High:
      if (level < 100) {
        state_ = Falling;
        dwell_ = 0;
      }
      break;
    case Falling:
      if (dwell_ > 3) {
        state_ = Idle;
        dwell_ = 0;
      }
      break;
  }
}
