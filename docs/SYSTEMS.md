# Every system FieldPilot runs on

FieldPilot is software, but it only works as a whole system in the field. This page lists every
piece, what runs on it, how it is wired and powered, and its status. **Built** means the code is
in this repository and tested. **Not built physically** means the firmware exists and is
tested off the hardware, but nobody has soldered and flown it yet.

```
                       4G (98 % of Indian villages, Feb 2026)
   Nebius Token Factory ─┐       Tavily ─┐       Open-Meteo ─┐
                         └───────────────┴──────────────────┴── FieldPilot server
                                                                (laptop in the vehicle, or a cloud VM)
                                                                    │ HTTP over the phone's hotspot
          ┌──────────────────────────┬──────────────────────────────┼───────────────────────┐
   operator's phone            FieldMast (ESP32)               SprayLog (ESP32)       pilot's K3-A Pro remote
   /desk  /ops  /  (browser)   wind · temp · RH at the field   on the drone: flow,    JIYI app: zones, spacing,
                               posts every 10 s                GPS, litres per sortie speed, dose from the
                                                               uploads after landing  FieldPilot flight card
                                                                                           │ radio
                                                                                     T1 10 L quad
                                                                                     JIYI K3-A Pro
```

| # | System | Hardware | What runs on it | Status |
|---|---|---|---|---|
| 1 | **FieldPilot server** | the operator's laptop (Windows/Linux, any 4 GB machine) or a small cloud VM | `python -m uvicorn fieldpilot.api:app`: day planner, field agent, rules, crop twin, sortie planner, flight cards, paperwork, device hub | built, 194 tests |
| 2 | **Operator's phone** | any Android phone with Chrome | `/desk` (the day), `/` (one farm), `/ops` (devices, batteries, as applied) | built |
| 3 | **Pilot's ground station** | the K3-A Pro's handheld remote with the JIYI app (the remote with a screen in our field video) | the pilot types in each card's zones, line spacing, speed, height and dose (L/mu), and loads the litres on the card | not ours; FieldPilot writes the card + KML |
| 4 | **Aircraft** | T1 10 L quad (BOM ₹2.02 lakh as recorded), JIYI K3-A Pro, 4 nozzles on a 6 mm line | the controller flies the card; FieldPilot's K3-A Pro twin (`fieldpilot/k3sim.py`) predicts what it will do | aircraft exists; controller closed |
| 4b | **Aircraft, open path** | the same T1 with a Pixhawk-class controller on unmodified ArduCopter | FieldPilot uploads each sortie over MAVLink; DO_SPRAYER per pass; flow follows ground speed | **flown in ArduCopter SITL on a T1 model**, see [FIRMWARE.md](FIRMWARE.md) |
| 5 | **FieldMast** | ESP32 + cup anemometer + SHT31, on a 2 m pole at the field edge | `firmware/fieldmast`: GO / NO-GO LEDs for the rule pack, posts to the server; the server blocks approval on a NO-GO | firmware built and tested off-hardware; **not built physically** |
| 6 | **SprayLog** | ESP32 + flow meter in the spray line + u-blox GPS + microSD, on the drone | `firmware/spraylog`: per-sortie flow, litres and position; uploads after landing; the server computes litres per zone, L/ha and off-target spray | firmware built and tested off-hardware; **not built physically** |
| 7 | **Bench pump bridge** | Arduino + IRLZ44N + 24 V supply, driving the T1's own pump | `firmware/pump_bridge`: the console's simulated flight drives the real pump (the hardware clip) | built; see [BENCH_RIG.md](BENCH_RIG.md) |
| 8 | **Cloud services** | Nebius Token Factory, Tavily, Open-Meteo | Nemotron (agent, vision, bookings + brief), registry checks, hourly forecasts | live with keys; see [MODELS.md](MODELS.md) |

## Why these two devices

