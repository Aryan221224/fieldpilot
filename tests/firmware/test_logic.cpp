// Host unit tests for firmware/common/fieldpilot_logic.h (built and run by tests/test_firmware.py).
// With an argument "deltat" it prints a Delta T grid for the Python cross-check instead.
#include <stdio.h>
#include <string.h>

#include "../../firmware/common/fieldpilot_logic.h"

static int fails = 0;
#define CHECK(c)                                                    \
  do {                                                              \
    if (!(c)) { printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); fails++; } \
  } while (0)
#define NEAR(a, b, tol) CHECK(fabs((double)(a) - (double)(b)) <= (tol))

static void nmea_with_checksum(const char* body, char* out) {
  uint8_t x = 0;
  for (const char* p = body + 1; *p; p++) x ^= (uint8_t)*p;
  sprintf(out, "%s*%02X", body, x);
}

int main(int argc, char** argv) {
  if (argc > 1 && strcmp(argv[1], "deltat") == 0) {
    for (int t = 10; t <= 45; t += 5)
      for (int rh = 10; rh <= 100; rh += 15) printf("%d %d %.4f\n", t, rh, fp::delta_t((float)t, (float)rh));
    return 0;
  }

  // --- rule packs (IN-SOP-2023: wind < 3, <= 35 C, RH >= 50)
  CHECK(fp::verdict(fp::IN_SOP_2023, 2.9f, 30, 60) == 0);
  CHECK(fp::verdict(fp::IN_SOP_2023, 3.0f, 30, 60) == fp::R_WIND_HIGH);
  CHECK(fp::verdict(fp::IN_SOP_2023, 1.0f, 35.5f, 49) == (fp::R_TEMP_HIGH | fp::R_RH_LOW));
  CHECK(fp::verdict(fp::IN_SOP_2023, NAN, 30, 60) == fp::R_NO_DATA);
  // GOOD-PRACTICE: 1 <= wind < 4.5, Delta T 2..10
  CHECK(fp::verdict(fp::GOOD_PRACTICE, 0.5f, 25, 60) & fp::R_WIND_LOW);
  CHECK(fp::verdict(fp::GOOD_PRACTICE, 2.0f, 25, 60) == 0);
  CHECK(fp::verdict(fp::GOOD_PRACTICE, 2.0f, 38, 15) & fp::R_DELTA_T);
  CHECK(&fp::pack_by_id("GOOD-PRACTICE") == &fp::GOOD_PRACTICE);
  CHECK(&fp::pack_by_id("anything") == &fp::IN_SOP_2023);

  // --- wind averaging
  fp::WindAverager<120> w;
  CHECK(isnan(w.mean()));
  for (int i = 0; i < 120; i++) w.add(i == 60 || i == 61 || i == 62 ? 6.0f : 2.0f);
  NEAR(w.mean(), (117 * 2.0 + 3 * 6.0) / 120.0, 1e-4);
  NEAR(w.gust(), 6.0, 1e-4);
  CHECK(w.full());
  for (int i = 0; i < 120; i++) w.add(1.0f);
  NEAR(w.mean(), 1.0, 1e-5);
  NEAR(fp::wind_from_volts(0.4f), 0.0, 1e-6);
  NEAR(fp::wind_from_volts(2.0f), 32.4, 1e-4);
  NEAR(fp::wind_from_volts(0.3f), 0.0, 1e-6);
  NEAR(fp::wind_from_hz(3.0f, 0.667f), 2.001, 1e-3);

  // --- flow: 5880 pulses/L (YF-S401) at 2.16 L/min for 1 s = 211.68 pulses
  NEAR(fp::flow_lpm(212, 1.0f, 5880.0f), 2.163, 2e-3);
  NEAR(fp::litres(5880, 5880.0f), 1.0, 1e-6);
  CHECK(fp::flow_lpm(100, 0.0f, 5880.0f) == 0.0f);

  // --- spray / session detection
  fp::SprayDetector d;
  CHECK(d.update(0.0f, 0.0f, 1) == 0);
  CHECK(d.update(2.0f, 4.5f, 1) == 1 && d.spraying && d.in_session);
  CHECK(d.update(0.0f, 4.5f, 0.5f) == 0 && d.spraying);         // within the off hold
  CHECK(d.update(0.0f, 4.5f, 0.6f) == 0 && !d.spraying);        // turn: spray off
  for (int i = 0; i < 120; i++) CHECK(d.update(0.0f, 5.0f, 1) == 0);   // still flying: open
  int ev = 0;
  for (int i = 0; i < 70 && !ev; i++) ev = d.update(0.0f, 0.1f, 1);   // landed
  CHECK(ev == 2 && !d.in_session);

  // --- NMEA
  char rmc[120], gga[120];
  nmea_with_checksum("$GNRMC,064512.20,A,2826.9910,N,07735.0200,E,8.747,45.0,281026,,,A", rmc);
  nmea_with_checksum("$GNGGA,064512.20,2826.9910,N,07735.0200,E,1,12,0.8,212.0,M,-36.0,M,,", gga);
  fp::Fix fx;
  CHECK(fp::nmea_checksum_ok(rmc) && fp::nmea_checksum_ok(gga));
  CHECK(fp::nmea_parse(rmc, fx) && fx.valid);
  NEAR(fx.lat, 28 + 26.9910 / 60.0, 1e-7);
  NEAR(fx.lon, 77 + 35.0200 / 60.0, 1e-7);
  NEAR(fx.speed_ms, 4.5, 1e-3);
  CHECK(fp::nmea_parse(gga, fx) && fx.quality == 1 && fx.sats == 12);
  NEAR(fx.hdop, 0.8, 1e-5);
  char bad[120];
  strcpy(bad, rmc);
  bad[20] = (bad[20] == '1') ? '2' : '1';
  CHECK(!fp::nmea_checksum_ok(bad) && !fp::nmea_parse(bad, fx));
  char south[120];
  nmea_with_checksum("$GPRMC,000000.00,A,2330.0000,S,04630.0000,W,0.0,0.0,281026,,,A", south);
  fp::Fix fs;
  CHECK(fp::nmea_parse(south, fs));
  NEAR(fs.lat, -23.5, 1e-9);
  NEAR(fs.lon, -46.5, 1e-9);
  char nofix[120];
  nmea_with_checksum("$GPRMC,000000.00,V,,,,,,,281026,,,N", nofix);
  fp::Fix fn;
  CHECK(fp::nmea_parse(nofix, fn) && !fn.valid && isnan(fn.lat));

  // --- UBX rate message: the stored checksum is right
  uint8_t a, b;
  fp::ubx_checksum(fp::UBX_RATE_5HZ, sizeof(fp::UBX_RATE_5HZ) - 2, a, b);
  CHECK(a == fp::UBX_RATE_5HZ[12] && b == fp::UBX_RATE_5HZ[13]);

  // --- SHT31: datasheet CRC example 0xBEEF -> 0x92
  uint8_t ex[2] = {0xBE, 0xEF};
  CHECK(fp::sht31_crc(ex, 2) == 0x92);
  uint8_t m[6] = {0x66, 0x66, 0, 0x80, 0x00, 0};
  m[2] = fp::sht31_crc(m, 2);
  m[5] = fp::sht31_crc(m + 3, 2);
  float t, rh;
  CHECK(fp::sht31_decode(m, t, rh));
  NEAR(t, -45 + 175 * 0x6666 / 65535.0, 1e-3);
  NEAR(rh, 100 * 0x8000 / 65535.0, 1e-3);
  m[5] ^= 1;
  CHECK(!fp::sht31_decode(m, t, rh));

  if (fails) { printf("%d failed\n", fails); return 1; }
  printf("all firmware logic tests passed\n");
  return 0;
}
