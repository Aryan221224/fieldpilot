"""Crop digital twin: WOFOST ensemble, phenology inversion, disease coupling.

This module is the reason the agent is allowed to act at all. The language
model never decides whether a spray is worth it -- it asks this module, which
simulates the rest of the season under every candidate action across an
ensemble of weather years and uncertain parameters, and returns the whole
distribution of outcomes.

Pieces
------
* **Engine.** PCSE WOFOST 8.1 (nitrogen- and water-limited, classic water
  balance). ~0.15 s per season on one core, so an ensemble is minutes on a
  laptop and seconds as a Nebius serverless job -- no neural surrogate needed.
* **Phenology inversion.** The photo gives a BBCH growth stage; the weather
  record gives thermal time. Walking backwards through the weather until the
  accumulated effective temperature matches that stage recovers an *effective*
  emergence date. One visit calibrates the crop's clock, and a wrong or unknown
  sowing date stops mattering.
* **Disease coupling.** WOFOST has no disease module. Foliar disease is coupled
  in the standard way -- as a daily relative loss of leaf area, applied through
  ``set_variable('LAI')``, which PCSE propagates into leaf biomass and on into
  yield. Severity sets the rate; a treatment scales it by (1 - efficacy). Both
  are sampled, not fixed, and both are documented as a simplified damage model.
* **Feasibility gate.** A photoperiod-sensitive cultivar whose optimum day
  length never occurs at this latitude makes WOFOST return 0 kg/ha *with no
  error*. The twin refuses instead.
* **Weather ensemble.** Observed weather up to today, then analogue years for
  the rest of the season (the same calendar dates from each of the last N
  years). Real inter-annual variability, no weather generator to defend.
"""
from __future__ import annotations

import copy
import functools
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .config import ROOT

log = logging.getLogger(__name__)

VENDOR = ROOT / "vendor"
ENGINE = "Wofost81_NWLP_CWB_CNB"
ENGINE_72 = "Wofost72_WLP_CWB"


def engine_for(category: str) -> str:
    """Pick the WOFOST engine a comparison needs.

    Nitrogen questions need 8.1 (the only engine with an N balance). Disease
    questions need 7.2: the leaf-area coupling works through
    ``set_variable('LAI')``, which only the 7.2 leaf-dynamics module implements
    -- on 8.1 the call returns ``{}`` and silently does nothing, which would make
    every disease look harmless and every spray look worthless. Every option for
    one zone uses the same engine, so comparisons stay paired.
    """
    return ENGINE if category == "nutrient_deficiency" else ENGINE_72

# Fresh-weight conversion and indicative farm-gate price (INR/kg fresh). These
# are assumptions, surfaced in every result so an agronomist can change them.
CROP_ECON = {
    "potato":   {"dm": 0.21, "price": 12.0},
    "maize":    {"dm": 0.86, "price": 22.0},
    "rice":     {"dm": 0.86, "price": 23.0},
    "soybean":  {"dm": 0.88, "price": 45.0},
    "wheat":    {"dm": 0.86, "price": 24.5},
    "chickpea": {"dm": 0.88, "price": 55.0},
    "mungbean": {"dm": 0.88, "price": 80.0},
    "groundnut": {"dm": 0.92, "price": 60.0},
    "sorghum":  {"dm": 0.88, "price": 30.0},
    "millet":   {"dm": 0.88, "price": 25.0},
}

# Daily relative LAI loss for an *untreated* foliar disease/pest, by severity.
# Median values; each ensemble member multiplies by a lognormal factor.
DAMAGE_RATE = {0: 0.0, 1: 0.004, 2: 0.010, 3: 0.020}


@dataclass(frozen=True)
class Intervention:
    code: str                  # no_action | spot_spray | foliar_n | topdress_n | irrigate
    label: str
    drone: bool                # can the drone deliver it?
    cost_inr_ha: float
    n_kg_ha: float = 0.0
    irrigation_cm: float = 0.0
    efficacy: tuple[float, float] = (0.0, 0.0)   # uniform range, disease only


