"""Simulation lab: every FieldPilot simulation, runnable from the app (and the exe), no terminal.

Each simulation is the same code the command line runs (``python -m fieldpilot.cli ...``).
A run happens in a background thread, one at a time; its console output is captured and
shown on the /sims page together with the report it wrote. Everything here is SIMULATED or
REHEARSAL and says so; none of it can fill docs/SUBMISSION.md.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .paths import ROOT

_lock = threading.Lock()
JOBS: dict[str, dict] = {}


def _opt(key, label, kind="number", default=None, choices=None, help=""):
    return {"key": key, "label": label, "type": kind, "default": default, "choices": choices, "help": help}


def _has_key() -> bool:
    try:
        from .config import load
        return bool(load().nebius_api_key)
    except Exception:
        return False


def _demos() -> list[str]:
    return sorted(p.stem.removeprefix("desk_demo_") for p in (ROOT / "examples").glob("desk_demo_*.json")) or ["in"]


def catalog() -> list[dict]:
    return [
        {"id": "fieldday", "title": "Simulated field day", "tag": "SIMULATED",
         "desc": "A whole operator day on the production planner: bookings read, day planned on the live "
                 "forecast, each sortie flown by the K3-A Pro twin, FieldMast and SprayLog posting from the "
                 "simulated air. Opens in /ops → Field day.",
         "options": [_opt("seed", "Seed", "number", 7), _opt("demo", "Bookings", "select", "in", _demos()),
                     _opt("date", "Date (blank = tomorrow)", "text", ""),
                     _opt("synthetic", "Synthetic weather (no network)", "bool", False),
                     _opt("nodrift", "Drift compensation off", "bool", False),
                     _opt("live", "Live Nemotron for intake and brief (needs NEBIUS_API_KEY)", "bool", False)],
         "view": "/ops#day"},
        {"id": "rehearse", "title": "Rehearsal: whole pipeline, mock Token Factory", "tag": "REHEARSAL",
         "desc": "Planner, rules, crop twin, label parser, paperwork, an emulated T1 pump and both operator days, "
                 "against a local stand-in for Token Factory. Predicts the real run; `deviation` compares.",
         "options": [_opt("quick", "Quick (skip the slow steps)", "bool", True)],
         "report": "runs/rehearsal/REHEARSAL_REPORT.md"},
        {"id": "k3twin", "title": "K3-A Pro twin: fly a flight card", "tag": "SIMULATED",
         "desc": "Plans the sorties for a grid field, loads each to the litre and flies them through the JIYI "
                 "K3-A Pro behavioural twin: breakpoints, pump dynamics, battery reserve, coverage.",
         "options": [_opt("w", "Width m", "number", 150), _opt("h", "Height m", "number", 120),
                     _opt("cols", "Zone columns", "number", 3), _opt("rows", "Zone rows", "number", 3)],
         "report": "runs/k3twin/latest.md"},
        {"id": "pump", "title": "T1 pump and nozzles (bench emulator)", "tag": "SIMULATED",
         "desc": "The hydraulic pump model behind the pump-bridge firmware protocol: litres for a spray command, "
                 "nominal and across the stated parameter ranges.",
         "options": [_opt("seconds", "Spray seconds", "number", 30), _opt("pwm", "Pump PWM (µs)", "number", 1900)]},
        {"id": "bench", "title": "Benchmark: FieldPilot vs the common way", "tag": "SIMULATED PLANNING",
         "desc": "Five countries, each day planned twice on the same bookings, forecast and batteries. Needs the "
                 "internet for the live forecasts.",
         "options": [_opt("days", "Days", "number", 3)], "report": "eval/results/latest.md"},
        {"id": "grounding", "title": "Grounding evaluation (crop twin and label parser)", "tag": "EVALUATION",
         "desc": "Checks the crop twin and label grounding. Without a key it runs the code-only part.",
         "options": [_opt("nollm", "No LLM (code only)", "bool", not _has_key()),
                     _opt("offline", "Offline synthetic weather (never for the submission)", "bool", False)]},
        {"id": "airframe", "title": "T1 airframe, battery kit and work rate", "tag": "MODEL",
         "desc": "Hover power, flow law, take-off category, sorties per day on the kit as shipped."},
        {"id": "devsim", "title": "FieldMast device simulator", "tag": "SIMULATED",
         "desc": "Posts simulated mast readings to this app, as the ESP32 would. Watch /ops → Live.",
         "options": [_opt("count", "Readings", "number", 10), _opt("every", "Seconds apart (whole)", "number", 2),
                     _opt("wind", "Wind m/s", "number", 2.1), _opt("temp", "Temp °C", "number", 29.0),
                     _opt("rh", "RH %", "number", 58.0)], "view": "/ops"},
        {"id": "deviation", "title": "Deviation: prediction vs reality", "tag": "REHEARSAL",
         "desc": "Compares the newest rehearsal with whatever real results exist. Run a rehearsal first.",
         "options": []},
        {"id": "sitl", "title": "ArduCopter firmware flight (SITL)", "tag": "SIMULATED",
         "desc": "FieldPilot's missions flown by unmodified ArduCopter on the T1 model. SITL is a Linux program: "
                 "this runs it where an ArduPilot tree is built (Linux/WSL). Saved runs open on the Firmware tab.",
         "options": [_opt("ardupilot", "ArduPilot tree", "text", os.environ.get("FP_ARDUPILOT", "")),
                     _opt("field", "Field", "select", "1ha", ["1ha", "demo"]),
                     _opt("nodrift", "Drift compensation off", "bool", False)], "view": "/ops#fw"},
        {"id": "console", "title": "Console flight simulator (agent demo run)", "tag": "SIMULATED",
         "desc": "The operator console flies a sortie in the simulator from the bundled synthetic photos.",
         "link": "/"},
        {"id": "desk", "title": "Operator desk: plan a day from bookings", "tag": "DEMO DATA",
         "desc": "Paste bookings or load a demo, plan the day on the forecast.", "link": "/desk"},
    ]


# ----------------------------------------------------------------------------- runners
def _argv(o: dict, pairs: list[tuple[str, str]], flags: list[tuple[str, str]]) -> list[str]:
    a: list[str] = []
    for key, flag in pairs:
        if str(o.get(key, "")).strip() != "":
            a += [flag, str(o[key])]
    for key, flag in flags:
        if o.get(key):
            a.append(flag)
    return a


def _run_k3twin(o: dict) -> int:
    from dataclasses import replace

    from . import k3sim
    from .field import grid_field
    from .mission import drone_spec
    from .sorties import best_takeoff, load_sheet, plan_sorties
    spec = drone_spec(None)
    f = grid_field("lab", (28.4499, 77.5837), float(o["w"]), float(o["h"]), int(o["cols"]), int(o["rows"]))
    z = f.zone_ids()
    home, _, _ = best_takeoff(f, z, spec)
    f = replace(f, home=home)
    parts = plan_sorties(f, z, spec)
    sheet = load_sheet(parts, spec)
    flown = k3sim.fly_card(f, z, spec, [r["water_l"] for r in sheet["rows"]])
    cmp = k3sim.compare_with_plan(f, parts, flown)
    cov = k3sim.coverage(f, z, flown, spec.swath_m)
    lines = [f"# K3-A Pro twin: {o['w']} x {o['h']} m, {len(z)} zones", f"**{k3sim.LABEL}**", "",
             "| Sortie | Plan s | Flown s | Ended | Battery left % | Litres |", "|---|---|---|---|---|---|"]
    for a, b in zip(parts, flown):
        lines.append(f"| {b.idx} | {a.est_time_s:.0f} | {b.flight_s:.0f} | {b.end} | {b.battery_left_pct:.1f} | {b.load_l:.2f} |")
    lines += ["", f"Breakpoint vs planned cut, worst: {cmp.get('max_cut_error_m')} m",
              f"Lowest zone coverage under the swath: {min(v['covered_pct'] for v in cov.values()):.1f} %",
              f"Left in the tank at the end: {flown[-1].left_in_tank_l:.2f} L"]
    text = "\n".join(lines)
    print(text)
    out = ROOT / "runs" / "k3twin"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.md").write_text(text, encoding="utf-8")
    return 0


def _run_pump(o: dict) -> int:
    from . import emulator
    s, pwm = float(o["seconds"]), int(float(o["pwm"]))
    n = int(s / 0.5)                                       # the bridge repeats the command: dead-man after 1.5 s
    cmds = [(2.0 + 0.5 * k, pwm) for k in range(n)] + [(2.0 + s, 1100)]
    b = emulator.bounds(cmds, s + 6.0)
    print(f"SIMULATED pump, {s:g} s at {pwm} µs: {b['litres_nominal']} L nominal "
          f"({b['litres_min']} to {b['litres_max']} L across the uncertain parameters)")
    return 0


def _run_devsim(o: dict) -> int:
    from .devsim import main as ds
    url = o.get("_url") or os.environ.get("FP_URL", "http://127.0.0.1:8080")
    return ds(["mast", "--url", url, "--count", str(int(float(o["count"]))), "--every", str(max(1, int(float(o["every"])))),
               "--wind", str(o["wind"]), "--temp", str(o["temp"]), "--rh", str(o["rh"])])


def _eval_mod(name: str):
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(f"eval.{name}")


def _run(sim: str, o: dict) -> int:
    if sim == "fieldday":
        from .fieldday import main
        a = _argv(o, [("seed", "--seed"), ("demo", "--demo"), ("date", "--date")],
                  [("synthetic", "--synthetic-weather"), ("nodrift", "--no-drift-compensation"), ("live", "--live")])
        return main(a)
    if sim == "rehearse":
        from .rehearsal import run_all
        return run_all(quick=bool(o.get("quick")))
    if sim == "k3twin":
        return _run_k3twin(o)
    if sim == "pump":
        return _run_pump(o)
    if sim == "bench":
        return _eval_mod("efficiency_bench").main(["--days", str(int(float(o.get("days", 3))))])
    if sim == "grounding":
        a = (["--no-llm"] if o.get("nollm") else []) + (["--offline-weather"] if o.get("offline") else [])
        return _eval_mod("grounding_eval").main(a)
    if sim == "airframe":
        from .cli import main
        return main(["airframe"])
    if sim == "devsim":
        return _run_devsim(o)
    if sim == "deviation":
        from .deviation import main
        return main([])
    if sim == "sitl":
        if sys.platform.startswith("win") or not str(o.get("ardupilot", "")).strip():
            print("ArduCopter SITL is a Linux program, and no built ArduPilot tree is set.\n"
                  "On Linux/WSL build ArduCopter, then run this here or `python -m fieldpilot.cli sitl --ardupilot <tree>`.\n"
                  "Saved firmware runs: /ops → Firmware tab.")
            return 2
        from .sitl import main
        return main(_argv(o, [("ardupilot", "--ardupilot"), ("field", "--field")], [("nodrift", "--no-drift-compensation")]))
    raise KeyError(sim)


class _Tee(io.TextIOBase):
    def __init__(self, job: dict):
        self.job = job

    def write(self, s):
        self.job["log"].append(s)
        if len(self.job["log"]) > 20000:
            del self.job["log"][:5000]
        return len(s)

    def flush(self):
        pass


def _worker(job: dict, sim: str, opts: dict) -> None:
    env0 = dict(os.environ)
    try:
        with contextlib.redirect_stdout(_Tee(job)), contextlib.redirect_stderr(_Tee(job)):
            try:
                job["rc"] = int(_run(sim, opts) or 0)
                job["status"] = "done" if job["rc"] == 0 else "failed"
            except SystemExit as e:
                job["rc"] = int(e.code or 0) if isinstance(e.code, int) else 1
                job["status"] = "done" if job["rc"] == 0 else "failed"
            except Exception:
                traceback.print_exc()
                job["rc"], job["status"] = 1, "error"
    finally:
        os.environ.clear()
        os.environ.update(env0)           # the rehearsal repoints FP_DRONE and the model URL: undo it
        job["ended"] = time.time()
        _lock.release()


def start(sim: str, opts: dict) -> dict:
    cat = {c["id"]: c for c in catalog()}
    if sim not in cat or cat[sim].get("link"):
        raise KeyError(sim)
    if not _lock.acquire(blocking=False):
        raise RuntimeError("another simulation is running; wait for it to finish")
    defaults = {o["key"]: o["default"] for o in cat[sim].get("options", [])}
    job = {"id": uuid.uuid4().hex[:10], "sim": sim, "title": cat[sim]["title"], "tag": cat[sim]["tag"],
           "status": "running", "rc": None, "started": time.time(), "ended": None, "log": []}
    JOBS[job["id"]] = job
    threading.Thread(target=_worker, args=(job, sim, {**defaults, **opts}), daemon=True).start()
    return job


def _newest(pattern: str) -> Path | None:
    hits = sorted(ROOT.glob(pattern), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def view(job_id: str) -> dict:
    j = JOBS[job_id]
    cat = {c["id"]: c for c in catalog()}[j["sim"]]
    report = None
    if j["status"] != "running":
        pat = {"fieldday": "runs/fieldday/*/FIELD_DAY_REPORT.md", "sitl": "runs/sitl/*/SITL_REPORT.md",
               "bench": "eval/results/*.md", "grounding": "eval/results/*.md"}.get(j["sim"]) or cat.get("report")
        p = _newest(pat) if pat else None
        if p and p.stat().st_mtime >= j["started"] - 1:
            report = {"path": str(p.relative_to(ROOT)), "text": p.read_text(encoding="utf-8", errors="replace")[:60000]}
    return {"id": j["id"], "sim": j["sim"], "title": j["title"], "tag": j["tag"], "status": j["status"],
            "rc": j["rc"], "seconds": round((j["ended"] or time.time()) - j["started"], 1),
            "log": "".join(j["log"])[-30000:], "report": report, "view": cat.get("view")}
