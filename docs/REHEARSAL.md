# Rehearsal: predict the whole run, then measure the real one against it

`python -m fieldpilot.cli rehearse` runs the entire FieldPilot pipeline end to end, with
nothing live except the code itself. It predicts what the real Token Factory run and the real
bench take will produce. After the real runs, `python -m fieldpilot.cli deviation` reports how
far reality landed from the prediction. Measuring that sim-to-real gap is how the pump model
and the plan get calibrated.

Rehearsal output is labelled **REHEARSAL (mock Token Factory, not Nebius)** everywhere: in the
console badge, every run file, the report and the doctor. It can never fill
`docs/SUBMISSION.md`, and `doctor --submission` fails on it. It is a prediction, not a result.

## What is real and what stands in

| Part | In a rehearsal | In the real run |
|---|---|---|
| Planner, sortie cutter, rules, crop twin, label parser, paperwork | **the same code** | the same code |
| Token Factory | a local OpenAI-compatible stand-in that serves the same three roles (agent, vision, fast) and honours `tools=` and `response_format: json_schema` | Nemotron 3 Super, 3.5 Super VL and 3.5 Lightning on Nebius |
| The agent's choices | FieldPilot's deterministic policy, so the tool sequence is the reference the live model is compared against | Nemotron's own tool calls |
| Flight | the console's simulator, with a trapezoidal speed profile on every pass | the same simulator (the T1's K3-A Pro is closed) |
| Pump | a hydraulic model of the T1's 24 V pump and four nozzles, behind the pump-bridge firmware's exact serial protocol | the real pump, nozzles and Arduino |
| Weather | the live forecast where reachable; otherwise labelled synthetic | the live forecast |

## The pump model (`fieldpilot/emulator.py`)

- The pump curve is linear, from the BOM: 5.5 L/min open flow, 0.8 MPa shut-off. Nozzle flow
  goes as √pressure, with the nozzle constant set so the full-speed operating point is the
  sheet's 3.5 L/min (0.29 MPa at the nozzles).
- Flow scales with motor speed. Below a stall duty the motor cannot move the diaphragm and
  nothing flows.
- Three parameters are uncertain, so each carries a stated range: stall duty 0.20 (0.12–0.30),
  spin-up lag 0.25 s (0.15–0.40), pump-to-nozzle dead time 0.15 s (0.05–0.30). The rehearsal
  reports the litres at the nominal values and at every corner of that box.

## Commands

```
python -m fieldpilot.cli rehearse            # everything, including both operator days
python -m fieldpilot.cli rehearse --quick    # skip the slow steps
```

Outputs go to `runs/rehearsal/`: `REHEARSAL_REPORT.md`, `rehearsal_<time>.json`, the flight
card and KML, and the doctor report. The grounding evaluation and its chart go to
`eval/results/rehearsal/`.

## After the real runs: the deviation report

```
python -m fieldpilot.cli deviation                                   # newest of everything
python -m fieldpilot.cli deviation --litres 1.35                     # tank drop after the bench take
python -m fieldpilot.cli deviation --jug-full 1.70 --jug-duty 1650 --jug-at-duty 1.02
```

For each pair it reports predicted, actual and the deviation in %:

- **Bench:** spray events, seconds spraying, zones sprayed, ack latency, and litres (with
  whether the real figure falls inside the predicted range).
- **Pump calibration:** two 30-second jug tests (full duty, then the spray-speed duty) give
  the real full flow and stall duty. Put them in `fieldpilot/airframe.py` and the bench lays
  the planned L/ha.
- **Field agent:** the live Nemotron tool sequence against the predicted one (edit distance),
  and the two proposals side by side.
- **Operator days:** farms served, effective hectares, battery cycles and out-of-window
  sorties, predicted against live.
- **Token Factory cost:** forecast against Billing → Usage.

## Why it is built this way

A plan is only useful if the hardware does what the plan assumed. Rehearsal fixes the
expected numbers in advance, before anyone sees the real ones. The deviation report then shows
exactly which assumption was wrong (pump flow, stall duty, model behaviour, weather) instead
of the gap being explained away after the fact.

## The simulated field day

`python -m fieldpilot.cli fieldday` runs a whole operator day on the production code in a
simulated world. It writes `runs/fieldday/<date>/` (`FIELD_DAY_REPORT.md`, `fieldday.json`, the
device data exactly as the devices would post it, and an as-applied report per farm). `/ops`
replays the day under **Field day (simulated)**. The same seed gives the same day.

