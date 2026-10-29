"""Split a treatment route into sorties a real spray drone can actually fly.

A 10 L agricultural drone sprays for roughly 8-9 minutes per battery and empties
its tank in about half a hectare. Most plots need more than one sortie, and today
the operator finds that out in the air: the battery alarm goes off mid-row, the
drone comes home, and nobody knows where it stopped. That is the single most
common complaint farmers make about drone service (see docs/PRODUCT.md).

``plan_sorties`` walks the exact route ``mission.build_mission`` would fly and cuts
it wherever the next metre would break the tank, the usable battery (energy to
fly home included) or the flight-time limit. A pass that is cut is resumed from
the same point on the next sortie, so nothing is sprayed twice and nothing is
missed. ``load_sheet`` turns the sorties into what the ground crew needs: water
and product per refill, battery swaps and ground time.

Pure functions: no model, no network.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from shapely.geometry import Polygon

from .field import Field

GROUND_TURNAROUND_S = 180.0      # land, swap battery, refill tank, take off again


def last_load_margin_l(water_l: float) -> float:
    """Extra mix in the LAST refill of a job so the route completes.

    A controller's liquid sensor trips with some liquid still in the tank and line (the K3-A
    Pro twin needs ~0.02 L), and its flow calibration is good to a few per cent. Earlier
    refills are loaded to the litre on purpose: their tank runs dry where the plan cuts the
    route and the controller's breakpoint resume continues from there. The last one has no
    next sortie, so it carries 0.1 L + 3 %; what is left after it is the only leftover mix.
    """
    return round(0.1 + 0.03 * water_l, 2) if water_l > 0 else 0.0
ZONE_OVERHEAD_S = 10.0           # settle + spray valve at each zone entry (matches build_mission)


@dataclass
class Sortie:
    idx: int
    items: list = field(default_factory=list)       # MissionItem, seq from 0
    zones: list[str] = field(default_factory=list)
    distance_m: float = 0.0
    est_time_s: float = 0.0
    est_energy_wh: float = 0.0
    spray_ml: float = 0.0
    spray_m: float = 0.0
    resume: dict | None = None                      # where this sortie picks up a cut pass

    def summary(self) -> dict:
        d = asdict(self)
        d["distance_m"] = round(self.distance_m, 1)
        d["est_time_s"] = round(self.est_time_s, 1)
        d["est_energy_wh"] = round(self.est_energy_wh, 2)
        d["spray_ml"] = round(self.spray_ml, 1)
        d["spray_m"] = round(self.spray_m, 1)
        return d


def _legs(field_: Field, zone_ids: list[str], drone) -> list[tuple]:
    """(kind, a_xy, b_xy, zone) in flight order, starting and ending at home (0, 0)."""
    from .mission import _lawnmower, _order_zones
    fr = field_.frame
    legs, here = [], (0.0, 0.0)
    for z in _order_zones(field_, zone_ids):
        zpoly = Polygon([fr.to_local(lat, lon) for lat, lon in field_.zone(z).polygon])
        passes = _lawnmower(zpoly, drone.swath_m)
        legs.append(("transit", here, passes[0], z))
        legs.append(("enter", passes[0], passes[0], z))
        # even legs are passes (spray on); odd legs are the headland turns between them
        # (spray off - the pump would otherwise lay a double dose along the zone edge)
        for i, (p0, p1) in enumerate(zip(passes, passes[1:])):
            legs.append(("spray" if i % 2 == 0 else "turn", p0, p1, z))
        legs.append(("exit", passes[-1], passes[-1], z))
        here = passes[-1]
    return legs


def plan_sorties(field_: Field, zone_ids: list[str], drone, balance: bool = True) -> list[Sortie]:
    """Cut the route into sorties that each fit the tank, the usable battery and the time limit.

    ``balance``: once the number of sorties is known, every load is the same size (total
    liquid / sorties) instead of full tanks and a sliver at the end. Same sortie count, no
    near-empty last flight, and each refill is mixed to the exact volume it will spray.
    """
    parts = _plan(field_, zone_ids, drone)
    if not balance or len(parts) < 2:
        return parts
    total = sum(p.spray_ml for p in parts)
    from dataclasses import replace
    target = total / len(parts) * 1.002 + 1.0
    if target >= drone.tank_ml:
        return parts
    even = _plan(field_, zone_ids, replace(drone, tank_ml=target))
    return even if len(even) == len(parts) else parts


def best_takeoff(field_: Field, zone_ids: list[str], drone, samples: int = 24,
                 inset_m: float = 1.5) -> tuple[tuple[float, float], float, float]:
    """The take-off / refill point on the field edge that minimises total flight time.

    Operators set the vehicle down wherever the road meets the field; on a long plot the
    drone then dead-heads the whole length on every refill. Returns (lat, lon), the best
    total flight seconds, and the seconds with the field's current home.
    """
    from dataclasses import replace
    from shapely.geometry import Polygon as _P
    fence = field_.geofence_local().buffer(-inset_m)
    base = sum(p.est_time_s for p in plan_sorties(field_, zone_ids, drone))
    best, best_t = field_.home, base
    if fence.is_empty:
        return best, best_t, base
    ring = fence.exterior
    for k in range(samples):
        x, y = ring.interpolate(k / samples, normalized=True).coords[0]
        lat, lon = field_.frame.to_wgs84(x, y)
        cand = replace(field_, home=(lat, lon))
        try:
            t = sum(p.est_time_s for p in plan_sorties(cand, zone_ids, drone))
        except ValueError:
            continue
        if t < best_t - 1e-6:
            best, best_t = (round(lat, 7), round(lon, 7)), t
    del _P
    return best, best_t, base


def _plan(field_: Field, zone_ids: list[str], drone) -> list[Sortie]:
    from .mission import MissionItem
    fr, alt = field_.frame, field_.treat_alt_m
    ml_per_m = drone.spray_rate_l_ha * drone.swath_m / 10.0          # L/ha x m / 10 000 m2 x 1000 mL
    climb_s = 2 * alt / drone.climb_speed_ms
    # Power follows mass when the spec has a mass model (a full 10 L tank is ~40 % of a
    # 24 kg take-off). Planned conservatively: every sortie lifts off with a full tank and
    # gets lighter only by what it has sprayed so far.
    climb_wh = climb_s * drone.hover_w(drone.tank_ml) / 3600

    def onboard() -> float:
        return drone.tank_ml - (cur.spray_ml if cur is not None else 0.0)

    def transit(a, b, speed=None):
        d = math.dist(a, b)
        t = drone.leg_time_s(d, speed or drone.transit_speed_ms)
        return d, t, t * drone.cruise_w(onboard()) / 3600

    def home_cost(p):
        _, t, e = transit(p, (0.0, 0.0))
        return t, e

    sorties: list[Sortie] = []
    cur: Sortie | None = None
    pos = (0.0, 0.0)
    spraying_zone = ""

    def item(kind, xy=None, z=""):
        if xy is None:
            cur.items.append(MissionItem(len(cur.items), kind, zone_id=z))
        else:
            cur.items.append(MissionItem(len(cur.items), kind, *fr.to_wgs84(*xy), alt, z))

    def open_sortie(resume_at=None, zone=""):
        nonlocal cur, pos
        cur = Sortie(idx=len(sorties) + 1, est_time_s=climb_s, est_energy_wh=climb_wh)
        cur.items.append(MissionItem(0, "takeoff", *field_.home, alt))
        pos = (0.0, 0.0)
        if resume_at is not None:
            d, t, e = transit(pos, resume_at)
            cur.distance_m += d
            cur.est_time_s += t
            cur.est_energy_wh += e
            pos = resume_at
            item("waypoint", resume_at, zone)
            cur.resume = {"zone_id": zone, "lat": round(fr.to_wgs84(*resume_at)[0], 7),
                          "lon": round(fr.to_wgs84(*resume_at)[1], 7)}

    def close_sortie():
        nonlocal cur, spraying_zone
        if spraying_zone:
            item("spray_off", z=spraying_zone)
        d, t, e = transit(pos, (0.0, 0.0))
        cur.distance_m += d
        cur.est_time_s += t
        cur.est_energy_wh += e
        cur.items.append(MissionItem(len(cur.items), "rtl", *field_.home, alt))
        sorties.append(cur)
        cur, spraying_zone = None, ""

    def fits(add_t, add_e, add_ml, end_xy) -> bool:
        ht, he = home_cost(end_xy)
        return (cur.est_energy_wh + add_e + he <= drone.usable_wh + 1e-9
                and cur.spray_ml + add_ml <= drone.tank_ml + 1e-9
                and cur.est_time_s + add_t + ht <= drone.max_flight_s + 1e-9)

    open_sortie()
    for kind, a, b, z in _legs(field_, zone_ids, drone):
        if kind == "transit":
            d, t, e = transit(pos, b)
            if not fits(t, e, 0.0, b):
                close_sortie()
                open_sortie()
                d, t, e = transit(pos, b)
                if not fits(t, e, 0.0, b):
                    raise ValueError(f"zone {z} is out of reach of one battery from home")
            cur.distance_m += d
            cur.est_time_s += t
            cur.est_energy_wh += e
            pos = b
            item("waypoint", b, z)
        elif kind == "enter":
            t = ZONE_OVERHEAD_S
            e = t * drone.hover_w(onboard()) / 3600
            if not fits(t, e, 0.0, pos):
                close_sortie()
                open_sortie(resume_at=a, zone=z)
            cur.est_time_s += t
            cur.est_energy_wh += e
            if z not in cur.zones:
                cur.zones.append(z)
            item("spray_on", z=z)
            spraying_zone = z
        elif kind == "exit":
            if spraying_zone:
                item("spray_off", z=z)
                spraying_zone = ""
        elif kind == "turn":
            if spraying_zone:
                item("spray_off", z=z)
                spraying_zone = ""
            d, t, e = transit(pos, b, drone.turn_speed_ms or None)
            if not fits(t, e, 0.0, b):
                close_sortie()
                open_sortie(resume_at=b, zone=z)
                if z not in cur.zones:
                    cur.zones.append(z)
            else:
                cur.distance_m += d
                cur.est_time_s += t
                cur.est_energy_wh += e
                pos = b
                item("waypoint", b, z)
        else:                                                   # spray leg, may be cut
            if spraying_zone != z:
                item("spray_on", z=z)
                spraying_zone = z
            start = a
            while True:
                L = math.dist(start, b)
                if L < 1e-6:
                    break

                def cost(f):
                    d = L * f
                    t = drone.leg_time_s(d, drone.spray_speed_ms)
                    end = (start[0] + (b[0] - start[0]) * f, start[1] + (b[1] - start[1]) * f)
                    return t, t * drone.hover_w(onboard()) / 3600, d * ml_per_m, end, d

                t, e, ml, end, d = cost(1.0)
                if fits(t, e, ml, end):
                    f = 1.0
                else:
                    lo, hi = 0.0, 1.0
                    for _ in range(40):
                        mid = (lo + hi) / 2
                        tm, em, mlm, endm, _ = cost(mid)
                        lo, hi = (mid, hi) if fits(tm, em, mlm, endm) else (lo, mid)
                    f = lo
                    t, e, ml, end, d = cost(f)
                if d >= 0.5 or f == 1.0:
                    cur.distance_m += d
                    cur.spray_m += d
                    cur.est_time_s += t
                    cur.est_energy_wh += e
                    cur.spray_ml += ml
                    pos = end
                    item("waypoint", end, z)
                if f == 1.0:
                    break
                # out of tank / battery / time part-way along the pass: go home, come back here
                close_sortie()
                open_sortie(resume_at=pos, zone=z)
                if z not in cur.zones:
                    cur.zones.append(z)
                item("spray_on", z=z)
                spraying_zone = z
                start = pos
                probe = cost(min(1.0, 1.0 / max(L, 1e-6)))           # can a fresh sortie fly 1 m?
                if not fits(probe[0], probe[1], probe[2], probe[3]):
                    raise ValueError("a full battery and tank cannot cover even 1 m from here: "
                                     "check the drone spec")
    close_sortie()
    return [s for s in sorties if s.spray_m > 0 or len(sorties) == 1]


def load_sheet(sorties: list[Sortie], drone, dose_per_ha: float | None = None,
               unit: str = "g", product: str = "") -> dict:
    """What the ground crew mixes and swaps, sortie by sortie.

    Product per refill is the per-hectare dose divided by the drone's water volume, times
    the water in that refill (``sop.tank_mix``) -- never the knapsack label's dilution.
    """
    from .sop import tank_mix
    rows = []
    for s in sorties:
        water_l = s.spray_ml / 1000.0
        prod = (round(tank_mix(dose_per_ha, drone.spray_rate_l_ha, water_l), 1)
                if dose_per_ha and water_l > 0 else None)
        last = s is sorties[-1]
        margin = last_load_margin_l(water_l) if last else 0.0
        # Earlier refills are exact: the twin shows the liquid sensor's residual (~0.02 L) and the
        # pump's slightly lower dose while each line speeds up cancel to within a metre.
        load_l = water_l + margin
        prod = (round(tank_mix(dose_per_ha, drone.spray_rate_l_ha, load_l), 1)
                if dose_per_ha and load_l > 0 else None)
        rows.append({"sortie": s.idx, "zones": s.zones, "water_l": round(load_l, 2),
                     "sprays_l": round(water_l, 2), "margin_l": margin,
                     "product": prod, "unit": unit,
                     "flight_min": round(s.est_time_s / 60, 1),
                     "battery_pct_used": round(100 * s.est_energy_wh / drone.battery_wh),
                     "resume": s.resume})
    n = len(sorties)
    water = sum(r["water_l"] for r in rows)
    sprayed_ha = sum(s.spray_m for s in sorties) * drone.swath_m / 10_000
    flight_s = sum(s.est_time_s for s in sorties)
    return {"product": product or None, "dose_per_ha": dose_per_ha, "unit": unit,
            "sorties": n, "battery_swaps": max(0, n - 1), "refills": max(0, n - 1),
            "water_l": round(water, 2),
            "product_total": (round(sum(r["product"] or 0 for r in rows), 1) if dose_per_ha else None),
            "sprayed_ha": round(sprayed_ha, 3),
            "flight_min": round(flight_s / 60, 1),
            "job_min": round((flight_s + GROUND_TURNAROUND_S * max(0, n - 1)) / 60, 1),
            "rows": rows}
