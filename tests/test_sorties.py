"""Sortie planning: big fields are cut into flyable sorties, nothing missed or sprayed twice."""
from datetime import date

import pytest

from fieldpilot.field import grid_field
from fieldpilot.mission import DroneSpec, build_mission, drone_spec, sortie_mission
from fieldpilot.sorties import load_sheet, plan_sorties


@pytest.fixture(scope="module")
def hectare():
    return grid_field("1 ha", (28.45, 77.58), 100, 100, 2, 2,
                      compliance={"product": "Mancozeb 75% WP", "dose_per_ha": 1000})


def test_one_hectare_on_a_10l_drone_needs_three_balanced_sorties(hectare):
    """1 ha at 20 L/ha is 20 L on paper; 50 m zones with a 4 m swath need 13 passes, so the
    last strip overlaps and 20.8 L are sprayed: more than two 10 L tanks."""
    import math
    spec = drone_spec("agri_10l")
    m = build_mission(hectare, hectare.zone_ids(), [], spec)
    assert m.ok, [c for c in m.checks if not c["ok"]]
    assert 20.0 < m.load["water_l"] < 21.2
    assert len(m.sorties) == math.ceil(m.load["water_l"] / 10.0) == 3
    loads = [r["water_l"] for r in m.load["rows"]]
    assert max(loads) - min(loads) < 0.2                     # balanced, no sliver of a last tank
    assert m.load["battery_swaps"] == 2 and m.load["refills"] == 2
    # the second sortie resumes the pass the first one could not finish
    assert m.sorties[1]["resume"] is not None
    assert hectare.inside_geofence(m.sorties[1]["resume"]["lat"], m.sorties[1]["resume"]["lon"])


def test_cutting_does_not_change_what_is_sprayed(hectare):
    big = DroneSpec(battery_wh=1e6, tank_ml=1e9, max_flight_s=1e9, max_range_m=1e6)
    one = plan_sorties(hectare, hectare.zone_ids(), big)
    many = plan_sorties(hectare, hectare.zone_ids(), DroneSpec(tank_ml=2500.0, battery_wh=200.0,
                                                               max_range_m=1e6))
    assert len(one) == 1 and len(many) > 3
    total = sum(s.spray_m for s in many)
    assert abs(total - one[0].spray_m) < 0.01 * one[0].spray_m       # within 1 %
    for s in many:
        kinds = [i.kind for i in s.items]
        assert kinds[0] == "takeoff" and kinds[-1] == "rtl"
        assert kinds.count("spray_on") == kinds.count("spray_off")


def test_load_sheet_uses_drone_volume_not_knapsack_dilution(hectare):
    spec = drone_spec("agri_10l")
    parts = plan_sorties(hectare, hectare.zone_ids(), spec)
    sheet = load_sheet(parts, spec, dose_per_ha=1000, unit="g", product="Mancozeb 75% WP")
    first = sheet["rows"][0]
    # 1000 g/ha at 20 L/ha -> 50 g per litre of drone tank, whatever the load
    assert first["product"] == pytest.approx(first["water_l"] * 50, rel=0.01)
    # balanced loads: no near-empty last sortie
    loads = [r["water_l"] for r in sheet["rows"]]
    assert max(loads) - min(loads) < 0.05 * max(loads)
    assert sheet["product_total"] == pytest.approx(sheet["water_l"] * 50, rel=0.01)


def test_sortie_mission_is_uploadable(hectare):
    m = build_mission(hectare, hectare.zone_ids(), [], drone_spec("agri_10l"))
    s2 = sortie_mission(m, 1)
    assert s2.mission_id.endswith("-S2")
    assert [i.seq for i in s2.items] == list(range(len(s2.items)))
    assert s2.items[0].kind == "takeoff" and s2.items[-1].kind == "rtl"


def test_approve_flies_one_sortie_at_a_time(tmp_path, monkeypatch, demo_images):
    monkeypatch.setenv("FP_RUNS_DIR", str(tmp_path / "runs"))
    from fieldpilot.service import FieldPilot
    fp = FieldPilot(today=date(2026, 9, 21), ensemble=(2, 2))
    fp.spec = DroneSpec(tank_ml=150.0)            # a tiny tank forces several sorties on the demo plot
    s = fp.new_run(demo_images)
    n = len(s.mission.sorties)
    assert n > 1 and s.mission.ok
    reason = "GP notice sent 26 Oct; RPC on file"
    fp.drone.scale = 400.0
    import time
    for k in range(1, n + 1):
        t0 = time.time()
        while fp.drone.telemetry().state in ("flying", "returning", "uploaded") and time.time() - t0 < 20:
            time.sleep(0.05)
        r = fp.approve(s.run_id, "op", override_reason=reason)
        assert r["dispatched"] and r["sortie"] == k and r["of"] == n
        if k < n:
            assert "swap battery" in r["next"]
            assert s.proposal["status"] == "awaiting_approval"
    assert s.proposal["status"] == "dispatched"
    assert len(s.proposal["approvals"]) == n
