"""How long ArduCopter takes to fly a FieldPilot mission, from the firmware's own parameters.

FieldPilot's generic estimate flies every leg at constant speed and allows a fixed climb and
descent. ArduCopter (4.x) moves along jerk-limited S-curves, slows at every corner, returns at
RTL_ALT, waits RTL_LOIT_TIME above home and lands at LAND_SPEED below LAND_ALT_LOW. On long lines
the two agree; on short lines and in the return they do not. This model takes the parameter
values the aircraft will fly with (sim/t1_sitl.parm, or the real controller's parameter file)
and predicts the time per phase. Nothing in it is fitted to the SITL logs it is compared with.
"""
from __future__ import annotations

import math
from pathlib import Path

# ArduCopter 4.5 defaults (libraries/AC_WPNav, ArduCopter/config.h, Parameters.cpp)
DEFAULTS = {"WPNAV_SPEED": 500, "WPNAV_ACCEL": 250, "WPNAV_JERK": 1.0, "WPNAV_SPEED_UP": 250,
            "WPNAV_SPEED_DN": 150, "WPNAV_ACCEL_Z": 100, "RTL_ALT": 1500, "RTL_SPEED": 0,
            "RTL_LOIT_TIME": 5000, "RTL_CLIMB_MIN": 0, "LAND_SPEED": 50, "LAND_SPEED_HIGH": 0,
            "LAND_ALT_LOW": 1000, "PSC_JERK_Z": 5.0}
AIRBORNE_M = 0.5            # the SITL analysis counts flight time above this height


def read_parm(path: str | Path) -> dict:
    """An ArduPilot .parm file as {name: value}, over the firmware defaults."""
    p = dict(DEFAULTS)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.replace(",", " ").split()
        if len(parts) >= 2:
            try:
                p[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return p


def scurve_leg(d: float, v: float, a: float, j: float) -> tuple[float, float]:
    """Rest-to-rest over ``d`` m with speed limit ``v``, acceleration ``a`` and jerk ``j``:
    (total time, time of one speed ramp)."""
    if d <= 0 or v <= 0:
        return 0.0, 0.0

    def ramp(vp: float) -> tuple[float, float]:          # time and distance from rest to vp
        t = vp / a + a / j if vp >= a * a / j else 2.0 * math.sqrt(vp / j)
        return t, vp * t / 2.0

    t1, d1 = ramp(v)
    if d >= 2 * d1:
        return 2 * t1 + (d - 2 * d1) / v, t1
    lo, hi = 0.0, v
    for _ in range(50):
        mid = (lo + hi) / 2
        if 2 * ramp(mid)[1] > d:
            hi = mid
        else:
            lo = mid
    t = ramp(lo)[0]
    return 2 * t, t


def scurve_s(d: float, v: float, a: float, j: float) -> float:
    return scurve_leg(d, v, a, j)[0]


def mission_time(mission, frame, home: tuple[float, float], spec, parm: dict) -> dict:
    """Predicted time per phase for one sortie, as ArduCopter flies it (seconds)."""
    a = parm["WPNAV_ACCEL"] / 100.0
    j = parm["WPNAV_JERK"]
    up = parm["WPNAV_SPEED_UP"] / 100.0
    az = parm["WPNAV_ACCEL_Z"] / 100.0
    jz = parm["PSC_JERK_Z"]
    alt = next((i.alt_m for i in mission.items if i.kind == "takeoff"), 2.5)
    t_climb = scurve_s(alt - AIRBORNE_M, up, az, jz)
    here = frame.to_local(*home)
    v = parm["WPNAV_SPEED"] / 100.0                     # until the first DO_CHANGE_SPEED
    items = mission.items
    first_on = next((k for k, i in enumerate(items) if i.kind == "spray_on"), len(items))
    last_off = max((k for k, i in enumerate(items) if i.kind == "spray_off"), default=len(items))
    # phases as the SITL analysis cuts them: up to the first pump-on, pump-on to last pump-off
    # (headland side-steps included), then the rest
    # ArduCopter flies "fast waypoints": the next leg's S-curve starts while this one brakes, so
    # each corner overlaps one speed ramp of the two legs (the waypoint counts as reached there)
    legs = []                                            # (phase, time, ramp)
    for k, it in enumerate(items):
        if it.kind == "spray_on":
            v = spec.spray_speed_ms
        elif it.kind == "spray_off":
            v = spec.transit_speed_ms
        elif it.kind == "waypoint":
            p = frame.to_local(it.lat, it.lon)
            t, r = scurve_leg(math.dist(here, p), v, a, j)
            legs.append([0 if k < first_on else 1 if k < last_off else 2, t, r])
            here = p
    for cur, nxt in zip(legs, legs[1:]):
        cur[1] -= min(cur[2], nxt[2])
    transit = sum(t for ph, t, _ in legs if ph == 0)
    spray = sum(t for ph, t, _ in legs if ph == 1)
    after = sum(t for ph, t, _ in legs if ph == 2)
    # return: climb to RTL_ALT, fly home, wait, land
    rtl_alt = max(alt, parm["RTL_ALT"] / 100.0, alt + parm["RTL_CLIMB_MIN"] / 100.0)
    t_rtl_climb = scurve_s(rtl_alt - alt, up, az, jz)
    v_rtl = (parm["RTL_SPEED"] or parm["WPNAV_SPEED"]) / 100.0
    t_home = scurve_s(math.dist(here, frame.to_local(*home)), v_rtl, a, j)
    low = parm["LAND_ALT_LOW"] / 100.0
    v_hi = (parm["LAND_SPEED_HIGH"] or parm["WPNAV_SPEED_DN"]) / 100.0
    v_lo = parm["LAND_SPEED"] / 100.0
    t_land = max(0.0, rtl_alt - low) / v_hi + (min(rtl_alt, low) - AIRBORNE_M) / v_lo
    ret = after + t_rtl_climb + t_home + parm["RTL_LOIT_TIME"] / 1000.0 + t_land
    return {"takeoff_and_transit_s": round(t_climb + transit, 1), "spraying_s": round(spray, 1),
            "return_and_land_s": round(ret, 1), "total_s": round(t_climb + transit + spray + ret, 1)}


def mission_energy_wh(t: dict, mission, spec) -> float:
    """Energy at hover power for the sortie's mean mass (half its load on board on average)."""
    return round(t["total_s"] * spec.hover_w(mission.spray_ml / 2.0) / 3600.0, 1)
