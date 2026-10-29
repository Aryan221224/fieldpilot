"""Perception: phone/drone photos of a zone -> a structured, gated observation.

A vision-language model is good at *describing* a canopy and bad at knowing how
sure it is. So its answer is never used raw:

1. **Structured output.** The model must fill a fixed schema (growth stage,
   canopy cover, findings with severity and confidence, image-quality problems)
   and is told that "none"/"unknown" are correct answers.
2. **Pixel cross-check.** Canopy cover is also measured directly from the image
   (Excess-Green + Otsu). If the model and the pixels disagree badly, the
   observation goes to a human -- one of them is wrong and we cannot tell which.
3. **Conformal gate.** Stated confidences are converted into a split-conformal
   prediction set using a calibration file built from labelled photos
   (``fieldpilot calibrate``). Only a *singleton* set may drive an action.
   Without calibration the gate is explicitly UNCALIBRATED and conservative.

The drone never treats a zone whose observation is flagged for review.
"""
from __future__ import annotations

import io
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

CATEGORIES = ("disease", "pest", "nutrient_deficiency", "water_stress", "weed", "none")

SYSTEM = (
    "You are an agronomy perception module inside a field robot. You look at close-range "
    "photos of one management zone of a crop plot and report ONLY what is visible. "
    "You never recommend treatments. When evidence is weak you say so: 'none' and "
    "'unknown' are correct answers, and an honest low confidence is more useful than a "
    "confident guess."
)

PROMPT = """Photos of zone {zone_id}. Expected crop: {crop} (verify, it may be wrong).

Return ONE JSON object with exactly these keys:
{{
  "crop_detected": "<crop name or 'unknown'>",
  "crop_confidence": <0-1>,
  "bbch": <integer BBCH growth stage 0-99, or -1 if unknown>,
  "bbch_confidence": <0-1>,
  "canopy_cover_pct": <0-100, fraction of ground covered by green leaves>,
  "findings": [
    {{"category": one of {categories},
      "label": "<specific name, e.g. 'early blight', 'aphids', 'nitrogen deficiency', or 'healthy'>",
      "severity": <0 none | 1 low (<5% leaf area) | 2 moderate (5-25%) | 3 severe (>25%)>,
      "confidence": <0-1, your honest probability this finding is correct>,
      "evidence": "<what in the image supports it, e.g. 'concentric brown rings on lower leaves'>"}}
  ],
  "image_quality": {{"usable": <true|false>, "issues": ["blur" | "overexposed" | "too far" | "not a crop" | ...]}},
  "notes": "<one sentence>"
}}
If the zone looks healthy, return a single finding with category "none", label "healthy".
List at most 3 findings, most important first.
Also return "differential": a probability for EVERY one of these candidate diagnoses, summing
to 1, e.g. {{"healthy": 0.1, ...}}: {candidates}. Spread the probability when two look alike;
"other problem" covers anything not listed."""

# Candidate diagnoses per crop for the differential: a fixed label set gives the conformal gate
# one probability per label, so the true label can sit in the prediction set even when it is
# not the model's first choice (a single free-text finding cannot).
CANDIDATES = {
    "potato": ["healthy", "early blight", "late blight", "other problem"],
    "tomato": ["healthy", "early blight", "late blight", "septoria leaf spot", "other problem"],
}
GENERIC = ["healthy", "disease", "pest", "nutrient deficiency", "water stress", "other problem"]


def candidates_for(crop: str) -> list[str]:
    return CANDIDATES.get((crop or "").strip().lower(), GENERIC)


