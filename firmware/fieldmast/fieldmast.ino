// FieldMast - spray-weather mast for the field FieldPilot is about to treat.
//
// A forecast describes a grid cell. The SOP's limits (India 2023: wind < 3 m/s, <= 35 C,
// RH >= 50 %) are about the air over the field at the moment of spraying. FieldMast measures
// it there: a cup anemometer and an SHT31 on a 2 m pole at the field edge.
//
//   * three LEDs show GO / NO-GO for the rule pack on the mast itself, without any network
//   * every 10 s it POSTs the 2-minute mean wind, 3-second gust, temperature, RH and
//     Delta T to FieldPilot (/api/devices/mast) over the operator's phone hotspot
//   * FieldPilot then refuses to approve a sortie while the mast says NO-GO, and shows the
//     reading live on the Field Ops dashboard (/ops)
//
// Board: ESP32 DevKitC (Arduino core 2.x or 3.x). No libraries beyond the core.
// Wiring and parts: docs/SYSTEMS.md. Settings: config.h.

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <Wire.h>

#include "config.h"
#include "src/fieldpilot_logic.h"

static const fp::Pack& PACK = fp::pack_by_id(FP_RULE_PACK);
static fp::WindAverager<WIND_WINDOW_S> wind;

static float temp_c = NAN, rh_pct = NAN;
static uint32_t last_sample_ms = 0, last_sht_ms = 0, last_post_ms = 0, last_ok_post_ms = 0;
static uint32_t sht_errors = 0;
static int last_http = 0;

#if ANEMO_PULSE
static volatile uint32_t anemo_pulses = 0;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
void IRAM_ATTR on_anemo() {
  portENTER_CRITICAL_ISR(&mux);
  anemo_pulses++;
  portEXIT_CRITICAL_ISR(&mux);
}
#endif

static float read_wind_sample() {
#if ANEMO_PULSE
  static uint32_t last_p = 0, last_ms = 0;
  portENTER_CRITICAL(&mux);
  uint32_t p = anemo_pulses;
  portEXIT_CRITICAL(&mux);
  uint32_t now = millis();
  float hz = last_ms ? (p - last_p) * 1000.0f / (float)(now - last_ms) : 0.0f;
  last_p = p;
  last_ms = now;
  return fp::wind_from_hz(hz, ANEMO_MS_PER_HZ);
#else
  return fp::wind_from_volts(analogReadMilliVolts(PIN_ANEMO) / 1000.0f);
#endif
}

static bool sht31_read(float& t, float& h) {
  Wire.beginTransmission(0x44);
  Wire.write(0x24);                      // single shot, high repeatability, no clock stretching
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) return false;
  delay(16);
  if (Wire.requestFrom(0x44, 6) != 6) return false;
  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.read();
  return fp::sht31_decode(b, t, h);
}

static float vbat() { return analogReadMilliVolts(PIN_VBAT) / 1000.0f * VBAT_DIVIDER; }

static uint8_t current_verdict() {
  if (wind.n < 30) return fp::R_NO_DATA;                    // 30 s of wind before any verdict
  return fp::verdict(PACK, wind.mean(), temp_c, rh_pct);
}

static void show(uint8_t v) {
  bool stale = (v & fp::R_NO_DATA) || (millis() - last_ok_post_ms > 60000UL);
  digitalWrite(PIN_LED_GO, v == 0);
  digitalWrite(PIN_LED_NOGO, v != 0 && !(v & fp::R_NO_DATA));
  digitalWrite(PIN_LED_WARN, stale && ((millis() / 500) % 2));   // blink
}

static void post_reading(uint8_t v) {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }
  char body[512];
  float m = wind.mean(), g = wind.gust();
  float dt = (isnan(temp_c) || isnan(rh_pct)) ? NAN : fp::delta_t(temp_c, rh_pct);
  snprintf(body, sizeof(body),
           "{\"device_id\":\"%s\",\"fw\":\"%s\",\"pack\":\"%s\",\"uptime_s\":%lu,"
           "\"wind_ms\":%.2f,\"gust_ms\":%.2f,\"wind_samples\":%d,\"temp_c\":%.2f,\"rh_pct\":%.1f,"
           "\"delta_t\":%.2f,\"device_verdict\":%u,\"lat\":%.7f,\"lon\":%.7f,"
           "\"battery_v\":%.2f,\"rssi\":%d,\"sht_errors\":%lu}",
           DEVICE_ID, FW_VERSION, PACK.id, (unsigned long)(millis() / 1000), m, g, wind.n,
           temp_c, rh_pct, dt, v, (double)MAST_LAT, (double)MAST_LON, vbat(), WiFi.RSSI(),
           (unsigned long)sht_errors);
  // printf writes "nan" for missing values; JSON needs null
  String s(body);
  s.replace("nan", "null");
  HTTPClient http;
  http.setTimeout(4000);
  http.begin(String(FP_SERVER) + "/api/devices/mast");
  http.addHeader("Content-Type", "application/json");
  if (strlen(FP_TOKEN)) http.addHeader("X-FP-Token", FP_TOKEN);
  last_http = http.POST(s);
  http.end();
  if (last_http == 200) last_ok_post_ms = millis();
  Serial.printf("post %d  %s\n", last_http, s.c_str());
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED_GO, OUTPUT);
  pinMode(PIN_LED_NOGO, OUTPUT);
  pinMode(PIN_LED_WARN, OUTPUT);
#if ANEMO_PULSE
  pinMode(PIN_ANEMO, INPUT);                                 // external 10 k pull-up
  attachInterrupt(digitalPinToInterrupt(PIN_ANEMO), on_anemo, FALLING);
#endif
  Wire.begin(PIN_SDA, PIN_SCL);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("FieldMast %s  pack %s  server %s\n", DEVICE_ID, PACK.id, FP_SERVER);
}

void loop() {
  uint32_t now = millis();
  if (now - last_sample_ms >= 1000) {
    last_sample_ms = now;
    wind.add(read_wind_sample());
  }
  if (now - last_sht_ms >= 2000) {
    last_sht_ms = now;
    float t, h;
    if (sht31_read(t, h)) {
      temp_c = t;
      rh_pct = h;
    } else {
      sht_errors++;
      if (sht_errors % 5 == 0) { temp_c = NAN; rh_pct = NAN; }   // persistent fault -> no verdict
    }
  }
  uint8_t v = current_verdict();
  show(v);
  if (now - last_post_ms >= POST_EVERY_MS) {
    last_post_ms = now;
    post_reading(v);
  }
  delay(20);
}
