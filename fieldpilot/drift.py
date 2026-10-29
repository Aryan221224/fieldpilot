"""Drift compensation: move the spray lines upwind so the deposit lands on the zone.

Droplets leave the nozzles about 3 m above the crop and are pushed down mostly by the rotor
downwash. In a crosswind the deposit lands downwind of the line the aircraft flew:

    shift = release height x wind speed / effective downwash (~3 m/s for a 24 kg quad)

At the SOP's limit of 3 m/s that is about 3 m, most of a 4 m swath. FieldPilot knows the wind
at take-off: the FieldMast measures its speed at the field, and the forecast gives its
direction. So the flight card moves each zone's boundary upwind by the expected shift, and the
pilot enters the moved corners in the JIYI app. The compensation is capped at 3 m. Above the
cap the wind is outside the SOP anyway, and the approval gate blocks the sortie.

The model (downwash speed, the fine airborne fraction) is an assumption, stated as such. On
the real aircraft, a water-sensitive-paper card at each zone edge measures the actual shift,
and ``DOWNWASH_MS`` gets calibrated from it.
"""
from __future__ import annotations

import math
from dataclasses import replace

DOWNWASH_MS = 3.0          # effective downward drive of the droplets (assumption; calibrate)
AIRBORNE_PER_MS = 0.015    # share of the spray that stays airborne, per m/s of wind (assumption)
MAX_SHIFT_M = 3.0


def deposit_shift(wind_ms: float, wind_from_deg: float, release_m: float) -> tuple[float, float]:
    """(dx, dy) in metres, east/north: where the deposit lands relative to the line flown."""
    s = release_m * max(0.0, wind_ms) / DOWNWASH_MS
    to = math.radians((wind_from_deg + 180.0) % 360.0)
    return s * math.sin(to), s * math.cos(to)


def compensated_field(field_, wind_ms: float, wind_from_deg: float | None):
    """The field with every zone moved upwind by the expected deposit shift (capped).

    Returns (field, info). Without a wind direction nothing is moved."""
    if wind_from_deg is None or wind_ms <= 0:
        return field_, {"applied": False, "why": "no wind direction" if wind_from_deg is None else "calm"}
    dx, dy = deposit_shift(wind_ms, wind_from_deg, field_.treat_alt_m)
    L = math.hypot(dx, dy)
    if L > MAX_SHIFT_M:
        dx, dy = dx * MAX_SHIFT_M / L, dy * MAX_SHIFT_M / L
    fr = field_.frame
    zones = []
    for z in field_.zones:
        pts = [fr.to_wgs84(x - dx, y - dy) for x, y in (fr.to_local(a, b) for a, b in z.polygon)]
        zones.append(replace(z, polygon=[(round(a, 7), round(b, 7)) for a, b in pts]))
    info = {"applied": True, "wind_ms": round(wind_ms, 2), "wind_from_deg": round(wind_from_deg),
            "upwind_offset_m": round(math.hypot(dx, dy), 2),
            "offset_east_m": round(-dx, 2), "offset_north_m": round(-dy, 2)}
    return replace(field_, zones=zones), info
