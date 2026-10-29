# Future Agri · FieldPilot

**The efficiency layer for agricultural spray drones.**
Every battery cycle, litre and flight minute goes where the crop needs it, in weather where the spray lands.

Nebius × NVIDIA Global AI Hackathon 2026 · Physical AI track · Bennett University

---

**Every spray-drone app plans the flight. Nobody plans the operator's day.** FieldPilot does:
which farm first, which battery set flies it, whether the weather lets the spray land and the
law allows it, and how much water and product go into each refill. It runs on the drone the
operator already owns, closed controllers included, and it is demonstrated on our own T1
10 L quad.

About 600,000 agricultural drones are in use worldwide. The aircraft is efficient; the day
around it is not. Battery sets and chargers, not the drone, cap a day's hectares, yet jobs are
taken in booking order. Sorties go up in wind and heat where spray drifts or evaporates. Whole
fields get sprayed when only some zones need it. Product is mixed in full-tank batches and the
remainder is dumped. In India's Namo Drone Didi programme, about 90 % of operators in one state
report a loss. What is new and who uses it: [docs/NOVELTY.md](docs/NOVELTY.md). The full field
setup, costs and the law: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Every system it runs on, with
firmware for the two field devices: [docs/SYSTEMS.md](docs/SYSTEMS.md). What flies the aircraft,
and FieldPilot's missions flown on unmodified ArduCopter in SITL: [docs/FIRMWARE.md](docs/FIRMWARE.md).
Problem and sources: [docs/PRODUCT.md](docs/PRODUCT.md).

FieldPilot plans the whole operation — the day across farms, and each flight on each farm —
and measures the gain against the common way of working on the same bookings, forecast and
batteries:

**Operator desk** (`/desk`, `fieldpilot day`) — *the day.* Nemotron on Token Factory reads
bookings in any language into jobs; code converts the farmer's area unit (acre, bigha, mu, rai,
alqueire, feddan …) and refuses any number the farmer did not write. The day is planned against
the live hourly forecast at every farm under the operator's **rule pack** (India SOP-2023, or
drift + Delta T good practice anywhere else), the sorties each job needs, battery sets, chargers,
charge time and drive time. Out come a timeline, a battery charging schedule, a printable day
sheet, the farmers to call with their next window, and a brief in the operator's language
(Nemotron, number-checked, read aloud).

**Field console** (`/`) — *one farm.* Photos per zone → perception with a conformal gate → a
WOFOST crop twin decides which zones a spray pays for → Nemotron orchestrates the tools,
Tavily verifies the product and dose against the national registry → the rule gate → a mission
cut into balanced, battery- and tank-sized sorties from the best take-off point → a human
approves each sortie → MAVLink → the paperwork.

**Efficiency benchmark** (`eval/efficiency_bench.py`) — five regions (India, Brazil, USA, Kenya,
Vietnam), live forecasts, three plans per day: the common way, FieldPilot without scans,
FieldPilot with scans. Reports effective hectares per battery cycle, sorties outside the spray
window, hectares sprayed beyond need and mixed product left over.

```
 phone / drone photos
        │
        ▼
 ┌─────────────────────┐   catalog VLM on Token Factory
 │ perception          │   structured findings  ─┐
 │  + pixel cross-check│   ExG canopy, lesions   ├─► conformal gate ──► human review queue
 └─────────────────────┘                         ┘        (only singleton sets may act)
        │
        ▼
 ┌─────────────────────┐   WOFOST ensemble: weather analogue years × parameter draws,
 │ crop digital twin   │   phenology inverted from the photo's growth stage,
 │                     │   disease coupled as leaf-area loss, photoperiod gate
 └─────────────────────┘   → yield and ₹/ha for every option, risk-adjusted (CVaR80)
        │
        ▼
 ┌─────────────────────┐   Nemotron 3 Super on Nebius Token Factory calls the tools,
 │ agent (tool calls)  │   cites Tavily for local advisories, writes the brief in
 │                     │   English and Hindi. It cannot pick a dose or start a motor.
 └─────────────────────┘
        │  plan_ids the twin produced, zones that passed the gate
        ▼
 ┌─────────────────────┐   geofence · altitude · range · energy · tank · wind/rain ·
 │ mission + safety    │   preflight — plain code, re-run at the moment of approval
 └─────────────────────┘
        │  operator presses Approve
        ▼
   MAVLink (PX4 / ArduPilot) → the drone sprays only the approved zones → RTL
```

