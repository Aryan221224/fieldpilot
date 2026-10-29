"""Open-Meteo daily archive -> PCSE weather: units and gaps, without the network."""
from datetime import date, timedelta

import pytest

from fieldpilot import twin


def _fake(days=800, start=date(2023, 1, 1), gap_at=10):
    t = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    d = {"time": t, "temperature_2m_max": [30.0] * days, "temperature_2m_min": [18.0] * days,
         "shortwave_radiation_sum": [20.0] * days, "precipitation_sum": [5.0] * days,
         "wind_speed_10m_mean": [3.0] * days, "dew_point_2m_mean": [15.0] * days}
    d["temperature_2m_max"][gap_at] = None
    return {"elevation": 210.0, "daily": d}


def test_units_and_gaps():
    w = twin.weather_from_daily(_fake(), 28.45, 77.58)
    c = w(date(2023, 1, 2))
    assert c.IRRAD == pytest.approx(20e6)          # MJ/m2 -> J/m2
    assert c.RAIN == pytest.approx(0.5)             # mm -> cm
    assert c.WIND == pytest.approx(3.0 * 0.748)     # 10 m -> 2 m
    assert c.VAP == pytest.approx(17.04, abs=0.1)   # hPa from a 15 C dew point
    assert c.ET0 > 0
    with pytest.raises(Exception):
        w(date(2023, 1, 11))                        # the day with a gap is skipped
    assert "ERA5" in w.description[0]


def test_too_short_is_refused():
    with pytest.raises(ValueError):
        twin.weather_from_daily(_fake(days=100), 28.45, 77.58)


def test_the_twin_runs_on_it():
    today = date(2025, 10, 1)
    js = _fake(days=(today - date(2019, 9, 1)).days + 1, start=date(2019, 9, 1), gap_at=5)
    base = twin.weather_from_daily(js, 28.45, 77.58)
    ev = twin.evaluate("potato", "Potato_701", 28.45, 77.58, today, bbch=40, category="late_blight",
                       severity=2, zone_id="A1", n_weather=2, n_params=1, base=base)
    assert ev.weather_source.startswith("Open-Meteo ERA5")
    assert ev.feasible
