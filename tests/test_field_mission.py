from fieldpilot.field import grid_field
from fieldpilot.mission import DroneSpec, build_mission, safety_checks


def test_grid_field_geometry():
    f = grid_field("t", (28.45, 77.58), 30, 20, 3, 2)
    assert f.zone_ids() == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert abs(f.area_m2() - (30 + 12) * (20 + 12)) < 2.0          # plot + 6 m margin
    assert f.inside_geofence(*f.home)
    assert all(f.inside_geofence(*z.centroid()) for z in f.zones)


def test_mission_passes_all_checks(field):
    m = build_mission(field, ["A2", "B2"], ["P-A2", "P-B2"])
    assert m.ok, m.checks
    kinds = [i.kind for i in m.items]
    assert kinds[0] == "takeoff" and kinds[-1] == "rtl"
    assert kinds.count("spray_on") == kinds.count("spray_off") >= 2      # on/off per pass
    zones_on = {i.zone_id for i in m.items if i.kind == "spray_on"}
    assert zones_on == {"A2", "B2"}
    # every geometric item stays inside the fence
    for i in m.items:
        if i.kind in ("takeoff", "waypoint", "rtl"):
            assert field.inside_geofence(i.lat, i.lon)


def test_oversized_route_is_split_into_sorties(field):
    """A route bigger than one battery/tank is cut into sorties instead of being refused."""
    spec = DroneSpec(battery_wh=40.0, tank_ml=300.0)
    m = build_mission(field, ["A1", "A2", "A3", "B1", "B2", "B3"], [], spec)
    assert len(m.sorties) > 1 and m.ok, m.checks
    for s in m.sorties:
        assert s["spray_ml"] <= spec.tank_ml + 1e-6
        assert s["est_energy_wh"] <= spec.usable_wh + 1e-6
        kinds = [i["kind"] for i in s["items"]]
        assert kinds[0] == "takeoff" and kinds[-1] == "rtl"
        assert kinds.count("spray_on") == kinds.count("spray_off")
    assert m.load["sorties"] == len(m.sorties) and m.load["battery_swaps"] == len(m.sorties) - 1


def test_energy_and_tank_limits_block_when_unflyable(field):
    """If even a fresh battery cannot reach the zone and come back, nothing is planned."""
    m = build_mission(field, ["A1", "A2", "A3", "B1", "B2", "B3"], [],
                      DroneSpec(battery_wh=0.5, tank_ml=100.0))
    failed = {c["check"] for c in m.checks if not c["ok"]}
    assert {"energy", "tank"} <= failed and not m.ok


def test_weather_and_preflight_are_checked(field):
    m = build_mission(field, ["A2"], [])
    checks = safety_checks(field, m, DroneSpec(), {"go": False, "reasons": ["wind 9 m/s"]},
                           {"ok": False, "issues": ["no GPS fix"]})
    failed = {c["check"] for c in checks if not c["ok"]}
    assert failed == {"weather", "preflight"}


def test_geofence_violation_detected(field):
    m = build_mission(field, ["A2"], [])
    wp = next(i for i in m.items if i.kind == "waypoint")
    wp.lat += 0.01                                          # ~1.1 km north
    failed = {c["check"] for c in safety_checks(field, m, DroneSpec()) if not c["ok"]}
    assert "geofence" in failed and "range" in failed