**Nemotron proposes. Physics disposes. A human approves. The drone flies.**

## What runs where

| Piece | Model / service | Where |
|---|---|---|
| Booking intake (any language → job) | NVIDIA Nemotron 3.5 Lightning, schema-constrained JSON (`response_format: json_schema`); every number checked against the message | Nebius Token Factory |
| Operator's brief, in the operator's language | NVIDIA Nemotron 3.5 Lightning (every number checked against the plan) | Nebius Token Factory |
| Agent brain (tool calling) | NVIDIA Nemotron 3 Super | Nebius Token Factory |
| Perception | NVIDIA Nemotron 3.5 Super VL (any OpenAI-compatible endpoint via `FP_VISION_BASE_URL`) | Nebius Token Factory |
| Grounding eval | Nemotron alone vs FieldPilot | Token Factory |
| Day plan | battery sets × chargers × rule-pack weather windows × sorties × drive time | plain code + Open-Meteo |
| Rule packs | India SOP-2023 (binding); drift + Delta T good practice (anywhere) | `fieldpilot/rules.py` |
| Efficiency benchmark | 5 regions × live forecasts × 3 plans | `eval/efficiency_bench.py` |
| Sorties | route cut at the exact metre the tank or battery runs out, resumed next sortie | plain code |
| Local advisories | Tavily search | Tavily API |
| Pesticide label check | Tavily search + extract on CIB&RC / university domains; parser first, Nemotron reads the row only if the parser cannot, every number verified verbatim | Tavily API + Token Factory |
| Indian drone-spraying law | `fieldpilot/sop.py` — SOP-2021 and SOP-2023 as 15 checks with clause citations | plain code |
| Paperwork | Gram Panchayat notice, SOP spray record, Hindi farmer slip | `fieldpilot/paperwork.py` |
| Crop twin | PCSE / WOFOST 7.2 + 8.1 | CPU, anywhere |
| Drone | JIYI K3-A Pro: flight card + KML per sortie · pymavlink → PX4 or ArduPilot (flown in ArduCopter SITL) | laptop / companion computer at the plot |
| Field devices | FieldMast (ESP32 weather mast) · SprayLog (ESP32 flow + GPS logger) | at the field edge / on the drone |

Model IDs are discovered from `GET /v1/models` at start-up and printed by `make models`, so a
renamed model never breaks the demo.

## Run it

