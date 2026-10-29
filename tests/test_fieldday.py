"""The simulated field day runs the production code end to end, labelled, and reproducibly."""
from __future__ import annotations

import json
from datetime import date

from fieldpilot import fieldday


def test_field_day_runs_and_is_labelled_and_reproducible(tmp_path):
    a = fieldday.run(date(2026, 10, 30), seed=7, synthetic_weather=True, out_root=tmp_path / "a")
    b = fieldday.run(date(2026, 10, 30), seed=7, synthetic_weather=True, out_root=tmp_path / "b")
    ra = json.loads((a / "fieldday.json").read_text(encoding="utf-8"))
    rb = json.loads((b / "fieldday.json").read_text(encoding="utf-8"))
    assert ra["summary"] == rb["summary"] and ra["timeline"] == rb["timeline"]
    assert ra["label"].startswith("SIMULATED") and "SYNTHETIC" in ra["weather_source"]
    md = (a / "FIELD_DAY_REPORT.md").read_text(encoding="utf-8")
    assert "SIMULATED field day" in md and "Nothing here is a field measurement" in md
    s = ra["summary"]
    assert s["sorties_flown"] > 0 and s["farms_completed"] >= 1
    assert abs(s["spraylog_error_pct"]) < 2.0                       # the logger tells the truth
    # realistic GPS (u-blox + GAGAN), flow-meter and drift errors: still well on target
    assert s["on_target_pct_min"] > 90 and s["deposited_on_target_pct_min"] > 90
    assert s["zone_coverage_pct_min"] > 90
    for d in s["drift_compensation"]:           # moving the lines upwind never makes it worse
        assert d["with"]["litres_off_zones"] <= d["without"]["litres_off_zones"]
    # every sortie took off with the mast at GO and inside the SOP hours
    for t in ra["timeline"]:
        assert "06:00" <= t["actual_start"] < "11:00" or "14:00" <= t["actual_start"] < "18:00"
    # device data was written as the devices would post it
    assert list((a / "devices").glob("spraylog_*.json")) and list((a / "devices").glob("mast_*.jsonl"))
