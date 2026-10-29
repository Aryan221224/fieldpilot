# Devpost submission text

Copy each section into the matching Devpost field. Every measured number below comes from a
saved result file in this repository (`eval/results/`, `runs/`), produced by live runs on
Nebius Token Factory on 29 Oct 2026. Simulated results are labelled SIMULATED.

**Project name:** Future Agri FieldPilot

**Tagline:** Every spray-drone app plans the flight. FieldPilot plans the operator's day: every battery cycle, litre and flight minute where the crop needs it, in weather where the spray lands.

**Track:** Physical AI

## Inspiration

About 600,000 agricultural spray drones now work farms worldwide. Every one of them comes with
software that plans the *flight*: a route over one field. None plans the *operator's day*:
which farm first, which battery set flies it, whether the wind and heat let the spray land,
how much product goes into each refill. That is decided in the operator's head over WhatsApp,
and it is where the money and the chemical go. The aircraft is efficient; the work around it
is not:

- **Battery sets cap the day.** A 10 L drone sprays for 8–10 minutes per battery set, then
  the set needs one to two hours to recharge. Jobs are still taken in the order the phone
  rang.
- **Weather is ignored.** Sorties fly in noon wind and heat, where spray drifts or
  evaporates.
- **Whole fields are sprayed** when only some zones need it.
- **Mix is wasted.** Tanks are mixed in full batches and the remainder is dumped.

India's Namo Drone Didi programme shows where this ends. The drones are subsidised, yet
2026 ground reports say about 90 % of the operators in one state are running at a loss.
The causes are battery limits, scattered bookings, mid-field battery swaps and flying
through forbidden weather to cover acres. We wanted to fix the efficiency of the operation,
not add another chatbot: an open, brand-neutral layer between the farmer's booking and the
drone, that works even on a closed controller like ours.

## What it does

FieldPilot plans a spray-drone operation at two levels, and measures the gain against the
common way of working on the same bookings, forecast and batteries.

**Operator desk: the day, across farms.**

1. **Nemotron reads the bookings.** It turns calls, chat messages and voice-note transcripts,
   in any language, into jobs. Code converts the farmer's area unit (acre, bigha, mu, rai,
   alqueire, feddan…) and refuses any area the farmer did not actually write.
2. **The day is planned against:**
   - the live hourly forecast at every farm, judged by the operator's **rule pack**: India's
     binding 2023 drone SOP, or wind and Delta T good practice anywhere else;
   - the operator's battery sets, chargers and charge time;
   - the sorties each job needs;
   - the drive between farms.
3. **Out come:** a timeline, a battery charging schedule, a printable day sheet, a list of
   farmers to call with their next window, and a brief in the operator's language. Nemotron
   writes the brief, every number in it is checked against the plan, and the browser reads it
   aloud.

**Field console: each farm, each flight.**

1. A phone or drone scan goes through a conformal gate. A WOFOST crop digital twin then
   decides which zones a spray pays for.
2. Nemotron 3 Super orchestrates the tools. Tavily verifies the product and dose against the
   national pesticide registry, and every number must appear verbatim in the cited page.
3. The rule gate checks the flight. The mission is cut into balanced, battery- and tank-sized
   sorties from the best take-off point on the field edge. Each refill is mixed to the litre
   it will spray, at the per-hectare dose divided by the drone's water volume.
4. A human approves each sortie, and weather, preflight and rules are re-checked before every
   take-off.
5. Paperwork is generated for the jurisdiction.

**On our own aircraft and its own controller.** FieldPilot is built for our T1-class spray
drone: 10 L tank, four rotors, four nozzles on a 6 mm line, 24 kg take-off, JIYI K3-A Pro flight
controller. The energy model comes from the specification sheet. One figure is fitted (12 min
hover at 24 kg), and the other is predicted to within 4 % (26.9 min at 14 kg; sheet 28).

The K3-A Pro is closed, so each approved sortie becomes a flight card and KML for the JIYI app,
with the dose in L/mu. A behavioural twin of the controller then flies that card: its line
pattern, flow that follows ground speed and stops below 0.5 m/s, the pump's lag, and its
low-liquid breakpoint and resume. On every test field it flies the same number of sorties as
the plan, the tank runs dry within 1.3–3.9 m of where the plan cut the route, sortie times match
within 3–5 %, and at least 98 % of each zone ends up under the swath. Each approval also checks
the airframe envelope: pump flow, spraying speed, take-off mass and temperature.