def clean_differential(raw: Any, candidates: list[str]) -> dict[str, float] | None:
    """The model's differential over ``candidates``, renormalised; None if unusable."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for c in candidates:
        v = raw.get(c)
        if v is None:
            v = next((raw[k] for k in raw if str(k).strip().lower() == c), 0.0)
        try:
            out[c] = max(0.0, float(v))
        except (TypeError, ValueError):
            out[c] = 0.0
    tot = sum(out.values())
    if tot <= 0:
        return None
    return {k: round(v / tot, 4) for k, v in out.items()}


def true_label_prob(findings: list[dict], differential: dict | None, truth: str) -> float:
    """p(true label) for conformal calibration: from the differential if there is one."""
    truth = truth.strip().lower()
    if differential:
        return max([p for k, p in differential.items() if truth in k or k in truth] or [0.0])
    return max([float(f.get("confidence") or 0) for f in findings
                if truth in str(f.get("label", "")).lower() or str(f.get("label", "")).lower() in truth]
               or [0.0])


# ---------------------------------------------------------------------------
# Pixel measurements (no model involved)
# ---------------------------------------------------------------------------

def _load_rgb(img_bytes: bytes, max_side: int = 768) -> np.ndarray:
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im.thumbnail((max_side, max_side))
    return np.asarray(im, dtype=np.float32) / 255.0


def _otsu(x: np.ndarray) -> float:
    hist, edges = np.histogram(x, bins=256)
    p = hist.astype(float) / max(hist.sum(), 1)
    w = np.cumsum(p)
    mu = np.cumsum(p * (edges[:-1] + edges[1:]) / 2)
    mt = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (mt * w - mu) ** 2 / (w * (1 - w))
    s = np.nan_to_num(s)
    return float((edges[:-1] + edges[1:])[int(np.argmax(s))] / 2)


def pixel_metrics(img_bytes: bytes) -> dict[str, float]:
    """Canopy cover (ExG+Otsu), lesion index and chlorosis index.

    ExG = 2g - r - b on chromatic coordinates is the standard RGB vegetation
    segmenter; Otsu picks the threshold per image so exposure changes between
    photos do not move it.

    Lesions are defined *topologically*, not by colour alone: small non-green
    holes fully enclosed by leaf tissue. Colour alone cannot separate a brown
    blight spot from the brown soil between plants -- both sit at ~30 deg hue --
    but soil is connected to the gaps between leaves and lesions are not.
    Chlorosis is yellow (45-70 deg hue) leaf-like area, which soil is not.
    Both are cross-checks on the vision model, never diagnoses.
    """
    from scipy import ndimage as ndi
    a = _load_rgb(img_bytes)
    s = a.sum(axis=2) + 1e-6
    r, g, b = a[..., 0] / s, a[..., 1] / s, a[..., 2] / s
    exg = 2 * g - r - b
    t = max(_otsu(exg), 0.02)
    veg = exg > t
    cover = float(veg.mean())

    holes = ndi.binary_fill_holes(veg) & ~veg
    lab, n = ndi.label(holes)
    lesion_px = 0
    if n:
        sizes = ndi.sum(holes, lab, index=np.arange(1, n + 1))
        small = np.isin(lab, 1 + np.flatnonzero(sizes < 0.004 * veg.size))
        lesion_px = int(small.sum())

    mx, mn = a.max(axis=2), a.min(axis=2)
    d = mx - mn + 1e-6
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    hue = np.where(mx == R, (60 * (G - B) / d) % 360, np.where(mx == G, 60 * (B - R) / d + 120, 0))
    sat = d / (mx + 1e-6)
    yellow = (hue > 45) & (hue < 70) & (sat > 0.3) & (mx > 0.35) & ~veg

    leafy = max(float(veg.sum()) + lesion_px + float(yellow.sum()), 1.0)
    return {"canopy_cover_exg_pct": round(100 * cover, 1),
            "lesion_index_pct": round(100 * lesion_px / leafy, 1),
            "chlorosis_index_pct": round(100 * float(yellow.sum()) / leafy, 1),
            "brightness": round(float(mx.mean()), 3)}


# ---------------------------------------------------------------------------
# Conformal gate
# ---------------------------------------------------------------------------

@dataclass
class ConformalGate:
    """Split-conformal gate over the model's stated confidences.

    Score s(x, y) = 1 - p(y|x), where p is the confidence the model gave the true
    label (0 if it never mentioned it). With n calibration scores, the threshold
    q = the ceil((n+1)(1-alpha))/n empirical quantile. The prediction set is every
    label with p >= 1 - q; if q >= 1 the set is "everything" (the model's
    confidences carry no information at this alpha). Coverage >= 1 - alpha holds
    for exchangeable data regardless of how badly calibrated the model is --
    which is the entire point of using it on a VLM.
    """
    alpha: float = 0.1
    qhat: float | None = None
    n: int = 0
    fallback_threshold: float = 0.85     # used only when uncalibrated
    # Second, coarser gate on the differential: healthy vs diseased. A vision model that cannot
    # tell two blights apart can still be certain a leaf is healthy; then the zone needs no human.
    coarse_alpha: float = 0.05
    coarse_qhat: float | None = None
    coarse_n: int = 0
    # what the scores were computed from; a gate fitted on differentials is not applied to
    # free-text finding confidences (a different, uncalibrated scale)
    basis: str = "findings"

    def coarse_set(self, differential: dict | None) -> list[str] | None:
        """{'healthy'} / {'diseased'} / both, at the coarse gate; None when it does not apply."""
        if not differential or self.coarse_qhat is None or self.coarse_n < 20:
            return None
        ph = float(differential.get("healthy", 0.0))
        thr = 1.0 - float(self.coarse_qhat)
        return [k for k, p in (("healthy", ph), ("diseased", 1.0 - ph)) if p >= thr]

    @property
    def calibrated(self) -> bool:
        return self.qhat is not None and self.n >= 20

    @classmethod
    def from_scores(cls, scores: list[float], alpha: float = 0.1) -> "ConformalGate":
        n = len(scores)
        if n == 0:
            return cls(alpha=alpha)
        k = math.ceil((n + 1) * (1 - alpha))
        qhat = 1.0 if k > n else float(sorted(scores)[k - 1])
        return cls(alpha=alpha, qhat=qhat, n=n)

    @classmethod
    def load(cls, path: str | Path | None) -> "ConformalGate":
        if not path or not Path(path).exists():
            return cls()
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        c = d.get("coarse") or {}
        return cls(alpha=d["alpha"], qhat=d["qhat"], n=d["n"], coarse_alpha=c.get("alpha", 0.05),
                   coarse_qhat=c.get("qhat"), coarse_n=c.get("n", 0), basis=d.get("basis", "findings"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"alpha": self.alpha, "qhat": self.qhat, "n": self.n}), encoding="utf-8")

    def prediction_set(self, findings: list[dict],
                       differential: dict | None = None) -> tuple[list[str], str]:
        scored = list(differential.items()) if differential else \
            [(f["label"], float(f.get("confidence", 0))) for f in findings]
        basis = "differential" if differential else "findings"
        if self.calibrated and basis == self.basis:
            thr = 1.0 - float(self.qhat)
            if self.qhat >= 1.0:
                return ["<any>"], "calibrated: confidences uninformative at this alpha"
            return [k for k, p in scored if p >= thr], \
                f"calibrated n={self.n}, alpha={self.alpha}, threshold p>={thr:.2f} ({basis})"
        thr = self.fallback_threshold
        return [k for k, p in scored if p >= thr], \
            f"UNCALIBRATED: conservative threshold p>={thr:.2f} ({basis})"


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    zone_id: str
    n_images: int
    crop_detected: str
    crop_confidence: float
    bbch: int
    bbch_confidence: float
    canopy_cover_pct: float
    canopy_cover_exg_pct: float
    lesion_index_pct: float
    findings: list[dict]
    image_quality: dict
    prediction_set: list[str]
    gate: str
    needs_review: bool
    review_reasons: list[str]
    source: str
    reasoning: str = ""
    notes: str = ""
    differential: dict | None = None

    def primary(self) -> dict:
        """The finding that drives the plan (highest severity x confidence)."""
        real = [f for f in self.findings if f.get("category") != "none" and f.get("severity", 0) > 0]
        if not real:
            none = [f for f in self.findings if f.get("category") == "none"]
            return none[0] if none else {"category": "none", "label": "healthy", "severity": 0,
                                         "confidence": 0.0, "evidence": ""}
        return max(real, key=lambda f: f.get("severity", 0) * f.get("confidence", 0))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["primary"] = self.primary()
        return d


def _clean_findings(raw: Any) -> list[dict]:
    out = []
    for f in (raw or [])[:3]:
        if not isinstance(f, dict):
            continue
        cat = str(f.get("category", "none")).strip().lower().replace(" ", "_")
        if cat not in CATEGORIES:
            cat = "disease" if "disease" in cat or "blight" in cat else "none"
        out.append({
            "category": cat,
            "label": str(f.get("label", "unknown"))[:60],
            "severity": int(max(0, min(3, round(float(f.get("severity", 0) or 0))))),
            "confidence": float(max(0.0, min(1.0, float(f.get("confidence", 0) or 0)))),
            "evidence": str(f.get("evidence", ""))[:200],
        })
    return out or [{"category": "none", "label": "healthy", "severity": 0,
                    "confidence": 0.5, "evidence": "no findings returned"}]


def gate_observation(zone_id: str, raw: dict, px: list[dict], gate: ConformalGate,
                     source: str, reasoning: str = "", candidates: list[str] | None = None) -> Observation:
    findings = _clean_findings(raw.get("findings"))
    diff = clean_differential(raw.get("differential"), candidates) if candidates else None
    exg = float(np.mean([p["canopy_cover_exg_pct"] for p in px])) if px else float("nan")
    lesion = float(np.mean([p["lesion_index_pct"] for p in px])) if px else 0.0
    cover = float(raw.get("canopy_cover_pct", exg if not math.isnan(exg) else 0) or 0)
    iq = raw.get("image_quality") or {"usable": True, "issues": []}

    reasons: list[str] = []
    pset, gate_desc = gate.prediction_set(findings, diff)
    cset = gate.coarse_set(diff)
    if cset == ["healthy"] and len(pset) != 1:
        pset = ["healthy"]
        gate_desc += f"; coarse gate: healthy (alpha={gate.coarse_alpha})"
    elif cset == ["diseased"] and len(pset) != 1:
        reasons.append("the gate is sure the leaves are diseased but not which disease "
                       f"({', '.join(x for x in pset if x != 'healthy')}); a human must pick")
        pset = [x for x in pset if x != "healthy"] or pset
    if len(pset) != 1:
        if not reasons:
            reasons.append(f"conformal set has {len(pset)} labels ({', '.join(pset) or 'empty'}); "
                           "a human must pick")
    elif diff:
        top_f = max(findings, key=lambda f: f["severity"] * f["confidence"])
        lab = top_f["label"].lower()
        if not (pset[0] in lab or lab in pset[0]) and not (pset[0] == "healthy" and top_f["category"] == "none"):
            reasons.append(f"the gate's label '{pset[0]}' and the model's finding '{top_f['label']}' disagree")
    if not iq.get("usable", True):
        reasons.append("image quality: " + ", ".join(iq.get("issues", [])) )
    if not math.isnan(exg) and abs(cover - exg) > 25:
        reasons.append(f"model canopy {cover:.0f}% vs pixel canopy {exg:.0f}% disagree")
    top = max(findings, key=lambda f: f["severity"] * f["confidence"])
    if top["category"] == "none" and lesion > 4:
        reasons.append(f"model says healthy but {lesion:.0f}% of leaf-area pixels are brown/yellow")
    return Observation(
        zone_id=zone_id, n_images=len(px),
        crop_detected=str(raw.get("crop_detected", "unknown")),
        crop_confidence=float(raw.get("crop_confidence", 0) or 0),
        bbch=int(raw.get("bbch", -1) if raw.get("bbch") is not None else -1),
        bbch_confidence=float(raw.get("bbch_confidence", 0) or 0),
        canopy_cover_pct=round(cover, 1), canopy_cover_exg_pct=round(exg, 1),
        lesion_index_pct=round(lesion, 1), findings=findings, image_quality=iq,
        prediction_set=pset, gate=gate_desc, needs_review=bool(reasons),
        review_reasons=reasons, source=source, reasoning=reasoning,
        notes=str(raw.get("notes", ""))[:240], differential=diff)


def stub_raw(zone_id: str, px: list[dict], crop: str) -> dict:
    """Offline stand-in for the VLM, driven only by pixel statistics.

    Deliberately modest: it can say "something brown is here" but it labels the
    finding generically and reports low-to-moderate confidence, so offline runs
    exercise the review path instead of pretending to diagnose.
    """
    cover = float(np.mean([p["canopy_cover_exg_pct"] for p in px])) if px else 0.0
    lesion = float(np.mean([p["lesion_index_pct"] for p in px])) if px else 0.0
    if lesion > 5:
        f = {"category": "disease", "label": "foliar lesions (leaf spot/blight type)",
             "severity": 3, "confidence": 0.9,
             "evidence": f"{lesion:.1f}% of leaf area is enclosed non-green spots (pixel heuristic)"}
    elif lesion > 2.5:
        f = {"category": "disease", "label": "foliar lesions (leaf spot/blight type)",
             "severity": 2, "confidence": 0.87,
             "evidence": f"{lesion:.1f}% of leaf area is enclosed non-green spots (pixel heuristic)"}
    elif lesion > 1.2:
        f = {"category": "disease", "label": "foliar lesions (leaf spot/blight type)",
             "severity": 1, "confidence": 0.55,
             "evidence": f"{lesion:.1f}% enclosed non-green spots, weak signal (pixel heuristic)"}
    else:
        f = {"category": "none", "label": "healthy", "severity": 0, "confidence": 0.9,
             "evidence": "no enclosed discoloured leaf area (pixel heuristic)"}
    bbch = 21 if cover < 25 else 31 if cover < 55 else 40
    return {"crop_detected": crop, "crop_confidence": 0.5, "bbch": bbch, "bbch_confidence": 0.4,
            "canopy_cover_pct": cover, "findings": [f],
            "image_quality": {"usable": True, "issues": []},
            "notes": "OFFLINE STUB: pixel heuristics only, no vision model was called."}


class Perception:
    def __init__(self, llm=None, gate: ConformalGate | None = None, crop: str = "potato"):
        self.llm = llm
        self.gate = gate or ConformalGate()
        self.crop = crop

    def observe(self, zone_id: str, images: list[bytes]) -> Observation:
        px = [pixel_metrics(b) for b in images]
        if self.llm is None:
            return gate_observation(zone_id, stub_raw(zone_id, px, self.crop), px, self.gate,
                                    source="OFFLINE-STUB (pixel heuristics)")
        cands = candidates_for(self.crop)
        prompt = PROMPT.format(zone_id=zone_id, crop=self.crop, categories=list(CATEGORIES),
                               candidates=json.dumps(cands))
        raw, meta = self.llm.vision_json(prompt, images[:3], system=SYSTEM)
        if not isinstance(raw, dict):
            raw = {}
        return gate_observation(zone_id, raw, px, self.gate, source=meta["model"],
                                reasoning=meta.get("reasoning", ""), candidates=cands)
