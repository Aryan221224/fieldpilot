"""A behavioural twin of the JIYI K3-A Pro flying a FieldPilot flight card.

The K3-A Pro is closed. FieldPilot cannot upload to it; the pilot enters each zone's boundary,
the line spacing, speed, height and dose from the flight card into the JIYI app, loads the
water on the card, and the controller flies. This module flies that card the way the
controller is documented to behave, so FieldPilot's predictions can be checked against the
controller's logic rather than against FieldPilot's own planner:

* **Route.** The app lays parallel lines over each zone at the card's line spacing, boundary to
  boundary, turning at the ends. (Modelled with the same line pattern FieldPilot plans;
  the real app's edge offset is a setting.)
* **Flow follows ground speed** to hold the dose per area, and the pump stops below 0.5 m/s
  (T1 brochure). Flow is off on the turns between lines and in transit.
* **Pump dynamics.** The T1's pump does not jump to the commanded flow: the same dead time,
  lag and stall duty as the bench emulator (fieldpilot/emulator.py).
* **Low liquid** (the tank's liquid sensor): the controller saves a breakpoint where spraying
  stopped and returns home. **Low battery**: when what is left of the usable pack is only
  enough to fly home and land, it saves a breakpoint and returns. (Brochure: breakpoint
  continuation, return on low battery / low liquid / lost link / manual.)
* **Breakpoint resume.** The next sortie flies to the breakpoint and continues the route from
  there.
* **Energy.** The T1 airframe model: hover or cruise power at the mass on board (power ∝
  mass^1.5), a 710 Wh pack, a 30 % reserve.

The result per sortie: the 5 Hz track (position, height, speed, spray, true flow, tank,
energy), the end reason, and where the breakpoint fell. FieldPilot loads each sortie to the
litre so the tank runs dry where its plan cut the route; ``compare_with_plan`` measures how
far, in metres, the controller's breakpoints land from those planned cuts.

Every assumption above is a parameter of ``K3Params``. None of this is a flight log.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .field import Field

LABEL = "SIMULATED (K3-A Pro behavioural twin)"


@dataclass
class K3Params:
    accel_ms2: float = 1.5            # assumption: along-track acceleration and braking
    climb_ms: float = 2.0             # T1 profile
    descend_ms: float = 1.5
    transit_ms: float = 6.0           # T1 profile (sheet: 7 m/s operating, 10 max)
    turn_ms: float = 1.5              # headland side-step between lines
    pump_cutoff_ms: float = 0.5       # brochure
    low_liquid_l: float = 0.02        # liquid sensor trips with the line still primed
    land_reserve_s: float = 25.0      # energy kept for descent and landing
    pump_tau_s: float = 0.25          # emulator nominal
    pump_dead_s: float = 0.15
    dt: float = 0.2                   # 5 Hz


@dataclass
class K3Sortie:
    idx: int
    load_l: float
    end: str = ""                       # low_liquid | low_battery | route_complete
    flight_s: float = 0.0
    sprayed_l: float = 0.0
    left_in_tank_l: float = 0.0
    energy_wh: float = 0.0
    battery_left_pct: float = 0.0
    breakpoint_xy: tuple[float, float] | None = None
    breakpoint_route_m: float | None = None
    events: list[dict] = field(default_factory=list)
    track: list[list] = field(default_factory=list)    # t, x, y, alt, v, spray, q_lpm, tank_l, wh, zone


class _Route:
    """The route as a list of legs (kind, a, b, zone) with cumulative distance along it."""

    def __init__(self, legs):
        self.legs = [lg for lg in legs if lg[0] in ("transit", "spray", "turn")]
        self.cum = [0.0]
        for _, a, b, _z in self.legs:
            self.cum.append(self.cum[-1] + math.dist(a, b))

    @property
    def length(self) -> float:
        return self.cum[-1]

    def at(self, s: float):
        """(leg index, point) at distance s along the route."""
        for i, (_, a, b, _z) in enumerate(self.legs):
            if s <= self.cum[i + 1] or i == len(self.legs) - 1:
                L = self.cum[i + 1] - self.cum[i]
                f = 0.0 if L <= 0 else min(1.0, max(0.0, (s - self.cum[i]) / L))
                return i, (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
        return len(self.legs) - 1, self.legs[-1][2]


class K3Twin:
    def __init__(self, field_: Field, zone_ids: list[str], spec, params: K3Params | None = None):
        from .sorties import _legs
        self.f, self.spec, self.p = field_, spec, params or K3Params()
        legs = _legs(field_, zone_ids, spec)
        self.route = _Route(legs)
        # zone entries: the controller settles and opens the valve (same allowance as the planner)
        self.enter_at = {round(self.route.cum[i], 3) for i, lg in enumerate(self.route.legs)
                         if lg[0] == "spray" and (i == 0 or self.route.legs[i - 1][0] == "transit")}
        self.alt = field_.treat_alt_m
        self.s = 0.0                       # progress along the route (m): the breakpoint memory
        self.t = 0.0

    # -- physics ---------------------------------------------------------------------------
    def _power(self, v: float, tank_l: float) -> float:
        ml = tank_l * 1000.0
        return self.spec.hover_w(ml) if v < 1.0 else self.spec.cruise_w(ml)

    def _flow_target(self, v: float) -> float:
        if v < self.p.pump_cutoff_ms:
            return 0.0
        return min(self.spec.pump_max_l_min or 1e9, self.spec.flow_l_min(v))

    def fly(self, load_l: float, idx: int) -> K3Sortie:
        p, sp = self.p, self.spec
        so = K3Sortie(idx=idx, load_l=load_l)
        tank = load_l
        wh = 0.0
        usable = sp.battery_wh * (1 - sp.reserve_frac)
        q = 0.0
        hist: list[float] = []                   # commanded flow, for the pump's dead time
        x = y = 0.0
        alt = 0.0
        v = 0.0
        t = 0.0
        dead_n = max(0, int(round(p.pump_dead_s / p.dt)))

        def step(vx, vy, valt, spraying, zone):
            nonlocal x, y, alt, t, wh, tank, q, v
            v = math.hypot(vx, vy)
            target = self._flow_target(v) if spraying else 0.0
            hist.append(target)
            cmd = hist[-1 - dead_n] if len(hist) > dead_n else 0.0
            q += (cmd - q) * (1 - math.exp(-p.dt / p.pump_tau_s))
            if q < 1e-4:
                q = 0.0
            used = min(tank, q / 60.0 * p.dt)
            tank -= used
            so.sprayed_l += used
            wh += self._power(v if valt == 0 else 0.0, tank) * p.dt / 3600.0
            x += vx * p.dt
            y += vy * p.dt
            alt = max(0.0, alt + valt * p.dt)
            t += p.dt
            so.track.append([round(t, 2), round(x, 2), round(y, 2), round(alt, 2), round(v, 2),
                             1 if spraying and q > 0.05 else 0, round(q, 3), round(tank, 4),
                             round(wh, 2), zone or ""])

        def goto(target, speed, spraying=False, zone="", stop_end=True, check=None):
            """Fly straight to target with a trapezoidal speed profile. check() may abort."""
            nonlocal v
            sx, sy = x, y
            L = math.dist((sx, sy), target)
            if L < 1e-6:
                return None
            ux, uy = (target[0] - sx) / L, (target[1] - sy) / L
            d = 0.0
            vv = 0.0
            while d < L - 1e-6:
                remaining = L - d
                vmax_stop = math.sqrt(2 * p.accel_ms2 * remaining) if stop_end else speed
                vv = min(speed, vv + p.accel_ms2 * p.dt, max(vmax_stop, 0.3))
                stepd = min(vv * p.dt, remaining)
                vv_eff = stepd / p.dt
                step(ux * vv_eff, uy * vv_eff, 0.0, spraying, zone)
                d += stepd
                if check is not None:
                    why = check(d)
                    if why:
                        return why
            return None

        def vertical(to_alt, rate):
            while abs(alt - to_alt) > 1e-3:
                dz = max(-rate * p.dt, min(rate * p.dt, to_alt - alt))
                step(0.0, 0.0, dz / p.dt, False, "")
                if abs(dz) < 1e-6:
                    break

        def home_need_wh() -> float:
            d = math.hypot(x, y)
            return (d / p.transit_ms * sp.cruise_w(tank * 1000) + (alt / p.descend_ms + p.land_reserve_s)
                    * sp.hover_w(tank * 1000)) / 3600.0

        so.events.append({"t": 0.0, "event": "takeoff", "load_l": round(load_l, 3)})
        vertical(self.alt, p.climb_ms)
        start_s = self.s
        if start_s > 0:                                  # breakpoint resume
            _, bp = self.route.at(start_s)
            goto(bp, p.transit_ms)
            so.events.append({"t": round(t, 1), "event": "resume_at_breakpoint",
                              "x": round(bp[0], 2), "y": round(bp[1], 2)})
        end = "route_complete"
        li = self.route.at(self.s)[0]
        while li < len(self.route.legs):
            kind, a, b, z = self.route.legs[li]
            seg_start = self.route.cum[li]
            _, here = self.route.at(max(self.s, seg_start))
            if math.dist((x, y), here) > 0.05:
                goto(here, p.transit_ms)
            spraying = kind == "spray"
            if spraying and round(max(self.s, seg_start), 3) in self.enter_at:
                from .sorties import ZONE_OVERHEAD_S
                for _ in range(int(round(ZONE_OVERHEAD_S / p.dt))):
                    step(0.0, 0.0, 0.0, False, "")
            speed = sp.spray_speed_ms if spraying else (p.turn_ms if kind == "turn" else p.transit_ms)
            base = max(self.s, seg_start)

            def check(d, base=base, spraying=spraying):
                self.s = base + d
                if spraying and tank <= p.low_liquid_l:
                    return "low_liquid"
                if usable - wh <= home_need_wh():
                    return "low_battery"
                return None

            why = goto(b, speed, spraying=spraying, zone=z if spraying else "",
                       stop_end=True, check=check)
            if why:
                end = why
                so.breakpoint_xy = (round(x, 2), round(y, 2))
                so.breakpoint_route_m = round(self.s, 2)
                so.events.append({"t": round(t, 1), "event": why, "x": round(x, 2), "y": round(y, 2),
                                  "route_m": round(self.s, 2), "tank_l": round(tank, 3)})
                break
            self.s = self.route.cum[li + 1]
            li += 1
        # return to launch and land
        so.events.append({"t": round(t, 1), "event": "return_home"})
        goto((0.0, 0.0), p.transit_ms)
        vertical(0.0, p.descend_ms)
        so.events.append({"t": round(t, 1), "event": "landed"})
        so.end, so.flight_s = end, round(t, 1)
        so.left_in_tank_l = round(tank, 4)
        so.sprayed_l = round(so.sprayed_l, 4)
        so.energy_wh = round(wh, 1)
        so.battery_left_pct = round(100 * (1 - wh / sp.battery_wh), 1)
        return so

    @property
    def done(self) -> bool:
        return self.s >= self.route.length - 0.05


def fly_card(field_: Field, zone_ids: list[str], spec, loads_l: list[float],
             params: K3Params | None = None, max_sorties: int = 20) -> list[K3Sortie]:
    """Fly the card's sorties in order; extra sorties (full tank) if the route is not done."""
    tw = K3Twin(field_, zone_ids, spec, params)
    out = []
    loads = list(loads_l)
    while len(out) < max_sorties:
        load = loads[len(out)] if len(out) < len(loads) else spec.tank_ml / 1000.0
        out.append(tw.fly(load, len(out) + 1))
        if tw.done:
            break
    return out


