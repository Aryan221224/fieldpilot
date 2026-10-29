"""Approved plans -> a drone mission, and the deterministic safety check.

The safety checker is plain code with no model in it. It runs when a mission is
built, and again -- against fresh weather and a fresh preflight -- the moment a
human presses Approve, because conditions change between the two. A mission
that fails any check is never uploaded, whatever the agent said about it.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, Polygon

from .field import Field


@dataclass
class DroneSpec:
    """Energy and payload model of the aircraft. Calibrate against real logs."""
    name: str = "college SLAM quad (edit me)"
    transit_speed_ms: float = 4.0
    spray_speed_ms: float = 4.5       # SOP-2023: 4.5-5.0 m/s for most crops
    climb_speed_ms: float = 1.5
    hover_power_w: float = 280.0      # at ref_mass_kg when a mass model is given
    cruise_power_w: float = 300.0
    battery_wh: float = 77.0          # 4S 5200 mAh
    reserve_frac: float = 0.35        # never plan into the last 35 %
    tank_ml: float = 1000.0
    swath_m: float = 3.0              # SOP-2023: 3-6 m
    spray_rate_l_ha: float = 20.0     # typical low-volume drone application
    max_range_m: float = 120.0        # visual line of sight
    max_flight_s: float = 600.0
    # -- optional airframe envelope (0 / None = not known, check skipped) ----------------
    pump_max_l_min: float = 0.0       # system flow through all nozzles at full pump
    max_spray_speed_ms: float = 0.0   # airframe's maximum operating (spraying) speed
    pump_cutoff_ms: float = 0.0       # flow controller stops the pump below this speed
    pump_stall_duty: float = 0.0      # PWM duty below which the pump motor does not turn
    dry_mass_kg: float = 0.0          # airframe without battery or liquid
    battery_mass_kg: float = 0.0      # one flight battery set
    ref_mass_kg: float = 0.0          # take-off mass at which hover_power_w was derived
    mtow_kg: float = 0.0              # maximum take-off weight
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    nozzles: int = 0
    rotors: int = 0
    # -- line kinematics (0 = fly every leg at constant speed, the old approximation) ---------
    accel_ms2: float = 0.0            # along-track acceleration / braking at the end of each line
    turn_speed_ms: float = 0.0        # headland side-step between lines (0 = transit speed)

    def leg_time_s(self, d: float, v: float) -> float:
        """Time to fly ``d`` metres from rest to rest at cruise ``v`` with ``accel_ms2``."""
        if d <= 0:
            return 0.0
        a = self.accel_ms2
        if a <= 0:
            return d / v
        if d >= v * v / a:
            return d / v + v / a
        return 2.0 * (d / a) ** 0.5

    @property
    def usable_wh(self) -> float:
        return self.battery_wh * (1.0 - self.reserve_frac)

    # -- mass model: a multirotor's induced power grows with mass^1.5 -------------------
    @property
    def has_mass_model(self) -> bool:
        return self.dry_mass_kg > 0 and self.ref_mass_kg > 0

    def mass_kg(self, liquid_ml: float) -> float:
        """Take-off/flying mass with ``liquid_ml`` on board (spray mix taken as water)."""
        return self.dry_mass_kg + self.battery_mass_kg + max(0.0, liquid_ml) / 1000.0

    def power_scale(self, liquid_ml: float | None) -> float:
        if not self.has_mass_model or liquid_ml is None:
            return 1.0
        return (self.mass_kg(liquid_ml) / self.ref_mass_kg) ** 1.5

    def hover_w(self, liquid_ml: float | None = None) -> float:
        return self.hover_power_w * self.power_scale(liquid_ml)

    def cruise_w(self, liquid_ml: float | None = None) -> float:
        return self.cruise_power_w * self.power_scale(liquid_ml)

    # -- spraying ----------------------------------------------------------------------
    def flow_l_min(self, speed_ms: float | None = None, rate_l_ha: float | None = None) -> float:
        """Pump flow needed to lay ``rate_l_ha`` across the swath at ``speed_ms``."""
        v = self.spray_speed_ms if speed_ms is None else speed_ms
        r = self.spray_rate_l_ha if rate_l_ha is None else rate_l_ha
        if self.pump_cutoff_ms and v < self.pump_cutoff_ms:
            return 0.0
        return r * self.swath_m * v * 60.0 / 10_000.0

    def pump_limited_speed_ms(self, rate_l_ha: float | None = None) -> float | None:
        """Fastest ground speed at which the pump can still deliver the rate."""
        if not self.pump_max_l_min:
            return None
        r = self.spray_rate_l_ha if rate_l_ha is None else rate_l_ha
        return self.pump_max_l_min * 10_000.0 / (60.0 * r * self.swath_m)


# Presets are conservative envelopes, not vendor specs: calibrate against your own flight
# logs. 10 L class: the small-holder workhorse (India, Africa, SE Asia) -- ~8-9 min of
# spraying per battery set, 4 m swath, 20 L/ha.
PRESETS: dict[str, DroneSpec] = {
    "bench_quad": DroneSpec(),
    "agri_10l": DroneSpec(name="10 L agri spray drone", transit_speed_ms=6.0, spray_speed_ms=4.5,
                          climb_speed_ms=2.0, hover_power_w=3600.0, cruise_power_w=3800.0,
                          battery_wh=700.0, reserve_frac=0.30, tank_ml=10_000.0, swath_m=4.0,
                          spray_rate_l_ha=20.0, max_range_m=400.0, max_flight_s=900.0),
    # A 40 L-class row-crop spray drone (large-farm service providers in the Americas,
    # China, Southeast Asia): wide swath, low volume, short flights on a heavy battery.
    "agri_40l": DroneSpec(name="40 L agri spray drone", transit_speed_ms=10.0, spray_speed_ms=7.0,
                          climb_speed_ms=3.0, hover_power_w=11_000.0, cruise_power_w=11_500.0,
                          battery_wh=1600.0, reserve_frac=0.30, tank_ml=40_000.0, swath_m=9.0,
                          spray_rate_l_ha=10.0, max_range_m=1000.0, max_flight_s=900.0),
}
# The project's own airframe: the T1-class 10 L quad (fieldpilot/airframe.py derives every
# number from its specification sheet and says which ones are assumptions).
from .airframe import t1_spec  # noqa: E402
PRESETS["t1_10l"] = t1_spec()
DEFAULT_AIRFRAME = "t1_10l"


def drone_spec(name: str | None) -> DroneSpec:
    """A copy of a preset. ``None``/empty gives the project airframe (the T1 10 L quad)."""
    if not name:
        name = DEFAULT_AIRFRAME
    if name not in PRESETS:
        raise KeyError(f"unknown drone preset {name!r}; have {sorted(PRESETS)}")
    from dataclasses import replace
    return replace(PRESETS[name])


@dataclass
class MissionItem:
    seq: int
    kind: str              # takeoff | waypoint | spray_on | spray_off | rtl
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    zone_id: str = ""


@dataclass
class Mission:
    mission_id: str
    field_name: str
    zones: list[str]
    plan_ids: list[str]
    items: list[MissionItem]
    distance_m: float
    est_time_s: float
    est_energy_wh: float
    spray_ml: float
    created_utc: str
    checks: list[dict] = field(default_factory=list)
    ok: bool = False
    # One entry when the whole route fits one battery and one tank. Otherwise the route is
    # cut into sorties (fieldpilot/sorties.py) and Approve flies them one at a time.
    sorties: list[dict] = field(default_factory=list)
    next_sortie: int = 0
    load: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = [[i.lat, i.lon] for i in self.items if i.kind in ("takeoff", "waypoint", "rtl")]
        return d


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _lawnmower(poly_xy: Polygon, swath: float) -> list[tuple[float, float]]:
    """Boustrophedon passes over one zone, along its longest edge."""
    coords = list(poly_xy.exterior.coords)
    best, ang = 0.0, 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        # 5 cm tolerance: equal edges (a square zone) always resolve to the first one, so moving
        # a zone a few metres (drift compensation) never turns its lines
        if L > best + 0.05:
            best, ang = L, math.degrees(math.atan2(y2 - y1, x2 - x1))
    c = poly_xy.centroid
    inner = poly_xy.buffer(-swath / 2.0)
    if inner.is_empty:
        return [(c.x, c.y)]
    # Across the passes: centre lines from swath/2 inside one edge to swath/2 inside the other,
    # evenly spaced and never more than one swath apart, so the strips cover the whole zone
    # (stepping by a fixed swath from one side can drop the last pass to rounding).
    # Along each pass: from boundary to boundary of the zone itself - the nozzles spray
    # sideways, not ahead, so stopping swath/2 short of each end would leave both ends dry.
    r_in = rotate(inner, -ang, origin=c)
    r_full = rotate(poly_xy, -ang, origin=c)
    minx, miny, maxx, maxy = r_in.bounds
    fminx, _, fmaxx, _ = r_full.bounds
    # 1 % of a swath: centimetres of coordinate rounding must not add a whole pass
    n = max(1, math.ceil((maxy - miny) / swath - 0.01) + 1)
    ys = [miny] if n == 1 else [miny + k * (maxy - miny) / (n - 1) for k in range(n)]
    pts: list[tuple[float, float]] = []
    flip = False
    for y in ys:
        seg = LineString([(fminx - 1, y), (fmaxx + 1, y)]).intersection(r_full)
        if not seg.is_empty:
            if isinstance(seg, MultiLineString):
                seg = max(seg.geoms, key=lambda g: g.length)
            a, b = list(seg.coords)[0], list(seg.coords)[-1]
            line = LineString([a, b] if not flip else [b, a])
            line = rotate(line, ang, origin=c)
            pts.extend(line.coords)
            flip = not flip
    return pts or [(c.x, c.y)]


def _order_zones(field_: Field, zone_ids: list[str]) -> list[str]:
    """Nearest-neighbour from home, then 2-opt. Exact enough for <= ~12 zones."""
    fr = field_.frame
    pos = {z: fr.to_local(*field_.zone(z).centroid()) for z in zone_ids}
    order, here = [], (0.0, 0.0)
    left = set(zone_ids)
    while left:
        nxt = min(left, key=lambda z: math.dist(here, pos[z]))
        order.append(nxt)
        left.remove(nxt)
        here = pos[nxt]

    def length(o):
        pts = [(0.0, 0.0)] + [pos[z] for z in o] + [(0.0, 0.0)]
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))

    improved = True
    while improved and len(order) > 3:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 2, len(order) + 1):
                cand = order[:i] + order[i:j][::-1] + order[j:]
                if length(cand) + 1e-9 < length(order):
                    order, improved = cand, True
    return order


# ---------------------------------------------------------------------------
# Build + check
# ---------------------------------------------------------------------------

def build_mission(field_: Field, zone_ids: list[str], plan_ids: list[str],
                  drone: DroneSpec | None = None) -> Mission:
    drone = drone or drone_spec(None)
    fr = field_.frame
    alt = field_.treat_alt_m
    items: list[MissionItem] = [MissionItem(0, "takeoff", *field_.home, alt)]
    dist = spray_dist = 0.0
    here = (0.0, 0.0)
    spray_ml = 0.0
    order = _order_zones(field_, zone_ids)
    for z in order:
        zpoly = Polygon([fr.to_local(lat, lon) for lat, lon in field_.zone(z).polygon])
        passes = _lawnmower(zpoly, drone.swath_m)
        first = passes[0]
        dist += math.dist(here, first)
        items.append(MissionItem(len(items), "waypoint", *fr.to_wgs84(*first), alt, z))
        for i, (p0, p1) in enumerate(zip(passes, passes[1:])):
            d = math.dist(p0, p1)
            if i % 2 == 0:                       # a pass: spray on along it
                items.append(MissionItem(len(items), "spray_on", zone_id=z))
                items.append(MissionItem(len(items), "waypoint", *fr.to_wgs84(*p1), alt, z))
                items.append(MissionItem(len(items), "spray_off", zone_id=z))
                spray_dist += d
                spray_ml += d * drone.spray_rate_l_ha * drone.swath_m / 10.0
            else:                                # headland turn: spray off
                items.append(MissionItem(len(items), "waypoint", *fr.to_wgs84(*p1), alt, z))
                dist += d
        if len(passes) == 1:                     # zone narrower than one swath: spot
            items.append(MissionItem(len(items), "spray_on", zone_id=z))
            items.append(MissionItem(len(items), "spray_off", zone_id=z))
            spray_ml += zpoly.area / 10_000.0 * drone.spray_rate_l_ha * 1000.0
        here = passes[-1]
    dist += math.dist(here, (0.0, 0.0))
    items.append(MissionItem(len(items), "rtl", *field_.home, alt))

    climb_s = 2 * alt / drone.climb_speed_ms
    t = dist / drone.transit_speed_ms + spray_dist / drone.spray_speed_ms + climb_s + 10 * len(order)
    # one sortie carries exactly what it sprays; power taken at that full load (conservative)
    e = (dist / drone.transit_speed_ms) * drone.cruise_w(spray_ml) / 3600 + \
        (spray_dist / drone.spray_speed_ms + climb_s + 10 * len(order)) * drone.hover_w(spray_ml) / 3600
    m = Mission(mission_id="M-" + uuid.uuid4().hex[:6].upper(), field_name=field_.name,
                zones=order, plan_ids=list(plan_ids), items=items,
                distance_m=round(dist + spray_dist, 1), est_time_s=round(t, 1),
                est_energy_wh=round(e, 2), spray_ml=round(spray_ml, 1),
                created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if (m.est_energy_wh <= drone.usable_wh and m.spray_ml <= drone.tank_ml
            and m.est_time_s <= drone.max_flight_s):
        m.sorties = [{"idx": 1, "zones": list(order), "distance_m": m.distance_m,
                      "est_time_s": m.est_time_s, "est_energy_wh": m.est_energy_wh,
                      "spray_ml": m.spray_ml, "resume": None,
                      "items": [asdict(i) for i in items]}]
    else:
        from .sorties import plan_sorties
        try:
            parts = plan_sorties(field_, order, drone)
        except ValueError:
            parts = []
        if parts:
            m.sorties = [p.summary() for p in parts]
            flat: list[MissionItem] = []
            for p in parts:
                for it in p.items:
                    flat.append(MissionItem(len(flat), it.kind, it.lat, it.lon, it.alt_m, it.zone_id))
            m.items = flat
            m.distance_m = round(sum(p.distance_m for p in parts), 1)
            m.est_time_s = round(sum(p.est_time_s for p in parts), 1)
            m.est_energy_wh = round(sum(p.est_energy_wh for p in parts), 2)
            m.spray_ml = round(sum(p.spray_ml for p in parts), 1)
    m.load = _load(field_, m, drone)
    m.checks = safety_checks(field_, m, drone)
    m.ok = all(c["ok"] for c in m.checks)
    return m


def _load(field_: Field, m: Mission, drone: DroneSpec) -> dict:
    from .sorties import GROUND_TURNAROUND_S
    from .sop import tank_mix
    c = field_.compliance or {}
    dose = c.get("dose_per_ha")
    rows = []
    for s in m.sorties:
        water = s["spray_ml"] / 1000.0
        rows.append({"sortie": s["idx"], "zones": s["zones"], "water_l": round(water, 2),
                     "product": (round(tank_mix(float(dose), drone.spray_rate_l_ha, water), 1)
                                 if dose and water > 0 else None),
                     "flight_min": round(s["est_time_s"] / 60, 1),
                     "battery_pct_used": round(100 * s["est_energy_wh"] / drone.battery_wh),
                     "resume": s.get("resume")})
    n = len(m.sorties)
    return {"drone": drone.name, "product": c.get("product"), "dose_per_ha": dose,
            "unit": c.get("dose_unit", "g"),
            "sorties": n, "battery_swaps": max(0, n - 1), "refills": max(0, n - 1),
            "water_l": round(m.spray_ml / 1000.0, 2),
            "product_total": (round(sum(r["product"] or 0 for r in rows), 1) if dose else None),
            "job_min": round((m.est_time_s + GROUND_TURNAROUND_S * max(0, n - 1)) / 60, 1),
            "rows": rows}


def sortie_mission(m: Mission, k: int) -> Mission:
    """Sortie ``k`` (0-based) of a multi-sortie mission as an uploadable Mission."""
    if len(m.sorties) <= 1:
        return m
    s = m.sorties[k]
    items = [MissionItem(**it) for it in s["items"]]
    for i, it in enumerate(items):
        it.seq = i
    sm = Mission(mission_id=f"{m.mission_id}-S{k + 1}", field_name=m.field_name, zones=list(s["zones"]),
                 plan_ids=list(m.plan_ids), items=items, distance_m=s["distance_m"],
                 est_time_s=s["est_time_s"], est_energy_wh=s["est_energy_wh"],
                 spray_ml=s["spray_ml"], created_utc=m.created_utc, checks=list(m.checks), ok=m.ok)
    sm.sorties = [s]
    return sm


def safety_checks(field_: Field, m: Mission, drone: DroneSpec,
                  weather: dict | None = None, preflight: dict | None = None) -> list[dict]:
    """Every check a mission must pass. Pure function: no model, no network."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    geo = [i for i in m.items if i.kind in ("takeoff", "waypoint", "rtl")]
    outside = [i.seq for i in geo if not field_.inside_geofence(i.lat, i.lon, margin_m=0.5)]
    add("geofence", not outside,
        "all waypoints >= 0.5 m inside the geofence" if not outside
        else f"items {outside} outside the geofence")
    alts = [i.alt_m for i in geo]
    add("altitude", all(field_.min_alt_m <= a <= field_.max_alt_m for a in alts),
        f"{min(alts):.1f}-{max(alts):.1f} m AGL within [{field_.min_alt_m}, {field_.max_alt_m}]")
    far = max(math.dist((0, 0), field_.frame.to_local(i.lat, i.lon)) for i in geo)
    add("range", far <= drone.max_range_m,
        f"max {far:.0f} m from pilot (limit {drone.max_range_m:.0f} m, visual line of sight)")
    if len(m.sorties) > 1:
        n = len(m.sorties)
        e = max(s["est_energy_wh"] for s in m.sorties)
        t = max(s["est_time_s"] for s in m.sorties)
        v = max(s["spray_ml"] for s in m.sorties)
        tail = f" per sortie, worst of {n} sorties (battery swap + refill between)"
        add("energy", e <= drone.usable_wh,
            f"{e:.1f} Wh of {drone.usable_wh:.1f} Wh usable{tail}")
        add("flight_time", t <= drone.max_flight_s,
            f"{t / 60:.1f} min (limit {drone.max_flight_s / 60:.0f} min){tail}")
        add("tank", v <= drone.tank_ml, f"{v:.0f} mL of {drone.tank_ml:.0f} mL tank{tail}")
    else:
        add("energy", m.est_energy_wh <= drone.usable_wh,
            f"{m.est_energy_wh:.1f} Wh of {drone.usable_wh:.1f} Wh usable "
            f"({drone.reserve_frac:.0%} reserve held back)")
        add("flight_time", m.est_time_s <= drone.max_flight_s,
            f"{m.est_time_s / 60:.1f} min (limit {drone.max_flight_s / 60:.0f} min)")
        add("tank", m.spray_ml <= drone.tank_ml,
            f"{m.spray_ml:.0f} mL needed, tank {drone.tank_ml:.0f} mL")
    add("zones", bool(m.zones), f"{len(m.zones)} zone(s): {', '.join(m.zones) or 'none'}")
    _airframe_checks(add, m, drone, weather)
    if weather is not None:
        add("weather", weather.get("go", False),
            "; ".join(weather.get("reasons", [])) or f"go ({weather.get('source')})")
    if preflight is not None:
        add("preflight", preflight.get("ok", False),
            "; ".join(preflight.get("issues", [])) or "vehicle reports ready")
    return checks


