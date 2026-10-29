# The whole system in the field: what runs where, what it costs, what the law asks

This is the setup a spray operator needs to run FieldPilot for real, on our T1 or any other
spray drone. The hardware, wiring and firmware of every system, including the two field devices
(FieldMast and SprayLog), are in [SYSTEMS.md](SYSTEMS.md). Each item says whether it is **built and tested**, **needed but not ours to
build**, or **next**.

## 1. The system at a glance

```
 farmer ── WhatsApp / call / voice note ──┐
                                          ▼
                              operator's phone (browser)
                              /desk   the day     ·   /   one farm
                                          │  HTTPS
                                          ▼
                     FieldPilot server (Python 3.12, FastAPI)
           day planner · sortie planner · crop twin · rule packs · paperwork
              │                 │                  │                 │
              ▼                 ▼                  ▼                 ▼
     Nebius Token Factory    Tavily          Open-Meteo        drone link
     Nemotron 3 Super        registry        hourly forecast   ├ K3-A Pro: flight card + KML
     Nemotron 3.5 Super VL   search·extract  per farm          │   → JIYI app on the pilot's
     Nemotron 3.5 Lightning  ·crawl                            │     ground station
                                                               └ PX4 / ArduPilot: MAVLink upload
                                          ▼
                    T1 10 L quad · pump · 4 nozzles · 2 × 16 Ah packs
                                          ▼
           paperwork: SOP spray record · Gram Panchayat notice · farmer slip
```

## 2. The aircraft and the field kit

| Item | What we use | Status |
|---|---|---|
| Aircraft | T1-class 10 L quad: 4 rotors, 4 nozzles, 24 kg take-off, 10 acres/h on the sheet ([AIRFRAME_T1.md](AIRFRAME_T1.md)). BOM as recorded: ₹2,02,430 | built; airframe modelled and tested |
| Flight controller | JIYI K3-A Pro: GPS, AB-line and polygon routes, breakpoint resume, return on low battery, low liquid or lost link. About ₹39,500 for the kit (Robu listing) | in the aircraft |
| Ground station | The K3-A Pro's remote and app. Routes are entered there, using the FieldPilot flight card | the pilot's own |
| Batteries | 2 × 16 Ah Li-Po supplied (₹15,000 each on the BOM). FieldPilot models them as 12S, 710 Wh | supplied |
| Charger | 1 × PC1080 12S (₹20,000 on the BOM). Charging 710 Wh in the planned 45 min draws about **1 kW** from the wall (derived: 710 Wh ÷ 0.75 h ÷ 0.9 efficiency) | supplied |
| Field power | Village mains is not dependable in the spray window. A **3 kVA generator** runs two chargers with margin (sized from the 1 kW per charger above) | needed, not ours |
| Recommended upgrade | 4 packs + 2 chargers doubles a day from 13 to 26 sorties (6.5 → 13 ha) on the same aircraft (`cli airframe`) | the operator's choice; FieldPilot shows the gain |
| Water and mixing | 20 L/ha: about 130 L for a 6.5 ha day. Two jerry cans, a measuring jug, a bucket. FieldPilot gives the litres and grams per refill | needed |
| Transport | An electric three-wheeler. IFFCO has handed these to Drone Didis with the drone | needed |
| Safety | Gloves, mask, goggles, boots. Never fly over people or cattle | the SOP requires it |
| FieldMast | ESP32 weather mast at the field edge: a NO-GO blocks approval | firmware built and tested off-hardware ([SYSTEMS.md](SYSTEMS.md)) |
| SprayLog | ESP32 flow + GPS logger on the drone: litres per zone after each sortie | firmware built and tested off-hardware ([SYSTEMS.md](SYSTEMS.md)) |

## 3. The operator's phone and the network

- **Any Android phone with Chrome.** The desk and the field console are web pages. The brief is
  read aloud by the browser's own speech, in the operator's language. Nothing needs installing.
- **Network.** Mobile data is needed to plan the day (forecasts, Nemotron) and at each
  approval, when the SOP is re-checked against a fresh forecast. If that check cannot run,
  nothing is cleared. As of 28 Feb 2026, 6,32,392 of India's 6,44,131 villages had 4G
  coverage. The rest is the **edge** item below.
- **Paper still works.** The day sheet, the flight cards and the paperwork print or forward as
  PDFs, so the field side does not depend on the phone staying online.
- **Bookings today come in by copy-paste** from WhatsApp into the desk. A WhatsApp Business API
  hook is **next**.

## 4. The server

FieldPilot is one Python service: `python -m uvicorn fieldpilot.api:app`. Three ways to run it:

| Where | When it fits | Status |
|---|---|---|
| The operator's laptop (Windows `scripts\t1_console.bat`) | one operator, one drone; this is how we demo it | built and run |
| A small cloud VM (2 vCPU, 4 GB) | an FPO or input company serving many operators. The WOFOST ensemble takes seconds on one core | runs as-is; logins and per-operator data are **next** |
| A Jetson-class box in the vehicle | villages without coverage | **next** (see §7) |

## 5. The AI and data services

