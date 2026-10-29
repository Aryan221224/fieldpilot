"""Operator console API.

    uvicorn fieldpilot.api:app --host 0.0.0.0 --port 8080

Runs execute in a background thread; the console follows them over
Server-Sent Events (the agent trace) and polls telemetry. Approve / Abort are
the only endpoints that touch the drone, and Abort is deliberately cheap to
call: no body, no confirmation, no model in the path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import defaultdict

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import ROOT
from .service import FieldPilot

log = logging.getLogger(__name__)
app = FastAPI(title="Future Agri FieldPilot", version="0.1.0")
_fp: FieldPilot | None = None
_running: dict[str, dict] = {}
_lock = threading.Lock()


def fp() -> FieldPilot:
    global _fp
    if _fp is None:
        import os
        # FP_SIM_SPEED=1 flies the simulator in real time (the bench clip needs real speeds)
        _fp = FieldPilot(sim_time_scale=float(os.environ.get("FP_SIM_SPEED", "4")))
    return _fp


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/state")
def state():
    f = fp()
    models = {}
    if f.llm is not None:
        try:
            models = f.llm.resolve()
        except Exception as e:  # noqa: BLE001
            models = {"error": str(e)}
    return {"field": f.field.to_dict(), "mode": f.cfg.mode(), "models": models,
            "drone": f.drone.telemetry().to_dict(), "drone_error": f.drone_error,
            "drone_spec": f.spec.__dict__,
            "runs": [{"run_id": k, "status": (v.proposal or {}).get("status", "running"),
                      "headline": (v.proposal or {}).get("headline")} for k, v in f.runs.items()]}


def _start(images: dict[str, list[bytes]], note: str, replan_of: str | None = None) -> str:
    f = fp()
    box: dict = {"run_id": replan_of, "events": [], "done": False, "error": None}

    def emit(e):
        box["events"].append(e)

    def work():
        try:
            s = f.replan(replan_of, emit=emit) if replan_of else f.new_run(images, note=note, emit=emit)
            box["run_id"] = s.run_id
        except Exception as e:  # noqa: BLE001
            log.exception("run failed")
            box["error"] = f"{type(e).__name__}: {e}"
            box["events"].append({"type": "error", "message": box["error"]})
        finally:
            box["done"] = True

    key = f"J{len(_running) + 1}"
    _running[key] = box
    box["images"] = images
    threading.Thread(target=work, daemon=True).start()
    return key


@app.post("/api/runs")
async def create_run(files: list[UploadFile] = File(...), zones: str = Form("{}"),
                     note: str = Form("")):
    """Upload photos. `zones` maps filename -> zone id; otherwise the filename
    prefix (A1_..., B2-...) is used."""
    mapping = json.loads(zones or "{}")
    zids = fp().field.zone_ids()
    images: dict[str, list[bytes]] = defaultdict(list)
    for up in files:
        z = mapping.get(up.filename)
        if not z:
            m = re.match(r"([A-Za-z]\d+)[_\-. ]", (up.filename or "") + " ")
            z = m.group(1).upper() if m else None
        if z not in zids:
            raise HTTPException(400, f"cannot assign {up.filename!r} to a zone {zids}")
        images[z].append(await up.read())
    return {"job": _start(dict(images), note)}


@app.post("/api/demo-run")
def demo_run():
    """Run the agent on the bundled SYNTHETIC example photos."""
    folder = ROOT / "examples" / "images"
    images: dict[str, list[bytes]] = defaultdict(list)
    for p in sorted(folder.glob("*.jpg")):
        images[p.name[:2]].append(p.read_bytes())
    if not images:
        raise HTTPException(500, "no example images; run examples/make_demo_images.py")
    return {"job": _start(dict(images), "demo run on synthetic example photos")}


@app.get("/api/jobs/{job}/events")
async def job_events(job: str):
    box = _running.get(job)
    if box is None:
        raise HTTPException(404, "unknown job")

    async def gen():
        i = 0
        while True:
            evs = box["events"]
            while i < len(evs):
                yield f"data: {json.dumps(evs[i], default=str, ensure_ascii=False)}\n\n"
                i += 1
            if box["done"] and i >= len(box["events"]):
                yield f"data: {json.dumps({'type': 'done', 'run_id': box['run_id'], 'error': box['error']})}\n\n"
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job}/image/{zone}/{idx}")
def job_image(job: str, zone: str, idx: int):
    box = _running.get(job)
    try:
        return Response(box["images"][zone][idx], media_type="image/jpeg")
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "no such image")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    s = fp().runs.get(run_id)
    if s is None:
        raise HTTPException(404, "unknown run")
    d = s.summary()
    d["trace_len"] = len(s.trace)
    return JSONResponse(json.loads(json.dumps(d, default=str)))


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, operator: str = Form("operator"), override_reason: str = Form("")):
    try:
        return fp().approve(run_id, operator=operator, override_reason=override_reason)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"dispatch failed: {type(e).__name__}: {e}")


@app.get("/api/runs/{run_id}/paperwork/{kind}")
def paperwork(run_id: str, kind: str):
    """Gram Panchayat notice, SOP spray record or Hindi farmer slip, as a printable page."""
    from fastapi.responses import HTMLResponse
    from .paperwork import from_state
    s = fp().runs.get(run_id)
    if s is None:
        raise HTTPException(404, "unknown run")
    try:
        docs = from_state(s)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if kind not in docs:
        raise HTTPException(404, f"kind must be one of {sorted(docs)}")
    return HTMLResponse(docs[kind])


@app.get("/api/runs/{run_id}/card")
def flight_card(run_id: str, fmt: str = "html"):
    """The pilot's flight card for a closed controller (JIYI K3-A Pro): html or kml."""
    from fastapi.responses import HTMLResponse
    from .k3card import card_html, flight_card as make_card, mission_kml
    svc = fp()
    s = svc.runs.get(run_id)
    if s is None or s.mission is None:
        raise HTTPException(404, "no mission for this run")
    if fmt == "kml":
        return Response(mission_kml(svc.field, s.mission), media_type="application/vnd.google-earth.kml+xml",
                        headers={"Content-Disposition": f'attachment; filename="{s.mission.mission_id}.kml"'})
    appr = (s.proposal or {}).get("approval") or {}
    return HTMLResponse(card_html(make_card(svc.field, s.mission, svc.spec, appr.get("operator"))))


