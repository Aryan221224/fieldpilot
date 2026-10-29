# Field day · 2026-10-30 · Village spray operator · Dankaur (demo)

**SIMULATED field day (production code; simulated world, K3-A Pro twin, emulated devices).** Every number below comes from FieldPilot's production code running a day in a simulated world. Nothing here is a field measurement; it is what the real day should look like, and the yardstick for the real one.

- Aircraft: T1 10 L quad (project airframe) · controller: JIYI K3-A Pro (behavioural twin, fieldpilot/k3sim.py)
- Rules: IN-SOP-2023 · weather: SYNTHETIC climatological day (late Oct, western UP) - Open-Meteo not reachable
- Bookings read: 7 schedulable, 0 need a call-back (REHEARSAL (mock Token Factory, not Nebius))

## The day

| | |
|---|---|
| Farms planned / completed | 4 / 3 |
| Abandoned on site (mast NO-GO > 30 min) | J-05 |
| Sorties flown (day plan's estimate) | 6 (8) |
| Start delay vs plan, mean / max | 19.8 / 30.0 min |
| Flight time vs mission plan | -3.5 % |
| Mast holds before take-off | 15 (NO-GO in 52 of 186 on-site minutes) |
| Litres planned / actually sprayed / measured by SprayLog | 47.57 / 38.84 / 38.63 L |
| SprayLog vs truth | -0.52 % (meter calibration -0.51 %) |
| On target by the logger's GPS (lowest farm) | 94.2 % |
| Actually deposited on target, after drift (lowest farm) | 95.1 % (deposit shifted [0.74, 2.8] m downwind) |
| Zone dose deviation, measured | [-13.9, 5.5] % |
| Zone coverage under the swath (lowest) | 94.1 % |
| K3 breakpoint vs planned cut (worst) | 1.51 m |

## Sorties

| Farm | # | Planned | Actual | Held | Battery set | K3 end | Flight s (plan) | Load L | Sprayed L | Left L | Pack left |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Ramesh | 1 | 06:15 | 06:17 | 0 min | 1 | route_complete | 368 (386) | 6.39 | 6.08 | 0.31 | 72.2 % |
| Vikram | 1 | 06:57 | 07:15 | 0 min | 2 | low_liquid | 415 (420) | 6.84 | 6.82 | 0.02 | 68.2 % |
| Vikram | 2 | 07:05 | 07:26 | 0 min | 1 | low_liquid | 419 (453) | 6.84 | 6.83 | 0.01 | 68.3 % |
| Vikram | 3 | 07:50 | 08:08 | 0 min | 2 | route_complete | 408 (408) | 7.1 | 6.80 | 0.30 | 67.7 % |
| Kamla Devi | 1 | 09:10 | 09:40 | 0 min | 1 | low_liquid | 453 (465) | 6.18 | 6.17 | 0.01 | 66.9 % |
| Kamla Devi | 2 | 09:20 | 09:50 | 0 min | 2 | route_complete | 407 (428) | 6.44 | 6.14 | 0.30 | 68.8 % |

## As applied, per farm (SprayLog through FieldPilot)

| Farm | Zones | Planned L | Measured L | On target | Outside field | Flags |
|---|---|---|---|---|---|---|
| Ramesh | A3, B2, B3 | 6.106 | 6.044 | 94.2 % | 0.0 L | none |
| Vikram | A1, A2, A3, A4, B1, B2, B3, B4 | 20.486 | 20.345 | 99.1 % | 0.0 L | none |
| Kamla Devi | A1, A2, A3, A4, B1, B2, B3, B4 | 12.34 | 12.245 | 98.0 % | 0.0 L | none |
| Imran | A1, B3 | 8.637 | - | - % | - L | none |

## Drift compensation (lines moved upwind by the measured wind)

| Farm | Offset | Deposited on target, with / without | Lowest zone coverage, with / without | Litres landing off the zones, with / without |
|---|---|---|---|---|
| Ramesh | 0.65 m | 98.1 % / 97.1 % | 98.3 % / 96.6 % | 0.113 / 0.177 L |
| Vikram | 0.97 m | 97.3 % / 96.7 % | 94.1 % / 93.3 % | 0.555 / 0.684 L |
| Kamla Devi | 2.54 m | 95.1 % / 93.2 % | 96.2 % / 86.8 % | 0.599 / 0.835 L |

## Mast holds

- J-05 11:17 sortie 1: RH 42 % < 50 %
- J-05 11:19 sortie 1: RH 42 % < 50 %
- J-05 11:21 sortie 1: RH 42 % < 50 %
- J-05 11:23 sortie 1: RH 42 % < 50 %
- J-05 11:25 sortie 1: RH 41 % < 50 %
- J-05 11:27 sortie 1: RH 41 % < 50 %
- J-05 11:29 sortie 1: RH 40 % < 50 %
- J-05 11:31 sortie 1: RH 41 % < 50 %
- J-05 11:33 sortie 1: RH 40 % < 50 %
- J-05 11:35 sortie 1: RH 41 % < 50 %
- J-05 11:37 sortie 1: RH 40 % < 50 %
- J-05 11:39 sortie 1: RH 39 % < 50 %
- J-05 11:41 sortie 1: RH 39 % < 50 %
- J-05 11:43 sortie 1: RH 40 % < 50 %
- J-05 11:45 sortie 1: RH 39 % < 50 %

## Token Factory

- Endpoint: **REHEARSAL (mock Token Factory, not Nebius)**
- Models discovered: agent `REHEARSAL-mock/nemotron-3-super-120b-a12b`, vision `REHEARSAL-mock/Nemotron-3.5-Super-VL`, bookings and brief `REHEARSAL-mock/Nemotron-3_5-Lightning`
- Bookings read: 7 (7 schema-constrained), 7 areas accepted by the number check, 3 flags for the operator's call
- Calls today: 8 ({'intake': 7, 'brief': 1}), 2,271 + 708 tokens, about **$0.00031** at each model's list price
- Crew brief (REHEARSAL-mock/Nemotron-3_5-Lightning): आज 4 खेत, कुल 2.47 हेक्टेयर, 8 उड़ान। Dankaur में Ramesh जी का खेत, पहली उड़ान 06:15 बजे। Atta में Vikram जी का खेत, पहली उड़ान 06:57 बजे। Dadri में Kamla Devi जी का खेत, पहली उड़ान 09:10 बजे। Bilaspur में Imran जी का खेत, पहली उड़ान 10:28 बजे। बैटरी सेट 1 को 06:20 बजे चार्जिंग पर लगाएं, 07:05 बजे तैयार। सुनीता जी को फ़ोन करें: अगला सही समय Sat 31 Oct 06:00। Mahesh जी को फ़ोन करें: अगला सही समय Sa

## The plan against the common way (same bookings, forecast, batteries)

| Metric | FieldPilot | Common way |
|---|---|---|
| farms_served_in_window | 4 | 3 |
| effective_ha_per_battery_cycle | 0.309 | 0.084 |
| out_of_window_sorties | 0 | 8 |
| sprayed_beyond_need_ha | 0.0 | 2.39 |
| leftover_mix_l | 1.2 | 39.4 |

## Assumptions

- **site_air**: forecast + per-farm bias (wind σ 0.35 m/s, temp σ 0.8 °C, RH σ 4 %) + slow drift + 22 % turbulence; wind from ~300° (NW) ± 25° per farm
- **mast_sensors**: anemometer bias σ 0.25 m/s + 0.1 m/s noise (sheet worst case ±1 m/s); SHT31 bias σ 0.2 °C / 1.5 % RH
- **drift**: deposit shifted downwind by release height x wind / 3 m/s (downwash); 1.5 % per m/s stays airborne
- **crew**: drive x 0.9-1.25, setup 15 ± 4 min, refill and swap 2-6 min (3 planned)
- **spraylog**: u-blox M8N + GAGAN: GPS bias random walk σ≈1.2 m + 0.5 m jitter; flow meter jug-calibrated to -0.5 % with 3 % reading noise
- **charging**: 45 min ± 3 min per pack, one charger queue
- **k3**: see fieldpilot/k3sim.py K3Params
