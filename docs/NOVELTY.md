# What is new, why it is needed, and who will use it

## The claim

**Every spray-drone app plans the flight. Nobody plans the operator's day.**
FieldPilot plans it: which farm first, which battery set flies it, whether the weather lets
the spray land (and the law allows it), and how much water and product go into each refill.
It does this for the drone the operator already owns, closed controllers included.

## The gap, product by product

| Layer | What exists today | What it does not do |
|---|---|---|
| **The flight** | Each controller's own app plans a route over one field: DJI's Agras apps, XAG, and on our aircraft the JIYI K3-A Pro app (AB lines, polygon routes, route memory, spray-area calculation) | One field at a time. No order across farms, no battery-set or charger plan, no spray-weather window, no legal check |
| **Fleet records** | DJI's agriculture platform: operational summaries by equipment, team and location, flight-path review, a device dashboard | Reports what happened. It does not decide what should happen next |
| **Bookings** | Spray-as-a-service from input companies (FMC India, Coromandel with Mahindra Krish-e, DeHaat with Drone Destination); district schemes where farmers "book drones like cabs"; WhatsApp | Takes the order. The day's order, the batteries, the weather and the dose are left to the operator |
| **The gap** | The operator's day: which farm, in which order, with which battery, inside which legal spray window, with how much product per refill. Today it is worked out in the operator's head over WhatsApp | |

That gap is where the money goes. In India's ₹1,261 crore Namo Drone Didi scheme the aircraft
is subsidised, yet 2026 ground reports say about 90 % of Drone Didis in Madhya Pradesh report a
loss. The causes are short flights (8–9 spraying minutes per battery, then about two hours to
recharge), scattered bookings, landing mid-field to swap packs, and flying in wind and heat the
SOP forbids just to cover acres. None of those is a flight-planning problem. All of them are
day-planning problems.

## What is new: four things together

1. **It plans around what actually limits the day.** Battery sets × chargers × legal spray
   windows × drive time, in one schedule, on the live hourly forecast at every farm. On our own
   T1 with the kit as shipped (2 packs, 1 charger), the aircraft is airborne for **12 %** of
   India's 9-hour spray window: 13 sorties, 6.5 ha. The charger sets the day, not the drone
   (`python -m fieldpilot.cli airframe`).
2. **The law and the label are checked in code, at take-off.**
   - India's 2023 drone SOP is encoded as rules, each citing its clause. Outside India, a drift
     and Delta T good-practice pack applies instead.
   - The product and dose are checked against the national registry (CIB&RC) through Tavily:
     search, extract, then a crawl of the regulator's own site. Every number has to appear word
     for word in the cited page.
   - The tank-mix trap is caught. A label's dose assumes about 500 L of water per hectare; a
     drone sprays 20. Mixing "as per label dilution" under-doses about 20×. FieldPilot doses per
     hectare and divides by the drone's water volume.
3. **It runs on the drone the operator already owns, closed controllers included.** The K3-A
   Pro has no SDK, so FieldPilot hands the pilot a flight card and a KML, in the app's own
   units (L/mu), one per approved sortie. Each sortie is loaded to the litre, so the tank runs
   dry exactly where the plan cuts it and the controller's own breakpoint resume takes over.
   Open controllers (PX4, ArduPilot) get a direct MAVLink upload instead. That path has been
   flown end to end on unmodified ArduCopter in its simulator, on a T1 model
   ([FIRMWARE.md](FIRMWARE.md)). Nothing on the aircraft changes.
4. **It closes the loop at the field edge.** Two small ESP32 devices
   ([SYSTEMS.md](SYSTEMS.md)) close it:
   - **FieldMast** measures the wind and humidity where the spray falls. Its NO-GO blocks
     approval, even against a forecast that says go.
   - **Drift compensation.** FieldPilot moves the spray lines upwind by the wind the mast
     measures, so the deposit lands on the zone. On the simulated field day, the windiest farm's
     lowest zone coverage rose from 86.8 % to 96.2 %. In ArduCopter's own flight, the share
     deposited on target rose from 96.5–98.4 % to 99.0–99.6 %. **Both results are simulated.**
   - **SprayLog** records litres and positions on the drone without touching the controller.
     FieldPilot turns them into litres per zone against the plan, and flags spray outside the
     zones: an as-applied record the operator can show.

