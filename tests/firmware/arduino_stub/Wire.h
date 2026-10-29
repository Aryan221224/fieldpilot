#pragma once
#include "Arduino.h"
class TwoWire {
 public:
  void begin(int, int) {}
  void beginTransmission(int) {}
  size_t write(uint8_t) { return 1; }
  uint8_t endTransmission() { return 0; }
  uint8_t requestFrom(int, int n) { return (uint8_t)n; }
  int read() { return 0; }
};
extern TwoWire Wire;
