# Field day · 2026-10-31 · Village spray operator · Dankaur (demo)

**SIMULATED field day (production code; simulated world, K3-A Pro twin, emulated devices).** Every number below comes from FieldPilot's production code running a day in a simulated world. Nothing here is a field measurement; it is what the real day should look like, and the yardstick for the real one.

- Aircraft: T1 10 L quad (project airframe) · controller: JIYI K3-A Pro (behavioural twin, fieldpilot/k3sim.py)
- Rules: IN-SOP-2023 · weather: open-meteo hourly forecast
- Bookings read: 7 schedulable, 0 need a call-back (LIVE Nemotron on Nebius Token Factory)

## The day

| | |
|---|---|
| Farms planned / completed | 5 / 4 |
| Abandoned on site (mast NO-GO > 30 min) | J-05 |
| Sorties flown (day plan's estimate) | 8 (10) |
| Start delay vs plan, mean / max | 15.5 / 30.0 min |
| Flight time vs mission plan | -4.0 % |
| Mast holds before take-off | 15 (NO-GO in 137 of 451 on-site minutes) |
| Litres planned / actually sprayed / measured by SprayLog | 61.96 / 53.15 / 52.88 L |
| SprayLog vs truth | -0.52 % (meter calibration -0.51 %) |
| On target by the logger's GPS (lowest farm) | 94.2 % |
| Actually deposited on target, after drift (lowest farm) | 95.5 % (deposit shifted [1.39, 2.86] m downwind) |
| Zone dose deviation, measured | [-13.8, 19.1] % |
| Zone coverage under the swath (lowest) | 95.6 % |
| K3 breakpoint vs planned cut (worst) | 3.62 m |

## Sorties

| Farm | # | Planned | Actual | Held | Battery set | K3 end | Flight s (plan) | Load L | Sprayed L | Left L | Pack left |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Ramesh | 1 | 06:15 | 06:17 | 0 min | 1 | route_complete | 369 (386) | 6.39 | 6.08 | 0.31 | 72.2 % |
| Vikram | 1 | 06:57 | 07:15 | 0 min | 2 | low_liquid | 405 (420) | 6.84 | 6.82 | 0.02 | 69.1 % |
| Vikram | 2 | 07:05 | 07:26 | 0 min | 1 | low_liquid | 437 (453) | 6.84 | 6.83 | 0.01 | 66.7 % |
| Vikram | 3 | 07:50 | 08:08 | 0 min | 2 | route_complete | 396 (408) | 7.1 | 6.80 | 0.30 | 68.7 % |
| Kamla Devi | 1 | 09:10 | 09:40 | 0 min | 1 | low_liquid | 453 (465) | 6.18 | 6.17 | 0.01 | 66.9 % |
| Kamla Devi | 2 | 09:20 | 09:50 | 0 min | 2 | route_complete | 407 (428) | 6.44 | 6.14 | 0.30 | 68.8 % |
| गीता | 1 | 16:30 | 16:30 | 0 min | 1 | low_liquid | 472 (497) | 7.21 | 7.20 | 0.01 | 63.7 % |
| गीता | 2 | 16:37 | 16:42 | 0 min | 2 | route_complete | 448 (470) | 7.5 | 7.12 | 0.38 | 64.3 % |

## As applied, per farm (SprayLog through FieldPilot)

| Farm | Zones | Planned L | Measured L | On target | Outside field | Flags |
|---|---|---|---|---|---|---|
| Ramesh | A3, B2, B3 | 6.106 | 6.044 | 94.2 % | 0.0 L | none |
| Vikram | A1, A2, A3, A4, B1, B2, B3, B4 | 20.486 | 20.345 | 99.2 % | 0.0 L | none |
| Kamla Devi | A1, A2, A3, A4, B1, B2, B3, B4 | 12.34 | 12.245 | 98.0 % | 0.0 L | none |
| Imran | A1, B3 | 8.637 | - | - % | - L | none |
| गीता | A1, A2, A3, A4, B1, B2, B3, B4 | 14.391 | 14.245 | 97.6 % | 0.0 L | zone A3: 1.87 L applied vs 1.58 L planned (+19 %); zone B4: 1.88 L applied vs 1.58 L planned (+19 %) |

## Drift compensation (lines moved upwind by the measured wind)

| Farm | Offset | Deposited on target, with / without | Lowest zone coverage, with / without | Litres landing off the zones, with / without |
|---|---|---|---|---|
| Ramesh | 1.24 m | 97.2 % / 95.4 % | 96.4 % / 93.4 % | 0.172 / 0.278 L |
| Vikram | 1.95 m | 96.8 % / 95.5 % | 95.6 % / 92.8 % | 0.645 / 0.928 L |
| Kamla Devi | 2.57 m | 95.5 % / 93.5 % | 96.4 % / 85.3 % | 0.555 / 0.803 L |
| गीता | 1.54 m | 96.9 % / 96.1 % | 96.8 % / 90.3 % | 0.437 / 0.561 L |

## Mast holds

- J-05 11:16 sortie 1: RH 48 % < 50 %
- J-05 11:18 sortie 1: RH 48 % < 50 %
- J-05 11:20 sortie 1: RH 48 % < 50 %
- J-05 11:22 sortie 1: RH 47 % < 50 %
- J-05 11:24 sortie 1: RH 47 % < 50 %
- J-05 11:26 sortie 1: RH 47 % < 50 %
- J-05 11:28 sortie 1: RH 47 % < 50 %
- J-05 11:30 sortie 1: RH 46 % < 50 %
- J-05 11:32 sortie 1: RH 46 % < 50 %
- J-05 11:34 sortie 1: RH 46 % < 50 %
- J-05 11:36 sortie 1: RH 46 % < 50 %
- J-05 11:38 sortie 1: RH 46 % < 50 %
- J-05 11:40 sortie 1: RH 46 % < 50 %
- J-05 11:42 sortie 1: RH 46 % < 50 %
- J-05 11:44 sortie 1: RH 45 % < 50 %

## Token Factory

- Endpoint: **LIVE Nemotron on Nebius Token Factory**
- Models discovered: agent `nvidia/nemotron-3-super-120b-a12b`, vision `nvidia/Nemotron-3.5-Super-VL`, bookings and brief `nvidia/Nemotron-3_5-Lightning`
- Bookings read: 7 (7 schema-constrained), 7 areas accepted by the number check, 4 flags for the operator's call
- Calls today: 8 ({'intake': 7, 'brief': 1}), 3,422 + 916 tokens, about **$0.00043** at each model's list price
- Crew brief (nvidia/Nemotron-3_5-Lightning): ड्रोन ऑपरेटर के लिए सुबह की आवाज़-नोट:
आज 5 खेतों में स्प्रे किया गया। क्रम: 1) रamesh, धनकौर 06:00-06:20, 2) विक्रम, अट्टा गाँव 06:42-07:54, 3) कमला देवी, दादरी 08:55-09:24, 4) इमरान 10:13-10:53, 5) गीता, झाझर 16:15-16:41। बैटरी 1 दोपहर 06:20 और 07:50 पर चार्जिंग के लिए जाएगी, बैटरी 2 शाम 07:05 और 08:35 पर। सुनीता और महेश को कल के लिए फ़ोन करें, आज उनका काम नहीं हो पाएगा। हवा तेज़ न हो और गर्मी क

