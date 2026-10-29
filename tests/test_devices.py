"""Field devices: mast readings gate approval, SprayLog becomes as-applied, battery board."""
from __future__ import annotations

from dataclasses import replace

import pytest

from fieldpilot.devices import DeviceHub, as_applied, planned_litres
from fieldpilot.field import grid_field
from fieldpilot.mission import drone_spec


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def reading(**kw):
    return {"device_id": "MAST-01", "wind_ms": 2.0, "gust_ms": 2.6, "temp_c": 28.0, "rh_pct": 60.0,
            "wind_samples": 120} | kw


def test_mast_is_judged_by_the_server_not_the_device(tmp_path):
    hub = DeviceHub(tmp_path, Clock())
    r = hub.ingest_mast(reading(device_verdict=0, wind_ms=3.1), "IN-SOP-2023")
    assert r["ok"] is False and r["reasons"] == ["wind 3.1 ≥ 3.0 m/s"]
    assert hub.ingest_mast(reading(rh_pct=45), "IN-SOP-2023")["reasons"] == ["RH 45 % < 50 %"]
    assert hub.ingest_mast(reading(), "IN-SOP-2023")["ok"] is True
    gp = hub.ingest_mast(reading(wind_ms=0.5), "GOOD-PRACTICE")
    assert gp["ok"] is False and "inversion" in gp["reasons"][0]


def test_mast_warming_up_or_faulty_cannot_clear(tmp_path):
    hub = DeviceHub(tmp_path, Clock())
    assert hub.ingest_mast(reading(wind_samples=10), "IN-SOP-2023")["ok"] is None
    assert hub.ingest_mast(reading(temp_c=None), "IN-SOP-2023")["ok"] is None
    assert hub.ingest_mast(reading(rh_pct="nan"), "IN-SOP-2023")["ok"] is None


def test_site_gate_uses_only_fresh_nearby_masts(tmp_path):
    clk = Clock()
    hub = DeviceHub(tmp_path, clk)
    field_home = (28.4499, 77.5837)
    assert hub.site_gate(field_home)["ok"] is None                       # no mast at all
    hub.ingest_mast(reading(wind_ms=3.5, lat=28.4500, lon=77.5838), "IN-SOP-2023")
    assert hub.site_gate(field_home)["ok"] is False
    clk.t += 601                                                       # stale after 10 min
    assert hub.site_gate(field_home)["ok"] is None
    hub.ingest_mast(reading(device_id="FAR", wind_ms=3.5, lat=28.60, lon=77.70), "IN-SOP-2023")
    assert hub.site_gate(field_home)["ok"] is None                       # 20 km away: not this field


def test_battery_board_counts_flights(tmp_path):
    clk = Clock()
    hub = DeviceHub(tmp_path, clk)
    hub.pack_event("1", "charged")
    hub.pack_event("1", "flying")
    clk.t += 400
    hub.pack_event("1", "charging")
    clk.t += 60
    v = hub.pack_event("1", "flying")
    assert v["flights_today"] == 2 and v["state"] == "flying"
    with pytest.raises(ValueError):
        hub.pack_event("1", "exploded")
    assert DeviceHub(tmp_path, clk).packs_view()[0]["flights_today"] == 2     # survives a restart


def _field():
    f = grid_field("t", (28.4499, 77.5837), 40, 20, 2, 1)
    return f


def _session(f, pts, sid="S1"):
    """pts: list of (x, y, litres_cum) in the field frame."""
    rows = []
    for i, (x, y, l) in enumerate(pts):
        lat, lon = f.frame.to_wgs84(x, y)
        rows.append([float(i), lat, lon, 2.0, l, 4.5, 1])
    return {"session_id": sid, "device_id": "SPRAYLOG", "litres_total": pts[-1][2], "samples": rows}


def test_as_applied_attributes_litres_to_zones_and_flags_off_target(tmp_path):
    f = _field()
    hub = DeviceHub(tmp_path, Clock())
    spec = drone_spec(None)
    planned = planned_litres(f, type("M", (), {"zones": ["A1"]})(), spec)
    assert planned["A1"] == pytest.approx(20 * 20 * 20 / 10_000, rel=1e-3)        # 0.8 L
    # the local frame starts at take-off, 4 m outside the SW corner: A1 is x 4-24, A2 x 24-44
    # walk across A1 laying 0.8 L, then 0.3 L into A2 (untreated), then 0.1 L outside the field
    pts = [(5 + i, 14, 0.8 * i / 18) for i in range(19)]
    pts += [(23.5, 14, 0.8)]
    pts += [(25 + 1.5 * i, 14, 0.8 + 0.3 * i / 10) for i in range(1, 11)]
    pts += [(5, -30, 1.1), (5, -31, 1.2)]
    hub.ingest_spraylog(_session(f, pts), run_id="R1")
    rep = as_applied(hub.sessions_for("R1"), f, planned)
    z = {r["zone_id"]: r for r in rep["zones"]}
    assert z["A1"]["applied_l"] == pytest.approx(0.8, abs=0.06)
    assert rep["litres_outside_field"] == pytest.approx(0.1, abs=0.01)
    assert any("A2 was not to be treated" in fl for fl in rep["flags"])
    assert any("outside the field" in fl for fl in rep["flags"]) and rep["ok"] is False
    assert rep["on_target_pct"] < 75