INTERVENTIONS = {
    "no_action":  Intervention("no_action", "No action (re-scout in 7 days)", False, 0.0),
    "spot_spray": Intervention("spot_spray", "Drone spot-spray, protectant fungicide",
                               True, 1100.0, efficacy=(0.55, 0.85)),
    "foliar_n":   Intervention("foliar_n", "Drone foliar urea, 10 kg N/ha", True, 450.0, n_kg_ha=10.0),
    "topdress_n": Intervention("topdress_n", "Manual urea top-dress, 30 kg N/ha", False, 700.0,
                               n_kg_ha=30.0),
    "irrigate":   Intervention("irrigate", "Irrigate 3 cm", False, 600.0, irrigation_cm=3.0),
}


def soil_n_for(category: str, severity: int) -> float:
    """Initial available soil N consistent with what the camera saw.

    A zone reported as nitrogen-deficient is assimilated as N-poor; otherwise
    the simulation would start from a healthy default and conclude that N never
    matters -- contradicting the observation it was asked to act on.
    """
    if category != "nutrient_deficiency":
        return 60.0
    return {1: 30.0, 2: 15.0, 3: 6.0}.get(severity, 60.0)


def options_for(category: str, severity: int) -> list[str]:
    if severity <= 0 or category == "none":
        return ["no_action"]
    return {
        "disease": ["no_action", "spot_spray"],
        "pest": ["no_action", "spot_spray"],
        "nutrient_deficiency": ["no_action", "foliar_n", "topdress_n"],
        "water_stress": ["no_action", "irrigate"],
    }.get(category, ["no_action"])


# ---------------------------------------------------------------------------
# Parameters and feasibility
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def _crop_params(branch: str):
    from pcse.input import YAMLCropDataProvider
    return YAMLCropDataProvider(fpath=str(VENDOR / branch))


@functools.lru_cache(maxsize=64)
def _crop_param_items(crop: str, variety: str, engine: str) -> tuple:
    branch = "wofost81" if engine.startswith("Wofost81") else "wofost72"
    cp = copy.deepcopy(_crop_params(branch))
    cp.set_active_crop(crop, variety)
    return tuple(dict(cp).items())


def crop_params(crop: str, variety: str, engine: str = ENGINE) -> dict:
    """A fresh plain-dict copy of one cultivar's parameters (cached parse)."""
    return copy.deepcopy(dict(_crop_param_items(crop, variety, engine)))


def varieties(crop: str, engine: str = ENGINE_72) -> list[str]:
    branch = "wofost81" if engine.startswith("Wofost81") else "wofost72"
    cp = _crop_params(branch)
    need = {"AMAX_REF"} if branch == "wofost81" else {"AMAXTB"}
    out = []
    for v in cp.get_crops_varieties().get(crop, []):
        c = copy.deepcopy(cp)
        c.set_active_crop(crop, v)
        if need.issubset(set(dict(c))):
            out.append(v)
    return out


def max_daylength_h(latitude: float) -> float:
    from pcse.util import daylength
    return max(float(daylength(date(2001, 1, 1) + timedelta(days=d), latitude, angle=-4.0))
               for d in range(0, 365, 2))


def feasibility(crop: str, variety: str, latitude: float,
                engine: str = ENGINE_72) -> tuple[bool, list[str]]:
    """Refuse cultivars that cannot complete development at this latitude."""
    reasons: list[str] = []
    usable = varieties(crop, engine)
    if variety not in usable:
        return False, [f"{crop}/{variety} lacks complete parameters for {engine}; usable: {usable}"]
    p = dict(crop_params(crop, variety, engine))
    idsl, dlo = float(p.get("IDSL", 0) or 0), p.get("DLO")
    if idsl >= 1 and dlo is not None:
        dl = max_daylength_h(latitude)
        if float(dlo) > dl + 0.05:
            reasons.append(
                f"{variety} needs a {float(dlo):.1f} h optimum day but the longest day at "
                f"{latitude:.2f} deg is {dl:.1f} h: development stalls and WOFOST would "
                "report 0 kg/ha with no error. Refusing.")
    return not reasons, reasons


