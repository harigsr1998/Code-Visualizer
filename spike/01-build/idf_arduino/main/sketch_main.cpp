// Turns the .ino into a normal C++ translation unit: include Arduino.h first
// (the Arduino IDE does this implicitly), then the sketch itself.
// SPIKE_SKETCH_INO is set by main/CMakeLists.txt, e.g. "sketch_multi.ino".
// Limitation: no automatic function prototypes; see spike/NOTES.md.
#include <Arduino.h>

#include SPIKE_SKETCH_INO
