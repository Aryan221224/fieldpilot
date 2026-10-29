"""Gate calibration on public photos: scoring and the stratified split (no network, no model)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_scans", ROOT / "eval" / "public_scans.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)


def test_manifest_is_balanced_and_licensed():
    rows = ps.rows()
    assert len(rows) == 150
    assert {r["licence"] for r in rows} == {"CC BY 4.0", "CC BY-SA 3.0"}
    assert {r["label"] for r in rows} == {"early blight", "late blight", "healthy"}
    assert all(r["url"].startswith("https://raw.githubusercontent.com/") for r in rows)


def test_score_uses_the_true_label_only():
    f = [{"label": "Early blight", "confidence": 0.8}, {"label": "healthy", "confidence": 0.1}]
    assert ps.p_true(f, "early blight") == 0.8
    assert ps.p_true(f, "late blight") == 0.0
    assert ps.p_true([], "healthy") == 0.0


def test_split_is_stratified_and_disjoint():
    obs = [{"file": f"{l}{i}", "label": l} for l in ("a", "b") for i in range(10)]
    cal, test = ps.split(obs)
    assert len(cal) == len(test) == 10
    assert not {d["file"] for d in cal} & {d["file"] for d in test}
    assert sum(d["label"] == "a" for d in cal) == 5
