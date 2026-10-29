"""Command line: demo, run, models, calibrate.

    python -m fieldpilot.cli demo                 # whole loop, offline, simulated drone
    python -m fieldpilot.cli run photos/          # photos named A1_*.jpg, B2_*.jpg ...
    python -m fieldpilot.cli models               # what Token Factory offers, what we'd pick
    python -m fieldpilot.cli calibrate labels.csv # build the conformal calibration file
    python -m fieldpilot.cli doctor               # what works live, what is stubbed, what is missing
    python -m fieldpilot.cli doctor --submission  # is the repo ready for Devpost?
    python -m fieldpilot.cli day examples/desk_demo_in.json  # bookings -> the operator's day
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from .config import ROOT, load


def _load_images(folder: Path, zones: list[str]) -> dict[str, list[bytes]]:
    out: dict[str, list[bytes]] = defaultdict(list)
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        m = re.match(r"([A-Za-z]\d+)[_\-. ]", p.name + " ")
        if m and m.group(1).upper() in zones:
            out[m.group(1).upper()].append(p.read_bytes())
    return dict(out)


def _print_event(e: dict) -> None:
    t = e["type"]
    if t == "tool_call":
        print(f"  -> {e['name']}({json.dumps(e.get('arguments', {}))[:110]})  [{e.get('protocol', '')}]")
    elif t == "tool_result":
        r = e.get("result", {})
        if not e.get("ok"):
            print(f"     x {r.get('error', '')[:160]}")
        elif e["name"] == "inspect_zones":
            for z in r.get("zones", []):
                flag = "REVIEW" if z.get("needs_human_review") else "ok"
                print(f"     {z['zone']}: {z.get('finding')} sev {z.get('severity')} "
                      f"conf {z.get('confidence')}  [{flag}]")
        elif e["name"] == "simulate_zone_options":
            for z in r.get("zones", []):
                print(f"     {z['zone']}: {z['recommended']:<10} {z['reason'][:110]}")
        elif e["name"] == "build_treatment_mission":
            print(f"     mission {r.get('mission_id')} ok={r.get('ok')} zones={r.get('zones_in_order')} "
                  f"{r.get('est_time_min')} min {r.get('spray_ml')} mL")
        elif e["name"] == "check_flight_window":
            print(f"     weather_go={r.get('weather_go')} drone_ready={r.get('drone_ready')} "
                  f"({r.get('weather_source')})")
        elif e["name"] == "search_advisories":
            print(f"     {r.get('source')}: {len(r.get('results', []))} results")
    elif t == "thought":
        print(f"  .. {e['text'][:140]}")
    elif t in ("status", "error", "say"):
        print(f"  [{t}] {e.get('message') or e.get('text', '')}"[:200])


def cmd_demo(args) -> int:
    from .service import FieldPilot
    imgs = ROOT / "examples" / "images"
    if not any(imgs.glob("*.jpg")):
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "examples" / "make_demo_images.py")], check=True)
    fp = FieldPilot(sim_time_scale=args.speed)
    print(f"FieldPilot demo | field: {fp.field.name} | mode: {fp.cfg.mode()}")
    s = fp.new_run(_load_images(imgs, fp.field.zone_ids()), emit=_print_event)
    p = s.proposal
    print("\nPROPOSAL:", p["headline"])
    print("  EN:", p["rationale"])
    if p.get("rationale_hi"):
        print("  HI:", p["rationale_hi"])
    if p.get("mission_id") and not args.no_fly:
        print(f"\nOperator approves {p['mission_id']} ...")
        r = fp.approve(s.run_id, operator="demo")
        print("  dispatched:", r["dispatched"], "" if r["dispatched"] else r)
        while r["dispatched"]:
            t = fp.drone.telemetry()
            print(f"  [{t.state:9}] seq {t.mission_seq:>2}/{t.mission_total} alt {t.alt_m:4.1f} m "
                  f"batt {t.battery_pct:4.1f}% {'SPRAY ' + t.zone_id if t.spraying else '':12} {t.message}")
            if t.state in ("landed", "aborted", "error"):
                break
            time.sleep(1.0)
    print(f"\nrun log: {fp.cfg.runs_dir / (s.run_id + '.json')}")
    return 0


def cmd_run(args) -> int:
    from .service import FieldPilot
    fp = FieldPilot()
    imgs = _load_images(Path(args.folder), fp.field.zone_ids())
    if not imgs:
        print("no photos matched zone names (name them A1_xxx.jpg, B2_xxx.jpg ...)")
        return 2
    s = fp.new_run(imgs, note=args.note or "", emit=_print_event)
    print(json.dumps(s.proposal, indent=1, ensure_ascii=False))
    return 0


def cmd_models(args) -> int:
    cfg = load()
    if not cfg.live_llm:
        print("NEBIUS_API_KEY is not set (see .env.example).")
        return 2
    from .llm import TokenFactory, is_nvidia
    tf = TokenFactory(cfg)
    ids = tf.catalog()
    print(f"{len(ids)} models in your Token Factory catalog:")
    for i in ids:
        print(("  * " if is_nvidia(i) else "    ") + i)
    picks = tf.resolve()
    print("\nFieldPilot would use:")
    print(f"  agent  : {picks['agent']}  {'(NVIDIA)' if is_nvidia(picks['agent']) else ''}")
    print(f"  vision : {picks['vision']}  {'(NVIDIA)' if is_nvidia(picks['vision']) else ''}")
    if not picks["vision"]:
        print("  -> no VLM in the catalog: deploy Cosmos-Reason2 with nebius/deploy_perception_endpoint.sh")
    return 0


def cmd_calibrate(args) -> int:
    """labels.csv columns: image_path,label  (label as the VLM would name it, or 'healthy')."""
    from .llm import TokenFactory
    from .perception import ConformalGate, Perception
    cfg = load()
    if not cfg.live_llm:
        print("calibration needs the live vision model (NEBIUS_API_KEY)")
        return 2
    per = Perception(llm=TokenFactory(cfg), crop=cfg.crop)
    scores = []
    with open(args.labels, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            o = per.observe("CAL", [Path(row["image_path"]).read_bytes()])
            truth = row["label"].strip().lower()
            p = max([f["confidence"] for f in o.findings
                     if truth in f["label"].lower() or f["label"].lower() in truth] or [0.0])
            scores.append(1.0 - p)
            print(f"{row['image_path']}: truth={truth} p={p:.2f}")
    gate = ConformalGate.from_scores(scores, alpha=args.alpha)
    out = ROOT / "calibration.json"
    gate.save(out)
    print(f"\nn={gate.n} alpha={gate.alpha} qhat={gate.qhat} -> {out}")
    if gate.n < 20:
        print("WARNING: fewer than 20 labelled photos; the gate stays in UNCALIBRATED mode.")
    return 0


def cmd_doctor(args) -> int:
    from .doctor import main as doctor_main
    return doctor_main(submission_mode=args.submission)


def main(argv: list[str] | None = None) -> int:
    import logging
    # Windows consoles and pipes default to cp1252, which cannot encode the Hindi
    # rationale; never crash on output.
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["bench"]:
        # Spray hardware on the bench, mirrored from the SITL mission (docs/BENCH_RIG.md).
        from .bench import main as bench_main
        return bench_main(argv[1:])
    if argv[:1] == ["day"]:
        # Operator desk: Nemotron reads the bookings, the day is planned against live
        # forecasts, SOP windows, sorties and batteries (fieldpilot/desk.py).
        dp = argparse.ArgumentParser(prog="fieldpilot day")
        dp.add_argument("bookings", help="JSON with operator + messages and/or jobs")
        dp.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
        dp.add_argument("--fill-submission", action="store_true",
                        help="write the measured comparison into docs/SUBMISSION.md ([DAY])")
        a = dp.parse_args(argv[1:])
        from .desk import main as desk_main
        return desk_main(a.bookings, a.date, a.fill_submission)
    if argv[:1] == ["rehearse"]:
        # Whole project end to end against a local Token Factory stand-in and an emulated
        # pump; predictions for `deviation`. Never counts for the submission (rehearsal.py).
        from .rehearsal import run_all
        return run_all(quick="--quick" in argv)
    if argv[:1] == ["deviation"]:
        from .deviation import main as dev_main
        return dev_main(argv[1:])
    if argv[:1] == ["airframe"]:
        # The project airframe (T1 10 L quad) from its spec sheet, and ArduPilot params.
        import json as _json
        from .airframe import report
        r = report()
        if "--json" in argv:
            print(_json.dumps(r, indent=1, default=str))
            return 0
        d, a = r["derived"], r["assumptions"]
        print(f"{r['airframe']}  ·  flight controller: JIYI K3-A Pro (closed; FP_DRONE=k3)")
        print(f"  pack {d['pack_wh']} Wh ({a['cells']}S 16 Ah, {d['pack_kg']} kg)  usable {d['usable_wh']} Wh "
              f"({a['reserve']:.0%} reserve)")
        print(f"  hover {d['hover_w_full_24kg']} W at 24 kg, {d['hover_w_empty_14kg']} W at 14 kg  -> "
              f"{d['hover_min_predicted_14kg']} min at 14 kg (sheet: {d['hover_min_sheet_14kg']})")
        print(f"  flow {d['flow_l_min_at_spray_speed']} L/min at {a['spray_speed_ms']} m/s, "
              f"{a['spray_rate_l_ha']} L/ha; pump holds the rate to {d['pump_limited_speed_ms']} m/s")
        print(f"  take-off {d['takeoff_kg_full_tank']} kg full: India category {d['india_category_full_tank']} "
              f"(at 28 kg MTOW: {d['india_category_at_mtow']})")
        w = d["work_rate"]
        print(f"  work rate {w['acres_per_h']} acres/h flying + swaps (sheet: {d['work_rate_sheet_acres_h']})")
        for key, label in (("kit_as_shipped", "kit as shipped"), ("kit_4_packs_2_chargers", "4 packs, 2 chargers")):
            k = d[key]
            print(f"  {label:20s} {k['sorties']} sorties, {k['ha']} ha in a {k['window_h']:g} h window "
                  f"({k['ha_per_h']} ha/h; aircraft busy {k['aircraft_busy_pct']} %)")
        print("  ArduPilot (if the FC is ever swapped):",
              ", ".join(f"{k}={v:g}" for k, v in r["ardupilot_params"].items()))
        return 0
    if argv[:1] == ["fieldday"]:
        from .fieldday import main as fd_main
        return fd_main(argv[1:])
    if argv[:1] == ["sitl"]:
        from .sitl import main as sitl_main
        return sitl_main(argv[1:])
    if argv[:1] == ["devsim"]:
        from .devsim import main as ds_main
        return ds_main(argv[1:])
    if argv[:1] == ["flywheel"]:
        from .flywheel import main as fly_main
        return fly_main(argv[1:])
    if argv[:1] == ["paperwork"]:
        from .paperwork import main as paperwork_main
        return paperwork_main(argv[1:])
    ap = argparse.ArgumentParser(prog="fieldpilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--speed", type=float, default=6.0, help="simulator time scale")
    d.add_argument("--no-fly", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("folder")
    r.add_argument("--note", default="")
    sub.add_parser("models")
    c = sub.add_parser("calibrate")
    c.add_argument("labels")
    c.add_argument("--alpha", type=float, default=0.1)
    dr = sub.add_parser("doctor")
    dr.add_argument("--submission", action="store_true", help="check Devpost readiness instead")
    a = ap.parse_args(argv)
    return {"demo": cmd_demo, "run": cmd_run, "models": cmd_models,
            "calibrate": cmd_calibrate, "doctor": cmd_doctor}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