def _airframe_checks(add, m: Mission, drone: DroneSpec, weather: dict | None) -> None:
    """The airframe's own envelope: pump flow, spraying speed, take-off mass, temperature."""
    if drone.pump_max_l_min:
        need = drone.flow_l_min()
        vmax = drone.pump_limited_speed_ms()
        add("pump_flow", need <= drone.pump_max_l_min + 1e-9,
            f"{need:.2f} L/min needed for {drone.spray_rate_l_ha:g} L/ha x {drone.swath_m:g} m at "
            f"{drone.spray_speed_ms:g} m/s; pump {drone.pump_max_l_min:g} L/min "
            f"(full rate holds up to {vmax:.1f} m/s)")
    if drone.max_spray_speed_ms:
        add("spray_speed", drone.spray_speed_ms <= drone.max_spray_speed_ms,
            f"{drone.spray_speed_ms:g} m/s (airframe max operating {drone.max_spray_speed_ms:g} m/s)")
    if drone.has_mass_model and drone.mtow_kg:
        load = max((s["spray_ml"] for s in m.sorties), default=m.spray_ml) if m.sorties else m.spray_ml
        kg = drone.mass_kg(load)
        from .airframe import india_category
        add("takeoff_mass", kg <= drone.mtow_kg,
            f"{kg:.1f} kg at take-off with {load / 1000:.1f} L (MTOW {drone.mtow_kg:g} kg; "
            f"India Drone Rules category at this weight: {india_category(kg)})")
    temp = ((weather or {}).get("now") or {}).get("temp_c")
    if temp is not None and (drone.min_temp_c is not None or drone.max_temp_c is not None):
        lo = drone.min_temp_c if drone.min_temp_c is not None else -99
        hi = drone.max_temp_c if drone.max_temp_c is not None else 99
        add("airframe_temperature", lo <= temp <= hi,
            f"{temp:g} C (airframe operating range {lo:g} to {hi:g} C)")


def recheck(field_: Field, m: Mission, drone: DroneSpec, weather: dict, preflight: dict) -> Mission:
    m.checks = safety_checks(field_, m, drone, weather, preflight)
    m.ok = all(c["ok"] for c in m.checks)
    return m
