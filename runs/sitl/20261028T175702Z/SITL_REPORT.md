# The T1 on ArduPilot firmware (SITL) · 20261028T175702Z

**SIMULATED (ArduCopter firmware in SITL, T1 physics model).** Unmodified ArduCopter (Copter-4.5.7) flew FieldPilot's sorties on a physics model of the T1. Every number is the firmware's own log, read by FieldPilot. Not a field flight.

- Field: FieldPilot 1 ha job (Dankaur) · zones A1, A2, B1, B2 · T1 10 L quad (project airframe)
- Site: wind 1.6 m/s from 290.0°, turbulence 0.4, GPS noise 0.5 m
- Drift compensation: off

| Sortie | Mass kg | Flight s (plan) | Take-off+transit / spraying / return+land s | Energy Wh (plan) | Pack left | Min volts | Litres (plan) | Spray speed | Line error, true median / p95 | Deposited on target |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 17.47 | 368.6 (380.5) | 18.7 / 300.5 / 49.0 | 204.2 (202.7) | 73.3 % | 46.22 | 6.834 (6.948) | 3.81 m/s | 0.03 / 0.13 m | 97.0 % |
| 2 | 17.47 | 375.0 (374.3) | 26.7 / 301.5 / 46.4 | 207.7 (202.3) | 72.8 % | 46.18 | 6.839 (6.948) | 3.81 m/s | 0.03 / 0.1 m | 98.4 % |
| 3 | 17.45 | 350.8 (348.3) | 23.1 / 292.2 / 35.0 | 193.9 (192.0) | 74.7 % | 46.37 | 6.803 (6.904) | 3.79 m/s | 0.03 / 0.09 m | 96.5 % |

## FieldPilot's ArduPilot timing model against the firmware

Predicted from the parameters the aircraft flies with (S-curves at WPNAV_ACCEL and WPNAV_JERK, RTL_ALT, RTL_LOIT_TIME, LAND_SPEED; `fieldpilot/ap_timing.py`), not fitted to these logs.

| Sortie | Take-off+transit s (model) | Spraying s (model) | Return+land s (model) | Total s (model) | Energy Wh (model) |
|---|---|---|---|---|---|
| 1 | 18.7 (15.0) | 300.5 (288.6) | 49.0 (50.1) | 368.6 (353.7) | 204.2 (184.3) |
| 2 | 26.7 (23.2) | 301.5 (288.8) | 46.4 (47.4) | 375.0 (359.4) | 207.7 (187.3) |
| 3 | 23.1 (20.3) | 292.2 (277.8) | 35.0 (35.6) | 350.8 (333.7) | 193.9 (173.5) |

## The whole job (coverage is a job property: each sortie sprays only part of it)

- Flight time 1094.4 s (plan 1103.1 s); energy 605.8 Wh (plan 597.0 Wh); litres 20.476
- ArduPilot timing model: 1046.8 s, 545.1 Wh
- Zone coverage under the swath, after drift: lowest **95.6 %** (A1 96.6 %, A2 98.6 %, B1 95.6 %, B2 97.8 %)
- Deposited per zone (planned): A1 4.845 L (5.0, -3.1 %), A2 4.929 L (5.0, -1.4 %), B1 4.845 L (5.0, -3.1 %), B2 4.833 L (5.0, -3.3 %)

## Assumptions

- **mass**: each sortie at its mean mass (dry 10 + pack 4 + half its load); hover throttle from the T1 thrust curve, reference current x mass^1.5
- **deposit**: true position (SITL), moved downwind by fieldpilot/drift.py; 1.5 %/(m/s) airborne
- **pump**: sprayer servo 1100-1900 us = 0-3.5 L/min, lag 0.25 s, dead time 0.15 s
- **frame**: sim/t1_frame.json; tuning from ArduPilot's Callisto heavy-lift model