## The plan against the common way (same bookings, forecast, batteries)

| Metric | FieldPilot | Common way |
|---|---|---|
| farms_served_in_window | 5 | 4 |
| effective_ha_per_battery_cycle | 0.31 | 0.126 |
| out_of_window_sorties | 0 | 6 |
| sprayed_beyond_need_ha | 0.0 | 2.39 |
| leftover_mix_l | 1.5 | 39.4 |

## Assumptions

- **site_air**: forecast + per-farm bias (wind σ 0.35 m/s, temp σ 0.8 °C, RH σ 4 %) + slow drift + 22 % turbulence; wind from ~300° (NW) ± 25° per farm
- **mast_sensors**: anemometer bias σ 0.25 m/s + 0.1 m/s noise (sheet worst case ±1 m/s); SHT31 bias σ 0.2 °C / 1.5 % RH
- **drift**: deposit shifted downwind by release height x wind / 3 m/s (downwash); 1.5 % per m/s stays airborne
- **crew**: drive x 0.9-1.25, setup 15 ± 4 min, refill and swap 2-6 min (3 planned)
- **spraylog**: u-blox M8N + GAGAN: GPS bias random walk σ≈1.2 m + 0.5 m jitter; flow meter jug-calibrated to -0.5 % with 3 % reading noise
- **charging**: 45 min ± 3 min per pack, one charger queue
- **k3**: see fieldpilot/k3sim.py K3Params
