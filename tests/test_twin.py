from datetime import date

import pytest

from fieldpilot import twin

TODAY = date(2026, 9, 21)
LAT, LON = 28.45, 77.58


@pytest.fixture(scope="module")
def base():
    return twin.base_weather(LAT, LON, TODAY, live=False)


def test_photoperiod_gate_refuses_european_wheat():
    ok, why = twin.feasibility("wheat", "Winter_wheat_101", LAT)
    assert not ok and "0 kg/ha with no error" in why[0]
    assert twin.feasibility("potato", "Potato_701", LAT)[0]


def test_bbch_mapping_monotonic():
    vals = [twin.bbch_to_dvs(b) for b in range(0, 98, 3)]
    assert vals == sorted(vals) and vals[0] == 0.0 and vals[-1] <= 2.0


def test_phenology_inversion_matches_thermal_time(base):
    p = twin.crop_params("maize", "Grain_maize_201", twin.ENGINE_72)
    em, acc = twin.infer_emergence(base, TODAY, 0.5, p)
    assert em < TODAY
    assert acc == pytest.approx(0.5 * float(p["TSUM1"]), rel=0.15)


def test_disease_coupling_actually_reduces_yield(base):
    """Regression: on WOFOST 8.1, set_variable('LAI') silently does nothing."""
    healthy = twin.evaluate("potato", "Potato_701", LAT, LON, TODAY, 31, "none", 0,
                            base=base, n_weather=2, n_params=2)
    sick = twin.evaluate("potato", "Potato_701", LAT, LON, TODAY, 31, "disease", 3,
                         base=base, n_weather=2, n_params=2)
    y_h = healthy.options[0]["yield_t_ha_p50"]
    y_s = next(o for o in sick.options if o["code"] == "no_action")["yield_t_ha_p50"]
    assert y_s < 0.9 * y_h
    assert sick.recommended == "spot_spray" and sick.options[0]["gain_vs_no_action_cvar"] > 0


def test_healthy_zone_is_left_alone(base):
    ev = twin.evaluate("potato", "Potato_701", LAT, LON, TODAY, 31, "none", 0,
                       base=base, n_weather=2, n_params=1)
    assert ev.recommended == "no_action" and len(ev.options) == 1


def test_small_gain_is_not_worth_flying(base):
    ev = twin.evaluate("potato", "Potato_701", LAT, LON, TODAY, 31, "disease", 1,
                       base=base, n_weather=2, n_params=2, min_gain_inr_ha=1e9)
    assert ev.recommended == "no_action" and "not worth" in ev.decision_reason


def test_nitrogen_question_uses_n_engine(base):
    ev = twin.evaluate("potato", "Potato_701", LAT, LON, TODAY, 31, "nutrient_deficiency", 3,
                       base=base, n_weather=2, n_params=1)
    assert ev.assumptions["engine"].startswith("Wofost81")
    assert ev.assumptions["initial_soil_n_kg_ha"] < 60
    no = next(o for o in ev.options if o["code"] == "no_action")
    top = next(o for o in ev.options if o["code"] == "topdress_n")
    assert top["yield_t_ha_p50"] > no["yield_t_ha_p50"]


def test_cvar_is_lower_tail_mean():
    import numpy as np
    x = np.arange(10.0)
    assert twin.cvar(x, 0.8) == pytest.approx(0.5)            # mean of worst 2
