#pragma once
#include <stdint.h>

// Header-only template: every method is a candidate for inlining.
template <unsigned N>
class MovingAverage {
 public:
  void add(uint32_t v) {
    sum_ -= buf_[pos_];
    buf_[pos_] = v;
    sum_ += v;
    pos_ = (pos_ + 1) % N;
    if (count_ < N) {
      ++count_;
    }
  }
  uint32_t value() const {
    return count_ ? sum_ / count_ : 0;
  }

 private:
  uint32_t buf_[N] = {};
  uint32_t sum_ = 0;
  unsigned pos_ = 0;
  unsigned count_ = 0;
};
