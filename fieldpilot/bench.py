r"""Bench rig: an approved mission's pump commands, driven onto real spray hardware.

    python -m fieldpilot.bench --check  --sink mavlink:COM5 --channel 9      # preflight only
    python -m fieldpilot.bench --source tcp:127.0.0.1:5763 --sink mavlink:COM5 --channel 9
    python -m fieldpilot.bench --source tcp:127.0.0.1:5763 --sink serial:COM6
    python -m fieldpilot.bench --source tcp:127.0.0.1:5763 --sink dry

What it is for
--------------
The Physical AI track asks for a minute of real hardware operating. Flying a
spray drone over a field for a hackathon video is the riskiest possible way to
get that minute. This gets it without a single propeller turning:

    simulator (where the drone is)  --pump PWM-->  bench  --DO_SET_SERVO-->  real drone, props off
                                                         \--serial-->       Arduino + pump

The vehicle's *position* comes from ArduPilot/PX4 SITL flying the mission
FieldPilot planned and a human approved. The *spray hardware* is real: the
autopilot's own pump output is mirrored, as it changes, onto the real drone's
flight controller on a bench, so its real pump sprays real water at exactly the
moments the simulated aircraft is over a zone the agent chose to treat. The
video says so on screen. Nothing about it is faked; one half is simulated and
the half that is not is the half a viewer can see.

Why it cannot fly the bench aircraft
------------------------------------
Four independent layers, any one of which is enough:

1. **Command allowlist.** The bench sink can emit exactly one actuating command,
   ``MAV_CMD_DO_SET_SERVO`` on the one configured channel. ARM, mode changes,
   motor tests and RC overrides are not merely unused - ``_command`` raises if
   asked for them, and a test proves it.
2. **The autopilot's own rule.** ArduPilot (4.0+) ignores DO_SET_SERVO on any
   output whose ``SERVOn_FUNCTION`` is not 0, 1 or 51-66, so a motor output
   cannot be driven this way even by mistake. The preflight reads that
   parameter and refuses to start if the channel is a motor (33-40) or any
   other active function.
3. **Armed means stop.** The bench refuses an armed vehicle, and if the vehicle
   arms mid-run it sets the pump off and exits.
4. **The safety switch stays ON.** With the switch engaged, ArduPilot disables
   every output except the channels in ``BRD_SAFETY_MASK``. Setting that mask to
   the pump channel alone leaves motors electrically dead while the pump works.
   The preflight prints the exact value. Props come off as well; see
   docs/BENCH_RIG.md.

A dead-man covers the link: if the simulator goes quiet for 2 s the pump is set
off, and the serial firmware turns itself off after 1.5 s without a command.

Flow law (``--flow-law t1_10l``)
--------------------------------
The project airframe's controller makes flow follow ground speed and stops the pump
below 0.5 m/s. With a flow law the bridge does the same on the bench: while the mission
says "spray", the pump runs at the share of full flow that lays the planned L/ha at the
simulated aircraft's ground speed (VFR_HUD), and stops when it slows below the cut-off.
The serial firmware's FLOW mode turns that into a PWM duty on the pump MOSFET.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOCAL_LINK = re.compile(r"^(tcp|udp|udpin|udpout):(127\.0\.0\.1|localhost|0\.0\.0\.0):\d+$", re.I)
# SERVOn_FUNCTION values ArduPilot will let DO_SET_SERVO drive (docs: 0, 1, 51-66).
SETTABLE_FUNCTIONS = {0, 1, *range(51, 67)}
MOTOR_FUNCTIONS = set(range(33, 41))          # Motor1..Motor8
SPRAYER_PUMP = 22
MAV_MODE_FLAG_SAFETY_ARMED = 128
SOURCE_TIMEOUT_S = 2.0
REFRESH_S = 1.0
FULL_PWM = 1900                     # full pump on the flow scale (firmware FLOW: 1100 -> 0 %, 1900 -> 100 %)
FLOW_STEP_US = 25                   # quantise so speed noise does not flood the link


def flow_pwm(spec, speed_ms: float | None, off_pwm: int = 1100) -> int:
    """PWM for the share of full pump flow the airframe needs at ``speed_ms``.

    A DC pump does not turn below its stall duty, and above it flow rises linearly with
    duty (fieldpilot/emulator.py), so the duty is stall + (1 - stall) x needed/maximum.
    """
    if speed_ms is None or not spec.pump_max_l_min:
        return FULL_PWM
    share = min(1.0, max(0.0, spec.flow_l_min(speed_ms) / spec.pump_max_l_min))
    d0 = getattr(spec, "pump_stall_duty", 0.0) or 0.0
    duty = 0.0 if share <= 0 else d0 + (1 - d0) * share
    us = off_pwm + (FULL_PWM - off_pwm) * duty
    return int(round(us / FLOW_STEP_US) * FLOW_STEP_US)


def _mavutil():
    """pymavlink, guaranteed MAVLink 2.

    SERVO_OUTPUT_RAW carries servo9..16 only as MAVLink 2 extension fields, and a pump
    is usually on an AUX output (9+). pymavlink picks its wire version from the
    MAVLINK20 environment variable *when the dialect loads*, so setting it late does
    nothing unless the dialect is reloaded - which this does, once.
    """
    os.environ.setdefault("MAVLINK20", "1")
    from pymavlink import mavutil
    if getattr(mavutil.mavlink, "WIRE_PROTOCOL_VERSION", "1.0") != "2.0":
        os.environ["MAVLINK20"] = "1"
        mavutil.set_dialect(os.environ.get("MAVLINK_DIALECT", "ardupilotmega"))
    from . import winsock
    winsock.apply()
    return mavutil


class BenchRefused(RuntimeError):
    """A preflight or runtime check said no. The message says why and how to fix it."""


@dataclass
class Event:
    t: float
    kind: str                  # pump_on | pump_off | refresh | deadman | ack | armed_abort | info
    pwm: int | None = None
    zone: str | None = None
    latency_ms: float | None = None
    detail: str = ""


@dataclass
class BenchLog:
    source: str
    sink: str
    channel: int
    on_threshold: int
    preflight: list[dict] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def add(self, ev: Event) -> None:
        self.events.append(ev)

    def summary(self) -> dict:
        on = [e for e in self.events if e.kind == "pump_on"]
        lat = [e.latency_ms for e in self.events if e.latency_ms is not None]
        spray_s, start = 0.0, None
        for e in self.events:
            if e.kind == "pump_on" and start is None:
                start = e.t
            elif e.kind in ("pump_off", "deadman", "armed_abort") and start is not None:
                spray_s += e.t - start
                start = None
        return {"pump_on_events": len(on), "spray_seconds": round(spray_s, 2),
                "ack_latency_ms_p50": _pct(lat, 50), "ack_latency_ms_p95": _pct(lat, 95),
                "zones": sorted({e.zone for e in on if e.zone})}


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))], 1)


# --------------------------------------------------------------------------- sinks

class Sink:
    name = "sink"

    def preflight(self) -> list[dict]:
        return []

    def set(self, pwm: int) -> float | None:
        """Apply a PWM; return round-trip latency in ms if the hardware acknowledged."""
        raise NotImplementedError

    def off(self) -> None:
        raise NotImplementedError

    def poll(self) -> None:
        """Called every loop; a sink raises BenchRefused here to stop the run."""

    def close(self) -> None:
        pass


class DrySink(Sink):
    name = "dry"

    def __init__(self, off_pwm: int = 1100):
        self.off_pwm, self.last = off_pwm, None
        self.history: list[int] = []

    def set(self, pwm: int) -> float | None:
        self.last = pwm
        self.history.append(pwm)
        return None

    def off(self) -> None:
        self.set(self.off_pwm)


class SerialSink(Sink):
    """An Arduino/ESP32 running firmware/pump_bridge. Line protocol: ``P<pwm>\\n`` -> ``OK <pwm>``."""
    name = "serial"

    def __init__(self, port: str, off_pwm: int = 1100, baud: int = 115200, ser=None):
        if ser is None:
            import serial
            ser = serial.Serial(port, baud, timeout=0.3)
            time.sleep(2.0)                          # most boards reset when the port opens
        self.ser, self.port, self.off_pwm = ser, port, off_pwm

    def preflight(self) -> list[dict]:
        self.ser.reset_input_buffer()
        self.ser.write(b"?\n")
        line = self.ser.readline().decode("ascii", "replace").strip()
        ok = line.startswith("PUMP_BRIDGE")
        return [{"check": "firmware", "ok": ok,
                 "detail": line or "no reply - is firmware/pump_bridge flashed, and the port right?"}]

    def set(self, pwm: int) -> float | None:
        t0 = time.perf_counter()
        self.ser.write(f"P{int(pwm)}\n".encode())
        line = self.ser.readline().decode("ascii", "replace").strip()
        if line == f"OK {int(pwm)}":
            return (time.perf_counter() - t0) * 1000
        return None

    def off(self) -> None:
        self.set(self.off_pwm)

    def close(self) -> None:
        try:
            self.off()
        finally:
            self.ser.close()


class MavlinkSink(Sink):
    """The real drone's flight controller on a bench. Only ever moves the pump output."""
    name = "mavlink"

    def __init__(self, url: str, channel: int, off_pwm: int = 1100, conn=None,
                 heartbeat_timeout_s: float = 8.0):
        if LOCAL_LINK.match(url):
            raise BenchRefused(f"{url!r} is a network link on this computer - that is a simulator, "
                               "not bench hardware. Use the flight controller's USB/serial port, "
                               "e.g. mavlink:COM5 or mavlink:/dev/ttyACM0.")
        mavutil = _mavutil()
        self.mavutil = mavutil
        self.m = conn or mavutil.mavlink_connection(url, baud=115200, source_system=254,
                                                    source_component=190)
        self.url, self.channel, self.off_pwm = url, channel, off_pwm
        self._last_hb = 0.0
        hb = self.m.wait_heartbeat(timeout=heartbeat_timeout_s)
        if hb is None:
            raise BenchRefused(f"no heartbeat on {url}. Is the flight controller powered and on "
                               "this port? (Mission Planner must not hold the port.)")
        self.target = (self.m.target_system, self.m.target_component or 1)
        self.autopilot = hb.autopilot
        self._armed = bool(hb.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)

    # -- the only door to the vehicle ------------------------------------------------
    def _command(self, cmd: int, *params: float) -> None:
        mav = self.mavutil.mavlink
        allowed = {mav.MAV_CMD_DO_SET_SERVO, mav.MAV_CMD_SET_MESSAGE_INTERVAL}
        if cmd not in allowed:
            raise BenchRefused(f"bench sink refuses MAVLink command {cmd}: it only moves the pump")
        if cmd == mav.MAV_CMD_DO_SET_SERVO and int(params[0]) != self.channel:
            raise BenchRefused(f"bench sink refuses servo {int(params[0])}: only channel "
                               f"{self.channel} (the pump) may be set")
        p = list(params) + [0.0] * (7 - len(params))
        self.m.mav.command_long_send(*self.target, cmd, 0, *p[:7])

    def _param(self, name: str, timeout_s: float = 3.0) -> float | None:
        self.m.mav.param_request_read_send(*self.target, name.encode(), -1)
        end = time.time() + timeout_s
        while time.time() < end:
            msg = self.m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.3)
            if msg is not None:
                pid = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode()
                if pid.rstrip("\x00") == name:
                    return float(msg.param_value)
        return None

    def _fresh_heartbeat(self, timeout_s: float = 3.0):
        while self.m.recv_match(blocking=False) is not None:   # drop anything already queued
            pass
        end = time.time() + timeout_s
        while time.time() < end:
            hb = self.m.recv_match(type="HEARTBEAT", blocking=True, timeout=0.3)
            if hb is not None and hb.get_srcSystem() == self.target[0]:
                return hb
        return None

    def preflight(self) -> list[dict]:
        out = []
        hb = self._fresh_heartbeat()
        if hb is not None:
            self._armed = bool(hb.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
        ap_ok = self.autopilot == self.mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA
        out.append({"check": "autopilot", "ok": ap_ok,
                    "detail": "ArduPilot" if ap_ok else f"autopilot id {self.autopilot}: the "
                    "function/safety checks below are ArduPilot's; PX4 is not supported on the bench"})
        out.append({"check": "disarmed", "ok": not self._armed,
                    "detail": "disarmed" if not self._armed else "VEHICLE IS ARMED - disarm it"})
        fn = self._param(f"SERVO{self.channel}_FUNCTION")
        if fn is None:
            out.append({"check": "pump channel function", "ok": False,
                        "detail": f"could not read SERVO{self.channel}_FUNCTION"})
        else:
            fn = int(fn)
            ok = fn in SETTABLE_FUNCTIONS
            why = ("motor output - this channel drives a motor" if fn in MOTOR_FUNCTIONS else
                   "ArduPilot's own sprayer (SprayerPump), which only runs armed and moving; "
                   "note 22 and put it back after the bench" if fn == SPRAYER_PUMP else
                   "an active function; DO_SET_SERVO is ignored on it")
            out.append({"check": "pump channel function", "ok": ok,
                        "detail": f"SERVO{self.channel}_FUNCTION = {fn}"
                        + ("" if ok else f" ({why}). Set it to 0 for a pump driven by mission commands.")})
        mask = self._param("BRD_SAFETY_MASK")
        need = 1 << (self.channel - 1)
        if mask is None:
            out.append({"check": "safety mask", "ok": True,
                        "detail": "BRD_SAFETY_MASK not reported (no safety switch on this board?)"})
        else:
            m = int(mask)
            extra = m & ~need
            out.append({"check": "safety mask", "ok": bool(m & need) and not extra,
                        "detail": f"BRD_SAFETY_MASK = {m}; pump-only value is {need}"
                        + ("" if m & need else " - pump will stay off while the safety switch is on")
                        + (f" - channels {_bits(extra)} are also live with safety on" if extra else "")})
        return out

    def set(self, pwm: int) -> float | None:
        t0 = time.perf_counter()
        self._command(self.mavutil.mavlink.MAV_CMD_DO_SET_SERVO, float(self.channel), float(pwm))
        ack = self.m.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.4)
        if ack is not None and ack.command == self.mavutil.mavlink.MAV_CMD_DO_SET_SERVO:
            if ack.result != 0:
                raise BenchRefused(f"flight controller rejected DO_SET_SERVO (result {ack.result}); "
                                   f"check SERVO{self.channel}_FUNCTION")
            return (time.perf_counter() - t0) * 1000
        return None

    def off(self) -> None:
        try:
            self.set(self.off_pwm)
        except BenchRefused:
            pass

    def poll(self) -> None:
        now = time.time()
        if now - self._last_hb > 1.0:                # a GCS heartbeat; the FC ignores us otherwise
            self.m.mav.heartbeat_send(6, 8, 0, 0, 0)    # MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID
            self._last_hb = now
        while True:
            hb = self.m.recv_match(type="HEARTBEAT", blocking=False)
            if hb is None:
                break
            if hb.get_srcSystem() == self.target[0] and hb.base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                self.off()
                raise BenchRefused("the bench vehicle ARMED - pump set off, bench stopped")

    def close(self) -> None:
        self.off()


