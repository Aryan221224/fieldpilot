# Firmware: what flies the T1, and what FieldPilot's own firmware does

## Who flies the aircraft

**The flight controller flies the aircraft, never FieldPilot's own code.** Attitude,
stabilisation, navigation and failsafes on a 24 kg machine with 34.7-inch propellers belong to
a mature, tested autopilot. FieldPilot plans, checks, gates and records; it does not stabilise
anything.

| Option | Firmware that drives the whole drone | How FieldPilot drives it | Status |
|---|---|---|---|
| **Today: JIYI K3-A Pro** (in our T1) | JIYI's closed firmware | flight card + KML per sortie for the JIYI app; the K3-A Pro twin predicts what it does ([K3_TWIN.md](K3_TWIN.md)) | flown by the pilot |
| **Open path: ArduPilot (ArduCopter)** on a Pixhawk-class controller | open-source ArduCopter, unmodified | MAVLink: per-sortie upload, DO_SPRAYER per pass, arm/start, RTL abort (`fieldpilot/drone/mavlink.py`) | **flown in SITL on a T1 model**, below |
| PX4 | open-source PX4 | the same MAVLink path (DO_SET_SERVO for the pump) | tested against a fake vehicle |

## The T1 on ArduPilot, in simulation (SITL)

`python -m fieldpilot.cli sitl --ardupilot <ardupilot tree>` runs unmodified ArduCopter (built
from the `Copter-4.5.7` tag) in its software-in-the-loop simulator. FieldPilot then flies its
own missions through it.

- **The aircraft** (`sim/t1_frame.json`): 1.30 m diagonal, 4 × 34.7-inch propellers (2.44 m² of
  disc), a 12S 16 Ah pack (50.4 V full, 0.024 Ω), hover at 24 kg from the airframe model
  (3170 W cruise). This is cross-checked against ArduPilot's own Callisto model, a 12S heavy-lift
  quad of the same size: the T1 model's hover draw at 24 kg is within 4 % of what the Callisto's
  measured figures give. Each sortie flies at its own mean mass (dry 10 kg + pack 4 kg + half its
  load). Hover throttle, reference current (∝ mass^1.5) and drag are scaled so the motors and the
  airframe stay the same.
- **The parameters** (`sim/t1_sitl.parm`): attitude tuning starts from the Callisto. Frame,
  motors and battery are the T1's. The sprayer's flow follows ground speed (SPRAY_PUMP_RATE 13.7 %
  per m/s gives 20 L/ha at a 4 m swath) and stops below 0.5 m/s, the T1's own law. The battery
  failsafes return or land; geofence and RC-loss failsafe are on. Every value that FieldPilot
  derives in `airframe.ardupilot_params()` is checked against this file by a test.
- **The site:** wind and turbulence at spray height, GPS noise, and the pack's internal
  resistance.
- **What is measured:** everything comes from the firmware's own dataflash log:
  - time per phase (take-off and transit, spraying, return and land);
  - energy, from the firmware's battery integration, and the minimum pack voltage;
  - the sprayer output (servo 9), mapped to the T1's 3.5 L/min with the pump's lag;
  - line tracking against the planned passes, using the simulator's **true** position;
  - spray deposited per zone after drift, and coverage under the swath.

