"""The rehearsal stand-in behaves like Token Factory where FieldPilot depends on it."""
from __future__ import annotations

import json

from fieldpilot import rehearsal
from fieldpilot.intake import BOOKING_SCHEMA


def test_json_schema_answers_have_exactly_the_schema_keys():
    out = json.loads(rehearsal._conform(json.dumps({"crop": "potato", "extra": 1}), BOOKING_SCHEMA))
    assert set(out) == set(BOOKING_SCHEMA["properties"]) and out["crop"] == "potato"
    assert rehearsal._conform("not json", BOOKING_SCHEMA) == "not json"


def test_forecast_prices_each_call_at_its_model():
    rows = [{"model": rehearsal.FAST_ID, "prompt_tokens": 1_000_000, "completion_tokens": 0},
            {"model": rehearsal.AGENT_ID, "prompt_tokens": 1_000_000, "completion_tokens": 0,
             "schema": False},
            {"model": rehearsal.VISION_ID, "prompt_tokens": 0, "completion_tokens": 1_000_000,
             "schema": True}]
    c = rehearsal.cost(rows)
    assert c["usd_at_token_factory_price"] == round(0.06 + 0.30 + 0.90, 4)
    assert c["calls_by_model"] == {"Nemotron-3_5-Lightning": 1, "nemotron-3-super-120b-a12b": 1,
                                   "Nemotron-3.5-Super-VL": 1}
    assert c["schema_constrained_calls"] == 1


def test_stand_in_serves_the_three_roles():
    from fieldpilot.llm import pick_agent_model, pick_fast_model, pick_vision_model
    ids = [rehearsal.AGENT_ID, rehearsal.VISION_ID, rehearsal.FAST_ID]
    assert pick_agent_model(ids) == rehearsal.AGENT_ID
    assert pick_vision_model(ids) == rehearsal.VISION_ID
    assert pick_fast_model(ids) == rehearsal.FAST_ID