**Proven on open flight firmware too (SIMULATED).** If the T1's controller is swapped for a
Pixhawk-class one, FieldPilot uploads each sortie over MAVLink. We flew that path on
**unmodified ArduCopter 4.5.7 in its software-in-the-loop simulator, on a physics model of the
T1**. On a 1 ha job of three sorties:

- the firmware's log shows 1094 s of flight against 1103 s planned, and 606 Wh against 597 Wh;
- every pack landed with at least 72.8 % left;
- 97.9 % of every zone was under the swath, and each zone's dose was within 1 % of plan.

**Drift compensation.** FieldPilot moves the spray lines upwind by the wind the mast measures,
so the spray lands on the zone instead of beside it. In the firmware's own flight it raised the
share deposited on target from 96.5–98.4 % to 99.0–99.6 %. On the simulated field day, the
windiest farm's lowest zone coverage rose from 86.8 % to 96.2 %.

**Two field devices close the loop.** **FieldMast** (ESP32, anemometer, SHT31) measures wind,
temperature and humidity at the field. A NO-GO from it blocks approval even with a written
override, because measured beats forecast. **SprayLog** (ESP32, flowmeter, GPS, microSD) rides
on the drone without touching the controller. After each sortie it uploads litres and
positions, and FieldPilot turns them into litres per zone against the plan, plus any spray
outside the treated zones or the field. The live dashboard at `/ops` shows the mast, the
battery board, the devices and the as-applied map.

**Measured:**

- Across 15 region-days in five countries on live forecasts (105 bookings, same batteries), the common way of working flew 67.2 % of sorties outside the spray-weather window, sprayed 82.8 ha that scans showed did not need it and left 380.4 L of mixed product; FieldPilot flew 0.0 % outside the window, sprayed 0.0 ha beyond need, left 13.9 L (the last-refill margins), and fully served 32 farms in-window vs 9. Effective hectares per battery cycle: 0.316 baseline, 0.939 with scheduling and sortie planning alone (+197 %), 1.318 with field scans (+317 %) (`eval/results/efficiency_2026-10-30.json`).
- On a live-forecast operator day of 7 bookings (2026-10-30, IN-SOP-2023 rules, same batteries), FieldPilot fully served 3 farms inside the spray window vs 0, at 0.328 effective ha per battery cycle vs 0.029 (+1031 %), with 0 sorties outside the window vs 10, 0.0 ha sprayed beyond need vs 2.39 and 0.9 L of mixed product left over vs 32.9 L (`runs/day_2026-10-30_20261029T085856Z.json`).
- With the T1's kit as shipped (2 packs, 1 charger), the aircraft is in the air for 12 % of
  India's 9-hour spray window: 13 sorties and 6.5 ha a day. The charger, not the drone, sets
  the day (`python -m fieldpilot.cli airframe`).

In the video, the approved mission is flown by the console's simulator. The T1's own pump,
tank and four nozzles spray real water in step with it, with the flight battery out and the
props off. The bench follows the K3-A Pro's law: flow tracks ground speed, and the pump stops
below 0.5 m/s.

## How we built it

- **Three NVIDIA Nemotron models on Nebius Token Factory, one per kind of work.**
  - **Nemotron 3 Super** runs the field agent's tool loop (native function calling with a JSON
    fallback) and reads a registry row when the parser cannot.
  - **Nemotron 3.5 Super VL** reads the field photos. Token Factory added it in October, so
    the whole pipeline runs on NVIDIA models.
  - **Nemotron 3.5 Lightning** reads bookings, as schema-constrained JSON
    (`response_format: json_schema`), and writes the operator's brief, at a fifth of Super's
    price.
  - Every call goes into a ledger. The desk shows the day's Token Factory calls, tokens and
    estimated cost under the brief.
  - Model IDs are discovered from `/v1/models`, and the submission check fails unless the
    live runs used an NVIDIA model.
