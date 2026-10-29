"""The Government of India's drone-spraying SOPs, as checks a mission must pass.

    from fieldpilot import sop
    report = sop.assess(sop.Plan(...))       # -> Report(verdict, checks)

Nemotron proposes and the crop twin decides whether spraying *pays*. This module
decides whether it is *permitted*, on the day and at the hour proposed, by the
two documents an Indian drone operator is actually held to:

* **SOP-2021** - Standard Operating Procedure for use of drones for pesticide
  application, MoA&FW, adopted at the 434th meeting of the Registration Committee,
  30 Nov 2021. Who may fly, what may be sprayed, notice, buffers, records.
* **SOP-2023** - Crop-specific SOP for drone application of pesticides, MoA&FW,
  Krishi Bhawan, 24 Feb 2023. The numbers: water volume, height, speed, swath,
  droplet size, wind, temperature, humidity, rain, flowering.

Every rule below carries the clause it comes from, and every check reports the
value it measured, the limit, and that clause - so a refusal on the console
reads as a citation, not as the model's opinion.

Three checks are fed by the crop twin rather than by the operator: the
flowering stage comes from WOFOST's development stage (DVS), the pre-harvest
interval is checked against the twin's simulated maturity date, and the water
volume follows the canopy stage. That is the point of having a twin: the law is
written in terms of the crop's state, and the twin is the only part of the
system that knows it.

Verdicts: ``CLEAR`` - every check passed. ``BLOCKED`` - a hard rule failed; the
mission cannot be dispatched. ``NEEDS_HUMAN`` - something could not be verified
(no forecast, no label row, no notice on record); a human can clear it, the
agent cannot.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SOP_2021 = "SOP for drone pesticide application, MoA&FW (434th RC, 30 Nov 2021)"
SOP_2023 = "Crop-specific SOP for drone pesticide application, MoA&FW (24 Feb 2023)"


@dataclass(frozen=True)
class Rule:
    key: str
    text: str
    source: str
    hard: bool = True          # False: advisory, never blocks on its own


RULES = {r.key: r for r in [
    Rule("registered", "Only CIB&RC-approved pesticides, within the CIB&RC-approved dose range",
         f"{SOP_2021} §5.2"),
    Rule("wind", "Do not operate above 3 m/s wind", f"{SOP_2023}, weather limits"),
    Rule("temperature", "Avoid spraying above 35 °C", f"{SOP_2023}, weather limits"),
    Rule("humidity", "Avoid spraying below 50 % relative humidity", f"{SOP_2023}, weather limits"),
    Rule("rain", "Do not spray in rain, or with heavy rain forecast within 1-2 days",
         f"{SOP_2023}, weather limits"),
    Rule("window", "Spray in the early morning (06-11) or late afternoon (14-18)",
         f"{SOP_2023}, operational guidance", hard=False),
    Rule("flowering", "Strictly avoid spraying during the flowering stage",
         f"{SOP_2023} (rice, maize, pigeon pea, safflower, sesame, soybean)"),
    Rule("height", "Fly 1.5-2.5 m above the canopy", f"{SOP_2023}, flight parameters"),
    Rule("speed", "Spray at 4.5-5.0 m/s (soybean 3.5-4.5, sugarcane 2.0-3.0)",
         f"{SOP_2023}, flight parameters"),
    Rule("swath", "Swath 3-6 m", f"{SOP_2023}, flight parameters"),
    Rule("volume", "Water volume 20 L/ha at early stage, 25 L/ha at full canopy",
         f"{SOP_2023}, spray volume"),
    Rule("phi", "Harvest no sooner than the label's waiting period after spraying",
         "CIB&RC major-use label (waiting period, days)"),
    Rule("notice", "Written notice to the Gram Panchayat and the Agriculture Officer at least "
         "24 h before any aerial operation", f"{SOP_2021} §2(d)"),
    Rule("buffer", "Keep at least 100 m from water bodies, villages, schools and other "
         "non-target areas", f"{SOP_2023}; {SOP_2021} §4.1(xv)"),
    Rule("pilot", "Only DGCA-certified remote pilots", f"{SOP_2021} §5.4(i)"),
]}

# SOP-2023 lists flowering as a no-spray stage for these crops (not wheat, cotton,
# groundnut or sugarcane).
FLOWERING_SENSITIVE = {"rice", "paddy", "maize", "pigeon pea", "pigeonpea", "safflower",
                       "sesame", "soybean"}
# WOFOST: DVS 0 = emergence, 1 = anthesis, 2 = maturity. The SOP says "flowering stage";
# anthesis-centred window, deliberately wide. FieldPilot's reading of the SOP, not its text.
FLOWERING_DVS = (0.90, 1.15)
FULL_CANOPY_DVS = 0.65          # past this, SOP-2023's "stage 2" water volume applies
SPEED_LIMITS = {"soybean": (3.5, 4.5), "sugarcane": (2.0, 3.0)}
DEFAULT_SPEED = (4.5, 5.0)


@dataclass
class Check:
    rule: str
    ok: bool | None              # None = could not be verified
    value: str
    limit: str
    source: str
    hard: bool = True
    note: str = ""

    @property
    def status(self) -> str:
        return "pass" if self.ok else ("unverified" if self.ok is None else "FAIL")


@dataclass
class Hour:
    time: datetime               # timezone-aware
    wind_ms: float
    temp_c: float
    rh_pct: float
    precip_mm: float
    precip_prob_pct: float | None = None
    wind_from_deg: float | None = None    # direction the wind blows FROM (forecast), for drift


@dataclass
class Plan:
    crop: str
    when: datetime                       # planned start, timezone-aware
    duration_min: float
    dvs: float | None = None             # from the crop twin
    maturity: datetime | None = None     # simulated maturity date, from the crop twin
    height_m: float | None = None        # above canopy
    speed_ms: float | None = None
    swath_m: float | None = None
    volume_l_ha: float | None = None
    waiting_days: float | None = None    # label pre-harvest interval
    label_ok: bool | None = None         # label.verify() verdict: True/False/None
    label_note: str = ""
    notice_sent: datetime | None = None
    pilot_dgca: bool | None = None
    buffer_m: float | None = None        # nearest sensitive feature, if known
    forecast: list[Hour] = field(default_factory=list)


@dataclass
class Report:
    verdict: str                          # CLEAR | BLOCKED | NEEDS_HUMAN
    checks: list[Check]
    earliest_clear: datetime | None = None   # next window when the weather would pass

    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.hard and c.ok is False]

    def unverified(self) -> list[Check]:
        return [c for c in self.checks if c.hard and c.ok is None]

    def to_dict(self) -> dict:
        return {"verdict": self.verdict,
                "earliest_clear": self.earliest_clear.isoformat() if self.earliest_clear else None,
                "checks": [asdict(c) | {"status": c.status} for c in self.checks]}

    def headline(self) -> str:
        if self.verdict == "CLEAR":
            return "SOP: clear to fly"
        bad = self.failed() or self.unverified()
        return f"SOP: {self.verdict.replace('_', ' ').lower()} - " + "; ".join(
            f"{c.rule} ({c.value} vs {c.limit})" for c in bad[:3])


def _c(rule: str, ok: bool | None, value: str, limit: str, note: str = "") -> Check:
    r = RULES[rule]
    return Check(rule, ok, value, limit, r.source, r.hard, note)


def water_volume(crop: str, dvs: float | None) -> float:
    """SOP-2023 water volume, L/ha, by canopy stage."""
    return 25.0 if (dvs is not None and dvs >= FULL_CANOPY_DVS) else 20.0


def tank_mix(formulation_per_ha: float, volume_l_ha: float, tank_l: float) -> float:
    """Formulation (g or ml) to put in one tank.

    The label's dose is per *hectare* and assumes ~500 L of water (a knapsack). A drone
    applies the same dose in 20-25 L/ha, so the tank is 20-25x more concentrated than
    the label's dilution. Mixing "as per label, 1 ml per litre" in a drone tank
    under-doses by that factor; dividing the per-hectare dose by the drone's water
    volume is the only arithmetic that keeps the dose legal.
    """
    if volume_l_ha <= 0 or tank_l <= 0:
        raise ValueError("volume and tank size must be positive")
    return formulation_per_ha / volume_l_ha * tank_l


def _weather_checks(p: Plan) -> tuple[list[Check], list[Hour]]:
    if not p.forecast:
        return [_c(r, None, "no forecast", RULES[r].text.split(" ", 1)[1])
                for r in ("wind", "temperature", "humidity", "rain")], []
    start, end = p.when, p.when + timedelta(minutes=max(1.0, p.duration_min))
    span = [h for h in p.forecast if start - timedelta(minutes=59) <= h.time <= end]
    if not span:
        return [_c(r, None, "forecast does not cover the slot", "") for r in
                ("wind", "temperature", "humidity", "rain")], []
    wind = max(h.wind_ms for h in span)
    temp = max(h.temp_c for h in span)
    rh = min(h.rh_pct for h in span)
    ahead = [h for h in p.forecast if start - timedelta(hours=1) <= h.time <= start + timedelta(hours=48)]
    rain_now = max(h.precip_mm for h in span)
    rain_48 = sum(h.precip_mm for h in ahead)
    out = [
        _c("wind", wind < 3.0, f"{wind:.1f} m/s", "< 3 m/s",
           "10 m wind from the forecast; wind at spray height is lower, so this is conservative"),
        _c("temperature", temp <= 35.0, f"{temp:.0f} °C", "≤ 35 °C"),
        _c("humidity", rh >= 50.0, f"{rh:.0f} %", "≥ 50 %"),
        _c("rain", rain_now < 0.1 and rain_48 < 10.0,
           f"{rain_now:.1f} mm in slot, {rain_48:.0f} mm next 48 h",
           "dry slot, < 10 mm next 48 h", "'heavy rain' read as ≥ 10 mm over 48 h"),
    ]
    return out, span


def _hour_ok(h: Hour) -> bool:
    lt = h.time.astimezone(IST).hour
    return (h.wind_ms < 3.0 and h.temp_c <= 35.0 and h.rh_pct >= 50.0 and h.precip_mm < 0.1
            and (6 <= lt < 11 or 14 <= lt < 18))


def next_clear(forecast: list[Hour], after: datetime, duration_min: float) -> datetime | None:
    """The earliest start, inside a spray window, at which every weather rule holds."""
    need = max(1, math.ceil(duration_min / 60))
    hs = sorted((h for h in forecast if h.time >= after - timedelta(minutes=59)),
                key=lambda h: h.time)
    for i in range(len(hs) - need + 1):
        run = hs[i:i + need]
        contiguous = all((b.time - a.time) <= timedelta(hours=1, minutes=5)
                         for a, b in zip(run, run[1:]))
        if contiguous and all(_hour_ok(h) for h in run):
            rain_48 = sum(h.precip_mm for h in hs if run[0].time <= h.time <= run[0].time + timedelta(hours=48))
            if rain_48 < 10.0:
                return run[0].time
    return None


def assess(p: Plan, now: datetime | None = None) -> Report:
    now = now or datetime.now(timezone.utc)
    crop = p.crop.lower().strip()
    checks: list[Check] = []

    checks.append(_c("registered", p.label_ok,
                     {True: "label row found, dose in range", False: "not registered / dose out of range",
                      None: "label not verified"}[p.label_ok],
                     "CIB&RC major-use row for this crop and pest", p.label_note))

    wx, _ = _weather_checks(p)
    checks += wx
    lt = p.when.astimezone(IST)
    end = (p.when + timedelta(minutes=p.duration_min)).astimezone(IST)
    in_window = ((6 <= lt.hour and end.hour + end.minute / 60 <= 11) or
                 (14 <= lt.hour and end.hour + end.minute / 60 <= 18))
    checks.append(_c("window", in_window, f"{lt:%H:%M}-{end:%H:%M} IST", "06-11 or 14-18 IST"))

    if crop in FLOWERING_SENSITIVE:
        if p.dvs is None:
            checks.append(_c("flowering", None, "no crop stage", "not flowering"))
        else:
            lo, hi = FLOWERING_DVS
            fl = lo <= p.dvs <= hi
            checks.append(_c("flowering", not fl, f"DVS {p.dvs:.2f}"
                             + (" (flowering)" if fl else ""), f"DVS outside {lo:.2f}-{hi:.2f}",
                             "DVS from the crop twin; 1.0 = anthesis"))

    def rng(rule, v, lo, hi, unit):
        if v is None:
            checks.append(_c(rule, None, "not set", f"{lo}-{hi} {unit}"))
        else:
            checks.append(_c(rule, lo <= v <= hi, f"{v:g} {unit}", f"{lo:g}-{hi:g} {unit}"))

    rng("height", p.height_m, 1.5, 2.5, "m")
    s_lo, s_hi = SPEED_LIMITS.get(crop, DEFAULT_SPEED)
    rng("speed", p.speed_ms, s_lo, s_hi, "m/s")
    rng("swath", p.swath_m, 3.0, 6.0, "m")
    want = water_volume(crop, p.dvs)
    if p.volume_l_ha is None:
        checks.append(_c("volume", None, "not set", f"{want:g} L/ha"))
    else:
        checks.append(_c("volume", abs(p.volume_l_ha - want) <= 2.5, f"{p.volume_l_ha:g} L/ha",
                         f"{want:g} L/ha at this stage"))

    if p.waiting_days is None or p.maturity is None:
        checks.append(_c("phi", None, "waiting period or harvest date unknown",
                         "harvest ≥ spray + waiting period"))
    else:
        days = (p.maturity - p.when).total_seconds() / 86400
        checks.append(_c("phi", days >= p.waiting_days,
                         f"harvest in {days:.0f} d (twin maturity {p.maturity:%d %b})",
                         f"≥ {p.waiting_days:g} d", "maturity from the crop twin"))

    if p.notice_sent is None:
        checks.append(_c("notice", None, "no notice on record", "sent ≥ 24 h before"))
    else:
        h = (p.when - p.notice_sent).total_seconds() / 3600
        checks.append(_c("notice", h >= 24.0, f"sent {h:.0f} h before", "≥ 24 h"))

    if p.buffer_m is None:
        checks.append(_c("buffer", None, "not surveyed", "≥ 100 m"))
    else:
        checks.append(_c("buffer", p.buffer_m >= 100.0, f"{p.buffer_m:.0f} m", "≥ 100 m"))

    checks.append(_c("pilot", p.pilot_dgca, {True: "DGCA certificate on file", False: "not certified",
                                             None: "not recorded"}[p.pilot_dgca], "DGCA RPC"))

    hard = [c for c in checks if c.hard]
    if any(c.ok is False for c in hard):
        verdict = "BLOCKED"
    elif any(c.ok is None for c in hard):
        verdict = "NEEDS_HUMAN"
    else:
        verdict = "CLEAR"
    earliest = None
    if p.forecast and any(c.rule in ("wind", "temperature", "humidity", "rain") and c.ok is False
                          for c in checks):
        earliest = next_clear(p.forecast, max(p.when, now), p.duration_min)
    return Report(verdict, checks, earliest)


# --------------------------------------------------------------------------- forecast

def fetch_forecast(lat: float, lon: float, days: int = 3, timeout_s: float = 10.0) -> list[Hour]:
    """Hourly forecast from Open-Meteo (no key). Wind in m/s at 10 m."""
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "forecast_days": days,
        "hourly": "wind_speed_10m,temperature_2m,relative_humidity_2m,precipitation,"
                  "precipitation_probability,wind_direction_10m",
        "wind_speed_unit": "ms", "timezone": "UTC"})
    with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{q}", timeout=timeout_s) as r:
        d = json.loads(r.read().decode("utf-8"))
    return parse_open_meteo(d)


def parse_open_meteo(d: dict) -> list[Hour]:
    h = d["hourly"]
    probs = h.get("precipitation_probability") or [None] * len(h["time"])
    dirs = h.get("wind_direction_10m") or [None] * len(h["time"])
    out = []
    for t, w, tc, rh, pr, pp, wd in zip(h["time"], h["wind_speed_10m"], h["temperature_2m"],
                                        h["relative_humidity_2m"], h["precipitation"], probs, dirs):
        if None in (w, tc, rh, pr):
            continue
        out.append(Hour(datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                        float(w), float(tc), float(rh), float(pr),
                        None if pp is None else float(pp), None if wd is None else float(wd)))
    return out
