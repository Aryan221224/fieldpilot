"""FieldPilot's missions flown by real autopilot firmware: ArduCopter in SITL, with a T1 model.

    python -m fieldpilot.cli sitl --ardupilot ~/ardupilot              # 1 ha, all zones, 3 sorties
    python -m fieldpilot.cli sitl --ardupilot ~/ardupilot --field demo # the console's demo field
    python -m fieldpilot.cli sitl ... --wind 2.4 --wind-dir 300        # site wind (m/s, from deg)

The T1 flies on a closed JIYI K3-A Pro. If its controller is ever swapped for an open one,
ArduPilot is the firmware that would drive the whole aircraft: attitude, navigation,
failsafes and the sprayer. This runs that firmware - unmodified ArduCopter, built for
SITL - on a physics model of the T1 (``sim/t1_frame.json``: 24 kg, 1.3 m, 4 x 34.7-inch
props, 12S 16 Ah) with the T1's parameters (``sim/t1_sitl.parm``: sprayer flow following
ground speed, battery and fence failsafes). FieldPilot plans the job, cuts it into sorties
and uploads each one over MAVLink exactly as it would to the real aircraft (DO_SPRAYER on
and off per pass), arms it and starts it. The firmware flies it and writes its own
dataflash log. From that log this computes, per sortie and against FieldPilot's plan:

* time, energy (the firmware's own battery integration) and the battery left;
* how well the aircraft held the planned lines (cross-track error while spraying);
* the litres the pump delivered: the firmware's sprayer output (servo 9) mapped to the T1's
  3.5 L/min full flow, passed through the T1 pump's lag and dead time;
* litres per zone and anything outside the zones, through the same as-applied code that
  reads SprayLog, and the share of each zone under the swath.

Realism added to the simulator: wind and turbulence at the site, GPS noise of a u-blox
class receiver, the pack's internal resistance. The mass stays at the full-tank 24 kg
(SITL cannot empty a tank), so energy errs high. Labelled SIMULATED in every output.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT
LABEL = "SIMULATED (ArduCopter firmware in SITL, T1 physics model)"
SERVO_MIN, SERVO_MAX = 1100, 1900


def _wait_port(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.3)
    return False


def _field(which: str):
    from .field import Field, grid_field
    if which == "demo":
        return Field.load(ROOT / "examples" / "field_demo.json"), None
    f = grid_field("FieldPilot 1 ha job (Dankaur)", (28.3517, 77.5530), 100.0, 100.0, 2, 2)
    return f, f.zone_ids()


def frame_for_mass(mass_kg: float, ref_mass: float = 24.0, ref_hover: float = 0.52, expo: float = 0.55,
                   ref_current: float = 67.7, ref_angle: float = 20.0) -> dict:
    """The T1 frame at this sortie's mass. SITL holds mass constant, so the sortie flies at its
    mean mass (dry + pack + half the load). The motors stay the same: hover throttle comes from
    the T1's thrust curve at 24 kg / 0.52, drag stays the same (reference tilt scaled), and the
    reference current scales with mass^1.5 as induced power does."""
    f24 = (1 - expo) * ref_hover + expo * ref_hover ** 2          # thrust fraction at hover, 24 kg
    fm = f24 * mass_kg / ref_mass
    t = (-(1 - expo) + math.sqrt((1 - expo) ** 2 + 4 * expo * fm)) / (2 * expo)
    ang = math.degrees(math.atan(math.tan(math.radians(ref_angle)) * ref_mass / mass_kg))
    return {"mass": round(mass_kg, 2), "hoverThrOut": round(t, 3),
            "refCurrent": round(ref_current * (mass_kg / ref_mass) ** 1.5, 1), "refAngle": round(ang, 1)}


def _write_frame(workdir: Path, mass_kg: float) -> dict:
    import re
    txt = (ROOT / "sim" / "t1_frame.json").read_text(encoding="utf-8")
    adj = frame_for_mass(mass_kg)
    for k, v in adj.items():
        txt = re.sub(rf'("{k}"\s*:\s*)[-0-9.]+', rf"\g<1>{v}", txt)
    (workdir / "t1_frame.json").write_text(txt, encoding="utf-8")
    return adj


def _sitl_cmd(ap: Path, workdir: Path, home: tuple[float, float], speedup: int) -> list[str]:
    """SITL resolves files relative to its working directory, so the model and parameters are
    copied next to it (the frame is written per sortie by _write_frame)."""
    binpath = ap / "build" / "sitl" / "bin" / "arducopter"
    shutil.copy(ap / "Tools" / "autotest" / "default_params" / "copter.parm", workdir / "copter.parm")
    shutil.copy(ROOT / "sim" / "t1_sitl.parm", workdir / "t1_sitl.parm")
    return [str(binpath), "-S", "--model", "x:t1_frame.json", "--speedup", str(speedup),
            "--defaults", "copter.parm,t1_sitl.parm,site.parm", "-I0",
            "--home", f"{home[0]:.7f},{home[1]:.7f},200,0"]


def _site_params(path: Path, wind: float, wind_dir: float, turb: float, gps_noise: float,
                 hover: float | None = None) -> None:
    path.write_text("\n".join(([f"MOT_THST_HOVER {hover:.3f}"] if hover else []) + [
        f"SIM_WIND_SPD {wind:.2f}", f"SIM_WIND_DIR {wind_dir:.0f}", f"SIM_WIND_TURB {turb:.2f}",
        "SIM_WIND_T 1", f"SIM_GPS_NOISE {gps_noise:.2f}", ""]), encoding="utf-8")


def fly_sortie(ap: Path, mission, spec, workdir: Path, home, speedup: int, site: dict,
               timeout_s: float = 900.0) -> dict:
    """Start SITL, upload one sortie, fly it, return the firmware's log path and events."""
    from .drone.mavlink import MavlinkBackend
    workdir.mkdir(parents=True, exist_ok=True)
    mass = spec.dry_mass_kg + spec.battery_mass_kg + mission.spray_ml / 1000.0 / 2
    frame = _write_frame(workdir, mass)
    _site_params(workdir / "site.parm", site["wind"], site["wind_dir"], site["turb"], site["gps_noise"],
                 frame["hoverThrOut"])
    proc = subprocess.Popen(_sitl_cmd(ap, workdir, home, speedup), cwd=workdir,
                            stdout=open(workdir / "sitl_stdout.txt", "w"), stderr=subprocess.STDOUT)
    events = []
    t0 = time.time()
    try:
        if not _wait_port(5760, 60):
            raise RuntimeError("SITL did not open its MAVLink port")
        be = MavlinkBackend(url="tcp:127.0.0.1:5760", spec=spec, spray_cmd="sprayer", heartbeat_timeout=30)
        be.connect()
        # wait for GPS, EKF and pre-arm checks
        end = time.time() + 240
        while time.time() < end:
            pf = be.preflight()
            if pf["ok"]:
                break
            time.sleep(1.0)
        events.append({"wall_s": round(time.time() - t0, 1), "event": "ready", "preflight": be.preflight()})
        be.upload(mission)
        for attempt in range(20):
            try:
                be.start()
                break
            except RuntimeError as e:
                events.append({"wall_s": round(time.time() - t0, 1), "event": "start refused", "why": str(e),
                               "msg": be.telemetry().message})
                time.sleep(2.0)
        else:
            raise RuntimeError("the autopilot would not arm/start")
        events.append({"wall_s": round(time.time() - t0, 1), "event": "mission started"})
        end = time.time() + timeout_s / max(1, speedup) + 120
        landed = False
        while time.time() < end:
            t = be.telemetry()
            if t.state == "landed" or (not t.armed and time.time() - t0 > 30 and t.state != "flying"):
                landed = True
                break
            time.sleep(0.5)
        events.append({"wall_s": round(time.time() - t0, 1), "event": "landed" if landed else "timeout",
                       "msg": be.telemetry().message})
        be.close()
        time.sleep(2.0)                      # let the firmware flush its log
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    logs = sorted((workdir / "logs").glob("*.BIN")) if (workdir / "logs").exists() else []
    return {"log": str(logs[-1]) if logs else None, "events": events, "frame": frame}


