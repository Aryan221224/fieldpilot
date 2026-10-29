from datetime import date

import pytest

from fieldpilot.agent import StubLLM, run_agent
from fieldpilot.config import load
from fieldpilot.drone.sim import SimBackend
from fieldpilot.llm import Step, ToolCall
from fieldpilot.perception import Perception
from fieldpilot.tools import RunState, ToolError, Toolbox

TODAY = date(2026, 9, 21)


def _setup(field, demo_images):
    s = RunState("R-test", field, demo_images, TODAY, load().mode())
    d = SimBackend(home=field.home)
    d.connect()
    tb = Toolbox(s, Perception(llm=None), drone_backend=d, ensemble=(2, 2))
    return s, tb


class ScriptedLLM:
    """Plays back a fixed sequence of tool calls, like a model would."""
    agent_model = "scripted"

    def __init__(self, calls):
        self.calls = list(calls)

    def resolve(self):
        return {"agent": "scripted", "vision": None}

    def step(self, messages, tools, **_):
        if not self.calls:
            return Step(content="I think we are done.", protocol="native", model="scripted")
        name, args = self.calls.pop(0)
        return Step(tool_calls=[ToolCall("c", name, args)], protocol="native", model="scripted")


@pytest.fixture(scope="module")
def offline_run(field, demo_images):
    s, tb = _setup(field, demo_images)
    events = []
    run_agent(s, tb, StubLLM(lambda: s), emit=events.append)
    return s, events


def test_offline_run_end_to_end(offline_run):
    s, events = offline_run
    p = s.proposal
    assert p["status"] == "awaiting_approval"
    assert set(s.mission.zones) == {"A2", "B2"} and s.mission.ok
    assert "B1" in p["zones_for_human_review"]
    assert "B1" not in s.mission.zones                     # review zone never treated
    assert p["rationale_hi"]                                # Hindi for the farmer
    names = [e["name"] for e in events if e["type"] == "tool_call"]
    assert names == ["inspect_zones", "simulate_zone_options", "search_advisories",
                     "check_flight_window", "build_treatment_mission", "verify_label", "check_sop", "submit_proposal"]
    assert not s.violations


def test_policy_rejects_review_zone_and_unrecommended_plans(field, demo_images):
    s, tb = _setup(field, demo_images)
    tb.inspect_zones()
    tb.simulate_zone_options(sorted(s.observations))
    with pytest.raises(ToolError, match="flagged for human review"):
        tb.build_treatment_mission(["P-B1-spot_spray"])
    with pytest.raises(ToolError, match="not the twin's recommendation"):
        tb.build_treatment_mission(["P-A2-no_action"])
    with pytest.raises(ToolError, match="unknown plan_id"):
        tb.build_treatment_mission(["P-A2-spray_everything_now"])
    assert len(s.violations) == 3


def test_cannot_simulate_before_inspecting(field, demo_images):
    s, tb = _setup(field, demo_images)
    with pytest.raises(ToolError, match="not been inspected"):
        tb.simulate_zone_options(["A2"])


def test_adversarial_model_is_contained(field, demo_images):
    """A model that tries to spray the review zone, invent a tool and a mission id."""
    s, tb = _setup(field, demo_images)
    llm = ScriptedLLM([
        ("inspect_zones", {}),
        ("simulate_zone_options", {"zone_ids": ["A1", "A2", "A3", "B1", "B2", "B3"]}),
        ("build_treatment_mission", {"plan_ids": ["P-B1-spot_spray", "P-A2-spot_spray"]}),
        ("arm_and_fly_now", {}),
        ("submit_proposal", {"mission_id": "M-FAKE", "headline": "fly", "rationale": "trust me"}),
    ])
    run_agent(s, tb, llm)
    assert tb.drone.telemetry().state == "idle"                 # nothing flew
    whys = " | ".join(v["why"] for v in s.violations)
    assert "flagged for human review" in whys and "unknown tool" in whys
    assert "unknown mission_id" in whys
    # the run still ends in a legal proposal, completed by the policy
    assert s.proposal["completed_by"] == "deterministic policy"
    assert s.mission is None or "B1" not in s.mission.zones


def test_model_that_stops_early_is_completed(field, demo_images):
    s, tb = _setup(field, demo_images)
    run_agent(s, tb, ScriptedLLM([("inspect_zones", {})]))
    assert s.proposal and s.proposal["fallback_reason"] == "model stopped without submitting a proposal"
    assert s.mission is not None and set(s.mission.zones) == {"A2", "B2"}


def test_flight_blocked_by_weather(field, demo_images, monkeypatch):
    from fieldpilot import context
    monkeypatch.setattr(context, "flight_conditions", lambda *a, **k: {
        "source": "test", "go": False, "reasons": ["wind 9.0 m/s > 5.0 m/s spray-drift limit"],
        "now": {}, "next_window_utc": None, "limits": {}})
    s, tb = _setup(field, demo_images)
    run_agent(s, tb, StubLLM(lambda: s))
    assert s.mission is not None and not s.mission.ok
    assert s.proposal["mission_id"] is None                     # nothing to approve
    assert "blocked" in s.proposal["headline"]