# ---------------------------------------------------------------------------
# Phenology inversion
# ---------------------------------------------------------------------------

_BBCH_DVS = [(0, 0.0), (9, 0.0), (10, 0.05), (19, 0.25), (30, 0.35), (39, 0.55),
             (40, 0.6), (49, 0.9), (60, 1.0), (69, 1.15), (70, 1.25), (79, 1.5),
             (80, 1.6), (89, 1.9), (97, 2.0)]


def bbch_to_dvs(bbch: int) -> float:
    xs, ys = zip(*_BBCH_DVS)
    return float(np.interp(max(0, min(97, bbch)), xs, ys))


def _afgen(table: list[float], x: float) -> float:
    xs, ys = table[0::2], table[1::2]
    return float(np.interp(x, xs, ys))


def infer_emergence(weather, today: date, dvs: float, params: dict,
                    max_back_days: int = 220) -> tuple[date, float]:
    """Effective emergence date that makes the crop's thermal clock match `dvs`.

    Development in WOFOST is thermal time through DTSMTB divided by TSUM1 (to
    flowering) and TSUM2 (flowering to maturity). Photoperiod slows pre-flowering
    development for sensitive cultivars; it is applied here too so the inversion
    agrees with the forward model.
    """
    from pcse.util import daylength
    tsum1, tsum2 = float(params["TSUM1"]), float(params["TSUM2"])
    target = dvs * tsum1 if dvs <= 1.0 else tsum1 + (dvs - 1.0) * tsum2
    if target <= 0:
        return today, 0.0
    dtsmtb = list(params["DTSMTB"])
    idsl = float(params.get("IDSL", 0) or 0)
    dlo, dlc = float(params.get("DLO", 0) or 0), float(params.get("DLC", 0) or 0)
    acc, day = 0.0, today
    for _ in range(max_back_days):
        day -= timedelta(days=1)
        try:
            w = weather(day)
        except Exception:
            break
        dts = _afgen(dtsmtb, 0.5 * (w.TMIN + w.TMAX))
        if idsl >= 1 and acc < tsum1 and dlo > dlc:
            dl = daylength(day, w.LAT, angle=-4.0)
            dts *= min(1.0, max(0.0, (dl - dlc) / (dlo - dlc)))
        acc += dts
        if acc >= target:
            return day, acc
    return day, acc


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def base_weather(lat: float, lon: float, today: date, live: bool):
    """Observed history (Open-Meteo via PCSE) or a seeded synthetic record."""
    if live:
        errors = []
        try:                                   # small daily request: fast and dependable
            return daily_openmeteo(lat, lon, today - timedelta(days=6 * 366), today)
        except Exception as e:  # noqa: BLE001
            errors.append(f"daily archive: {type(e).__name__}: {str(e)[:160]}")
        try:                                   # PCSE's own provider (hourly, much larger)
            return _robust_openmeteo()(latitude=lat, longitude=lon, forecast=True,
                                       start_date=today - timedelta(days=6 * 366))
        except Exception as e:  # noqa: BLE001
            errors.append(f"PCSE provider: {type(e).__name__}: {str(e)[:160]}")
        log.warning("Open-Meteo unavailable (%s); using SYNTHETIC weather", "; ".join(errors))
        print("Open-Meteo unavailable (" + "; ".join(errors) + "); using SYNTHETIC weather")
    return synthetic_weather(lat, lon, today - timedelta(days=6 * 366), 6 * 366 + 260)


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = ("temperature_2m_max,temperature_2m_min,shortwave_radiation_sum,precipitation_sum,"
              "wind_speed_10m_mean,dew_point_2m_mean")


