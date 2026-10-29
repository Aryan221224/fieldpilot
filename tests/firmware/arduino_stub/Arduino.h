// Minimal Arduino-ESP32 API stand-ins, enough to compile the sketches on a PC (syntax and types).
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#define IRAM_ATTR
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define FALLING 2
#define SERIAL_8N1 0
typedef struct { int x; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED {0}
inline void portENTER_CRITICAL(portMUX_TYPE*) {}
inline void portEXIT_CRITICAL(portMUX_TYPE*) {}
inline void portENTER_CRITICAL_ISR(portMUX_TYPE*) {}
inline void portEXIT_CRITICAL_ISR(portMUX_TYPE*) {}
inline uint32_t millis() { return 0; }
inline void delay(uint32_t) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline uint32_t analogReadMilliVolts(int) { return 0; }
inline int digitalPinToInterrupt(int p) { return p; }
inline void attachInterrupt(int, void (*)(), int) {}
class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c) {}
  String(const std::string& x) : s(x) {}
  void reserve(size_t n) { s.reserve(n); }
  String& operator+=(const char* c) { s += c; return *this; }
  String operator+(const char* c) const { return String(s + c); }
  void replace(const char* a, const char* b) {
    size_t p = 0, la = strlen(a), lb = strlen(b);
    while ((p = s.find(a, p)) != std::string::npos) { s.replace(p, la, b); p += lb; }
  }
  const char* c_str() const { return s.c_str(); }
};
class Print {
 public:
  size_t print(const char*) { return 0; }
  size_t println(const char*) { return 0; }
  int printf(const char*, ...) { return 0; }
  size_t write(const uint8_t*, size_t n) { return n; }
};
class HardwareSerial : public Print {
 public:
  HardwareSerial(int = 0) {}
  void begin(unsigned long, int = 0, int = -1, int = -1) {}
  int available() { return 0; }
  int read() { return -1; }
};
extern HardwareSerial Serial;
class EspClass { public: uint64_t getEfuseMac() { return 0; } };
extern EspClass ESP;