- **Tavily.** To find the registered dose and waiting period:
  1. search restricted to the regulator's domains (ppqs.gov.in, cibrc.gov.in);
  2. extract with the product × crop × pest as the query, so only the relevant chunks of a
     200-page PDF come back;
  3. if search misses, a guided crawl of the regulator's own site;
  4. only then, university copies of the list.

  Every number must appear verbatim in the cited page. Every verdict reports the Tavily credits
  it spent. Current local advisories are passed to the planner as untrusted data.
- **Efficiency engine.**
  - A discrete-event day scheduler over battery sets and chargers, with live Open-Meteo
    forecasts per farm.
  - Rule packs with Delta T from the Stull wet-bulb formula.
  - A sortie planner that cuts boustrophedon passes at the exact metre the tank, the usable
    battery (energy to fly home included) or the time limit runs out, balances the loads,
    and searches the field edge for the take-off point with the least dead-heading.
- **Digital twin.** PCSE/WOFOST 7.2 and 8.1, with phenology inverted from the photo's growth
  stage, disease as leaf-area loss, and options ranked by the worst-20 % margin.
- **Our drone.** A T1-class 10 L quad modelled from its specification sheet
  (`fieldpilot/airframe.py`). Power scales with mass^1.5, so every metre of a sortie is
  charged at the mass then on board.
  - **JIYI K3-A Pro hand-off:** flight cards and KML, one approval per sortie.
  - **Bench bridge:** drives the drone's own 24 V pump with the controller's speed law,
    behind four tested safety layers.
  - **MAVLink path:** pymavlink, MAVLink 2, per-sortie upload with DO_SET_SERVO or DO_SPRAYER.
    It uses ArduPilot sprayer parameters derived from the same profile, for when the
    controller is swapped.
- **A Windows desktop app.** `FieldPilot.exe` (PyInstaller + WebView2) opens the operator desk in
  its own window, keeps the keys on a settings page, and runs every simulation from one page.
- **Benchmark.** Five regions (India, Brazil, USA, Kenya, Vietnam), each with its own drone
  class, battery kit, time zone and rule pack, on live forecasts.
- **ArduPilot SITL.** `cli sitl` builds a T1 frame for ArduPilot's simulator: 1.30 m, 34.7-inch
  props, 12S 16 Ah, with each sortie at its own mean mass. It flies FieldPilot's sorties through
  unmodified ArduCopter and reads the firmware's dataflash log:
  - time per phase, energy and minimum pack voltage;
  - the sprayer servo, through the pump's lag;
  - the line error against the simulator's true position;
  - the deposit per zone after drift.

  `fieldpilot/ap_timing.py` predicts the flight time from the firmware's own parameters
  (S-curves, RTL, landing speed) without fitting.
- **The K3-A Pro twin and a simulated field day.** `fieldpilot/k3sim.py` flies FieldPilot's
  flight card the way the closed controller does. `cli fieldday` runs a whole operator day on
  the production code in a simulated world: the bookings, the plan, the air over each field
  (forecast plus bias and turbulence), the mast gate, every sortie on the twin, SprayLog with GPS
  and flow-meter error, the battery board, and the as-applied results. All of it is labelled
  SIMULATED and replayed on `/ops`.
- **Firmware.** `firmware/fieldmast` and `firmware/spraylog` (ESP32, no libraries beyond the
  core) share one logic header. That header is unit-tested on the PC and matches the Python
  rule packs, and both sketches compile with warnings as errors against Arduino API stand-ins.
- **Sim-to-real.** `cli rehearse` predicts the whole run before the real one: the agent's tool
  sequence, the Token Factory cost, and the litres the T1's pump will deliver, from a hydraulic
  model with stated uncertainty ranges. `cli deviation` then measures the real bench take and
  the live Nemotron run against that prediction, and two jug tests calibrate the pump.

## Challenges

- **An honest baseline.** Comparing against a strawman proves nothing. The baseline gets the
  same bookings, forecast and batteries. "Effective" hectares count only coverage flown inside
  the spray window. The gain from scheduling alone is reported separately from the gain from
  scans, and the scan assumptions are printed in the result file.