def _bits(m: int) -> str:
    return ", ".join(str(i + 1) for i in range(32) if m >> i & 1)


# --------------------------------------------------------------------------- source

class SimSource:
    """The simulator's pump output, as the autopilot itself reports it (SERVO_OUTPUT_RAW)."""

    def __init__(self, url: str, channel: int, conn=None, zone_of=None):
        if conn is None and not LOCAL_LINK.match(url):
            raise BenchRefused(f"{url!r} is not a simulator on this computer. The bench reads the "
                               "pump from SITL only (tcp:127.0.0.1:5763 / udp:127.0.0.1:14550).")
        mavutil = _mavutil()
        self.mavutil = mavutil
        self.m = conn or mavutil.mavlink_connection(url, source_system=253)
        self.url, self.channel = url, channel
        self.zone_of = zone_of or (lambda lat, lon: None)
        self.lat = self.lon = None
        self.speed: float | None = None          # ground speed, m/s (VFR_HUD)
        self.last_t = 0.0

    def start(self, timeout_s: float = 15.0) -> None:
        if self.m.wait_heartbeat(timeout=timeout_s) is None:
            raise BenchRefused(f"no simulator on {self.url}. Start SITL first (docs/SITL_WINDOWS.md).")
        mav = self.mavutil.mavlink
        for msg_id, hz in ((36, 10), (33, 5), (74, 5)):  # SERVO_OUTPUT_RAW, GLOBAL_POSITION_INT, VFR_HUD
            self.m.mav.command_long_send(self.m.target_system, self.m.target_component or 1,
                                         mav.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                                         msg_id, 1e6 / hz, 0, 0, 0, 0, 0)

    def read(self, timeout_s: float = 0.2) -> int | None:
        """Next pump PWM sample, or None if nothing arrived in time."""
        end = time.time() + timeout_s
        while time.time() < end:
            msg = self.m.recv_match(type=["SERVO_OUTPUT_RAW", "GLOBAL_POSITION_INT", "VFR_HUD"],
                                    blocking=True, timeout=max(0.01, end - time.time()))
            if msg is None:
                return None
            if msg.get_type() == "VFR_HUD":
                self.speed = float(msg.groundspeed)
                continue
            if msg.get_type() == "GLOBAL_POSITION_INT":
                self.lat, self.lon = msg.lat / 1e7, msg.lon / 1e7
                continue
            pwm = getattr(msg, f"servo{self.channel}_raw", None)
            if pwm is None:
                raise BenchRefused(f"SERVO_OUTPUT_RAW has no servo{self.channel}_raw - "
                                   "channels above 8 need MAVLink 2 (SERIALn_PROTOCOL=2)")
            self.last_t = time.time()
            return int(pwm)
        return None

    def zone(self) -> str | None:
        return None if self.lat is None else self.zone_of(self.lat, self.lon)