1. `python3 -m pip install -r requirements.txt`, then fetch the pinned WOFOST crop parameters into `vendor/`: `powershell -ExecutionPolicy Bypass -File scripts/setup_vendor.ps1` (Windows) or clone branches `wofost72` and `wofost81` of `ajwdewit/WOFOST_crop_parameters` into `vendor/wofost72` and `vendor/wofost81` (commits pinned in that script)
2. `cp .env.example .env` and fill `NEBIUS_API_KEY` and `TAVILY_API_KEY` — see [docs/NEBIUS_SETUP.md](docs/NEBIUS_SETUP.md)
3. `python3 -m fieldpilot.cli doctor` — checks the catalog, a real Nemotron tool call, the vision model, Tavily, weather, the twin and the drone link; writes `runs/doctor_<time>.json`
4. `python3 -m uvicorn fieldpilot.api:app --port 8080` → http://localhost:8080/desk (operator desk) and http://localhost:8080 (field console)
5. `python3 -m fieldpilot.cli day examples/desk_demo_in.json --fill-submission` (or `desk_demo_br.json`) — Nemotron reads the demo bookings, the day is planned on live forecasts and compared with the common way of working; writes `runs/day_*.json`, a printable day sheet, and the measured sentence into `docs/SUBMISSION.md`
5b. `python3 eval/efficiency_bench.py --fill-submission` — the five-region benchmark on live forecasts; writes `eval/results/efficiency_*.json` + chart and the sentence into `docs/SUBMISSION.md`
6. `python3 -m fieldpilot.cli run path/to/photos/` — one farm; photos named `A1_*.jpg`, `B2_*.jpg`…; plans for our T1 10 L quad by default (`FP_DRONE_SPEC=agri_40l` etc. for other presets)
7. `python3 eval/grounding_eval.py` then `python3 eval/make_charts.py --fill-submission` — the Nemotron-alone baseline, chart and numbers
8. `python3 -m pytest` — 199 tests, including the MAVLink mission handshake against a fake vehicle, multi-sortie approval, the SOP gate, the booking intake, the field-device firmware logic and a whole simulated field day (one flies a sortie in ArduCopter SITL; it runs when `FP_ARDUPILOT` points to an ArduPilot build, and is skipped otherwise)
9. `python3 -m fieldpilot.cli doctor --submission` — Devpost readiness
10. `python3 -m fieldpilot.cli rehearse`, then after the real runs `python3 -m fieldpilot.cli deviation` — the predicted run and the measured sim-to-real gap ([docs/REHEARSAL.md](docs/REHEARSAL.md))
11. `python3 -m fieldpilot.cli fieldday` — a whole operator day, simulated end to end on the production code: the K3-A Pro twin flies every sortie, the field devices are emulated, the spray lines are drift-compensated (`--no-drift-compensation` to compare, `--live` for Nemotron on Token Factory); replay it at http://localhost:8080/ops ([docs/REHEARSAL.md](docs/REHEARSAL.md#the-simulated-field-day), [docs/K3_TWIN.md](docs/K3_TWIN.md))
12. http://localhost:8080/ops — Field Ops: the FieldMast GO/NO-GO, the battery board, devices and the as-applied map; `python3 -m fieldpilot.cli devsim mast` stands in for a mast ([docs/SYSTEMS.md](docs/SYSTEMS.md))
13. `python3 -m fieldpilot.cli sitl --ardupilot <ArduPilot tree> --field 1ha` (Linux/WSL; build `./waf configure --board sitl && ./waf copter` first) — unmodified ArduCopter flies FieldPilot's sorties on a T1 physics model; the report is read from the firmware's own log ([docs/FIRMWARE.md](docs/FIRMWARE.md))
14b. A double-clickable Windows app with every simulation on one page (`/sims`) and a settings page for the keys: double-click `scripts\build_exe.bat`, then run `dist\FieldPilot\FieldPilot.exe` ([docs/WINDOWS_APP.md](docs/WINDOWS_APP.md))
14. Windows, everything at once: double-click `scripts\run_all.bat` — tests, airframe, benchmark, field day and rehearsal into `run_all_log.txt`, then the console opens on `/ops`

The operator desk and the benchmark use Nemotron and live forecasts by design.

### On the lab PC's GPU (free development runs)

The same Nemotron models run locally through Ollama on the RTX A6000 for prompt and
tool-loop iteration without spending credits. Those runs are labelled `LOCAL-GPU (not
Nebius)` everywhere and do not count for the entry — see [docs/LOCAL_GPU.md](docs/LOCAL_GPU.md).

## Our drone: the T1 10 L quad (JIYI K3-A Pro)

FieldPilot runs on our own aircraft: 10 L tank, four rotors, four nozzles, 24 kg take-off,
JIYI K3-A Pro controller. `python3 -m fieldpilot.cli airframe` prints its profile, derived
from the specification sheet. The model predicts the sheet's 28 min hover at 14 kg as 26.9 min
and, with braking and turning at every line end, works out 8.5 acres/h against the brochure's 10. With the kit as
shipped (2 packs, 1 charger), it shows the aircraft in the air for 12 % of the spray window. See [docs/AIRFRAME_T1.md](docs/AIRFRAME_T1.md).

1. Edit `examples/field_demo.json` with your plot's corners (walk them with a phone GPS).
2. `FP_DRONE=k3 python3 -m uvicorn fieldpilot.api:app --port 8080`. Each approved sortie
   becomes a flight card and KML for the JIYI app, because the K3-A Pro cannot be commanded
   over a link. The pilot flies it; one-key return is the abort.
3. With a PX4 or ArduPilot controller instead: `FP_DRONE=mavlink`, see
   [docs/DRONE_SETUP.md](docs/DRONE_SETUP.md).
4. Read [docs/SAFETY.md](docs/SAFETY.md) before the first flight. Spray water for the demo.

## Indian law, as code (the binding rule pack)