def read_log(path: str) -> dict:
    """The firmware's dataflash log: position, speed, battery, sprayer output."""
    from pymavlink import mavutil
    mlog = mavutil.mavlink_connection(path)
    pos, bat, rcou, gps, modes, sim, cmds = [], [], [], [], [], [], []
    t_upload = None
    while True:
        m = mlog.recv_match(type=["POS", "BAT", "RCOU", "GPS", "MODE", "ARM", "SIM", "CMD"])
        if m is None:
            break
        t = getattr(m, "TimeUS", 0) / 1e6
        typ = m.get_type()
        if typ == "POS":
            pos.append((t, m.Lat, m.Lng, getattr(m, "RelHomeAlt", getattr(m, "Alt", 0.0))))
        elif typ == "BAT":
            bat.append((t, m.Volt, m.Curr, m.CurrTot, getattr(m, "EnrgTot", float("nan"))))
        elif typ == "RCOU":
            rcou.append((t, getattr(m, "C9", 0)))
        elif typ == "GPS":
            gps.append((t, m.Spd))
        elif typ == "MODE":
            modes.append((t, getattr(m, "Mode", None)))
        elif typ == "SIM":
            sim.append((t, m.Lat, m.Lng, m.Alt))
        elif typ == "CMD":
            if t_upload is None:
                t_upload = t
            if t != t_upload:                       # logged again when executed
                cmds.append((t, int(m.CId), float(m.Prm1)))
    return {"pos": pos, "bat": bat, "rcou": rcou, "gps": gps, "modes": modes, "sim": sim, "cmds": cmds}


