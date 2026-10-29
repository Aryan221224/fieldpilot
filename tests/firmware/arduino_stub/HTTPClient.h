#pragma once
#include "Arduino.h"
class HTTPClient {
 public:
  void setTimeout(int) {}
  bool begin(const String&) { return true; }
  void addHeader(const char*, const char*) {}
  int POST(const String&) { return 200; }
  void end() {}
};
