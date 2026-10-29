"""The ArduPilot timing model (fieldpilot/ap_timing.py) and the pass-count rounding fix."""
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from fieldpilot.ap_timing import mission_time, read_parm, scurve_s
from fieldpilot.drift import compensated_field
from fieldpilot.field import Field
from fieldpilot.mission import _lawnmower, build_mission, drone_spec
from fieldpilot.sorties import best_takeoff

ROOT = Path(__file__).resolve().parents[1]


def test_scurve_long_leg_has_a_cruise():
    # ramp to 4.5 m/s at 1.5 m/s2 and 1 m/s3: 4.5 s over 10.125 m, then cruise
    assert scurve_s(100, 4.5, 1.5, 1.0) == pytest.approx(9.0 + (100 - 20.25) / 4.5, rel=1e-6)


def test_scurve_short_leg_never_reaches_speed():
    # 4 m side-step: peak v with 2 v^1.5 = 4, i.e. 1.587 m/s, 2 x 2.52 s
    assert scurve_s(4, 4.0, 1.5, 1.0) == pytest.approx(5.04, abs=0.02)
    assert scurve_s(4, 4.0, 1.5, 1.0) > 4 / 4.0 * 4


def test_scurve_monotonic():
    ts = [scurve_s(d, 4.5, 1.5, 1.0) for d in (1, 2, 5, 10, 20, 21, 40, 80)]
    assert ts == sorted(ts)


def test_parm_file_and_defaults():
    p = read_parm(ROOT / "sim" / "t1_sitl.parm")
    assert p["RTL_ALT"] == 1000 and p["LAND_SPEED"] == 50 and p["WPNAV_ACCEL"] == 150
    assert p["WPNAV_JERK"] == 1.0 and p["RTL_LOIT_TIME"] == 5000      # firmware defaults


def test_demo_sortie_model_against_generic_estimate():
    spec = drone_spec(None)
    f = Field.load(ROOT / "examples" / "field_demo.json")
    zones = ["A2", "B2"]
    home, _, _ = best_takeoff(f, zones, spec)
    f = replace(f, home=home)
    m = build_mission(f, zones, ["t"], spec)
    t = mission_time(m, f.frame, f.home, spec, read_parm(ROOT / "sim" / "t1_sitl.parm"))
    # RTL to 10 m, 5 s wait, 9.5 m down at 0.5 m/s: about 43 s (the SITL log: 42.1 s)
    assert 38 < t["return_and_land_s"] < 48
    # short lines: the firmware is much slower than constant-speed legs (SITL: 150 s vs 86 s)
    assert t["total_s"] > 1.4 * m.est_time_s
    assert t["total_s"] == pytest.approx(sum(t[k] for k in ("takeoff_and_transit_s", "spraying_s",
                                                            "return_and_land_s")), abs=0.2)


def test_coordinate_rounding_does_not_add_a_pass():
    spec = drone_spec(None)
    f = Field.load(ROOT / "examples" / "field_demo.json")
    g, info = compensated_field(f, 1.6, 290)
    assert info["applied"]
    for z in ("A2", "B2"):
        n0 = len(_lawnmower(Polygon([f.frame.to_local(a, b) for a, b in f.zone(z).polygon]), spec.swath_m))
        n1 = len(_lawnmower(Polygon([g.frame.to_local(a, b) for a, b in g.zone(z).polygon]), spec.swath_m))
        assert n0 == n1