| Service | Job | Model / endpoint | Cost |
|---|---|---|---|
| Nebius Token Factory | agent tool loop | `nvidia/nemotron-3-super-120b-a12b` | $0.30 / $0.90 per 1M tokens (in / out) |
| Nebius Token Factory | perception from field photos | `nvidia/Nemotron-3.5-Super-VL` | $0.30 / $0.90 |
| Nebius Token Factory | reading bookings, writing the brief | `nvidia/Nemotron-3_5-Lightning` | $0.06 / $0.24 |
| Tavily | product and dose against CIB&RC | search → extract → crawl of ppqs.gov.in | credits per check, reported in every verdict |
| Open-Meteo | hourly forecast at every farm | `api.open-meteo.com` | free for non-commercial use only. **A commercial operator needs a paid Open-Meteo plan** |

The desk shows the day's Token Factory use and its estimated cost under the brief. Models are
discovered from `/v1/models` at start-up, so a catalog change does not break a deployment
([MODELS.md](MODELS.md)).

## 6. The law (India)

FieldPilot checks the spraying rules. The aircraft and pilot paperwork are the operator's, and
FieldPilot does not replace them.

| Requirement | What it is | Who handles it |
|---|---|---|
| **Type certificate** (Form D-1) | Commercial drones need a certified model. **Our self-built T1 has none.** Before paid spraying, the T1 needs certification, or the operator flies a type-certified model. FieldPilot works the same on either | the manufacturer |
| **UIN** (Form D-2, eGCA, ₹100) | Every drone over 250 g is registered | the owner |
| **Remote Pilot Certificate** (Form D-3, ₹100, 10 years) | Training at a DGCA-authorised RPTO; needed for small drones and above | the pilot |
| **Airspace** | Green zone up to 400 ft needs no permission; yellow needs ATC permission; red needs central government permission. Spraying is at 2–3 m | the pilot checks the airspace map before each day |
| **Third-party insurance** | Required for commercial operation | the operator |
| **Crop-specific drone SOP (24 Feb 2023)** | Wind < 3 m/s, ≤ 35 °C, RH ≥ 50 %, 1.5–2.5 m above canopy, buffers, no flowering-stage spraying of listed crops, registered products at registered doses | **FieldPilot**: rules as code, re-checked at every approval |
| **24 h notice and spray record** | Notice to the Gram Panchayat before spraying; a record within 7 days | **FieldPilot** generates both |

Outside India the rule pack switches to drift and Delta T good practice. The operator's
national aviation rules still apply.

## 7. Next: offline in the vehicle

NVIDIA describes Nemotron 3.5 Lightning (30B, 3B active) as built to run locally on Jetson
systems and RTX GPUs. The plan is a Jetson-class box in the vehicle running Lightning for
bookings and the brief, with the planner, rules and forecast cache on the same box, syncing
when coverage returns. FieldPilot's model client already speaks to any OpenAI-compatible
endpoint (`NEBIUS_BASE_URL`), so this is a deployment change, not a rewrite. **Not built or
tested yet.**

## 8. How it spreads

| Who | Why they would deploy it |
|---|---|
| Drone Didi programmes (fertiliser cooperatives, FPOs) | the drones are bought; operators' income is the open problem |
| Input companies running spray services (FMC, Coromandel, DeHaat) | more hectares per drone-day, registered doses, paperwork done |
| Independent operators anywhere | the same constraints on every battery drone; rule packs and units per country |

## 9. Bench and demo setup

The demo uses the T1's own pump, tank and nozzles, driven by an Arduino in step with the
console's simulated flight, with the flight battery out and the props off
([BENCH_RIG.md](BENCH_RIG.md)). Before the real take, `cli rehearse` predicts what the pump will
deliver. Afterwards, `cli deviation` measures the gap between that prediction and the real run
([REHEARSAL.md](REHEARSAL.md)).

## Sources

- T1 specification sheet and BOM (supplied by the team).
- JIYI K3-A Pro: https://robu.in/product/jiyi-k3-apro-flight-controller-kit/ · https://www.worldronemarket.com/product/jiyi-k3a-pro-flight-controller/
- 4G coverage, Lok Sabha Unstarred Question 5480, Annexure III (data as of 28 Feb 2026): https://usof.gov.in/upload/parliament_questions/LSPQ_5480/v11_Draft_LSPQ_5480_15233_25_03_2026.pdf
- Drone registration, type certificate, RPC and zones (2026 summary; verify against DGCA/eGCA): https://www.legalraasta.com/blog/drone-registration-in-india/
- IFFCO three-wheeler with the drone: https://www.indiancooperative.com/co-op-news-snippets/iffco-gifts-drone-3-wheeler-to-namo-didi-in-raipur/
- Open-Meteo terms (free API for non-commercial use only): https://open-meteo.com/en/terms
- Token Factory Nemotron models and prices: https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference
- Nemotron 3.5 Lightning on Jetson and RTX: https://interestingengineering.com/ai-robotics/nvidia-nemotron-35-lightning-autonomous-agents
- Crop-specific drone SOP, MoA&FW, 24 Feb 2023: https://croplifeindia.org/wp-content/uploads/2024/01/TTC_SOP_2023-4.pdf
