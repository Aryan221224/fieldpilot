// SprayLog settings. Copy, edit, flash.
#pragma once

#define DEVICE_ID      "SPRAYLOG-T1"
#define FW_VERSION     "spraylog 1.0"

// --- network (used only after landing, to upload the sortie)
#define WIFI_SSID      "fieldpilot-hotspot"
#define WIFI_PASS      "change-me"
#define FP_SERVER      "http://192.168.43.100:8080"
#define FP_TOKEN       ""

// --- flow meter: pulses per litre. Start from the sheet, then calibrate with a jug:
//     send 'c' on the serial monitor, run the pump into a measuring jug, read the pulse count,
//     K = pulses / litres in the jug. (YF-S401 sheet: 5880. Drone flowmeters: see their sheet.)
#define K_PULSES_PER_L 5880.0f

// --- detection
#define ON_LPM         0.15f   // spraying at or above this
#define SESSION_GAP_S  60.0f   // a sortie ends after this long without flow, once landed
#define MAX_SAMPLES    900     // 1 Hz upload samples kept in RAM (15 min; halves rate if longer)

// --- pins (ESP32 DevKitC)
#define PIN_FLOW       27      // flow meter signal (NPN open collector): 4.7 k to 3.3 V
#define PIN_GPS_RX     16      // <- GPS TX (u-blox M8N, 9600 baud)
#define PIN_GPS_TX     17      // -> GPS RX (sets 5 Hz at boot)
#define PIN_SD_CS      5       // microSD on VSPI: SCK 18, MISO 19, MOSI 23
#define PIN_LED        2       // on-board LED: blink = GPS fix, solid = recording