def _svp_hpa(t_c: float) -> float:
    return 6.108 * math.exp(17.27 * t_c / (t_c + 237.3))


def weather_from_daily(js: dict, lat: float, lon: float):
    """PCSE weather from an Open-Meteo daily response (ERA5 reanalysis).

    Units: radiation MJ/m2 -> J/m2, rain mm -> cm, wind 10 m -> 2 m (FAO-56 log profile,
    x0.748), vapour pressure from the mean dew point (or, if absent, from Tmin, the FAO-56
    fallback). Days with any value missing are skipped; the twin reads the nearest earlier day.
    """
    from pcse.base import WeatherDataContainer, WeatherDataProvider
    from pcse.util import reference_ET
    d = js.get("daily") or {}
    days = d.get("time") or []
    if len(days) < 400:
        raise ValueError(f"Open-Meteo returned only {len(days)} days")
    elev = float(js.get("elevation") or 200.0)
    wdp = WeatherDataProvider()
    wdp.description = [f"Open-Meteo ERA5 reanalysis, daily ({days[0]} to {days[-1]})"]
    wdp.latitude, wdp.longitude, wdp.elevation = lat, lon, elev
    wdp.angstA, wdp.angstB = 0.25, 0.5
    n = 0
    dew = d.get("dew_point_2m_mean") or [None] * len(days)
    for i, ds in enumerate(days):
        tx, tn = d["temperature_2m_max"][i], d["temperature_2m_min"][i]
        rad, rain, wind = d["shortwave_radiation_sum"][i], d["precipitation_sum"][i], d["wind_speed_10m_mean"][i]
        if None in (tx, tn, rad, rain, wind):
            continue
        td = dew[i] if dew[i] is not None else tn
        day = date.fromisoformat(ds)
        rec = dict(DAY=day, LAT=lat, LON=lon, ELEV=elev, IRRAD=float(max(1e5, rad * 1e6)),
                   TMIN=float(min(tn, tx)), TMAX=float(max(tn, tx)),
                   VAP=float(max(1.0, min(_svp_hpa(td), _svp_hpa(tx)))),
                   WIND=float(max(0.1, wind * 0.748)), RAIN=float(max(0.0, rain) / 10.0))
        e0, es0, et0 = reference_ET(DAY=day, LAT=lat, ELEV=elev, TMIN=rec["TMIN"], TMAX=rec["TMAX"],
                                    IRRAD=rec["IRRAD"], VAP=rec["VAP"], WIND=rec["WIND"],
                                    ANGSTA=0.25, ANGSTB=0.5, ETMODEL="PM")
        rec.update(E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0)
        wdp._store_WeatherDataContainer(WeatherDataContainer(**rec), day)
        n += 1
    if n < 400:
        raise ValueError(f"only {n} complete days from Open-Meteo")
    return wdp


@functools.lru_cache(maxsize=8)
def daily_openmeteo(lat: float, lon: float, start: date, end: date):
    """Observed daily weather from Open-Meteo's ERA5 archive: one small request (~100 kB),
    retried with back-off. Cached on disk for the day so repeated runs do not refetch."""
    import json as _json
    import time as _time

    import requests
    cache = Path.home() / ".fieldpilot" / f"openmeteo_{lat:.2f}_{lon:.2f}_{start}_{end}.json"
    if cache.exists():
        try:
            return weather_from_daily(_json.loads(cache.read_text(encoding="utf-8")), lat, lon)
        except Exception:  # noqa: BLE001
            pass
    params = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(),
              "end_date": end.isoformat(), "daily": DAILY_VARS, "wind_speed_unit": "ms",
              "timezone": "auto"}
    last = None
    for attempt in range(4):
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=(15, 90))
            if r.status_code == 200:
                js = r.json()
                w = weather_from_daily(js, lat, lon)
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(_json.dumps(js), encoding="utf-8")
                except OSError:
                    pass
                return w
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        log.warning("Open-Meteo archive try %d of 4 failed (%s)", attempt + 1, last)
        _time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"Open-Meteo archive did not answer after 4 tries ({last})")


