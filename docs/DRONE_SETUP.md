# Connecting a drone

> **Our aircraft is the T1 10 L quad with a JIYI K3-A Pro**, a closed controller with no
> MAVLink. For it, read `docs/AIRFRAME_T1.md` (flight cards, `FP_DRONE=k3`) and
> `docs/BENCH_RIG.md` Option C (the hardware clip). This page is for drones with a PX4 or
> ArduPilot controller.

FieldPilot talks MAVLink through pymavlink, so it works with **PX4 and ArduPilot** alike.
It uploads a normal waypoint mission; the autopilot flies it; your RC transmitter overrides
everything at any time.

## 0. What you need to know about the aircraft

| Question | Where to look | Why |
|---|---|---|
| PX4 or ArduPilot? | QGroundControl / Mission Planner header | mode names and arming differ; auto-detected from the heartbeat |
| Weight class | scale | Drone Rules 2021 category (see SAFETY.md) |
| Battery | label, e.g. 4S 5200 mAh ≈ 77 Wh | `DroneSpec.battery_wh` in `fieldpilot/mission.py` |
| Free servo/relay output | autopilot outputs | payload channel `FP_PAYLOAD_SERVO` |
| Telemetry link | radio (SiK 433/915), Wi-Fi companion, or USB | `FP_MAVLINK_URL` |

"SLAM drone" usually means a companion computer (Jetson / Pi) running SLAM next to the
autopilot. FieldPilot does not need the SLAM stack; it needs a MAVLink endpoint. If the
companion runs MAVROS or mavlink-router, add a UDP output to the laptop.

## 1. Payload: a pump the mission can switch

Parts (~₹1,200): 12 V diaphragm pump, 1 L tank, flat-fan nozzle, 5 V relay module (or a
MOSFET board), silicone tube.

1. Relay signal → the autopilot's free PWM output (e.g. AUX 1 = servo 9 on ArduPilot).
2. Relay switches the pump from the flight battery through a fuse.
3. Mount nozzle pointing down, below the frame, away from props.
4. Test on the bench: set the output to 1900 µs → pump on, 1100 µs → off.
5. Put the channel and PWMs in `.env`: `FP_PAYLOAD_SERVO=9`, `FP_PAYLOAD_PWM_ON=1900`, `FP_PAYLOAD_PWM_OFF=1100`.

ArduPilot: set `SERVO9_FUNCTION=0` (so the mission's DO_SET_SERVO controls it).
PX4: map the output as a generic actuator (Actuators tab) so `DO_SET_SERVO`/`DO_SET_ACTUATOR` reaches it.

**Spray water for every demo flight.**

## 2. Tell FieldPilot where the plot is

1. Walk the plot corners with a phone GPS app; note the south-west corner.
2. Regenerate the field:
   ```
   python3 - <<'PY'
   import json; from datetime import date
   from fieldpilot.field import grid_field
   f = grid_field("Bennett plot", sw_corner=(LAT, LON), width_m=24, height_m=16, cols=3, rows=2,
                  crop="potato", variety="Potato_701", sowing_date=date(2026,10,20), max_alt_m=12)
   json.dump(f.to_dict(), open("examples/field_demo.json","w"), indent=2)
   PY
   ```
3. The home point is placed 4 m outside the south-west corner; stand there.
4. Also set the autopilot's own fence (ArduPilot `FENCE_ENABLE=1`, `FENCE_RADIUS`, `FENCE_ALT_MAX`;
   PX4 `GF_ACTION`, geofence in QGC) — a second, independent layer.

## 3. Rehearse in SITL first

PX4 — either a local PX4-Autopilot build (`make px4_sitl gz_x500`) or the community
headless image `jonasvautherin/px4-gazebo-headless` (see its README for the flag that sends
MAVLink to your host on UDP 14550). Then:
```
FP_DRONE=mavlink FP_MAVLINK_URL=udpin:0.0.0.0:14550 python3 -m uvicorn fieldpilot.api:app --port 8080
```
ArduPilot:
```
sim_vehicle.py -v ArduCopter --out=udp:127.0.0.1:14550 -l <lat>,<lon>,200,0
```
Run a demo proposal, Approve, watch it fly the lawnmower passes in the console and in
QGC/Mission Planner at the same time. Press ABORT mid-mission once; it must RTL.

## 4. Real aircraft

1. Props off: connect, confirm the console shows GPS fix 3 and battery %.
2. Props off: Approve a mission and check the relay clicks on at each zone and off after.
3. Props on, safety pilot holding the transmitter in a manual-capable mode switch position.
4. Approve. Hands on the sticks. The 1-minute video clip starts here.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no heartbeat` | wrong URL/port; telemetry radio baud; firewall on UDP 14550 |
| `battery level unknown` | enable battery monitor (`BATT_MONITOR` / PX4 power module) |
| `arming refused` | read the autopilot's pre-arm messages (shown in the console message line) |
| mission rejected | check the autopilot log; ArduPilot needs `FENCE`/`MIS_TOTAL` headroom for ~60 items |