class ConsoleSource:
    """The FieldPilot console's own simulated aircraft (FP_DRONE=sim), read over its local API.

    No SITL install: the console flies the approved mission in its simulator, and the bench
    reads spray state, zone and ground speed from ``/api/telemetry`` at 10 Hz. Start the
    console with FP_SIM_SPEED=1 so the speeds are real.
    """

    def __init__(self, url: str, channel: int = 9, client=None, on_pwm: int = 1900, off_pwm: int = 1100):
        base = url.split("://", 1)[-1].split("/", 1)[0]
        if not re.match(r"^(127\.0\.0\.1|localhost)(:\d+)?$", base, re.I):
            raise BenchRefused(f"{url!r} is not a console on this computer")
        self.url = url.rstrip("/") + "/api/telemetry"
        self.channel, self.on_pwm, self.off_pwm = channel, on_pwm, off_pwm
        if client is None:
            import httpx
            client = httpx.Client(timeout=0.5)
        self.client = client
        self.speed: float | None = None
        self._zone: str | None = None
        self.last_t = 0.0

    def start(self, timeout_s: float = 10.0) -> None:
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                self.client.get(self.url).raise_for_status()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        raise BenchRefused(f"no FieldPilot console at {self.url}. Start it first (FP_SIM_SPEED=1).")

    def read(self, timeout_s: float = 0.1) -> int | None:
        time.sleep(timeout_s)
        try:
            t = self.client.get(self.url).json()
        except Exception:  # noqa: BLE001
            return None
        if t.get("backend") != "sim":
            raise BenchRefused("the console is not on the simulator (FP_DRONE=sim); the bench "
                               "mirrors a simulated aircraft only")
        self.speed = float(t.get("groundspeed_ms") or 0.0)
        self._zone = t.get("zone_id") or None
        self.last_t = time.time()
        return self.on_pwm if t.get("spraying") else self.off_pwm

    def zone(self) -> str | None:
        return self._zone