- **FieldMast.** The SOP's limits (wind < 3 m/s, ≤ 35 °C, RH ≥ 50 %) describe the air over the
  field at the moment of spraying. A forecast describes a grid cell at the top of the hour. In
  the simulated field day the measured 2-minute wind ran from 1.4 m/s below to 0.6 m/s above the
  forecast. One farm was held at the mast for 30 minutes and then abandoned, after the morning's
  delays pushed it past the window and humidity fell below 50 %
  ([REHEARSAL.md](REHEARSAL.md#the-simulated-field-day)). The mast turns "the forecast said it
  was fine" into "it was measured to be fine at take-off".
- **SprayLog.** The K3-A Pro shares no telemetry, so today nothing records how much was
  sprayed, or where. SprayLog gives the SOP spray record real numbers: litres per zone, L/ha,
  and any spray outside the treated zones or the field. It also checks FieldPilot itself: did
  the tank really run dry where the plan cut the route?

## FieldMast: parts, wiring, power

| Part | Notes | Price |
|---|---|---|
| ESP32 DevKitC (WROOM-32) | any ESP32 dev board with ADC1 pins free | ~₹500 (estimate) |
| Cup anemometer, analog 0.4–2.0 V (Adafruit 1733 type, 0–32.4 m/s, 7–24 V supply) | or a pulse head (set `ANEMO_PULSE 1`) | US$44.95 (Adafruit) |
| SHT31 temperature/RH breakout, in a radiation shield | 1.5–2 m above ground, shaded, ventilated | ~₹500 (estimate) |
| 12 V 7 Ah sealed battery + 12→5 V buck | runs the mast through a full working day | ~₹1,500 (estimate) |
| 3 LEDs (green, red, amber) + 330 Ω | readable without a phone | ~₹30 |
| 2 m pole or tripod, IP65 box | | ~₹1,200 (estimate) |

| From | To |
|---|---|
| Anemometer power (brown) / ground (black) | 12 V / GND |
| Anemometer signal (blue) | GPIO 34 (ADC1; max 2.0 V, safe for the ESP32) |
| SHT31 VIN / GND / SDA / SCL | 3V3 / GND / GPIO 21 / GPIO 22 |
| 12 V through a 47 k / 10 k divider | GPIO 35 (supply voltage on the dashboard) |
| LEDs GO / NO-GO / WARN through 330 Ω | GPIO 25 / 26 / 27 |
| Buck 5 V out | ESP32 5V pin |

## SprayLog: parts, wiring, power

| Part | Notes | Price |
|---|---|---|
| ESP32 DevKitC | | ~₹500 (estimate) |
| Drone spray flowmeter, NPN pulse, 0.2–6 L/min, 8 mm hose (JIYI type) | in the 6 mm line after the pump, before the first T-fitting, with 6→8 mm adapters. The YF-S401 (0.3–6 L/min, 5880 pulses/L) also works, but its 4 mm bore restricts the line | ₹1,715 (Robu, JIYI flowmeter) |
| u-blox NEO-M8N GPS | antenna on top, at least 10 cm from the K3-A Pro's GNSS mast | ~₹1,500 (estimate) |
| microSD module + card | keeps every sortie as CSV even when the upload fails | ~₹150 (estimate) |
| Wide-input buck, ≥ 60 V in → 5 V 2 A, with a 1 A fuse | from the flight-battery bus (12S is up to 50.4 V) through an XT60 tap | ~₹400 (estimate) |
| Small IP54 box, under the canopy | aim for under 120 g all-up | ~₹300 (estimate) |

| From | To |
|---|---|
| Flowmeter signal (NPN) | GPIO 27, with 4.7 k to 3V3 |
| Flowmeter V+ / GND | 5 V / GND |
| GPS TX / RX | GPIO 16 / GPIO 17 (the sketch sets 5 Hz at boot) |
| microSD CS / SCK / MISO / MOSI | GPIO 5 / 18 / 19 / 23 |
| Buck 5 V | ESP32 5V, GPS, microSD, flowmeter |

**SprayLog never connects to the K3-A Pro, the ESCs or the pump driver.** It only taps power
through its own fuse and measures. If the K3-A Pro already has a JIYI flowmeter, keep SprayLog's
meter separate rather than sharing the controller's signal line.

## Flashing and calibration

1. Arduino IDE 2 (or `arduino-cli`), board package **esp32 by Espressif** 3.x, board
   **ESP32 Dev Module**. No other libraries are needed.
2. Edit `config.h` in the sketch folder: Wi-Fi (the operator's hotspot), `FP_SERVER` (the
   laptop's address on the hotspot, e.g. `http://192.168.43.100:8080`), device id, rule pack.
   If you set `FP_DEVICE_TOKEN` on the server, put the same value in `FP_TOKEN`.
3. Upload. Open the serial monitor at 115200.
4. **SprayLog K-factor:** send `c`, run the pump into a measuring jug for 30 s, read the pulse
   count, and set `K_PULSES_PER_L = pulses / litres`. This is the same jug test that calibrates
   the pump model (`cli deviation --jug-full …`).
5. **FieldMast:** hold a handheld anemometer next to the cups for two minutes and compare the
   2-minute means. The server always applies the rule pack itself and ignores the LEDs'
   verdict.
6. Open `/ops`: both devices appear under **Devices** within a minute.

Without hardware, `python -m fieldpilot.cli devsim mast --wind 3.4` posts what a mast would
post, labelled SIMULATED, so the approval gate and the dashboard can be exercised.

## How it is tested before anyone solders

- `firmware/common/fieldpilot_logic.h` holds the logic both sketches share: rule limits, Delta T,
  the wind average and gust, flow from pulses, spray and sortie detection, the NMEA parser and
  the SHT31 CRC. It is plain C++ and unit-tested on the PC (`tests/firmware/test_logic.cpp`). Its
  Delta T agrees with `fieldpilot/rules.py` to 0.01 at every point of a 10–45 °C × 10–100 % grid.
- Both sketches compile with all warnings as errors against stand-ins for the Arduino-ESP32 API
  (`tests/firmware/arduino_stub`). That checks our code's syntax and types. It is not a build for
  the ESP32: flashing with the Espressif core is the next step.
- The server side (`fieldpilot/devices.py`) is tested end to end through the API. A mast NO-GO
  blocks approval even with a written override. A SprayLog upload attaches to the dispatched run
  and becomes the as-applied report.
- The whole chain runs in the simulated field day (`cli fieldday`). The emulated devices post
  exactly what the firmware posts.
