import math

from fieldpilot.perception import ConformalGate, Perception, gate_observation, pixel_metrics


def test_pixel_metrics_separate_lesions_from_soil(demo_images):
    healthy = pixel_metrics(demo_images["A1"][0])
    blight = pixel_metrics(demo_images["A2"][0])
    assert 15 < healthy["canopy_cover_exg_pct"] < 60
    # brown soil between plants must NOT read as lesions; enclosed spots must
    assert healthy["lesion_index_pct"] < 1.0
    assert blight["lesion_index_pct"] > 4 * max(healthy["lesion_index_pct"], 0.5)


def test_conformal_quantile_finite_sample():
    scores = [i / 100 for i in range(100)]
    g = ConformalGate.from_scores(scores, alpha=0.1)
    k = math.ceil(101 * 0.9)                      # 91st smallest
    assert g.qhat == sorted(scores)[k - 1] and g.calibrated
    assert ConformalGate.from_scores([0.1] * 5, alpha=0.1).qhat == 1.0   # too few: no info


def test_prediction_set_and_review_routing():
    g = ConformalGate(alpha=0.1, qhat=0.2, n=100)            # threshold p >= 0.8
    f = [{"label": "early blight", "confidence": 0.9}, {"label": "late blight", "confidence": 0.85}]
    pset, _ = g.prediction_set(f)
    assert pset == ["early blight", "late blight"]            # ambiguous -> human
    raw = {"findings": [{"category": "disease", "label": "early blight", "severity": 2,
                         "confidence": 0.9}, {"category": "disease", "label": "late blight",
                                              "severity": 2, "confidence": 0.85}],
           "canopy_cover_pct": 40}
    o = gate_observation("A1", raw, [{"canopy_cover_exg_pct": 38, "lesion_index_pct": 5}], g, "m")
    assert o.needs_review and "conformal set has 2 labels" in o.review_reasons[0]


def test_canopy_disagreement_goes_to_human():
    raw = {"findings": [{"category": "none", "label": "healthy", "severity": 0, "confidence": 0.95}],
           "canopy_cover_pct": 90}
    o = gate_observation("A1", raw, [{"canopy_cover_exg_pct": 20, "lesion_index_pct": 0}],
                         ConformalGate(), "m")
    assert o.needs_review and any("disagree" in r for r in o.review_reasons)


def test_offline_stub_mix(demo_images):
    p = Perception(llm=None)
    obs = {z: p.observe(z, imgs) for z, imgs in demo_images.items()}
    assert obs["A2"].primary()["severity"] == 3 and not obs["A2"].needs_review
    assert obs["B1"].needs_review                               # weak signal -> human
    assert obs["A1"].primary()["category"] == "none"
    assert all(o.source.startswith("OFFLINE") for o in obs.values())


def test_differential_drives_the_conformal_set():
    from fieldpilot.perception import (ConformalGate, candidates_for, clean_differential,
                                       gate_observation, true_label_prob)
    cands = candidates_for("potato")
    d = clean_differential({"healthy": 1, "Early Blight": 6, "late blight": 3}, cands)
    assert abs(sum(d.values()) - 1) < 1e-6 and d["early blight"] == 0.6
    assert true_label_prob([], d, "late blight") == 0.3            # the 2nd choice still scores
    g = ConformalGate(alpha=0.1, qhat=0.5, n=50, basis="differential")  # threshold p >= 0.5
    raw = {"findings": [{"category": "disease", "label": "early blight", "severity": 2,
                         "confidence": 0.8}], "differential": {"early blight": 0.7, "late blight": 0.3}}
    o = gate_observation("A1", raw, [], g, source="t", candidates=cands)
    assert o.prediction_set == ["early blight"] and not o.needs_review
    raw["differential"] = {"early blight": 0.45, "late blight": 0.55}
    o = gate_observation("A1", raw, [], g, source="t", candidates=cands)
    assert o.prediction_set == ["late blight"] and o.needs_review    # gate and finding disagree
    raw["differential"] = {"early blight": 0.5, "late blight": 0.5}
    o = gate_observation("A1", raw, [], g, source="t", candidates=cands)
    assert len(o.prediction_set) == 2 and o.needs_review           # a human picks


def test_coarse_gate_passes_a_certain_healthy_zone_and_names_disease_for_a_human():
    from fieldpilot.perception import ConformalGate, candidates_for, gate_observation
    cands = candidates_for("potato")
    g = ConformalGate(alpha=0.1, qhat=0.9, n=74, coarse_alpha=0.05, coarse_qhat=0.5, coarse_n=74, basis="differential")
    healthy = {"findings": [{"category": "none", "label": "healthy", "severity": 0, "confidence": 0.7}],
               "differential": {"healthy": 0.7, "early blight": 0.15, "late blight": 0.1, "other problem": 0.05}}
    o = gate_observation("A1", healthy, [], g, source="t", candidates=cands)
    assert o.prediction_set == ["healthy"] and not o.needs_review
    sick = {"findings": [{"category": "disease", "label": "early blight", "severity": 2, "confidence": 0.6}],
            "differential": {"healthy": 0.05, "early blight": 0.5, "late blight": 0.4, "other problem": 0.05}}
    o = gate_observation("A2", sick, [], g, source="t", candidates=cands)
    assert o.needs_review and "healthy" not in o.prediction_set
    assert "diseased" in o.review_reasons[0]
    # a healthy differential with a disease finding is a contradiction: review
    odd = dict(healthy, findings=[{"category": "disease", "label": "late blight", "severity": 2, "confidence": 0.6}])
    assert gate_observation("A3", odd, [], g, source="t", candidates=cands).needs_review
    # round trip through calibration.json
    import json, tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "c.json")
    json.dump({"alpha": 0.1, "qhat": 0.9, "n": 74, "coarse": {"alpha": 0.05, "qhat": 0.5, "n": 74}}, open(p, "w"))
    assert ConformalGate.load(p).coarse_set(healthy["differential"]) == ["healthy"]