# --------------------------------------------------------------------------- bridge

class Bridge:
    def __init__(self, source: SimSource, sink: Sink, log: BenchLog, on_threshold: int = 1500,
                 echo=print, clock=time.time, flow_law=None, off_pwm: int = 1100):
        self.source, self.sink, self.log = source, sink, log
        self.flow_law, self.off_pwm = flow_law, off_pwm
        self.on_threshold, self.echo, self.clock = on_threshold, echo, clock
        self.t0 = clock()
        self.state: bool | None = None        # pump on?
        self.peak = 0                         # highest PWM in the current spray burst
        self.applied: int | None = None
        self.last_refresh = 0.0
        self.stop = threading.Event()

    def _t(self) -> float:
        return round(self.clock() - self.t0, 3)

    def step(self) -> None:
        self.sink.poll()
        pwm = self.source.read()
        now = self.clock()
        if pwm is None:
            if (self.source.last_t and now - self.source.last_t > SOURCE_TIMEOUT_S
                    and self.state is not False):
                self.sink.off()
                self.state, self.applied = False, None
                self.log.add(Event(self._t(), "deadman", detail="simulator silent for "
                                   f"{SOURCE_TIMEOUT_S:.0f} s - pump off"))
                self.echo(f"  {self._t():7.1f}s  DEADMAN  simulator silent, pump off")
            return
        on = pwm >= self.on_threshold
        if self.flow_law is not None:
            # the airframe's law: flow follows ground speed, pump off below the cut-off
            pwm = flow_pwm(self.flow_law, getattr(self.source, "speed", None), self.off_pwm) \
                if on else self.off_pwm
            on = pwm > self.off_pwm
        if pwm != self.applied or now - self.last_refresh > REFRESH_S:
            lat = self.sink.set(pwm)
            self.peak = max(self.peak, pwm) if on else self.peak
            changed = on != self.state
            self.applied, self.last_refresh = pwm, now
            if changed:
                zone = self.source.zone()
                kind = "pump_on" if on else "pump_off"
                peak = self.peak if not on else None
                self.log.add(Event(self._t(), kind, pwm=pwm, zone=zone, latency_ms=lat,
                                   detail=f"peak pwm {peak}" if peak and self.flow_law else ""))
                flow = (f"  peak {100 * (peak - self.off_pwm) / (FULL_PWM - self.off_pwm):.0f} % flow"
                        if peak and self.flow_law else "")
                self.echo(f"  {self._t():7.1f}s  {'PUMP ON ' if on else 'pump off'}  pwm {pwm}"
                          + (f"  zone {zone}" if zone else "")
                          + (f"  ack {lat:.0f} ms" if lat is not None else "") + flow)
                self.state = on
                if not on:
                    self.peak = 0

    def run(self, duration_s: float | None = None) -> None:
        try:
            while not self.stop.is_set():
                if duration_s is not None and self.clock() - self.t0 > duration_s:
                    break
                self.step()
        except BenchRefused as e:
            self.log.add(Event(self._t(), "armed_abort" if "ARMED" in str(e) else "info",
                               detail=str(e)))
            raise
        finally:
            self.sink.off()


