"""MAVLink backend for a real drone or PX4 / ArduPilot SITL, via pymavlink.

Why pymavlink and not MAVSDK: it speaks to both PX4 and ArduPilot with no gRPC
server binary on the companion computer, and the mission-upload protocol is
simple enough to own (MISSION_COUNT -> MISSION_REQUEST_INT* -> MISSION_ACK).
Owning it means the test suite can exercise the real handshake against a fake
vehicle, which it does.

What goes up
------------
    [home placeholder]            ArduPilot only: seq 0 is overwritten by home
    NAV_TAKEOFF    alt
    for each zone:
      NAV_WAYPOINT  pass start
      DO_CHANGE_SPEED spray speed
      DO_SET_SERVO  payload ON          <- pump relay / sprayer servo
        (or DO_SPRAYER 1 with FP_SPRAY_CMD=sprayer: ArduPilot's sprayer then makes flow
         follow ground speed and stops the pump below SPRAY_SPEED_MIN - the T1's law)
      NAV_WAYPOINT  ... boustrophedon passes ...
      DO_SET_SERVO  payload OFF         (or DO_SPRAYER 0)
      DO_CHANGE_SPEED transit speed
    NAV_RETURN_TO_LAUNCH

The safety pilot's RC transmitter overrides all of it, always. Abort sets RTL.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from .base import DroneBackend, Telemetry

log = logging.getLogger(__name__)


class MavlinkBackend(DroneBackend):
    name = "mavlink"

    def __init__(self, url: str = "udpin:0.0.0.0:14550", spec=None, payload_servo: int = 9,
                 pwm_on: int = 1900, pwm_off: int = 1100, heartbeat_timeout: float = 15.0,
                 min_battery_pct: float = 60.0, spray_cmd: str = "servo", **_):
        from ..mission import DroneSpec
        self.url = url
        self.spec = spec or _project_spec()
        self.servo, self.pwm_on, self.pwm_off = payload_servo, pwm_on, pwm_off
        if spray_cmd not in ("servo", "sprayer"):
            raise ValueError("spray_cmd must be 'servo' or 'sprayer'")
        self.spray_cmd = spray_cmd
        self.hb_timeout = heartbeat_timeout
        self.min_battery = min_battery_pct
        self.t = Telemetry(backend="mavlink")
        self.m = None
        self.autopilot = "unknown"
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._seq_map: list[tuple[str, str]] = []     # mavlink seq -> (kind, zone)
        self._prearm_ok: bool | None = None

    # -- connection ------------------------------------------------------------
    def connect(self) -> Telemetry:
        # MAVLink 2: SERVO_OUTPUT_RAW reports AUX outputs (servo9..16) only as v2 extension
        # fields, and the pump usually sits on AUX. pymavlink defaults to v1.
        from ..bench import _mavutil
        mavutil = _mavutil()
        self.m = mavutil.mavlink_connection(self.url, source_system=254, source_component=190,
                                            autoreconnect=True)
        hb = self.m.wait_heartbeat(timeout=self.hb_timeout)
        if hb is None:
            raise TimeoutError(f"no heartbeat on {self.url} within {self.hb_timeout:.0f} s")
        ap = hb.autopilot
        self.autopilot = ("ardupilot" if ap == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA
                          else "px4" if ap == mavutil.mavlink.MAV_AUTOPILOT_PX4 else "generic")
        self.t.connected = True
        self.t.message = f"heartbeat from sys {self.m.target_system} ({self.autopilot})"
        for msg_id, hz in ((33, 5), (1, 1), (24, 1), (42, 2), (46, 2)):
            self._cmd(mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, msg_id, 1e6 / hz, wait=False)
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self.t

    def _read_loop(self) -> None:
        from pymavlink import mavutil
        M = mavutil.mavlink
        while not self._stop.is_set():
            try:
                msg = self.m.recv_match(blocking=True, timeout=1.0)
            except Exception as e:  # noqa: BLE001
                self.t.message = f"link error: {e}"
                time.sleep(0.5)
                continue
            if msg is None:
                continue
            typ = msg.get_type()
            if typ in ("MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK", "COMMAND_ACK"):
                self._q.put(msg)
            elif typ == "HEARTBEAT" and msg.get_srcSystem() == self.m.target_system:
                self.t.armed = bool(msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)
                try:
                    self.t.mode = self.m.flightmode
                except Exception:  # noqa: BLE001
                    pass
                if not self.t.armed and self.t.state in ("flying", "returning"):
                    self.t.state, self.t.spraying = "landed", False
                    self.t.message = "landed, disarmed"
            elif typ == "GLOBAL_POSITION_INT":
                self.t.lat, self.t.lon = msg.lat / 1e7, msg.lon / 1e7
                self.t.alt_m = msg.relative_alt / 1000.0
                if self.t.armed:
                    self.t.trail.append([round(self.t.lat, 7), round(self.t.lon, 7), self.t.spraying])
            elif typ == "SYS_STATUS":
                self.t.battery_pct = float(msg.battery_remaining)
                pre = M.MAV_SYS_STATUS_PREARM_CHECK
                if msg.onboard_control_sensors_present & pre:
                    self._prearm_ok = bool(msg.onboard_control_sensors_health & pre)
            elif typ == "GPS_RAW_INT":
                self.t.gps_fix, self.t.satellites = int(msg.fix_type), int(msg.satellites_visible)
            elif typ == "MISSION_CURRENT":
                self._on_seq(int(msg.seq))
            elif typ == "MISSION_ITEM_REACHED":
                self._on_seq(int(msg.seq))
            elif typ == "STATUSTEXT":
                self.t.message = str(msg.text)

    def _on_seq(self, seq: int) -> None:
        self.t.mission_seq = seq
        spraying, zone = False, ""
        for kind, z in self._seq_map[:seq + 1]:
            if kind == "spray_on":
                spraying, zone = True, z
            elif kind == "spray_off":
                spraying = False
        self.t.spraying, self.t.zone_id = spraying, zone
        if self._seq_map and seq >= len(self._seq_map) - 1 and self.t.state == "flying":
            self.t.state = "returning"

    # -- commands ------------------------------------------------------------------
    def _cmd(self, command: int, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0,
             wait: bool = True, timeout: float = 5.0) -> bool:
        self.m.mav.command_long_send(self.m.target_system, self.m.target_component,
                                     command, 0, p1, p2, p3, p4, p5, p6, p7)
        if not wait:
            return True
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg.get_type() == "COMMAND_ACK" and msg.command == command:
                from pymavlink import mavutil
                return msg.result in (mavutil.mavlink.MAV_RESULT_ACCEPTED,
                                      mavutil.mavlink.MAV_RESULT_IN_PROGRESS)
        return False

    def preflight(self) -> dict:
        issues = []
        if not self.t.connected:
            issues.append("not connected")
        if self.t.gps_fix < 3:
            issues.append(f"GPS fix type {self.t.gps_fix} (need 3D fix)")
        if 0 <= self.t.battery_pct < self.min_battery:
            issues.append(f"battery {self.t.battery_pct:.0f}% < {self.min_battery:.0f}%")
        if self.t.battery_pct < 0:
            issues.append("battery level unknown")
        if self._prearm_ok is False:
            issues.append("autopilot pre-arm checks failing (see flight controller messages)")
        if self.t.armed:
            issues.append("vehicle is already armed")
        return {"ok": not issues, "issues": issues, "battery_pct": self.t.battery_pct,
                "gps_fix": self.t.gps_fix, "satellites": self.t.satellites,
                "autopilot": self.autopilot, "backend": "mavlink"}

    # -- mission ---------------------------------------------------------------------
    def to_items(self, mission) -> list[dict]:
        """Our mission -> MAVLink MISSION_ITEM_INT fields (also used by tests)."""
        from pymavlink import mavutil
        M = mavutil.mavlink
        REL, MIS = M.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, M.MAV_FRAME_MISSION
        out: list[dict] = []
        seq_map: list[tuple[str, str]] = []

        def add(kind, zone, cmd, frame, p=(0, 0, 0, 0), lat=0.0, lon=0.0, alt=0.0):
            out.append({"command": cmd, "frame": frame, "params": list(p),
                        "x": int(round(lat * 1e7)), "y": int(round(lon * 1e7)), "z": float(alt)})
            seq_map.append((kind, zone))

        items = mission.items
        if self.autopilot == "ardupilot":
            h = items[0]
            add("home", "", M.MAV_CMD_NAV_WAYPOINT, M.MAV_FRAME_GLOBAL, lat=h.lat, lon=h.lon)
        for it in items:
            if it.kind == "takeoff":
                add("takeoff", "", M.MAV_CMD_NAV_TAKEOFF, REL, lat=it.lat, lon=it.lon, alt=it.alt_m)
            elif it.kind == "waypoint":
                add("waypoint", it.zone_id, M.MAV_CMD_NAV_WAYPOINT, REL, (0, 0.5, 0, float("nan")),
                    it.lat, it.lon, it.alt_m)
            elif it.kind == "spray_on":
                add("speed", it.zone_id, M.MAV_CMD_DO_CHANGE_SPEED, MIS, (1, self.spec.spray_speed_ms, -1, 0))
                if self.spray_cmd == "sprayer":
                    add("spray_on", it.zone_id, M.MAV_CMD_DO_SPRAYER, MIS, (1, 0, 0, 0))
                else:
                    add("spray_on", it.zone_id, M.MAV_CMD_DO_SET_SERVO, MIS, (self.servo, self.pwm_on, 0, 0))
            elif it.kind == "spray_off":
                if self.spray_cmd == "sprayer":
                    add("spray_off", it.zone_id, M.MAV_CMD_DO_SPRAYER, MIS, (0, 0, 0, 0))
                else:
                    add("spray_off", it.zone_id, M.MAV_CMD_DO_SET_SERVO, MIS, (self.servo, self.pwm_off, 0, 0))
                add("speed", it.zone_id, M.MAV_CMD_DO_CHANGE_SPEED, MIS,
                    (1, self.spec.transit_speed_ms, -1, 0))
            elif it.kind == "rtl":
                add("rtl", "", M.MAV_CMD_NAV_RETURN_TO_LAUNCH, MIS)
        self._seq_map = seq_map
        return out

    def upload(self, mission, timeout: float = 10.0) -> None:
        from pymavlink import mavutil
        M = mavutil.mavlink
        items = self.to_items(mission)
        while not self._q.empty():
            self._q.get_nowait()
        ts, tc = self.m.target_system, self.m.target_component
        self.m.mav.mission_count_send(ts, tc, len(items), M.MAV_MISSION_TYPE_MISSION)
        sent: set[int] = set()
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            typ = msg.get_type()
            if typ in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                s = int(msg.seq)
                if s >= len(items):
                    raise RuntimeError(f"vehicle requested item {s} of {len(items)}")
                it = items[s]
                self.m.mav.mission_item_int_send(
                    ts, tc, s, it["frame"], it["command"], 0, 1, *it["params"],
                    it["x"], it["y"], it["z"], M.MAV_MISSION_TYPE_MISSION)
                sent.add(s)
                end = time.time() + timeout
            elif typ == "MISSION_ACK":
                if msg.type != M.MAV_MISSION_ACCEPTED:
                    raise RuntimeError(f"mission rejected by vehicle (MAV_MISSION_RESULT {msg.type})")
                if len(sent) != len(items):
                    raise RuntimeError(f"vehicle accepted after {len(sent)}/{len(items)} items")
                self.t.mission_total = len(items)
                self.t.state = "uploaded"
                self.t.message = f"mission {mission.mission_id} accepted ({len(items)} items)"
                return
        raise TimeoutError(f"mission upload timed out after {len(sent)}/{len(items)} items")

    def start(self) -> None:
        from pymavlink import mavutil
        M = mavutil.mavlink
        if self.autopilot == "ardupilot":
            self.m.set_mode("GUIDED")
            time.sleep(0.5)
        if not self._cmd(M.MAV_CMD_COMPONENT_ARM_DISARM, 1):
            raise RuntimeError("arming refused by the autopilot (check pre-arm messages)")
        if self.autopilot == "ardupilot":
            self.m.set_mode("AUTO")
            time.sleep(0.5)
        if not self._cmd(M.MAV_CMD_MISSION_START, 0, 0):
            self.abort()
            raise RuntimeError("mission start refused; RTL commanded")
        self.t.state, self.t.message = "flying", "mission started"

    def abort(self) -> None:
        from pymavlink import mavutil
        try:
            self.m.set_mode("RTL")
        except Exception:  # noqa: BLE001
            self._cmd(mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH, wait=False)
        self.t.state, self.t.message = "returning", "ABORT: RTL commanded"

    def telemetry(self) -> Telemetry:
        return self.t

    def close(self) -> None:
        self._stop.set()
        if self.m is not None:
            self.m.close()


def _project_spec():
    """The project airframe (T1 10 L quad) unless a spec is passed."""
    from fieldpilot.mission import drone_spec as _ds
    return _ds(None)
