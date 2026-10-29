"""How far the real runs deviate from the rehearsal's predictions - and the pump calibration.

    python -m fieldpilot.cli deviation                                  # newest of everything
    python -m fieldpilot.cli deviation --litres 1.35                    # tank drop after the take
    python -m fieldpilot.cli deviation --jug-full 1.70 --jug-duty 1650 --jug-at-duty 1.02
                                                                        # 30 s in a jug, twice

Inputs (newest by default): ``runs/rehearsal/rehearsal_*.json`` (the prediction), a real bench
log ``runs/bench_*.json`` (serial or MAVLink sink, not dry or emulated), and a live field run
``runs/R-*.json`` made on Nebius Token Factory. Writes ``runs/deviation_<time>.md``.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT


def _newest(pattern: str, ok=lambda d: True) -> tuple[Path | None, dict | None]:
    for p in sorted(ROOT.glob(pattern), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if ok(d):
            return p, d
    return None, None


def pct(actual: float | None, pred: float | None) -> str:
    if actual is None or pred in (None, 0):
        return "-"
    return f"{(actual - pred) / pred * 100:+.0f} %"


def fit_pump(jug_full_l_30s: float | None, jug_duty_us: int | None, jug_at_duty_l_30s: float | None,
             off: int = 1100, full: int = 1900) -> dict:
    """Full system flow and stall duty from two 30-second jug measurements."""
    out = {}
    if jug_full_l_30s:
        q_full = jug_full_l_30s * 2
        out["q_full_l_min"] = round(q_full, 3)
        if jug_duty_us and jug_at_duty_l_30s:
            d = (jug_duty_us - off) / (full - off)
            r = (jug_at_duty_l_30s * 2) / q_full
            if 0 < r < 1 and r < d:
                out["stall_duty"] = round((d - r) / (1 - r), 3)
            else:
                out["stall_duty_note"] = "measured flow is not below the linear line; check the reading"
    return out


def edit_distance(a: list, b: list) -> int:
    dp = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, y in enumerate(b, 1):
            cur = min(dp[j] + 1, dp[j - 1] + 1, prev + (x != y))
            prev, dp[j] = dp[j], cur
    return dp[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fieldpilot deviation")
    ap.add_argument("--predicted")
    ap.add_argument("--bench")
    ap.add_argument("--run")
    ap.add_argument("--litres", type=float, help="water used by the real bench take (tank drop)")
    ap.add_argument("--jug-full", type=float, help="litres collected in 30 s at full duty (P1900)")
    ap.add_argument("--jug-duty", type=int, help="PWM used for the second jug test, e.g. 1650")
    ap.add_argument("--jug-at-duty", type=float, help="litres collected in 30 s at --jug-duty")
    a = ap.parse_args(argv)

    pp, pred = (Path(a.predicted), json.loads(Path(a.predicted).read_text(encoding="utf-8"))) \
        if a.predicted else _newest("runs/rehearsal/rehearsal_*.json")
    if pred is None:
        print("no prediction: run `python -m fieldpilot.cli rehearse` first")
        return 2
    bp, bench = (Path(a.bench), json.loads(Path(a.bench).read_text(encoding="utf-8"))) if a.bench else \
        _newest("runs/bench_*.json", lambda d: str(d.get("sink", "")).split(":")[0] in ("serial", "mavlink"))
    rp, run = (Path(a.run), json.loads(Path(a.run).read_text(encoding="utf-8"))) if a.run else \
        _newest("runs/R-*.json", lambda d: (d.get("mode") or {}).get("llm") == "nebius-token-factory")

    L = [f"# Deviation from the rehearsal prediction · "
         f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
         f"Prediction: `{pp.relative_to(ROOT) if pp else '-'}` ({pred.get('label')})", ""]

    pb = pred.get("bench") or {}
    L += ["## Bench (T1 pump)", "", "| Quantity | Predicted | Actual | Deviation |", "|---|---|---|---|"]
    if bench:
        s = bench.get("summary", {})
        ps = pb.get("bench_summary", {})
        L.append(f"| Spray events | {ps.get('pump_on_events')} | {s.get('pump_on_events')} | "
                 f"{pct(s.get('pump_on_events'), ps.get('pump_on_events'))} |")
        L.append(f"| Seconds spraying (command) | {ps.get('spray_seconds')} | {s.get('spray_seconds')} | "
                 f"{pct(s.get('spray_seconds'), ps.get('spray_seconds'))} |")
        L.append(f"| Zones sprayed | {', '.join(ps.get('zones') or [])} | {', '.join(s.get('zones') or [])} | "
                 f"{'same' if ps.get('zones') == s.get('zones') else 'DIFFERENT'} |")
        L.append(f"| Ack latency p50 (ms) | - | {s.get('ack_latency_ms_p50')} | - |")
    else:
        L.append("| real bench log | - | none yet (`runs/bench_*.json` from a serial sink) | - |")
    ep = pb.get("emulated_pump") or {}
    bd = pb.get("litres_bounds_over_param_ranges") or {}
    if a.litres is not None:
        L.append(f"| Water sprayed (L) | {ep.get('litres')} ({bd.get('litres_min')}–{bd.get('litres_max')}) | "
                 f"{a.litres} | {pct(a.litres, ep.get('litres'))}"
                 + (" (inside the predicted range)" if bd and bd['litres_min'] <= a.litres <= bd['litres_max']
                    else " (outside the predicted range)" if bd else "") + " |")
    fit = fit_pump(a.jug_full, a.jug_duty, a.jug_at_duty)
    if fit:
        L += ["", "### Pump calibration from the jug tests", ""]
        if "q_full_l_min" in fit:
            L.append(f"- Full system flow: **{fit['q_full_l_min']} L/min** (sheet 3.5; "
                     f"{pct(fit['q_full_l_min'], 3.5)}). Set `pump_max_l_min` / `PumpParams.q_full_l_min`.")
        if "stall_duty" in fit:
            L.append(f"- Stall duty: **{fit['stall_duty']}** (assumed 0.20). Set `PUMP_STALL_DUTY` in "
                     "fieldpilot/airframe.py, then the bench flow law lays the planned L/ha.")
        if fit.get("stall_duty_note"):
            L.append(f"- {fit['stall_duty_note']}")

    pf = pred.get("field_run") or {}
    L += ["", "## Field agent (stand-in vs live Nemotron)", ""]
    if run:
        live_tools = [e.get("name") for e in run.get("trace", []) if e.get("type") == "tool_call"]
        pt = pf.get("tool_sequence") or []
        pz = ((pf.get("proposal") or {}).get("headline"))
        L += [f"- Live run: `{rp.relative_to(ROOT)}` on {(run.get('models') or {}).get('agent')}",
              f"- Tool sequence: predicted {len(pt)} calls, live {len(live_tools)}; edit distance "
              f"{edit_distance(pt, live_tools)}",
              f"- Predicted proposal: {pz}", f"- Live proposal: {(run.get('proposal') or {}).get('headline')}"]
    else:
        L.append("- No live Nebius run yet (`runs/R-*.json` with mode nebius-token-factory).")

    for key in ("in", "br"):
        d = pred.get(f"day_{key}")
        if not d:
            continue
        lp, live = _newest("runs/day_*.json", lambda x, k=key: (x.get("models") or {}).get(
            "intake_and_brief", "").startswith("nvidia/") and x.get("fieldpilot", {}).get("rules") ==
            ("IN-SOP-2023" if k == "in" else "GOOD-PRACTICE"))
        L += ["", f"## Operator desk day · {key.upper()}", "", "| Metric | Predicted | Live | Deviation |",
              "|---|---|---|---|"]
        for m in ("farms_served_in_window", "effective_ha", "battery_cycles", "out_of_window_sorties"):
            pv = d["fieldpilot"].get(m)
            lv = live["fieldpilot"]["metrics"].get(m) if live else None
            L.append(f"| {m} | {pv} | {lv if lv is not None else '-'} | {pct(lv, pv)} |")

    t = pred.get("token_use_forecast") or {}
    if t:
        L += ["", "## Token Factory cost", "", f"- Forecast: about ${t['usd_at_token_factory_price']} "
              f"for the rehearsal's {t['calls']} calls. Compare with Billing → Usage after the live run."]
    out = ROOT / "runs" / f"deviation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n-> {out.relative_to(ROOT)}")
    return 0
