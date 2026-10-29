#pragma once
#include "Arduino.h"
#define WIFI_STA 1
#define WL_CONNECTED 3
class WiFiClass {
 public:
  void mode(int) {}
  void begin(const char*, const char*) {}
  int status() { return 0; }
  void reconnect() {}
  void disconnect(bool = false) {}
  int RSSI() { return -60; }
};
extern WiFiClass WiFi;