@functools.lru_cache(maxsize=1)
def _robust_openmeteo():
    """PCSE's Open-Meteo provider with a patient client.

    Six years of hourly history is a large response; PCSE's own client gives up after a 10 s
    read and then fails with an unrelated error. This one waits up to 3 minutes per try,
    retries four times with back-off (also on HTTP 429/5xx), and raises a clear error if the
    service never answers. PCSE caches the result for 90 days, so this is paid once.
    """
    import time as _time

    import requests
    from pcse.input import OpenMeteoWeatherDataProvider

    class PatientOpenMeteo(OpenMeteoWeatherDataProvider):
        def _get_cache_filename(self, latitude, longitude):      # share PCSE's own cache file
            import os
            from pcse import settings
            fname = "OpenMeteoWeatherDataProvider_LAT%05i_LON%05i_%s.cache" % (
                int(latitude * 10), int(longitude * 10), self.model[:5])
            return os.path.join(settings.METEO_CACHE_DIR, fname)

        def _get_response(self, url, params):
            last = None
            for attempt in range(4):
                try:
                    r = requests.get(url, params=params, timeout=(20, 180))
                    if r.status_code == 200:
                        return r.json()
                    last = f"HTTP {r.status_code}"
                except requests.RequestException as e:
                    last = f"{type(e).__name__}: {e}"
                log.warning("Open-Meteo try %d of 4 failed (%s)", attempt + 1, last)
                _time.sleep(5 * (attempt + 1))
            raise RuntimeError(f"Open-Meteo did not answer after 4 tries ({last})")

    return PatientOpenMeteo


@functools.lru_cache(maxsize=4)
def synthetic_weather(lat: float, lon: float, start: date, days: int, seed: int = 7):
    """Deterministic synthetic climate for offline runs. Stamped NOT-FOR-ADVICE."""
    from pcse.base import WeatherDataContainer, WeatherDataProvider
    from pcse.util import reference_ET

    rng = np.random.default_rng(seed)
    wdp = WeatherDataProvider()
    wdp.description = ["SYNTHETIC weather -- NOT FOR ADVICE"]
    wdp.latitude, wdp.longitude, wdp.elevation = lat, lon, 200.0
    wdp.angstA, wdp.angstB = 0.25, 0.5
    year_anom = {}
    for i in range(days):
        d = start + timedelta(days=i)
        ya = year_anom.setdefault(d.year, rng.normal(0, 1.2))
        doy = d.timetuple().tm_yday
        season = math.sin(2 * math.pi * (doy - 105) / 365.25)
        tmean = 25.0 + 8.5 * season + ya + rng.normal(0, 1.4)
        monsoon = 1.0 if 170 <= doy <= 265 else 0.08
        irrad = (1.6e7 + 6.0e6 * season) * (0.75 - 0.3 * monsoon * rng.random())
        svp = 6.11 * 10 ** (7.5 * tmean / (237.3 + tmean))
        rec = dict(DAY=d, LAT=lat, LON=lon, ELEV=200.0, IRRAD=float(max(4e6, irrad)),
                   TMIN=float(tmean - 7.0), TMAX=float(tmean + 7.0),
                   VAP=float(max(4.0, (0.45 + 0.35 * monsoon) * svp)),
                   WIND=float(max(0.5, rng.gamma(2.2, 0.9))),
                   # PCSE wants cm/day: ~7 mm/day mean in the monsoon, ~0.4 mm/day outside it.
                   RAIN=float(min(20.0, round(rng.gamma(0.5, 1.6) * (0.9 * monsoon + 0.05 * (1 - monsoon)), 3))))
        e0, es0, et0 = reference_ET(DAY=d, LAT=lat, ELEV=200.0, TMIN=rec["TMIN"],
                                    TMAX=rec["TMAX"], IRRAD=rec["IRRAD"], VAP=rec["VAP"],
                                    WIND=rec["WIND"], ANGSTA=0.25, ANGSTB=0.5, ETMODEL="PM")
        rec.update(E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0)
        wdp._store_WeatherDataContainer(WeatherDataContainer(**rec), d)
    return wdp


