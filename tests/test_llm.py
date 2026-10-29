import dataclasses

import pytest

from fieldpilot.config import Config
from fieldpilot.llm import (TokenFactory, extract_json, is_nvidia, pick_agent_model,
                            pick_fast_model, pick_vision_model, price_of, split_reasoning,
                            usage_summary)
from mock_openai import MockOpenAI

CATALOG = ["deepseek-ai/DeepSeek-V4-Pro", "google/gemma-3-27b-it", "Qwen/Qwen3-Embedding-8B",
           "nvidia/Nemotron-3_5-Lightning", "nvidia/nemotron-3-super-120b-a12b",
           "nvidia/Nemotron-3-Nano-Omni-30B-A3B", "nvidia/Nemotron-3.5-Super-VL"]


def test_model_selection_prefers_nemotron():
    assert pick_agent_model(CATALOG) == "nvidia/nemotron-3-super-120b-a12b"
    assert pick_vision_model(CATALOG) == "nvidia/Nemotron-3.5-Super-VL"
    assert pick_fast_model(CATALOG) == "nvidia/Nemotron-3_5-Lightning"
    no_vl = [c for c in CATALOG if "-VL" not in c]
    assert pick_vision_model(no_vl) == "nvidia/Nemotron-3-Nano-Omni-30B-A3B"
    no_nv = [c for c in no_vl if "Omni" not in c]
    assert pick_vision_model(no_nv) == "google/gemma-3-27b-it"        # non-NVIDIA fallback
    assert pick_fast_model(["x/llama"]) is None
    assert pick_agent_model(["x/llama", "nvidia/Nemotron-3_5-Lightning"]) == "nvidia/Nemotron-3_5-Lightning"
    assert is_nvidia("nvidia/Cosmos-Reason2-8B") and not is_nvidia("google/gemma-3-27b-it")


def test_extract_json_is_robust():
    t = '<think>let me see {"no": 1}</think>Here:\n```json\n{"a": {"b": "x}"}, "c": [1,2]}\n```'
    assert extract_json(t) == {"a": {"b": "x}"}, "c": [1, 2]}
    assert extract_json('<think>..</think><answer>{"ok": true}</answer>') == {"ok": True}
    th, ans = split_reasoning("<think>because</think>final")
    assert th == "because" and ans == "final"
    with pytest.raises(ValueError):
        extract_json("no json here")


def _cfg(url):
    return dataclasses.replace(Config(), nebius_api_key="test", nebius_base_url=url)


def test_native_tool_calls_against_mock_server():
    script = [{"content": "<think>inspect first</think>", "tool_calls": [("inspect_zones", {})]}]
    with MockOpenAI(CATALOG, script) as srv:
        tf = TokenFactory(_cfg(srv.url))
        assert tf.resolve()["agent"] == "nvidia/nemotron-3-super-120b-a12b"
        st = tf.step([{"role": "user", "content": "go"}], [{"type": "function", "function": {
            "name": "inspect_zones", "parameters": {"type": "object", "properties": {}}}}])
        assert st.protocol == "native" and st.tool_calls[0].name == "inspect_zones"
        assert st.reasoning == "inspect first"
        assert srv.requests[-1]["model"] == "nvidia/nemotron-3-super-120b-a12b"
        assert srv.requests[-1]["tools"][0]["function"]["name"] == "inspect_zones"


def test_json_protocol_fallback_when_tools_rejected():
    script = [{"content": '{"tool": "simulate_zone_options", "arguments": {"zone_ids": ["A2"]}}'}]
    with MockOpenAI(CATALOG, script, reject_tools=True) as srv:
        tf = TokenFactory(_cfg(srv.url))
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
        st = tf.step(msgs, [{"type": "function", "function": {"name": "simulate_zone_options",
                                                              "parameters": {}}}])
        assert st.protocol == "json"
        assert st.tool_calls[0].name == "simulate_zone_options"
        assert st.tool_calls[0].arguments == {"zone_ids": ["A2"]}
        assert "TOOL PROTOCOL" in srv.requests[-1]["messages"][0]["content"]


def test_vision_request_carries_image(demo_images):
    script = [{"content": '{"crop_detected": "potato", "findings": [{"category": "none", '
                          '"label": "healthy", "severity": 0, "confidence": 0.9}]}'}]
    with MockOpenAI(CATALOG, script) as srv:
        tf = TokenFactory(_cfg(srv.url))
        obj, meta = tf.vision_json("describe", [demo_images["A1"][0]])
        assert obj["crop_detected"] == "potato"
        assert meta["model"] == "nvidia/Nemotron-3.5-Super-VL"
        parts = srv.requests[-1]["messages"][-1]["content"]
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_bookings_and_brief_go_to_the_fast_nemotron():
    with MockOpenAI(CATALOG, []) as srv:
        tf = TokenFactory(_cfg(srv.url))
        assert tf.model_for("intake") == "nvidia/Nemotron-3_5-Lightning"
        assert tf.model_for("brief") == "nvidia/Nemotron-3_5-Lightning"
        assert tf.model_for("agent") == "nvidia/nemotron-3-super-120b-a12b"


def test_cost_is_priced_per_model():
    assert price_of("nvidia/Nemotron-3_5-Lightning") == (0.06, 0.24)
    assert price_of("nvidia/Nemotron-3-Ultra-550b-a55b") == (1.0, 3.0)
    led = [{"purpose": "intake", "model": "nvidia/Nemotron-3_5-Lightning", "protocol": "json_schema",
            "prompt": 1_000_000, "completion": 0},
           {"purpose": "agent", "model": "nvidia/nemotron-3-super-120b-a12b", "protocol": "native",
            "prompt": 0, "completion": 1_000_000}]
    u = usage_summary(led)
    assert u["usd_estimate"] == 0.96 and u["schema_constrained_calls"] == 1
    assert u["calls_by_purpose"] == {"intake": 1, "agent": 1}
