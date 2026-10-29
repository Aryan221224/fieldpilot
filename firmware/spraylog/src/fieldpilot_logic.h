// fieldpilot_logic.h - the logic shared by FieldPilot's field devices.
//
// Plain C++11, no Arduino calls: the FieldMast and SprayLog sketches include a copy of this
// file (in their src/ folders, kept identical by tests/test_firmware.py), and the same code is
// compiled and unit-tested on a PC (tests/firmware/test_logic.cpp).
//
//   * spray-weather limits per rule pack, mirroring fieldpilot/rules.py
//   * Stull wet-bulb and Delta T, identical to fieldpilot/rules.py
//   * a rolling wind average and gust, as the SOP limits are about sustained wind
//   * anemometer conversions (analog 0.4-2.0 V, or pulses)
//   * flow-meter pulses -> L/min and litres, and a spray on/off and session detector
//   * a small NMEA parser (RMC + GGA) with checksum checking
//   * the SHT31 CRC and raw -> engineering-unit conversion
#pragma once

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

namespace fp {

// ----------------------------------------------------------------------------- rule packs
// NAN = no limit. Thresholds as in fieldpilot/rules.py PACKS.
struct Pack {
  const char* id;
  float wind_max_ms;   // spray only below this
  float wind_min_ms;   // and at or above this (inversion risk in calm air)
  float temp_max_c;    // and at or below this
  float rh_min_pct;    // and at or above this
  float dt_lo, dt_hi;  // Delta T range
};

static const Pack IN_SOP_2023 = {"IN-SOP-2023", 3.0f, 0.0f, 35.0f, 50.0f, NAN, NAN};
static const Pack GOOD_PRACTICE = {"GOOD-PRACTICE", 4.5f, 1.0f, NAN, NAN, 2.0f, 10.0f};

inline const Pack& pack_by_id(const char* id) {
  return (id && strcmp(id, "GOOD-PRACTICE") == 0) ? GOOD_PRACTICE : IN_SOP_2023;
}

// Stull (2011) wet-bulb temperature, valid for 5-99 % RH and -20..50 C (as rules.py).
inline float wet_bulb_c(float t, float rh) {
  if (rh < 5.0f) rh = 5.0f;
  if (rh > 99.0f) rh = 99.0f;
  return t * atanf(0.151977f * sqrtf(rh + 8.313659f)) + atanf(t + rh) - atanf(rh - 1.676331f) +
         0.00391838f * powf(rh, 1.5f) * atanf(0.023101f * rh) - 4.686035f;
}
inline float delta_t(float t, float rh) { return t - wet_bulb_c(t, rh); }

enum Reason : uint8_t {
  R_WIND_HIGH = 1, R_WIND_LOW = 2, R_TEMP_HIGH = 4, R_RH_LOW = 8, R_DELTA_T = 16, R_NO_DATA = 32
};

// 0 = GO. Otherwise a bitmask of Reason. Same comparisons as Pack.site_reasons in rules.py.
inline uint8_t verdict(const Pack& p, float wind, float temp, float rh) {
  if (isnan(wind) || isnan(temp) || isnan(rh)) return R_NO_DATA;
  uint8_t r = 0;
  if (wind >= p.wind_max_ms) r |= R_WIND_HIGH;
  if (wind < p.wind_min_ms) r |= R_WIND_LOW;
  if (!isnan(p.temp_max_c) && temp > p.temp_max_c) r |= R_TEMP_HIGH;
  if (!isnan(p.rh_min_pct) && rh < p.rh_min_pct) r |= R_RH_LOW;
  if (!isnan(p.dt_lo)) {
    float dt = delta_t(temp, rh);
    if (dt < p.dt_lo || dt > p.dt_hi) r |= R_DELTA_T;
  }
  return r;
}

// --------------------------------------------------------------------------------- wind
// Feed one sample per second. mean() = average over the last `window` seconds (SOP limits are
// about sustained wind); gust() = highest 3-second average in that window.
template <int N>
struct WindAverager {
  float buf[N];
  int n = 0, head = 0;
  void add(float v) {
    buf[head] = v;
    head = (head + 1) % N;
    if (n < N) n++;
  }
  float at(int i) const { return buf[(head - n + i + N) % N]; }   // i = 0 oldest
  float mean() const {
    if (!n) return NAN;
    float s = 0;
    for (int i = 0; i < n; i++) s += at(i);
    return s / n;
  }
  float gust() const {
    if (n < 3) return mean();
    float g = 0;
    for (int i = 2; i < n; i++) {
      float a = (at(i) + at(i - 1) + at(i - 2)) / 3.0f;
      if (a > g) g = a;
    }
    return g;
  }
  bool full() const { return n == N; }
};

// Adafruit 1733-type analog anemometer: 0.4 V = 0 m/s, 2.0 V = 32.4 m/s.
inline float wind_from_volts(float v, float v0 = 0.4f, float v1 = 2.0f, float ms1 = 32.4f) {
  if (v <= v0) return 0.0f;
  return (v - v0) / (v1 - v0) * ms1;
}
// Pulse cup anemometer: m/s per Hz from its sheet (e.g. 0.667 for a 1.492 mph/Hz head).
inline float wind_from_hz(float hz, float ms_per_hz) { return hz > 0 ? hz * ms_per_hz : 0.0f; }

// --------------------------------------------------------------------------------- flow
// k = pulses per litre, from the meter's sheet and then a jug calibration.
inline float flow_lpm(uint32_t pulses, float dt_s, float k) {
  return (dt_s > 0 && k > 0) ? (pulses / k) / dt_s * 60.0f : 0.0f;
}
inline float litres(uint32_t pulses, float k) { return k > 0 ? pulses / k : 0.0f; }

// Spray on as soon as flow reaches on_lpm; off after off_hold_s below it. A session (one
// sortie) closes after gap_s with no spray and the aircraft below still_ms (landed).
struct SprayDetector {
  float on_lpm = 0.15f, off_hold_s = 1.0f, gap_s = 60.0f, still_ms = 0.5f;
  bool spraying = false, in_session = false;
  float since_flow_s = 1e9f;
  // returns: 0 nothing, 1 session started, 2 session closed
  int update(float lpm, float speed_ms, float dt_s) {
    int ev = 0;
    if (lpm >= on_lpm) {
      since_flow_s = 0;
      spraying = true;
      if (!in_session) { in_session = true; ev = 1; }
    } else {
      since_flow_s += dt_s;
      if (since_flow_s >= off_hold_s) spraying = false;
      if (in_session && since_flow_s >= gap_s && (isnan(speed_ms) || speed_ms < still_ms)) {
        in_session = false;
        ev = 2;
      }
    }
    return ev;
  }
};

// ---------------------------------------------------------------------------------- NMEA
struct Fix {
  bool valid = false;      // RMC status A
  int quality = 0;         // GGA fix quality (0 = none)
  int sats = 0;
  double lat = NAN, lon = NAN;
  float speed_ms = NAN;
  float hdop = NAN;
  char utc[11] = {0};      // hhmmss.ss
};

inline int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

inline bool nmea_checksum_ok(const char* s) {
  if (!s || s[0] != '$') return false;
  uint8_t x = 0;
  const char* p = s + 1;
  while (*p && *p != '*') x ^= (uint8_t)*p++;
  if (*p != '*') return false;
  int h = hexval(p[1]), l = hexval(p[2]);
  return h >= 0 && l >= 0 && x == (uint8_t)(h * 16 + l);
}

// ddmm.mmmm + hemisphere -> signed degrees
inline double nmea_deg(const char* f, char hemi) {
  if (!f || !*f) return NAN;
  double v = atof(f);
  int d = (int)(v / 100);
  double deg = d + (v - d * 100) / 60.0;
  return (hemi == 'S' || hemi == 'W') ? -deg : deg;
}

// Split a sentence into fields (in place on a copy). Returns the field count.
inline int nmea_fields(char* buf, char* out[], int maxf) {
  int n = 0;
  char* p = buf;
  out[n++] = p;
  while (*p && *p != '*' && n < maxf) {
    if (*p == ',') { *p = 0; out[n++] = p + 1; }
    p++;
  }
  if (*p == '*') *p = 0;
  return n;
}

// Feed one complete line ($GPRMC/$GNRMC/$GPGGA/$GNGGA). Returns true if it updated `fix`.
inline bool nmea_parse(const char* line, Fix& fix) {
  if (!nmea_checksum_ok(line)) return false;
  char buf[100];
  size_t L = strlen(line);
  if (L >= sizeof(buf)) return false;
  memcpy(buf, line, L + 1);
  char* f[20];
  int n = nmea_fields(buf, f, 20);
  const char* type = f[0] + 3;                     // skip $GP / $GN
  if (strncmp(type, "RMC", 3) == 0 && n >= 8) {
    strncpy(fix.utc, f[1], sizeof(fix.utc) - 1);
    fix.valid = f[2][0] == 'A';
    if (fix.valid) {
      fix.lat = nmea_deg(f[3], f[4][0]);
      fix.lon = nmea_deg(f[5], f[6][0]);
      fix.speed_ms = (float)(atof(f[7]) * 0.514444);
    }
    return true;
  }
  if (strncmp(type, "GGA", 3) == 0 && n >= 9) {
    fix.quality = atoi(f[6]);
    fix.sats = atoi(f[7]);
    fix.hdop = f[8][0] ? (float)atof(f[8]) : NAN;
    if (fix.quality > 0) {
      fix.lat = nmea_deg(f[2], f[3][0]);
      fix.lon = nmea_deg(f[4], f[5][0]);
    }
    return true;
  }
  return false;
}

// u-blox UBX-CFG-RATE: 200 ms measurement period (5 Hz), checksum included.
static const uint8_t UBX_RATE_5HZ[] = {0xB5, 0x62, 0x06, 0x08, 0x06, 0x00, 0xC8, 0x00,
                                       0x01, 0x00, 0x01, 0x00, 0xDE, 0x6A};

inline void ubx_checksum(const uint8_t* msg, size_t len, uint8_t& a, uint8_t& b) {
  a = b = 0;                                      // over class..payload (skip 2 sync bytes)
  for (size_t i = 2; i < len; i++) { a += msg[i]; b += a; }
}

// --------------------------------------------------------------------------------- SHT31
inline uint8_t sht31_crc(const uint8_t* d, int len) {
  uint8_t crc = 0xFF;
  for (int i = 0; i < len; i++) {
    crc ^= d[i];
    for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}
inline float sht31_temp_c(uint16_t raw) { return -45.0f + 175.0f * raw / 65535.0f; }
inline float sht31_rh(uint16_t raw) { return 100.0f * raw / 65535.0f; }

// Decode the 6-byte answer to a single-shot measurement. False on a CRC error.
inline bool sht31_decode(const uint8_t b[6], float& t, float& rh) {
  if (sht31_crc(b, 2) != b[2] || sht31_crc(b + 3, 2) != b[5]) return false;
  t = sht31_temp_c((uint16_t)(b[0] << 8 | b[1]));
  rh = sht31_rh((uint16_t)(b[3] << 8 | b[4]));
  return true;
}

}  // namespace fp
