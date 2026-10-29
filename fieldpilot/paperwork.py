"""The paper an Indian drone-spraying job needs, generated from a FieldPilot run.

    python -m fieldpilot.cli paperwork runs/R-....json            # -> runs/paper/R-.../*.html (+ .pdf)

Three documents, all from the same run record:

* the **Gram Panchayat notice** (SOP-2021: at least 24 h before the operation), EN + HI;
* the **spray record** (SOP-2021: submitted within 7 days), with every SOP check the
  agent ran, its value, limit and clause;
* the **farmer slip**, in Hindi first, led by the first legal harvest date.

Names, plot number, pilot certificate and drone UIN come from ``compliance`` in the
field file. Anything missing prints as "(fill in)" -- the documents never invent a
pilot, a farmer or a licence number.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import records, sop
from .label import unit_for
from .tools import RunState

FILL = "(fill in)"


def _report(d: dict | None) -> sop.Report | None:
    if not d:
        return None
    names = {f.name for f in fields(sop.Check)}
    return sop.Report(d["verdict"], [sop.Check(**{k: v for k, v in c.items() if k in names})
                                     for c in d["checks"]])


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def build(run: dict, field: dict) -> dict[str, str]:
    """run: RunState.summary() (or a saved runs/*.json); field: Field.to_dict()."""
    c = field.get("compliance") or {}
    mission = run.get("mission") or {}
    if not mission:
        raise ValueError("this run proposed no flight; there is nothing to file")
    zones = mission.get("zones", [])
    from .field import Field
    f = Field.from_dict(field)
    from shapely.geometry import Polygon
    zone_ha = {z.zone_id: Polygon([f.frame.to_local(la, lo) for la, lo in z.polygon]).area / 1e4
               for z in f.zones}
    treated = round(sum(zone_ha.get(z, 0.0) for z in zones), 3)
    blanket = round(f.area_m2() / 1e4, 3)
    s = run.get("sop") or {}
    start = _dt(s.get("earliest_clear")) or _dt(s.get("planned_start_utc")) \
        or datetime.now(timezone.utc) + timedelta(hours=24)
    end = start + timedelta(seconds=float(mission.get("est_time_s") or 600))
    lab = run.get("label") or {}
    claim = lab.get("claim") or {}
    product = c.get("product") or claim.get("product") or FILL
    waiting = claim.get("waiting_days", c.get("waiting_days"))
    flight = (run.get("flight") or {}).get("weather", {}).get("now", {})
    chk = {x["rule"]: x for x in s.get("checks", [])}

    def num(rule: str, default: float) -> float:
        try:
            return float(str(chk[rule]["value"]).split()[0])
        except (KeyError, ValueError, IndexError):
            return default

    site = records.Site(
        farmer=c.get("farmer", FILL), village=c.get("village", FILL),
        gram_panchayat=c.get("gram_panchayat", FILL),
        panchayat_samiti=c.get("panchayat_samiti", FILL), district=c.get("district", FILL),
        state=c.get("state", FILL), plot_ref=c.get("plot_ref", FILL),
        lat=f.latitude, lon=f.longitude, area_ha=blanket)
    op = records.Operator(c.get("pilot_name", FILL), c.get("pilot_rpc", FILL),
                          c.get("pilot_phone", FILL), c.get("organisation", ""))
    drone = records.Drone(c.get("drone_uin", FILL), c.get("drone_model", FILL),
                          tank_l=float(c.get("tank_l", 10)))
    app = records.Application(
        crop=f.crop, pest=c.get("pest", FILL), product=product,
        formulation_per_ha=float(c.get("dose_per_ha") or 0.0),
        unit=claim.get("unit") or unit_for(product) or "g",
        volume_l_ha=num("volume", 20.0), zones=zones, treated_ha=treated,
        start=start, end=end, height_m=num("height", 2.0), speed_ms=num("speed", 4.5),
        swath_m=num("swath", 3.0), waiting_days=waiting,
        label_source=", ".join(lab.get("citations") or []) or "label not verified",
        weather={"temp_c": flight.get("temp_c"), "wind_ms": flight.get("wind_ms")},
        blanket_ha=blanket,
        cost_saved_inr=round((blanket - treated) * float(c.get("spray_cost_inr_ha", 1100))))
    return {
        "gram_panchayat_notice": records.notice_html(site, op, drone, app),
        "spray_record": records.record_html(site, op, drone, app, sop_report=_report(s)),
        "farmer_slip": records.farmer_slip_html(site, app),
    }


def write(run: dict, field: dict, out_dir: Path, pdf: bool = True) -> list[Path]:
    out = []
    for name, html in build(run, field).items():
        out.append(records.write_html(html, out_dir / f"{name}.html"))
        if pdf:
            try:
                p = records.write_pdf(html, out_dir / f"{name}.pdf")
                if p:
                    out.append(p)
            except Exception:  # noqa: BLE001 -- PDF is a convenience; HTML prints fine
                pass
    return out


def from_state(s: RunState) -> dict[str, str]:
    return build(s.summary(), s.field.to_dict())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fieldpilot paperwork", description=__doc__.split("\n\n")[0])
    ap.add_argument("run_json", help="runs/R-....json written by a run")
    ap.add_argument("--out", default=None, help="output folder (default runs/paper/<run_id>)")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args(argv)
    run = json.loads(Path(a.run_json).read_text(encoding="utf-8"))
    out = Path(a.out) if a.out else Path(a.run_json).parent / "paper" / run["run_id"]
    for p in write(run, run["field"], out, pdf=not a.no_pdf):
        print(p)
    return 0