@app.post("/api/runs/{run_id}/reject")
def reject(run_id: str, reason: str = Form("")):
    return fp().reject(run_id, reason)


@app.post("/api/runs/{run_id}/replan")
def replan(run_id: str):
    s = fp().runs.get(run_id)
    if s is None:
        raise HTTPException(404, "unknown run")
    return {"job": _start(s.images, "", replan_of=run_id)}


@app.post("/api/runs/{run_id}/adjudicate")
def adjudicate(run_id: str, zone_id: str = Form(...), category: str = Form(...),
               label: str = Form(...), severity: int = Form(...)):
    try:
        return fp().adjudicate(run_id, zone_id, category, label, severity)
    except KeyError as e:
        raise HTTPException(400, f"unknown {e}")


@app.post("/api/abort")
def abort():
    return fp().abort()


@app.get("/api/telemetry")
def telemetry():
    return fp().drone.telemetry().to_dict()


# --------------------------------------------------------------------------- operator desk

@app.get("/desk")
def desk_page():
    return FileResponse(ROOT / "web" / "desk.html")


@app.get("/api/desk/demos")
def desk_demos():
    out = {}
    for p in sorted((ROOT / "examples").glob("desk_demo_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[p.stem.removeprefix("desk_demo_")] = d.get("operator", {}).get("name", p.stem)
    return out


@app.get("/api/desk/demo/{key}")
def desk_demo(key: str):
    p = ROOT / "examples" / f"desk_demo_{re.sub(r'[^a-z0-9_]', '', key.lower())}.json"
    if not p.exists():
        raise HTTPException(404, "no such demo")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.post("/api/desk/intake")
def desk_intake(body: dict):
    """Booking messages -> jobs. Nemotron on Token Factory reads them; code checks numbers."""
    from .dayplan import Operator
    from .desk import intake_messages
    f = fp()
    if f.llm is None:
        raise HTTPException(400, "reading bookings needs NEBIUS_API_KEY (Nemotron on Token Factory)")
    op = Operator.from_dict(body.get("operator") or {})
    _last_day["ledger_from"] = len(getattr(f.llm, "ledger", []) or [])
    rows = intake_messages(body.get("messages", []), op, f.llm)
    return {"jobs": rows, "model": f.llm.model_for("intake") if hasattr(f.llm, "model_for")
            else getattr(f.llm, "agent_model", None)}


_last_day: dict = {}


@app.post("/api/desk/correct")
def desk_correct(body: dict):
    """The operator fixed a value the model read: keep it as a regression case for intake."""
    from .flywheel import record
    case = record(body.get("job") or {}, body.get("corrections") or {}, fp().cfg.runs_dir)
    return {"recorded": case is not None}


@app.post("/api/desk/plan")
def desk_plan(body: dict):
    """Jobs -> the day, against live forecasts at every farm, compared with first-come-first-served."""
    from datetime import date as _date
    from .dayplan import Operator
    from .desk import jobs_from, plan, save
    f = fp()
    op = Operator.from_dict(body.get("operator") or {})
    day = _date.fromisoformat(body.get("date") or _date.today().isoformat())
    jobs, callback = jobs_from(body.get("jobs", []))
    if not jobs:
        raise HTTPException(400, "no schedulable jobs (each needs an area and a location)")
    try:
        res = plan(jobs, op, day, llm=f.llm, ledger_from=_last_day.pop("ledger_from", None))
    except OSError as e:
        raise HTTPException(502, f"forecast unavailable: {e}") from e
    res["callback"] = callback
    p = save(res, f.cfg.runs_dir)
    _last_day["html"] = res.pop("sheet_html")
    res["saved"] = p.name
    return JSONResponse(json.loads(json.dumps(res, default=str)))


@app.get("/api/desk/sheet")
def desk_sheet():
    if "html" not in _last_day:
        raise HTTPException(404, "plan a day first")
    return Response(_last_day["html"], media_type="text/html; charset=utf-8")


# --------------------------------------------------------------------------- field devices
# FieldMast (site weather), SprayLog (as applied) and the battery board; dashboard at /ops.

def _device_auth(request: Request) -> None:
    import os
    tok = os.environ.get("FP_DEVICE_TOKEN", "")
    if tok and request.headers.get("X-FP-Token", "") != tok:
        raise HTTPException(401, "device token missing or wrong")


@app.get("/ops")
def ops_page():
    return FileResponse(ROOT / "web" / "ops.html")


@app.post("/api/devices/mast")
def device_mast(body: dict, request: Request):
    """FieldMast reading, every 10 s. Judged here against the console's rule pack."""
    _device_auth(request)
    f = fp()
    r = f.devices.ingest_mast(body, f.rule_pack)
    return {"ok": r["ok"], "reasons": r["reasons"]}


@app.post("/api/devices/spraylog")
def device_spraylog(body: dict, request: Request):
    """One sortie from SprayLog, uploaded after landing; linked to the run flying now."""
    _device_auth(request)
    f = fp()
    try:
        return f.devices.ingest_spraylog(body, run_id=f.flying_run_id())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/batteries/{pack_id}")
def battery_event(pack_id: str, body: dict):
    try:
        return fp().devices.pack_event(pack_id, str(body.get("state", "")), str(body.get("note", "")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/devices/as-applied")
def device_as_applied(run_id: str | None = None):
    rep = fp().as_applied(run_id)
    if rep is None:
        raise HTTPException(404, "no SprayLog upload for a dispatched mission yet")
    return rep


@app.get("/api/ops")
def ops_state():
    """Everything the Field Ops dashboard shows, live."""
    f = fp()
    gate = f.devices.site_gate(near=f.field.home)
    try:
        aa = f.as_applied()
    except Exception:  # noqa: BLE001
        aa = None
    return JSONResponse(json.loads(json.dumps({
        "mode": f.cfg.mode(), "rule_pack": f.rule_pack,
        "field": {"name": f.field.name, "home": f.field.home,
                  "zones": {z.zone_id: z.polygon for z in f.field.zones},
                  "geofence": f.field.geofence},
        "site": gate, "mast_history": f.devices.mast_history(),
        "packs": f.devices.packs_view(), "devices": f.devices.status(),
        "drone": f.drone.telemetry().to_dict() if hasattr(f.drone, "telemetry") else None,
        "as_applied": aa}, default=str)))


@app.get("/api/sitl")
def sitl_list():
    from .sitl import runs_list
    return runs_list()


@app.get("/api/sitl/{run}")
def sitl_get(run: str):
    from .sitl import run_view
    name = re.sub(r"[^0-9TZ]", "", run)
    if not (ROOT / "runs" / "sitl" / name / "sitl.json").is_file():
        raise HTTPException(404, "no firmware run of that name: run `python -m fieldpilot.cli sitl`")
    return run_view(name)


@app.get("/api/fieldday")
def fieldday_list():
    base = ROOT / "runs" / "fieldday"
    return sorted((p.parent.name for p in base.glob("*/fieldday.json") if p.is_file()), reverse=True) \
        if base.exists() else []


@app.get("/api/fieldday/{day}")
def fieldday_get(day: str):
    p = ROOT / "runs" / "fieldday" / re.sub(r"[^0-9-]", "", day) / "fieldday.json"
    if not p.exists():
        raise HTTPException(404, "no simulated field day for that date: run `python -m fieldpilot.cli fieldday`")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/sims")
def sims_page():
    return FileResponse(ROOT / "web" / "sims.html")


@app.get("/api/sims")
def sims_catalog():
    from .simlab import catalog
    return catalog()


@app.post("/api/sims/{sim}/run")
async def sims_run(sim: str, request: Request):
    from .simlab import start
    try:
        opts = await request.json()
    except Exception:
        opts = {}
    try:
        opts = dict(opts) if isinstance(opts, dict) else {}
        opts["_url"] = str(request.base_url).rstrip("/")        # where the device feeds post
        j = start(sim, opts)
    except KeyError:
        raise HTTPException(404, "no such simulation")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"id": j["id"], "title": j["title"]}


@app.get("/api/sims/jobs/{job}")
def sims_job(job: str):
    from .simlab import JOBS, view
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    return view(job)


# ------------------------------------------------------------------ settings (keys), this PC only
def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "settings can only be changed from this computer")


def _set_env_file(updates: dict[str, str]) -> None:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    done = set()
    for i, ln in enumerate(lines):
        k = ln.split("=", 1)[0].strip()
        if k in updates and "=" in ln and not ln.lstrip().startswith("#"):
            lines[i] = f"{k}={updates[k]}"
            done.add(k)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/settings")
def settings_page():
    return FileResponse(ROOT / "web" / "settings.html")


@app.get("/api/settings")
def settings_get(request: Request):
    _local_only(request)
    import os
    from .config import load
    c = load()
    mask = lambda v: ("set (ends …" + v[-4:] + ")") if v else "not set"      # noqa: E731
    return {"nebius_api_key": mask(c.nebius_api_key), "tavily_api_key": mask(c.tavily_api_key),
            "base_url": c.nebius_base_url, "on_nebius": bool(c.nebius_api_key) and "nebius" in c.nebius_base_url,
            "env_file": str(ROOT / ".env"), "frozen": bool(os.environ.get("FP_URL"))}


@app.post("/api/settings")
async def settings_set(request: Request):
    _local_only(request)
    import os
    body = await request.json()
    ups = {}
    for field, env in (("nebius_api_key", "NEBIUS_API_KEY"), ("tavily_api_key", "TAVILY_API_KEY")):
        v = str(body.get(field, "")).strip()
        if v:
            if any(ch in v for ch in "\r\n\"' "):
                raise HTTPException(400, f"{field} has spaces, quotes or line breaks: paste only the key")
            ups[env] = v
    if not ups:
        raise HTTPException(400, "nothing to save")
    _set_env_file(ups)
    os.environ.update(ups)
    global _fp
    _fp = None                                           # next run rebuilds the service with the new key
    return {"saved": sorted(ups)}


@app.post("/api/settings/test")
def settings_test(request: Request):
    """One tiny call to the fast Nemotron: proves the key, the model and the billing."""
    _local_only(request)
    from .config import load
    from .llm import TokenFactory
    c = load()
    if not c.nebius_api_key:
        raise HTTPException(400, "no Nebius key set")
    try:
        tf = TokenFactory(c)
        picks = tf.resolve()
        reply = tf.text([{"role": "user", "content": "Reply with the single word OK."}],
                        max_tokens=400, temperature=0.0, purpose="brief")
        return {"ok": True, "agent": picks["agent"], "vision": picks["vision"], "fast": tf.fast_model,
                "reply": reply.strip()[:60], "models_seen": len(tf.catalog())}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}"}, status_code=200)