def test_spraylog_upload_validation_and_restart(tmp_path):
    hub = DeviceHub(tmp_path, Clock())
    with pytest.raises(ValueError):
        hub.ingest_spraylog({"samples": []})
    r = hub.ingest_spraylog({"session_id": "S/9", "samples": [[0, None, None, 0, 0], [1, 1, 1, "x", 0.1],
                                                             ["bad"], [2, 28.4, 77.5, 2.0, 0.2, 4.5, 1]]})
    # no time or litres -> dropped; an unreadable flow is kept as 0 L/min
    assert r["samples"] == 3 and r["litres_total"] == 0.2
    assert "S/9" in DeviceHub(tmp_path, Clock()).sessions                 # persisted


def test_api_mast_no_go_blocks_approval_even_with_an_override(tmp_path, monkeypatch):
    """End to end through the API: the operator's written override clears the offline SOP
    NEEDS_HUMAN, but never a NO-GO measured at the field."""
    import json
    import time

    from fastapi.testclient import TestClient

    import fieldpilot.api as api
    from fieldpilot.service import FieldPilot
    monkeypatch.setenv("FP_RUNS_DIR", str(tmp_path / "runs"))
    old = api._fp
    api._fp = FieldPilot(ensemble=(2, 1), sim_time_scale=300.0)
    try:
        c = TestClient(api.app)
        job = c.post("/api/demo-run").json()["job"]
        run_id = None
        with c.stream("GET", f"/api/jobs/{job}/events") as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    e = json.loads(line[6:])
                    if e["type"] == "done":
                        run_id = e["run_id"]
                        break
        home = api._fp.field.home
        why = {"operator": "t", "override_reason": "notice to GP sent 26 Oct, pilot RPC on file"}
        assert c.post("/api/devices/mast", json=reading(wind_ms=3.6, lat=home[0], lon=home[1])).json()["ok"] is False
        r = c.post(f"/api/runs/{run_id}/approve", data=why).json()
        assert r["dispatched"] is False and r["reason"].startswith("FIELD MAST NO-GO")
        ops = c.get("/api/ops").json()
        assert ops["site"]["ok"] is False and ops["devices"][0]["device_id"] == "MAST-01"
        assert c.post("/api/devices/mast", json=reading(wind_ms=1.9, lat=home[0], lon=home[1])).json()["ok"] is True
        r = c.post(f"/api/runs/{run_id}/approve", data=why).json()
        assert r["dispatched"] is True
        rec = api._fp.runs[run_id].proposal["approvals"][-1]
        assert rec["site_mast"]["ok"] is True and rec["site_mast"]["wind_ms"] == 1.9
        for _ in range(200):
            if c.get("/api/telemetry").json()["state"] == "landed":
                break
            time.sleep(0.05)
        # a SprayLog upload now belongs to this run, and the as-applied report reads it
        z = api._fp.runs[run_id].mission.zones[0]
        poly = api._fp.field.zone(z).polygon
        lat = sum(p[0] for p in poly) / len(poly)
        lon = sum(p[1] for p in poly) / len(poly)
        up = c.post("/api/devices/spraylog", json={"session_id": "T-1", "device_id": "SPRAYLOG-T1",
                                                   "samples": [[0, lat, lon, 2.0, 0.0, 4.5, 1],
                                                               [1, lat + 1e-6, lon, 2.0, 0.036, 4.5, 1]]}).json()
        assert up["run_id"] == run_id
        aa = c.get("/api/devices/as-applied").json()
        assert aa["run_id"] == run_id and any(r["applied_l"] > 0 for r in aa["zones"])
        assert c.post("/api/batteries/1", json={"state": "flying"}).json()["flights_today"] == 1
        assert c.get("/ops").status_code == 200
    finally:
        api._fp = old


def test_device_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import fieldpilot.api as api
    from fieldpilot.service import FieldPilot
    monkeypatch.setenv("FP_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FP_DEVICE_TOKEN", "s3cret")
    old = api._fp
    api._fp = FieldPilot(ensemble=(2, 1))
    try:
        c = TestClient(api.app)
        assert c.post("/api/devices/mast", json=reading()).status_code == 401
        assert c.post("/api/devices/mast", json=reading(), headers={"X-FP-Token": "s3cret"}).status_code == 200
    finally:
        api._fp = old
