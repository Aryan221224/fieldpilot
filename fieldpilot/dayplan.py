"""The operator's day: which farms, in what order, on which battery, in which weather.

A spray drone's working day is bounded by four things nobody plans together today:

* **battery** -- a 10 L-class drone sprays for roughly 8-10 minutes per battery set and the
  set needs one to two hours to cool and recharge, so battery sets x chargers, not the
  drone, decide how many hectares a day holds;
* **weather** -- wind, temperature and humidity decide whether spray lands on target or
  drifts or evaporates; many jurisdictions make that a rule (``rules.py`` packs);
* **area** -- spraying the whole field when a scan shows only some zones need it burns
  chemical, battery cycles and time;
* **travel** -- bookings are scattered over farms kilometres apart.

``plan_day`` schedules a day's bookings against all four: the live hourly forecast at each
farm evaluated with the operator's rule pack, the battery sets / chargers / charge time
the operator has, the sorties each job needs (``sorties.plan_sorties`` with balanced loads
and the best take-off point), the scanned spray area, and drive time. ``compare`` runs the
same bookings the way they are commonly run -- in booking order, whole field, full tanks,
take off from the field corner, fly whenever a battery is charged -- on the same forecast
and the same batteries, and reports the difference in efficiency terms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from . import rules, sop
from .intake import _km
from .mission import drone_spec

ACRE_HA = 0.404686


@dataclass
class Operator:
    name: str = "Spray operator"
    base: tuple[float, float] = (28.45, 77.58)
    country: str = "IN"                     # ISO-2, for place lookup
    utc_offset_h: float = 5.5               # local time = UTC + offset
    rules: str = "IN-SOP-2023"              # rules.PACKS key
    language: str = "hi"                    # brief language (ISO 639-1)
    currency: str = "₹"
    rate_per_ha: float = 741.0              # ₹300/acre
    drone: str = "t1_10l"                   # the project airframe (fieldpilot/airframe.py)
    battery_sets: int = 2                   # the T1 kit: 2 x 16000 mAh packs
    chargers: int = 1                       # one 12S charger
    charge_min: float = 45.0                # 30 min advertised; planned 45 incl. cool-down
    setup_min: float = 15.0                 # unload, mark, mix, safety brief at each farm
    turnaround_min: float = 3.0             # land, swap battery, refill, take off
    road_kmh: float = 25.0
    road_factor: float = 1.4                # road km per straight-line km
    day_start: str = "06:00"
    day_end: str = "18:00"
    local_units: dict = field(default_factory=lambda: {"bigha": 2529.0})   # m2 per local unit

    @classmethod
    def from_dict(cls, d: dict) -> "Operator":
        d = dict(d)
        if "rate_inr_acre" in d and "rate_per_ha" not in d:          # older files
            d["rate_per_ha"] = float(d.pop("rate_inr_acre")) / ACRE_HA
        if "bigha_m2" in d:
            d.setdefault("local_units", {})["bigha"] = float(d.pop("bigha_m2"))
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "base" in kw:
            kw["base"] = tuple(kw["base"])
        return cls(**kw)

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.utc_offset_h))

    @property
    def pack(self) -> rules.Pack:
        return rules.pack(self.rules)


@dataclass
class JobSpec:
    job_id: str
    farmer: str
    lat: float
    lon: float
    area_ha: float                         # whole field
    spray_ha: float | None = None          # what the scan + twin justified (None = not scanned)
    crop: str = ""
    problem: str = ""
    product: str = ""
    dose_per_ha: float | None = None
    urgency: float = 1.0                   # >1 when the twin says delay costs yield
    village: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "JobSpec":
        return cls(job_id=d["job_id"], farmer=d.get("farmer") or d["job_id"],
                   lat=float(d["lat"]), lon=float(d["lon"]), area_ha=float(d["area_ha"]),
                   spray_ha=(float(d["spray_ha"]) if d.get("spray_ha") is not None else None),
                   crop=d.get("crop") or "", problem=d.get("problem") or "",
                   product=d.get("product") or "", dose_per_ha=d.get("dose_per_ha"),
                   urgency=float(d.get("urgency", 1.0)), village=d.get("village") or "")


# ----------------------------------------------------------------------------- sorties

_SORTIE_CACHE: dict[tuple, dict] = {}


def sorties_for(area_ha: float, drone: str, optimised: bool = True) -> dict:
    """Sorties, flight minutes and liquid to spray ``area_ha`` with this drone.

    Same route planner as a real mission, on a 2:1 plot of that area whose access point is
    a corner (where the track usually meets the field). ``optimised``: balanced loads and
    the best take-off point on the edge; otherwise full tanks from the corner.
    """
    key = (drone, round(area_ha, 2), optimised)
    if key not in _SORTIE_CACHE:
        from dataclasses import replace
        from .field import grid_field
        from .sorties import best_takeoff, plan_sorties
        spec = drone_spec(drone)
        spec.max_range_m = 10_000.0
        w = max(8.0, math.sqrt(area_ha * 10_000 * 2))
        h = max(4.0, area_ha * 10_000 / w)
        f = grid_field("job", (0.0, 0.0), w, h, 1, 1, home_offset_m=(-3.0, -3.0), fence_margin_m=5.0)
        if optimised:
            home, _, _ = best_takeoff(f, f.zone_ids(), spec)
            f = replace(f, home=home)
        parts = plan_sorties(f, f.zone_ids(), spec, balance=optimised)
        n = len(parts)
        water_l = sum(p.spray_ml for p in parts) / 1000
        # common practice mixes in full-tank batches; what is left after the last sortie
        # has to be stored, re-sprayed or disposed of. Per-refill mixing leaves only the last margin.
        tank_l = spec.tank_ml / 1000
        # FieldPilot mixes each refill to what it sprays, plus a small margin in the last one
        # so the route completes (sorties.last_load_margin_l): that margin is its only leftover.
        from .sorties import last_load_margin_l
        last_l = (parts[-1].spray_ml / 1000) if parts else 0.0
        mixed_l = (water_l + last_load_margin_l(last_l) if optimised
                   else math.ceil(water_l / tank_l - 1e-9) * tank_l)
        _SORTIE_CACHE[key] = {"n": n, "flight_min": sum(p.est_time_s for p in parts) / 60.0,
                              "water_l": water_l, "mixed_l": mixed_l,
                              "transit_m": sum(p.distance_m - p.spray_m for p in parts)}
    return _SORTIE_CACHE[key]


# ----------------------------------------------------------------------------- weather

def hour_at(fc: list[sop.Hour], t: datetime) -> sop.Hour | None:
    t0 = t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for h in fc:
        if h.time == t0:
            return h
    return None


def _in_hours(op: Operator, start: datetime, end: datetime) -> bool:
    """The whole sortie inside one of the rule pack's spray-hour windows, by the local clock.

    (The forecast's hours need not line up with the local clock: India is UTC+5:30, so its
    hourly forecast lands at half past. Judging spray hours by the forecast hour would let a
    sortie start at 11:09 in a window that closes at 11:00, and lose 06:00-06:30.)"""
    ls, le = start.astimezone(op.tz), end.astimezone(op.tz)
    if le.date() != ls.date() and not (le.hour == 0 and le.minute == 0):
        return False
    a = ls.hour + ls.minute / 60 + ls.second / 3600
    b = le.hour + le.minute / 60 + le.second / 3600 if le.date() == ls.date() else 24.0
    return any(h0 <= a and b <= h1 for h0, h1 in op.pack.hours)


def _weather_ok(op: Operator, h: sop.Hour) -> bool:
    return not op.pack.site_reasons(h.wind_ms, h.temp_c, h.rh_pct) and h.precip_mm < op.pack.max_rain_mm_h


def slot_ok(fc: list[sop.Hour], start: datetime, end: datetime, op: Operator) -> bool:
    if not _in_hours(op, start, end):
        return False
    t = start
    while t < end:
        h = hour_at(fc, t)
        if h is None or not _weather_ok(op, h):
            return False
        t = t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return True


def next_window(fc: list[sop.Hour], after: datetime, minutes: float, op: Operator) -> datetime | None:
    """Earliest start at or after ``after`` whose whole sortie fits the weather and the hours."""
    after = after.astimezone(op.tz)
    dur = timedelta(minutes=max(minutes, 0.1))
    cands = {after}
    cands |= {h.time.astimezone(op.tz) for h in fc if h.time.astimezone(op.tz) > after}
    d0 = after.date()
    for k in range(4):
        for h0, _h1 in op.pack.hours:
            t = datetime.combine(d0 + timedelta(days=k), time(int(h0), int(round((h0 % 1) * 60))), op.tz)
            if t > after:
                cands.add(t)
    for c in sorted(cands):
        if slot_ok(fc, c, c + dur, op):
            return c
    return None


# ----------------------------------------------------------------------------- simulation

@dataclass
class _State:
    t: datetime
    pos: tuple[float, float]
    batteries: list[datetime]              # when each set is charged
    chargers: list[datetime]               # when each charger is free
    log: list[dict] = field(default_factory=list)
    batt_log: list[dict] = field(default_factory=list)
    km: float = 0.0
    idle_min: float = 0.0

    def copy(self) -> "_State":
        return _State(self.t, self.pos, list(self.batteries), list(self.chargers),
                      list(self.log), list(self.batt_log), self.km, self.idle_min)


def _do_job(st: _State, job: JobSpec, op: Operator, fc: list[sop.Hour], end: datetime,
            optimised: bool, max_wait_min: float = 180.0, selective: bool | None = None) -> dict | None:
    """Fly one job from state ``st`` (mutated). None if it cannot finish today."""
    km = _km(st.pos, (job.lat, job.lon)) * op.road_factor
    st.km += km
    st.t += timedelta(minutes=km / op.road_kmh * 60)
    arrive = st.t
    st.t += timedelta(minutes=op.setup_min)
    selective = optimised if selective is None else selective
    ha = job.spray_ha if (selective and job.spray_ha is not None) else job.area_ha
    so = sorties_for(ha, op.drone, optimised) if ha > 0 else {"n": 0, "flight_min": 0.0,
                                                              "water_l": 0.0, "mixed_l": 0.0,
                                                              "transit_m": 0.0}
    n = so["n"]
    per = so["flight_min"] / n if n else 0.0
    sorties, bad = [], 0
    for k in range(n):
        bi = min(range(len(st.batteries)), key=lambda i: st.batteries[i])
        start = max(st.t, st.batteries[bi])
        if optimised:
            t0 = start
            for _ in range(48):                 # the whole sortie must fit inside the window
                nl = next_window(fc, start, per, op)
                if nl is None or (nl - t0).total_seconds() / 60 > max_wait_min:
                    return None
                start = max(start, nl)
                if slot_ok(fc, start, start + timedelta(minutes=per), op):
                    break
                start = (start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                         + timedelta(hours=1)).astimezone(op.tz)
            else:
                return None
        stop = start + timedelta(minutes=per)
        if stop > end:
            return None
        ok = slot_ok(fc, start, stop, op)
        bad += 0 if ok else 1
        st.idle_min += (start - st.t).total_seconds() / 60
        ci = min(range(len(st.chargers)), key=lambda i: st.chargers[i])
        c0 = max(stop, st.chargers[ci])
        st.chargers[ci] = c0 + timedelta(minutes=op.charge_min)
        st.batteries[bi] = st.chargers[ci]
        st.batt_log.append({"set": bi + 1, "job": job.job_id, "flew": start.strftime("%H:%M"),
                            "charge_from": c0.strftime("%H:%M"),
                            "ready": st.batteries[bi].strftime("%H:%M")})
        sorties.append({"start": start.strftime("%H:%M"), "end": stop.strftime("%H:%M"),
                        "battery_set": bi + 1, "in_window": ok})
        st.t = stop + timedelta(minutes=op.turnaround_min if k < n - 1 else 0)
    st.pos = (job.lat, job.lon)
    if sorties:                                  # waiting for a window: arrive just before it
        first = datetime.combine(arrive.date(), time.fromisoformat(sorties[0]["start"]), op.tz)
        arrive = max(arrive, first - timedelta(minutes=op.setup_min))
    dose = float(job.dose_per_ha) if job.dose_per_ha else None
    row = {"job_id": job.job_id, "farmer": job.farmer, "village": job.village,
           "arrive": arrive.strftime("%H:%M"), "done": st.t.strftime("%H:%M"),
           "drive_km": round(km, 1), "sprayed_ha": round(ha, 3), "field_ha": job.area_ha,
           "scanned": job.spray_ha is not None and selective,
           "needed_ha": job.spray_ha if job.spray_ha is not None else job.area_ha,
           "sorties": sorties, "out_of_window_sorties": bad,
           "flight_min": round(so["flight_min"], 1), "water_l": round(so["water_l"], 1),
           "leftover_mix_l": round(max(0.0, so["mixed_l"] - so["water_l"]), 1),
           "charge": round(ha * op.rate_per_ha),
           "product": job.product,
           "product_used": round(ha * dose, 1) if dose else None,
           "product_wasted": (round(max(0.0, so["mixed_l"] - so["water_l"]) * dose
                                    / drone_spec(op.drone).spray_rate_l_ha, 1) if dose else None)}
    st.log.append(row)
    return row


def _metrics(st: _State, jobs: list[JobSpec], op: Operator, start: datetime, end: datetime) -> dict:
    rows = st.log
    by_id = {j.job_id: j for j in jobs}
    sprayed = sum(r["sprayed_ha"] for r in rows)
    needed = sum(min(r["sprayed_ha"], r["needed_ha"]) for r in rows)
    cycles = sum(len(r["sorties"]) for r in rows)
    flight = sum(r["flight_min"] for r in rows)
    needed_served = needed
    # A sortie outside the spray window breaks the rule pack (law where it is binding) and
    # puts product where drift or evaporation takes it: count only in-window coverage as
    # effective, pro rata by sortie.
    eff = sum(min(r["sprayed_ha"], r["needed_ha"]) *
              (sum(1 for x in r["sorties"] if x["in_window"]) / len(r["sorties"]))
              for r in rows if r["sorties"])
    return {"farms_served": len(rows), "farms_booked": len(jobs),
            "needed_ha_served": round(needed_served, 2),
            "sprayed_ha": round(sprayed, 2),
            "sprayed_beyond_need_ha": round(max(0.0, sprayed - needed), 2),
            "battery_cycles": cycles,
            "needed_ha_per_battery_cycle": round(needed_served / cycles, 3) if cycles else 0.0,
            "effective_ha": round(eff, 2),
            "effective_ha_per_battery_cycle": round(eff / cycles, 3) if cycles else 0.0,
            "farms_served_in_window": sum(1 for r in rows if r["out_of_window_sorties"] == 0),
            "flight_min": round(flight, 1),
            "out_of_window_sorties": sum(r["out_of_window_sorties"] for r in rows),
            "in_window_pct": round(100 * (1 - sum(r["out_of_window_sorties"] for r in rows)
                                          / cycles), 1) if cycles else 100.0,
            "water_l": round(sum(r["water_l"] for r in rows), 1),
            "leftover_mix_l": round(sum(r["leftover_mix_l"] for r in rows), 1),
            "farmer_cost": sum(r["charge"] for r in rows),
            "drive_km": round(st.km, 1), "idle_min": round(st.idle_min),
            "finish": rows[-1]["done"] if rows else None}


def _day_bounds(op: Operator, day: date) -> tuple[datetime, datetime]:
    return (datetime.combine(day, time.fromisoformat(op.day_start), op.tz),
            datetime.combine(day, time.fromisoformat(op.day_end), op.tz))


def plan_day(jobs: list[JobSpec], op: Operator, day: date, forecasts: dict[str, list[sop.Hour]],
             policy: str = "fieldpilot") -> dict:
    """policy 'fieldpilot': rule-pack weather windows, scanned areas, balanced loads, best
    take-off point, best value per hour next.
    policy 'fieldpilot_noscan': the same, but every field sprayed whole (no scan).
    policy 'baseline': booking order, whole field, full tanks from the corner, fly whenever
    a battery is charged."""
    start, end = _day_bounds(op, day)
    st = _State(start, op.base, [start] * op.battery_sets, [start] * op.chargers)
    left = list(jobs)
    deferred = []
    if policy == "baseline":
        for i, j in enumerate(left):
            trial = st.copy()
            if _do_job(trial, j, op, forecasts[j.job_id], end, optimised=False) is None:
                deferred = [{"job_id": x.job_id, "farmer": x.farmer, "reason": "ran out of day"}
                            for x in left[i:]]
                break
            st = trial
    else:
        while left:
            best, best_score, best_state = None, -1.0, None
            # work that fits without a long wait first; otherwise wait for the next window
            # (the crew charges batteries and drives in the meantime)
            for wait in (120.0, 24 * 60.0):
                for j in left:
                    trial = st.copy()
                    r = _do_job(trial, j, op, forecasts[j.job_id], end, True, max_wait_min=wait,
                                selective=(policy != "fieldpilot_noscan"))
                    if r is None:
                        continue
                    hours = max(0.25, (trial.t - st.t).total_seconds() / 3600)
                    score = j.urgency * max(r["sprayed_ha"], 0.05) / hours
                    if score > best_score:
                        best, best_score, best_state = j, score, trial
                if best is not None:
                    break
            if best is None:
                break
            st = best_state
            left.remove(best)
        for j in left:
            ha = j.spray_ha if j.spray_ha is not None else j.area_ha
            so = sorties_for(ha, op.drone)
            nw = next_window(forecasts[j.job_id], end, so["flight_min"] / max(1, so["n"]), op)
            deferred.append({"job_id": j.job_id, "farmer": j.farmer,
                             "reason": "no spray window left today with the batteries available",
                             "next_window": nw.strftime("%a %d %b %H:%M") if nw
                             else "none in the forecast"})
    return {"policy": policy, "day": day.isoformat(), "operator": op.name, "drone": op.drone,
            "rules": op.pack.pack_id, "plan": st.log, "batteries": st.batt_log,
            "deferred": deferred, "metrics": _metrics(st, jobs, op, start, end)}


IMPROVEMENT_KEYS = ("farms_served", "farms_served_in_window", "needed_ha_served",
                    "effective_ha", "battery_cycles", "needed_ha_per_battery_cycle",
                    "effective_ha_per_battery_cycle", "flight_min", "out_of_window_sorties",
                    "sprayed_beyond_need_ha", "water_l", "leftover_mix_l", "farmer_cost",
                    "drive_km", "idle_min")


def compare(jobs: list[JobSpec], op: Operator, day: date,
            forecasts: dict[str, list[sop.Hour]]) -> dict:
    fp = plan_day(jobs, op, day, forecasts, "fieldpilot")
    bl = plan_day(jobs, op, day, forecasts, "baseline")
    a, b = fp["metrics"], bl["metrics"]
    return {"fieldpilot": fp, "baseline": bl,
            "delta": {k: round(a[k] - b[k], 3) for k in IMPROVEMENT_KEYS}}


def live_forecasts(jobs: list[JobSpec], fetch=sop.fetch_forecast) -> dict[str, list[sop.Hour]]:
    """One Open-Meteo forecast per ~5 km cell, shared by the jobs in it."""
    cache: dict[tuple[float, float], list[sop.Hour]] = {}
    out = {}
    for j in jobs:
        cell = (round(j.lat / 0.05) * 0.05, round(j.lon / 0.05) * 0.05)
        if cell not in cache:
            cache[cell] = fetch(j.lat, j.lon)
        out[j.job_id] = cache[cell]
    return out


def day_sheet_html(plan: dict, op: Operator) -> str:
    """The printable sheet the crew carries: route, sorties, battery charging, deferrals."""
    from html import escape as e
    cur = e(op.currency)
    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{e(r['farmer'])}<br><small>{e(r['village'])}</small></td>"
        f"<td>{r['arrive']}</td><td>{r['done']}</td><td>{r['sprayed_ha']:.2f} / {r['field_ha']:.2f} ha</td>"
        f"<td>{' '.join(s['start'] + ' (B' + str(s['battery_set']) + ')' for s in r['sorties'])}</td>"
        f"<td>{r['water_l']} L{' · ' + e(r['product']) if r['product'] else ''}"
        f"{' · ' + str(r['product_used']) + ' g' if r['product_used'] else ''}</td>"
        f"<td>{cur}{r['charge']:,}</td></tr>" for i, r in enumerate(plan["plan"]))
    batt = "".join(f"<tr><td>B{b['set']}</td><td>{e(b['job'])}</td><td>{b['flew']}</td>"
                   f"<td>{b['charge_from']}</td><td>{b['ready']}</td></tr>" for b in plan["batteries"])
    dfr = "".join(f"<li>{e(d.get('farmer', d['job_id']))}: {e(d['reason'])}"
                  f"{' — next window ' + e(d['next_window']) if d.get('next_window') else ''}</li>"
                  for d in plan["deferred"])
    m = plan["metrics"]
    return f"""<!doctype html><meta charset="utf-8"><title>Day sheet {e(plan['day'])}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #999;padding:4px 6px;text-align:left;vertical-align:top}}h1{{font-size:20px}}</style>
<h1>{e(op.name)} · {e(plan['day'])}</h1>
<p>{m['farms_served']} of {m['farms_booked']} farms · {m['sprayed_ha']} ha · {m['battery_cycles']} battery
cycles · {m['flight_min']} flight min · {m['water_l']} L mixed per refill · {m['drive_km']} km ·
every sortie inside the {e(op.pack.title)} window.</p>
<table><tr><th>#</th><th>Farm</th><th>Arrive</th><th>Done</th><th>Spray</th>
<th>Sorties (battery set)</th><th>Mix</th><th>Charge</th></tr>{rows}</table>
<h2>Batteries</h2><table><tr><th>Set</th><th>Job</th><th>Flew</th><th>Charging from</th>
<th>Ready</th></tr>{batt}</table>
<h2>Not today</h2><ul>{dfr or '<li>none</li>'}</ul>"""