An Indian drone operator is bound by two Ministry of Agriculture SOPs: the 2021 SOP (434th
Registration Committee) and the 2023 crop-specific SOP. FieldPilot checks every mission
against both before it can be approved (`fieldpilot/sop.py`):

| Check | Limit | Where the value comes from |
|---|---|---|
| wind / temperature / humidity / rain | < 3 m/s · ≤ 35 °C · ≥ 50 % RH · dry, < 10 mm in 48 h | hourly forecast (Open-Meteo) |
| flowering stage (rice, maize, soybean …) | no spraying at flowering | **crop twin** (WOFOST DVS) |
| pre-harvest interval | harvest ≥ spray + label waiting period | **crop twin** maturity date + label |
| water volume | 20 L/ha early, 25 L/ha at full canopy | **crop twin** canopy stage |
| height · speed · swath | 1.5–2.5 m above canopy · 4.5–5.0 m/s · 3–6 m | mission + drone spec |
| registered product and dose | CIB&RC major-use row for this crop and pest | `verify_label` (Tavily) |
| Gram Panchayat notice · 100 m buffer · DGCA pilot | ≥ 24 h before · ≥ 100 m · certificate | operator record (`compliance` in the field file) |

The verdict is **CLEAR**, **BLOCKED** (a hard rule failed: Approve is disabled, no override
exists) or **NEEDS_HUMAN** (something could not be verified: the operator must type how they
checked it, and that reason is written into the run record). When the weather blocks, the
console shows the next legal spray window. Approve runs the SOP again with a fresh forecast.

The tank-mix trap is handled too: a label's dose is per hectare at ~500 L of water; a drone
applies it in 20–25 L, so "mix as per label" in a drone tank under-doses ~20×.
`sop.tank_mix()` divides the per-hectare dose by the drone's water volume, and the farmer
slip prints grams per tank.

`python3 -m fieldpilot.cli paperwork runs/<run_id>.json` (or the buttons in the console) writes
the Gram Panchayat notice (EN + HI), the spray record with every SOP check, and a Hindi farmer
slip that starts with the first legal harvest date.

## Physical AI: the spray hardware

The aircraft's position in the demo is simulated; the spray hardware is our T1's own pump,
tank and four nozzles, with the flight battery out and the props off.
`python3 -m fieldpilot.cli bench --source console --flow-law t1_10l` drives that pump, live,
from the approved mission. Flow follows the simulated ground speed and stops below 0.5 m/s,
as the K3-A Pro does (`scripts\t1_console.bat` + `scripts\t1_bench.bat COM6`). The same
bridge can drive an ArduPilot controller's pump output with props off, or an Arduino + 12 V
pump. MAVLink 2 is forced so AUX outputs (servo 9–16) are
reported. Four independent safety layers, each tested; see [docs/BENCH_RIG.md](docs/BENCH_RIG.md).

## The rules that are code, not prompt

| Rule | Where |
|---|---|
| A zone is simulated only after it is inspected | `fieldpilot/tools.py` `simulate_zone_options` |
| Only the twin's recommended, drone-deliverable plan for a zone that passed the gate can enter a mission | `tools.py` `build_treatment_mission` |
| The model has no tool that moves the aircraft | `tools.py` `TOOL_SCHEMAS` |
| Approve re-fetches weather and preflight and re-runs every safety check | `fieldpilot/service.py` `approve` |
| No flight can be proposed until the mission has passed through `check_sop` | `tools.py` `submit_proposal` |
| A BLOCKED SOP verdict cannot be approved; NEEDS_HUMAN needs a written operator reason | `service.py` `approve` |
| Every number in a label claim must appear verbatim in the cited excerpt | `fieldpilot/label.py` |
| Search results are passed as untrusted data and never reach the planner | `fieldpilot/context.py` |
| Rule violations are logged per run | `runs/<run_id>.json` → `violations` |

`tests/test_agent.py::test_adversarial_model_is_contained` scripts a model that tries to spray a
review-flagged zone, call a made-up `arm_and_fly_now` tool and submit a fake mission id. Nothing
flies, every attempt is logged, and the run still ends in a legal proposal.

## Honest status

