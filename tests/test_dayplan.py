"""Operator day planning: efficiency against the common way of working, same day, same batteries.

The forecast is a typical subtropical day: calm humid mornings, windy dry early afternoons,
calm again before sunset (hours in the operator's local time)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from fieldpilot import dayplan, rules, sop
from fieldpilot.dayplan import JobSpec, Operator, compare, plan_day

DAY = date(2026, 10, 28)


def typical_day(utc_offset_h=5.5, windy_from=10, windy_to=15) -> list[sop.Hour]:
    t0 = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)
    out = []
    for h in range(96):
        t = t0 + timedelta(hours=h)
        local = (t + timedelta(hours=utc_offset_h)).hour
        windy = windy_from <= local < windy_to
        out.append(sop.Hour(t, 4.8 if windy else 1.8, 33.0 if windy else 24.0,
                            30.0 if windy else 70.0, 0.0, 0.0))
    return out


# kept for other test modules
october_day = typical_day


@pytest.fixture(scope="module")
def jobs():
    return [
        JobSpec("J1", "Ramesh", 28.351, 77.555, area_ha=1.0, spray_ha=0.35, crop="potato",
                problem="late blight", product="Mancozeb 75% WP", dose_per_ha=1000, village="Dankaur"),
        JobSpec("J2", "Sunita", 28.25, 77.60, area_ha=0.8, spray_ha=None, crop="mustard",
                problem="aphid", village="Rabupura"),
        JobSpec("J3", "Mahesh", 28.13, 77.55, area_ha=1.2, spray_ha=0.4, crop="potato",
                problem="late blight", product="Mancozeb 75% WP", dose_per_ha=1000, village="Jewar"),
        JobSpec("J4", "Kamla", 28.55, 77.55, area_ha=0.6, spray_ha=0.6, crop="wheat",
                problem="weeds", village="Dadri", urgency=1.3),
        JobSpec("J5", "Imran", 28.43, 77.63, area_ha=1.5, spray_ha=0.5, crop="potato",
                problem="late blight", village="Bilaspur"),
    ]


@pytest.fixture(scope="module")
def forecasts(jobs):
    fc = typical_day()
    return {j.job_id: fc for j in jobs}


def test_sorties_scale_with_area():
    a = dayplan.sorties_for(0.3, "agri_10l")
    b = dayplan.sorties_for(1.5, "agri_10l")
    assert a["n"] == 1 and b["n"] >= 3 and b["flight_min"] > a["flight_min"]


def test_optimised_sorties_beat_full_tanks_from_the_corner():
    for ha in (0.8, 1.5, 3.0):
        opt = dayplan.sorties_for(ha, "agri_10l", optimised=True)
        base = dayplan.sorties_for(ha, "agri_10l", optimised=False)
        assert opt["n"] <= base["n"]
        assert opt["flight_min"] <= base["flight_min"] + 1e-6
        # mixed to the litre, plus the last refill's margin so the route completes
        assert 0 < opt["mixed_l"] - opt["water_l"] <= 0.1 + 0.03 * 10 + 1e-9
        assert base["mixed_l"] > base["water_l"]                      # full tanks leave mix over


def test_every_sortie_is_inside_the_rule_pack_window(jobs, forecasts):
    op = Operator()
    p = plan_day(jobs, op, DAY, forecasts, "fieldpilot")
    assert p["metrics"]["out_of_window_sorties"] == 0
    for r in p["plan"]:
        for s in r["sorties"]:
            # India is UTC+5:30 and the forecast is hourly in UTC, so the windy block in local
            # time runs 10:30-15:30 (the hour starting 09:30 local is still calm).
            assert s["in_window"] and not ("10:30" <= s["start"] < "15:30")
            # and spray hours are judged by the local clock, not by the forecast's hour
            assert any(f"{a:02d}:00" <= s["start"] and s["end"] <= f"{b:02d}:00" for a, b in op.pack.hours)


def test_india_window_is_judged_by_the_clock_not_the_half_hour_forecast():
    """UTC+5:30: the hourly forecast lands at :30 local. A sortie may start at 06:00 and must
    land by 11:00, whatever the forecast's hour boundaries."""
    from datetime import datetime, timedelta, timezone
    from fieldpilot.sop import Hour
    op = Operator(rules="IN-SOP-2023", utc_offset_h=5.5)
    t0 = datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc)
    fc = [Hour(t0 + timedelta(hours=k), 1.5, 25.0, 70.0, 0.0) for k in range(48)]
    tz = op.tz
    start = dayplan.next_window(fc, datetime(2026, 10, 30, 5, 40, tzinfo=tz), 8, op)
    assert start.strftime("%H:%M") == "06:00"
    assert dayplan.slot_ok(fc, datetime(2026, 10, 30, 10, 50, tzinfo=tz), datetime(2026, 10, 30, 10, 58, tzinfo=tz), op)
    assert not dayplan.slot_ok(fc, datetime(2026, 10, 30, 10, 55, tzinfo=tz), datetime(2026, 10, 30, 11, 3, tzinfo=tz), op)
    late = dayplan.next_window(fc, datetime(2026, 10, 30, 10, 55, tzinfo=tz), 8, op)
    assert late.strftime("%H:%M") == "14:00"