# --------------------------------------------------------------------------- CLI

def make_sink(spec: str, channel: int, off_pwm: int) -> Sink:
    kind, _, target = spec.partition(":")
    if kind == "dry":
        return DrySink(off_pwm)
    if kind == "serial" and target:
        return SerialSink(target, off_pwm)
    if kind == "emu":
        from .emulator import PumpBridgeEmulator
        return SerialSink("emulated", off_pwm, ser=PumpBridgeEmulator())
    if kind == "mavlink" and target:
        return MavlinkSink(target, channel, off_pwm)
    raise SystemExit(f"--sink must be dry, emu, serial:<port> or mavlink:<port>, not {spec!r}")


def print_preflight(rows: list[dict]) -> bool:
    for r in rows:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['check']:22s} {r['detail']}")
    return all(r["ok"] for r in rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="tcp:127.0.0.1:5763",
                    help="SITL link, local only (5763), or console[:http://127.0.0.1:8080] for "
                         "the FieldPilot console's own simulator")
    ap.add_argument("--sink", default="dry", help="dry | emu (emulated T1 pump) | serial:<port> | mavlink:<port>")
    ap.add_argument("--channel", type=int, default=9, help="pump output number on both vehicles")
    ap.add_argument("--on-threshold", type=int, default=1500, help="PWM at or above = spraying")
    ap.add_argument("--off-pwm", type=int, default=1100)
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    ap.add_argument("--check", action="store_true", help="run the sink preflight and exit")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--flow-law", default=None, metavar="PRESET",
                    help="drive flow from simulated ground speed like this airframe (t1_10l); "
                         "needs firmware in FLOW mode")
    a = ap.parse_args(argv)

    try:
        sink = make_sink(a.sink, a.channel, a.off_pwm)
        print(f"bench sink: {a.sink}  channel {a.channel}")
        pre = sink.preflight()
        ok = print_preflight(pre) if pre else True
        if a.check or not ok:
            sink.close()
            if not ok:
                print("\nrefusing to start: fix the FAIL lines above (docs/BENCH_RIG.md).")
            return 0 if ok else 2
        if a.source.startswith("console"):
            url = a.source.partition(":")[2] or "http://127.0.0.1:8080"
            src = ConsoleSource(url, a.channel, off_pwm=a.off_pwm)
        else:
            src = SimSource(a.source, a.channel)
        src.start()
    except BenchRefused as e:
        print(f"refusing: {e}")
        return 2

    log = BenchLog(a.source, a.sink, a.channel, a.on_threshold, pre)
    law = None
    if a.flow_law:
        from .mission import drone_spec
        law = drone_spec(a.flow_law)
        print(f"flow law: {law.name} - {law.spray_rate_l_ha:g} L/ha x {law.swath_m:g} m, "
              f"pump {law.pump_max_l_min:g} L/min, off below {law.pump_cutoff_ms:g} m/s")
    bridge = Bridge(src, sink, log, a.on_threshold, flow_law=law, off_pwm=a.off_pwm)
    print(f"mirroring pump channel {a.channel}: {a.source} -> {a.sink}   (Ctrl-C stops, pump off)")
    code = 0
    try:
        bridge.run(a.duration)
    except KeyboardInterrupt:
        print("\n  stopped by operator - pump off")
    except BenchRefused as e:
        print(f"\n  STOPPED: {e}")
        code = 3
    finally:
        sink.close()
    out = Path(a.runs_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"bench_{ts}.json"
    rec = {"utc": ts, "source": a.source, "sink": a.sink, "channel": a.channel,
           "on_threshold": a.on_threshold, "flow_law": a.flow_law, "preflight": pre,
           "summary": log.summary(), "events": [e.__dict__ for e in log.events]}
    emu = getattr(sink, "ser", None)
    if hasattr(emu, "summary"):                       # emulated pump: what it would have sprayed
        emu.finish()
        rec["emulated_pump"] = emu.summary() | {"label": "EMULATED hardware (not a measurement)"}
        rec["emulated_trace"] = emu.trace[::5]
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    s = log.summary()
    print(f"\n{s['pump_on_events']} spray events, {s['spray_seconds']} s sprayed"
          + (f", ack p50 {s['ack_latency_ms_p50']} ms" if s['ack_latency_ms_p50'] else "")
          + f"\n-> {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