* All three roles run on NVIDIA Nemotron models on Token Factory: 3 Super (agent), 3.5 Super VL
  (perception) and 3.5 Lightning (bookings and the brief), discovered from `/v1/models`. If a
  catalog ever lacks an NVIDIA VLM, perception falls back to another one and the console says
  "non-NVIDIA". See [docs/MODELS.md](docs/MODELS.md).
* `python -m fieldpilot.cli rehearse` predicts the whole run (a stand-in Token Factory, the
  simulated flight, the T1 pump modelled hydraulically). It is labelled REHEARSAL everywhere and
  can never fill the submission; `cli deviation` later measures the real runs against it
  ([docs/REHEARSAL.md](docs/REHEARSAL.md)).
* The T1 is self-built and has no DGCA type certificate. Paid spraying needs one, or a
  type-certified aircraft; FieldPilot works the same on either ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#6-the-law-india)).
* The aircraft in the hardware clip is simulated; the pump is real. The video says so on screen.
* The field day (`cli fieldday`) and the ArduPilot runs (`cli sitl`) are simulations, labelled
  SIMULATED in every file they write. They show what a real day should look like and are the
  yardstick for it; they are not field measurements.

* Tavily is verified live (27 Oct 2026: the CIB&RC row for Mancozeb 75 % WP on potato / late
  blight, 1000 g/ha, from ppqs.gov.in — [docs/TAVILY.md](docs/TAVILY.md)). The Token Factory
  paths are exercised against a mock OpenAI-compatible server in the tests; quote only numbers
  from runs made with a real key.
* The day-plan comparison is a schedule on a real forecast, not a field trial: drive times use a
  road factor, battery and charger numbers are the operator's inputs.
* The grounding-eval and benchmark numbers come from the saved result files in `eval/results/`
  (live runs on Token Factory, 29 Oct 2026); quote those files, not a guess.
* WOFOST parameters are European calibrations; absolute yields are indicative, relative decisions
  are the defensible output. Disease is a simplified leaf-area-loss model, documented as such.
* The conformal gate is calibrated on 150 public, labelled potato leaf photos (PlantDoc, PlantVillage;
  `python eval/public_scans.py all`, results in `eval/results/calibration_2026-10-29.md`). On those
  photos Nemotron 3.5 Super VL is right about healthy vs diseased 98 % of the time but close to a coin
  toss between early and late blight, so a healthy zone passes on its own and every diseased zone goes
  to the operator to pick the disease. These are leaf close-ups, not our drone scans: recalibrate on a
  season of labelled field scans (`fieldpilot calibrate labels.csv`).

## Repository

```
fieldpilot/      intake.py dayplan.py desk.py sorties.py rules.py   (operator desk + efficiency engine)
                 agent.py tools.py llm.py perception.py twin.py mission.py context.py
                 field.py service.py api.py cli.py doctor.py  drone/{sim,mavlink,k3}.py airframe.py k3card.py
                 sop.py label.py records.py paperwork.py bench.py
                 rehearsal.py emulator.py deviation.py flywheel.py
                 k3sim.py (K3-A Pro twin) fieldday.py devices.py devsim.py drift.py
                 sitl.py (ArduCopter SITL) ap_timing.py (ArduPilot timing model)
sim/             t1_frame.json, t1_sitl.parm (the T1 for ArduPilot's simulator)
runs/            sitl/ (four ArduCopter runs) · fieldday/ (the simulated field day)
scripts/         run_all.bat (everything on a Windows laptop) · t1_console.bat · setup_vendor.ps1
firmware/        pump_bridge/ (bench pump) · fieldmast/ (ESP32 spray-weather mast) · spraylog/ (ESP32 as-applied logger) · common/ (shared, host-tested logic)
web/            index.html field console · desk.html operator desk · ops.html field ops (no external assets)
eval/            efficiency_bench.py (5 regions, live forecasts), grounding_eval.py (Nemotron alone vs FieldPilot), make_charts.py
examples/        field_demo.json, desk_demo_in.json (India), desk_demo_br.json (Brazil), synthetic photos generator
tests/           199 tests incl. mock Token Factory server, fake MAVLink vehicle, the SOP gate, firmware logic and SITL
docs/            one topic per file: novelty, systems, firmware, deployment, models, K3 twin, rehearsal, setup, local GPU, drone, safety, architecture, submission, video, judging, checklist
vendor/          pinned WOFOST crop parameters
```

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
