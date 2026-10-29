"""The efficiency benchmark runs end to end on a stated forecast and reports consistent numbers."""
import importlib.util
from datetime import date
from pathlib import Path

from test_dayplan import typical_day

ROOT = Path(__file__).resolve().parents[1]


def _bench():
    spec = importlib.util.spec_from_file_location("efficiency_bench", ROOT / "eval" / "efficiency_bench.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_benchmark_runs_and_fieldpilot_is_never_worse(tmp_path):
    b = _bench()
    regions = {k: v for k, v in b.REGIONS.items() if k.startswith(("IN", "BR"))}
    res = b.run([date(2026, 10, 28)], lambda lat, lon: typical_day(utc_offset_h=round(lon / 15)),
                regions)
    s = res["summary"]
    base, ns, ws = s["baseline"], s["fieldpilot_no_scan"], s["fieldpilot_with_scan"]
    assert ns["out_of_window_sorties"] == 0 and ws["out_of_window_sorties"] == 0
    assert base["out_of_window_sorties"] > 0
    assert ns["leftover_mix_l"] < base["leftover_mix_l"] and ws["sprayed_beyond_need_ha"] == 0
    assert base["sprayed_beyond_need_ha"] > 0 and base["leftover_mix_l"] > 0
    assert ws["effective_ha_per_battery_cycle"] > base["effective_ha_per_battery_cycle"]
    assert ns["effective_ha_per_battery_cycle"] >= base["effective_ha_per_battery_cycle"]
    assert "region-days" in b.sentence(res)
    b.chart(res, tmp_path / "c.png")
    assert (tmp_path / "c.png").stat().st_size > 1000
