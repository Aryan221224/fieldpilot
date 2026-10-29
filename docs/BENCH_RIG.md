# Bench rig — the Physical AI clip without flying

The Physical AI track needs **at least one minute of real hardware operating**.
This rig gets that minute with every propeller off and the motors electrically
dead, while the spray hardware does exactly what the approved mission tells it
to.

```
ArduPilot SITL  ── flies the approved mission, decides when the pump is on
      │  SERVO_OUTPUT_RAW (pump channel) + VFR_HUD (ground speed)
      ▼
python -m fieldpilot.bench  ── mirrors only that channel, 4 safety layers,
      │                         flow follows ground speed (--flow-law t1_10l)
      │  "P1600\n" over USB                        │  DO_SET_SERVO (pump channel only)
      ▼                                            ▼
Option C: OUR T1 DRONE's own pump, tank      Option A: a drone with an ArduPilot FC
and 4 nozzles; battery out, props off        Option B: Arduino + MOSFET + 12 V pump
```

**Use Option C.** Our aircraft is the T1 10 L quad with a JIYI K3-A Pro controller
(`docs/AIRFRAME_T1.md`). The K3-A Pro has no open link, so the bench drives the drone's own
24 V pump directly and leaves the controller out of the loop entirely.

**What the video must say on screen:** *"Aircraft position: simulated (ArduPilot
SITL). Spray hardware: real."* Both halves are true and the viewer can see which
is which. Do not cut it.

---

## Option C — our T1 drone: its own pump, tank and nozzles (use this)

The aircraft's real spray system, with the aircraft unable to fly: the flight battery is
unplugged, so the K3-A Pro, the ESCs and the motors have no power at all.

### 1. Make it safe

| | Step | Why |
|---|---|---|
| 1 | **Unplug the flight battery (XT90) and take it off the aircraft.** | No power to the controller, ESCs or motors. |
| 2 | **Fold or remove all four 34.7-inch propellers.** | Belt and braces, and visible on camera. |
| 3 | Unplug the pump's two-wire lead from the drone's pump driver; connect it to the bench circuit below. | The pump is driven by the bench, not the K3-A Pro. Plug it back afterwards. |
| 4 | Fuse the 24 V supply (5 A). | The pump is 0.8 MPa; a stall must blow a fuse, not a wire. |

### 2. Bench circuit (about ₹600 plus a 24 V supply)

| Part | Example |
|---|---|
| Arduino Nano or Uno | any clone |
| Logic-level N-MOSFET | IRLZ44N (55 V; about 0.2 W at a 3 A pump, no heatsink) |
| Flyback diode | 1N5822 (3 A, 40 V) across the pump, stripe to +24 V |
| Resistors | 220 Ω gate, 10 kΩ gate-to-ground |
| Supply | 24 V ≥ 5 A adapter, or a 6S Li-Po (22.2 V) |
| Fuse | 5 A inline blade fuse on +24 V |

Wiring: Arduino **D5 → 220 Ω → gate**, 10 kΩ gate → GND, source → GND (shared with the
Arduino and the supply −), drain → pump −, pump + → fuse → +24 V, diode across the pump.

Flash `firmware/pump_bridge/pump_bridge.ino` (default **FLOW** mode: 1100 µs off, 1900 µs
full, PWM duty in between). Put 3 L of water in the tank; a 75 s take uses about 1.5 L.

### 3. Check, then run (no SITL install needed)

The console's own simulator flies the approved mission in real time on the console map; the
bridge reads its spray state, zone and ground speed from the console and drives the pump.

```powershell
# rehearsal with no hardware: prints every pump change
scripts\t1_console.bat            :: window 1 - console at http://127.0.0.1:8080, simulator in real time
scripts\t1_bench.bat dry          :: window 2 - the bridge, T1 flow law, no hardware

# the real take
.\.venv\Scripts\python.exe -m fieldpilot.bench --check --sink serial:COM6
scripts\t1_bench.bat COM6         :: window 2 - the bridge driving the drone's pump
```

Then in the console: **Run demo** → **Approve** (give the written override the SOP asks for:
"water-only bench demonstration"). The demo plot (`examples/field_demo.json`, 60 × 40 m,
six 20 × 20 m zones) takes about 90 s to fly with two zones treated: a complete take.

ArduPilot SITL also works as the source (`--source tcp:127.0.0.1:5763`) if it is installed.

The bridge runs the pump at the share of 3.5 L/min that lays 20 L/ha at the simulated
aircraft's ground speed (about 62 % at 4.5 m/s) and stops it below 0.5 m/s, as the K3-A Pro
does. On camera the plume is full on the passes, fades in the turns and stops in transit.

### On-screen text for Option C

**"Aircraft position: simulated (ArduPilot SITL) · Spray system: our drone's own pump, tank and
nozzles · Battery out, props off"**

---

## Option A — a drone with an ArduPilot flight controller

