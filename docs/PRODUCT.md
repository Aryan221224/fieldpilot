# FieldPilot: the efficiency layer for agricultural spray drones

What is new compared with existing drone software, and who uses it: [NOVELTY.md](NOVELTY.md).
The full field setup, its costs and the law: [DEPLOYMENT.md](DEPLOYMENT.md).

## The problem: drones are everywhere, but the work around them is inefficient

Spray drones have become a mainstream farm machine. DJI alone reports about 600,000
agricultural drones in use worldwide. They work for rice farmers in the Mekong and
Uttar Pradesh, soybean and coffee growers in Brazil, and vegetable growers in Kenya and
California. The aircraft is efficient: low water volume, no soil compaction, no one walking
through the crop with a knapsack. **How it is used is not.** The same pattern shows up
wherever operators sell spraying as a service.

| Where the efficiency is lost | What happens today |
|---|---|
| **Battery cycles** | A 10 L-class drone sprays for about 8–10 minutes per battery set, and the set needs one to two hours to cool and recharge. Battery sets × chargers, not the aircraft, cap the hectares in a day. Jobs are taken in booking order, so batteries run flat at the wrong farm and the drone lands mid-field. |
| **Weather** | Wind, temperature and humidity decide whether spray lands on the leaf or drifts and evaporates. Some jurisdictions make this law, for example India's 2023 drone SOP (wind < 3 m/s, ≤ 35 °C, RH ≥ 50 %). Elsewhere it is label and agronomy guidance (wind of about 1–4.5 m/s, Delta T 2–10). Operators under pressure fly through the bad hours to cover acres. |
| **Area** | The whole field is sprayed because nobody has decided which zones need it. Every unneeded hectare costs chemical, water, a share of a battery cycle and money. |
| **Tank mix** | Product is mixed in full-tank batches. The remainder after the last sortie has to be stored, re-sprayed or dumped. Label rates assume 200–300 L/ha of water and a drone uses 10–25, so "mix as per label" in a drone tank under-doses by an order of magnitude. |
| **Flight path** | Passes run along whichever way the field was walked, the vehicle parks wherever the road meets the field, and on a long plot every refill dead-heads its whole length. |
| **Bookings** | Bookings arrive as calls, chat messages and voice notes, in the farmer's language and local area unit: bigha, mu, rai, alqueire, feddan. |

### A case study: India's Namo Drone Didi (2026 ground reports)

The Indian government has put spray drones with women's self-help groups under a ₹1,261
crore scheme. The aircraft is subsidised. Ground reporting in 2026 shows the efficiency
problems above turning into business failure:

- About 90 % of Madhya Pradesh's Drone Didis report a loss, and only about 10 % earned more
  than ₹1 lakh in their first year.
- A battery gives 16–17 minutes, of which only 8–9 are spraying. It then needs nearly two
  hours to recharge after about 30 minutes of cooling. A spare battery costs ₹40,000–50,000.
- Short flights mean landing mid-job to swap, which irritates farmers.
- 42.7 % report transport problems, and the operator alone decides which village's fields
  come first.
- Some operators fly in wind and heat the SOP forbids, to cover more acreage.

The same constraints apply to every battery-powered spray drone. India is where the
problem is most visible and best documented, not where it ends.

## What FieldPilot changes, and how each gain is measured

