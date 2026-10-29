"""The drone-spraying SOPs as checks. Each test is one clause of SOP-2021 / SOP-2023."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fieldpilot import sop
from fieldpilot.sop import Hour, Plan

IST = sop.IST
T0 = datetime(2026, 10, 14, 7, 0, tzinfo=IST)          # 07:00 IST, inside the morning window


def calm(start=T0 - timedelta(hours=2), hours=72, over=None):
    base = dict(wind_ms=1.6, temp_c=27.0, rh_pct=68.0, precip_mm=0.0)
    out = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        kw = dict(base)
        for (a, b), vals in (over or {}).items():
            if a <= t < b:
                kw.update(vals)
        out.append(Hour(t, **kw))
    return out


def good_plan(**kw) -> Plan:
    p = dict(crop="rice", when=T0, duration_min=34, dvs=0.55,
             maturity=T0 + timedelta(days=60), height_m=2.0, speed_ms=4.8, swath_m=5.0,
             volume_l_ha=20.0, waiting_days=47, label_ok=True,
             notice_sent=T0 - timedelta(hours=30), pilot_dgca=True, buffer_m=180.0,
             forecast=calm())
    p.update(kw)
    return Plan(**p)


def rule(report, key):
    return next(c for c in report.checks if c.rule == key)


def test_a_compliant_plan_is_clear():
    r = sop.assess(good_plan(), now=T0 - timedelta(hours=1))
    assert r.verdict == "CLEAR", [(c.rule, c.status, c.value) for c in r.checks if c.ok is not True]
    assert r.headline() == "SOP: clear to fly"


def test_every_check_cites_a_clause():
    for c in sop.assess(good_plan()).checks:
        assert "SOP" in c.source or "CIB&RC" in c.source


def test_wind_over_3_ms_blocks_and_finds_the_next_calm_window():
    windy = calm(over={(T0 - timedelta(hours=1), T0 + timedelta(hours=3)): {"wind_ms": 4.2}})
    r = sop.assess(good_plan(forecast=windy), now=T0 - timedelta(hours=1))
    assert r.verdict == "BLOCKED" and rule(r, "wind").ok is False
    assert "4.2 m/s" in rule(r, "wind").value
    # calm again from 10:00 IST, still inside the 06-11 window
    assert r.earliest_clear is not None
    assert r.earliest_clear.astimezone(IST).hour in (10, 14)


def test_heat_and_dry_air_block():
    hot = calm(over={(T0 - timedelta(hours=1), T0 + timedelta(hours=2)): {"temp_c": 37.5, "rh_pct": 41}})
    r = sop.assess(good_plan(forecast=hot))
    assert rule(r, "temperature").ok is False and rule(r, "humidity").ok is False


def test_heavy_rain_forecast_within_48h_blocks():
    wet = calm(over={(T0 + timedelta(hours=20), T0 + timedelta(hours=26)): {"precip_mm": 3.0}})
    r = sop.assess(good_plan(forecast=wet))
    assert rule(r, "rain").ok is False and "18 mm next 48 h" in rule(r, "rain").value


def test_no_forecast_needs_a_human_not_a_guess():
    r = sop.assess(good_plan(forecast=[]))
    assert r.verdict == "NEEDS_HUMAN" and rule(r, "wind").ok is None


def test_flowering_rice_is_blocked_by_the_twins_stage():
    r = sop.assess(good_plan(dvs=1.02))
    assert r.verdict == "BLOCKED" and "flowering" in rule(r, "flowering").value


def test_wheat_has_no_flowering_rule_in_the_sop():
    r = sop.assess(good_plan(crop="wheat", dvs=1.02, volume_l_ha=25.0))
    assert all(c.rule != "flowering" for c in r.checks)


def test_pre_harvest_interval_against_the_twins_maturity_date():
    # chlorantraniliprole 18.5 SC on rice: 47-day waiting period; the twin says 30 days to harvest
    r = sop.assess(good_plan(maturity=T0 + timedelta(days=30), waiting_days=47))
    assert r.verdict == "BLOCKED"
    assert rule(r, "phi").ok is False and "30 d" in rule(r, "phi").value


def test_notice_missing_needs_human_and_short_notice_blocks():
    assert sop.assess(good_plan(notice_sent=None)).verdict == "NEEDS_HUMAN"
    r = sop.assess(good_plan(notice_sent=T0 - timedelta(hours=12)))
    assert r.verdict == "BLOCKED" and rule(r, "notice").value == "sent 12 h before"


def test_flight_parameters_from_the_crop_specific_sop():
    r = sop.assess(good_plan(height_m=3.2, speed_ms=6.0, swath_m=7.0))
    assert {c.rule for c in r.failed()} >= {"height", "speed", "swath"}
    soy = sop.assess(good_plan(crop="soybean", speed_ms=4.8))
    assert rule(soy, "speed").ok is False and rule(soy, "speed").limit == "3.5-4.5 m/s"


def test_water_volume_follows_the_canopy_stage():
    assert sop.water_volume("rice", 0.30) == 20.0
    assert sop.water_volume("rice", 0.90) == 25.0
    r = sop.assess(good_plan(dvs=0.80, volume_l_ha=20.0))
    assert rule(r, "volume").ok is False and "25 L/ha" in rule(r, "volume").limit


def test_spray_window_is_advice_and_never_blocks_alone():
    p = good_plan(when=datetime(2026, 10, 14, 12, 0, tzinfo=IST),
                  forecast=calm(start=datetime(2026, 10, 14, 9, 0, tzinfo=IST)))
    r = sop.assess(p)
    assert rule(r, "window").ok is False and r.verdict == "CLEAR"


def test_tank_mix_is_per_hectare_dose_over_drone_water_volume():
    # chlorantraniliprole 18.5 SC, rice: 150 ml formulation per ha (label, 500 L water)
    per_tank = sop.tank_mix(150.0, 25.0, 10.0)
    assert per_tank == pytest.approx(60.0)
    naive = 150.0 / 500.0 * 10.0            # "mix as per label dilution" in a 10 L tank
    assert per_tank / naive == pytest.approx(20.0)
    with pytest.raises(ValueError):
        sop.tank_mix(150.0, 0.0, 10.0)


def test_label_not_verified_needs_a_human():
    r = sop.assess(good_plan(label_ok=None))
    assert r.verdict == "NEEDS_HUMAN" and rule(r, "registered").ok is None
    assert sop.assess(good_plan(label_ok=False)).verdict == "BLOCKED"


def test_report_serialises():
    d = sop.assess(good_plan(dvs=1.0)).to_dict()
    assert d["verdict"] == "BLOCKED" and any(c["status"] == "FAIL" for c in d["checks"])


def test_parse_open_meteo():
    d = {"hourly": {"time": ["2026-10-14T01:00", "2026-10-14T02:00"],
                    "wind_speed_10m": [1.2, None], "temperature_2m": [26.1, 27.0],
                    "relative_humidity_2m": [71, 70], "precipitation": [0.0, 0.0],
                    "precipitation_probability": [5, 5]}}
    hs = sop.parse_open_meteo(d)
    assert len(hs) == 1 and hs[0].wind_ms == 1.2 and hs[0].time.tzinfo is timezone.utc
