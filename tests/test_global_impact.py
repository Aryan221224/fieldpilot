import math

import pytest

from eval import global_impact as gi


@pytest.fixture(scope="module")
def bench():
    return gi.bench_totals()


def test_bench_rows_match_published_summary(bench):
    s = bench["summary"]
    for arm in ("baseline", "fieldpilot_with_scan"):
        assert bench[arm]["battery_cycles"] == s[arm]["battery_cycles"]
        assert bench[arm]["effective_ha"] == pytest.approx(s[arm]["effective_ha"], abs=0.2)
        assert bench[arm]["sprayed_ha"] == pytest.approx(s[arm]["sprayed_ha"], abs=0.2)
        assert bench[arm]["leftover_mix_l"] == pytest.approx(s[arm]["leftover_mix_l"], abs=0.2)


def test_gain_fraction_matches_summary_ratio(bench):
    s = bench["summary"]
    g = gi.gain_fractions(bench)
    ratio = s["baseline"]["effective_ha_per_battery_cycle"] / s["fieldpilot_with_scan"]["effective_ha_per_battery_cycle"]
    assert g["battery_cycles"] == pytest.approx(1 - ratio, abs=0.005)
    fm = s["fieldpilot_with_scan"]["flight_min_per_effective_ha"] / s["baseline"]["flight_min_per_effective_ha"]
    assert g["flight_min"] == pytest.approx(1 - fm, abs=0.005)
    for v in g.values():
        assert 0 < v < 1


def test_hand_calculation(bench):
    e = gi.estimate(gi.INPUTS, bench)
    ha = 203e6 + 6.64e6
    cycles_now = ha * 235 / bench["baseline"]["sprayed_ha"]
    cycles_saved = cycles_now * (1 - (133 / 175.3) / (235 / 74.3)) * 0.25
    assert e["saved_per_year"]["battery_cycles"] == pytest.approx(cycles_saved, rel=2e-3)
    kwh = 1.5666 * 0.8 / 0.9
    assert e["per_cycle"]["grid_kwh"] == pytest.approx(kwh, rel=1e-3)
    assert e["saved_per_year"]["energy_gwh"] == pytest.approx(cycles_saved * kwh / 1e6, rel=2e-3)
    assert e["saved_per_year"]["co2_tonnes"] == pytest.approx(cycles_saved * kwh * 0.445 / 1000, rel=2e-3)
    assert e["saved_per_year"]["battery_wear_usd"] == pytest.approx(cycles_saved * 2799 / 1500, rel=2e-3)


def test_fleet_cross_check_is_plausible(bench):
    # cycles implied by the treated area, spread over the fleet, must be a believable season
    per_drone = gi.estimate(gi.INPUTS, bench)["today"]["cycles_per_drone_per_year"]
    assert 100 < per_drone < 1000


def test_linear_in_realisation_and_zero_at_zero(bench):
    zero = gi.estimate({**gi.INPUTS, "realisation": 0.0}, bench)["saved_per_year"]
    assert all(v == 0 for v in zero.values())
    a = gi.estimate({**gi.INPUTS, "realisation": 0.1}, bench)["saved_per_year"]
    b = gi.estimate({**gi.INPUTS, "realisation": 0.5}, bench)["saved_per_year"]
    for k in a:
        assert b[k] == pytest.approx(5 * a[k], rel=1e-9)


def test_headline_numbers_used_in_video(bench):
    s = gi.estimate(gi.INPUTS, bench)["saved_per_year"]
    # the video rounds these down; keep the video honest if inputs change
    assert s["battery_cycles"] >= 35e6
    assert s["battery_wear_usd"] >= 65e6
    assert s["wasted_mix_litres"] >= 70e6
    assert s["energy_gwh"] >= 48
    assert s["co2_tonnes"] >= 21e3
    assert s["flight_hours"] >= 3.3e6
    assert not any(math.isnan(v) for v in s.values())
