"""Operator corrections become a regression eval for booking intake.

Every time the operator fixes a job the model read (today: the field area, the number that
sizes the whole day), the booking message and the confirmed value are saved. Replaying
those messages through Nemotron on Token Factory, plus the unit conversion in code, says
whether a model or prompt change makes intake better or worse on real bookings, not on
examples we wrote ourselves.

    python -m fieldpilot.cli flywheel            # how many cases, what kind
    python -m fieldpilot.cli flywheel --eval     # replay them through the live model

Cases live in ``runs/flywheel/intake_cases.jsonl``, one per line. Nothing here trains a
model: Token Factory's fine-tuning catalogue has no Nemotron base model today, so the
corrections are an evaluation set first. The file keeps the message verbatim, so it is
also the raw material for a future fine-tune.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT
CORRECTABLE = ("area_ha",)


def cases_path(runs_dir: str | Path | None = None) -> Path:
    base = Path(runs_dir) if runs_dir else ROOT / "runs"
    return base / "flywheel" / "intake_cases.jsonl"


def record(job: dict, corrections: dict, runs_dir: str | Path | None = None) -> dict | None:
    """Save one correction. Returns the case, or None if nothing correctable changed."""
    expected = {}
    for k in CORRECTABLE:
        if k in corrections and corrections[k] is not None:
            try:
                v = round(float(corrections[k]), 3)
            except (TypeError, ValueError):
                continue
            if job.get(k) is None or abs(float(job[k]) - v) > 1e-3:
                expected[k] = v
    msg = (job.get("message") or "").strip()
    if not expected or not msg:
        return None
    case = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": msg,
            "expected": expected, "model_read": {k: job.get(k) for k in CORRECTABLE},
            "model_fields": job.get("extracted") or {}, "model": job.get("model"),
            "protocol": job.get("protocol")}
    p = cases_path(runs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case


def load(runs_dir: str | Path | None = None) -> list[dict]:
    p = cases_path(runs_dir)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def evaluate(llm, cases: list[dict], op=None, tol: float = 0.01) -> dict:
    """Replay each message through intake; an area within ``tol`` (relative) passes."""
    from .dayplan import Operator
    from .desk import intake_messages
    op = op or Operator()
    rows = intake_messages([{"text": c["message"]} for c in cases], op, llm,
                           geocoder=lambda *a, **k: [])
    passed, fails = 0, []
    for c, r in zip(cases, rows):
        want, got = c["expected"].get("area_ha"), r.get("area_ha")
        ok = want is not None and got is not None and abs(got - want) <= tol * max(want, 1e-9)
        passed += ok
        if not ok:
            fails.append({"message": c["message"], "expected_ha": want, "got_ha": got,
                          "flags": r.get("confirm")})
    n = len(cases)
    return {"cases": n, "passed": passed, "accuracy": round(passed / n, 3) if n else None,
            "model": getattr(llm, "agent_model", None), "failures": fails[:20]}


def main(argv: list[str]) -> int:
    from .config import load as load_cfg
    cfg = load_cfg()
    cases = load(cfg.runs_dir)
    print(f"{len(cases)} operator-corrected bookings in {cases_path(cfg.runs_dir)}")
    if "--eval" not in argv:
        return 0
    if not cases:
        print("nothing to replay yet: correct a field area on the desk first")
        return 0
    if not cfg.live_llm:
        print("replaying needs NEBIUS_API_KEY (Nemotron on Token Factory)")
        return 2
    from .llm import TokenFactory
    res = evaluate(TokenFactory(cfg), cases)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0
