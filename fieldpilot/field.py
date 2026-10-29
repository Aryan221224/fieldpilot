"""The field: geofence, management zones, home point and crop.

Geometry is done in a local east-north frame (metres) anchored at the home
point. An equirectangular projection is exact to well under a centimetre over a
few hundred metres, which is the scale of anything a small drone treats, and it
avoids a pyproj dependency on the companion computer.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shapely.geometry import Point, Polygon

EARTH_R = 6378137.0


@dataclass(frozen=True)
class LocalFrame:
    lat0: float
    lon0: float

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        x = math.radians(lon - self.lon0) * EARTH_R * math.cos(math.radians(self.lat0))
        y = math.radians(lat - self.lat0) * EARTH_R
        return x, y

    def to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        lat = self.lat0 + math.degrees(y / EARTH_R)
        lon = self.lon0 + math.degrees(x / (EARTH_R * math.cos(math.radians(self.lat0))))
        return lat, lon


@dataclass
class Zone:
    zone_id: str
    polygon: list[tuple[float, float]]          # (lat, lon) ring

    def centroid(self) -> tuple[float, float]:
        p = Polygon([(lon, lat) for lat, lon in self.polygon])
        c = p.centroid
        return c.y, c.x


@dataclass
class Field:
    name: str
    geofence: list[tuple[float, float]]         # (lat, lon) ring the drone may never leave
    home: tuple[float, float]                   # take-off / landing point (lat, lon)
    zones: list[Zone]
    crop: str = "potato"
    variety: str = "Potato_701"
    sowing_date: date = date(2026, 10, 20)
    max_alt_m: float = 15.0                     # hard ceiling, AGL
    min_alt_m: float = 2.0
    treat_alt_m: float = 3.0                    # spray / inspection height
    notes: str = ""
    # Operator paperwork the SOP checks need and no sensor can supply: the product and dose
    # the operator intends to use, when the Gram Panchayat notice went out, the pilot's DGCA
    # certificate, the nearest water body / house / school. Missing keys stay "unverified".
    compliance: dict = field(default_factory=dict)
    frame: LocalFrame = field(init=False)

    def __post_init__(self) -> None:
        self.frame = LocalFrame(*self.home)

    # -- geometry --------------------------------------------------------
    def geofence_local(self) -> Polygon:
        return Polygon([self.frame.to_local(lat, lon) for lat, lon in self.geofence])

    def zone(self, zone_id: str) -> Zone:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        raise KeyError(f"unknown zone {zone_id!r}; field has {[z.zone_id for z in self.zones]}")

    def zone_ids(self) -> list[str]:
        return [z.zone_id for z in self.zones]

    def inside_geofence(self, lat: float, lon: float, margin_m: float = 0.0) -> bool:
        poly = self.geofence_local()
        if margin_m:
            poly = poly.buffer(-margin_m)
        return poly.contains(Point(*self.frame.to_local(lat, lon)))

    def area_m2(self) -> float:
        return float(self.geofence_local().area)

    @property
    def latitude(self) -> float:
        return self.home[0]

    @property
    def longitude(self) -> float:
        return self.home[1]

    # -- io ----------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name, "geofence": self.geofence, "home": self.home,
            "zones": [{"zone_id": z.zone_id, "polygon": z.polygon} for z in self.zones],
            "crop": self.crop, "variety": self.variety,
            "sowing_date": self.sowing_date.isoformat(),
            "max_alt_m": self.max_alt_m, "min_alt_m": self.min_alt_m,
            "treat_alt_m": self.treat_alt_m, "notes": self.notes,
            "area_m2": round(self.area_m2(), 1),
            "compliance": self.compliance,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Field":
        return cls(
            name=d["name"],
            geofence=[tuple(p) for p in d["geofence"]],
            home=tuple(d["home"]),
            zones=[Zone(z["zone_id"], [tuple(p) for p in z["polygon"]]) for z in d["zones"]],
            crop=d.get("crop", "potato"), variety=d.get("variety", "Potato_701"),
            sowing_date=date.fromisoformat(d.get("sowing_date", "2026-10-20")),
            max_alt_m=float(d.get("max_alt_m", 15.0)), min_alt_m=float(d.get("min_alt_m", 2.0)),
            treat_alt_m=float(d.get("treat_alt_m", 3.0)), notes=d.get("notes", ""),
            compliance=dict(d.get("compliance") or {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Field":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def grid_field(name: str, sw_corner: tuple[float, float], width_m: float, height_m: float,
               cols: int, rows: int, home_offset_m: tuple[float, float] = (-4.0, -4.0),
               fence_margin_m: float = 6.0, **kw) -> Field:
    """A rectangular plot split into cols x rows zones (A1, A2, ... B1 ...).

    The geofence is the plot grown by ``fence_margin_m`` so the drone can turn
    at the edge, and the home point sits just outside the south-west corner --
    you never take off over the crop you are about to treat.
    """
    lat0, lon0 = sw_corner
    f0 = LocalFrame(lat0, lon0)
    zones: list[Zone] = []
    zw, zh = width_m / cols, height_m / rows
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * zw, r * zh
            ring = [f0.to_wgs84(x, y) for x, y in
                    ((x0, y0), (x0 + zw, y0), (x0 + zw, y0 + zh), (x0, y0 + zh))]
            zones.append(Zone(f"{chr(ord('A') + r)}{c + 1}", ring))
    m = fence_margin_m
    fence = [f0.to_wgs84(x, y) for x, y in
             ((-m, -m), (width_m + m, -m), (width_m + m, height_m + m), (-m, height_m + m))]
    home = f0.to_wgs84(*home_offset_m)
    return Field(name=name, geofence=fence, home=home, zones=zones, **kw)
