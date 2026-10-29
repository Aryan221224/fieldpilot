"""Bench rig: mirrors the simulator's pump onto real hardware, and nothing else.

The MAVLink tests run two fake vehicles over UDP loopback - a "simulator" that
reports a scripted pump output, and a "bench flight controller" that answers
parameter reads, acknowledges DO_SET_SERVO and records every command it is sent.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from fieldpilot import bench, winsock
from fieldpilot.bench import _mavutil

mavutil = _mavutil()
from fieldpilot.bench import (BenchLog, BenchRefused, Bridge, DrySink, MavlinkSink,
                              SerialSink, SimSource)

MAV = mavutil.mavlink


def test_pymavlink_is_mavlink2_so_aux_pump_channels_exist():
    assert MAV.WIRE_PROTOCOL_VERSION == "2.0"
    assert "servo9_raw" in MAV.MAVLink_servo_output_raw_message.fieldnames


def _port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeVehicle(threading.Thread):
    """A MAVLink peer. ``pump`` is a list of (seconds, pwm) steps for SERVO_OUTPUT_RAW."""

    def __init__(self, port: int, *, params=None, armed=False, pump=None, autopilot=None,
                 servo_rate_hz=20.0):
        super().__init__(daemon=True)
        self.m = winsock.vehicle_connection(mavutil, port, source_system=1, source_component=1)
        self.params = params or {}
        self.armed = armed
        self.pump = pump
        self.autopilot = MAV.MAV_AUTOPILOT_ARDUPILOTMEGA if autopilot is None else autopilot
        self.rate = servo_rate_hz
        self.commands: list[tuple[int, list[float]]] = []
        self.servo_set: list[tuple[int, int]] = []
        self.stop = threading.Event()
        self.silent_after: float | None = None

    def run(self) -> None:
        t0, last_hb, last_servo = time.time(), 0.0, 0.0
        while not self.stop.is_set():
            now = time.time()
            el = now - t0
            if self.silent_after is not None and el > self.silent_after:
                time.sleep(0.02)
                continue
            if now - last_hb > 0.25:
                mode = MAV.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0
                self.m.mav.heartbeat_send(MAV.MAV_TYPE_HEXAROTOR, self.autopilot, mode, 0, 3)
                last_hb = now
            if self.pump is not None and now - last_servo > 1 / self.rate:
                pwm = self.pump[0][1]
                for at, v in self.pump:
                    if el >= at:
                        pwm = v
                vals = [1500] * 16
                vals[8] = pwm                                      # servo9
                self.m.mav.servo_output_raw_send(int(el * 1e6) & 0xFFFFFFFF, 0, *vals)
                self.m.mav.global_position_int_send(int(el * 1000), int(28.45e7), int(77.58e7),
                                                    210000, 10000, 0, 0, 0, 0)
                last_servo = now
            msg = self.m.recv_match(blocking=True, timeout=0.01)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "PARAM_REQUEST_READ":
                name = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode()
                name = name.rstrip("\x00")
                if name in self.params:
                    self.m.mav.param_value_send(name.encode(), float(self.params[name]),
                                                MAV.MAV_PARAM_TYPE_REAL32, len(self.params), 0)
            elif t == "COMMAND_LONG":
                p = [msg.param1, msg.param2, msg.param3, msg.param4, msg.param5, msg.param6, msg.param7]
                self.commands.append((msg.command, p))
                if msg.command == MAV.MAV_CMD_DO_SET_SERVO:
                    self.servo_set.append((int(p[0]), int(p[1])))
                self.m.mav.command_ack_send(msg.command, 0)


@pytest.fixture
def fc():
    """A bench flight controller configured the way docs/BENCH_RIG.md says."""
    port = _port()
    v = FakeVehicle(port, params={"SERVO9_FUNCTION": 0, "BRD_SAFETY_MASK": 256})
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=254,
                                      source_component=190)
    v.start()
    yield v, conn
    v.stop.set()


def _sink(fc, **kw) -> MavlinkSink:
    v, conn = fc
    return MavlinkSink("COM_TEST", channel=9, conn=conn, **kw)


# --------------------------------------------------------------------------- safety


def test_preflight_passes_on_a_correctly_configured_bench(fc):
    rows = _sink(fc).preflight()
    assert all(r["ok"] for r in rows), rows


def test_refuses_a_motor_output(fc):
    fc[0].params["SERVO9_FUNCTION"] = 33               # Motor1
    rows = {r["check"]: r for r in _sink(fc).preflight()}
    assert not rows["pump channel function"]["ok"]
    assert "motor" in rows["pump channel function"]["detail"]


def test_explains_ardupilots_own_sprayer_function(fc):
    fc[0].params["SERVO9_FUNCTION"] = 22               # SprayerPump
    row = {r["check"]: r for r in _sink(fc).preflight()}["pump channel function"]
    assert not row["ok"] and "SprayerPump" in row["detail"] and "put it back" in row["detail"]


def test_refuses_an_armed_vehicle(fc):
    fc[0].armed = True
    time.sleep(0.4)
    rows = {r["check"]: r for r in _sink(fc).preflight()}
    assert not rows["disarmed"]["ok"]


def test_flags_motors_left_live_by_the_safety_mask(fc):
    fc[0].params["BRD_SAFETY_MASK"] = 256 | 0b1111     # pump + motors 1-4
    rows = {r["check"]: r for r in _sink(fc).preflight()}
    assert not rows["safety mask"]["ok"]
    assert "1, 2, 3, 4" in rows["safety mask"]["detail"]


def test_command_allowlist_only_lets_the_pump_through(fc):
    s = _sink(fc)
    with pytest.raises(BenchRefused):
        s._command(MAV.MAV_CMD_COMPONENT_ARM_DISARM, 1)
    with pytest.raises(BenchRefused):
        s._command(MAV.MAV_CMD_DO_MOTOR_TEST, 1, 0, 1200, 5)
    with pytest.raises(BenchRefused):
        s._command(MAV.MAV_CMD_DO_SET_SERVO, 1, 1900)     # a motor channel on most frames
    with pytest.raises(BenchRefused):
        s._command(MAV.MAV_CMD_DO_SET_MODE, 1, 4)
    s._command(MAV.MAV_CMD_DO_SET_SERVO, 9, 1900)         # the pump: allowed
    time.sleep(0.2)
    sent = {c for c, _ in fc[0].commands}
    assert sent == {MAV.MAV_CMD_DO_SET_SERVO}


def test_refuses_a_simulator_url_as_the_bench_sink():
    with pytest.raises(BenchRefused):
        MavlinkSink("tcp:127.0.0.1:5762", channel=9)


def test_refuses_a_non_local_source():
    with pytest.raises(BenchRefused):
        SimSource("COM5", channel=9)
    with pytest.raises(BenchRefused):
        SimSource("udp:192.168.1.20:14550", channel=9)


def test_arming_mid_run_turns_the_pump_off_and_stops(fc):
    s = _sink(fc)
    s.set(1900)
    fc[0].armed = True
    with pytest.raises(BenchRefused, match="ARMED"):
        end = time.time() + 2
        while time.time() < end:
            s.poll()
            time.sleep(0.05)
    time.sleep(0.2)
    assert fc[0].servo_set[-1] == (9, 1100)


# --------------------------------------------------------------------------- mirroring


def test_mirrors_the_simulated_pump_onto_the_bench_controller(fc):
    sim_port = _port()
    sim = FakeVehicle(sim_port, pump=[(0, 1100), (0.6, 1900), (1.4, 1100)])
    sim_conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{sim_port}", source_system=253)
    sim.start()
    try:
        src = SimSource("udp:127.0.0.1:14550", channel=9, conn=sim_conn,
                        zone_of=lambda lat, lon: "Z3")
        src.start(timeout_s=3)
        sink = _sink(fc)
        log = BenchLog("sim", "mavlink:COM_TEST", 9, 1500)
        Bridge(src, sink, log, echo=lambda *_: None).run(duration_s=2.2)
    finally:
        sim.stop.set()
    kinds = [e.kind for e in log.events]
    assert "pump_on" in kinds and kinds.index("pump_on") < len(kinds) - 1
    on = next(e for e in log.events if e.kind == "pump_on")
    assert on.zone == "Z3" and on.pwm == 1900 and on.latency_ms is not None
    assert (9, 1900) in fc[0].servo_set
    assert fc[0].servo_set[-1] == (9, 1100)              # always ends off
    assert {ch for ch, _ in fc[0].servo_set} == {9}
    s = log.summary()
    assert 0.4 < s["spray_seconds"] < 1.3 and s["zones"] == ["Z3"]


class ScriptedSource:
    def __init__(self, samples, clock):
        self.samples, self.clock, self.last_t = list(samples), clock, 0.0

    def read(self):
        self.clock.t += 0.1
        if not self.samples:
            return None
        v = self.samples.pop(0)
        if v is not None:
            self.last_t = self.clock.t
        return v

    def zone(self):
        return None


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


_real_time = time.time


def test_deadman_turns_the_pump_off_when_the_simulator_goes_quiet():
    clk = Clock()
    src = ScriptedSource([1900] * 5 + [None] * 40, clk)
    bench.time.time = clk                                  # the deadman reads wall time too
    try:
        sink, log = DrySink(), BenchLog("s", "dry", 9, 1500)
        br = Bridge(src, sink, log, echo=lambda *_: None, clock=clk)
        for _ in range(45):
            br.step()
    finally:
        bench.time.time = _real_time
    assert [e.kind for e in log.events] == ["pump_on", "deadman"]
    assert sink.last == 1100


def test_refresh_keeps_reasserting_the_current_state():
    clk = Clock()
    src = ScriptedSource([1900] * 30, clk)
    sink = DrySink()
    br = Bridge(src, sink, BenchLog("s", "dry", 9, 1500), echo=lambda *_: None, clock=clk)
    for _ in range(30):
        br.step()
    # 3 s of identical samples: set once on change, then about once a second
    assert 3 <= len(sink.history) <= 5 and set(sink.history) == {1900}


# --------------------------------------------------------------------------- serial


class FakeFirmware:
    def __init__(self, reply=True):
        self.out, self.reply, self.writes = [], reply, []

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, b):
        line = b.decode().strip()
        self.writes.append(line)
        if line == "?":
            self.out.append(b"PUMP_BRIDGE 1 mode=switch pin=5 deadman_ms=1500\n")
        elif line.startswith("P") and self.reply:
            self.out.append(f"OK {line[1:]}\n".encode())

    def readline(self):
        return self.out.pop(0) if self.out else b""

    def close(self):
        pass


def test_serial_sink_speaks_the_firmware_protocol():
    fw = FakeFirmware()
    s = SerialSink("COMX", ser=fw)
    assert s.preflight()[0]["ok"]
    assert s.set(1900) is not None
    s.close()
    assert fw.writes[-1] == "P1100"


def test_serial_preflight_fails_without_firmware():
    class Silent(FakeFirmware):
        def write(self, b):
            pass
    assert not SerialSink("COMX", ser=Silent()).preflight()[0]["ok"]