Results: [SITL results](#sitl-results) below. Each run's full report is
`runs/sitl/<time>/SITL_REPORT.md`.

## FieldPilot's own firmware

None of FieldPilot's own firmware flies the aircraft:

| Firmware | Board | Job |
|---|---|---|
| `firmware/pump_bridge` | Arduino | drives the T1's own pump on the bench, in step with the simulated flight (the hardware clip) |
| `firmware/fieldmast` | ESP32 | measures wind, temperature and humidity at the field; GO / NO-GO for the rule pack; feeds the approval gate and drift compensation |
| `firmware/spraylog` | ESP32 | passive flow + GPS logger on the drone; per-sortie as-applied record |

These three share one logic header that is unit-tested on a PC ([SYSTEMS.md](SYSTEMS.md)).

## SITL results

**SIMULATED: unmodified ArduCopter Copter-4.5.7 in SITL, on the T1 physics model. Not a field
flight.** Site: wind 1.6 m/s from 290°, turbulence 0.4, GPS noise 0.5 m. Every number comes from
the firmware's own dataflash log. Four runs are in `runs/sitl/`:

| Run | Job | Drift compensation |
|---|---|---|
| `20261028T175702Z` | 1 ha, 4 zones, 3 sorties | off |
| `20261028T180419Z` | 1 ha, 4 zones, 3 sorties | on (lines 1.6 m upwind) |
| `20261028T175951Z` | demo plot, 2 zones of 20 × 20 m, 1 sortie | off |
| `20261028T180822Z` | demo plot, 2 zones of 20 × 20 m, 1 sortie | on (lines 1.6 m upwind) |

### The 1 ha job: the firmware flies FieldPilot's plan

| | Plan | ArduCopter (SITL), compensation on | Compensation off |
|---|---|---|---|
| Flight time, 3 sorties | 1102.6 s | **1094.0 s** (−0.8 %) | 1094.4 s |
| Energy | 596.6 Wh | **605.6 Wh** (+1.5 %) | 605.8 Wh |
| Litres | 20.80 (zone areas: 20.0) | 20.50 | 20.48 |
| Pack left after each sortie | ≥ 25 % reserve | 73.3 / 72.8 / 74.7 % | the same |
| Lowest pack voltage | | 46.18 V (12S, 3.85 V/cell) | the same |
| True line error while spraying, median / p95 | | 0.03 / 0.09–0.12 m | 0.03 / 0.09–0.13 m |
| Deposited on target, per sortie | | **99.0 / 99.6 / 99.0 %** | 97.0 / 98.4 / 96.5 % |
| Lowest zone coverage under the swath | | **97.9 %** | 95.6 % |
| Zone dose against plan | | **−0.4 to −1.0 %** | −1.4 to −3.3 % |

Each sortie's phases (take-off and transit / spraying / return and land) were 18.4 / 300.8 /
49.0 s, 26.4 / 301.5 / 46.4 s and 23.2 / 291.9 / 35.0 s. The pump peaked at 2.18 L/min, which is
the T1's flow law at 4.5 m/s (4 m × 20 L/ha). The mean speed while spraying was 3.8 m/s, because
ArduCopter accelerates and brakes along S-curves at each end of a line.

### The demo plot: where the generic estimate breaks, and what fixed it

On 20 m lines the aircraft never reaches spray speed (mean 3.0 m/s), and the return is slow:
RTL climbs to 10 m, waits 5 s above home and lands at 0.5 m/s.

| | Plan | ArduCopter (SITL), compensation on | Compensation off |
|---|---|---|---|
| Flight time | 85.9 s | **150.4 s** (9.6 / 98.4 / 42.1) | 150.0 s |
| Energy | 38.1 Wh | 65.7 Wh | 65.5 Wh |
| Litres (plan 1.6) | | 1.539 | 1.542 |
| Deposited on target | | **98.2 %** | 91.6 % |
| Lowest zone coverage | | **92.8 %** | 88.8 % |
| Zone dose against plan | | **−7.8 %** | −13.5 to −14.1 % |

FieldPilot's generic estimate flies every leg at constant speed. That holds on long lines, but on
this plot it is 43 % short. So FieldPilot now has an **ArduPilot timing model**
(`fieldpilot/ap_timing.py`). It takes the parameters the aircraft flies with and follows
ArduCopter's S-curves (WPNAV_ACCEL, WPNAV_JERK) with overlapping corners, the RTL climb, the
RTL_LOIT_TIME wait and the LAND_SPEED descent. **None of it is fitted to these logs:**

| Run | ArduCopter | Timing model | Generic estimate |
|---|---|---|---|
| 1 ha job | 1094.0 s | 1046.1 s (−4.4 %) | 1102.6 s (+0.8 %) |
| Demo plot | 150.4 s | 139.6 s (−7.2 %) | 85.9 s (−43 %) |
| Return and land, every sortie | 35.0–49.0 s | within 1 s of the log | |

The model predicts the return and landing to within a second, and the whole flight to 4–7 %. It
still reads a little short, because ArduCopter does not overlap the corners fully. Its energy at
hover power reads 10–14 % low, because forward flight and the accelerations cost more than hover.
For energy, the planner's own estimate (+1.5 % on the 1 ha job) remains the one to use.

### What this proves, and what it does not

- **Proved, in simulation:**
  - unmodified open-source flight firmware flies FieldPilot's missions end to end: upload,
    DO_SPRAYER per pass, the T1 flow law, return to launch and landing;
  - the sorties land inside the planned battery reserve;
  - the drift compensation adds dose on target in the firmware's own flight, as it does in the
    K3-A Pro twin ([REHEARSAL.md](REHEARSAL.md)).
- **Not proved:**
  - **The 3 cm line error is the controller tracking its own position estimate.** SITL's GPS
    noise is white, so the EKF filters it away. A real M8N + GAGAN fix wanders by about a metre
    over minutes. That is the error the field-day simulation and SprayLog's flags are built for.
  - The T1 model is built from the spec sheet and ArduPilot's Callisto. Its hover draw and
    tuning need one real hover log to confirm.
  - Nothing here is the K3-A Pro. The K3-A Pro is closed firmware. FieldPilot drives it by flight
    card, and its behaviour is modelled by the twin ([K3_TWIN.md](K3_TWIN.md)).

Each run folder keeps its report, `sitl.json` and the parameter files each sortie flew with. The
dataflash logs (10–23 MB each) are not in the repository. Rerunning regenerates them.

Rerun: `python -m fieldpilot.cli sitl --ardupilot <tree> --field 1ha` (or `--field demo`,
`--no-drift-compensation`). `--reanalyse runs/sitl/<time>` reads a run's logs again without
flying.
