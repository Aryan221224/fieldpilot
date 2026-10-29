"""The SOP is a gate in code: the agent must check it, and Approve re-checks it."""
from datetime import date, datetime, timedelta, timezone

import pytest

from fieldpilot.agent import StubLLM, run_agent
from fieldpilot.config import load
from fieldpilot.drone.sim import SimBackend
from fieldpilot.perception import Perception
from fieldpilot.tools import RunState, ToolError, Toolbox, offline_forecast, sop_report

TODAY = date(2026, 9, 21)


@pytest.fixture(scope="module")
def run(field, demo_images):
    s = RunState("R-sop", field, demo_images, TODAY, load().mode())
    d = SimBackend(home=field.home)
    d.connect()
    tb = Toolbox(s, Perception(llm=None), drone_backend=d, ensemble=(2, 2))
    run_agent(s, tb, StubLLM(lambda: s))
    return s, tb


def test_proposal_carries_sop_verdict(run):
    s, _ = run
    assert s.sop is not None
    assert s.proposal["sop_verdict"] == s.sop["verdict"]
    rules = {c["rule"] for c in s.sop["checks"]}
    assert {"wind", "registered", "notice", "phi", "volume", "speed", "swath"} <= rules
    # offline: no Tavily key -> label unverified -> a human must clear it
    assert s.label["ok"] is None and s.sop["verdict"] == "NEEDS_HUMAN"


def test_twin_feeds_phi_and_stage(run):
    s, _ = run
    phi = next(c for c in s.sop["checks"] if c["rule"] == "phi")
    z = s.mission.zones[0]
    assert s.evals[z].maturity, "the twin must report a maturity date"
    assert "twin maturity" in phi["value"] or phi["ok"] is None


def test_cannot_propose_flight_without_sop(field, demo_images):
    s = RunState("R-nosop", field, demo_images, TODAY, load().mode())
    d = SimBackend(home=field.home)
    d.connect()
    tb = Toolbox(s, Perception(llm=None), drone_backend=d, ensemble=(2, 2))
    tb.call("inspect_zones", {})
    sim = tb.call("simulate_zone_options", {"zone_ids": sorted(s.observations)})
    pids = [z["recommended_plan_id"] for z in sim["zones"]
            if z["recommended"] == "spot_spray" and not z["blocked_for_review"]]
    m = tb.call("build_treatment_mission", {"plan_ids": pids})
    with pytest.raises(ToolError, match="check_sop"):
        tb.call("submit_proposal", {"mission_id": m["mission_id"], "headline": "go",
                                    "rationale": "trust me"})
    assert any(v["why"] == "SOP not checked" for v in s.violations)


def test_wind_blocks_and_gives_next_window(run):
    s, tb = run
    now = datetime.now(timezone.utc)
    rep = sop_report(s, tb.spec, live=False, forecast=offline_forecast(now, wind_ms=4.2))
    assert rep["verdict"] == "BLOCKED"
    wind = next(c for c in rep["checks"] if c["rule"] == "wind")
    assert wind["ok"] is False and "4.2" in wind["value"] and "2023" in wind["source"]
    assert rep["earliest_clear"] is not None
    assert datetime.fromisoformat(rep["earliest_clear"]) >= now.replace(minute=0, second=0,
                                                                       microsecond=0) + timedelta(hours=4)


def test_approve_refuses_blocked_even_with_override(tmp_path, monkeypatch, demo_images):
    monkeypatch.setenv("FIELDPILOT_OFFLINE_WIND", "4.2")
    monkeypatch.setenv("FP_RUNS_DIR", str(tmp_path / "runs"))
    from fieldpilot.service import FieldPilot
    fp = FieldPilot(today=TODAY, ensemble=(2, 2))
    s = fp.new_run(demo_images)
    assert s.proposal["sop_verdict"] == "BLOCKED"
    r = fp.approve(s.run_id, "op", override_reason="I am sure the wind is fine, trust me")
    assert not r["dispatched"] and "BLOCKED" in r["reason"]
    assert s.proposal["status"] == "awaiting_approval"
    monkeypatch.setenv("FIELDPILOT_OFFLINE_WIND", "0")
    r = fp.approve(s.run_id, "op")                             # NEEDS_HUMAN, no reason
    assert not r["dispatched"]
    r = fp.approve(s.run_id, "op", override_reason="GP notice sent 26 Oct; RPC on file")
    assert r["dispatched"]
    assert s.proposal["approval"]["override_reason"].startswith("GP notice")


def test_paperwork_from_run(run, tmp_path):
    from fieldpilot.paperwork import from_state, main
    s, _ = run
    docs = from_state(s)
    assert set(docs) == {"gram_panchayat_notice", "spray_record", "farmer_slip"}
    assert "Mancozeb" in docs["spray_record"] and "(fill in)" in docs["gram_panchayat_notice"]
    assert "wind" in docs["spray_record"]                      # SOP checks are on the record
    import json
    p = tmp_path / f"{s.run_id}.json"
    p.write_text(json.dumps(s.summary(), default=str), encoding="utf-8")
    assert main([str(p), "--no-pdf"]) == 0
    assert (tmp_path / "paper" / s.run_id / "farmer_slip.html").exists()
