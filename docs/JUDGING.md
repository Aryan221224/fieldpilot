# How FieldPilot meets each judging criterion

| Criterion (equal weight) | What the judge sees | Evidence |
|---|---|---|
| **Technological implementation** — effective model use | Three NVIDIA Nemotron models on Token Factory, each matched to its job: 3 Super for the agent's tool loop, 3.5 Super VL for perception, 3.5 Lightning for bookings (schema-constrained JSON) and the brief. Every model number is checked against its source, and the day's Token Factory cost is shown to the operator. Behind them sits an efficiency engine: a day scheduler over batteries × chargers × weather windows, rule packs with Delta T, exact-metre sortie cutting, balanced loads and the best take-off point. 194 tests. Modelled on our own T1 10 L quad and its JIYI K3-A Pro: a behavioural twin of the closed controller flies FieldPilot's flight card and lands within 1.3–3.9 m of every planned cut, with ≥ 98 % zone coverage. Two ESP32 field devices (FieldMast, SprayLog) feed the approval gate and the as-applied record. The open-firmware path is flown end to end on unmodified ArduCopter 4.5.7 in SITL on a T1 physics model: 1094 s against 1103 s planned, 606 against 597 Wh, 97.9 % coverage, every zone within 1 % of its dose (simulated) | `fieldpilot/llm.py`, `intake.py`, `agent.py`, `desk.py`, `dayplan.py`, `sorties.py`, `rules.py`, `sitl.py`, `ap_timing.py`, [MODELS.md](MODELS.md), [FIRMWARE.md](FIRMWARE.md) |
| **Design** — complete product experience | Two screens for one operator. The desk: bookings → jobs with confirm flags → timeline with spray windows and battery sets → comparison → day sheet → spoken brief → today's AI cost. The field console: scan → trace → map → proposal → rule gate → per-sortie Approve → flight → ABORT → paperwork. Flight cards for a closed controller. Works in Hindi, Portuguese or English by configuration. A third screen, `/ops`, shows the mast's GO/NO-GO, the battery board, the devices and the as-applied map, plus a replay of a simulated field day. Runs in a phone browser ([SYSTEMS.md](SYSTEMS.md), [DEPLOYMENT.md](DEPLOYMENT.md)) | `web/desk.html`, `web/index.html`, video |
| **Potential impact** — real problem, real audience | ~600,000 agricultural drones worldwide. Every one has flight software; none plans the operator's day ([NOVELTY.md](NOVELTY.md)). Case study: India's Namo Drone Didi, ~90 % of operators in one state at a loss. Measured on the same bookings, forecast and batteries in five countries: effective ha per battery cycle, sorties outside the spray window, ha sprayed beyond need, litres of leftover mix. The deployment path is costed, including the law | `docs/PRODUCT.md`, `eval/results/efficiency_*.json`, `runs/day_*.json` |
| **Quality of the idea** — non-obvious use | Not a farm chatbot and not another flight planner: the missing layer between the booking and the drone. It treats battery cycles, spray-weather windows, law and crop need as one scheduling problem. Nemotron works at the language edges (bookings in, brief out) and as the field agent's orchestrator, never as the source of a number. It runs on the drone the operator already owns, closed controller included, and every prediction is checked against the real hardware (`cli rehearse` → `cli deviation`). It closes the loop at the field edge: the mast's measured wind gates approval and moves the spray lines upwind, and SprayLog proves where the litres landed. In simulation, the windiest farm's lowest zone coverage rose from 86.8 % to 96.2 % | [NOVELTY.md](NOVELTY.md), [ARCHITECTURE.md](ARCHITECTURE.md), [REHEARSAL.md](REHEARSAL.md), `fieldpilot/drift.py` |

## Requirements checklist

- [x] Runs on Nebius Token Factory (agent, booking intake, brief, eval)
- [x] Uses NVIDIA open models (Nemotron 3 Super, 3.5 Super VL and 3.5 Lightning on Token Factory)
- [x] Open-source licence file (Apache 2.0)
- [x] README with setup instructions
- [ ] Public repository (push before submitting)
- [ ] Demo video < 3 min with ≥ 1 min of the hardware operating (docs/VIDEO_SCRIPT.md)
- [ ] Demo URL (optional for Physical AI)
- [x] Newly created during the submission window (repository started 21 Sep 2026)

## Bonus prizes this also targets

- **Best use of Tavily** — at runtime, search → extract → (if search misses) a guided crawl of
  the regulator's own site finds the registered dose and waiting period for the product ×
  crop × pest. The numbers must appear verbatim in the cited page, they feed two SOP checks
  (dose, harvest interval), and every verdict reports the credits it spent. Verified live:
  [docs/TAVILY.md](TAVILY.md). Search also supplies current local advisories as untrusted data.
- City Winner is not available: none of the 20 cities is in India.
