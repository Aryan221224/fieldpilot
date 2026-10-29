"""Rehearsal: the whole project run end to end without a Token Factory key or a flyable drone.

    python -m fieldpilot.cli rehearse            # everything; report in runs/rehearsal/

What it is for
--------------
1. **Proving the code path.** A local stand-in for Nebius Token Factory speaks the same
   OpenAI-compatible protocol (``/v1/models``, ``/v1/chat/completions`` with native tool
   calls, image inputs, JSON replies). FieldPilot's real client, parsers, number checks and
   fall-backs run against it exactly as they will against Nebius. When the key works, the
   same commands run live with one line changed in ``.env``.
2. **A predicted run to measure the real one against.** Every step saves what FieldPilot
   predicts - the tool sequence, the zones, sortie loads, pump schedule, litres per zone,
   token use and cost - so ``python -m fieldpilot.cli deviation`` can report how far the real
   run and the real hardware deviate from the plan.

What it is not
--------------
The stand-in is not Nemotron. Its replies come from FieldPilot's own deterministic policy
and simple parsers, so a rehearsal says nothing about Nemotron's quality. Every file it writes
is stamped ``REHEARSAL (mock Token Factory, not Nebius)``, lives under ``runs/rehearsal`` and
``eval/results/rehearsal``, and is refused by ``doctor --submission`` and by every
``--fill-submission``.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import Request   # module level: FastAPI resolves the string annotation here

LABEL = "REHEARSAL (mock Token Factory, not Nebius)"
AGENT_ID = "REHEARSAL-mock/nemotron-3-super-120b-a12b"
VISION_ID = "REHEARSAL-mock/Nemotron-3.5-Super-VL"
FAST_ID = "REHEARSAL-mock/Nemotron-3_5-Lightning"
# The stand-in mirrors the live catalog's three roles: Nemotron 3 Super (agent tool loop),
# Nemotron 3.5 Super VL (perception), Nemotron 3.5 Lightning (bookings and the brief).

_state_ref: Callable | None = None
_ledger: list[dict] = []
_lock = threading.Lock()


def active() -> bool:
    return os.environ.get("FP_REHEARSAL") == "1"


def bind(state_ref: Callable | None) -> None:
    """The agent run the stand-in answers for (the deterministic policy reads its state)."""
    global _state_ref
    _state_ref = state_ref


def ledger() -> list[dict]:
    with _lock:
        return list(_ledger)


def _tokens(x) -> int:
    """Approximate token count (4 characters per token; images ~ 1 000 tokens each)."""
    if isinstance(x, str):
        return max(1, len(x) // 4)
    if isinstance(x, list):
        n = 0
        for part in x:
            if isinstance(part, dict) and part.get("type") == "image_url":
                n += 1000
            elif isinstance(part, dict):
                n += _tokens(part.get("text") or "")
            else:
                n += _tokens(str(part))
        return n
    if isinstance(x, dict):
        return _tokens(json.dumps(x, ensure_ascii=False))
    return 1


# ---------------------------------------------------------------------------
# The stand-in's answers
# ---------------------------------------------------------------------------

CROPS = {"aalu": "potato", "alu": "potato", "आलू": "potato", "potato": "potato",
         "sarson": "mustard", "सरसों": "mustard", "mustard": "mustard",
         "gehun": "wheat", "गेहूं": "wheat", "गेहूँ": "wheat", "wheat": "wheat",
         "dhan": "rice", "धान": "rice", "rice": "rice", "arroz": "rice",
         "soja": "soybean", "soy": "soybean", "soybean": "soybean", "milho": "maize",
         "maize": "maize", "corn": "maize", "café": "coffee", "cafe": "coffee", "coffee": "coffee",
         "tomate": "tomato", "tomato": "tomato", "cotton": "cotton", "kapas": "cotton",
         "algodão": "cotton"}
PROBLEMS = [("kaale dhabbe", "late blight"), ("jhulsa", "late blight"), ("late blight", "late blight"),
            ("requeima", "late blight"), ("माहू", "aphid"), ("mahu", "aphid"), ("maahu", "aphid"),
            ("aphid", "aphid"), ("kharpatwar", "weeds"), ("weed", "weeds"),
            ("ferrugem", "soybean rust"), ("lagarta", "fall armyworm"),
            ("bicho-mineiro", "coffee leaf miner"), ("percevejo", "stink bug"),
            ("पत्ते सूख", "leaf drying"), ("blight", "blight")]
UNIT_WORDS = {"acre": "acre", "acres": "acre", "acre.": "acre", "bigha": "bigha", "बीघा": "bigha",
              "hectare": "hectare", "hectares": "hectare", "ha": "hectare", "alqueire": "alqueire",
              "alqueires": "alqueire", "mu": "mu", "亩": "mu", "rai": "rai", "feddan": "feddan"}
PRODUCTS = ["mancozeb", "chlorantraniliprole", "imidacloprid", "azoxystrobin", "glyphosate"]


def _intake(message: str) -> dict:
    from .intake import NUMBER_WORDS
    text = message.strip()
    low = text.lower()
    toks = re.findall(r"[\wऀ-ॿ.\-]+", low)
    out: dict = {k: None for k in ("farmer", "phone", "village", "district", "state", "crop",
                                   "problem", "area_value", "area_unit", "area_quote", "product",
                                   "preferred_day")}
    for i, t in enumerate(toks):
        unit = UNIT_WORDS.get(t.strip("."))
        if not unit or i == 0:
            continue
        prev = toks[i - 1]
        val = None
        try:
            val = float(prev)
        except ValueError:
            val = NUMBER_WORDS.get(prev)
        if val is not None:
            out["area_value"], out["area_unit"] = val, unit
            m = re.search(re.escape(prev) + r"\s*" + re.escape(t.strip(".")), low)
            out["area_quote"] = text[m.start():m.end()] if m else None
            break
    for k, v in CROPS.items():
        if re.search(r"(?<![\wऀ-ॿ])" + re.escape(k) + r"(?![\wऀ-ॿ])", low):
            out["crop"] = v
            break
    for k, v in PROBLEMS:
        if k in low:
            out["problem"] = v
            break
    for p in PRODUCTS:
        if p in low:
            out["product"] = p.capitalize()
    m = re.search(r"\+?\d[\d\s-]{9,}\d", text)
    if m:
        out["phone"] = m.group(0)
    # names and places, by the common booking patterns
    m = re.search(r"([\wऀ-ॿ]+)\s+(?:se|से)\b", text)
    if m:
        out["village"] = m.group(1)
    m = re.search(r"([\wऀ-ॿ]+)\s+(?:bol|बोल)", text)
    if m:
        out["farmer"] = m.group(1)
    elif out["village"]:
        m = re.search(re.escape(out["village"]) + r"\s+(?:se|से)\s+([^.।,]+)", text)
        if m:
            out["farmer"] = m.group(1).strip()
    if not out["farmer"]:
        m = re.search(r"^([\wÀ-ſ]+),\s*([\wÀ-ſ ]+?)\s+(here|gaon)\b", text, re.I)
        if m and m.group(3).lower() == "here":
            out["village"], out["farmer"] = m.group(1), m.group(2)
        elif m:
            out["farmer"], out["village"] = m.group(1), m.group(2)
    if not out["farmer"]:
        m = re.search(r"aqui é o ([\wÀ-ſ]+).*?\bem ([\wÀ-ſ]+)", text)
        if m:
            out["farmer"], out["village"] = m.group(1), m.group(2)
    if not out["farmer"]:
        m = re.search(r"^(?:Oi,\s*)?([\wÀ-ſ]+) de ([\wÀ-ſ]+)", text)
        if m:
            out["farmer"], out["village"] = m.group(1), m.group(2)
    if not out["farmer"]:
        m = re.search(r"^([\wÀ-ſ]+) aqui,\s*([\wÀ-ſ]+)", text)
        if m:
            out["farmer"], out["village"] = m.group(1), m.group(2)
    if not out["farmer"]:
        m = re.search(r"^([\wÀ-ſ ]+?),\s*([\wÀ-ſ]+)[.,]", text)
        if m:
            out["farmer"], out["village"] = m.group(1).strip(), m.group(2)
    out["language"] = ("hi" if re.search(r"[ऀ-ॿ]", text) or re.search(
        r"\b(hai|hoon|se|mein|kal)\b", low) else "pt" if re.search(
        r"\b(aqui|tenho|preciso|pode|oi|bom dia|quanto)\b", low) else "en")
    return out


def _brief(system: str, user: str) -> str:
    from .desk import LANGS, template_brief
    lang = next((k for k, v in LANGS.items() if v in system), "en")
    facts = json.loads(user)
    plan = {"metrics": facts["metrics"],
            "plan": [{**r, "sorties": [{"start": s} for s in r.get("sorties", [])]}
                     for r in facts.get("plan", [])],
            "batteries": facts.get("batteries", []), "deferred": facts.get("call_for_another_day", [])}
    return template_brief(plan, "hi" if lang == "hi" else "en")


def _grounding(user: str) -> str:
    m = re.search(r"Camera finding: (.*?) \((\w+)\), severity (\d)", user)
    cat, sev = (m.group(2), int(m.group(3))) if m else ("none", 0)
    action = "spot_spray" if cat in ("disease", "pest") and sev >= 1 else \
        "foliar_n" if cat == "nutrient" else "irrigate" if cat == "water" else "no_action"
    return json.dumps({"action": action, "fly_drone": action in ("spot_spray", "foliar_n"),
                       "expected_yield_t_ha": 25.0, "yield_confidence": 0.5,
                       "rationale": "rehearsal stand-in: act on any visible finding"})


def _vision(prompt: str, content: list) -> str:
    from .perception import pixel_metrics, stub_raw
    imgs = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = part["image_url"]["url"]
            imgs.append(base64.b64decode(url.split(",", 1)[1]))
    px = [pixel_metrics(b) for b in imgs]
    if "green_fraction" in prompt:
        cover = sum(p["canopy_cover_exg_pct"] for p in px) / max(1, len(px))
        return json.dumps({"plant_visible": cover > 5, "green_fraction": round(cover / 100, 3),
                           "visible_problem": "none"})
    zone = (re.search(r"Photos of zone (\w+)", prompt) or [None, "?"])[1]
    crop = (re.search(r"Expected crop: (\w+)", prompt) or [None, "unknown"])[1]
    raw = stub_raw(zone, px, crop)
    raw["notes"] = LABEL + ": pixel heuristics answered for the vision model"
    return json.dumps(raw)


def _agent(body: dict) -> dict:
    """Native tool call from FieldPilot's deterministic policy (or the doctor's probe)."""
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    if "get_zone_status" in tools:
        user = next((m["content"] for m in body["messages"] if m["role"] == "user"), "")
        z = (re.search(r"zone (\w+)", user) or [None, "B2"])[1]
        return {"tool_calls": [("get_zone_status", {"zone_id": z})]}
    if _state_ref is None:
        return {"content": "No run is bound to the rehearsal stand-in."}
    from .agent import next_policy_action
    call = next_policy_action(_state_ref())
    return {"tool_calls": [(call.name, call.arguments)],
            "content": f"<think>policy: next step is {call.name}</think>"}


def answer(body: dict) -> dict:
    msgs = body.get("messages") or []
    system = next((m["content"] for m in msgs if m["role"] == "system"), "") or ""
    user_msg = next((m for m in reversed(msgs) if m["role"] == "user"), {"content": ""})
    user = user_msg.get("content") or ""
    if isinstance(user, list):
        prompt = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
        return {"content": _vision(prompt, user)}
    if body.get("tools"):
        return _agent(body)
    if "Available tools:" in system:                      # JSON-protocol fallback
        fns = json.loads(system.split("Available tools:\n", 1)[1])
        r = _agent({**body, "tools": [{"function": f} for f in fns]})
        if r.get("tool_calls"):
            n, a = r["tool_calls"][0]
            return {"content": json.dumps({"tool": n, "arguments": a})}
        return r
    from .intake import SYSTEM as INTAKE
    if system == INTAKE:
        return {"content": json.dumps(_intake(user), ensure_ascii=False)}
    if system.startswith("You write the morning voice-note brief"):
        return {"content": _brief(system, user)}
    if system.startswith("You are an agronomy adviser"):
        return {"content": _grounding(user)}
    if "Major uses of pesticides" in user:
        return {"content": json.dumps({"found": False})}
    return {"content": "OK"}


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class StandIn:
    """OpenAI-compatible stand-in for Token Factory on 127.0.0.1, in a background thread."""

    def __init__(self, port: int | None = None, latency_s: float = 0.0):
        import uvicorn
        from fastapi import FastAPI
        self.port = port or _free_port()
        self.latency_s = latency_s
        app = FastAPI()

        @app.get("/v1/models")
        def models():
            return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "rehearsal"}
                                               for m in (AGENT_ID, VISION_ID, FAST_ID)]}

        @app.post("/v1/chat/completions")
        async def chat(req: Request):
            body = await req.json()
            t0 = time.time()
            if self.latency_s:
                time.sleep(self.latency_s)
            r = answer(body)
            rf = body.get("response_format") or {}
            if rf.get("type") == "json_schema" and r.get("content"):
                r["content"] = _conform(r["content"], (rf.get("json_schema") or {}).get("schema") or {})
            msg = {"role": "assistant", "content": r.get("content")}
            if r.get("tool_calls"):
                msg["tool_calls"] = [{"id": f"call_{int(t0 * 1e6) % 10**8}_{i}", "type": "function",
                                      "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                                     for i, (n, a) in enumerate(r["tool_calls"])]
            pin = sum(_tokens(m.get("content") or "") for m in body.get("messages") or []) + \
                (_tokens(body.get("tools")) if body.get("tools") else 0)
            pout = _tokens(json.dumps(msg, ensure_ascii=False))
            with _lock:
                _ledger.append({"t": round(t0, 3), "model": body.get("model"), "prompt_tokens": pin,
                                "completion_tokens": pout, "tools": bool(body.get("tools")),
                                "schema": rf.get("type") == "json_schema",
                                "kind": ("agent" if body.get("tools") else "vision" if isinstance(
                                    (body.get("messages") or [{}])[-1].get("content"), list) else "text")})
            return {"id": "rehearsal", "object": "chat.completion", "created": int(t0),
                    "model": body.get("model"),
                    "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "tool_calls" if r.get("tool_calls") else "stop"}],
                    "usage": {"prompt_tokens": pin, "completion_tokens": pout,
                              "total_tokens": pin + pout}}

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/"

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def environ(url: str, out: Path) -> dict:
    """Environment that points FieldPilot at the stand-in and keeps outputs apart."""
    return {"NEBIUS_API_KEY": "rehearsal-not-a-key", "NEBIUS_BASE_URL": url,
            "FP_REHEARSAL": "1", "FP_RUNS_DIR": str(out.relative_to(ROOT)).replace("\\", "/"),
            "FP_RESULTS_DIR": str((ROOT / "eval" / "results" / "rehearsal").relative_to(ROOT)).replace("\\", "/"),
            "FP_AGENT_MODEL": "", "FP_VISION_MODEL": "", "FP_VISION_BASE_URL": ""}


from .paths import ROOT


def _conform(content: str, schema: dict) -> str:
    """What response_format json_schema guarantees: exactly the schema's keys, as JSON."""
    try:
        obj = json.loads(content)
    except (TypeError, ValueError):
        return content
    props = schema.get("properties") or {}
    if isinstance(obj, dict) and props:
        obj = {k: obj.get(k) for k in props}
    return json.dumps(obj, ensure_ascii=False)