def compare_with_plan(field_: Field, plan_sorties: list, flown: list[K3Sortie]) -> dict:
    """Where the controller's breakpoints fell vs where FieldPilot's plan cut the route."""
    fr = field_.frame
    rows = []
    for i, ps in enumerate(plan_sorties[:-1]):
        nxt = plan_sorties[i + 1]
        planned = None
        if nxt.resume:
            planned = fr.to_local(nxt.resume["lat"], nxt.resume["lon"])
        k = flown[i] if i < len(flown) else None
        err = (round(math.dist(planned, k.breakpoint_xy), 2)
               if planned is not None and k is not None and k.breakpoint_xy else None)
        rows.append({"sortie": i + 1, "planned_cut_xy": [round(c, 2) for c in planned] if planned else None,
                     "k3_breakpoint_xy": list(k.breakpoint_xy) if k and k.breakpoint_xy else None,
                     "k3_end": k.end if k else None, "distance_m": err})
    return {"planned_sorties": len(plan_sorties), "k3_sorties": len(flown),
            "same_count": len(plan_sorties) == len(flown), "cuts": rows,
            "max_cut_error_m": max((r["distance_m"] for r in rows if r["distance_m"] is not None),
                                   default=None)}


def coverage(field_: Field, zone_ids: list[str], flown: list[K3Sortie], swath_m: float) -> dict:
    """Share of each zone's area under the spray swath while the pump was flowing, and
    the share sprayed twice (overlap at turns and at the breakpoints)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    fr = field_.frame
    strips = []
    for so in flown:
        seg = []
        for r in so.track:
            if r[5]:
                seg.append((r[1], r[2]))
            elif len(seg) >= 2:
                strips.append(LineString(seg).buffer(swath_m / 2, cap_style=2))
                seg = []
            else:
                seg = []
        if len(seg) >= 2:
            strips.append(LineString(seg).buffer(swath_m / 2, cap_style=2))
    union = unary_union(strips) if strips else None
    out = {}
    for z in zone_ids:
        poly = Polygon([fr.to_local(a, b) for a, b in field_.zone(z).polygon])
        cov = union.intersection(poly).area / poly.area if union is not None else 0.0
        twice = 0.0
        if strips:
            tot = sum(s.intersection(poly).area for s in strips)
            twice = max(0.0, tot - union.intersection(poly).area) / poly.area
        out[z] = {"covered_pct": round(100 * cov, 1), "sprayed_twice_pct": round(100 * twice, 1)}
    return out
