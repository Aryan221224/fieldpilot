"""Global impact estimate: what FieldPilot's measured gains would mean across today's
agricultural spray-drone fleet.

Method (deliberately conservative, every input sourced below):

1.  Activity.  Hectare-treatments flown by agricultural drones in a year.  We use only the two
    countries with published 2025 totals, China and the USA, so the world total is a floor.
2.  Baseline resource use per hectare-treatment comes from the baseline arm of our efficiency
    benchmark (eval/efficiency_bench.py -> eval/results/efficiency_2026-10-30.json):
    battery cycles, flight minutes and leftover tank mix per sprayed hectare.
    Cross-check: area / (hectares per cycle) / fleet size gives cycles per drone per year,
    which must land in a plausible range (tested).
3.  Gain fraction per metric = 1 - (FieldPilot use per useful ha) / (baseline use per useful ha),
    from the same benchmark (SIMULATED flights, live forecasts, production planner).
4.  Realisation factor r = 0.25.  The benchmark baseline is a naive operator (booking order, fly
    whenever a battery is charged).  Real operators are better than that, so we count only a
    quarter of the measured gain.  Results are also reported for r = 0.10 and r = 0.50.
5.  Energy, CO2 and battery-wear cost follow from cycles saved using the DJI DB1560 battery
    (the T40 / T50 pack), a charger efficiency and the IEA world-average grid intensity.

Run:  python -m eval.global_impact      (writes eval/results/global_impact_2026-10-30.{json,md})
Tested by tests/test_global_impact.py.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCH = ROOT / "results" / "efficiency_2026-10-30.json"

SOURCES = {
    "china_ha_2025": ("about 203 million hectare-treatments by agricultural drones in China in 2025 "
                      "(178 million in 2024)",
                      "CleanTechnica, 20 Sep 2026, https://cleantechnica.com/2026/09/20/china-agricultural-drones-30-times-us/"),
    "us_ha_2025": ("about 16.4 million acres = 6.64 million hectare-treatments in the USA in 2025 "
                   "(American Spray Drone Coalition 2025 Impact Survey)",
                   "CleanTechnica, 20 Sep 2026 (same article)"),
    "fleet_2025": ("over 600,000 DJI agricultural drones in use globally at the end of 2025",
                   "DJI Agriculture annual report, 29 Apr 2026, https://www.aap.com.au/aapreleases/cision20260429ae46633/"),
    "battery": ("DJI DB1560 (Agras T40/T50): 30,000 mAh, 52.22 V, about 1,567 Wh, up to 1,500 cycles, USD 2,799",
                "https://www.dronefly.com/products/dji-db1560-intelligent-flight-battery"),
    "grid_co2": ("world-average CO2 intensity of electricity generation 445 g/kWh in 2024",
                 "IEA Electricity 2025, https://iea.org/reports/electricity-2025/emissions"),
    "benchmark": ("FieldPilot efficiency benchmark, 5 regions x 3 days, 105 bookings, live forecasts, "
                  "SIMULATED flights", "eval/results/efficiency_2026-10-30.json"),
}

INPUTS = {
    "china_ha_treatments": 203e6,
    "us_ha_treatments": 6.64e6,
    "fleet_drones": 600_000,
    "battery_wh": 30.0 * 52.22,          # 1,566.6 Wh
    "battery_cycles_rated": 1500,
    "battery_price_usd": 2799.0,
    "depth_of_discharge": 0.80,          # pilots land with ~20 % reserve
    "charger_efficiency": 0.90,
    "grid_g_co2_per_kwh": 445.0,
    "realisation": 0.25,
}


def bench_totals(path: Path = BENCH) -> dict:
    d = json.loads(path.read_text())
    out = {}
    for arm in ("baseline", "fieldpilot_with_scan"):
        rows = [r[arm] for r in d["rows"]]
        out[arm] = {
            "sprayed_ha": sum(r["sprayed_ha"] for r in rows),
            "effective_ha": sum(r["effective_ha"] for r in rows),
            "battery_cycles": sum(r["battery_cycles"] for r in rows),
            "flight_min": sum(r["flight_min"] for r in rows),
            "leftover_mix_l": sum(r["leftover_mix_l"] for r in rows),
        }
    out["summary"] = d["summary"]
    return out


def gain_fractions(b: dict) -> dict:
    """1 - FieldPilot/baseline, both per useful (effective) hectare."""
    base, fp = b["baseline"], b["fieldpilot_with_scan"]
    g = {}
    for k in ("battery_cycles", "flight_min", "leftover_mix_l"):
        g[k] = 1 - (fp[k] / fp["effective_ha"]) / (base[k] / base["effective_ha"])
    return g


def estimate(inp: dict = INPUTS, b: dict | None = None) -> dict:
    b = b or bench_totals()
    base = b["baseline"]
    g = gain_fractions(b)
    r = inp["realisation"]

    ha = inp["china_ha_treatments"] + inp["us_ha_treatments"]
    per_ha = {k: base[k] / base["sprayed_ha"] for k in ("battery_cycles", "flight_min", "leftover_mix_l")}

    cycles_now = ha * per_ha["battery_cycles"]
    flight_h_now = ha * per_ha["flight_min"] / 60
    mix_now = ha * per_ha["leftover_mix_l"]

    cycles_saved = cycles_now * g["battery_cycles"] * r
    kwh_per_cycle = inp["battery_wh"] / 1000 * inp["depth_of_discharge"] / inp["charger_efficiency"]
    gwh_saved = cycles_saved * kwh_per_cycle / 1e6
    co2_t_saved = gwh_saved * 1e6 * inp["grid_g_co2_per_kwh"] / 1e6
    wear_usd_per_cycle = inp["battery_price_usd"] / inp["battery_cycles_rated"]
    wear_usd_saved = cycles_saved * wear_usd_per_cycle
    packs_saved = cycles_saved / inp["battery_cycles_rated"]
    flight_h_saved = flight_h_now * g["flight_min"] * r
    mix_l_saved = mix_now * g["leftover_mix_l"] * r

    return {
        "realisation": r,
        "world_ha_treatments_floor": ha,
        "baseline_per_sprayed_ha": per_ha,
        "gain_fraction_per_useful_ha": g,
        "today": {
            "battery_cycles": cycles_now,
            "cycles_per_drone_per_year": cycles_now / inp["fleet_drones"],
            "flight_hours": flight_h_now,
            "leftover_mix_l": mix_now,
        },
        "saved_per_year": {
            "battery_cycles": cycles_saved,
            "battery_packs": packs_saved,
            "energy_gwh": gwh_saved,
            "co2_tonnes": co2_t_saved,
            "battery_wear_usd": wear_usd_saved,
            "flight_hours": flight_h_saved,
            "wasted_mix_litres": mix_l_saved,
        },
        "per_cycle": {"grid_kwh": kwh_per_cycle, "wear_usd": wear_usd_per_cycle},
    }


def main() -> None:
    res = {"inputs": INPUTS, "sources": {k: {"fact": v[0], "source": v[1]} for k, v in SOURCES.items()},
           "central": estimate(), "range": {}}
    for r in (0.10, 0.25, 0.50):
        res["range"][str(r)] = estimate({**INPUTS, "realisation": r})["saved_per_year"]
    out = ROOT / "results" / "global_impact_2026-10-30.json"
    out.write_text(json.dumps(res, indent=1))

    c = res["central"]; s = c["saved_per_year"]; g = c["gain_fraction_per_useful_ha"]
    md = [
        "# Global impact estimate (ESTIMATE, from a SIMULATED benchmark)", "",
        f"World agricultural-drone activity, floor: **{c['world_ha_treatments_floor']/1e6:.1f} M hectare-treatments / year** "
        "(China 203 M + USA 6.64 M, 2025).", "",
        "Gains measured per useful hectare in the benchmark (FieldPilot vs naive day): "
        f"battery cycles -{g['battery_cycles']*100:.0f} %, flight minutes -{g['flight_min']*100:.0f} %, "
        f"leftover mix -{g['leftover_mix_l']*100:.0f} %.", "",
        f"Counting only **{c['realisation']*100:.0f} %** of those gains:", "",
        "| saved per year | central (r = 0.25) | r = 0.10 | r = 0.50 |", "|---|---|---|---|",
    ]
    rows = [("battery cycles", "battery_cycles", 1e6, "M"), ("battery packs (1,500-cycle)", "battery_packs", 1e3, "k"),
            ("charging energy", "energy_gwh", 1, "GWh"), ("CO2", "co2_tonnes", 1e3, "kt"),
            ("battery wear", "battery_wear_usd", 1e6, "M USD"), ("pilot flight hours", "flight_hours", 1e6, "M h"),
            ("wasted tank mix", "wasted_mix_litres", 1e6, "M L")]
    for lab, k, div, unit in rows:
        md.append(f"| {lab} | {s[k]/div:.1f} {unit} | {res['range']['0.1'][k]/div:.1f} {unit} | {res['range']['0.5'][k]/div:.1f} {unit} |")
    md += ["", f"Cross-check: {c['today']['battery_cycles']/1e6:.0f} M cycles a year today over 600,000 drones = "
           f"{c['today']['cycles_per_drone_per_year']:.0f} cycles per drone per year.", "", "## Sources", ""]
    md += [f"- {v[0]}. {v[1]}" for v in SOURCES.values()]
    md += ["", "Assumptions: 80 % depth of discharge per cycle, 90 % charger efficiency, DB1560 pack for the whole fleet, "
           "benchmark baseline per-hectare use as today's real-world use. Electricity price and chemical value are not "
           "counted, so the money figure is battery wear only."]
    (ROOT / "results" / "global_impact_2026-10-30.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
