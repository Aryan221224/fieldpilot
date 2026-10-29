import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import fieldpilot.api as api
    from fieldpilot.service import FieldPilot
    api._fp = FieldPilot(ensemble=(2, 1), sim_time_scale=300.0)
    api._fp.cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    return TestClient(api.app)


def _follow(client, job):
    run_id = None
    with client.stream("GET", f"/api/jobs/{job}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                e = json.loads(line[6:])
                if e["type"] == "done":
                    run_id = e["run_id"]
                    assert not e["error"], e["error"]
                    break
    return run_id


def test_console_and_state(client):
    assert "FieldPilot" in client.get("/").text
    st = client.get("/api/state").json()
    assert st["mode"]["llm"] == "OFFLINE-STUB" and len(st["field"]["zones"]) == 6


def test_demo_run_approve_fly(client):
    job = client.post("/api/demo-run").json()["job"]
    run_id = _follow(client, job)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["proposal"]["status"] == "awaiting_approval"
    assert run["proposal"]["sop_verdict"] == "NEEDS_HUMAN"     # offline: no label, no notice
    r = client.post(f"/api/runs/{run_id}/approve", data={"operator": "test"}).json()
    assert not r["dispatched"] and "SOP" in r["reason"]       # refused without a reason
    r = client.post(f"/api/runs/{run_id}/approve",
                    data={"operator": "test",
                          "override_reason": "notice to GP sent 26 Oct, pilot RPC on file"}).json()
    assert r["dispatched"]
    for _ in range(200):
        t = client.get("/api/telemetry").json()
        if t["state"] == "landed":
            break
        time.sleep(0.05)
    assert t["state"] == "landed"
    # approving twice must fail
    assert client.post(f"/api/runs/{run_id}/approve").status_code == 400


def test_upload_run_and_adjudicate_replan(client):
    from fieldpilot.config import ROOT
    files = [("files", (p.name, p.read_bytes(), "image/jpeg"))
             for p in sorted((ROOT / "examples" / "images").glob("*.jpg"))]
    job = client.post("/api/runs", files=files).json()["job"]
    run_id = _follow(client, job)
    run = client.get(f"/api/runs/{run_id}").json()
    assert "B1" in run["proposal"]["zones_for_human_review"]
    client.post(f"/api/runs/{run_id}/adjudicate",
                data={"zone_id": "B1", "category": "disease", "label": "early blight", "severity": 2})
    job2 = client.post(f"/api/runs/{run_id}/replan").json()["job"]
    assert _follow(client, job2) == run_id
    run = client.get(f"/api/runs/{run_id}").json()
    assert "B1" not in run["proposal"]["zones_for_human_review"]
    assert "B1" in run["mission"]["zones"]                    # human-confirmed zone now treated


def test_bad_zone_name_rejected(client):
    r = client.post("/api/runs", files=[("files", ("Z9_x.jpg", b"123", "image/jpeg"))])
    assert r.status_code == 400


def test_settings_save_and_mask(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fieldpilot import api
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    c = TestClient(api.app)
    assert c.post("/api/settings", json={}).status_code == 400
    assert c.post("/api/settings", json={"nebius_api_key": "bad key"}).status_code == 400
    r = c.post("/api/settings", json={"nebius_api_key": "abcd1234wxyz"})
    assert r.status_code == 200
    assert "NEBIUS_API_KEY=abcd1234wxyz" in (tmp_path / ".env").read_text()
    assert c.get("/api/settings").json()["nebius_api_key"].endswith("wxyz)")
