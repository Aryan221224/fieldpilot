"""The operator's desk: bookings in, an efficient, weather-safe, battery-feasible day out.

    python -m fieldpilot.cli day examples/desk_demo_in.json         # live: Nemotron + Open-Meteo
    POST /api/desk/intake   {"messages": [...], "operator": {...}}
    POST /api/desk/plan     {"jobs": [...], "operator": {...}, "date": "2026-10-28"}

Nemotron (Token Factory) reads each booking, in any language, and writes the operator's
morning brief in the operator's language. Code does the rest: units, place lookup,
forecasts, rule-pack spray windows, sorties, batteries. Every number in the brief must
already be in the plan, or a template brief is used.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import dayplan, intake
from .dayplan import JobSpec, Operator

LANGS = {"hi": "Hindi (Devanagari script)", "en": "English", "es": "Spanish",
         "pt": "Portuguese", "fr": "French", "zh": "Simplified Chinese", "th": "Thai",
         "sw": "Swahili", "id": "Indonesian", "vi": "Vietnamese", "bn": "Bengali",
         "mr": "Marathi", "ta": "Tamil", "te": "Telugu"}
SCRIPTS = {"hi": ("\u0900", "\u097f"), "mr": ("\u0900", "\u097f"), "bn": ("\u0980", "\u09ff"),
           "ta": ("\u0b80", "\u0bff"), "te": ("\u0c00", "\u0c7f"), "zh": ("\u4e00", "\u9fff"),
           "th": ("\u0e00", "\u0e7f")}

BRIEF_SYSTEM = """You write the morning voice-note brief for an agricultural spray-drone operator,
in simple spoken {lang}. 6-9 short sentences. Say: how many farms, in what order with the
place names and times, when each battery set must go on charge, which farmers must be phoned
to move to another day and when, and one safety line about wind and heat. Use ONLY the
numbers and times given in the JSON; do not add any number of your own."""


def intake_messages(messages: list, op: Operator, llm, geocoder=intake.geocode) -> list[dict]:
    """messages: strings, or {"text", "pin": [lat, lon], "spray_ha"} (pin = the farmer's
    WhatsApp location; spray_ha = the area a FieldPilot scan justified)."""
    out = []
    for m in messages:
        m = m if isinstance(m, dict) else {"text": m}
        j = intake.parse_booking((m.get("text") or "").strip(), llm, base=op.base,
                                 geocoder=geocoder, pin=m.get("pin"),
                                 local_units=op.local_units, country=op.country)
        d = j.to_dict()
        if m.get("spray_ha") is not None:
            d["spray_ha"] = float(m["spray_ha"])
        out.append(d)
    return out


def jobs_from(rows: list[dict]) -> tuple[list[JobSpec], list[dict]]:
    """Schedulable jobs, and the ones that need a call-back first (no area or no place)."""
    ok, callback = [], []
    for r in rows:
        if r.get("area_ha") and r.get("lat") is not None and r.get("lon") is not None:
            ok.append(JobSpec.from_dict(r | {"farmer": r.get("farmer") or r.get("job_id")}))
        else:
            callback.append({"job_id": r.get("job_id"), "farmer": r.get("farmer"),
                             "why": [c for c in r.get("confirm", [])
                                     if "area" in c or "village" in c or "pin" in c]
                             or ["area or location missing"]})
    return ok, callback


def _numbers(text: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+", text.translate(intake.DEVANAGARI_DIGITS))}


def template_brief(plan: dict, lang: str = "en") -> str:
    m = plan["metrics"]
    if lang == "hi":
        parts = [f"आज {m['farms_served']} खेत, कुल {m['sprayed_ha']} हेक्टेयर, {m['battery_cycles']} उड़ान।"]
        for r in plan["plan"]:
            first = r["sorties"][0]["start"] if r["sorties"] else r["arrive"]
            parts.append(f"{r['village'] or r['farmer']} में {r['farmer']} जी का खेत, पहली उड़ान {first} बजे।")
        if plan["batteries"]:
            b = plan["batteries"][0]
            parts.append(f"बैटरी सेट {b['set']} को {b['charge_from']} बजे चार्जिंग पर लगाएं, {b['ready']} बजे तैयार।")
        for d in plan["deferred"]:
            parts.append(f"{d.get('farmer') or d['job_id']} जी को फ़ोन करें: अगला सही समय "
                         f"{d.get('next_window', 'कल सुबह')}।")
        parts.append("तेज़ हवा या तेज़ गर्मी में छिड़काव नहीं।")
        return " ".join(parts)
    parts = [f"Today: {m['farms_served']} farms, {m['sprayed_ha']} ha, {m['battery_cycles']} sorties."]
    for r in plan["plan"]:
        first = r["sorties"][0]["start"] if r["sorties"] else r["arrive"]
        parts.append(f"{r['farmer']} at {r['village'] or 'the farm'}: first sortie {first}.")
    if plan["batteries"]:
        b = plan["batteries"][0]
        parts.append(f"Put battery set {b['set']} on charge at {b['charge_from']}; ready {b['ready']}.")
    for d in plan["deferred"]:
        parts.append(f"Call {d.get('farmer') or d['job_id']}: next window {d.get('next_window', 'tomorrow')}.")
    parts.append("No spraying in strong wind or heat.")
    return " ".join(parts)


def brief(plan: dict, llm, lang: str = "hi") -> dict:
    """Nemotron writes the brief in the operator's language; any number not in the plan,
    or text not in that language's script, rejects it."""
    tpl = template_brief(plan, lang)
    if llm is None:
        return {"text": tpl, "by": "template"}
    facts = {"metrics": plan["metrics"], "plan": [
        {k: r[k] for k in ("farmer", "village", "arrive", "done", "sprayed_ha")} |
        {"sorties": [s["start"] for s in r["sorties"]]} for r in plan["plan"]],
        "batteries": plan["batteries"], "call_for_another_day": plan["deferred"]}
    try:
        txt = llm.text([{"role": "system", "content": BRIEF_SYSTEM.format(lang=LANGS.get(lang, lang))},
                        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                       max_tokens=700, temperature=0.2, purpose="brief").strip()
    except Exception as e:  # noqa: BLE001
        return {"text": tpl, "by": "template", "note": f"model call failed ({type(e).__name__})"}
    letters = [c for c in txt if c.isalpha()]
    if not letters or txt.lstrip().startswith(("{", "[")):
        return {"text": tpl, "by": "template", "note": "model brief rejected: not prose"}
    if lang in SCRIPTS:
        lo, hi = SCRIPTS[lang]
        if sum(1 for c in letters if lo <= c <= hi) / len(letters) < 0.5:
            return {"text": tpl, "by": "template",
                    "note": f"model brief rejected: not in {LANGS.get(lang, lang)}"}
    allowed = _numbers(json.dumps(facts, ensure_ascii=False))
    stray = sorted(_numbers(txt) - allowed)
    if stray:
        return {"text": tpl, "by": "template",
                "note": f"model brief rejected: numbers not in the plan {stray[:5]}"}
    return {"text": txt, "by": (llm.model_for("brief") if hasattr(llm, "model_for") else getattr(llm, "agent_model", None)) or "model"}


def plan(jobs: list[JobSpec], op: Operator, day: date, forecasts=None, llm=None,
         ledger_from: int | None = None) -> dict:
    """``ledger_from``: index in ``llm.ledger`` where this day's calls began (the intake),
    so the day's Token Factory cost covers reading the bookings as well as the brief."""
    forecasts = forecasts or dayplan.live_forecasts(jobs)
    c = dayplan.compare(jobs, op, day, forecasts)
    led = getattr(llm, "ledger", None)
    start = ledger_from if ledger_from is not None else (len(led) if led is not None else 0)
    c["brief"] = brief(c["fieldpilot"], llm, op.language)
    c["windows"] = windows(jobs, forecasts, day, op)
    c["rules"] = {"id": op.pack.pack_id, "title": op.pack.title, "binding": op.pack.binding,
                  "sources": list(op.pack.sources)}
    c["forecast_source"] = "open-meteo hourly forecast per farm"
    c["models"] = {"intake_and_brief": (llm.model_for("intake") if hasattr(llm, "model_for") else getattr(llm, "agent_model", None)) if llm else None}
    if led is not None:
        from .llm import usage_summary
        c["token_factory"] = usage_summary(led[start:]) | {
            "note": "this operator-day's Nemotron calls on Token Factory; estimate at list price"}
    c["sheet_html"] = dayplan.day_sheet_html(c["fieldpilot"], op)
    return c


def windows(jobs: list[JobSpec], forecasts: dict, day: date, op: Operator) -> dict:
    """Per farm, per local hour 05-19: may the drone spray, and why not? (for the timeline)"""
    from datetime import time
    out = {}
    for j in jobs:
        fc = forecasts[j.job_id]
        hrs = []
        for h in range(5, 19):
            t = datetime.combine(day, time(h, 30), op.tz)            # mid-hour
            x = dayplan.hour_at(fc, t)
            why = op.pack.reasons(x, op.utc_offset_h) if x else ["no forecast"]
            hrs.append({"h": h, "ok": not why, "why": why,
                        "wind": round(x.wind_ms, 1) if x else None,
                        "rh": round(x.rh_pct) if x else None,
                        "temp": round(x.temp_c) if x else None})
        out[j.job_id] = hrs
    return out


def save(result: dict, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = runs_dir / f"day_{result['fieldpilot']['day']}_{stamp}.json"
    p.write_text(json.dumps({k: v for k, v in result.items() if k != "sheet_html"},
                            ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    p.with_suffix(".html").write_text(result["sheet_html"], encoding="utf-8")
    return p


def headline(res: dict, n_bookings: int) -> str:
    """One sentence for the submission, straight from the saved result."""
    a, b = res["fieldpilot"]["metrics"], res["baseline"]["metrics"]

    def pct(x, y):
        return f"{(x - y) / y * 100:+.0f} %" if y else "n/a"
    return (f"On a live-forecast operator day of {n_bookings} bookings ({res['fieldpilot']['day']}, "
            f"{res['rules']['id']} rules, same batteries), FieldPilot fully served "
            f"{a['farms_served_in_window']} farms inside the spray window vs {b['farms_served_in_window']}, "
            f"at {a['effective_ha_per_battery_cycle']} effective ha per battery cycle vs "
            f"{b['effective_ha_per_battery_cycle']} "
            f"({pct(a['effective_ha_per_battery_cycle'], b['effective_ha_per_battery_cycle'])}), with "
            f"{a['out_of_window_sorties']} sorties outside the window vs {b['out_of_window_sorties']}, "
            f"{a['sprayed_beyond_need_ha']} ha sprayed beyond need vs {b['sprayed_beyond_need_ha']} and "
            f"{a['leftover_mix_l']} L of mixed product left over vs {b['leftover_mix_l']} L")


def fill_submission(sentence: str, saved: str) -> bool:
    from .config import ROOT
    p = ROOT / "docs" / "SUBMISSION.md"
    t = p.read_text(encoding="utf-8")
    import re
    pat = re.compile(r"(?<!`)\[DAY\](?!`)")
    if not pat.search(t):
        return False
    p.write_text(pat.sub(lambda _m: f"{sentence} (`runs/{saved}`)", t), encoding="utf-8")
    return True


def main(path: str, day: str | None = None, fill: bool = False) -> int:
    from .config import load
    from .llm import TokenFactory
    cfg = load()
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    op = Operator.from_dict(d.get("operator", {}))
    when = date.fromisoformat(day or d.get("date") or date.today().isoformat())
    llm = TokenFactory(cfg) if cfg.live_llm else None
    led0 = 0
    rows = list(d.get("jobs", []))
    if d.get("messages"):
        if llm is None:
            print("Reading bookings needs NEBIUS_API_KEY: Nemotron on Token Factory parses them.")
            return 2
        # each message: a string, or {"text": ..., "spray_ha": <from a FieldPilot scan>,
        # "pin": [lat, lon] dropped by the farmer on WhatsApp}
        parsed = intake_messages(d["messages"], op, llm)
        for r in parsed:
            print(f"  {r['job_id']}  {r.get('farmer')}  {r.get('village')}  {r.get('crop')}  "
                  f"{r.get('area_ha')} ha  confirm: {'; '.join(r['confirm'])}")
        rows += parsed
    jobs, callback = jobs_from(rows)
    res = plan(jobs, op, when, llm=llm, ledger_from=led0 if llm is not None else None)
    res["callback"] = callback
    p = save(res, cfg.runs_dir)
    if res.get("token_factory"):
        t = res["token_factory"]
        print(f"Token Factory: {t['calls']} calls ({t['schema_constrained_calls']} schema-constrained), "
              f"{t['prompt_tokens']}+{t['completion_tokens']} tokens, about ${t['usd_estimate']} for the day")
    for k in ("fieldpilot", "baseline"):
        print(k.upper(), json.dumps(res[k]["metrics"], ensure_ascii=False))
    print("brief:", res["brief"]["text"])
    print("saved", p, "and", p.with_suffix(".html"))
    sen = headline(res, len(rows))
    print(sen)
    fpm = res["fieldpilot"]["metrics"]
    unread = sum(1 for r in rows if r.get("area_ha") in (None, 0))
    if fill and (not fpm.get("farms_booked") or unread == len(rows)):
        print("docs/SUBMISSION.md not touched: no booking was read into a job "
              f"({unread} of {len(rows)} without an area), so this day proves nothing")
    elif fill and not cfg.on_nebius:
        print("docs/SUBMISSION.md not touched: this run is " + cfg.mode()["llm"] +
              ", and only a Nebius Token Factory run may fill it")
    elif fill:
        print("docs/SUBMISSION.md updated" if fill_submission(sen, p.name)
              else "docs/SUBMISSION.md: no [DAY] placeholder to fill")
    return 0