def analogue_weather(base, today: date, end: date, years_back: int):
    """Observed weather to `today`, then the same dates from `years_back` years ago."""
    from pcse.base import WeatherDataContainer, WeatherDataProvider
    w = WeatherDataProvider()
    w.description = list(getattr(base, "description", [])) + [f"analogue year -{years_back}"]
    w.latitude, w.longitude = base.latitude, base.longitude
    w.elevation = getattr(base, "elevation", 200.0)
    w.angstA, w.angstB = getattr(base, "angstA", 0.25), getattr(base, "angstB", 0.5)
    start = today - timedelta(days=230)
    d = start
    while d <= end:
        src = d if d <= today else _years_ago(d, years_back)
        c = _get(base, src)
        rec = {k: getattr(c, k) for k in ("LAT", "LON", "ELEV", "IRRAD", "TMIN", "TMAX",
                                          "VAP", "WIND", "RAIN", "E0", "ES0", "ET0")}
        rec["DAY"] = d
        w._store_WeatherDataContainer(WeatherDataContainer(**rec), d)
        d += timedelta(days=1)
    return w


def _get(base, d: date):
    """Weather for `d`, or the nearest earlier day if the record ends (reanalysis lag)."""
    for back in range(0, 15):
        try:
            return base(d - timedelta(days=back))
        except Exception:  # noqa: BLE001
            continue
    return base(_years_ago(d, 1))


