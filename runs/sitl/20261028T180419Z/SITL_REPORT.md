# The T1 on ArduPilot firmware (SITL) · 20261028T180419Z

**SIMULATED (ArduCopter firmware in SITL, T1 physics model).** Unmodified ArduCopter (Copter-4.5.7) flew FieldPilot's sorties on a physics model of the T1. Every number is the firmware's own log, read by FieldPilot. Not a field flight.

- Field: FieldPilot 1 ha job (Dankaur) · zones A1, A2, B1, B2 · T1 10 L quad (project airframe)
- Site: wind 1.6 m/s from 290.0°, turbulence 0.4, GPS noise 0.5 m
- Drift compensation: lines moved 1.6 m upwind

| Sortie | Mass kg | Flight s (plan) | Take-off+transit / spraying / return+land s | Energy Wh (plan) | Pack left | Min volts | Litres (plan) | Spray speed | Line error, true median / p95 | Deposited on target |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 17.47 | 368.6 (380.3) | 18.4 / 300.8 / 49.0 | 204.2 (202.5) | 73.3 % | 46.22 | 6.848 (6.948) | 3.8 m/s | 0.03 / 0.12 m | 99.0 % |
| 2 | 17.47 | 374.8 (374.1) | 26.4 / 301.5 / 46.4 | 207.6 (202.2) | 72.8 % | 46.18 | 6.853 (6.948) | 3.8 m/s | 0.03 / 0.09 m | 99.6 % |
| 3 | 17.45 | 350.6 (348.2) | 23.2 / 291.9 / 35.0 | 193.8 (191.9) | 74.7 % | 46.37 | 6.797 (6.903) | 3.79 m/s | 0.03 / 0.09 m | 99.0 % |

## FieldPilot's ArduPilot timing model against the firmware

Predicted from the parameters the aircraft flies with (S-curves at WPNAV_ACCEL and WPNAV_JERK, RTL_ALT, RTL_LOIT_TIME, LAND_SPEED; `fieldpilot/ap_timing.py`), not fitted to these logs.

| Sortie | Take-off+transit s (model) | Spraying s (model) | Return+land s (model) | Total s (model) | Energy Wh (model) |
|---|---|---|---|---|---|
| 1 | 18.4 (14.9) | 300.8 (288.6) | 49.0 (49.9) | 368.6 (353.4) | 204.2 (184.1) |
| 2 | 26.4 (23.0) | 301.5 (288.8) | 46.4 (47.3) | 374.8 (359.1) | 207.6 (187.1) |
| 3 | 23.2 (20.2) | 291.9 (277.8) | 35.0 (35.5) | 350.6 (333.6) | 193.8 (173.5) |

## The whole job (coverage is a job property: each sortie sprays only part of it)

- Flight time 1094.0 s (plan 1102.6 s); energy 605.6 Wh (plan 596.6 Wh); litres 20.498
- ArduPilot timing model: 1046.1 s, 544.7 Wh
- Zone coverage under the swath, after drift: lowest **97.9 %** (A1 98.2 %, A2 97.9 %, B1 98.1 %, B2 98.4 %)
- Deposited per zone (planned): A1 4.981 L (5.0, -0.4 %), A2 4.955 L (5.0, -0.9 %), B1 4.957 L (5.0, -0.9 %), B2 4.951 L (5.0, -1.0 %)

## Assumptions

- **mass**: each sortie at its mean mass (dry 10 + pack 4 + half its load); hover throttle from the T1 thrust curve, reference current x mass^1.5
- **deposit**: true position (SITL), moved downwind by fieldpilot/drift.py; 1.5 %/(m/s) airborne
- **pump**: sprayer servo 1100-1900 us = 0-3.5 L/min, lag 0.25 s, dead time 0.15 s
- **frame**: sim/t1_frame.json; tuning from ArduPilot's Callisto heavy-lift model
