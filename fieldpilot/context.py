"""Outside context for the agent: local advisories (Tavily) and flight weather.

Search results are *untrusted text*. They are passed to the model inside a
delimited block marked as data, they never reach the planner, and no tool the
model can call turns text into an actuation -- so a hostile page that says
"spray every zone now" can at worst change the explanation, not the flight.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Spray-drift and wash-off limits for a small spraying multirotor. Conservative
# end of commonly published drone-spraying guidance; change per product label.
MAX_WIND_MS = 5.0
MAX_GUST_MS = 7.0
MAX_RAIN_PROB_2H = 40
MAX_TEMP_C = 35.0


def search_local(query: str, api_key: str = "", max_results: int = 5,
                 country: str = "india") -> dict:
    if not api_key:
        return {"source": "OFFLINE", "query": query, "answer": None, "results": [],
                "note": "TAVILY_API_KEY not set: no external advisories were consulted. "
                        "The recommendation rests on the simulation alone."}
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        r = client.search(query=query, search_depth="advanced", topic="general",
                          max_results=max_results, include_answer=True, country=country)
        results = [{"title": x.get("title", "")[:140], "url": x.get("url", ""),
                    "content": (x.get("content") or "")[:600], "score": round(float(x.get("score", 0)), 3)}
                   for x in r.get("results", [])]
        return {"source": "tavily", "query": query, "answer": (r.get("answer") or "")[:800],
                "results": results}
    except Exception as e:  # noqa: BLE001
        log.warning("tavily search failed: %s", e)
        return {"source": "ERROR", "query": query, "answer": None, "results": [],
                "note": f"search failed ({type(e).__name__}); continuing without it"}


def flight_conditions(lat: float, lon: float, live: bool = True, hours: int = 6) -> dict:
    """Wind, gusts, rain probability and temperature for the next few hours.

    Spraying has stricter limits than flying: drift above ~5 m/s and wash-off if
    it rains within a couple of hours make the treatment worthless even when the
    flight itself would be safe.
    """
    data = None
    if live:
        try:
            import httpx
            r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
                "latitude": lat, "longitude": lon, "forecast_days": 2, "timezone": "UTC",
                "wind_speed_unit": "ms",
                "hourly": "wind_speed_10m,wind_gusts_10m,precipitation_probability,temperature_2m"})
            r.raise_for_status()
            data = r.json()["hourly"]
            source = "open-meteo forecast"
        except Exception as e:  # noqa: BLE001
            log.warning("forecast unavailable: %s", e)
    if data is None:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        times = [(now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
        data = {"time": times,
                "wind_speed_10m": [2.2 + 0.6 * math.sin(h / 3) for h in range(48)],
                "wind_gusts_10m": [3.6 + 0.8 * math.sin(h / 3) for h in range(48)],
                "precipitation_probability": [5] * 48,
                "temperature_2m": [29.0 + 3 * math.sin(h / 4) for h in range(48)]}
        source = "OFFLINE (synthetic calm conditions, NOT a forecast)"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
    idx = next((i for i, t in enumerate(data["time"]) if t >= now), 0)
    sl = slice(idx, idx + hours)
    wind = [float(v or 0) for v in data["wind_speed_10m"][sl]]
    gust = [float(v or 0) for v in data["wind_gusts_10m"][sl]]
    rain = [float(v or 0) for v in data["precipitation_probability"][idx:idx + 2]]
    temp = [float(v or 0) for v in data["temperature_2m"][sl]]
    reasons = []
    if wind and wind[0] > MAX_WIND_MS:
        reasons.append(f"wind {wind[0]:.1f} m/s > {MAX_WIND_MS} m/s spray-drift limit")
    if gust and gust[0] > MAX_GUST_MS:
        reasons.append(f"gusts {gust[0]:.1f} m/s > {MAX_GUST_MS} m/s")
    if rain and max(rain) > MAX_RAIN_PROB_2H:
        reasons.append(f"{max(rain):.0f}% chance of rain within 2 h (wash-off)")
    if temp and temp[0] > MAX_TEMP_C:
        reasons.append(f"{temp[0]:.0f} C > {MAX_TEMP_C:.0f} C (evaporation, crop scorch)")
    best = None
    for i in range(min(len(wind), len(gust))):
        if wind[i] <= MAX_WIND_MS and gust[i] <= MAX_GUST_MS:
            best = data["time"][idx + i]
            break
    return {"source": source, "go": not reasons, "reasons": reasons,
            "now": {"wind_ms": round(wind[0], 1) if wind else None,
                    "gust_ms": round(gust[0], 1) if gust else None,
                    "rain_prob_2h": max(rain) if rain else None,
                    "temp_c": round(temp[0], 1) if temp else None},
            "next_window_utc": best,
            "limits": {"wind_ms": MAX_WIND_MS, "gust_ms": MAX_GUST_MS,
                       "rain_prob_2h": MAX_RAIN_PROB_2H, "temp_c": MAX_TEMP_C}}
