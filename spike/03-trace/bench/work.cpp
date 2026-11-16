#include "work.h"

// A small xorshift + branch loop: several basic blocks per iteration, so the
// trace has a realistic mix of short blocks, and the compiler can't fold it.
uint32_t workChunk(uint32_t iters) {
  uint32_t x = 0x12345678u;
  uint32_t acc = 0;
  for (uint32_t i = 0; i < iters; ++i) {
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    if (x & 1u) {
      acc += x;
    } else {
      acc ^= x >> 3;
    }
  }
  return acc ^ x;
}