- **Cutting a pass mid-row** and resuming it on the next battery without spraying anything
  twice. The test checks that sprayed length is conserved to within 1 %.
- **Keeping the model's numbers safe.** An area, dose or time from Nemotron is used only if
  it appears in its source text.
- **A closed flight controller.** The K3-A Pro has no MAVLink and no SDK. We made the plan
  flow into the pilot's own app: a flight card per sortie with zone corners, 4 m spacing,
  speed and dose in L/mu. Each sortie is loaded to the litre, so the tank runs dry where the
  plan cuts it and the controller's own breakpoint resume takes over.
- **Short lines on real firmware.** FieldPilot's estimate flew every leg at constant speed. On
  the 20 m lines of our demo plot, ArduCopter took 150 s against 86 s planned, because its
  S-curves never reach spray speed and its return climbs, waits and lands slowly. We built a
  timing model from the firmware's own parameters, without fitting it to the logs. It predicts
  the return and landing to within a second, and the whole flight to within 4–7 %.
- **A silent WOFOST bug.** WOFOST 8.1 ignores leaf-area updates, so disease looked harmless.
  We switched disease questions to 7.2 and wrote a regression test.

## Accomplishments

- One engine for very different operations: a 10 L drone in Uttar Pradesh under a binding
  SOP, and a 40 L drone in Paraná under Delta T good practice. The only difference is
  configuration.
- Measured, not asserted. The benchmark and day numbers above come from saved result files.
  Separately, Nemotron alone would have flown in 7 situations where the crop twin says the
  spray loses money, and in 4 where the zone was unsafe or unverified.
- 194 automated tests, including:
  - the MAVLink mission handshake against a fake vehicle;
  - multi-sortie approval;
  - pass-cut conservation;
  - battery double-booking;
  - Delta T limits;
  - an adversarial model;
  - an intake that refuses an area the farmer never said;
  - the T1 profile reproducing its spec sheet, the K3-A Pro card and KML, and the bench flow law;
  - schema-constrained intake that still refuses an unwritten area, model routing and cost
    per model, and the Tavily crawl tier;
  - the field-device firmware logic, unit-tested on the PC against the Python rule packs;
  - a whole simulated field day;
  - a sortie flown through ArduCopter SITL, which runs when an ArduPilot build is present.

## What we learned

The biggest efficiency gains are not in the aircraft. They come from deciding where to spray,
when the weather lets the spray land, and which battery flies which job. The model is most
valuable at the edges, reading the booking and briefing the crew. Physics, rules and battery
arithmetic decide everything in between.

## What's next

- A season-long pilot with one Indian SHG operator and one Brazilian service provider, with
  bookings over WhatsApp.
- Offline in the vehicle: Nemotron 3.5 Lightning on a Jetson-class box, for villages without
  coverage (the client already speaks to any OpenAI-compatible endpoint).
- A DGCA type certificate for the T1, or the same software on a type-certified aircraft.
- Fleet planning for operators who run several drones.
- An ArduPilot controller in the T1, so approved sorties upload directly (`FP_SPRAY_CMD=sprayer`).
  This path is proven in SITL. Next, one real hover log will confirm the T1 model.
- More rule packs (EU derogations, US label drift language, China's aerial-application
  standards).
- Calibrate the conformal gate on a season of labelled scans.

## Built during the submission period

FieldPilot is new: it was built after the submission period opened on 26 Aug 2026, and this
repository was published for the submission. It sits beside our earlier college course
project, Future Agri, which was a report and a digital-twin concept with no agent, no Nebius or
NVIDIA models, no drone control and no rules layer. If the judges treat Future Agri as the pre-existing project, the significant
updates are:

- the Nemotron agent and multilingual intake on Token Factory;
- the efficiency engine (day plan, rule packs, sorties) and its benchmark;
- the WOFOST ensemble twin and the perception gate;
- the mission and approval path;
- the label layer, the paperwork and the bench rig.

## Feedback

Paste `docs/FEEDBACK.md`.

## Built with

nvidia-nemotron, nebius-token-factory, tavily, python, fastapi, pcse-wofost, pymavlink,
ardupilot, ardupilot-sitl, open-meteo, matplotlib, arduino, esp32, shapely
