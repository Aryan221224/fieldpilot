"""The doctor (live readiness checks) and the eval chart script, without the network."""
import dataclasses
import importlib.util
import json
import shutil

from fieldpilot import doctor
from fieldpilot.config import ROOT, Config
from mock_openai import MockOpenAI

CATALOG = ["google/gemma-3-27b-it", "nvidia/nemotron-3-super-120b-a12b",
           "nvidia/Nemotron-3-Nano-Omni-30B-A3B"]


def _tf(url):
    return doctor._client(dataclasses.replace(Config(), nebius_api_key="test", nebius_base_url=url))


def test_doctor_live_checks_pass_against_mock_token_factory(demo_images):
    script = [{"content": "", "tool_calls": [("get_zone_status", {"zone_id": "B2"})]},
              {"content": '{"plant_visible": true, "green_fraction": 0.6, "visible_problem": "none"}'}]
    with MockOpenAI(CATALOG, script) as srv:
        tf = _tf(srv.url)
        st, detail, extra = doctor.check_catalog(tf)
        assert st == doctor.PASS and "nemotron-3-super" in detail
        assert extra["picked"]["vision"] == "nvidia/Nemotron-3-Nano-Omni-30B-A3B"
        st, detail, extra = doctor.check_tool_calling(tf)
        assert st == doctor.PASS and extra["protocol"] == "native"
        st, detail, extra = doctor.check_vision(tf)
        assert st == doctor.PASS and extra["reply"]["green_fraction"] == 0.6


def test_doctor_fails_when_the_model_does_not_call_the_tool():
    with MockOpenAI(CATALOG, [{"content": "Zone B2 is probably fine."}]) as srv:
        st, detail, _ = doctor.check_tool_calling(_tf(srv.url))
        assert st == doctor.FAIL and "no tool call" in detail


def test_doctor_refuses_a_non_nvidia_agent():
    with MockOpenAI(["google/gemma-3-27b-it"], []) as srv:
        st, detail, _ = doctor.check_catalog(_tf(srv.url))
        assert st == doctor.FAIL


def _fake_result(n=18):
    rows = [{"scenario": {"sid": f"S{i:02d}", "crop": "potato", "label": "late blight", "severity": 2},
             "fieldpilot": {"recommended": "no_action", "fly": False},
             "nemotron_alone": {"action": "spot_spray", "fly_drone": True},
             "score": {"wasted_flight": i < 5, "unsafe_flight": i < 3, "confident_zero": False}}
            for i in range(n)]
    return {"summary": {"n": n, "today": "2026-10-10", "weather": "TEST FIXTURE",
                        "nemotron_alone": {"model": "nvidia/nemotron-3-super-120b-a12b",
                                           "agreement_with_twin": 0.5, "wasted_flights": 5,
                                           "unsafe_flights": 3, "confident_zero_yields": 1,
                                           "mean_regret_inr_ha": 900, "median_yield_error": 0.4}},
            "rows": rows}


def _load_charts():
    spec = importlib.util.spec_from_file_location("make_charts", ROOT / "eval" / "make_charts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_charts_writes_png_markdown_and_fills_submission(tmp_path, monkeypatch):
    mc = _load_charts()
    src = tmp_path / "grounding_2026-10-10.json"
    d = _fake_result()
    src.write_text(json.dumps(d), encoding="utf-8")
    mc.chart(d, src.with_suffix(".png"))
    assert src.with_suffix(".png").stat().st_size > 20_000
    sen = mc.sentences(d)
    assert sen["N"] == "5" and sen["K"] == "3" and "18 field situations" in sen["headline"]
    md = mc.markdown(d, sen, src.with_suffix(".png"))
    assert "| S00 |" in md and "WASTED FLIGHT" in md
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SUBMISSION.md").write_text(
        "flown in [N] situations and [K] unsafe. *(from eval/results)*", encoding="utf-8")
    monkeypatch.setattr(mc, "ROOT", tmp_path)
    assert mc.fill_submission(sen)
    assert (tmp_path / "docs" / "SUBMISSION.md").read_text(encoding="utf-8") == "flown in 5 situations and 3 unsafe."


def test_submission_check_flags_what_is_missing(tmp_path, monkeypatch):
    shutil.copy(ROOT / "LICENSE", tmp_path / "LICENSE")
    shutil.copy(ROOT / "README.md", tmp_path / "README.md")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SUBMISSION.md").write_text("flown in [N] situations", encoding="utf-8")
    (tmp_path / "docs" / "FEEDBACK.md").write_text("latency: ____", encoding="utf-8")
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    rep = doctor.submission()
    st = {r["check"]: r["status"] for r in rep.rows}
    assert st["open-source licence"] == doctor.PASS
    assert st["README setup"] == doctor.PASS
    assert st["public repository"] == doctor.FAIL           # not a git repo, no remote
    assert st["evaluation numbers"] == doctor.FAIL
    assert st["live agent run"] == doctor.FAIL
    assert st["devpost text"] == doctor.WARN
    assert st["tool feedback"] == doctor.WARN
    res = tmp_path / "eval" / "results"
    res.mkdir(parents=True)
    (res / "grounding_2026-10-10.json").write_text(json.dumps(_fake_result()), encoding="utf-8")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "run1.json").write_text(json.dumps(
        {"mode": {"llm": "nebius-token-factory"}, "models": {"agent": "meta-llama/Llama-3.3-70B"}}),
        encoding="utf-8")
    st = {r["check"]: r["status"] for r in doctor.submission().rows}
    assert st["live agent run"] == doctor.FAIL                # live, but not an NVIDIA model
    (tmp_path / "runs" / "run2.json").write_text(json.dumps(
        {"mode": {"llm": "nebius-token-factory"},
         "models": {"agent": "nvidia/nemotron-3-super-120b-a12b"}}), encoding="utf-8")
    st = {r["check"]: r["status"] for r in doctor.submission().rows}
    assert st["evaluation numbers"] == doctor.PASS and st["live agent run"] == doctor.PASS
