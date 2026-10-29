"""Operator desk end to end through the API: bookings -> Nemotron intake -> planned day."""
import json

import pytest
from fastapi.testclient import TestClient

from test_dayplan import typical_day as october_day


class FakeNemotron:
    """Stands in for Token Factory: answers the intake prompt from a table, writes a brief."""
    agent_model = "nvidia/nemotron-3-super-120b-a12b"

    def __init__(self, table, brief="आज 3 खेत।"):
        self.table, self.brief_text = table, brief

    def text(self, messages, **_):
        user = messages[-1]["content"]
        for key, ans in self.table.items():
            if key in user:
                return json.dumps(ans, ensure_ascii=False)
        return self.brief_text

    def resolve(self):
        return {"agent": self.agent_model, "vision": None}


TABLE = {
    "Dankaur": {"farmer": "Ramesh", "village": "Dankaur", "crop": "potato", "problem": "late blight",
                "area_value": 2, "area_unit": "acre"},
    "Jewar": {"farmer": "Mahesh", "village": "Jewar", "crop": "potato", "problem": "late blight",
              "area_value": 3, "area_unit": "acre"},
    "Atta": {"farmer": "Vikram", "village": "Atta", "crop": "mustard", "area_value": 9,
             "area_unit": "acre"},                                   # 9 is not in the message
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import fieldpilot.api as api
    from fieldpilot import dayplan
    from fieldpilot.service import FieldPilot
    monkeypatch.setenv("FP_RUNS_DIR", str(tmp_path / "runs"))
    api._fp = FieldPilot(ensemble=(2, 1))
    api._fp.llm = FakeNemotron(TABLE)
    fc = october_day()
    monkeypatch.setattr(dayplan, "live_forecasts", lambda jobs, fetch=None: {j.job_id: fc for j in jobs})
    return TestClient(api.app)


def test_desk_flow(client):
    assert "operator desk" in client.get("/desk").text
    demos = client.get("/api/desk/demos").json()
    assert {"in", "br"} <= set(demos)
    demo = client.get("/api/desk/demo/in").json()
    assert demo["messages"] and demo["operator"]["drone"] == "t1_10l"
    msgs = [{"text": "Ramesh, Dankaur. 2 acre aalu, jhulsa.", "spray_ha": 0.35, "pin": [28.3517, 77.553]},
            {"text": "Jewar se Mahesh, 3 acre potato blight", "pin": [28.1222, 77.556]},
            {"text": "Vikram, Atta gaon, sarson me spray chahiye", "pin": [28.395, 77.505]}]
    r = client.post("/api/desk/intake", json={"messages": msgs, "operator": demo["operator"]}).json()
    jobs = r["jobs"]
    assert r["model"].startswith("nvidia/")
    assert jobs[0]["spray_ha"] == 0.35 and jobs[0]["area_ha"] == pytest.approx(0.809, abs=1e-3)
    assert jobs[2]["area_ha"] is None                     # the model's "9 acre" was not in the text
    p = client.post("/api/desk/plan", json={"jobs": jobs, "operator": demo["operator"],
                                            "date": "2026-10-28"}).json()
    assert p["fieldpilot"]["metrics"]["out_of_window_sorties"] == 0
    assert p["models"]["intake_and_brief"].startswith("nvidia/")
    assert {c["farmer"] for c in p["callback"]} == {"Vikram"}
    assert p["windows"] and p["brief"]["text"]
    assert "Batteries" in client.get("/api/desk/sheet").text


def test_brief_with_invented_numbers_is_rejected():
    from fieldpilot.dayplan import JobSpec, Operator, plan_day
    from fieldpilot.desk import brief
    from datetime import date
    jobs = [JobSpec("J1", "Ramesh", 28.35, 77.55, 0.8, 0.35, village="Dankaur")]
    plan = plan_day(jobs, Operator(), date(2026, 10, 28), {"J1": october_day()})
    out = brief(plan, FakeNemotron({}, brief="आज 7 खेत और 973 उड़ान।"), "hi")
    assert out["by"] == "template" and "973" in out["note"]
    ok = brief(plan, FakeNemotron({}, brief="आज 1 खेत है, दनकौर गाँव में रमेश जी का।"), "hi")
    assert ok["by"].startswith("nvidia/")
    en = brief(plan, FakeNemotron({}, brief="Good morning. One farm today, Ramesh at Dankaur."), "en")
    assert en["by"].startswith("nvidia/")
    wrong_script = brief(plan, FakeNemotron({}, brief="Good morning. One farm today."), "hi")
    assert wrong_script["by"] == "template"
