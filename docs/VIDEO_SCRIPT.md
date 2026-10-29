# Demo video: FieldPilot in 3:00

**Runtime target: 2:55.** The rule is under 3:00, and judges may stop watching at 3:00. Upload to
YouTube as Public. **Track: Physical AI.** The video must show the project working on its device,
say how Nebius Token Factory and NVIDIA Nemotron are used, and contain **at least one continuous
minute of the hardware operating**. Use no copyrighted music and no third-party logos.

Every number below comes from a saved file in this repository (see "Source" in each shot).
Simulated results carry the word SIMULATED on screen. Never present a simulated or rehearsal number
as a field result.

## The final cut (3:00, as published)

The shot list further down was the plan. This is what the published cut contains.

| Time | Scene | What is on screen | Source of every number |
|---|---|---|---|
| 0:00–0:11 | Hook | Our T1 spraying, real footage labelled *FieldPilot drone run* | DJI Agriculture annual report 2025 (600,000 drones) |
| 0:11–0:27 | The problem | Pilot and drone footage with number cards: 8–9 min per battery, about 2 h to charge, 90 % of Drone Didis in Madhya Pradesh at a loss | Feminism in India, 4 Jun 2026; Dainik Bhaskar via Navbharat Live, 15 Sep 2025 |
| 0:27–0:40 | How it works | Animated flow: bookings → Nemotron 3.5 Lightning → day planner → brief; inspect (Super VL + gate) → decide (Nemotron 3 Super, WOFOST, Tavily) → checks → flight card or MAVLink | Architecture in [README](../README.md) |
| 0:40–0:54 | Read | FieldPilot.exe desk on the laptop, live: 7 demo bookings read on Token Factory; the bigha flag | Live desk run, 29 Oct 2026 |
| 0:54–1:16 | Plan my day | Live timeline for 30 Oct 2026, comparison table, bars, Hindi brief, AI cost | Desk on screen: 6 vs 4 farms in window, 0 vs 5 sorties outside it, 1.8 vs 39.4 L left over, 12 vs 15 battery cycles; 8 Nemotron calls, 4,572 tokens, about $0.00045 |
| 1:16–1:27 | At scale (SIMULATED) | Benchmark bars | `eval/results/efficiency_2026-10-30.json` |
| 1:27–1:52 | At the farm | Field console, live: trace, gate sends A2 and B2 to a human, she confirms, twin says only B2 pays, the weather check refuses to fly (86 % rain within 2 h) | Live console run, 29 Oct 2026, example photos (synthetic), drone simulator |
| 1:52–2:30 | Hardware | Our T1 (real footage, FieldPilot drone runs), then the flight card the pilot gets: A2 · B2, 4.0 m, 4.5 m/s, 3.0 m AGL, 1.33 L/mu, load 1.6 L | [`docs/video/flight_card_M-A6E6CF.html`](video/flight_card_M-A6E6CF.html) (rehearsal run) |
| 2:30–2:44 | Proof (SIMULATED) | K3-A Pro twin, field-day SprayLog map, ArduPilot SITL | [`docs/video/k3twin_150x120m.md`](video/k3twin_150x120m.md); `runs/fieldday/2026-10-31/`; `runs/sitl/20261028T180419Z/` |
| 2:44–3:00 | Close | Nemotron alone: 11 bad calls of 18; FieldPilot: 0; title | `eval/results/grounding_2026-10-29.json` |

Music is original, synthesised for the video. The voice is a synthetic voice (Kokoro, Apache-2.0).
The drone footage is our T1 on FieldPilot prototype runs, labelled *FieldPilot drone run* on screen.

## The story in one line

*Every spray-drone app plans the flight. Nobody plans the operator's day. FieldPilot does it with
NVIDIA Nemotron, on the drone she already owns.*

## Shot list

The voice-over (VO) is about 330 words at a calm 140 words a minute. The hardware minute is mostly
silent so the pump can be heard. On-screen text (OST) is a lower-third, white on a dark band, held at
least 3 s.

### 1 · Hook, 0:00–0:12

| | |
|---|---|
| Picture | Our T1 spraying a real field: `media/T1_field_manual.mp4`, 0:03–0:15 |
| OST | *Our drone: T1 10 L quad · JIYI K3-A Pro · FieldPilot drone run* |
| VO | "Six hundred thousand spray drones work farms today. Every one comes with an app that plans the flight. None of them plans the operator's day." |
| Source | DJI Agriculture annual report 2025 (about 600,000 drones in use) |

### 2 · The problem, 0:12–0:27

| | |
|---|---|
| Picture | Three plain number cards on black, 4 s each, then the T1's battery bay |
| OST | **8–9 min** of spraying per battery · **about 2 h** to recharge · **90 %** of Drone Didis in Madhya Pradesh at a loss |
| VO | "In India, a Drone Didi gets eight or nine minutes of spraying per battery, then waits two hours to charge. Jobs come in the order the phone rang, and sorties fly in noon wind, where the spray drifts. In one state, ninety percent of these operators are losing money. The problem isn't the drone. It's the day." |
| Source | Feminism in India ground report, 4 Jun 2026; Dainik Bhaskar via Navbharat Live, 15 Sep 2025 |

