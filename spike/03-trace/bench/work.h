#pragma once
#include <stdint.h>

// Iterations per loop() chunk. At 240 MHz on real silicon one iteration is
// roughly 10-15 cycles, so a chunk is ~0.2 s of real CPU time. (200k was too
// small: QEMU runs this loop faster than real silicon, so UART printing
// dominated.)
#define BENCH_CHUNK_ITERS 4000000u

uint32_t workChunk(uint32_t iters);
