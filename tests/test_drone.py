import time

import pytest

from fake_vehicle import FakeVehicle
from fieldpilot.drone.mavlink import MavlinkBackend
from fieldpilot.drone.sim import SimBackend
from fieldpilot.mission import build_mission
from mock_openai import free_port


def _udp_port():
    return free_port()


@pytest.mark.parametrize("autopilot", ["px4", "ardupilot"])
def test_mavlink_mission_handshake(field, autopilot):
    port = _udp_port()
    m = build_mission(field, ["A2", "B2"], ["P"])
    b = MavlinkBackend(url=f"udpin:127.0.0.1:{port}", heartbeat_timeout=8)
    v = FakeVehicle(port, autopilot=autopilot).start()
    try:
        b.connect()
        time.sleep(0.8)
        assert b.autopilot == autopilot
        assert b.preflight()["ok"], b.preflight()
        b.upload(m)
        items = [v.items[i] for i in sorted(v.items)]
        cmds = [i.command for i in items]
        assert len(items) == v.expected
        if autopilot == "ardupilot":
            assert cmds[0] == 16 and cmds[1] == 22          # home placeholder, then takeoff
        else:
            assert cmds[0] == 22
        assert cmds[-1] == 20                                # RTL
        servo = [i for i in items if i.command == 183]
        pw = [int(i.param2) for i in servo]                 # on/off around every pass
        assert len(pw) >= 4 and pw[::2] == [1900] * (len(pw) // 2) and pw[1::2] == [1100] * (len(pw) // 2)
        b.start()
        time.sleep(0.4)
        assert v.armed and v.mission_started
        n = len(v.commands)
        b.abort()
        time.sleep(0.4)
        assert 176 in v.commands[n:]                        # DO_SET_MODE (RTL)
    finally:
        v.stop()
        b.close()


def test_mavlink_rejected_mission_raises(field):
    port = _udp_port()
    b = MavlinkBackend(url=f"udpin:127.0.0.1:{port}", heartbeat_timeout=8)
    v = FakeVehicle(port, reject_mission=True).start()
    try:
        b.connect()
        time.sleep(0.5)
        with pytest.raises(RuntimeError, match="rejected"):
            b.upload(build_mission(field, ["A2"], []))
    finally:
        v.stop()
        b.close()


def test_mavlink_preflight_blocks_low_battery_and_no_fix():
    port = _udp_port()
    b = MavlinkBackend(url=f"udpin:127.0.0.1:{port}", heartbeat_timeout=8)
    v = FakeVehicle(port, battery=35, fix=1).start()
    try:
        b.connect()
        time.sleep(0.8)
        pf = b.preflight()
        assert not pf["ok"] and len(pf["issues"]) == 2
    finally:
        v.stop()
        b.close()


def test_mavlink_refused_arm_raises(field):
    port = _udp_port()
    b = MavlinkBackend(url=f"udpin:127.0.0.1:{port}", heartbeat_timeout=8)
    v = FakeVehicle(port, refuse_arm=True).start()
    try:
        b.connect()
        time.sleep(0.5)
        b.upload(build_mission(field, ["A2"], []))
        with pytest.raises(RuntimeError, match="arming refused"):
            b.start()
    finally:
        v.stop()
        b.close()


def test_sim_flies_sprays_and_lands(field):
    s = SimBackend(home=field.home, time_scale=200.0)
    s.connect()
    s.upload(build_mission(field, ["A2"], []))
    s.start()
    sprayed = False
    for _ in range(300):
        t = s.telemetry()
        sprayed |= t.spraying
        if t.state == "landed":
            break
        time.sleep(0.02)
    assert t.state == "landed" and sprayed and not t.armed
    assert abs(t.lat - field.home[0]) < 1e-6


def test_sim_abort_returns_home(field):
    s = SimBackend(home=field.home, time_scale=40.0)
    s.connect()
    s.upload(build_mission(field, ["A1", "A2", "A3", "B1", "B2", "B3"], []))
    s.start()
    time.sleep(0.4)
    s.abort()
    for _ in range(300):
        if s.telemetry().state == "aborted":
            break
        time.sleep(0.02)
    t = s.telemetry()
    assert t.state == "aborted" and t.alt_m == pytest.approx(0.0, abs=0.05)
