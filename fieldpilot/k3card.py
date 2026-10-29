"""Flight cards for a closed agricultural flight controller (the T1's JIYI K3-A Pro).

The K3-A Pro has no open protocol: missions are planned in JIYI's own app, on the pilot's
remote or tablet, and FieldPilot cannot upload to it. So on this aircraft FieldPilot does
everything up to the take-off decision - which zones, which sorties, how much to load, the
weather and rule gate, a human approval per sortie - and hands the pilot a card with exactly
what to enter in the app, plus a KML of the same zones and routes.

    card = flight_card(field, mission, spec)       # dict
    html = card_html(card)                         # printable, one page per job
    kml  = mission_kml(field, mission)             # zones, routes, take-off, resume points

The numbers on the card are the plan's numbers; nothing here re-plans.
"""
from __future__ import annotations

import math
from html import escape
from xml.sax.saxutils import escape as xml_escape

MU_M2 = 10_000.0 / 15.0          # 1 mu = 666.7 m2: JIYI's app sets dose per mu
SOP_HEIGHT_ABOVE_CANOPY_M = (1.5, 2.5)


def _heading(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial bearing a -> b in degrees (0 = north, clockwise)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _pass_heading(items: list[dict], zone: str) -> float | None:
    """Bearing of the first spray pass in ``zone`` (the app's route direction)."""
    on, pts, last = False, [], None
    for it in items:
        if it["kind"] == "spray_on" and it.get("zone_id") == zone:
            on = True
            if last is not None and not pts:
                pts.append(last)                 # the pass starts where the aircraft is
        elif it["kind"] == "spray_off":
            on = False
        elif it["kind"] == "waypoint":
            last = (it["lat"], it["lon"])
            if on:
                pts.append(last)
                if len(pts) == 2:
                    break
    if len(pts) < 2:
        return None
    h = _heading(pts[0], pts[1])
    return round(h % 180.0, 0)       # passes run both ways; the app takes the line direction


def _last_wp(items: list[dict]) -> tuple[float, float] | None:
    """Position just before ``rtl`` - where this sortie stops spraying."""
    last = None
    for it in items:
        if it["kind"] == "waypoint":
            last = (round(it["lat"], 7), round(it["lon"], 7))
    return last


def flight_card(field_, mission, spec, approved_by: str | None = None, drift: dict | None = None) -> dict:
    """Everything the pilot enters in the K3-A Pro app, sortie by sortie.

    ``drift``: when FieldPilot moved the zones upwind (fieldpilot/drift.py), ``field_`` is the
    moved field and this is its info; the card says so, so the pilot enters the moved corners."""
    m = mission
    rows = (m.load or {}).get("rows") or []
    sorties = []
    for i, s in enumerate(m.sorties or []):
        items = s.get("items") or []
        row = rows[i] if i < len(rows) else {}
        zones = []
        for z in s["zones"]:
            zone = field_.zone(z)
            zones.append({"zone_id": z,
                          "corners": [[round(a, 7), round(b, 7)] for a, b in zone.polygon],
                          "route_heading_deg": _pass_heading(items, z)})
        sorties.append({
            "sortie": s["idx"], "zones": zones,
            "load_water_l": row.get("water_l", round(s["spray_ml"] / 1000.0, 2)),
            "load_product": row.get("product"), "unit": m.load.get("unit") if m.load else None,
            "flight_min": round(s["est_time_s"] / 60.0, 1),
            "battery_pct_planned": row.get("battery_pct_used"),
            "starts_at": s.get("resume"),
            "tank_runs_dry_near": _last_wp(items) if i < len(m.sorties) - 1 else None,
        })
    rate = spec.spray_rate_l_ha
    return {
        "aircraft": spec.name, "flight_controller": "JIYI K3-A Pro (closed; flown from the JIYI app)",
        "field": field_.name, "mission_id": m.mission_id, "approved_by": approved_by,
        "go": bool(m.ok), "failed_checks": [c for c in m.checks if not c["ok"]],
        "takeoff": [round(field_.home[0], 7), round(field_.home[1], 7)],
        "app_settings": {
            "line_spacing_m": spec.swath_m,
            "speed_ms": spec.spray_speed_ms,
            "height_agl_m": field_.treat_alt_m,
            "height_above_canopy_m_sop": list(SOP_HEIGHT_ABOVE_CANOPY_M),
            "dose_l_per_ha": rate,
            "dose_l_per_mu": round(rate * MU_M2 / 10_000.0, 2),
            "flow_l_min": round(spec.flow_l_min(), 2),
            "pump_max_l_min": spec.pump_max_l_min or None,
        },
        "sorties": sorties,
        "drift_compensation": drift,
        "notes": ([f"Drift compensation: these corners are moved {drift['upwind_offset_m']} m upwind for a "
                   f"{drift['wind_ms']} m/s wind from {drift['wind_from_deg']}° (FieldMast speed, forecast "
                   "direction). Enter these corners, not the field's own."] if drift and drift.get("applied") else []) + [
            "Enter each zone as its own field boundary in the app; spray nothing outside it.",
            "Load exactly the water on the card: the tank then runs dry where the plan cuts the "
            "sortie, and the K3-A Pro's own breakpoint resume continues from there.",
            "Hold the remote throughout. One-key return is the abort.",
        ],
    }


def card_html(card: dict) -> str:
    s = card["app_settings"]
    head = ("<b style='color:#0a7d27'>GO: all checks passed</b>" if card["go"] else
            "<b style='color:#b00020'>DO NOT FLY: " +
            escape("; ".join(c["check"] + " - " + c["detail"] for c in card["failed_checks"])) + "</b>")
    rows = []
    for so in card["sorties"]:
        zs = "".join(
            f"<li><b>{escape(z['zone_id'])}</b> heading {z['route_heading_deg'] if z['route_heading_deg'] is not None else '-'}°"
            f"<br><small>{' · '.join(f'{a:.6f}, {b:.6f}' for a, b in z['corners'])}</small></li>"
            for z in so["zones"])
        prod = (f" + {so['load_product']} {escape(str(so['unit'] or 'g'))} product"
                if so["load_product"] else "")
        start = (f"<br>starts at {so['starts_at']['lat']:.6f}, {so['starts_at']['lon']:.6f} "
                 f"(zone {escape(so['starts_at']['zone_id'])})" if so["starts_at"] else "")
        dry = (f"<br>tank runs dry near {so['tank_runs_dry_near'][0]:.6f}, "
               f"{so['tank_runs_dry_near'][1]:.6f}" if so["tank_runs_dry_near"] else "")
        rows.append(f"<tr><td><b>{so['sortie']}</b></td><td>{so['load_water_l']} L{prod}</td>"
                    f"<td><ul>{zs}</ul>{start}{dry}</td><td>{so['flight_min']} min · "
                    f"{so['battery_pct_planned']}% pack</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Flight card {escape(card['mission_id'])}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#111}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #999;padding:6px;vertical-align:top;text-align:left}}ul{{margin:0;padding-left:16px}}
.k{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:12px 0}}.k div{{border:1px solid #999;padding:6px}}</style>
</head><body>
<h2>Flight card · {escape(card['field'])} · {escape(card['mission_id'])}</h2>
<p>{escape(card['aircraft'])} · {escape(card['flight_controller'])}</p>
<p>{head}{' · approved by ' + escape(card['approved_by']) if card.get('approved_by') else ''}</p>
<h3>JIYI app settings</h3>
<div class="k"><div>Line spacing<br><b>{s['line_spacing_m']} m</b></div>
<div>Speed<br><b>{s['speed_ms']} m/s</b></div>
<div>Height<br><b>{s['height_agl_m']} m AGL</b> (SOP: {s['height_above_canopy_m_sop'][0]}-{s['height_above_canopy_m_sop'][1]} m above canopy)</div>
<div>Dose<br><b>{s['dose_l_per_mu']} L/mu</b> ({s['dose_l_per_ha']} L/ha)</div>
<div>Flow at that speed<br><b>{s['flow_l_min']} L/min</b> of {s['pump_max_l_min']} max</div>
<div>Take-off<br><b>{card['takeoff'][0]:.6f}, {card['takeoff'][1]:.6f}</b></div></div>
<h3>Sorties</h3>
<table><tr><th>#</th><th>Load</th><th>Zones (boundary corners, route heading)</th><th>Plan</th></tr>
{''.join(rows)}</table>
<ul>{''.join(f'<li>{escape(n)}</li>' for n in card['notes'])}</ul>
</body></html>"""


def mission_kml(field_, mission) -> str:
    """Zones to treat, each sortie's route, the take-off point and resume points."""
    def coords(pts):
        return " ".join(f"{lon:.7f},{lat:.7f},0" for lat, lon in pts)

    marks = []
    for z in mission.zones:
        poly = list(field_.zone(z).polygon)
        if poly and poly[0] != poly[-1]:
            poly.append(poly[0])
        marks.append(f"<Placemark><name>zone {xml_escape(z)}</name><styleUrl>#zone</styleUrl>"
                     f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(poly)}</coordinates>"
                     "</LinearRing></outerBoundaryIs></Polygon></Placemark>")
    for s in mission.sorties or []:
        pts = [(it["lat"], it["lon"]) for it in s.get("items") or []
               if it["kind"] in ("takeoff", "waypoint", "rtl")]
        if len(pts) >= 2:
            marks.append(f"<Placemark><name>sortie {s['idx']}</name><styleUrl>#route</styleUrl>"
                         f"<LineString><coordinates>{coords(pts)}</coordinates></LineString></Placemark>")
        if s.get("resume"):
            r = s["resume"]
            marks.append(f"<Placemark><name>sortie {s['idx']} starts here</name>"
                         f"<Point><coordinates>{r['lon']:.7f},{r['lat']:.7f},0</coordinates></Point></Placemark>")
    h = field_.home
    marks.append(f"<Placemark><name>take-off</name><Point><coordinates>{h[1]:.7f},{h[0]:.7f},0"
                 "</coordinates></Point></Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>{xml_escape(field_.name)} {xml_escape(mission.mission_id)}</name>"
            '<Style id="zone"><LineStyle><color>ff00a5ff</color><width>2</width></LineStyle>'
            '<PolyStyle><color>5500a5ff</color></PolyStyle></Style>'
            '<Style id="route"><LineStyle><color>ffffffff</color><width>1</width></LineStyle></Style>'
            + "".join(marks) + "</Document></kml>\n")