### 3 · Nemotron reads the bookings, 0:27–0:47

| | |
|---|---|
| Picture | FieldPilot.exe, **operator desk**, India demo: Hindi and Hinglish WhatsApp bookings → **Read with Nemotron** → job cards appear, one flagged *bigha taken as 2529 m² · confirm* |
| OST | *Nemotron 3.5 Lightning on Nebius Token Factory · schema-constrained JSON*, then *Code checks every number the model reads* |
| VO | "Nemotron 3.5 Lightning on Nebius Token Factory reads every booking, from Hindi voice notes to local units like the bigha. Then code checks every number it pulled out. If the farmer never said it, it goes on the call list, not into the plan." |
| Source | `runs/day_2026-10-30_20261029T085856Z.json` (7 bookings, live) |

### 4 · Plan my day, 0:47–1:05

| | |
|---|---|
| Picture | **Plan my day** → the timeline: green spray windows per farm, sorties on battery sets 1 and 2, the charger queue → the comparison panel → **Read aloud** plays 3 s of the Hindi brief |
| OST | *Live forecast · India 2023 drone SOP · 2 packs, 1 charger*, then *Same bookings, same forecast, same batteries* |
| VO | "Plan my day schedules every farm against the live forecast, India's drone rules and the batteries she actually owns. Same bookings, same weather, same batteries: the common way flies ten sorties outside the legal window. FieldPilot flies none." |
| Source | Same file: 0 vs 10 sorties outside the window; 0.9 L vs 32.9 L of mix left over |

### 5 · At scale, 1:05–1:15

| | |
|---|---|
| Picture | `eval/results/efficiency_2026-10-30.png`, then one number card |
| OST | *5 countries · 15 days · 105 bookings · live forecasts*, then **2.4× the useful hectares · 43 % fewer battery cycles · 67 % → 0 % of sorties in bad weather** |
| VO | "Across five countries and a hundred and five bookings: two point four times the useful hectares, on forty-three percent fewer battery cycles, and no sorties in bad weather." |
| Source | `eval/results/efficiency_2026-10-30.json`: effective ha 74.3 → 175.3, battery cycles 235 → 133, out-of-window sorties 67.2 % → 0 % |

### 6 · On the farm: three Nemotron models and a calibrated gate, 1:15–1:30

| | |
|---|---|
| Picture | **Field console** `/` → **Run demo**: zone photos → the trace streams native tool calls → the map: healthy zones green, A2 and B2 orange *review* → 2 s of the Tavily registry row from `docs/TAVILY.md` (ppqs.gov.in) |
| OST | *Nemotron 3 Super: the tool loop · Nemotron 3.5 Super VL: the photos*, then *Gate calibrated on 150 labelled leaf photos*, then *Tavily: the registered dose, from the government site* |
| VO | "On the farm, Nemotron 3 Super runs the tools and Super VL reads the photos. A conformal gate, calibrated on a hundred and fifty labelled leaves, lets the healthy zones through and sends every diseased one to the operator. Tavily checks the dose against the government registry." |
| Source | `eval/results/calibration_2026-10-29.md` (healthy vs diseased right 98 %); `docs/TAVILY.md` |

### 7 · The hardware minute, 1:30–2:35 (one continuous take, 65 s, no cuts)

| | |
|---|---|
| Picture | Wide shot of **our T1** on the bench: tank, 6 mm line and four nozzles visible, battery bay empty, props folded. Picture-in-picture, top right: the console map flying the approved mission. The pump sprays only on the treated zones' passes, the flow follows the simulated speed and stops on each turn. At about 2:25, press **ABORT**: the spray stops at once |
| OST (4 s each, spaced out) | *Aircraft position: simulated · pump, tank and nozzles: our T1, real* → *Flow follows ground speed and stops below 0.5 m/s, the K3-A Pro's own law* → *Only the zones the crop twin says are worth spraying* → *ABORT: pump off* |
| VO (sparse) | At 1:32: "This is our drone's own spray system. The flight is simulated; the pump is real." At 1:55: "It sprays only the zones that pay for it, at the flow our controller would use at that speed, and stops on every turn." At 2:25: "And the abort cuts it instantly." |
| Source | `docs/BENCH_RIG.md`; the bench log `runs/bench_*.json` from this take |

### 8 · Proof that it flies, 2:35–2:47