def cost(rows: list[dict]) -> dict:
    """Forecast Token Factory spend: each call at its own model's list price."""
    from .llm import price_of
    pin = sum(r["prompt_tokens"] for r in rows)
    pout = sum(r["completion_tokens"] for r in rows)
    usd, by = 0.0, {}
    for r in rows:
        m = (r.get("model") or "?").split("/")[-1]
        a, b = price_of(m)
        usd += r["prompt_tokens"] / 1e6 * a + r["completion_tokens"] / 1e6 * b
        by[m] = by.get(m, 0) + 1
    return {"calls": len(rows), "prompt_tokens": pin, "completion_tokens": pout,
            "usd_at_token_factory_price": round(usd, 4), "calls_by_model": by,
            "schema_constrained_calls": sum(1 for r in rows if r.get("schema")),
            "note": "token counts approximated at 4 characters per token, images at 1 000 tokens; "
                    "each call priced at its model's Token Factory list price"}


# ---------------------------------------------------------------------------
# The full rehearsal
# ---------------------------------------------------------------------------

class InProcessSource:
    """Bench source reading the console simulator in this process (no HTTP)."""

    def __init__(self, drone, on_pwm: int = 1900, off_pwm: int = 1100):
        self.drone, self.on_pwm, self.off_pwm = drone, on_pwm, off_pwm
        self.speed: float | None = None
        self._zone: str | None = None
        self.last_t = 0.0
        self.samples: list[tuple[float, bool, str | None, float]] = []

    def read(self, timeout_s: float = 0.05):
        time.sleep(timeout_s)
        t = self.drone.telemetry()
        self.speed, self._zone, self.last_t = t.groundspeed_ms, t.zone_id or None, time.time()
        self.samples.append((self.last_t, t.spraying, self._zone, t.groundspeed_ms))
        return self.on_pwm if t.spraying else self.off_pwm

    def zone(self):
        return self._zone


