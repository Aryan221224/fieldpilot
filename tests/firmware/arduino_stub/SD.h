#pragma once
#include "Arduino.h"
#define FILE_WRITE "w"
class File : public Print {
 public:
  bool ok = false;
  operator bool() const { return ok; }
  void close() {}
};
class SDClass {
 public:
  bool begin(int) { return true; }
  File open(const char*, const char*) { return File(); }
};
extern SDClass SD;