| | |
|---|---|
| Picture | 3 s each: the **K3-A Pro flight card** (L/mu, 4 m spacing) → `/sims`, the *K3-A Pro twin* result → `/ops`, **Firmware: ArduPilot SITL** tab → `/ops`, the field-day map with drift compensation on and off |
| OST | *SIMULATED · K3-A Pro twin: the tank runs dry within 3.9 m of the planned cut*, then *SIMULATED · unmodified ArduCopter: 1094 s flown vs 1103 s planned, every zone within 1 % of its dose*, then *SIMULATED · drift compensation: worst zone coverage 85 % → 96 %* |
| VO | "Our controller is closed, so the pilot gets a flight card for each sortie, and a twin of the controller flies it. On open firmware, unmodified ArduPilot flies our plan to within one percent." |
| Source | `docs/K3_TWIN.md`; `runs/sitl/` and `docs/FIRMWARE.md`; `runs/fieldday/2026-10-31/FIELD_DAY_REPORT.md` |

### 9 · Close, 2:47–2:55

| | |
|---|---|
| Picture | Title card: **FieldPilot** · *Future Agri* · `github.com/Aryan221224/fieldpilot`, with a small line *NVIDIA Nemotron on Nebius Token Factory · Tavily · WOFOST · ArduPilot* |
| OST | *Asked alone, Nemotron would have sprayed in 11 of 18 situations where it should not. FieldPilot: 0.* |
| VO | "Asked alone, Nemotron would have sprayed in eleven of eighteen situations where it shouldn't. FieldPilot: zero. Every battery, every litre, where the crop needs it." |
| Source | `eval/results/grounding_2026-10-29.json` (7 money-losing and 4 unsafe situations, all distinct; FieldPilot 0) |

## How to record it (one afternoon)

1. **Screens:** record with OBS at 1920×1080, 30 fps. Open `dist\FieldPilot\FieldPilot.exe`. Close every other window and turn off notifications.
2. **Desk take:** load the India demo, then press **Read with Nemotron**, **Plan my day** and **Read aloud**. Record one take and cut it later. Record Brazil once for B-roll.
3. **Console take:** press **Run demo** on `/`, let the trace stream, then show the map and the proposal.
4. **Hardware take:**
   - Put the phone on a tripod, in landscape, in 4K if possible.
   - Light the bench from the side so the spray shows; a dark background behind the nozzles helps.
   - Start `scripts\t1_console.bat` and `scripts\t1_bench.bat COM6` ([BENCH_RIG.md](BENCH_RIG.md)), then approve the mission.
   - Film **at least 70 s without stopping**, and press ABORT near the end.
   - Record OBS at the same time, for the picture-in-picture.
5. **Proof take:** record `/sims` (K3-A Pro twin), the Firmware and Field day tabs on `/ops`, and the flight card.
6. **Voice:** record the VO separately in a quiet room, with the phone 20 cm from your mouth, then lay it over the picture.
7. **Edit:** cut to the timings above, keep the 65 s hardware take uncut, and export at 1080p.
8. **Upload:**
   - Post it on YouTube as **Public**, titled *FieldPilot: Nemotron plans a spray-drone operator's day (Physical AI)*.
   - Paste the description below.
   - Open it logged out to check it plays, and that it runs under 3:00.

## If the bench rig cannot be filmed today

The hardware minute is required. From strongest to weakest:

1. **The bench rig above:** FieldPilot drives the real pump. This is the preferred option.
2. **The T1 flying a FieldPilot flight card** over your own field.
   - The pilot enters the card's zone corners, 4 m spacing, speed and L/mu dose in the JIYI app, loads the litres on the card, and flies it.
   - Film one continuous minute of the drone spraying the planned zones.
   - Fly only with your licensed pilot, in legal weather and airspace, with props clear of people.
3. **The T1's pump on the bench, run from the JIYI app's pump test**, with the console beside it and labelled honestly. This is the weakest, because FieldPilot is not commanding the hardware.

## YouTube description (paste)

FieldPilot by Future Agri plans a spray-drone operator's whole day: which farm first, which battery
set flies it, whether the weather lets the spray land, and how much product each refill needs. NVIDIA
Nemotron 3.5 Lightning on Nebius Token Factory reads bookings in Hindi and Portuguese. Nemotron 3 Super
runs the field agent's tools. Nemotron 3.5 Super VL reads the crop photos behind a calibrated conformal
gate. Tavily checks the dose against the government registry. Hardware: our T1 10 L quad's own pump, tank
and nozzles (aircraft position simulated, battery out, props off). Simulated results (K3-A Pro twin,
ArduCopter SITL, field day) are labelled as such. Code: github.com/Aryan221224/fieldpilot

## Final checks before upload

- [ ] Under 3:00, plays logged out, Public.
- [ ] The hardware take is one unbroken shot of at least 60 s, with the nozzles, the empty battery bay and the folded props visible.
- [ ] "Nebius Token Factory" and "NVIDIA Nemotron" are spoken, and the model names appear on screen.
- [ ] Every spoken number matches its source file above, and every simulated result says SIMULATED on screen.
- [ ] No music without a licence, and no third-party logos.
