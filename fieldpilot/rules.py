"""Spray-weather rule packs: when a drone may and should spray, by jurisdiction.

The planner never hard-codes one country's rules. A pack says which hourly weather is
acceptable and in which local hours spraying is allowed. Two ship:

* ``IN-SOP-2023``  India, MoA&FW crop-specific drone SOP (24 Feb 2023): wind < 3 m/s,
  <= 35 °C, RH >= 50 %, dry, morning and late-afternoon windows. Legally binding there.
* ``GOOD-PRACTICE``  For anywhere without drone-specific rules: wind 1-4.5 m/s (below about
  1 m/s, inversions carry fine droplets off target; above about 4.5 m/s, drift rises), Delta T
  2-10 °C (the evaporation index used in Australian spray guidance: 2-8 is ideal, above 10
  droplets evaporate before they land), no rain in the hour, daylight only. The product label
  always overrides it.

Add a pack by adding an entry to ``PACKS``; nothing else changes. Forecast hours are UTC;
each pack is evaluated in the operator's local time (``utc_offset_h``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta

from .sop import Hour


def wet_bulb_c(t_c: float, rh_pct: float) -> float:
    """Stull (2011) wet-bulb temperature, valid for 5-99 % RH and -20..50 °C."""
    rh = min(99.0, max(5.0, rh_pct))
    return (t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t_c + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
            - 4.686035)


def delta_t(t_c: float, rh_pct: float) -> float:
    return t_c - wet_bulb_c(t_c, rh_pct)


@dataclass(frozen=True)
class Pack:
    pack_id: str
    title: str
    wind_max_ms: float
    wind_min_ms: float = 0.0
    temp_max_c: float | None = None
    rh_min_pct: float | None = None
    delta_t_range: tuple[float, float] | None = None
    max_rain_mm_h: float = 0.1
    hours: tuple[tuple[int, int], ...] = ((6, 19),)       # local [start, end) windows
    binding: bool = False                                  # law, or guidance?
    sources: tuple[str, ...] = field(default_factory=tuple)

    def reasons(self, h: Hour, utc_offset_h: float) -> list[str]:
        """Why this hour fails (empty = sprayable)."""
        out = []
        lt = (h.time + timedelta(hours=utc_offset_h)).hour
        if not any(a <= lt < b for a, b in self.hours):
            out.append(f"{lt:02d}:00 outside spray hours")
        out += self.site_reasons(h.wind_ms, h.temp_c, h.rh_pct)
        if h.precip_mm >= self.max_rain_mm_h:
            out.append(f"rain {h.precip_mm:.1f} mm")
        return out

    def site_reasons(self, wind_ms: float, temp_c: float, rh_pct: float) -> list[str]:
        """The weather limits alone: used for forecast hours and for a mast at the field.
        (firmware/common/fieldpilot_logic.h mirrors these thresholds for the mast's LEDs.)"""
        out = []
        if wind_ms >= self.wind_max_ms:
            out.append(f"wind {wind_ms:.1f} ≥ {self.wind_max_ms} m/s")
        if wind_ms < self.wind_min_ms:
            out.append(f"wind {wind_ms:.1f} < {self.wind_min_ms} m/s (inversion risk)")
        if self.temp_max_c is not None and temp_c > self.temp_max_c:
            out.append(f"{temp_c:.0f} °C > {self.temp_max_c:.0f} °C")
        if self.rh_min_pct is not None and rh_pct < self.rh_min_pct:
            out.append(f"RH {rh_pct:.0f} % < {self.rh_min_pct:.0f} %")
        if self.delta_t_range is not None:
            dt = delta_t(temp_c, rh_pct)
            lo, hi = self.delta_t_range
            if not lo <= dt <= hi:
                out.append(f"Delta T {dt:.1f} outside {lo:g}-{hi:g}")
        return out

    def ok(self, h: Hour, utc_offset_h: float) -> bool:
        return not self.reasons(h, utc_offset_h)


PACKS: dict[str, Pack] = {
    "IN-SOP-2023": Pack(
        "IN-SOP-2023", "India · MoA&FW crop-specific drone SOP (2023)",
        wind_max_ms=3.0, temp_max_c=35.0, rh_min_pct=50.0, hours=((6, 11), (14, 18)),
        binding=True,
        sources=("https://croplifeindia.org/wp-content/uploads/2024/01/TTC_SOP_2023-4.pdf",)),
    "GOOD-PRACTICE": Pack(
        "GOOD-PRACTICE", "Drift and evaporation good practice (no drone-specific law)",
        wind_max_ms=4.5, wind_min_ms=1.0, delta_t_range=(2.0, 10.0), hours=((6, 19),),
        binding=False,
        sources=("Delta T 2-8 ideal, >10 unsuitable: Australian (GRDC) spray-application guidance",
                 "wind roughly 3-10 mph (1.3-4.5 m/s): common pesticide-label / extension drift guidance")),
}


def pack(pack_id: str | None) -> Pack:
    return PACKS[pack_id or "GOOD-PRACTICE"]