def _step(report: list, name: str, fn):
    t0 = time.time()
    try:
        info = fn() or {}
        report.append({"step": name, "ok": True, "seconds": round(time.time() - t0, 1)} | info)
    except SystemExit as e:
        report.append({"step": name, "ok": e.code in (0, None), "seconds": round(time.time() - t0, 1),
                       "detail": f"exit {e.code}"})
    except Exception as e:  # noqa: BLE001
        report.append({"step": name, "ok": False, "seconds": round(time.time() - t0, 1),
                       "detail": f"{type(e).__name__}: {e}"})
    print(f"[{'ok  ' if report[-1]['ok'] else 'FAIL'}] {name} ({report[-1]['seconds']} s)"
          + (f" - {report[-1].get('detail')}" if report[-1].get('detail') else ""))


def _bench_prediction(fp, run_id: str, out: Path) -> dict:
    """Approve the demo mission, fly it in the simulator, drive the emulated T1 pump."""
    from .bench import BenchLog, Bridge, SerialSink
    from .emulator import PumpBridgeEmulator, bounds
    from .k3card import card_html, flight_card, mission_kml
    s = fp.runs[run_id]
    m = s.mission
    if m is None:
        return {"detail": "no mission proposed (nothing to treat)"}
    (out / f"card_{m.mission_id}.html").write_text(
        card_html(flight_card(fp.field, m, fp.spec, "rehearsal")), encoding="utf-8")
    (out / f"card_{m.mission_id}.kml").write_text(mission_kml(fp.field, m), encoding="utf-8")
    emu = PumpBridgeEmulator()
    sink = SerialSink("emulated", ser=emu)
    src = InProcessSource(fp.drone)
    log = BenchLog("rehearsal-sim", "emu", 9, 1500)
    br = Bridge(src, sink, log, echo=lambda *_: None, flow_law=fp.spec)
    r = fp.approve(run_id, operator="rehearsal",
                   override_reason="Rehearsal: water-only bench prediction, SOP paperwork not on record")
    if not r.get("dispatched"):
        return {"detail": "approval refused: " + str(r.get("reason")), "approval": r}
    th = threading.Thread(target=br.run, daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < 900:
        if fp.drone.telemetry().state in ("landed", "aborted", "error"):
            break
        time.sleep(0.5)
    br.stop.set()
    th.join(timeout=5)
    emu.finish()
    # litres per zone: each emulator step is assigned to the zone the aircraft was spraying in
    zones: dict[str, float] = {}
    smp = src.samples
    j = 0
    prev_v = 0.0
    for t, _d, _q, v in emu.trace:
        while j + 1 < len(smp) and smp[j + 1][0] <= t:
            j += 1
        z = smp[j][2] if smp else None
        if z:
            zones[z] = zones.get(z, 0.0) + (v - prev_v)
        prev_v = v
    from shapely.geometry import Polygon
    planned = {z: round(Polygon([fp.field.frame.to_local(a, b) for a, b in fp.field.zone(z).polygon]).area
                        * fp.spec.spray_rate_l_ha / 10_000, 3) for z in m.zones}
    cmds = [(round(tc - emu.cmds[0][0], 3), int(1100 + 800 * d)) for tc, d in emu.cmds]
    bnd = bounds(cmds, end_s=emu.t - emu.cmds[0][0])
    return {"mission_id": m.mission_id, "zones": m.zones, "sorties": len(m.sorties),
            "planned_litres_per_zone": planned,
            "emulated_litres_per_zone": {z: round(v, 3) for z, v in zones.items()},
            "emulated_pump": emu.summary(), "litres_bounds_over_param_ranges": bnd,
            "bench_summary": log.summary(), "flight_s": round(time.time() - t0, 1),
            "events": [e.__dict__ for e in log.events]}


def run_all(quick: bool = False) -> int:
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runs" / "rehearsal"
    out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    pred: dict = {"label": LABEL, "utc": stamp}
    with StandIn() as srv:
        os.environ.update(environ(srv.url, out))
        os.environ["FP_DRONE"] = "sim"
        print(f"{LABEL}\nstand-in Token Factory at {srv.url}\n")
        from .config import load

        def models():
            from .llm import TokenFactory
            tf = TokenFactory(load())
            return {"catalog": tf.catalog(), "picked": tf.resolve()}
        _step(report, "models (catalog + pick)", models)

        def doctor():
            from .doctor import main as dmain
            code = dmain(False)
            return {"detail": f"doctor exit {code} (see runs/rehearsal/doctor_*.json)"}
        _step(report, "doctor", doctor)

        state = {}

        def field_run():
            from .cli import _load_images
            from .service import FieldPilot
            fp = FieldPilot(sim_time_scale=1.0)
            imgs = _load_images(ROOT / "examples" / "images", fp.field.zone_ids())
            s = fp.new_run(imgs)
            state.update(fp=fp, run=s.run_id)
            tools = [e.get("name") for e in getattr(s, "trace", [])
                     if isinstance(e, dict) and e.get("type") == "tool_call"]
            pred["field_run"] = {"run_id": s.run_id, "proposal": s.proposal, "tool_sequence": tools,
                                 "models": getattr(s, "models", None)}
            return {"detail": (s.proposal or {}).get("headline")}
        _step(report, "field run (Nemotron agent via stand-in)", field_run)

        def bench():
            if "fp" not in state:
                raise RuntimeError("field run did not complete")
            b = _bench_prediction(state["fp"], state["run"], out)
            pred["bench"] = b
            return {"detail": f"emulated {b.get('emulated_pump', {}).get('litres')} L"
                    if b.get("emulated_pump") else b.get("detail")}
        _step(report, "approve + fly (sim) + emulated T1 pump", bench)

        for key in ("in", "br"):
            def day(key=key):
                from .desk import main as dmain
                before = set(out.glob("day_*.json"))
                code = dmain(str(ROOT / "examples" / f"desk_demo_{key}.json"))
                new = sorted(set(out.glob("day_*.json")) - before)
                if new:
                    d = json.loads(new[-1].read_text(encoding="utf-8"))
                    pred[f"day_{key}"] = {"file": new[-1].name, "fieldpilot": d["fieldpilot"]["metrics"],
                                          "baseline": d["baseline"]["metrics"], "brief": d.get("brief"),
                                          "callback": d.get("callback")}
                return {"detail": f"desk exit {code}"}
            _step(report, f"operator desk day ({key.upper()})", day)

        def grounding():
            sys_argv = ["--ensemble", "2,1"] if quick else []
            import importlib.util
            spec = importlib.util.spec_from_file_location("grounding_eval", ROOT / "eval" / "grounding_eval.py")
            mod = importlib.util.module_from_spec(spec)
            import sys as _sys
            _sys.modules["grounding_eval"] = mod
            spec.loader.exec_module(mod)
            code = mod.main(sys_argv)
            files = sorted((ROOT / "eval" / "results" / "rehearsal").glob("grounding_*.json"))
            if files:
                pred["grounding"] = json.loads(files[-1].read_text(encoding="utf-8"))["summary"]
            return {"detail": f"exit {code}"}
        _step(report, "grounding eval (stand-in as 'Nemotron alone')", grounding)

        def charts():
            import importlib.util
            spec = importlib.util.spec_from_file_location("make_charts", ROOT / "eval" / "make_charts.py")
            mod = importlib.util.module_from_spec(spec)
            import sys as _sys
            _sys.modules["make_charts"] = mod
            spec.loader.exec_module(mod)
            return {"detail": f"exit {mod.main([])}"}
        _step(report, "charts", charts)

    pred["token_use_forecast"] = cost(ledger())
    pred["steps"] = report
    (out / f"rehearsal_{stamp}.json").write_text(json.dumps(pred, indent=1, ensure_ascii=False, default=str),
                                                 encoding="utf-8")
    md = _report_md(pred)
    (out / "REHEARSAL_REPORT.md").write_text(md, encoding="utf-8")
    print(f"\n{sum(r['ok'] for r in report)}/{len(report)} steps ok -> runs/rehearsal/REHEARSAL_REPORT.md")
    return 0 if all(r["ok"] for r in report) else 1


def _report_md(p: dict) -> str:
    L = [f"# Rehearsal report · {p['utc']}", "", f"**{LABEL}.** Every number below is a prediction "
         "made by FieldPilot's own code against a local stand-in for Token Factory and an emulated "
         "pump. None of it is a Nemotron answer or a hardware measurement; it is the baseline the "
         "real runs are compared with (`python -m fieldpilot.cli deviation`).", "",
         "## Steps", "", "| Step | Result | Time | Detail |", "|---|---|---|---|"]
    for r in p["steps"]:
        L.append(f"| {r['step']} | {'ok' if r['ok'] else 'FAIL'} | {r['seconds']} s | {r.get('detail', '')} |")
    fr = p.get("field_run") or {}
    if fr.get("proposal"):
        L += ["", "## Field run (predicted agent behaviour)", "",
              f"- Proposal: {fr['proposal'].get('headline')}",
              f"- Tool sequence: {' → '.join(fr.get('tool_sequence') or []) or 'see runs/rehearsal/' + fr['run_id'] + '.json'}"]
    b = p.get("bench") or {}
    if b.get("emulated_pump"):
        bd = b["litres_bounds_over_param_ranges"]
        L += ["", "## Bench prediction (T1 pump, emulated)", "",
              f"- Mission {b['mission_id']}: zones {', '.join(b['zones'])}, {b['sorties']} sortie(s), "
              f"flown in {b['flight_s']} s",
              f"- Planned dose: {b['planned_litres_per_zone']} L per zone (20 L/ha)",
              f"- Emulated delivery: {b['emulated_litres_per_zone']} L per zone; total "
              f"{b['emulated_pump']['litres']} L (range {bd['litres_min']}–{bd['litres_max']} L over the "
              "stated pump uncertainties)",
              f"- Pump flowing {b['emulated_pump']['seconds_flowing']} s, peak "
              f"{b['emulated_pump']['peak_l_min']} L/min, nozzles at "
              f"{b['emulated_pump']['nozzle_mpa_at_full']} MPa at full flow",
              f"- Spray events {b['bench_summary']['pump_on_events']}, zones {b['bench_summary']['zones']}",
              "", "Measure on the real bench: litres from the tank (mark before/after), seconds of "
              "spray, and a jug at full duty for 30 s. `cli deviation` compares them with this."]
    for key in ("in", "br"):
        d = p.get(f"day_{key}")
        if d:
            f, bl = d["fieldpilot"], d["baseline"]
            L += ["", f"## Operator desk day · {key.upper()}", "",
                  f"- FieldPilot: {f.get('farms_served_in_window')} farms in window, "
                  f"{f.get('effective_ha')} effective ha, {f.get('battery_cycles')} battery cycles, "
                  f"{f.get('out_of_window_sorties')} sorties out of window",
                  f"- Baseline: {bl.get('farms_served_in_window')} farms in window, {bl.get('effective_ha')} "
                  f"effective ha, {bl.get('out_of_window_sorties')} sorties out of window",
                  f"- Brief by: {(d.get('brief') or {}).get('by')}"]
    t = p.get("token_use_forecast") or {}
    if t:
        L += ["", "## Token Factory use forecast", "",
              f"- {t['calls']} calls, ~{t['prompt_tokens']:,} prompt + ~{t['completion_tokens']:,} completion "
              f"tokens, about **${t['usd_at_token_factory_price']}**, each call at its model's list price. "
              "Real Nemotron answers are longer (reasoning), so expect 2-5x.",
              "- Calls by model: " + ", ".join(f"{k} {v}" for k, v in (t.get('calls_by_model') or {}).items())
              + f"; schema-constrained: {t.get('schema_constrained_calls', 0)}",
              f"- {t['note']}"]
    L += ["", "## Not covered by a rehearsal", "",
          "- Nemotron's own quality (reading bookings, tool choices, the brief): only a live run shows it.",
          "- The real pump's stall duty, lag and flow: measure them on the bench.",
          "- Flight on the T1 with the K3-A Pro: the pilot flies the card; record the log in the JIYI app."]
    return "\n".join(L) + "\n"
