"""One command that says what works, what is stubbed and what is missing.

    python -m fieldpilot.cli doctor               # live checks: Nebius, Tavily, weather, twin, drone
    python -m fieldpilot.cli doctor --submission  # is the repo ready to submit on Devpost?

Every check prints PASS / WARN / FAIL / SKIP with a one-line reason, and the
whole report (including latencies and raw error text) is written to
runs/doctor_<timestamp>.json. That file is the thing to share when something
breaks, and its latencies and errors feed the "feedback on Nebius/NVIDIA tools"
field of the Devpost form.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .config import ROOT, Config, load

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, status: str, detail: str, **extra) -> None:
        self.rows.append({"check": name, "status": status, "detail": detail, **extra})
        print(f"  {status:4}  {name:<26} {detail}")

    def run(self, name: str, fn: Callable[[], tuple[str, str, dict]]) -> None:
        t0 = time.time()
        try:
            status, detail, extra = fn()
        except Exception as e:  # noqa: BLE001
            status, detail = FAIL, f"{type(e).__name__}: {str(e)[:220]}"
            extra = {"traceback": traceback.format_exc()[-1500:]}
        self.add(name, status, detail, seconds=round(time.time() - t0, 2), **extra)

    def counts(self) -> dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for r in self.rows:
            out[r["status"]] += 1
        return out


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------

def _deps() -> tuple[str, str, dict]:
    need = ["openai", "pcse", "numpy", "scipy", "PIL", "shapely", "fastapi", "httpx", "pymavlink"]
    optional = ["tavily"]
    missing = [m for m in need if importlib.util.find_spec(m) is None]
    miss_opt = [m for m in optional if importlib.util.find_spec(m) is None]
    if missing:
        return FAIL, "missing: " + ", ".join(missing) + " -> pip install -r requirements.txt", {}
    if miss_opt:
        return WARN, "optional missing: " + ", ".join(miss_opt), {}
    return PASS, "all required packages import", {}


def _client(cfg: Config):
    from .llm import TokenFactory
    tf = TokenFactory(cfg)
    tf.client = tf.client.with_options(timeout=60.0, max_retries=0)
    if tf.vision_client is not tf.client and cfg.vision_base_url:
        tf.vision_client = tf.vision_client.with_options(timeout=120.0, max_retries=0)
    else:
        tf.vision_client = tf.client
    return tf


def check_catalog(tf) -> tuple[str, str, dict]:
    from .llm import is_nvidia
    ids = tf.catalog()
    if not ids:
        return FAIL, "catalog empty or unreachable (key, base URL or network)", {}
    picks = tf.resolve()
    nv = [i for i in ids if is_nvidia(i)]
    extra = {"catalog_size": len(ids), "nvidia_models": nv, "picked": picks}
    if picks["agent"] not in ids:
        return FAIL, f"agent {picks['agent']} is not in your catalog (NVIDIA models: {nv or 'none'})", extra
    if not is_nvidia(picks["agent"] or ""):
        return FAIL, f"agent is not an NVIDIA model: {picks['agent']}", extra
    if not picks["vision"]:
        return WARN, (f"agent {picks['agent']}; no vision model in catalog -> "
                      "nebius/deploy_perception_endpoint.sh"), extra
    return PASS, f"agent {picks['agent']} | vision {picks['vision']}", extra


PROBE_TOOL = [{"type": "function", "function": {
    "name": "get_zone_status", "description": "Return the status of one field zone.",
    "parameters": {"type": "object", "properties": {"zone_id": {"type": "string"}},
                   "required": ["zone_id"]}}}]


def check_tool_calling(tf) -> tuple[str, str, dict]:
    msgs = [{"role": "system", "content": "You are a field agent. Use tools; do not guess."},
            {"role": "user", "content": "What is the status of zone B2? Use the tool."}]
    t0 = time.time()
    st = tf.step(msgs, PROBE_TOOL, max_tokens=512)
    dt = round(time.time() - t0, 2)
    calls = [(c.name, c.arguments) for c in st.tool_calls]
    extra = {"protocol": st.protocol, "latency_s": dt, "calls": calls, "usage": st.usage,
             "reasoning_chars": len(st.reasoning or "")}
    if not calls:
        return FAIL, f"no tool call in reply ({st.protocol}); content: {st.content[:120]!r}", extra
    name, args = calls[0]
    if name != "get_zone_status" or str(args.get("zone_id", "")).upper() != "B2":
        return WARN, f"unexpected call {name}({args})", extra
    status = PASS if st.protocol == "native" else WARN
    return status, f"{st.protocol} tool call in {dt}s -> {name}({args})", extra


def check_vision(tf) -> tuple[str, str, dict]:
    imgs = sorted((ROOT / "examples" / "images").glob("*.jpg"))
    if not imgs:
        subprocess.run([sys.executable, str(ROOT / "examples" / "make_demo_images.py")], check=True)
        imgs = sorted((ROOT / "examples" / "images").glob("*.jpg"))
    real = sorted((ROOT / "photos").glob("*.jpg")) if (ROOT / "photos").exists() else []
    img = (real or imgs)[0]
    prompt = ('Look at this crop photo. Reply with ONLY JSON: {"plant_visible": true/false, '
              '"green_fraction": 0-1, "visible_problem": "<short text or none>"}')
    t0 = time.time()
    obj, meta = tf.vision_json(prompt, [img.read_bytes()], max_tokens=3000)
    dt = round(time.time() - t0, 2)
    extra = {"image": str(img.relative_to(ROOT)), "latency_s": dt, "reply": obj,
             "model": meta.get("model")}
    if not isinstance(obj, dict) or "green_fraction" not in obj:
        return WARN, f"model answered but not in the expected JSON ({dt}s)", extra
    return PASS, f"{meta.get('model')} parsed JSON in {dt}s ({img.name})", extra


def check_tavily(cfg: Config) -> tuple[str, str, dict]:
    from .context import search_local
    if not cfg.live_search:
        return SKIP, "TAVILY_API_KEY not set (advisories run offline)", {}
    t0 = time.time()
    r = search_local("potato late blight management India advisory", cfg.tavily_api_key, 3)
    dt = round(time.time() - t0, 2)
    extra = {"latency_s": dt, "source": r["source"], "urls": [x["url"] for x in r["results"]]}
    if r["source"] != "tavily" or not r["results"]:
        return FAIL, f"search returned {r['source']}: {r.get('note', '')}", extra
    return PASS, f"{len(r['results'])} results in {dt}s", extra


def check_weather(field) -> tuple[str, str, dict]:
    from .context import flight_conditions
    lat, lon = field.home
    r = flight_conditions(lat, lon, live=True)
    extra = {"source": r["source"], "now": r["now"], "go": r["go"], "reasons": r["reasons"]}
    if r["source"].startswith("OFFLINE"):
        return FAIL, "Open-Meteo unreachable; the flight check would use synthetic weather", extra
    n = r["now"]
    return PASS, (f"wind {n['wind_ms']} m/s, gust {n['gust_ms']} m/s, rain {n['rain_prob_2h']}% "
                  f"-> {'GO' if r['go'] else 'NO-GO: ' + '; '.join(r['reasons'])}"), extra


def check_twin(field) -> tuple[str, str, dict]:
    from . import twin
    lat, lon = field.home
    today = date.today()
    base = twin.base_weather(lat, lon, today, live=True)
    src = "; ".join(getattr(base, "description", [])) or "?"
    t0 = time.time()
    ev = twin.evaluate("potato", "Potato_701", lat, lon, today, 31, "disease", 2,
                       n_weather=2, n_params=2, base=base)
    dt = round(time.time() - t0, 2)
    extra = {"weather": src, "recommended": ev.recommended, "latency_s": dt}
    status = WARN if "OFFLINE" in src.upper() or "SYNTHETIC" in src.upper() else PASS
    return status, f"potato test zone -> {ev.recommended} in {dt}s (weather: {src[:60]})", extra


def check_drone(cfg: Config) -> tuple[str, str, dict]:
    if cfg.drone != "mavlink":
        return SKIP, "FP_DRONE=sim (set FP_DRONE=mavlink + FP_MAVLINK_URL to test the vehicle)", {}
    from .drone.mavlink import MavlinkBackend
    d = MavlinkBackend(url=cfg.mavlink_url, heartbeat_timeout=10.0)
    try:
        d.connect()
        time.sleep(3.0)                       # let telemetry streams arrive
        pf = d.preflight()
    finally:
        d.close()
    extra = {"autopilot": d.autopilot, "preflight": pf}
    if not pf["ok"]:
        return WARN, f"{d.autopilot} connected; preflight: {'; '.join(pf['issues'])}", extra
    return PASS, f"{d.autopilot} connected, GPS fix {pf['gps_fix']}, battery {pf['battery_pct']:.0f}%", extra


def check_calibration() -> tuple[str, str, dict]:
    from .perception import ConformalGate
    p = ROOT / "calibration.json"
    if not p.exists():
        return WARN, "no calibration.json: gate runs uncalibrated (p >= 0.85 fallback)", {}
    g = ConformalGate.load(p)
    if not g.calibrated:
        return WARN, f"calibration has n={g.n} (< 20): still uncalibrated", {"n": g.n}
    return PASS, f"n={g.n}, alpha={g.alpha}, qhat={g.qhat}", {"n": g.n}


def live(cfg: Config | None = None) -> Report:
    from .field import Field
    cfg = cfg or load()
    rep = Report()
    print(f"FieldPilot doctor | mode {cfg.mode()}")
    rep.run("python packages", _deps)
    fld = Field.load(cfg.field_path)
    if cfg.live_llm and not cfg.on_nebius:
        rep.add("nebius", WARN, f"agent endpoint is {cfg.nebius_base_url} -- a LOCAL server, not Nebius. "
                "Fine for development; runs made this way do not count for the hackathon")
    if cfg.live_llm:
        tf = _client(cfg)
        rep.run("token factory catalog", lambda: check_catalog(tf))
        rep.run("nemotron tool calling", lambda: check_tool_calling(tf))
        if tf.vision_model:
            rep.run("vision model", lambda: check_vision(tf))
        else:
            rep.add("vision model", FAIL, "no vision model resolved")
    else:
        rep.add("token factory", FAIL, "NEBIUS_API_KEY not set (copy .env.example to .env)")
    rep.run("tavily search", lambda: check_tavily(cfg))
    rep.run("open-meteo weather", lambda: check_weather(fld))
    rep.run("crop twin (PCSE)", lambda: check_twin(fld))
    rep.run("conformal calibration", check_calibration)
    rep.run("drone link", lambda: check_drone(cfg))
    return rep


# ---------------------------------------------------------------------------
# Submission readiness
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def submission() -> Report:
    rep = Report()
    print("FieldPilot submission check")

    def lic():
        t = (ROOT / "LICENSE").read_text(errors="ignore", encoding="utf-8") if (ROOT / "LICENSE").exists() else ""
        return (PASS, "Apache-2.0 LICENSE present", {}) if "Apache License" in t else \
            (FAIL, "LICENSE missing or not an open-source licence", {})
    rep.run("open-source licence", lic)

    def readme():
        t = (ROOT / "README.md").read_text(errors="ignore", encoding="utf-8")
        need = ["pip install", "NEBIUS_API_KEY", "demo"]
        miss = [n for n in need if n not in t]
        return (FAIL, "README lacks: " + ", ".join(miss), {}) if miss else \
            (PASS, "README has install, key and demo instructions", {})
    rep.run("README setup", readme)

    def remote():
        url = _git("remote", "get-url", "origin")
        if not url:
            return FAIL, "no git remote: push to a PUBLIC GitHub repo", {}
        dirty = _git("status", "--porcelain")
        return (WARN if dirty else PASS), url + (" (uncommitted changes)" if dirty else ""), {"url": url}
    rep.run("public repository", remote)

    def dates():
        first = _git("log", "--reverse", "--format=%cs")
        first = first.splitlines()[0] if first else ""
        return (PASS if first >= "2026-08-26" else WARN,
                f"first commit {first or '?'} (submission period starts 2026-08-26)", {})
    rep.run("built in the period", dates)

    def evalres():
        files = sorted((ROOT / "eval" / "results").glob("grounding_*.json"))
        if not files:
            return FAIL, "no eval results: python3 eval/grounding_eval.py", {}
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        mode = (d["summary"].get("mode") or {}).get("llm")
        if mode and mode != "nebius-token-factory":
            return FAIL, f"{files[-1].name} is not a Nebius run ({mode})", {}
        if "SYNTH" in str(d["summary"].get("weather", "")).upper():
            return FAIL, (f"{files[-1].name} ran on SYNTHETIC weather (Open-Meteo was unreachable): "
                          "run eval/grounding_eval.py again"), {}
        na = d["summary"].get("nemotron_alone")
        if not isinstance(na, dict):
            return FAIL, f"{files[-1].name}: Nemotron baseline not run (needs NEBIUS_API_KEY)", {}
        return PASS, (f"{files[-1].name}: n={d['summary']['n']}, Nemotron alone wasted "
                      f"{na['wasted_flights']}, unsafe {na['unsafe_flights']}"), {}
    rep.run("evaluation numbers", evalres)

    def chart():
        pngs = sorted((ROOT / "eval" / "results").glob("grounding_*.png"))
        return (PASS, pngs[-1].name, {}) if pngs else \
            (WARN, "no chart: python3 eval/make_charts.py", {})
    rep.run("evaluation chart", chart)

    rep.run("conformal calibration", check_calibration)

    def subtext():
        t = (ROOT / "docs" / "SUBMISSION.md").read_text(errors="ignore", encoding="utf-8")
        holes = sorted(set(re.findall(r"(?<!`)\[(?:N|K|[A-Z_]{2,})\](?!`)|TODO|TBD|youtu\.be/XXXX", t)))
        return (WARN, "placeholders left in docs/SUBMISSION.md: " + ", ".join(holes), {}) if holes \
            else (PASS, "no placeholders", {})
    rep.run("devpost text", subtext)

    def runs():
        live_runs = []
        for p in sorted((ROOT / "runs").glob("*.json")) if (ROOT / "runs").exists() else []:
            if p.name.startswith("doctor_"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if d.get("mode", {}).get("llm") == "nebius-token-factory":
                live_runs.append((p.name, (d.get("models") or {}).get("agent")))
        if not live_runs:
            return FAIL, "no live run in runs/: python3 -m fieldpilot.cli run photos/", {}
        from .llm import is_nvidia
        # Hackathon rule: at least one NVIDIA open model, called at runtime on Token Factory.
        nv = [(n, m) for n, m in live_runs if m is None or is_nvidia(m)]
        named = [(n, m) for n, m in nv if m]
        if not nv:
            return FAIL, ("live runs used no NVIDIA model: "
                          + ", ".join(sorted({m for _, m in live_runs if m}))), {}
        if named:
            return PASS, (f"{len(named)} live run(s) on NVIDIA {named[-1][1]} via Token Factory, "
                          f"latest {named[-1][0]}"), {}
        return PASS, f"{len(nv)} live Nemotron run(s), latest {nv[-1][0]}", {}
    rep.run("live agent run", runs)

    def fb():
        p = ROOT / "docs" / "FEEDBACK.md"
        if not p.exists():
            return WARN, "docs/FEEDBACK.md missing", {}
        t = p.read_text(errors="ignore", encoding="utf-8")
        return (WARN, "docs/FEEDBACK.md still has blanks (____)", {}) if "____" in t else \
            (PASS, "feedback written", {})
    rep.run("tool feedback", fb)
    return rep


def main(submission_mode: bool = False) -> int:
    rep = submission() if submission_mode else live()
    c = rep.counts()
    cfg = load()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = cfg.runs_dir / f"doctor_{'submission_' if submission_mode else ''}{ts}.json"
    out.write_text(json.dumps({"generated_utc": ts, "mode": cfg.mode(), "counts": c,
                               "checks": rep.rows}, indent=1, default=str), encoding="utf-8")
    print(f"\n{c[PASS]} pass, {c[WARN]} warn, {c[FAIL]} fail, {c[SKIP]} skip -> {out}")
    return 1 if c[FAIL] else 0
