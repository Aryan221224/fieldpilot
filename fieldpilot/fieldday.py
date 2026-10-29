"""A whole operator day, simulated end to end on the real system: the field-day twin.

    python -m fieldpilot.cli fieldday                       # India demo, tomorrow
    python -m fieldpilot.cli fieldday --date 2026-10-30 --seed 7
    python -m fieldpilot.cli fieldday --synthetic-weather   # no network: labelled climatology

Everything FieldPilot does in a day runs on the production code; only the world is simulated:

1. **Bookings** (examples/desk_demo_in.json) go through the real intake and its number
   checks. The reading is done by the rehearsal stand-in, not Nemotron.
2. **The day plan**: the real day planner on the hourly forecast for every farm (Open-Meteo
   if reachable, otherwise a labelled climatological day for late October in western UP).
3. **The air over each field** differs from the forecast, as it does: a per-farm bias and
   minute-to-minute turbulence. **FieldMast** measures it (2-minute mean wind, 3-second gust,
   temperature, RH) and posts every 10 s; FieldPilot's approval gate holds a sortie while the
   mast says NO-GO, and gives up on the farm after 30 minutes.
4. **Each farm's mission** is planned by the real sortie planner on the zones the scan
   justified, and each approved sortie is **flown by the K3-A Pro twin** (fieldpilot/k3sim.py):
   the controller's line pattern, flow-follows-speed, pump dynamics, low-liquid breakpoint
   and resume, the T1's energy model.
5. **SprayLog** records each sortie the way the device would: GPS with u-blox-class error
   (a slowly wandering bias plus jitter), the flow meter's calibration error and pulse
   counting. The uploads go through the real DeviceHub, which works out the as-applied
   litres per zone against the plan.
6. **The battery board** follows each pack: flying, then on the charger, then charged.

Output: ``runs/fieldday/<date>/`` with ``FIELD_DAY_REPORT.md``, ``fieldday.json`` (every
number in the report), the device data exactly as the devices would have posted it
(``devices/``), and per-farm as-applied reports. Every file is labelled
**SIMULATED field day**. It is a prediction of a day on the real system, not a record of one.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .paths import ROOT
LABEL = "SIMULATED field day (production code; simulated world, K3-A Pro twin, emulated devices)"


# ----------------------------------------------------------------------------- weather

def synthetic_hours(day: date, utc_offset_h: float, lat: float, lon: float, seed: int = 0):
    """A climatological late-October day in western Uttar Pradesh (labelled synthetic).

    Temperature 17 -> 31 °C (minimum at 06:00, maximum at 14:00); dew point ~14 °C, so RH runs
    from ~83 % at dawn to ~35 % mid-afternoon; wind 0.8 m/s at night rising to ~3.2 m/s at
    13:00. Typical of the season's IMD normals around Delhi; small per-farm variation.
    """
    from .sop import Hour
    rnd = random.Random(f"{seed}:{lat:.3f}:{lon:.3f}")
    tz = timezone(timedelta(hours=utc_offset_h))
    tmin, tmax = 17.0 + rnd.uniform(-0.8, 0.8), 31.0 + rnd.uniform(-1.0, 1.0)
    td = 14.0 + rnd.uniform(-1.0, 1.0)
    wpk = 3.2 + rnd.uniform(-0.4, 0.4)
    out = []
    # hourly on the whole UTC hour, as Open-Meteo serves it
    start = datetime.combine(day - timedelta(days=1), time(0), timezone.utc)
    for k in range(72):
        t = (start + timedelta(hours=k)).astimezone(tz)
        h = t.hour + t.minute / 60
        ph = (h - 6) / 8 * math.pi if 6 <= h <= 14 else (math.pi + (h - 14) / 16 * math.pi if h > 14
                                                       else math.pi + (h + 10) / 16 * math.pi)
        temp = tmin + (tmax - tmin) * (1 - math.cos(ph)) / 2 if 6 <= h <= 14 else \
            tmin + (tmax - tmin) * (1 + math.cos(ph - math.pi)) / 2
        es = lambda c: math.exp(17.625 * c / (243.04 + c))       # noqa: E731
        rh = max(15.0, min(98.0, 100 * es(td) / es(temp)))
        wind = 0.8 + (wpk - 0.8) * max(0.0, math.sin(math.pi * (h - 7) / 12)) if 7 <= h <= 19 else 0.8
        out.append(Hour(time=t.astimezone(timezone.utc), wind_ms=round(wind, 2), temp_c=round(temp, 1),
                        rh_pct=round(rh, 1), precip_mm=0.0, precip_prob_pct=0.0, wind_from_deg=300.0))
    return out


def _interp(fc, t: datetime) -> tuple[float, float, float]:
    """Wind, temperature, RH from the hourly forecast at t (linear)."""
    tu = t.astimezone(timezone.utc)
    for a, b in zip(fc, fc[1:]):
        if a.time <= tu <= b.time:
            f = (tu - a.time).total_seconds() / 3600.0
            return (a.wind_ms + f * (b.wind_ms - a.wind_ms), a.temp_c + f * (b.temp_c - a.temp_c),
                    a.rh_pct + f * (b.rh_pct - a.rh_pct))
    h = min(fc, key=lambda x: abs((x.time - tu).total_seconds()))
    return h.wind_ms, h.temp_c, h.rh_pct


class SiteAir:
    """The real air over one farm: forecast + a farm bias + AR(1) drift + 1-s turbulence."""

    def __init__(self, fc, rnd: random.Random):
        self.fc, self.rnd = fc, rnd
        self.bw, self.bt, self.bh = rnd.gauss(0, 0.35), rnd.gauss(0, 0.8), rnd.gauss(0, 4.0)
        self.d = 0.0
        # the true wind direction is the forecast's (spray hours) plus a per-farm error of 25 deg;
        # with no direction in the forecast: late-October western UP north-westerly, 300 deg
        dirs = [h.wind_from_deg for h in fc if getattr(h, "wind_from_deg", None) is not None
                and 6 <= h.time.astimezone(timezone(timedelta(hours=5, minutes=30))).hour <= 10]
        base = 300.0
        if dirs:
            base = math.degrees(math.atan2(sum(math.sin(math.radians(d)) for d in dirs),
                                           sum(math.cos(math.radians(d)) for d in dirs))) % 360.0
        self.wind_from_deg = (base + rnd.gauss(0, 25.0)) % 360.0
        self.last_mean = 0.0

    def second(self, t: datetime) -> tuple[float, float, float]:
        w, tc, rh = _interp(self.fc, t)
        self.d = 0.995 * self.d + self.rnd.gauss(0, 0.03)          # slow drift (minutes)
        mean = max(0.0, w + self.bw + self.d)
        self.last_mean = mean
        gusty = max(0.0, mean * (1 + self.rnd.gauss(0, 0.22)))     # turbulence intensity ~22 %
        return gusty, tc + self.bt + self.rnd.gauss(0, 0.1), min(100.0, max(5.0, rh + self.bh))


class Mast:
    """FieldMast firmware logic in Python (same window and gust as fieldpilot_logic.h)."""

    def __init__(self, device_id="MAST-01", rnd: random.Random | None = None):
        from collections import deque
        self.id = device_id
        self.buf = deque(maxlen=120)
        self.temp = self.rh = None
        # sensor error from the parts' sheets: cup anemometer worst case ±1 m/s (Adafruit 1733),
        # SHT31 ±2 % RH and ±0.3 °C. A per-instrument bias plus reading noise.
        self.rnd = rnd or random.Random(0)
        self.bw = self.rnd.gauss(0, 0.25)
        self.bt, self.bh = self.rnd.gauss(0, 0.2), self.rnd.gauss(0, 1.5)

    def add(self, w, tc, rh):
        self.buf.append(max(0.0, w + self.bw + self.rnd.gauss(0, 0.1)))
        self.temp = tc + self.bt
        self.rh = min(100.0, max(0.0, rh + self.bh + self.rnd.gauss(0, 0.3)))

    def reading(self) -> dict:
        b = list(self.buf)
        mean = sum(b) / len(b) if b else None
        gust = max(((b[i] + b[i - 1] + b[i - 2]) / 3 for i in range(2, len(b))), default=mean)
        return {"device_id": self.id, "fw": "fieldmast 1.0 (simulated)", "wind_ms": round(mean, 2) if b else None,
                "gust_ms": round(gust, 2) if gust is not None else None, "wind_samples": len(b),
                "temp_c": round(self.temp, 2) if self.temp is not None else None,
                "rh_pct": round(self.rh, 1) if self.rh is not None else None, "battery_v": 12.4, "rssi": -61}


# ----------------------------------------------------------------------------- SprayLog

def spraylog_session(sortie, frame, t0_local: datetime, rnd: random.Random, k_true=5880.0,
                     meter_err=0.0, k_cfg=5880.0, session_id="", device="SPRAYLOG-T1") -> dict:
    """The upload SprayLog would make for one K3 twin sortie (1 Hz samples)."""
    bx = by = 0.0
    pulses_true = 0.0
    rows = []
    last_t = -1.0
    for r in sortie.track:
        t, x, y, _alt, v, _spr, q, _tank, _wh, _z = r
        bx = 0.98 * bx + rnd.gauss(0, 0.24)           # u-blox M8N + GAGAN SBAS: bias wanders, σ ≈ 1.2 m
        by = 0.98 * by + rnd.gauss(0, 0.24)
        pulses_true += q / 60.0 * 0.2 * k_true * (1 + meter_err)
        if t - last_t >= 1.0 - 1e-6:
            last_t = t
            mx, my = x + bx + rnd.gauss(0, 0.5), y + by + rnd.gauss(0, 0.5)
            lat, lon = frame.to_wgs84(mx, my)
            litres = math.floor(pulses_true) / k_cfg
            rows.append([round(t, 1), round(lat, 7), round(lon, 7),
                         round(max(0.0, q * (1 + meter_err) * k_true / k_cfg * (1 + rnd.gauss(0, 0.03))), 3),
                         round(litres, 4), round(max(0.0, v + rnd.gauss(0, 0.05)), 2), 1])
    lt = math.floor(pulses_true) / k_cfg
    return {"device_id": device, "fw": "spraylog 1.0 (simulated)", "session_id": session_id,
            "k_pulses_per_l": k_cfg, "pulses": int(pulses_true), "litres_total": round(lt, 4),
            "sample_period_ms": 1000, "samples": rows, "started_local": t0_local.isoformat(timespec="seconds")}


# ----------------------------------------------------------------------------- the farm

def farm_field(job: dict, seed: int):
    """A 2:1 plot of the booked area at the farmer's pin, split into 4 x 2 zones, and the zones
    the scan justified (as many as cover the scanned area; all of them if not scanned)."""
    from .field import grid_field
    area_m2 = max(400.0, job["area_ha"] * 10_000)
    w = math.sqrt(area_m2 * 2)
    h = area_m2 / w
    f = grid_field(f"{job['farmer']} ({job['job_id']})", (job["lat"], job["lon"]), w, h, 4, 2,
                   home_offset_m=(-3.0, -3.0), fence_margin_m=5.0)
    zones = f.zone_ids()
    if job.get("spray_ha") is not None and job["spray_ha"] < job["area_ha"]:
        k = max(1, round(job["spray_ha"] / job["area_ha"] * len(zones)))
        rnd = random.Random(f"{seed}:{job['lat']:.5f}:{job['lon']:.5f}")
        zones = sorted(rnd.sample(zones, k))
    return f, zones


# ----------------------------------------------------------------------------- the day

def run(day: date | None = None, seed: int = 7, demo: str = "in", synthetic_weather: bool = False,
        out_root: Path | None = None, live_llm: bool = False, drift_compensation: bool = True) -> Path:
    from . import k3sim, rehearsal
    from .dayplan import JobSpec, Operator, compare
    from .desk import intake_messages, jobs_from
    from .devices import DeviceHub, as_applied, planned_litres
    from .mission import drone_spec
    from .sop import fetch_forecast
    from .sorties import GROUND_TURNAROUND_S, best_takeoff, load_sheet, plan_sorties

    src = json.loads((ROOT / "examples" / f"desk_demo_{demo}.json").read_text(encoding="utf-8"))
    op = Operator.from_dict(src["operator"])
    tz = op.tz
    day = day or (datetime.now(tz).date() + timedelta(days=1))
    rnd = random.Random(seed)
    out = (out_root or ROOT / "runs" / "fieldday") / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    hub = DeviceHub(out / "devices", clock=lambda: clock[0])
    clock = [datetime.combine(day, time(5, 0), tz).timestamp()]

    # 1. bookings through the real intake, read by FieldPilot's own Token Factory client: against
    #    Nebius with a key (live_llm), otherwise against the local stand-in endpoint (labelled)
    import dataclasses
    from .config import load
    from .desk import brief
    from .llm import TokenFactory
    cfg = load()
    standin = None
    if live_llm and cfg.live_llm and not cfg.rehearsal:
        llm_mode = "LIVE Nemotron on Nebius Token Factory"
    else:
        standin = rehearsal.StandIn().__enter__()
        cfg = dataclasses.replace(cfg, nebius_api_key="rehearsal-not-a-key", nebius_base_url=standin.url,
                                  agent_model="", vision_model="", fast_model="", vision_base_url="")
        llm_mode = rehearsal.LABEL
    try:
        llm = TokenFactory(cfg)
        models = llm.resolve() | {"fast": llm.model_for("intake")}
        rows = intake_messages(src["messages"], op, llm, geocoder=lambda *a, **k: [])
        for i, r in enumerate(rows):                  # stable ids, so a seed reproduces the day
            r["job_id"] = f"J-{i + 1:02d}"
        jobs, callback = jobs_from(rows)

        # 2. forecasts, the day plan, and the crew's brief
        fc, weather_src = {}, "open-meteo hourly forecast"
        for j in jobs:
            try:
                if synthetic_weather:
                    raise OSError("synthetic requested")
                fc[j.job_id] = fetch_forecast(j.lat, j.lon, days=3)
            except Exception:  # noqa: BLE001
                fc[j.job_id] = synthetic_hours(day, op.utc_offset_h, j.lat, j.lon, seed)
                weather_src = "SYNTHETIC climatological day (late Oct, western UP) - Open-Meteo not reachable"
        plan = compare(jobs, op, day, fc)
        fp = plan["fieldpilot"]
        day_brief = brief(fp, llm, op.language)
        token_use = llm.usage()
    finally:
        if standin is not None:
            standin.__exit__(None, None, None)
    intake_checks = {"jobs_read": len(rows), "schema_constrained": sum(1 for r in rows if r.get("protocol") == "json_schema"),
                     "flags_for_the_call": sum(len(r.get("confirm") or []) for r in rows),
                     "areas_accepted": sum(1 for r in rows if r.get("area_ha"))}

    # 3-6. execute the plan in the simulated world
    spec = drone_spec(op.drone)
    packs_ready = {i + 1: datetime.combine(day, time(5, 0), tz) for i in range(op.battery_sets)}
    charger_free = [datetime.combine(day, time(5, 0), tz)] * op.chargers
    for i in packs_ready:
        clock[0] = packs_ready[i].timestamp()
        hub.pack_event(str(i), "charged", "night charge")
    meter_err = rnd.gauss(0, 0.02)           # drone flowmeter ±10 % raw; after the jug calibration ±2 %
    crew = random.Random(f"{seed}:crew")
    by_id = {j.job_id: j for j in jobs}
    now = datetime.combine(day, time.fromisoformat(src["operator"].get("day_start", "06:00")), tz)
    pos = tuple(op.base)
    farms, timeline, mast_minutes, holds = [], [], [], []
    session_n = 0
    for row in fp["plan"]:
        j = by_id[row["job_id"]]
        if j.farmer == j.job_id and j.village:
            j = replace(j, farmer=f"{j.village} farm")
        jd = {"job_id": j.job_id, "farmer": j.farmer, "lat": j.lat, "lon": j.lon,
              "area_ha": j.area_ha, "spray_ha": j.spray_ha}
        km = math.dist(pos, (j.lat, j.lon)) * 111.0 * op.road_factor
        now += timedelta(minutes=km / op.road_kmh * 60 * crew.uniform(0.9, 1.25))   # village roads
        arrive = now
        now += timedelta(minutes=max(8.0, crew.gauss(op.setup_min, 4.0)))           # unload, mast up, brief
        pos = (j.lat, j.lon)
        f, zones = farm_field(jd, seed)
        home, _, _ = best_takeoff(f, zones, spec)
        f = replace(f, home=home)
        parts = plan_sorties(f, zones, spec)
        sheet = load_sheet(parts, spec)
        air = SiteAir(fc[j.job_id], random.Random(f"{seed}:air:{j.lat:.5f}:{j.lon:.5f}"))
        mast = Mast(rnd=random.Random(f"{seed}:mast"))
        tw = None                       # built at the first approval, when the mast has measured
        fly_field, comp = f, {"applied": False}
        flown, sessions, farm_holds = [], [], []
        mast_t = arrive
        gave_up = None

        def advance_air(until: datetime):
            nonlocal mast_t
            while mast_t < until:
                w, tc, rh = air.second(mast_t)
                mast.add(w, tc, rh)
                if int(mast_t.timestamp()) % 10 == 0:
                    clock[0] = mast_t.timestamp()
                    r = hub.ingest_mast(mast.reading() | {"lat": f.home[0], "lon": f.home[1]}, op.pack.pack_id)
                    if int(mast_t.timestamp()) % 60 == 0:
                        mast_minutes.append({"t": mast_t.strftime("%H:%M"), "job": j.job_id,
                                             "wind_ms": r["wind_ms"], "gust_ms": r["gust_ms"],
                                             "temp_c": r["temp_c"], "rh_pct": r["rh_pct"], "ok": r["ok"],
                                             "forecast_wind_ms": round(_interp(fc[j.job_id], mast_t)[0], 2)})
                mast_t += timedelta(seconds=1)

        planned_starts = [datetime.combine(day, time.fromisoformat(s["start"]), tz) for s in row["sorties"]]
        k = 0
        while k < len(parts):
            load = sheet["rows"][k]["water_l"]
            ps = planned_starts[k] if k < len(planned_starts) else now
            bset = row["sorties"][k]["battery_set"] if k < len(row["sorties"]) else \
                min(packs_ready, key=packs_ready.get)
            start = max(now, ps, packs_ready[bset])
            advance_air(start)
            # approval gate: the forecast window is the planner's; the mast is the field's
            waited = 0
            while True:
                clock[0] = start.timestamp()
                gate = hub.site_gate(near=f.home)
                in_window = op.pack.ok(min(fc[j.job_id], key=lambda h: abs((h.time - start).total_seconds())),
                                       op.utc_offset_h)
                if gate["ok"] is True and in_window:
                    break
                if waited >= 30:
                    gave_up = {"at": start.strftime("%H:%M"), "sortie": k + 1,
                               "reasons": gate["reasons"] if gate["ok"] is False else ["outside the rule window"]}
                    break
                farm_holds.append({"at": start.strftime("%H:%M"), "sortie": k + 1,
                                   "reasons": gate["reasons"] if gate["ok"] is False else
                                   ["outside the rule window"]})
                start += timedelta(minutes=2)
                waited += 2
                advance_air(start)
            if gave_up:
                break
            # fly it
            clock[0] = start.timestamp()
            hub.pack_event(str(bset), "flying", f"{j.job_id} sortie {k + 1}")
            if tw is None:
                # drift compensation from the mast's measured speed and the forecast's direction
                if drift_compensation:
                    from .drift import compensated_field
                    r = gate.get("reading") or {}
                    h = min(fc[j.job_id], key=lambda x: abs((x.time - start).total_seconds()))
                    fly_field, comp = compensated_field(f, r.get("wind_ms") or 0.0, h.wind_from_deg)
                tw = k3sim.K3Twin(fly_field, zones, spec)
            so = tw.fly(load, k + 1)
            flown.append(so)
            land = start + timedelta(seconds=so.flight_s)
            advance_air(land)
            session_n += 1
            sid = f"SPRAYLOG-T1-SIM-{day.strftime('%Y%m%d')}-{session_n:02d}"
            gps_seed = f"{seed}:gps:{session_n}"
            body = spraylog_session(so, f.frame, start, random.Random(gps_seed),
                                    meter_err=meter_err, session_id=sid)
            clock[0] = (land + timedelta(seconds=75)).timestamp()     # uploads once landed and still
            hub.ingest_spraylog(body, run_id=j.job_id)
            sessions.append(body)
            # battery: onto the charger (the queue), charged after charge_min (± 3 min)
            ci = min(range(len(charger_free)), key=lambda i: charger_free[i])
            c0 = max(land, charger_free[ci])
            ready = c0 + timedelta(minutes=op.charge_min + random.Random(f"{seed}:chg:{session_n}").gauss(0, 3))
            charger_free[ci] = ready
            packs_ready[bset] = ready
            clock[0] = c0.timestamp()
            hub.pack_event(str(bset), "charging", f"after {j.job_id} sortie {k + 1}")
            clock[0] = ready.timestamp()
            hub.pack_event(str(bset), "charged", "")
            timeline.append({"job_id": j.job_id, "farmer": j.farmer, "sortie": k + 1,
                             "planned_start": ps.strftime("%H:%M"), "actual_start": start.strftime("%H:%M"),
                             "landed": land.strftime("%H:%M:%S"), "battery_set": bset,
                             "k3_end": so.end, "flight_s": so.flight_s, "planned_flight_s": round(parts[k].est_time_s),
                             "load_l": load, "sprayed_l": so.sprayed_l, "left_l": so.left_in_tank_l,
                             "battery_left_pct": so.battery_left_pct,
                             "held_min": sum(2 for h in farm_holds if h["sortie"] == k + 1)})
            # refill, swap pack, check nozzles: 3 min planned, 2-6 min in practice
            now = land + timedelta(seconds=crew.triangular(120.0, 360.0, GROUND_TURNAROUND_S))
            k += 1
            if tw.done:
                break
        holds += [h | {"job_id": j.job_id} for h in farm_holds]
        # the logger is judged against the lines the drone was given (moved upwind if compensated);
        # what landed is judged against the zones themselves, after the real drift
        rep = as_applied(sessions_from_hub(hub, j.job_id), fly_field,
                         planned_litres(fly_field, _Mission(zones), spec)) if sessions else None
        drift = _drift(air.last_mean, air.wind_from_deg, f.treat_alt_m)
        truth = _truth_per_zone(f, zones, flown, drift) if flown else {}
        cov = k3sim.coverage(f, zones, _drifted(flown, drift), spec.swath_m) if flown else {}
        cmp = k3sim.compare_with_plan(fly_field, plan_sorties(fly_field, zones, spec) if comp.get("applied")
                                      else parts, flown) if flown else None
        no_comp = None
        if flown and comp.get("applied"):
            # the same sorties without compensation, for the comparison
            fl0 = k3sim.fly_card(f, zones, spec, [sheet["rows"][i]["water_l"] for i in range(len(flown))])
            t0 = _truth_per_zone(f, zones, fl0, drift)
            c0 = k3sim.coverage(f, zones, _drifted(fl0, drift), spec.swath_m)
            no_comp = {"deposited_on_target_pct": t0.get("_on_target_pct"),
                       "coverage_min_pct": min(v["covered_pct"] for v in c0.values()),
                       "litres_off_zones": round(sum(v for k2, v in t0.items()
                                                     if not k2.startswith("_") and k2 not in zones), 3)}
        farms.append({"job_id": j.job_id, "farmer": j.farmer, "village": row.get("village"),
                      "arrive": arrive.strftime("%H:%M"), "area_ha": j.area_ha, "spray_ha": j.spray_ha,
                      "zones": zones, "planned_sorties": len(parts), "flown_sorties": len(flown),
                      "completed": bool(flown) and tw.done, "gave_up": gave_up,
                      "planned_l": round(sum(p.spray_ml for p in parts) / 1000, 3),
                      "loaded_l": round(sum(sheet["rows"][i]["water_l"] for i in range(len(flown))), 3),
                      "sprayed_l_true": round(sum(s.sprayed_l for s in flown), 3),
                      "left_in_tank_l": round(sum(s.left_in_tank_l for s in flown), 3),
                      "truth_per_zone_l": truth, "coverage": cov, "k3_vs_plan": cmp,
                      "drift_compensation": comp, "without_compensation": no_comp,
                      "litres_off_zones": round(sum(v for k2, v in truth.items()
                                                    if not k2.startswith("_") and k2 not in zones), 3) if truth else None,
                      "as_applied": {k2: v for k2, v in (rep or {}).items() if k2 != "track"},
                      "track": (rep or {}).get("track", [])[:1500],
                      "zone_polygons": {z: f.zone(z).polygon for z in f.zone_ids()}, "home": f.home})
        (out / f"as_applied_{j.job_id}.json").write_text(json.dumps(farms[-1], ensure_ascii=False, indent=1),
                                                          encoding="utf-8")

    summary = _summary(fp, plan["baseline"], farms, timeline, holds, mast_minutes, meter_err)
    res = {"label": LABEL, "day": day.isoformat(), "seed": seed, "operator": op.name,
           "rules": op.pack.pack_id, "weather_source": weather_src, "aircraft": spec.name,
           "flight_controller": "JIYI K3-A Pro (behavioural twin, fieldpilot/k3sim.py)",
           "intake": {"reader": llm_mode, "jobs": len(jobs), "callback": callback} | intake_checks,
           "token_factory": {"mode": llm_mode, "models": models, "usage": token_use,
                             "brief": day_brief},
           "plan": {"fieldpilot": fp, "baseline_metrics": plan["baseline"]["metrics"]},
           "timeline": timeline, "holds": holds, "farms": farms, "mast_minutes": mast_minutes,
           "packs": hub.packs_view(), "summary": summary,
           "assumptions": {"site_air": "forecast + per-farm bias (wind σ 0.35 m/s, temp σ 0.8 °C, RH σ 4 %) "
                                       "+ slow drift + 22 % turbulence; wind from ~300° (NW) ± 25° per farm",
                           "mast_sensors": "anemometer bias σ 0.25 m/s + 0.1 m/s noise (sheet worst case ±1 m/s); "
                                           "SHT31 bias σ 0.2 °C / 1.5 % RH",
                           "drift": "deposit shifted downwind by release height x wind / 3 m/s (downwash); "
                                    "1.5 % per m/s stays airborne",
                           "crew": "drive x 0.9-1.25, setup 15 ± 4 min, refill and swap 2-6 min (3 planned)",
                           "spraylog": "u-blox M8N + GAGAN: GPS bias random walk σ≈1.2 m + 0.5 m jitter; flow meter "
                                       f"jug-calibrated to {meter_err * 100:+.1f} % with 3 % reading noise",
                           "charging": f"{op.charge_min} min ± 3 min per pack, one charger queue",
                           "k3": "see fieldpilot/k3sim.py K3Params"}}
    (out / "fieldday.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "FIELD_DAY_REPORT.md").write_text(report_md(res), encoding="utf-8")
    return out


class _Mission:
    def __init__(self, zones):
        self.zones = zones


def sessions_from_hub(hub, job_id: str) -> list[dict]:
    return hub.sessions_for(job_id)


def _drift(wind_ms: float, wind_from_deg: float, release_m: float) -> dict:
    """Where the spray lands relative to the nozzles, and how much never lands in the field.

    Droplets leave the nozzles about ``release_m`` above the crop and are driven down mostly by
    the rotor downwash (~3 m/s effective for a 24 kg quad, assumption), so in a crosswind
    the deposit shifts downwind by release x wind / 3. A small fine fraction stays airborne:
    taken as 1.5 % per m/s of wind (assumption, consistent with ground-boom drift classes).
    """
    shift = release_m * wind_ms / 3.0
    to_rad = math.radians((wind_from_deg + 180.0) % 360.0)       # blowing towards
    return {"wind_ms": round(wind_ms, 2), "from_deg": round(wind_from_deg),
            "dx": shift * math.sin(to_rad), "dy": shift * math.cos(to_rad),
            "shift_m": round(shift, 2), "airborne_loss": min(0.2, 0.015 * wind_ms)}


def _drifted(flown, drift):
    from types import SimpleNamespace
    return [SimpleNamespace(track=[[r[0], r[1] + drift["dx"], r[2] + drift["dy"]] + r[3:] for r in so.track])
            for so in flown]


def _truth_per_zone(f, zones, flown, drift=None) -> dict:
    """Litres that actually landed on each zone: the twin's flow at the nozzles, moved
    downwind by the drift shift, less the airborne fraction (no sensor error)."""
    from shapely.geometry import Point, Polygon
    fr = f.frame
    polys = {z.zone_id: Polygon([fr.to_local(a, b) for a, b in z.polygon]) for z in f.zones}
    fence = Polygon([fr.to_local(a, b) for a, b in f.geofence])
    dx, dy = (drift["dx"], drift["dy"]) if drift else (0.0, 0.0)
    loss = drift["airborne_loss"] if drift else 0.0
    out = {}
    for so in flown:
        for r in so.track:
            q = r[6]
            if q <= 0:
                continue
            dl = q / 60.0 * 0.2
            out["airborne (drift)"] = out.get("airborne (drift)", 0.0) + dl * loss
            pt = Point(r[1] + dx, r[2] + dy)
            hit = next((z for z, p in polys.items() if p.contains(pt)), None)
            if hit is None:
                hit = "field, between zones" if fence.contains(pt) else "outside the field"
            out[hit] = out.get(hit, 0.0) + dl * (1 - loss)
    out = {k: round(v, 3) for k, v in out.items()}
    landed_on = sum(v for k, v in out.items() if k in zones)
    total = sum(out.values())
    out["_on_target_pct"] = round(100 * landed_on / total, 1) if total else None
    if drift:
        out["_drift"] = {k: drift[k] for k in ("wind_ms", "from_deg", "shift_m", "airborne_loss")}
    return out


def _summary(fp, bl, farms, timeline, holds, mast_minutes, meter_err) -> dict:
    served = [x for x in farms if x["completed"]]
    planned_l = sum(x["planned_l"] for x in farms)
    true_l = sum(x["sprayed_l_true"] for x in farms)
    measured = sum((x["as_applied"] or {}).get("litres_total", 0) for x in farms)
    on_target = [x["as_applied"].get("on_target_pct") for x in farms if x.get("as_applied")]
    covs = [v["covered_pct"] for x in farms for v in (x["coverage"] or {}).values()]
    dep = [x["truth_per_zone_l"].get("_on_target_pct") for x in farms if x.get("truth_per_zone_l")]
    shifts = [x["truth_per_zone_l"]["_drift"]["shift_m"] for x in farms
              if (x.get("truth_per_zone_l") or {}).get("_drift")]
    cut = [c["distance_m"] for x in farms if x["k3_vs_plan"] for c in x["k3_vs_plan"]["cuts"]
           if c["distance_m"] is not None]
    delays = []
    for t in timeline:
        a = datetime.strptime(t["actual_start"], "%H:%M")
        p = datetime.strptime(t["planned_start"], "%H:%M")
        delays.append((a - p).total_seconds() / 60)
    ftime = [(t["flight_s"], t["planned_flight_s"]) for t in timeline]
    nogo = sum(1 for m in mast_minutes if m["ok"] is False)
    zone_dev = [z["deviation_pct"] for x in farms if x.get("as_applied") for z in x["as_applied"].get("zones", [])
                if z["deviation_pct"] is not None]
    return {
        "farms_planned": len(fp["plan"]), "farms_completed": len(served),
        "farms_deferred_by_plan": len(fp["deferred"]),
        "farms_abandoned_on_site": [x["job_id"] for x in farms if x["gave_up"]],
        "sorties_flown": len(timeline),
        "sorties_planned_by_day_plan": sum(len(r["sorties"]) for r in fp["plan"]),
        "litres_planned": round(planned_l, 2), "litres_sprayed_true": round(true_l, 2),
        "litres_measured_by_spraylog": round(measured, 2),
        "spraylog_error_pct": round((measured - true_l) / true_l * 100, 2) if true_l else None,
        "flow_meter_calibration_error_pct": round(meter_err * 100, 2),
        "on_target_pct_min": min(on_target) if on_target else None,
        "deposited_on_target_pct_min": min(d for d in dep if d is not None) if dep else None,
        "drift_shift_m_range": [min(shifts), max(shifts)] if shifts else None,
        "drift_compensation": [{"farm": x["farmer"], "offset_m": x["drift_compensation"].get("upwind_offset_m"),
                                "with": {"deposited_on_target_pct": x["truth_per_zone_l"].get("_on_target_pct"),
                                         "coverage_min_pct": min(v["covered_pct"] for v in x["coverage"].values()),
                                         "litres_off_zones": x["litres_off_zones"]},
                                "without": x["without_compensation"]}
                               for x in farms if (x.get("drift_compensation") or {}).get("applied")
                               and x.get("without_compensation")],
        "zone_dose_deviation_pct_range": [min(zone_dev), max(zone_dev)] if zone_dev else None,
        "zone_coverage_pct_min": min(covs) if covs else None,
        "k3_breakpoint_vs_planned_cut_m_max": max(cut) if cut else None,
        "start_delay_min": {"mean": round(sum(delays) / len(delays), 1) if delays else None,
                            "max": round(max(delays), 1) if delays else None},
        "flight_time_vs_plan_pct": round((sum(a for a, _ in ftime) / sum(b for _, b in ftime) - 1) * 100, 1)
        if ftime else None,
        "mast_holds": len(holds), "mast_minutes_nogo": nogo, "mast_minutes": len(mast_minutes),
        "plan_vs_common_way": {k: (fp["metrics"].get(k), bl["metrics"].get(k)) for k in
                               ("farms_served_in_window", "effective_ha_per_battery_cycle",
                                "out_of_window_sorties", "sprayed_beyond_need_ha", "leftover_mix_l")},
    }


def report_md(r: dict) -> str:
    s = r["summary"]
    L = [f"# Field day · {r['day']} · {r['operator']}", "",
         f"**{r['label']}.** Every number below comes from FieldPilot's production code running a day in "
         "a simulated world. Nothing here is a field measurement; it is what the real day should look "
         "like, and the yardstick for the real one.", "",
         f"- Aircraft: {r['aircraft']} · controller: {r['flight_controller']}",
         f"- Rules: {r['rules']} · weather: {r['weather_source']}",
         f"- Bookings read: {r['intake']['jobs']} schedulable, {len(r['intake']['callback'])} need a call-back "
         f"({r['intake']['reader']})", "",
         "## The day", "",
         "| | |", "|---|---|",
         f"| Farms planned / completed | {s['farms_planned']} / {s['farms_completed']} |",
         f"| Abandoned on site (mast NO-GO > 30 min) | {', '.join(s['farms_abandoned_on_site']) or 'none'} |",
         f"| Sorties flown (day plan's estimate) | {s['sorties_flown']} ({s['sorties_planned_by_day_plan']}) |",
         f"| Start delay vs plan, mean / max | {s['start_delay_min']['mean']} / {s['start_delay_min']['max']} min |",
         f"| Flight time vs mission plan | {s['flight_time_vs_plan_pct']} % |",
         f"| Mast holds before take-off | {s['mast_holds']} (NO-GO in {s['mast_minutes_nogo']} of {s['mast_minutes']} on-site minutes) |",
         f"| Litres planned / actually sprayed / measured by SprayLog | {s['litres_planned']} / {s['litres_sprayed_true']} / {s['litres_measured_by_spraylog']} L |",
         f"| SprayLog vs truth | {s['spraylog_error_pct']} % (meter calibration {s['flow_meter_calibration_error_pct']} %) |",
         f"| On target by the logger's GPS (lowest farm) | {s['on_target_pct_min']} % |",
         f"| Actually deposited on target, after drift (lowest farm) | {s['deposited_on_target_pct_min']} % (deposit shifted {s['drift_shift_m_range']} m downwind) |",
         f"| Zone dose deviation, measured | {s['zone_dose_deviation_pct_range']} % |",
         f"| Zone coverage under the swath (lowest) | {s['zone_coverage_pct_min']} % |",
         f"| K3 breakpoint vs planned cut (worst) | {s['k3_breakpoint_vs_planned_cut_m_max']} m |", "",
         "## Sorties", "", "| Farm | # | Planned | Actual | Held | Battery set | K3 end | Flight s (plan) | Load L | Sprayed L | Left L | Pack left |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in r["timeline"]:
        L.append(f"| {t['farmer']} | {t['sortie']} | {t['planned_start']} | {t['actual_start']} | {t['held_min']} min | "
                 f"{t['battery_set']} | {t['k3_end']} | {t['flight_s']:.0f} ({t['planned_flight_s']}) | {t['load_l']} | "
                 f"{t['sprayed_l']:.2f} | {t['left_l']:.2f} | {t['battery_left_pct']} % |")
    L += ["", "## As applied, per farm (SprayLog through FieldPilot)", "",
          "| Farm | Zones | Planned L | Measured L | On target | Outside field | Flags |", "|---|---|---|---|---|---|---|"]
    for x in r["farms"]:
        a = x.get("as_applied") or {}
        L.append(f"| {x['farmer']} | {', '.join(x['zones'])} | {x['planned_l']} | {a.get('litres_total', '-')} | "
                 f"{a.get('on_target_pct', '-')} % | {a.get('litres_outside_field', '-')} L | "
                 f"{'; '.join(a.get('flags') or []) or 'none'} |")
    if s.get("drift_compensation"):
        L += ["", "## Drift compensation (lines moved upwind by the measured wind)", "",
              "| Farm | Offset | Deposited on target, with / without | Lowest zone coverage, with / without | Litres landing off the zones, with / without |",
              "|---|---|---|---|---|"]
        for d in s["drift_compensation"]:
            w, o = d["with"], d["without"]
            L.append(f"| {d['farm']} | {d['offset_m']} m | {w['deposited_on_target_pct']} % / {o['deposited_on_target_pct']} % | "
                     f"{w['coverage_min_pct']} % / {o['coverage_min_pct']} % | {w['litres_off_zones']} / {o['litres_off_zones']} L |")
    if r["holds"]:
        L += ["", "## Mast holds", ""] + [f"- {h['job_id']} {h['at']} sortie {h['sortie']}: {'; '.join(h['reasons'])}"
                                          for h in r["holds"][:30]]
    tf = r.get("token_factory") or {}
    if tf:
        u = tf["usage"]
        L += ["", "## Token Factory", "",
              f"- Endpoint: **{tf['mode']}**",
              f"- Models discovered: agent `{tf['models'].get('agent')}`, vision `{tf['models'].get('vision')}`, "
              f"bookings and brief `{tf['models'].get('fast')}`",
              f"- Bookings read: {r['intake']['jobs_read']} ({r['intake']['schema_constrained']} schema-constrained), "
              f"{r['intake']['areas_accepted']} areas accepted by the number check, "
              f"{r['intake']['flags_for_the_call']} flags for the operator's call",
              f"- Calls today: {u['calls']} ({u['calls_by_purpose']}), {u['prompt_tokens']:,} + "
              f"{u['completion_tokens']:,} tokens, about **${u['usd_estimate']}** at each model's list price",
              f"- Crew brief ({tf['brief'].get('by')}): {tf['brief'].get('text', '')[:400]}"]
    pv = s["plan_vs_common_way"]
    L += ["", "## The plan against the common way (same bookings, forecast, batteries)", "",
          "| Metric | FieldPilot | Common way |", "|---|---|---|"]
    L += [f"| {k} | {a} | {b} |" for k, (a, b) in pv.items()]
    L += ["", "## Assumptions", ""] + [f"- **{k}**: {v}" for k, v in r["assumptions"].items()]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fieldpilot fieldday")
    ap.add_argument("--date")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--demo", default="in")
    ap.add_argument("--synthetic-weather", action="store_true")
    ap.add_argument("--no-drift-compensation", action="store_true")
    ap.add_argument("--live", action="store_true", help="read bookings and write the brief with live "
                    "Nemotron on Token Factory (needs NEBIUS_API_KEY); default: the labelled stand-in")
    a = ap.parse_args(argv)
    out = run(date.fromisoformat(a.date) if a.date else None, a.seed, a.demo, a.synthetic_weather,
              live_llm=a.live, drift_compensation=not a.no_drift_compensation)
    print((out / "FIELD_DAY_REPORT.md").read_text(encoding="utf-8"))
    print(f"-> {out.relative_to(ROOT)}")
    return 0
