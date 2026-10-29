// FieldMast settings. Copy, edit, flash. Nothing here is secret except the Wi-Fi password and
// the device token; keep this file out of a public repository once you have filled them in.
#pragma once

// --- identity and rules
#define DEVICE_ID      "MAST-01"
#define FW_VERSION     "fieldmast 1.0"
#define FP_RULE_PACK   "IN-SOP-2023"        // or "GOOD-PRACTICE" (must match the server's)

// --- network: the operator's phone hotspot, and the FieldPilot server on it or in the cloud
#define WIFI_SSID      "fieldpilot-hotspot"
#define WIFI_PASS      "change-me"
#define FP_SERVER      "http://192.168.43.100:8080"   // no trailing slash
#define FP_TOKEN       ""                             // = FP_DEVICE_TOKEN on the server, if set
#define POST_EVERY_MS  10000

// --- mast position (decimal degrees). 0,0 = "at the field the console has open".
#define MAST_LAT       0.0
#define MAST_LON       0.0

// --- anemometer: analog 0.4-2.0 V (Adafruit 1733 type, 7-24 V supply) or a pulse cup head
#define ANEMO_PULSE      0                 // 1 = pulse head on PIN_ANEMO
#define ANEMO_MS_PER_HZ  0.667f            // pulse heads only: from the head's sheet
#define WIND_WINDOW_S    120               // sustained wind = 2-minute mean

// --- pins (ESP32 DevKitC)
#define PIN_ANEMO      34    // ADC1, input only. Analog: the anemometer's signal wire directly
                             // (max 2.0 V). Pulse: open collector with 10 k to 3.3 V.
#define PIN_VBAT       35    // ADC1: supply through a divider, see VBAT_DIVIDER
#define VBAT_DIVIDER   5.7f  // 47 k / 10 k divider -> 12 V reads as 2.1 V
#define PIN_SDA        21    // SHT31 (I2C address 0x44), in a radiation shield, 1.5-2 m up
#define PIN_SCL        22
#define PIN_LED_GO     25    // green: inside the rule pack's limits
#define PIN_LED_NOGO   26    // red: outside them
#define PIN_LED_WARN   27    // amber: warming up, sensor fault or no server