Only for an ArduPilot controller (not our T1's K3-A Pro). The drone's own pump, own tank,
own nozzles. Takes 15 minutes to set up.

### 1. Make it safe — do all four

| | Step | Why |
|---|---|---|
| 1 | **Remove every propeller.** | Nothing else on this list matters if a prop is on. |
| 2 | **Leave the safety switch ON** (not pressed). | ArduPilot disables every output except those in `BRD_SAFETY_MASK`. |
| 3 | Set `BRD_SAFETY_MASK` to the pump channel only. | Pump on AUX1 = output 9 → `256`. Output *n* → `2^(n-1)`. |
| 4 | Do not arm. | The bench refuses an armed vehicle and stops if it arms. |

### 2. Configure the pump output (Mission Planner → Config → Full Parameter List)

Find the pump's output number *n* (usually AUX1 = 9 on a Pixhawk). Then:

| Parameter | Bench value | Note the original first |
|---|---|---|
| `SERVOn_FUNCTION` | `0` | If it is `22` (SprayerPump), ArduPilot only runs it armed and moving. Put `22` back afterwards. |
| `BRD_SAFETY_MASK` | `2^(n-1)` — `256` for output 9 | Put the original back afterwards. |
| `SERIAL0_PROTOCOL` | `2` (MAVLink 2) | Already 2 on any recent firmware. |

Close Mission Planner afterwards — it holds the USB port.

### 3. Check, then run

```powershell
cd C:\Users\aryan\Documents\FutureAgri\fieldpilot
..\.venv-fp\Scripts\python.exe -m fieldpilot.bench --check --sink mavlink:COM5 --channel 9
```

Every line must say `ok`. The check reads the parameters off the aircraft and
refuses a motor output, an armed vehicle, a safety mask with motors in it, or a
channel ArduPilot will not let DO_SET_SERVO drive. Find the COM port in Device
Manager → Ports.

Then, with SITL running (`docs/SITL_WINDOWS.md`) and the console up:

```powershell
# window 1 - the bridge
..\.venv-fp\Scripts\python.exe -m fieldpilot.bench --source tcp:127.0.0.1:5763 --sink mavlink:COM5 --channel 9
# window 2 - the flight, approved automatically because the vehicle is a simulator
..\.venv-fp\Scripts\python.exe -m fieldpilot.cli rehearse --sitl --url tcp:127.0.0.1:5762
```

ArduPilot SITL serves MAVLink on TCP 5760 (Mission Planner), 5762 and 5763; the
rehearsal takes 5762 and the bridge 5763, so neither steals the other's link. The bridge prints every pump change with
its acknowledgement latency, and writes `runs/bench_<time>.json`.

---

## Option B — a standalone pump (₹1,200–1,500, no drone needed)

| Part | Example | ₹ approx |
|---|---|---|
| Arduino Nano or Uno | any clone | 250–450 |
| 12 V micro diaphragm pump | "R385" or "12V 365/385 diaphragm pump" | 250–400 |
| Logic-level N-MOSFET | IRLZ44N (5 V boards) | 40 |
| Flyback diode | 1N5819 or 1N4007 across the pump | 5 |
| Resistors | 220 Ω gate, 10 kΩ gate-to-ground | 5 |
| 12 V 2 A supply or 3S LiPo | | 250 |
| Silicone tube + one flat-fan or mist nozzle | | 100 |

Wiring: Arduino **D5 → 220 Ω → MOSFET gate**, 10 kΩ gate→GND, MOSFET source →
GND (shared with the Arduino), drain → pump −, pump + → +12 V, diode across the
pump (stripe to +12 V). On an ESP32 (3.3 V logic) the IRLZ44N is marginal; use
a relay module or a 3.3 V-logic MOSFET board.

Flash `firmware/pump_bridge/pump_bridge.ino` with the Arduino IDE (115200 baud,
default `MODE_SWITCH`). Then:

```powershell
..\.venv-fp\Scripts\python.exe -m fieldpilot.bench --check --sink serial:COM6
..\.venv-fp\Scripts\python.exe -m fieldpilot.bench --source tcp:127.0.0.1:5763 --sink serial:COM6
```

The board's LED follows the pump, which shows up on camera. If the USB cable is
pulled or the laptop closes, the firmware turns the pump off within 1.5 s.

---

## The clip — one continuous take, 75–90 s

The judges need to believe it is one run, so do not cut inside it. Record the
laptop screen with OBS (Display Capture) and the hardware with a phone on a
tripod at the same time; put them side by side in the edit, phone on the right.

**Set dressing.** Under the nozzles, lay a strip of seedling trays or a sheet of
brown paper marked with the zone IDs from `examples/field_*.json` (Z1, Z2, …) in
large print. Water shows on brown paper. Point a desk lamp across the spray so
the droplets catch the light.

| t | Laptop (left) | Hardware (right) |
|---|---|---|
| 0–8 s | Console: the proposal, Nemotron's trace, the **Approve** button | The T1 on the ground, props folded, battery bay empty, the pump lead going to the Arduino |
| 8–12 s | Operator clicks Approve | — |
| 12–60 s | Console map: the aircraft flies the passes. Bridge window prints `PUMP ON zone A2 · ack 8 ms`, then `pump off · peak 62 % flow` | The four nozzles spray on each pass, the plume fades in every turn (below 0.5 m/s), off in transit |
| 60–70 s | Operator presses **ABORT** mid-zone | Pump stops within a second |
| 70–80 s | `runs/bench_*.json` summary: spray events, seconds sprayed, ack p50 | Pump silent, nozzles dripping; tank level down |

The last shot — wet where the agent decided, dry where it did not — is the
argument, made physically. Hold it for three seconds.

Lower-third text, on screen the whole time:
**"Aircraft position: simulated (ArduPilot SITL) · Spray hardware: real · Props removed"**

---

## Put it back

Restore `SERVOn_FUNCTION` (e.g. `22`) and `BRD_SAFETY_MASK` to the values you
noted, write params, reboot the flight controller. Props back on only after that.