def _interp(series, t, k=1):
    """Value at time t from [(t, v...)], linear."""
    lo, hi = 0, len(series) - 1
    if not series:
        return float("nan")
    if t <= series[0][0]:
        return series[0][k]
    if t >= series[-1][0]:
        return series[-1][k]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    a, b = series[lo], series[hi]
    f = (t - a[0]) / max(1e-9, b[0] - a[0])
    return a[k] + f * (b[k] - a[k])


def analyse(log: dict, field_, mission, spec, pump_tau=0.25, pump_dead=0.15, site: dict | None = None,
            fly_field=None) -> dict:
    """Per-sortie results from the firmware log, against FieldPilot's plan."""
    from shapely.geometry import LineString, Point

    from .devices import as_applied, planned_litres
    if not log["pos"]:
        return {"error": "empty log"}
    fr = field_.frame
    # flight window: from the first pump/motor activity to the end of the log
    t_start = log["pos"][0][0]
    t_end = log["pos"][-1][0]
    alt_series = [(p[0], p[3]) for p in log["pos"]]
    airborne = [t for t, a in alt_series if a > 0.5]
    t_to = airborne[0] if airborne else t_start
    t_ld = airborne[-1] if airborne else t_end
    # commanded flow from the firmware's sprayer output, then the pump's lag and dead time
    dt = 0.1
    q = 0.0
    litres = 0.0
    hist = []
    dead_n = int(round(pump_dead / dt))
    samples = []
    track = []
    t = t_start
    last_s = -1.0
    peak = 0.0
    while t <= t_end:
        pwm = _interp(log["rcou"], t) if log["rcou"] else SERVO_MIN
        cmd = max(0.0, min(1.0, (pwm - SERVO_MIN) / (SERVO_MAX - SERVO_MIN))) * spec.pump_max_l_min
        hist.append(cmd)
        target = hist[-1 - dead_n] if len(hist) > dead_n else 0.0
        q += (target - q) * (1 - math.exp(-dt / pump_tau))
        litres += q / 60.0 * dt
        peak = max(peak, q)
        lat, lon = _interp(log["pos"], t, 1), _interp(log["pos"], t, 2)
        x, y = fr.to_local(lat, lon)
        tx, ty = (fr.to_local(_interp(log["sim"], t, 1), _interp(log["sim"], t, 2)) if log.get("sim") else (x, y))
        spd = _interp(log["gps"], t, 1) if log["gps"] else float("nan")
        track.append((t, x, y, q, spd, tx, ty))
        if t - last_s >= 1.0:
            last_s = t
            samples.append([round(t - t_start, 1), lat, lon, round(q, 3), round(litres, 4), spd, 1])
        t += dt
    sess = {"session_id": f"SITL-{mission.mission_id}", "litres_total": round(litres, 4), "samples": samples}
    planned = {z: v for z, v in planned_litres(field_, mission, spec).items() if z in mission.zones}
    aa = as_applied([sess], fly_field or field_, planned)       # the logger, against the lines flown
    # cross-track error while spraying: distance from the planned pass lines
    lines = []
    it = mission.items
    on = False
    prev = None
    for i in it:
        if i.kind == "spray_on":
            on = True
        elif i.kind == "spray_off":
            on = False
        elif i.kind == "waypoint":
            p = fr.to_local(i.lat, i.lon)
            if on and prev is not None:
                lines.append(LineString([prev, p]))
            prev = p
    xte, xte_true = [], []
    for (tt, x, y, qq, spd, tx, ty) in track:
        if qq > 0.3 and lines:
            xte.append(min(ln.distance(Point(x, y)) for ln in lines))
            xte_true.append(min(ln.distance(Point(tx, ty)) for ln in lines))
    xte.sort()
    xte_true.sort()
    # coverage under the swath while the pump flows
    from types import SimpleNamespace

    from .k3sim import coverage
    # what landed: the TRUE position (SIM), moved downwind by the drift model
    from .drift import AIRBORNE_PER_MS, deposit_shift
    dx, dy = deposit_shift(site["wind"], site["wind_dir"], field_.treat_alt_m) if site else (0.0, 0.0)
    rows = [[r[0], r[5] + dx, r[6] + dy, 3.0, r[4], 1 if r[3] > 0.05 else 0, r[3], 0, 0, ""] for r in track]
    cov = coverage(field_, list(planned), [SimpleNamespace(track=rows)], spec.swath_m)
    from shapely.geometry import Polygon
    polys = {z: Polygon([fr.to_local(a, b) for a, b in field_.zone(z).polygon]) for z in planned}
    loss = AIRBORNE_PER_MS * (site["wind"] if site else 0.0)
    landed = {z: 0.0 for z in planned}
    off = 0.0
    for a, b in zip(track, track[1:]):
        dl = b[3] / 60.0 * (b[0] - a[0]) * (1 - loss)
        if dl <= 0:
            continue
        pt = Point(b[5] + dx, b[6] + dy)
        hit = next((z for z, pg in polys.items() if pg.contains(pt)), None)
        if hit:
            landed[hit] += dl
        else:
            off += dl
    tot = sum(landed.values()) + off
    # phases, from the commands the firmware executed
    # (ArduCopter does not log DO_SPRAYER again when it executes it: use the pump output itself)
    sp = [r[0] for r in log["rcou"] if r[1] > SERVO_MIN + 5] or [c[0] for c in log.get("cmds", []) if c[1] == 216]
    rtl = [c[0] for c in log.get("cmds", []) if c[1] == 20 and (not sp or c[0] >= sp[-1])]
    phases = {"takeoff_and_transit_s": round(sp[0] - t_to, 1) if sp else None,
              "spraying_s": round(sp[-1] - sp[0], 1) if len(sp) > 1 else None,
              "return_and_land_s": round(t_ld - (rtl[0] if rtl else (sp[-1] if sp else t_ld)), 1)}
    # FieldPilot's ArduPilot timing model, from the parameters the aircraft flies with (not fitted)
    from .ap_timing import mission_energy_wh, mission_time, read_parm
    apt = mission_time(mission, fr, field_.home, spec, read_parm(ROOT / "sim" / "t1_sitl.parm"))
    apt["energy_wh"] = mission_energy_wh(apt, mission, spec)
    bat_end = log["bat"][-1] if log["bat"] else None
    v_min = min((b[1] for b in log["bat"] if b[0] >= t_to), default=float("nan"))
    gs_spray = [r[4] for r in track if r[3] > 0.3 and r[4] == r[4]]
    return {
        "flight_s": round(t_ld - t_to, 1), "planned_s": round(mission.est_time_s, 1),
        "energy_wh": round(bat_end[4], 1) if bat_end and bat_end[4] == bat_end[4] else None,
        "planned_wh": round(mission.est_energy_wh, 1),
        "ardupilot_model": apt,
        "mah_used": round(bat_end[3]) if bat_end else None,
        "battery_left_pct": round(100 * (1 - bat_end[3] / 16000.0), 1) if bat_end else None,
        "min_voltage_v": round(v_min, 2),
        "litres": round(litres, 3), "planned_litres": round(mission.spray_ml / 1000, 3),
        "peak_lpm": round(peak, 3),
        "mean_spray_speed_ms": round(sum(gs_spray) / len(gs_spray), 2) if gs_spray else None,
        "xte_m": {"median": round(xte[len(xte) // 2], 2) if xte else None,
                  "p95": round(xte[int(len(xte) * 0.95)], 2) if xte else None},
        "xte_true_m": {"median": round(xte_true[len(xte_true) // 2], 2) if xte_true else None,
                       "p95": round(xte_true[int(len(xte_true) * 0.95)], 2) if xte_true else None},
        "deposited_on_target_pct": round(100 * sum(landed.values()) / tot, 1) if tot else None,
        "deposited_per_zone_l": {z: round(v, 3) for z, v in landed.items()},
        "phases": phases,
        "as_applied": {k: v for k, v in aa.items() if k != "track"}, "coverage": cov,
        "track": [[round(r[1], 2), round(r[2], 2), round(r[3], 2)] for r in track[::5]],
        "_deposit_rows": rows,
    }


def run(ap: Path, which: str = "1ha", speedup: int = 10, wind: float = 1.6, wind_dir: float = 290.0,
        turb: float = 0.4, gps_noise: float = 0.5, drift_compensation: bool = True) -> Path:
    from .mission import build_mission, drone_spec, sortie_mission
    from .sorties import best_takeoff, load_sheet, plan_sorties
    from dataclasses import replace
    spec = drone_spec(None)
    field_, zones = _field(which)
    if zones is None:
        zones = ["A2", "B2"]
    home, _, _ = best_takeoff(field_, zones, spec)
    field_ = replace(field_, home=home)
    # FieldPilot moves the lines upwind by the measured wind (fieldpilot/drift.py); the aircraft
    # flies the moved lines, the deposit is judged on the zones themselves
    fly_field, comp = field_, {"applied": False}
    if drift_compensation:
        from .drift import compensated_field
        fly_field, comp = compensated_field(field_, wind, wind_dir)
    m = build_mission(fly_field, zones, ["sitl"], spec)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runs" / "sitl" / stamp
    out.mkdir(parents=True, exist_ok=True)
    site = {"wind": wind, "wind_dir": wind_dir, "turb": turb, "gps_noise": gps_noise, "drift_compensation": comp}
    n = max(1, len(m.sorties))
    results = []
    for k in range(n):
        sm = sortie_mission(m, k)
        r = fly_sortie(ap, sm, spec, out / f"sortie_{k + 1}", field_.home, speedup, site)
        res = {"sortie": k + 1, "mission_id": sm.mission_id, "zones": sm.zones, "events": r["events"],
               "frame": r.get("frame")}
        if r["log"]:
            res["log"] = str(Path(r["log"]).relative_to(ROOT))
        results.append(res)
    return _finish(out, field_, zones, m, spec, site, results, ap, fly_field)


def _finish(out: Path, field_, zones, m, spec, site, results, ap, fly_field=None) -> Path:
    """Analyse every sortie's firmware log, then the job as a whole (coverage is a job property:
    each sortie sprays only part of it)."""
    from types import SimpleNamespace

    from .k3sim import coverage
    from .mission import sortie_mission
    from .sorties import load_sheet, plan_sorties
    n = max(1, len(m.sorties))
    rows_all, landed = [], {}
    for k, res in enumerate(results):
        if not res.get("log"):
            continue
        sm = sortie_mission(m, k) if n > 1 else m
        a = analyse(read_log(str(ROOT / res["log"])), field_, sm, spec, site=site, fly_field=fly_field)
        rows_all += a.pop("_deposit_rows", [])
        for z, v in (a.get("deposited_per_zone_l") or {}).items():
            landed[z] = landed.get(z, 0.0) + v
        res.update(a)
    job = {}
    if rows_all:
        cov = coverage(field_, zones, [SimpleNamespace(track=rows_all)], spec.swath_m)
        from .devices import planned_litres
        pl = planned_litres(field_, m, spec)
        job = {"coverage": cov, "coverage_min_pct": min(v["covered_pct"] for v in cov.values()),
               "deposited_l": {z: round(landed.get(z, 0.0), 3) for z in zones},
               "planned_l": {z: round(pl[z], 3) for z in zones},
               "dose_deviation_pct": {z: round((landed.get(z, 0.0) - pl[z]) / pl[z] * 100, 1) for z in zones},
               "litres": round(sum(r.get("litres", 0) for r in results), 3),
               "energy_wh": round(sum(r.get("energy_wh") or 0 for r in results), 1),
               "planned_energy_wh": round(sum(r.get("planned_wh") or 0 for r in results), 1),
               "flight_s": round(sum(r.get("flight_s") or 0 for r in results), 1),
               "planned_s": round(sum(r.get("planned_s") or 0 for r in results), 1),
               "ardupilot_model_s": round(sum((r.get("ardupilot_model") or {}).get("total_s", 0) for r in results), 1),
               "ardupilot_model_wh": round(sum((r.get("ardupilot_model") or {}).get("energy_wh", 0) for r in results), 1)}
    fw = _fw_version(ap) if ap else (json.loads((out / "sitl.json").read_text(encoding="utf-8")).get("firmware")
                                     if (out / "sitl.json").exists() else "ArduCopter (SITL)")
    rep = {"label": LABEL, "utc": out.name, "firmware": fw, "field": field_.name,
           "drift_compensation": site.get("drift_compensation"),
           "zones": zones, "aircraft": spec.name, "site": site, "sorties": results, "job": job,
           "sheet": load_sheet(plan_sorties(field_, zones, spec), spec)["rows"],
           "assumptions": {"mass": "each sortie at its mean mass (dry 10 + pack 4 + half its load); hover throttle "
                                   "from the T1 thrust curve, reference current x mass^1.5",
                           "deposit": "true position (SITL), moved downwind by fieldpilot/drift.py; 1.5 %/(m/s) airborne",
                           "pump": "sprayer servo 1100-1900 us = 0-3.5 L/min, lag 0.25 s, dead time 0.15 s",
                           "frame": "sim/t1_frame.json; tuning from ArduPilot's Callisto heavy-lift model"}}
    (out / "sitl.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (out / "SITL_REPORT.md").write_text(report_md(rep), encoding="utf-8")
    return out


def reanalyse(out: Path) -> Path:
    """Re-read a finished run's firmware logs (after an analysis change) without flying again."""
    from dataclasses import replace

    from .mission import build_mission, drone_spec
    from .sorties import best_takeoff
    out = out if out.is_absolute() else ROOT / out
    rep = json.loads((out / "sitl.json").read_text(encoding="utf-8"))
    spec = drone_spec(None)
    which = "demo" if "demo" in rep["field"].lower() else "1ha"
    field_, zones = _field(which)
    zones = rep["zones"]
    home, _, _ = best_takeoff(field_, zones, spec)
    field_ = replace(field_, home=home)
    fly_field = field_
    if (rep["site"].get("drift_compensation") or {}).get("applied"):
        from .drift import compensated_field
        fly_field, _ = compensated_field(field_, rep["site"]["wind"], rep["site"]["wind_dir"])
    m = build_mission(fly_field, zones, ["sitl"], spec)
    results = [{k: s[k] for k in ("sortie", "mission_id", "zones", "events", "frame", "log") if k in s}
               for s in rep["sorties"]]
    for res in results:                      # the run folder may have been moved or copied
        if res.get("log") and not (ROOT / res["log"]).exists():
            moved = out / Path(*Path(res["log"]).parts[3:])
            if moved.exists():
                res["log"] = str(moved.relative_to(ROOT)) if moved.is_relative_to(ROOT) else str(moved)
    return _finish(out, field_, zones, m, spec, rep["site"], results, None, fly_field)


def _fw_version(ap: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(ap), "describe", "--tags", "--always"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "ArduCopter (SITL)"
    except Exception:  # noqa: BLE001
        return "ArduCopter (SITL)"


def runs_list() -> list[dict]:
    """The saved firmware runs, newest first, for the /ops dashboard."""
    out = []
    for f in sorted((ROOT / "runs" / "sitl").glob("*/sitl.json"), reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        j = r.get("job") or {}
        out.append({"run": f.parent.name, "field": r.get("field"), "firmware": r.get("firmware"),
                    "drift_compensation": bool((r.get("drift_compensation") or {}).get("applied")),
                    "sorties": len(r.get("sorties", [])), "flight_s": j.get("flight_s"),
                    "planned_s": j.get("planned_s"), "ardupilot_model_s": j.get("ardupilot_model_s"),
                    "energy_wh": j.get("energy_wh"), "planned_energy_wh": j.get("planned_energy_wh"),
                    "coverage_min_pct": j.get("coverage_min_pct"),
                    "dose_deviation_pct": j.get("dose_deviation_pct"),
                    "on_target_pct": [s.get("deposited_on_target_pct") for s in r.get("sorties", [])]})
    return out


def run_view(name: str) -> dict:
    """One firmware run with the zones in the same local frame as its tracks (take-off at 0, 0)."""
    from dataclasses import replace

    from .mission import drone_spec
    from .sorties import best_takeoff
    f = ROOT / "runs" / "sitl" / name / "sitl.json"
    r = json.loads(f.read_text(encoding="utf-8"))
    spec = drone_spec(None)
    field_, _ = _field("demo" if "demo" in r["field"].lower() else "1ha")
    home, _, _ = best_takeoff(field_, r["zones"], spec)
    field_ = replace(field_, home=home)
    fr = field_.frame
    r["zone_xy"] = {z.zone_id: [list(fr.to_local(a, b)) for a, b in z.polygon] for z in field_.zones}
    comp = r.get("drift_compensation") or {}
    if comp.get("applied"):
        from .drift import compensated_field
        moved, _ = compensated_field(field_, r["site"]["wind"], r["site"]["wind_dir"])
        r["flown_zone_xy"] = {z.zone_id: [list(fr.to_local(a, b)) for a, b in z.polygon] for z in moved.zones}
    return r


def report_md(r: dict) -> str:
    L = [f"# The T1 on ArduPilot firmware (SITL) · {r['utc']}", "",
         f"**{r['label']}.** Unmodified ArduCopter ({r['firmware']}) flew FieldPilot's sorties on a physics model "
         "of the T1. Every number is the firmware's own log, read by FieldPilot. Not a field flight.", "",
         f"- Field: {r['field']} · zones {', '.join(r['zones'])} · {r['aircraft']}",
         f"- Site: wind {r['site']['wind']} m/s from {r['site']['wind_dir']}°, turbulence {r['site']['turb']}, "
         f"GPS noise {r['site']['gps_noise']} m",
         f"- Drift compensation: " + (f"lines moved {r['drift_compensation']['upwind_offset_m']} m upwind"
                                      if (r.get('drift_compensation') or {}).get('applied') else "off"), "",
         "| Sortie | Mass kg | Flight s (plan) | Take-off+transit / spraying / return+land s | Energy Wh (plan) | Pack left | Min volts | Litres (plan) | Spray speed | Line error, true median / p95 | Deposited on target |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in r["sorties"]:
        if "flight_s" not in s:
            L.append(f"| {s['sortie']} | failed: {s.get('error') or s['events'][-1]} ||||||||")
            continue
        ph = s["phases"]
        L.append(f"| {s['sortie']} | {(s.get('frame') or {}).get('mass')} | {s['flight_s']} ({s['planned_s']}) | "
                 f"{ph['takeoff_and_transit_s']} / {ph['spraying_s']} / {ph['return_and_land_s']} | "
                 f"{s['energy_wh']} ({s['planned_wh']}) | "
                 f"{s['battery_left_pct']} % | {s['min_voltage_v']} | {s['litres']} ({s['planned_litres']}) | "
                 f"{s['mean_spray_speed_ms']} m/s | {s['xte_true_m']['median']} / {s['xte_true_m']['p95']} m | "
                 f"{s['deposited_on_target_pct']} % |")
    L += ["", "## FieldPilot's ArduPilot timing model against the firmware", "",
          "Predicted from the parameters the aircraft flies with (S-curves at WPNAV_ACCEL and WPNAV_JERK, "
          "RTL_ALT, RTL_LOIT_TIME, LAND_SPEED; `fieldpilot/ap_timing.py`), not fitted to these logs.", "",
          "| Sortie | Take-off+transit s (model) | Spraying s (model) | Return+land s (model) | Total s (model) | Energy Wh (model) |",
          "|---|---|---|---|---|---|"]
    for s in r["sorties"]:
        apm, ph = s.get("ardupilot_model"), s.get("phases")
        if not apm or not ph:
            continue
        L.append(f"| {s['sortie']} | {ph['takeoff_and_transit_s']} ({apm['takeoff_and_transit_s']}) | "
                 f"{ph['spraying_s']} ({apm['spraying_s']}) | {ph['return_and_land_s']} ({apm['return_and_land_s']}) | "
                 f"{s['flight_s']} ({apm['total_s']}) | {s['energy_wh']} ({apm['energy_wh']}) |")
    j = r.get("job") or {}
    if j:
        L += ["", "## The whole job (coverage is a job property: each sortie sprays only part of it)", "",
              f"- Flight time {j['flight_s']} s (plan {j['planned_s']} s); energy {j['energy_wh']} Wh "
              f"(plan {j['planned_energy_wh']} Wh); litres {j['litres']}",
              f"- ArduPilot timing model: {j.get('ardupilot_model_s')} s, {j.get('ardupilot_model_wh')} Wh",
              f"- Zone coverage under the swath, after drift: lowest **{j['coverage_min_pct']} %** "
              f"({', '.join(f'{z} {v[chr(99)+'overed_pct']} %' for z, v in j['coverage'].items())})",
              "- Deposited per zone (planned): " + ", ".join(
                  f"{z} {j['deposited_l'][z]} L ({j['planned_l'][z]}, {j['dose_deviation_pct'][z]:+} %)"
                  for z in j["deposited_l"])]
    L += ["", "## Assumptions", ""] + [f"- **{k}**: {v}" for k, v in r["assumptions"].items()]
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fieldpilot sitl")
    ap.add_argument("--ardupilot", default=os.environ.get("FP_ARDUPILOT", ""),
                    help="ArduPilot source tree with build/sitl/bin/arducopter built")
    ap.add_argument("--field", default="1ha", choices=["1ha", "demo"])
    ap.add_argument("--speedup", type=int, default=10)
    ap.add_argument("--wind", type=float, default=1.6)
    ap.add_argument("--wind-dir", type=float, default=290.0)
    ap.add_argument("--turb", type=float, default=0.4)
    ap.add_argument("--gps-noise", type=float, default=0.5)
    ap.add_argument("--reanalyse", help="a runs/sitl/<time> folder: read its logs again, no flying")
    ap.add_argument("--no-drift-compensation", action="store_true")
    a = ap.parse_args(argv)
    if a.reanalyse:
        out = reanalyse(Path(a.reanalyse))
        print((out / "SITL_REPORT.md").read_text(encoding="utf-8"))
        return 0
    path = Path(a.ardupilot).expanduser()
    if not (path / "build" / "sitl" / "bin" / "arducopter").exists():
        print("build ArduCopter SITL first: git clone ArduPilot, then ./waf configure --board sitl && ./waf copter")
        return 2
    out = run(path, a.field, a.speedup, a.wind, a.wind_dir, a.turb, a.gps_noise,
              drift_compensation=not a.no_drift_compensation)
    print((out / "SITL_REPORT.md").read_text(encoding="utf-8"))
    print(f"-> {out.relative_to(ROOT)}")
    return 0
