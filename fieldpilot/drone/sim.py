"""Kinematic drone simulator: flies the uploaded mission in real (or scaled) time.

No physics beyond constant speeds and a linear energy model -- enough to show
the operator console, telemetry, spray on/off and abort behaving exactly as
they will with the real aircraft, with no hardware and no SITL install.
"""
from __future__ import annotations

import math
import threading
import time

from ..field import LocalFrame
from .base import DroneBackend, Telemetry


class SimBackend(DroneBackend):
    name = "sim"

    def __init__(self, home: tuple[float, float] = (0.0, 0.0), spec=None,
                 time_scale: float = 4.0, battery_pct: float = 96.0, **_):
        from ..mission import DroneSpec
        self.spec = spec or _project_spec()
        self.home = home
        self.frame = LocalFrame(*home)
        self.scale = time_scale
        self.t = Telemetry(connected=False, lat=home[0], lon=home[1], battery_pct=battery_pct,
                           gps_fix=3, satellites=14, backend="sim", mode="STANDBY")
        self._mission = None
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def connect(self) -> Telemetry:
        self.t.connected = True
        self.t.message = "simulated vehicle connected"
        return self.t

    def preflight(self) -> dict:
        issues = []
        if not self.t.connected:
            issues.append("not connected")
        if self.t.gps_fix < 3:
            issues.append("no 3D GPS fix")
        if self.t.battery_pct < 60:
            issues.append(f"battery {self.t.battery_pct:.0f}% < 60%")
        if self.t.state in ("flying", "returning"):
            issues.append("vehicle already flying")
        return {"ok": not issues, "issues": issues, "battery_pct": self.t.battery_pct,
                "gps_fix": self.t.gps_fix, "backend": "sim"}

    def swap_battery(self, pct: float = 96.0) -> None:
        """The ground crew fitted a charged battery between sorties (simulation only)."""
        if self.t.state not in ("flying", "returning"):
            self.t.battery_pct = pct

    def upload(self, mission) -> None:
        self._mission = mission
        self.t.mission_total = len(mission.items)
        self.t.mission_seq = 0
        self.t.state = "uploaded"
        self.t.message = f"mission {mission.mission_id} uploaded ({len(mission.items)} items)"

    def start(self) -> None:
        if self._mission is None:
            raise RuntimeError("no mission uploaded")
        self._abort.clear()
        self._thread = threading.Thread(target=self._fly, daemon=True)
        self._thread.start()

    def abort(self) -> None:
        self._abort.set()
        self.t.message = "ABORT: returning to launch"

    def telemetry(self) -> Telemetry:
        with self._lock:
            return self.t

    # -- flight loop --------------------------------------------------------
    ACCEL_MS2 = 2.0          # agricultural multirotor: slows into every turn, speeds up out of it

    @staticmethod
    def _profile(dist: float, v: float, a: float, dt: float = 0.2) -> list[tuple[float, float]]:
        """(fraction of the leg done, ground speed) every ``dt`` s on a trapezoidal profile."""
        if dist < 1e-6:
            return [(1.0, 0.0)]
        ta = v / a
        if a * ta * ta >= dist:                        # too short to reach v: triangle
            ta = math.sqrt(dist / a)
            v = a * ta
        cruise = (dist - a * ta * ta) / v
        total = 2 * ta + cruise
        out, t = [], dt
        while t < total:
            if t < ta:
                s, sp = 0.5 * a * t * t, a * t
            elif t < ta + cruise:
                s, sp = 0.5 * a * ta * ta + v * (t - ta), v
            else:
                r = total - t
                s, sp = dist - 0.5 * a * r * r, a * r
            out.append((s / dist, sp))
            t += dt
        out.append((1.0, 0.0))
        return out

    def _move_to(self, x: float, y: float, alt: float, speed: float) -> bool:
        cx, cy = self.frame.to_local(self.t.lat, self.t.lon)
        dist = math.hypot(x - cx, y - cy)
        dz = alt - self.t.alt_m
        prof = self._profile(dist, speed, self.ACCEL_MS2)
        steps = max(len(prof), int(abs(dz) / self.spec.climb_speed_ms * 5), 1)
        if steps > len(prof):                          # vertical move dominates
            prof = [(k / steps, 0.0 if dist < 0.1 else speed) for k in range(1, steps + 1)]
        z0 = self.t.alt_m
        for f, sp in prof:
            if self._abort.is_set():
                return False
            with self._lock:
                self.t.lat, self.t.lon = self.frame.to_wgs84(cx + (x - cx) * f, cy + (y - cy) * f)
                self.t.alt_m = z0 + dz * f
                self.t.groundspeed_ms = round(sp, 2)
                p = self.spec.cruise_power_w if dist > 0.1 else self.spec.hover_power_w
                self.t.battery_pct -= (p * 0.2 / 3600) / self.spec.battery_wh * 100
                self.t.trail.append([round(self.t.lat, 7), round(self.t.lon, 7), self.t.spraying])
            time.sleep(0.2 / self.scale)
        return True

    def _fly(self) -> None:
        m = self._mission
        self.t.armed, self.t.state, self.t.mode = True, "flying", "AUTO"
        try:
            for it in m.items:
                if self._abort.is_set():
                    break
                self.t.mission_seq = it.seq
                if it.kind == "takeoff":
                    self.t.message = f"taking off to {it.alt_m:.0f} m"
                    self._move_to(0.0, 0.0, it.alt_m, 1.0)
                elif it.kind == "spray_on":
                    self.t.spraying, self.t.zone_id = True, it.zone_id
                    self.t.message = f"spraying zone {it.zone_id}"
                elif it.kind == "spray_off":
                    self.t.spraying = False
                    self.t.message = f"zone {it.zone_id} done"
                elif it.kind == "waypoint":
                    x, y = self.frame.to_local(it.lat, it.lon)
                    sp = self.spec.spray_speed_ms if self.t.spraying else self.spec.transit_speed_ms
                    self._move_to(x, y, it.alt_m, sp)
                elif it.kind == "rtl":
                    break
            # Return to launch and land -- also the abort path.
            aborted = self._abort.is_set()
            self.t.spraying = False
            self.t.state, self.t.mode = "returning", "RTL"
            self.t.message = "ABORT: returning to launch" if aborted else "returning to launch"
            self._abort.clear()
            self._move_to(0.0, 0.0, max(self.t.alt_m, 3.0), self.spec.transit_speed_ms)
            self._move_to(0.0, 0.0, 0.0, 1.0)
            self.t.armed, self.t.mode = False, "LAND"
            self.t.state = "aborted" if aborted else "landed"
            self.t.message = "aborted: landed at home, disarmed" if aborted else "mission complete: landed, disarmed"
        except Exception as e:  # noqa: BLE001
            self.t.state, self.t.message = "error", f"sim error: {e}"


def _project_spec():
    """The project airframe (T1 10 L quad) unless a spec is passed."""
    from fieldpilot.mission import drone_spec as _ds
    return _ds(None)