| Layer | Real code or simulated |
|---|---|
| Booking intake, number checks, day planner, sortie planner, drift compensation, flight cards, mast gate, as-applied, battery board | **production code** |
| Reading the bookings and writing the brief | the real Token Factory client, against the rehearsal stand-in (not Nemotron). `--live` uses Nemotron on Token Factory |
| Weather | live forecast where reachable; otherwise a labelled climatological late-October day for western UP |
| The air over each field | forecast + per-farm bias (wind σ 0.35 m/s, temp σ 0.8 °C, RH σ 4 %) + slow drift + 22 % turbulence; wind from about 300° ± 25° per farm |
| FieldMast | the firmware's logic in Python (2-minute mean, 3-second gust), posting every 10 s, with anemometer bias σ 0.25 m/s + 0.1 m/s noise and SHT31 bias σ 0.2 °C / 1.5 % RH |
| Each sortie | flown by the K3-A Pro twin ([K3_TWIN.md](K3_TWIN.md)) |
| Where the spray lands | the true track, moved downwind by release height × wind ÷ 3 m/s (downwash); 1.5 % per m/s of wind stays airborne |
| SprayLog | u-blox M8N + GAGAN: GPS bias random walk σ ≈ 1.2 m + 0.5 m jitter; flow meter jug-calibrated to −0.5 % with 3 % reading noise; pulse counting |
| The crew | drive time × 0.9–1.25, setup 15 ± 4 min, refill and pack swap 2–6 min (3 planned) |
| Charging | 45 ± 3 min per pack, one charger queue |

### India demo day, 30 Oct 2026, seed 7 (synthetic weather in this run)

Our T1 kit as shipped (2 packs, 1 charger), 7 bookings near Dankaur, India SOP rule pack. The
late-October air is the constraint: humidity falls below 50 % by about 11:00 and stays there
all afternoon, so the SOP leaves a morning window only.

| | |
|---|---|
| Farms the plan fitted into the window / completed | 4 / 3. The other 3 bookings get a call with their next window (Sat 31 Oct, 06:00) |
| Abandoned on site | 1 (J-05). The morning's delays pushed its sortie past 11:00, and the mast held it for 30 min at RH 42 % |
| Sorties flown (the day plan's estimate) | 6 (8), every one inside the SOP hours with the mast at GO |
| Take-off delay against the plan | mean 19.8 min, max 30 min. Small slips stack up through a day |
| Flight time against the mission plan | −3.5 % |
| Litres planned / actually sprayed / measured by SprayLog | 47.57 / 38.84 / 38.63 L. SprayLog reads −0.52 %, the flow meter's calibration error |
| On target by the logger's GPS (lowest farm) | 94.2 % |
| Actually deposited on target, after drift (lowest farm) | 95.1 % (deposit shifted 0.7–2.8 m downwind) |
| Zone coverage under the swath (lowest) | 94.1 % |
| Zone dose, measured against plan | −13.9 % to +5.5 % (GPS attribution at zone borders) |
| K3-A Pro breakpoint against planned cut (worst) | 1.51 m |
| Token Factory (stand-in) | 8 calls, 7 of 7 bookings schema-constrained, about $0.00031 at list prices |

**Drift compensation on the same day.** The farms are flown again with the lines left where
they are, so each pair of numbers below is the same air, the same crew and the same seed:

| Farm | Lines moved upwind | Deposited on target, with / without | Lowest zone coverage, with / without | Litres landing off the zones, with / without |
|---|---|---|---|---|
| Ramesh | 0.65 m | 98.1 % / 97.1 % | 98.3 % / 96.6 % | 0.113 / 0.177 L |
| Vikram | 0.97 m | 97.3 % / 96.7 % | 94.1 % / 93.3 % | 0.555 / 0.684 L |
| Kamla Devi | 2.54 m | 95.1 % / 93.2 % | 96.2 % / 86.8 % | 0.599 / 0.835 L |

The windier the farm, the more it gains. At Kamla Devi's farm, the lowest zone coverage rose
from 86.8 % to 96.2 %, and a third less spray landed off the zones. The same compensation in
ArduCopter's own flight is in [FIRMWARE.md](FIRMWARE.md#sitl-results).

On the same bookings, forecast and batteries, the common way of working served 3 farms in the
window against FieldPilot's 4, flew 8 sorties outside it against 0, and left 39.4 L of mixed
product against 1.2 L.

**What the day teaches.**

- The morning window is short, so a 20–30 minute slip early in the day costs the last farm.
  Re-planning the rest of the day after each landing is the next feature.
- The measured air differs from the forecast enough that a mast gate at the field is worth
  having.
- GPS error, not the aircraft, sets the spread of the measured zone doses. SprayLog's flags
  therefore allow for about a metre of GPS error at the zone borders.
