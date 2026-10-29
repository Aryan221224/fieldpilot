# Safety

## Layers (outer to inner)

1. **Safety pilot** with the RC transmitter can take over at any time. Non-negotiable.
2. **Autopilot geofence and RTL** configured on the flight controller itself.
3. **FieldPilot safety checks** (`fieldpilot/mission.py`): geofence with 0.5 m margin, altitude
   band, range from pilot (visual line of sight, 120 m default), energy with 35 % reserve,
   flight-time limit, tank volume, wind ≤ 5 m/s, gusts ≤ 7 m/s, rain probability ≤ 40 % in 2 h,
   temperature ≤ 35 °C, vehicle preflight (3D fix, battery ≥ 60 %, pre-arm healthy, disarmed).
4. **Re-check at approval**: weather and preflight are fetched again when Approve is pressed.
5. **Policy layer** (`fieldpilot/tools.py`): the model cannot treat a zone flagged for review,
   cannot choose a plan the twin did not recommend, and has no tool that moves the aircraft.
6. **ABORT** button: one click, no confirmation, commands RTL.

## Before any flight on campus

- [ ] Written permission from the university (estate office / department) for the ground and time.
- [ ] Check the **Digital Sky airspace map** for the campus location. Greater Noida can fall in a
      yellow zone (airport proximity); yellow and red zones need prior permission.
- [ ] Know the aircraft's weight category under **Drone Rules 2021** (nano ≤ 250 g, micro ≤ 2 kg,
      small ≤ 25 kg, medium 25–150 kg, by all-up weight). Our T1 is 24 kg with a full 10 L tank
      (small) and 28 kg at its MTOW (medium): FieldPilot's `takeoff_mass` check reports the
      category for every planned load. Per the Rules, nano drones, micro drones for non-commercial use, and
      authorised R&D entities do not need a remote pilot certificate; confirm the registration
      (UIN) and insurance position for your aircraft with the drone's owner (the college).
- [ ] Spray **water only**. Drone application of pesticides in India falls under CIB&RC approvals
      and the Ministry of Agriculture SOP; an interim approval was reported valid to 18 Apr 2025 —
      check the current position before ever loading a chemical.
- [ ] Nobody inside the geofence during flight; mark it with tape.
- [ ] Battery charged and balanced; spare battery; fire bag.
- [ ] SITL rehearsal passed with the same field file (DRONE_SETUP.md §3).

## The T1 specifically

- 34.7-inch propellers on a 24 kg aircraft: the bench clip is filmed with the **flight battery
  out and the props folded or removed**. Never with the battery in.
- The K3-A Pro cannot be commanded by FieldPilot. In real flight the pilot flies from the JIYI
  app with a FieldPilot flight card; **one-key return on the remote is the abort**.
- Airframe envelope checks at every approval: pump flow (3.5 L/min), spraying speed (7 m/s),
  take-off mass (28 kg MTOW) and temperature (−10 to +40 °C).

## During the demo

- Fly at the field's treat altitude (3 m default), slow spray speed (2 m/s).
- If anything surprises you: ABORT, then take manual control if needed.
- Record the console screen and the aircraft at the same time — the video needs both.
