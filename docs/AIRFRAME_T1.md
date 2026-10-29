# The project airframe: T1-class 10 L quad with a JIYI K3-A Pro

FieldPilot plans, checks and demonstrates for one real aircraft: our 10 L agricultural spray
quad. Every number the software uses for it comes from its specification sheet, or is derived
from it in `fieldpilot/airframe.py`, which names each assumption. Print the profile with:

```
python -m fieldpilot.cli airframe            # add --json for everything
```

## The aircraft (specification sheet)

| | |
|---|---|
| Tank · rotors · nozzles | 10 L · 4 · 4 |
| Wheelbase · unfolded · folded | 1300 mm · 920 × 920 × 480 mm · 582 × 582 × 480 mm |
| Weight without battery · standard take-off · MTOW | 10 kg · 24 kg · 28 kg |
| Hover | 12 min at 24 kg, 28 min at 14 kg |
| Spray width · pump (system) | 4 m · 3.5 L/min; flow follows ground speed, pump stops below 0.5 m/s |
| Speed | 7 m/s operating, 10 m/s maximum |
| Radio range · operating temperature | 1000 m unobstructed · −10 to +40 °C |
| Kit | 2 × 16000 mAh Li-Po, 1 × PC1080 12S charger (30 min advertised), 24 V 0.8 MPa pump |
| Propulsion | 4 × 120 KV motors with ESCs, 34.7-inch folding propellers |
| Flight controller | **JIYI K3-A Pro**: a closed agricultural controller, flown from JIYI's app |

## What FieldPilot derives, and how well it matches the sheet

| Quantity | Value | How |
|---|---|---|
| Pack energy | 710 Wh, 4.0 kg | 12S assumed from the kit's 12S charger; 24 − 10 − 10 kg |
| Hover power | 3019 W at 24 kg, 1345 W at 14 kg | fitted to 12 min at 24 kg; scaled by mass^1.5 |
| **Check:** hover at 14 kg | **26.9 min predicted vs 28 min on the sheet** | not fitted, a prediction |
| Energy per sortie | follows the tank emptying | the planner charges each metre at the mass then on board |
| Flow needed | 2.16 L/min at 4.5 m/s, 20 L/ha, 4 m | the pump holds the rate up to 7.3 m/s |
| Work rate | **8.5 acres/h** predicted vs 10 acres/h on the brochure | sortie planner, 4 ha, swaps included, with braking/acceleration (1.5 m/s²) and a 1.5 m/s side-step at every line end, as the K3-A Pro twin flies it. The brochure's figure counts no line-end time |
| Take-off with a full tank | 24 kg: *small* under India's Drone Rules 2021 | at the 28 kg MTOW it would be *medium* (25–150 kg) |

The hover figure the sheet gives at 14 kg was not used to build the model, and the model
reproduces it within 4 %: the energy numbers in the benchmark are the aircraft's, not a generic
drone's. Work rate sits 15 % under the brochure because the planner now charges every line end
with braking, a side-step and acceleration; the K3-A Pro twin flies the same plan within 3–5 %
of the planned sortie time ([K3_TWIN.md](K3_TWIN.md)).

### What the kit as shipped really delivers

The sheet's 10 acres/h is the aircraft with unlimited charged packs. With the kit as shipped
(2 packs, 1 charger, planned 45 min a charge), in India's 9-hour SOP spray window:

| Kit | Full-tank sorties a day | ha a day | Aircraft in the air |
|---|---|---|---|
| 2 packs, 1 charger | 13 | 6.5 | 12 % of the window |
| 4 packs, 2 chargers | 26 | 13.0 | 25 % |

The charger, not the drone, sets the day. That is why the day planner schedules batteries
and chargers together with the weather, and why the efficiency benchmark counts effective
hectares per battery cycle.

## How FieldPilot runs on it

The K3-A Pro has no open command link (no MAVLink, no SDK), so FieldPilot cannot upload a
mission to it. FieldPilot does everything up to the take-off decision and hands the pilot the
mission:

```
FP_DRONE=k3 python -m uvicorn fieldpilot.api:app --port 8080
```

1. Scan, crop twin, label check and rule pack decide **which zones** get sprayed.
2. The sortie planner cuts the job into **10 L sorties** at the exact metre the tank or the
   pack (energy home included) runs out, and loads each refill to the litre.
3. **Approve**, sortie by sortie, re-checks the live weather, the rule pack, the airframe
   envelope (pump flow, speed, take-off mass, temperature) and the pilot's checklist.
4. Approve then **issues a flight card** (`/api/runs/<id>/card`, saved as
   `runs/card_<mission>.html`) and a **KML** (`?fmt=kml`) for the JIYI app:
   - each zone's boundary corners and route heading;
   - line spacing 4 m, speed 4.5 m/s, height, and dose as **L/mu** (JIYI's unit) and L/ha;
   - the water (and product) to load for that sortie. Loaded to the litre, the tank runs dry
     where the plan cuts the sortie, and the K3-A Pro's own breakpoint resume continues from
     there.
5. The pilot flies it from the JIYI app, holding the remote. **One-key return on the remote
   is the abort.**

If the controller is ever swapped for an ArduPilot one, the same plan uploads directly
(`FP_DRONE=mavlink`, `FP_SPRAY_CMD=sprayer`). `cli airframe` prints the starting parameters:
sprayer `SERVOn_FUNCTION=22`, `SPRAY_PUMP_RATE=13.7` % per m/s and `SPRAY_SPEED_MIN=50` cm/s
(the same speed law as the K3-A Pro), plus battery failsafes for a 12S 16 Ah pack and a fence.

## The hardware clip on this aircraft

See `docs/BENCH_RIG.md`, **Option C**. The drone's own pump, tank and four nozzles spray real
water while the flight battery is disconnected and the props are off. The pump is driven by
FieldPilot through an Arduino, following the approved mission flown in ArduPilot SITL, with
the K3-A Pro's speed law reproduced (`--flow-law t1_10l`).

## Before it flies for real

- Props: 34.7-inch blades on a 24 kg aircraft. Nobody within the geofence, the pilot well back.
- Category: small at 24 kg. Do not load past 25 kg all-up unless the paperwork covers medium.
- Pilot certificate, UIN and insurance as the Drone Rules 2021 require; Digital Sky zone check.
- Water only until the product and dose are approved for drone application (CIB&RC).
- Battery: confirm the cell count on the pack label. If it is not 12S, change `CELLS` in
  `fieldpilot/airframe.py` and every number above follows.