def test_batteries_are_never_double_booked(jobs, forecasts):
    p = plan_day(jobs, Operator(battery_sets=2, chargers=1), DAY, forecasts, "fieldpilot")
    flights = {}
    for b in p["batteries"]:
        flights.setdefault(b["set"], []).append(b)
    for runs in flights.values():
        for a, b in zip(runs, runs[1:]):
            assert b["flew"] >= a["ready"], (a, b)


def test_efficiency_against_the_common_way(jobs, forecasts):
    c = compare(jobs, Operator(), DAY, forecasts)
    fp, bl = c["fieldpilot"]["metrics"], c["baseline"]["metrics"]
    assert bl["out_of_window_sorties"] > 0 and fp["out_of_window_sorties"] == 0
    assert fp["sprayed_beyond_need_ha"] == 0 and bl["sprayed_beyond_need_ha"] > 0
    assert fp["leftover_mix_l"] <= 0.41 * fp["farms_served"] + 0.05      # last-refill margins only
    assert fp["leftover_mix_l"] < bl["leftover_mix_l"]
    assert fp["effective_ha_per_battery_cycle"] > bl["effective_ha_per_battery_cycle"]
    assert fp["farms_served_in_window"] >= bl["farms_served_in_window"]
    every = {j.job_id for j in jobs}
    assert {r["job_id"] for r in c["fieldpilot"]["plan"]} | {d["job_id"] for d in
                                                             c["fieldpilot"]["deferred"]} == every


def test_same_engine_anywhere_brazil_good_practice():
    """Mato Grosso, UTC-4, no drone-specific law: Delta T + wind good practice."""
    op = Operator(name="Pulverização MT", base=(-13.0, -55.9), country="BR", utc_offset_h=-4,
                  rules="GOOD-PRACTICE", language="pt", currency="R$", rate_per_ha=90.0,
                  battery_sets=4, chargers=2, day_start="05:30", day_end="18:30")
    js = [JobSpec("B1", "Fazenda A", -13.05, -55.90, 12.0, 4.0, crop="soybean"),
          JobSpec("B2", "Fazenda B", -12.95, -55.80, 8.0, None, crop="cotton")]
    fc = typical_day(utc_offset_h=-4)
    c = compare(js, op, DAY, {j.job_id: fc for j in js})
    fp = c["fieldpilot"]
    assert fp["rules"] == "GOOD-PRACTICE" and fp["metrics"]["out_of_window_sorties"] == 0
    assert all(not ("10:00" <= s["start"] < "15:00") for r in fp["plan"] for s in r["sorties"])


def test_delta_t_pack():
    gp = rules.pack("GOOD-PRACTICE")
    noon = sop.Hour(datetime(2026, 10, 28, 6, tzinfo=timezone.utc), 2.0, 36.0, 20.0, 0.0)
    morning = sop.Hour(datetime(2026, 10, 28, 1, tzinfo=timezone.utc), 2.0, 22.0, 70.0, 0.0)
    assert rules.delta_t(36.0, 20.0) > 10 and 2 <= rules.delta_t(22.0, 70.0) <= 8
    assert not gp.ok(noon, 5.5) and any("Delta T" in r for r in gp.reasons(noon, 5.5))
    assert gp.ok(morning, 5.5)
    calm = sop.Hour(morning.time, 0.4, 22.0, 70.0, 0.0)
    assert any("inversion" in r for r in gp.reasons(calm, 5.5))


def test_deferred_jobs_get_the_next_window(jobs, forecasts):
    op = Operator(battery_sets=1, chargers=1, day_end="09:00")
    p = plan_day(jobs, op, DAY, forecasts, "fieldpilot")
    assert p["deferred"]
    assert all(d["next_window"] != "none in the forecast" for d in p["deferred"])


def test_day_sheet_renders(jobs, forecasts):
    p = plan_day(jobs, Operator(), DAY, forecasts, "fieldpilot")
    html = dayplan.day_sheet_html(p, Operator())
    assert "Batteries" in html and "mixed per refill" in html
