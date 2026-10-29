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
