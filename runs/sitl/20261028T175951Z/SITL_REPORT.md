# The T1 on ArduPilot firmware (SITL) · 20261028T175951Z

**SIMULATED (ArduCopter firmware in SITL, T1 physics model).** Unmodified ArduCopter (Copter-4.5.7) flew FieldPilot's sorties on a physics model of the T1. Every number is the firmware's own log, read by FieldPilot. Not a field flight.

- Field: Bennett demo plot (REPLACE corners with your real plot) · zones A2, B2 · T1 10 L quad (project airframe)
- Site: wind 1.6 m/s from 290.0°, turbulence 0.4, GPS noise 0.5 m
- Drift compensation: off

| Sortie | Mass kg | Flight s (plan) | Take-off+transit / spraying / return+land s | Energy Wh (plan) | Pack left | Min volts | Litres (plan) | Spray speed | Line error, true median / p95 | Deposited on target |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 14.8 | 150.0 (85.6) | 9.2 / 98.3 / 42.1 | 65.5 (38.0) | 91.6 % | 48.54 | 1.542 (1.6) | 3.01 m/s | 0.06 / 0.17 m | 91.6 % |

## FieldPilot's ArduPilot timing model against the firmware

Predicted from the parameters the aircraft flies with (S-curves at WPNAV_ACCEL and WPNAV_JERK, RTL_ALT, RTL_LOIT_TIME, LAND_SPEED; `fieldpilot/ap_timing.py`), not fitted to these logs.

| Sortie | Take-off+transit s (model) | Spraying s (model) | Return+land s (model) | Total s (model) | Energy Wh (model) |
|---|---|---|---|---|---|
| 1 | 9.2 (6.8) | 98.3 (89.6) | 42.1 (43.0) | 150.0 (139.4) | 65.5 (56.6) |

## The whole job (coverage is a job property: each sortie sprays only part of it)

- Flight time 150.0 s (plan 85.6 s); energy 65.5 Wh (plan 38.0 Wh); litres 1.542
- ArduPilot timing model: 139.4 s, 56.6 Wh
- Zone coverage under the swath, after drift: lowest **88.8 %** (A2 89.9 %, B2 88.8 %)
- Deposited per zone (planned): A2 0.687 L (0.8, -14.1 %), B2 0.692 L (0.8, -13.5 %)

## Assumptions

- **mass**: each sortie at its mean mass (dry 10 + pack 4 + half its load); hover throttle from the T1 thrust curve, reference current x mass^1.5
- **deposit**: true position (SITL), moved downwind by fieldpilot/drift.py; 1.5 %/(m/s) airborne
- **pump**: sprayer servo 1100-1900 us = 0-3.5 L/min, lag 0.25 s, dead time 0.15 s
- **frame**: sim/t1_frame.json; tuning from ArduPilot's Callisto heavy-lift model
