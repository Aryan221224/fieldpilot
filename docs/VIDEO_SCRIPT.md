# Demo video — under 3:00, with a continuous 75 s hardware clip and voice-over

Rules this script satisfies:

- The video is public on YouTube and under 3:00.
- It shows the project working, with **audio explaining how Nebius Token Factory and the NVIDIA models are used**.
- Physical AI: it includes **at least one continuous minute of real hardware operating**.
- No copyrighted music. Use silence or a royalty-free track you have a licence for.

Record the bench rig (phone on a tripod) and the console (OBS, 1920×1080, browser zoom 110 %)
at the same time, then cut between them.

| Time | Picture | Voice-over (read slowly) |
|---|---|---|
| 0:00–0:14 | **Our T1 spraying a real field** (team clip `T1_field_manual.mp4`, about 10 s from the 0:03–0:27 shot), lower-third *"Our airframe: 10 L quad, JIYI K3-A Pro · manual flight, before FieldPilot"* | "There are about six hundred thousand spray drones on farms worldwide. Every one has an app that plans the flight. None plans the operator's day: eight minutes per battery, two hours to charge, jobs in the order the phone rang, sorties in wind where the spray drifts. In India's Drone Didi scheme, most operators in one state are losing money. FieldPilot plans the day." |
| 0:14–0:40 | **Operator desk** `/desk`, India demo: Hindi/Hinglish bookings → **Read with Nemotron** → job cards with a "bigha — confirm" flag → **Plan my day** → timeline: green spray windows per farm, sorties with battery sets. Quick cut: switch the demo to Brazil (Portuguese, alqueire, 40 L drone, Delta T rules) → plan | "Nemotron 3.5 Lightning on Nebius Token Factory reads every booking, in any language. Code checks every number it extracts. Then the day is planned on the live forecast at each farm, the local spray rules, and the batteries the operator actually has — the same engine in India and in Brazil." |
| 0:40–0:55 | Comparison table → benchmark chart (effective ha per battery cycle · % sorties outside window · leftover mix) → **Read aloud** plays the brief 3 s | "Same bookings, same forecast, same batteries. Across five countries: more effective hectares per battery cycle, no sorties outside the spray window, no unneeded hectares, and only a few hundred millilitres of mix left per farm." |
| 0:55–1:10 | **Field console** for one farm: photos → trace streaming (Nemotron tool calls) → map with two zones orange → Tavily label check with the ppqs.gov.in citation → SOP panel | "On the farm, Nemotron 3 Super runs the tools, and Nemotron Super VL reads the photos: the crop twin decides which zones a spray pays for, Tavily checks the product and dose against the government registration, and the SOP gate checks the law." |
| **1:10–2:25** | **Continuous bench clip on our T1** (docs/BENCH_RIG.md, Option C). Console map flies the approved mission (picture-in-picture); the T1's own pump and four nozzles spray only on the treated zones' passes, fade in each turn, stop in transit; press ABORT once. Lower-third: *"Aircraft position simulated · our drone's own pump, tank and nozzles · battery out, props off"* | "This is our drone's own spray system. The flight is simulated; the pump is real. It sprays only the zones the physics justified, at the flow the K3-A Pro would use at that speed… off in transit… and the abort cuts it instantly." (Stay quiet for part of it.) |
| 2:25–2:45 | Load sheet → **K3-A Pro flight card** (L/mu, 4 m spacing, zone corners) → 3 s of `/ops`: mast GO/NO-GO and the as-applied map (label "simulated field day" if replayed) → 3 s of `/ops` **Firmware: ArduPilot SITL** tab, 1 ha run (lower-third *"Simulated: unmodified ArduCopter on a T1 model · 1094 s flown vs 1103 s planned"*) → `cli airframe`: "kit as shipped: 13 sorties, 6.5 ha a day, aircraft in the air 12 % of the window" | "Our controller is closed, so FieldPilot hands the pilot a flight card for each approved sortie. On open firmware it uploads the mission directly: in simulation, unmodified ArduPilot flew our plan to within one percent. And on our own kit, two packs and one charger, the drone is in the air twelve percent of the spray window. The charger sets the day, which is exactly what FieldPilot plans around." |
| 2:45–2:58 | Title card: measured numbers from `eval/results/efficiency_*.json` · "Nemotron on Nebius Token Factory · Tavily · WOFOST" | "FieldPilot: every battery cycle, litre and flight minute where the crop needs it." |

## Making the refusal (offline or live)

- **Live:** pick an hour when the forecast wind is above 3 m/s. The SOP check uses the real Open-Meteo forecast.
- **Offline rehearsal:** `FIELDPILOT_OFFLINE_WIND=4.2 python3 -m uvicorn fieldpilot.api:app --port 8080`. The console labels the forecast "OFFLINE (synthetic, NOT a forecast)". Never present this as real weather in the video.

## Shot checklist

- [ ] Header badges show the live Nemotron model name, not "offline stub".
- [ ] The SOP BLOCKED panel is readable for at least 3 s.
- [ ] Hardware clip is one unbroken take of at least 60 s (75 s is safer) on the T1, with the nozzles, the empty battery bay and the folded props visible.
- [ ] The team field clip is labelled "manual flight" and is under 15 s; it is context, not the hardware minute.
- [ ] Every number spoken comes from `runs/<run_id>.json` or `eval/results/*.json`.
- [ ] Upload as **Public**, check that it plays logged out, and check it is under 3:00.