NVIDIA Nemotron on Nebius Token Factory works at the two language edges of the day: it reads
bookings in any language and unit, and it writes the crew's brief in the operator's language.
It also orchestrates the field agent's tools. It never supplies a number. Every area, dose and
time it produces is checked against its source, and code decides.

## The positive change, and how it is measured

Each benchmark day is planned twice, on the same bookings, forecast and batteries: once the
common way, once by FieldPilot (`eval/efficiency_bench.py`, five countries, live forecasts).
The change shows up in four places:

| Change | Measured as |
|---|---|
| Less chemical sprayed | hectares sprayed beyond need; litres of mixed product left over |
| Spray that lands, not drifts onto neighbours and water | % of sorties flown outside the spray-weather window |
| More farms served on the same kit | effective hectares per battery cycle; farms fully served in the window |
| An operator who can stay in business | the same three numbers, per day, on her own kit |

The latest numbers are in `docs/SUBMISSION.md`, and they come only from saved result files.

## Who uses it, on an ordinary day

A Drone Didi with one T1, two packs, one charger and an electric three-wheeler:

| When | What she does | What FieldPilot does |
|---|---|---|
| 06:30 | Pastes the night's WhatsApp bookings and voice-note transcripts | Nemotron reads them: farmer, village, crop, "dhai bigha" converted to hectares in code. Anything the farmer did not actually say is flagged to confirm on the call |
| 06:35 | Taps **Plan my day** | Live forecast at every farm. Out come a timeline inside the legal spray windows, a charging schedule, a call list for farms that move to tomorrow, and a brief read aloud in Hindi |
| At each farm | Optional phone scan of the field; approves each sortie | Zones a spray pays for; product and dose verified; a flight card per sortie for the JIYI app; weather and rules re-checked at the moment of approval |
| Evening | Prints or forwards the paperwork | The SOP spray record, the 24-hour Gram Panchayat notice for tomorrow's farms, and a farmer slip with the do-not-harvest-before date |

What she needs is a phone with a browser and mobile data. As of 28 Feb 2026, 6,32,392 of
India's 6,44,131 villages had 4G coverage. The full physical setup is in
[DEPLOYMENT.md](DEPLOYMENT.md).

## Why it travels

The same engine runs a 10 L drone in Uttar Pradesh under a binding SOP and a 40 L drone in
Paraná under Delta T good practice. Only the configuration differs: rule pack, units,
language, drone class and battery kit. The benchmark runs five countries for that reason.
About 600,000 agricultural drones are in use worldwide, and every one that runs on batteries
faces the same constraints.

## What it is not

- Not an autopilot. The aircraft's own controller flies; a human approves every sortie.
- Not a chatbot. The model never chooses a dose, an area or a take-off.
- Not proven at scale yet:
  - there has been no season-long pilot with an operator;
  - the perception gate is not yet calibrated on a season of labelled scans;
  - on the closed K3-A Pro the pilot types the card into the JIYI app by hand;
  - FieldMast, SprayLog and the ArduPilot path are tested in simulation and off-hardware, not
    yet in a field.

## Sources

- DJI Agriculture annual report 2025 (≈600,000 drones in use): https://www.dji.com/global/media-center/announcements/dji-agricultural-annual-report-2025
- DJI agriculture platform features: https://ag.dji.com/ag-platform
- JIYI K3-A Pro features (AB route, polygon routes, route memory): https://www.worldronemarket.com/product/jiyi-k3a-pro-flight-controller/
- Input-company spray services: https://ag.fmc.com/in/en/press-release/fmc-introduces-drone-spray-services-farmers-india · https://www.indianchemicalnews.com/technology/lite/coromandel-international-and-mahindra-krish-e-partner-to-provide-drone-spraying-services-for-farmers-24439 · https://inc42.com/buzz/dehaat-inks-pact-with-drone-destination-to-offer-agri-drone-solutions/
- District booking scheme: https://deccanchronicle.com/southern-states/andhra-pradesh/farmers-can-book-drones-like-cabs-collector-1935759
- Namo Drone Didi ground reports and scheme size: see [PRODUCT.md](PRODUCT.md#sources)
- 4G village coverage, Lok Sabha Unstarred Question 5480 (25 Mar 2026), Annexure III: https://usof.gov.in/upload/parliament_questions/LSPQ_5480/v11_Draft_LSPQ_5480_15233_25_03_2026.pdf
