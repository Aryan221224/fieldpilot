"""The K3-A Pro twin flies FieldPilot's flight card; the plan must survive the controller."""
from __future__ import annotations

from dataclasses import replace

import pytest

from fieldpilot import k3sim
from fieldpilot.field import grid_field
from fieldpilot.mission import drone_spec
from fieldpilot.sorties import best_takeoff, load_sheet, plan_sorties


@pytest.fixture(scope="module")
def spec():
    return drone_spec(None)


def _fly(spec, w, h, c, r):
    f = grid_field("t", (28.4499, 77.5837), w, h, c, r)
    z = f.zone_ids()
    home, _, _ = best_takeoff(f, z, spec)
    f = replace(f, home=home)
    parts = plan_sorties(f, z, spec)
    sheet = load_sheet(parts, spec)
    flown = k3sim.fly_card(f, z, spec, [row["water_l"] for row in sheet["rows"]])
    return f, z, parts, sheet, flown


@pytest.mark.parametrize("dims", [(100, 100, 2, 2), (150, 120, 3, 3), (200, 50, 4, 1)])
def test_the_controller_flies_the_plan_it_was_given(spec, dims):
    f, z, parts, sheet, flown = _fly(spec, *dims)
    assert len(flown) == len(parts)                                  # no extra micro-sortie
    assert all(s.end == "low_liquid" for s in flown[:-1]) and flown[-1].end == "route_complete"
    cmp = k3sim.compare_with_plan(f, parts, flown)
    assert cmp["max_cut_error_m"] is None or cmp["max_cut_error_m"] < 5.0     # tank dry at the cut
    for a, b in zip(parts, flown):
        assert b.flight_s == pytest.approx(a.est_time_s, rel=0.08)          # plan time within 8 %
        assert b.battery_left_pct > 100 * spec.reserve_frac                 # never into the reserve
    cov = k3sim.coverage(f, z, flown, spec.swath_m)
    assert min(v["covered_pct"] for v in cov.values()) >= 98.0
    left = flown[-1].left_in_tank_l
    assert 0 < left <= sheet["rows"][-1]["margin_l"] + 0.05              # leftover = the margin


def test_breakpoint_resume_continues_where_it_stopped(spec):
    f, z, parts, sheet, flown = _fly(spec, 100, 100, 2, 2)
    for a, b in zip(flown, flown[1:]):
        ev = [e for e in b.events if e["event"] == "resume_at_breakpoint"]
        assert ev and abs(ev[0]["x"] - a.breakpoint_xy[0]) < 0.05 and abs(ev[0]["y"] - a.breakpoint_xy[1]) < 0.05


def test_low_battery_returns_with_energy_to_land(spec):
    small = replace(spec, battery_wh=250.0)
    f = grid_field("t", (28.4499, 77.5837), 100, 100, 2, 2)
    flown = k3sim.fly_card(f, f.zone_ids(), small, [10.0])
    assert flown[0].end == "low_battery"
    assert flown[0].energy_wh <= small.battery_wh * (1 - small.reserve_frac) + 1.0


def test_pump_stops_below_cutoff_and_in_turns(spec):
    f, z, parts, sheet, flown = _fly(spec, 60, 40, 3, 2)
    for so in flown:
        for r in so.track:
            if r[4] < spec.pump_cutoff_ms - 1e-9 and r[5]:
                # flow can only persist for the pump's lag after the command stops
                assert r[6] < spec.flow_l_min(spec.spray_speed_ms)