| Inefficiency | FieldPilot | Measured as | Code |
|---|---|---|---|
| Unneeded hectares | A phone or drone scan with a conformal gate, then a WOFOST crop twin, decides which zones pay to spray | ha sprayed beyond need | `perception.py`, `twin.py` |
| Out-of-window sorties | Every sortie is scheduled inside the live hourly forecast window of the operator's **rule pack**: India SOP-2023, or drift and Delta T good practice anywhere else | % of sorties outside the window | `rules.py`, `dayplan.py` |
| Battery cycles | A day plan across all bookings: battery sets, chargers, charge time, drive time and weather windows, scheduled together, with the most value per hour next | effective ha per battery cycle | `dayplan.py` |
| Mid-job surprises | The route is cut into sorties at the exact metre the tank or battery runs out, then resumed. Loads are balanced, so there is no near-empty last flight. The best take-off point on the field edge is chosen. Each sortie is approved separately. | flight minutes per ha; resume points | `sorties.py`, `mission.py` |
| Leftover mix and dose errors | Each refill is mixed to the litre it will spray (the last one carries a 0.1 L + 3 % margin so the route completes), and product per refill is the per-hectare dose ÷ the drone's L/ha | L of mixed product left over | `sorties.py`, `sop.py` |
| Wrong product or dose | Tavily searches the national registry (CIB&RC in India) and every number must appear verbatim in the cited page | label verdict + citation | `label.py` |
| Language and units | Nemotron reads bookings in any language. Area units are converted in code, and any number the farmer didn't write is refused. | bookings needing a call-back | `intake.py` |
| Briefing the crew | Nemotron writes the day's brief in the operator's language. Every number in it is checked against the plan. | template fallbacks | `desk.py` |

## The benchmark

`eval/efficiency_bench.py` runs operator days in five regions on live Open-Meteo forecasts:
India (Uttar Pradesh), Brazil (Paraná), USA (Central Valley), Kenya (Nakuru) and Vietnam
(Mekong). Each region has its own drone class, battery kit, time zone and rule pack; the 10 L
regions fly **our own aircraft's profile** (the T1 10 L quad, `docs/AIRFRAME_T1.md`), the
large-farm regions a 40 L class drone. Every day is planned three ways on the **same bookings,
forecast and batteries**:

* **baseline**: booking order, whole field, full-tank batches, corner take-off, fly whenever
  a charged battery is available;
* **FieldPilot without scans**: scheduling, weather windows and sortie planning only;
* **FieldPilot with scans**: plus zone-level spraying. The share of scanned fields and the
  fraction needing spray are stated assumptions in the result file.

"Effective" hectares count only coverage flown inside the spray window, because product
sprayed outside it drifts, evaporates, or breaks the law where the pack is binding. The
numbers in the submission come from `eval/results/efficiency_*.json`, nowhere else.

## Why Nemotron, and where it is kept away from numbers

Language is the hard part of this business: bookings in many languages and units, and a
brief a crew can listen to while loading the vehicle. On the farm, it is the tool-calling
loop that ties scan, crop twin, label and rules together. Nemotron on Nebius Token Factory
does that work. Numbers, law, physics and the motors are code. Wherever a model's number
could reach a decision (an area, a dose, a label row, a time in the brief), it is checked
verbatim against its source.

## Sources

- DJI Agriculture, Agricultural Drone Industry annual report 2025 (≈600,000 drones in use): https://www.dji.com/global/media-center/announcements/dji-agricultural-annual-report-2025 · coverage: https://www.queenslandcountrylife.com.au/story/9239054/dji-agriculture-600000-drones-deployed-saving-global-water/
- Ground report, Madhya Pradesh (Feminism in India, 4 Jun 2026): https://feminisminindia.com/2026/06/04/madhya-pradesh-namo-drone-didi-limits-battery/
- "90 % of Drone Didis in loss" (NavBharat Live, citing Dainik Bhaskar): https://navbharatlive.com/business/90-percent-of-women-associated-with-namo-drone-didi-yojana-are-in-loss-1355652.html
- As told to Parliament, 24 Mar 2026 (Down To Earth): https://www.downtoearth.org.in/governance/as-told-to-parliament-march-24-2026-centre-clears-rs-1261-crore-namo-drone-didi-scheme-for-women-shgs-in-farm-services
- Low water volume and drone spraying (Global Agriculture, 24 Feb 2025): https://www.global-agriculture.com/india-region/low-water-high-risk-why-drone-based-pesticide-spraying-raises-concerns/
- India crop-specific drone SOP, MoA&FW, 24 Feb 2023: https://croplifeindia.org/wp-content/uploads/2024/01/TTC_SOP_2023-4.pdf
- Delta T and wind guidance: Australian GRDC spray-application guidance (Delta T 2–8 ideal, > 10 unsuitable); common label and extension drift guidance (spray in roughly 3–10 mph wind, not in calm inversions)
