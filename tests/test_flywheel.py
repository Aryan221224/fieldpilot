"""Operator corrections become intake regression cases; schema-constrained intake."""
from __future__ import annotations

from fieldpilot import flywheel, intake


class SchemaLLM:
    """Answers through .json like Token Factory with response_format json_schema."""
    agent_model = "nvidia/test-nemotron"

    def __init__(self, value=2.5, unit="bigha", quote="dhai bigha"):
        self.v, self.u, self.q = value, unit, quote
        self.schemas = []

    def model_for(self, purpose):
        return "nvidia/Nemotron-3_5-Lightning"

    def json(self, messages, schema, name, **kw):
        self.schemas.append((name, schema))
        return ({k: None for k in schema["properties"]} |
                {"crop": "potato", "area_value": self.v, "area_unit": self.u,
                 "area_quote": self.q, "language": "hi"}), "json_schema"


def test_intake_uses_the_booking_schema_when_available():
    llm = SchemaLLM()
    j = intake.parse_booking("dhai bigha aalu, Ram, Barauli", llm, geocoder=lambda *a: [])
    name, schema = llm.schemas[0]
    assert name == "booking" and schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert j.protocol == "json_schema" and j.model == "nvidia/Nemotron-3_5-Lightning"
    assert j.area_ha == round(2.5 * 2529 / 10_000, 3) and j.extracted["area_quote"] == "dhai bigha"


def test_schema_does_not_bypass_the_number_check():
    j = intake.parse_booking("aalu ka spray karna hai", SchemaLLM(value=7, quote="7 bigha"),
                             geocoder=lambda *a: [])
    assert j.area_ha is None and any("not written in the message" in c for c in j.confirm)


def test_correction_is_recorded_and_replayed(tmp_path):
    job = intake.parse_booking("dhai bigha aalu, Ram", SchemaLLM(), geocoder=lambda *a: []).to_dict()
    assert flywheel.record(job, {"area_ha": job["area_ha"]}, tmp_path) is None   # unchanged
    case = flywheel.record(job, {"area_ha": 0.42}, tmp_path)
    assert case["expected"] == {"area_ha": 0.42} and case["protocol"] == "json_schema"
    rows = flywheel.load(tmp_path)
    assert len(rows) == 1
    res = flywheel.evaluate(SchemaLLM(), rows)
    assert res["cases"] == 1 and res["accuracy"] == 0.0
    assert res["failures"][0]["expected_ha"] == 0.42
    fixed = flywheel.evaluate(SchemaLLM(value=1, unit="acre", quote="dhai bigha"), rows)
    assert fixed["passed"] == 0          # 1 acre is not written in the message: still refused