def _years_ago(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:        # 29 Feb
        return d.replace(year=d.year - n, day=28)


# ---------------------------------------------------------------------------
# One ensemble member
# ---------------------------------------------------------------------------

@dataclass
class Member:
    """One ensemble draw. Shared across options, so comparisons are paired."""
    weather_idx: int
    tsum_scale: float
    span_scale: float
    damage_mult: float
    eff_u: float              # quantile of treatment efficacy within its range


def run_member(crop: str, variety: str, weather, emergence: date, today: date,
               option: Intervention, severity: int, category: str, m: Member,
               soil_n: float = 60.0, engine: str | None = None) -> dict:
    from pcse.base import ParameterProvider
    from pcse import models

    engine = engine or engine_for(category)
    p = crop_params(crop, variety, engine)
    soil = {"SMFCF": 0.32, "SM0": 0.45, "SMW": 0.15, "CRAIRC": 0.06, "RDMSOL": 100.0,
            "K0": 10.0, "SOPE": 1.0, "KSUB": 1.0}
    if engine.startswith("Wofost81"):
        from pcse.input import WOFOST81SiteDataProvider_Classic as Site81
        site = Site81(WAV=20.0, CO2=425.0, NAVAILI=soil_n, NSOILBASE=10.0, NSOILBASE_FR=0.025)
    else:
        from pcse.input import WOFOST72SiteDataProvider
        site = WOFOST72SiteDataProvider(WAV=20.0)
    params = ParameterProvider(cropdata=p, soildata=soil, sitedata=site)
    params.set_override("TSUM1", float(p["TSUM1"]) * m.tsum_scale)
    params.set_override("TSUM2", float(p["TSUM2"]) * m.tsum_scale)
    params.set_override("SPAN", float(p["SPAN"]) * m.span_scale)

    act_day = today + timedelta(days=1)
    timed = []
    if option.n_kg_ha and engine.startswith("Wofost81"):
        timed.append({"event_signal": "apply_n", "name": "N", "comment": option.code,
                      "events_table": [{act_day: {"N_amount": option.n_kg_ha,
                                                  "N_recovery": 0.6 if option.code == "foliar_n" else 0.7}}]})
    if option.irrigation_cm:
        timed.append({"event_signal": "irrigate", "name": "irr", "comment": option.code,
                      "events_table": [{act_day: {"amount": option.irrigation_cm, "efficiency": 0.7}}]})
    agro = [{emergence - timedelta(days=1): {
        "CropCalendar": {"crop_name": crop, "variety_name": variety,
                         "crop_start_date": emergence, "crop_start_type": "emergence",
                         "crop_end_date": None, "crop_end_type": "maturity",
                         "max_duration": 240},
        "TimedEvents": timed or None, "StateEvents": None}}]
    model = getattr(models, engine)(params, weather, agro)

    diseased = category in ("disease", "pest") and severity > 0
    if not diseased:
        model.run_till_terminate()
    else:
        model.run_till(today)
        rate = DAMAGE_RATE.get(severity, 0.0) * m.damage_mult
        step = 3
        treated = option.code == "spot_spray"
        lo, hi = option.efficacy
        eff = lo + m.eff_u * (hi - lo)
        while not model.flag_terminate:
            r = rate * ((1 - eff) if treated and model.day >= act_day else 1.0)
            lai = model.get_variable("LAI")
            if lai and r > 0:
                try:
                    model.set_variable("LAI", float(lai) * (1 - r) ** step)
                except Exception:  # noqa: BLE001 -- LAI can be 0 late in season
                    pass
            model.run(days=step)
    s = model.get_summary_output()
    s = s[0] if s else {}
    return {"TWSO": float(s.get("TWSO") or 0.0), "TAGP": float(s.get("TAGP") or 0.0),
            "LAIMAX": float(s.get("LAIMAX") or 0.0), "DOM": str(s.get("DOM") or "")}


# ---------------------------------------------------------------------------
# Zone evaluation
# ---------------------------------------------------------------------------

def cvar(x: np.ndarray, alpha: float = 0.8) -> float:
    """Mean of the worst (1 - alpha) share of outcomes (lower tail of margin)."""
    x = np.sort(np.asarray(x, float))
    k = max(1, int(math.ceil((1 - alpha) * len(x))))
    return float(x[:k].mean())


@dataclass
class ZoneEval:
    zone_id: str
    category: str
    severity: int
    emergence: str
    dvs_now: float
    feasible: bool
    feasibility_notes: list[str]
    options: list[dict] = field(default_factory=list)
    recommended: str = "no_action"
    decision_reason: str = ""
    weather_source: str = ""
    assumptions: dict = field(default_factory=dict)
    maturity: str = ""          # earliest simulated maturity date (no action), ISO; for the SOP's PHI check


def evaluate(crop: str, variety: str, lat: float, lon: float, today: date,
             bbch: int, category: str, severity: int, zone_id: str = "",
             live_weather: bool = False, n_weather: int = 5, n_params: int = 3,
             alpha: float = 0.8, min_gain_inr_ha: float = 500.0, min_p_better: float = 0.6,
             seed: int = 0, base=None) -> ZoneEval:
    engine = engine_for(category)
    ok, notes = feasibility(crop, variety, lat, engine)
    ev = ZoneEval(zone_id, category, severity, "", 0.0, ok, notes)
    if not ok:
        ev.decision_reason = "Refused: cultivar cannot complete development here."
        return ev

    base = base or base_weather(lat, lon, today, live_weather)
    params = crop_params(crop, variety, engine)
    dvs = bbch_to_dvs(bbch if bbch >= 0 else 30)
    emergence, _ = infer_emergence(base, today, dvs, params)
    ev.emergence, ev.dvs_now = emergence.isoformat(), round(dvs, 2)
    ev.weather_source = "; ".join(getattr(base, "description", []) or ["observed"])
    end = today + timedelta(days=260)
    weathers = [analogue_weather(base, today, end, k + 1) for k in range(n_weather)]

    rng = np.random.default_rng(seed)
    members = []
    for wi in range(n_weather):
        for _ in range(n_params):
            members.append(Member(wi, float(rng.normal(1.0, 0.06)), float(rng.normal(1.0, 0.08)),
                                  float(rng.lognormal(0.0, 0.35)), float(rng.uniform())))
    econ = CROP_ECON.get(crop, {"dm": 0.86, "price": 20.0})
    ev.assumptions = {"price_inr_per_kg_fresh": econ["price"], "dry_matter_fraction": econ["dm"],
                      "alpha": alpha, "ensemble": len(members),
                      "damage_rate_per_day_median": DAMAGE_RATE.get(severity, 0.0),
                      "min_gain_inr_ha": min_gain_inr_ha, "min_p_better": min_p_better,
                      "engine": engine,
                      "initial_soil_n_kg_ha": soil_n_for(category, severity)}

    margins: dict[str, np.ndarray] = {}
    yields: dict[str, np.ndarray] = {}
    maturities: list[str] = []
    for code in options_for(category, severity):
        opt = INTERVENTIONS[code]
        ys, ms = [], []
        for m in members:
            out = run_member(crop, variety, weathers[m.weather_idx], emergence, today, opt,
                             severity, category, m, soil_n=soil_n_for(category, severity),
                             engine=engine)
            if code == "no_action" and out.get("DOM") and out["DOM"] != "None":
                maturities.append(out["DOM"][:10])
            fresh = out["TWSO"] / econ["dm"]
            ys.append(fresh)
            ms.append(fresh * econ["price"] - opt.cost_inr_ha)
        yields[code], margins[code] = np.array(ys), np.array(ms)

    ev.maturity = min(maturities) if maturities else ""
    base_m = margins["no_action"]
    for code, mg in margins.items():
        opt = INTERVENTIONS[code]
        ev.options.append({
            "code": code, "label": opt.label, "drone": opt.drone, "cost_inr_ha": opt.cost_inr_ha,
            "yield_t_ha_p10": round(float(np.percentile(yields[code], 10)) / 1000, 2),
            "yield_t_ha_p50": round(float(np.percentile(yields[code], 50)) / 1000, 2),
            "yield_t_ha_p90": round(float(np.percentile(yields[code], 90)) / 1000, 2),
            "margin_mean_inr_ha": round(float(mg.mean())),
            "margin_cvar_inr_ha": round(cvar(mg, alpha)),
            "gain_vs_no_action_cvar": round(cvar(mg, alpha) - cvar(base_m, alpha)),
            "p_better_than_no_action": round(float((mg > base_m).mean()), 2) if code != "no_action" else None,
        })
    ev.options.sort(key=lambda o: o["margin_cvar_inr_ha"], reverse=True)
    best = ev.options[0]
    if best["code"] == "no_action":
        ev.recommended = "no_action"
        ev.decision_reason = ("Doing nothing has the best worst-case margin." if len(ev.options) > 1
                              else "Zone is healthy: nothing to treat.")
    elif best["gain_vs_no_action_cvar"] < min_gain_inr_ha:
        ev.recommended = "no_action"
        ev.decision_reason = (f"Best action gains only INR {best['gain_vs_no_action_cvar']}/ha in the bad "
                              f"seasons (< {min_gain_inr_ha:.0f}); not worth the cost and the flight.")
    elif (best["p_better_than_no_action"] or 0) < min_p_better:
        ev.recommended = "no_action"
        ev.decision_reason = (f"Action beats doing nothing in only {best['p_better_than_no_action']:.0%} of "
                              "simulated seasons; too uncertain to act.")
    else:
        ev.recommended = best["code"]
        ev.decision_reason = (f"{best['label']} raises the worst-20% margin by INR "
                              f"{best['gain_vs_no_action_cvar']}/ha and wins in "
                              f"{best['p_better_than_no_action']:.0%} of simulated seasons.")
    return ev
