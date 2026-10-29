"""Calibrate the perception gate on public, labelled potato leaf photos.

    python eval/public_scans.py fetch        # download the 150 photos listed in eval/public_scans.csv
    python eval/public_scans.py calibrate    # the live vision model reads each one; fit and test the gate

Sources (labels by the dataset authors, images not redistributed here; the CSV lists their URLs):

* PlantDoc (Singh et al., CoDS-COMAD 2020), CC BY 4.0: potato early and late blight photos
  collected from the web, in natural light and backgrounds (90 photos).
* PlantVillage (Mohanty, Hughes and Salathé, Front. Plant Sci. 2016), CC BY-SA 3.0: potato
  healthy, early and late blight leaves photographed in the lab (60 photos).

Method (split conformal, ``fieldpilot.perception.ConformalGate``):

1. Each photo goes through the same ``Perception.observe`` the field agent uses, on the live
   vision model. Its score is ``1 - p``, where ``p`` is the confidence the model gave the true
   label (0 if it never named it). Answers are cached in ``eval/results/public_scans_obs.jsonl``,
   so an interrupted run resumes.
2. The photos are split in half, stratified by label, with a fixed seed. The gate's threshold
   comes from the calibration half only; coverage and the size of the prediction sets are
   measured on the other half.
3. ``calibration.json`` is written with its source, so the gate and the doctor say what it was
   calibrated on. These are leaf close-ups, not drone scans of our fields: the guarantee holds
   for photos like these, and a season of labelled field scans should replace it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "eval" / "public_scans.csv"
DATA = ROOT / "data" / "public_scans"
RESULTS = ROOT / "eval" / "results"
OBS = RESULTS / "public_scans_obs_v2.jsonl"     # v2: the model also returns a differential


def rows() -> list[dict]:
    with open(MANIFEST, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fetch() -> int:
    import requests
    got = skipped = failed = 0
    for r in rows():
        p = DATA / r["file"]
        if p.exists() and p.stat().st_size > 1000:
            skipped += 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                resp = requests.get(r["url"], timeout=60)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    p.write_bytes(resp.content)
                    got += 1
                    break
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        else:
            failed += 1
            print("could not fetch", r["url"])
    print(f"fetched {got}, already present {skipped}, failed {failed} -> {DATA}")
    return 0 if failed == 0 else 1


def p_true(findings: list[dict], truth: str, differential: dict | None = None) -> float:
    """Probability the model gave the true label (the same rule as the gate and ``cli calibrate``)."""
    from fieldpilot.perception import true_label_prob
    return true_label_prob(findings, differential, truth)


def pt(d: dict) -> float:
    return p_true(d["findings"], d["label"], d.get("differential"))


def observe_all() -> list[dict]:
    from fieldpilot.config import load
    from fieldpilot.llm import TokenFactory
    from fieldpilot.perception import ConformalGate, Perception
    done = {}
    if OBS.exists():
        for line in OBS.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            done[d["file"]] = d
    if all(r["file"] in done for r in rows()):
        return [done[r["file"]] for r in rows()]          # every answer cached: no model needed
    cfg = load()
    if not cfg.live_llm:
        raise SystemExit("calibration needs the live vision model (NEBIUS_API_KEY)")
    tf = TokenFactory(cfg)
    per = Perception(llm=tf, crop="potato", gate=ConformalGate())
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = []
    with open(OBS, "a", encoding="utf-8") as fh:
        for i, r in enumerate(rows()):
            if r["file"] in done:
                out.append(done[r["file"]])
                continue
            p = DATA / r["file"]
            if not p.exists():
                print("missing photo (run fetch first):", r["file"])
                continue
            t0 = time.time()
            try:
                o = per.observe("CAL", [p.read_bytes()])
                findings = [{"label": f.get("label"), "confidence": f.get("confidence"),
                             "category": f.get("category")} for f in o.findings]
                diff, err = o.differential, None
            except Exception as e:  # noqa: BLE001
                findings, diff, err = [], None, f"{type(e).__name__}: {str(e)[:200]}"
            d = {"file": r["file"], "label": r["label"], "source": r["source"],
                 "model": tf.vision_model, "findings": findings, "differential": diff, "error": err,
                 "seconds": round(time.time() - t0, 2)}
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
            fh.flush()
            out.append(d)
            top = findings[0]["label"] if findings else "-"
            print(f"[{i + 1}/{len(rows())}] {r['label']:12s} -> {top} "
                  f"p_true={pt(d):.2f} ({d['seconds']}s){' ERROR ' + err if err else ''}")
    return out


def split(obs: list[dict], seed: int = 7) -> tuple[list[dict], list[dict]]:
    by = defaultdict(list)
    for d in obs:
        by[d["label"]].append(d)
    rng = random.Random(seed)
    cal, test = [], []
    for lab in sorted(by):
        xs = sorted(by[lab], key=lambda d: d["file"])
        rng.shuffle(xs)
        h = len(xs) // 2
        cal += xs[:h]
        test += xs[h:]
    return cal, test


def calibrate(alpha: float = 0.1, coarse_alpha: float = 0.05) -> int:
    from fieldpilot.perception import ConformalGate, gate_observation, candidates_for
    obs = [d for d in observe_all() if not d.get("error") and d.get("differential")]
    if len(obs) < 40:
        print(f"only {len(obs)} usable answers; need at least 40")
        return 2
    cal, test = split(obs)

    def coarse_true(d):
        ph = d["differential"].get("healthy", 0.0)
        return ph if d["label"] == "healthy" else 1 - ph

    fine = ConformalGate.from_scores([1 - pt(d) for d in cal], alpha=alpha)
    coarse = ConformalGate.from_scores([1 - coarse_true(d) for d in cal], alpha=coarse_alpha)
    gate = ConformalGate(alpha=fine.alpha, qhat=fine.qhat, n=fine.n, coarse_alpha=coarse.alpha,
                         coarse_qhat=coarse.qhat, coarse_n=coarse.n, basis="differential")
    thr, cthr = 1 - float(gate.qhat), 1 - float(gate.coarse_qhat)

    # held-out half: the fine gate, the coarse gate, and the production decision end to end
    fine_cov = sum(pt(d) >= thr for d in test) / len(test)
    fine_sizes = [sum(1 for p in d["differential"].values() if p >= thr) for d in test]
    coarse_cov = sum(coarse_true(d) >= cthr for d in test) / len(test)
    cands = candidates_for("potato")
    e2e = {"auto_healthy_right": 0, "auto_healthy_wrong": 0, "auto_disease_right": 0,
           "auto_disease_wrong": 0, "to_human": 0, "to_human_named_disease": 0}
    for d in test:
        raw = {"findings": [{"category": "none" if str(f["label"]).lower() == "healthy" else "disease",
                             "label": f["label"], "severity": 0 if str(f["label"]).lower() == "healthy" else 2,
                             "confidence": f.get("confidence") or 0} for f in d["findings"]],
               "differential": d["differential"]}
        o = gate_observation("CAL", raw, [], gate, source="eval", candidates=cands)
        if o.needs_review:
            e2e["to_human"] += 1
            e2e["to_human_named_disease"] += int(d["label"] != "healthy" and "healthy" not in o.prediction_set)
        elif o.prediction_set == ["healthy"]:
            e2e["auto_healthy_right" if d["label"] == "healthy" else "auto_healthy_wrong"] += 1
        else:
            ok = d["label"] in o.prediction_set[0]
            e2e["auto_disease_right" if ok else "auto_disease_wrong"] += 1

    def top1(d):
        return max(d["differential"], key=d["differential"].get)
    top_ok = sum(1 for d in obs if top1(d) == d["label"]) / len(obs)
    sick_ok = sum(1 for d in obs if (top1(d) == "healthy") == (d["label"] == "healthy")) / len(obs)
    confusion = Counter(f"{d['label']} -> {top1(d)}" for d in obs)
    model = obs[0]["model"]
    meta = {"alpha": gate.alpha, "qhat": gate.qhat, "n": gate.n, "basis": "differential",
            "coarse": {"alpha": gate.coarse_alpha, "qhat": gate.coarse_qhat, "n": gate.coarse_n,
                       "classes": ["healthy", "diseased"]},
            "source": "public potato leaf photos: PlantDoc (CC BY 4.0) + PlantVillage (CC BY-SA 3.0), "
                      "eval/public_scans.csv",
            "model": model, "date": date.today().isoformat(),
            "test": {"n": len(test), "coverage": round(fine_cov, 3), "target": 1 - alpha,
                     "mean_set_size": round(sum(fine_sizes) / len(fine_sizes), 2),
                     "coarse_coverage": round(coarse_cov, 3), "coarse_target": 1 - coarse_alpha,
                     "decisions": e2e},
            "top_label_correct": round(top_ok, 3), "healthy_vs_diseased_correct": round(sick_ok, 3),
            "confusion": dict(sorted(confusion.items())),
            "labels": dict(Counter(d["label"] for d in obs))}
    (ROOT / "calibration.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    name = f"calibration_{date.today().isoformat()}"
    (RESULTS / f"{name}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    n = len(test)
    md = [f"# Perception gate calibration, {meta['date']}", "",
          f"Vision model `{model}` on {len(obs)} public, labelled potato leaf photos "
          f"({', '.join(f'{k} {v}' for k, v in meta['labels'].items())}). The model returns a probability "
          "for each candidate diagnosis (healthy, early blight, late blight, other problem). Gates are "
          f"fitted on {gate.n} photos and tested on the other {n}.", "",
          "| | |", "|---|---|",
          f"| Model's most likely diagnosis correct (all photos) | {top_ok:.1%} |",
          f"| Model right about healthy vs diseased (all photos) | {sick_ok:.1%} |",
          f"| Which disease: gate at alpha {alpha} (threshold p >= {thr:.2f}) | coverage {fine_cov:.1%}, "
          f"mean set {meta['test']['mean_set_size']} of 4 labels |",
          f"| Healthy vs diseased: gate at alpha {coarse_alpha} (threshold p >= {cthr:.2f}) | coverage "
          f"**{coarse_cov:.1%}** (target >= {1 - coarse_alpha:.0%}) |",
          f"| Held-out decisions: healthy, passed without a human | {e2e['auto_healthy_right']} right, "
          f"{e2e['auto_healthy_wrong']} wrong |",
          f"| Held-out decisions: one disease named without a human | {e2e['auto_disease_right']} right, "
          f"{e2e['auto_disease_wrong']} wrong |",
          f"| Held-out decisions: sent to a human | {e2e['to_human']} of {n} "
          f"({e2e['to_human_named_disease']} as 'diseased, pick which') |", "",
          "Confusion (true -> model's most likely): " + "; ".join(f"{k} {v}" for k, v in meta["confusion"].items()),
          "", "What it means: on these photos Nemotron 3.5 Super VL is reliable about healthy versus "
          "diseased, but close to a coin toss between early and late blight. The gate therefore lets a "
          "healthy zone through on its own and sends every diseased zone to the operator with the "
          "candidate diseases, instead of trusting the model's first guess.", "",
          "These are leaf close-ups from public datasets, not drone scans of our fields. The coverage "
          "guarantee holds for photos like these; a season of labelled field scans should replace this "
          "calibration (`python -m fieldpilot.cli calibrate labels.csv`)."]
    (RESULTS / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "calibrate", "all"])
    ap.add_argument("--alpha", type=float, default=0.1)
    a = ap.parse_args(argv)
    if a.cmd in ("fetch", "all"):
        rc = fetch()
        if rc and a.cmd == "fetch":
            return rc
    if a.cmd in ("calibrate", "all"):
        return calibrate(a.alpha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
