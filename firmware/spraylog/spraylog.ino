// SprayLog - an as-applied record for a drone whose controller is closed.
//
// The T1's JIYI K3-A Pro shares no telemetry, so nothing shows how much was actually
// sprayed, or where. SprayLog is a passive logger strapped to the airframe:
//
//   flow meter in the spray line (after the pump, before the nozzle manifold)
//   + GPS (u-blox M8N at 5 Hz) + microSD + ESP32, powered by a 5 V buck from the drone's bus
//
// It never connects to the flight controller or the pump: it only measures. Per sortie:
//
//   * every 200 ms: flow (L/min), litres so far, position, ground speed -> SD card (CSV)
//   * when the tank has stopped flowing for 60 s and the aircraft is still (landed), the
//     sortie is closed and uploaded over the operator's hotspot to FieldPilot
//     (/api/devices/spraylog), which works out litres per zone against the plan, L/ha, and
//     any spray outside the treated zones or outside the field
//   * if the upload fails, it retries every 60 s while idle; the CSV stays on the card either way
//
// Serial commands (115200): 'c' prints raw pulse counts each second (jug calibration),
// 'r' resets the count, 's' prints the state.
//
// Board: ESP32 DevKitC (Arduino core 2.x or 3.x). No libraries beyond the core.

#include <Arduino.h>
#include <HTTPClient.h>
#include <SD.h>
#include <SPI.h>
#include <WiFi.h>

#include "config.h"
#include "src/fieldpilot_logic.h"

static volatile uint32_t flow_pulses = 0;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
void IRAM_ATTR on_flow() {
  portENTER_CRITICAL_ISR(&mux);
  flow_pulses++;
  portEXIT_CRITICAL_ISR(&mux);
}
static uint32_t pulses_now() {
  portENTER_CRITICAL(&mux);
  uint32_t p = flow_pulses;
  portEXIT_CRITICAL(&mux);
  return p;
}

HardwareSerial GPS(2);
static fp::Fix fix;
static char nmea[100];
static int nmea_len = 0;

static fp::SprayDetector det;
static bool sd_ok = false, cal_mode = false, pending = false;
static File csv;
static char session_id[40];
static uint32_t session_n = 0, session_t0 = 0, session_p0 = 0, last_tick = 0, last_p = 0;
static uint32_t last_sample_ms = 0, last_retry_ms = 0, last_cal_ms = 0;

struct Sample { float t_s; double lat, lon; float lpm, litres, speed; uint8_t q; };
static Sample samples[MAX_SAMPLES];
static int n_samples = 0;
static uint32_t sample_every_ms = 1000;

static void gps_poll() {
  while (GPS.available()) {
    char c = (char)GPS.read();
    if (c == '$') nmea_len = 0;
    if (c == '\r') continue;
    if (c == '\n') {
      nmea[nmea_len] = 0;
      fp::nmea_parse(nmea, fix);
      nmea_len = 0;
    } else if (nmea_len < (int)sizeof(nmea) - 1) {
      nmea[nmea_len++] = c;
    }
  }
}

static void keep_sample(const Sample& s) {
  if (n_samples == MAX_SAMPLES) {          // long sortie: keep every other sample, halve the rate
    for (int i = 0; i < MAX_SAMPLES / 2; i++) samples[i] = samples[2 * i];
    n_samples = MAX_SAMPLES / 2;
    sample_every_ms *= 2;
  }
  samples[n_samples++] = s;
}

static void open_session() {
  session_n++;
  snprintf(session_id, sizeof(session_id), "%s-%08lX-%lu", DEVICE_ID,
           (unsigned long)(ESP.getEfuseMac() & 0xFFFFFFFF), (unsigned long)session_n);
  session_t0 = millis();
  session_p0 = pulses_now();
  n_samples = 0;
  sample_every_ms = 1000;
  if (sd_ok) {
    char path[48];
    snprintf(path, sizeof(path), "/%s.csv", session_id);
    csv = SD.open(path, FILE_WRITE);
    if (csv) csv.println("t_s,lat,lon,lpm,litres,speed_ms,fix_q,sats,hdop");
  }
  Serial.printf("session %s started\n", session_id);
}

