"""ArduPilot SITL tooling: the T1 frame at each sortie's mass, and reading a firmware log.
The flight itself runs only where ArduCopter SITL is built (FP_ARDUPILOT)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from fieldpilot import sitl

ROOT = Path(__file__).resolve().parents[1]


def _frame():
    txt = (ROOT / "sim" / "t1_frame.json").read_text(encoding="utf-8")
    body = re.sub(r"#[^\n\r]*", "", txt[txt.index("{"):])           # as ArduPilot's loader does
    return json.loads(body)


def test_t1_frame_matches_the_airframe_model():
    from fieldpilot import airframe
    from fieldpilot.mission import drone_spec
    f, s = _frame(), drone_spec(None)
    assert f["mass"] == s.ref_mass_kg == 24.0 and f["diagonal_size"] == 1.30 and f["num_motors"] == 4
    assert f["battCapacityAh"] == 16 and f["maxVoltage"] == 50.4
    assert f["refCurrent"] * f["refVoltage"] == pytest.approx(s.cruise_power_w, rel=0.01)
    assert f["disc_area"] == pytest.approx(4 * 3.14159 * (34.7 * 0.0254 / 2) ** 2, rel=0.01)
    parm = (ROOT / "sim" / "t1_sitl.parm").read_text()
    for k, v in airframe.ardupilot_params().items():
        m = re.search(rf"^{k}\s+(\S+)", parm, re.M)
        assert m and float(m.group(1)) == pytest.approx(float(v)), k


def test_frame_scales_with_sortie_mass(tmp_path):
    full, light = sitl.frame_for_mass(24.0), sitl.frame_for_mass(15.0)
    assert full["hoverThrOut"] == 0.52 and full["refCurrent"] == 67.7
    assert light["hoverThrOut"] < full["hoverThrOut"] and light["refCurrent"] == pytest.approx(67.7 * (15 / 24) ** 1.5, abs=0.1)
    adj = sitl._write_frame(tmp_path, 15.0)
    txt = (tmp_path / "t1_frame.json").read_text()
    assert f'"mass" : {adj["mass"]}' in txt and f'"hoverThrOut" : {adj["hoverThrOut"]}' in txt


def test_analyse_reads_a_firmware_log_against_the_plan():
    """A hand-made log: a straight pass across zone A2 at 4.5 m/s with the sprayer on."""
    from dataclasses import replace

    from fieldpilot.field import Field
    from fieldpilot.mission import build_mission, drone_spec
    spec = drone_spec(None)
    f = Field.load(ROOT / "examples" / "field_demo.json")
    m = build_mission(f, ["A2"], ["t"], spec)
    pz = f.zone("A2").polygon
    lat0 = min(p[0] for p in pz) + 1e-5
    la = (min(p[0] for p in pz) + max(p[0] for p in pz)) / 2
    lo0, lo1 = min(p[1] for p in pz), max(p[1] for p in pz)
    pos, sim_, rcou, gps, bat = [], [], [], [], []
    n = 50
    for i in range(n + 1):
        t = 10 + i * 0.1
        lon = lo0 + (lo1 - lo0) * i / n
        pos.append((t, la, lon, 3.0))
        sim_.append((t, la, lon, 203.0))
        rcou.append((t, 1100 + int(800 * 0.617)))
        gps.append((t, 4.5))
        bat.append((t, 47.0, 40.0, 50.0 * i / n, 2.0 * i / n))
    log = {"pos": pos, "sim": sim_, "rcou": rcou, "gps": gps, "bat": bat, "modes": [],
           "cmds": [(10.0, 216, 1.0), (15.0, 216, 0.0)]}
    r = sitl.analyse(log, f, m, spec, site={"wind": 0.0, "wind_dir": 0.0, "turb": 0, "gps_noise": 0})
    assert r["peak_lpm"] == pytest.approx(0.617 * 3.5, rel=0.02)
    assert r["deposited_on_target_pct"] > 95 and r["phases"]["spraying_s"] == 5.0
    assert r["xte_true_m"]["median"] is not None


@pytest.mark.skipif(not os.environ.get("FP_ARDUPILOT"), reason="ArduCopter SITL not built (set FP_ARDUPILOT)")
def test_the_firmware_flies_the_demo_sortie():
    import shutil
    out = sitl.run(Path(os.environ["FP_ARDUPILOT"]), "demo", speedup=8)
    try:
        rep = json.loads((out / "sitl.json").read_text())
        s = rep["sorties"][0]
        assert s["litres"] == pytest.approx(s["planned_litres"], rel=0.10)
        assert s["deposited_on_target_pct"] > 90 and s["xte_true_m"]["p95"] < 1.0
        # the timing model from the firmware's parameters lands within 15 % of the firmware
        assert s["ardupilot_model"]["total_s"] == pytest.approx(s["flight_s"], rel=0.15)
    finally:
        shutil.rmtree(out, ignore_errors=True)      # the saved runs in runs/sitl stay as they are


def test_saved_firmware_runs_are_served_to_the_dashboard():
    from fastapi.testclient import TestClient

    from fieldpilot.api import app
    c = TestClient(app)
    runs = c.get("/api/sitl").json()
    assert isinstance(runs, list)
    if not runs:
        pytest.skip("no saved SITL run in runs/sitl")
    r = c.get("/api/sitl/" + runs[0]["run"]).json()
    assert r["label"].startswith("SIMULATED") and set(r["zones"]) <= set(r["zone_xy"])
    assert all("ardupilot_model" in s for s in r["sorties"])
    assert c.get("/api/sitl/19990101T000000Z").status_code == 404
