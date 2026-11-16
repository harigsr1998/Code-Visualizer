// Turns the .ino into a normal C++ translation unit: include Arduino.h first
// (the Arduino IDE does this implicitly), then the sketch itself.
// Limitation: no automatic function prototypes; see spike/NOTES.md.
#include <Arduino.h>

#include "sketch_multi.ino"