static bool upload() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) delay(500);
    if (WiFi.status() != WL_CONNECTED) return false;
  }
  float total = fp::litres(pulses_now() - session_p0, K_PULSES_PER_L);
  String body;
  body.reserve(200 + n_samples * 72);
  char head[256];
  snprintf(head, sizeof(head),
           "{\"device_id\":\"%s\",\"fw\":\"%s\",\"session_id\":\"%s\",\"k_pulses_per_l\":%.1f,"
           "\"pulses\":%lu,\"litres_total\":%.4f,\"sample_period_ms\":%lu,\"samples\":[",
           DEVICE_ID, FW_VERSION, session_id, K_PULSES_PER_L,
           (unsigned long)(pulses_now() - session_p0), total, (unsigned long)sample_every_ms);
  body += head;
  char row[96];
  for (int i = 0; i < n_samples; i++) {
    const Sample& s = samples[i];
    if (isnan(s.lat) || isnan(s.lon))
      snprintf(row, sizeof(row), "%s[%.1f,null,null,%.3f,%.4f,null,0]", i ? "," : "", s.t_s, s.lpm, s.litres);
    else
      snprintf(row, sizeof(row), "%s[%.1f,%.7f,%.7f,%.3f,%.4f,%.2f,%u]", i ? "," : "", s.t_s,
               s.lat, s.lon, s.lpm, s.litres, isnan(s.speed) ? 0.0f : s.speed, s.q);
    body += row;
  }
  body += "]}";
  HTTPClient http;
  http.setTimeout(15000);
  http.begin(String(FP_SERVER) + "/api/devices/spraylog");
  http.addHeader("Content-Type", "application/json");
  if (strlen(FP_TOKEN)) http.addHeader("X-FP-Token", FP_TOKEN);
  int code = http.POST(body);
  http.end();
  Serial.printf("upload %s: HTTP %d, %d samples, %.3f L\n", session_id, code, n_samples, total);
  if (code == 200) WiFi.disconnect(true);
  return code == 200;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_FLOW, INPUT_PULLUP);                    // plus the external 4.7 k to 3.3 V
  attachInterrupt(digitalPinToInterrupt(PIN_FLOW), on_flow, FALLING);
  GPS.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  delay(200);
  GPS.write(fp::UBX_RATE_5HZ, sizeof(fp::UBX_RATE_5HZ));
  sd_ok = SD.begin(PIN_SD_CS);
  det.on_lpm = ON_LPM;
  det.gap_s = SESSION_GAP_S;
  last_tick = millis();
  Serial.printf("SprayLog %s  K=%.1f pulses/L  SD %s\n", DEVICE_ID, K_PULSES_PER_L, sd_ok ? "ok" : "MISSING");
}

void loop() {
  gps_poll();
  uint32_t now = millis();

  if (Serial.available()) {
    char c = (char)Serial.read();
    if (c == 'c') cal_mode = !cal_mode;
    if (c == 'r') { portENTER_CRITICAL(&mux); flow_pulses = 0; portEXIT_CRITICAL(&mux); last_p = 0; }
    if (c == 's') Serial.printf("pulses %lu  fix %d sats %d  session %d  pending %d\n",
                                (unsigned long)pulses_now(), fix.quality, fix.sats, det.in_session, pending);
  }
  if (cal_mode && now - last_cal_ms >= 1000) {
    last_cal_ms = now;
    Serial.printf("CAL pulses %lu\n", (unsigned long)pulses_now());
  }

  if (now - last_tick >= 200) {
    float dt = (now - last_tick) / 1000.0f;
    last_tick = now;
    uint32_t p = pulses_now();
    float lpm = fp::flow_lpm(p - last_p, dt, K_PULSES_PER_L);
    last_p = p;
    int ev = det.update(lpm, fix.speed_ms, dt);
    if (ev == 1) open_session();
    if (det.in_session || ev == 2) {
      float t_s = (now - session_t0) / 1000.0f;
      float l = fp::litres(p - session_p0, K_PULSES_PER_L);
      uint8_t q = (uint8_t)fix.quality;
      if (csv) csv.printf("%.2f,%.7f,%.7f,%.3f,%.4f,%.2f,%u,%d,%.1f\n", t_s, fix.lat, fix.lon, lpm, l,
                          fix.speed_ms, q, fix.sats, fix.hdop);
      if (now - last_sample_ms >= sample_every_ms || ev == 2) {
        last_sample_ms = now;
        keep_sample({t_s, fix.lat, fix.lon, lpm, l, fix.speed_ms, q});
      }
    }
    if (ev == 2) {
      if (csv) csv.close();
      Serial.printf("session %s closed\n", session_id);
      pending = !upload();
      last_retry_ms = now;
    }
  }
  if (pending && !det.in_session && now - last_retry_ms >= 60000UL) {
    last_retry_ms = now;
    pending = !upload();
  }
  digitalWrite(PIN_LED, det.in_session ? HIGH : (fix.quality > 0 ? ((now / 500) % 2) : LOW));
}
