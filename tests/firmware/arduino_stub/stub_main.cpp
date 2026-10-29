#include "Arduino.h"
#include "WiFi.h"
#include "Wire.h"
#include "SD.h"
HardwareSerial Serial;
EspClass ESP;
WiFiClass WiFi;
TwoWire Wire;
SDClass SD;
void setup();
void loop();
int main() { setup(); loop(); return 0; }
