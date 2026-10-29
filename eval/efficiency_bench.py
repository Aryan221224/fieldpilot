"""Efficiency benchmark: FieldPilot vs the common way of running a spray-drone day.

    python eval/efficiency_bench.py                     # live Open-Meteo forecasts, next 3 days
    python eval/efficiency_bench.py --fill-submission   # also write [EFF] into docs/SUBMISSION.md

Five operating regions, each with its own drone class, battery kit, time zone and spray-weather
rule pack. The 10 L regions fly the project airframe (T1 10 L quad, fieldpilot/airframe.py)
with the battery kit typical of that region; the large-farm regions fly a 40 L class drone. For each region and day, a seeded set of bookings around the operator's base is
planned twice on the SAME live forecast and the SAME batteries:

* baseline  -- booking order, whole field, full-tank batches, take-off from the field
               corner, fly whenever a charged battery is available;
* FieldPilot -- rule-pack spray windows, scanned areas, balanced loads mixed per refill,
               best take-off point, best value per hour next.

Two FieldPilot variants are reported so the gains can be attributed honestly:
``no_scan`` (every booking sprays its whole field -- scheduling, windows and sortie planning
only) and ``with_scan`` (a share of fields were scanned and need only part sprayed; the
share and fractions are stated assumptions, printed in the result).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fieldpilot import dayplan, sop  # noqa: E402
from fieldpilot.dayplan import JobSpec, Operator  # noqa: E402

REGIONS = {
    "IN · Uttar Pradesh": dict(base=(28.35, 77.55), utc_offset_h=5.5, rules="IN-SOP-2023",
                               drone="t1_10l", battery_sets=3, chargers=1, charge_min=120,
                               road_kmh=25, bookings=8, ha=(0.3, 2.0), country="IN"),
    "BR · Paraná": dict(base=(-23.31, -51.16), utc_offset_h=-3, rules="GOOD-PRACTICE",
                        drone="agri_40l", battery_sets=4, chargers=2, charge_min=45,
                        road_kmh=45, bookings=6, ha=(3.0, 20.0), country="BR"),
    "US · Central Valley": dict(base=(36.75, -119.77), utc_offset_h=-7, rules="GOOD-PRACTICE",
                                drone="agri_40l", battery_sets=4, chargers=2, charge_min=45,
                                road_kmh=60, bookings=5, ha=(5.0, 30.0), country="US"),
    "KE · Nakuru": dict(base=(-0.30, 36.07), utc_offset_h=3, rules="GOOD-PRACTICE",
                        drone="t1_10l", battery_sets=3, chargers=1, charge_min=90,
                        road_kmh=25, bookings=8, ha=(0.2, 2.0), country="KE"),
    "VN · Mekong Delta": dict(base=(10.03, 105.78), utc_offset_h=7, rules="GOOD-PRACTICE",
                              drone="t1_10l", battery_sets=4, chargers=2, charge_min=60,
                              road_kmh=25, bookings=8, ha=(0.5, 3.0), country="VN"),
}
SCAN_SHARE = 0.6            # assumption: share of bookings with a FieldPilot scan
SCAN_FRACTION = (0.25, 0.7)  # assumption: share of a scanned field that needs spraying


def bookings(region: str, cfg: dict, seed: int) -> list[JobSpec]:
    rnd = random.Random(f"{region}-{seed}")
    lat0, lon0 = cfg["base"]
    out = []
    for i in range(cfg["bookings"]):
        r_km, th = 25 * math.sqrt(rnd.random()), rnd.uniform(0, 2 * math.pi)
        lat = lat0 + r_km / 111.0 * math.cos(th)
        lon = lon0 + r_km / (111.0 * math.cos(math.radians(lat0))) * math.sin(th)
        area = round(rnd.uniform(*cfg["ha"]), 2)
        scanned = rnd.random() < SCAN_SHARE
        spray = round(area * rnd.uniform(*SCAN_FRACTION), 2) if scanned else None
        out.append(JobSpec(f"{i + 1}", f"farm {i + 1}", round(lat, 4), round(lon, 4), area, spray,
                           urgency=1.0 + 0.3 * (rnd.random() < 0.25)))
    return out


def operator(region: str, cfg: dict) -> Operator:
    keys = ("base", "utc_offset_h", "rules", "drone", "battery_sets", "chargers", "charge_min",
            "road_kmh", "country")
    return Operator(name=region, **{k: cfg[k] for k in keys}, day_start="06:00", day_end="18:00")


KEYS = ("farms_served", "farms_served_in_window", "needed_ha_served", "effective_ha",
        "battery_cycles", "flight_min", "out_of_window_sorties", "sprayed_ha",
        "sprayed_beyond_need_ha", "leftover_mix_l", "idle_min", "drive_km")


def run(days: list[date], forecast_for, regions=REGIONS) -> dict:
    rows = []
    for region, cfg in regions.items():
        op = operator(region, cfg)
        for k, day in enumerate(days):
            jobs = bookings(region, cfg, k)
            fc = {j.job_id: forecast_for(j.lat, j.lon) for j in jobs}
            c = dayplan.compare(jobs, op, day, fc)
            ns = dayplan.plan_day(jobs, op, day, fc, "fieldpilot_noscan")
            rows.append({"region": region, "day": day.isoformat(), "rules": op.rules,
                         "drone": op.drone, "bookings": len(jobs),
                         "baseline": {x: c["baseline"]["metrics"][x] for x in KEYS},
                         "fieldpilot_no_scan": {x: ns["metrics"][x] for x in KEYS},
                         "fieldpilot_with_scan": {x: c["fieldpilot"]["metrics"][x] for x in KEYS}})
    return {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "assumptions": {"scan_share": SCAN_SHARE, "scan_fraction": SCAN_FRACTION,
                            "baseline": "booking order, whole field, full-tank batches, corner "
                                        "take-off, fly whenever a battery is charged"},
            "rows": rows, "summary": summarise(rows)}


def summarise(rows: list[dict]) -> dict:
    def tot(variant, k):
        return sum(r[variant][k] for r in rows)
    out = {}
    for v in ("baseline", "fieldpilot_no_scan", "fieldpilot_with_scan"):
        cyc = tot(v, "battery_cycles")
        eff = tot(v, "effective_ha")
        out[v] = {"region_days": len(rows), "farms_booked": sum(r["bookings"] for r in rows),
                  "farms_served": tot(v, "farms_served"),
                  "farms_served_in_window": tot(v, "farms_served_in_window"),
                  "effective_ha": round(eff, 1), "battery_cycles": cyc,
                  "effective_ha_per_battery_cycle": round(eff / cyc, 3) if cyc else 0,
                  "flight_min_per_effective_ha": round(tot(v, "flight_min") / eff, 2) if eff else 0,
                  "out_of_window_sorties": tot(v, "out_of_window_sorties"),
                  "out_of_window_pct": round(100 * tot(v, "out_of_window_sorties") / cyc, 1) if cyc else 0,
                  "sprayed_ha": round(tot(v, "sprayed_ha"), 1),
                  "sprayed_beyond_need_ha": round(tot(v, "sprayed_beyond_need_ha"), 1),
                  "leftover_mix_l": round(tot(v, "leftover_mix_l"), 1)}
    return out


def sentence(res: dict) -> str:
    s = res["summary"]
    b, n, w = s["baseline"], s["fieldpilot_no_scan"], s["fieldpilot_with_scan"]

    def pct(x, y):
        return f"{(x - y) / y * 100:+.0f} %" if y else "n/a"
    return (f"Across {b['region_days']} region-days in five countries on live forecasts "
            f"({b['farms_booked']} bookings, same batteries), the common way of working flew "
            f"{b['out_of_window_pct']} % of sorties outside the spray-weather window, sprayed "
            f"{b['sprayed_beyond_need_ha']} ha that scans showed did not need it and left "
            f"{b['leftover_mix_l']} L of mixed product; FieldPilot flew {w['out_of_window_pct']} % "
            f"outside the window, sprayed {w['sprayed_beyond_need_ha']} ha beyond need, left "
            f"{w['leftover_mix_l']} L (the last-refill margins), and fully served "
            f"{w['farms_served_in_window']} farms "
            f"in-window vs {b['farms_served_in_window']}. Effective hectares per battery cycle: "
            f"{b['effective_ha_per_battery_cycle']} baseline, {n['effective_ha_per_battery_cycle']} "
            f"with scheduling and sortie planning alone "
            f"({pct(n['effective_ha_per_battery_cycle'], b['effective_ha_per_battery_cycle'])}), "
            f"{w['effective_ha_per_battery_cycle']} with field scans "
            f"({pct(w['effective_ha_per_battery_cycle'], b['effective_ha_per_battery_cycle'])})")


def chart(res: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = res["summary"]
    labels = ["Baseline", "FieldPilot\n(no scan)", "FieldPilot\n(with scan)"]
    keys = ["baseline", "fieldpilot_no_scan", "fieldpilot_with_scan"]
    fig, ax = plt.subplots(1, 3, figsize=(11, 3.4))
    for a, (k, t) in zip(ax, [("effective_ha_per_battery_cycle", "Effective ha per battery cycle"),
                              ("out_of_window_pct", "% sorties outside spray window"),
                              ("leftover_mix_l", "Mixed product left over (L)")]):
        vals = [s[v][k] for v in keys]
        a.bar(labels, vals, color=["#9aa5a0", "#5aa7e8", "#76b900"])
        a.set_title(t, fontsize=10)
        for i, v in enumerate(vals):
            a.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=9)
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Same bookings, same live forecast, same batteries", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def fill_submission(sen: str, saved: str) -> bool:
    p = ROOT / "docs" / "SUBMISSION.md"
    t = p.read_text(encoding="utf-8")
    import re
    pat = re.compile(r"(?<!`)\[EFF\](?!`)")
    if not pat.search(t):
        return False
    p.write_text(pat.sub(lambda _m: f"{sen} (`eval/results/{saved}`)", t), encoding="utf-8")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--fill-submission", action="store_true")
    a = ap.parse_args(argv)
    start = date.today() + timedelta(days=1)
    days = [start + timedelta(days=k) for k in range(a.days)]
    cache: dict = {}

    def fc(lat, lon):
        # one forecast per ~50 km cell: keeps the run inside Open-Meteo's free rate limit;
        # every farm in a region is within 25 km of the operator's base
        cell = (round(lat / 0.5) * 0.5, round(lon / 0.5) * 0.5)
        if cell not in cache:
            import time as _t
            for attempt in range(4):                     # flaky mobile links: retry with backoff
                try:
                    cache[cell] = sop.fetch_forecast(lat, lon, days=a.days + 2, timeout_s=25)
                    break
                except OSError as e:
                    if attempt == 3:
                        raise SystemExit(f"forecast unavailable for {lat},{lon}: {e}")
                    _t.sleep(15 * (attempt + 1) if "429" in str(e) else 3 * (attempt + 1))
        return cache[cell]
    res = run(days, fc)
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    name = f"efficiency_{days[0].isoformat()}.json"
    (out / name).write_text(json.dumps(res, indent=1), encoding="utf-8")
    chart(res, out / f"efficiency_{days[0].isoformat()}.png")
    print(json.dumps(res["summary"], indent=1))
    sen = sentence(res)
    print(sen)
    if a.fill_submission:
        print("docs/SUBMISSION.md updated" if fill_submission(sen, name)
              else "docs/SUBMISSION.md: no [EFF] placeholder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
