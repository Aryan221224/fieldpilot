"""Stand-in devices for a demo without hardware: they post to a running FieldPilot exactly
what the firmware posts, with ``SIM-`` device ids so the dashboard labels them SIMULATED.

    python -m fieldpilot.cli devsim mast                          # 2.1 m/s, 29 °C, 58 %
    python -m fieldpilot.cli devsim mast --wind 3.4 --count 30    # a NO-GO for the approval gate
    python -m fieldpilot.cli devsim spraylog runs/fieldday/2026-10-30/devices/spraylog_X.json
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request
from pathlib import Path


def _post(url: str, body: dict, token: str = "") -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **({"X-FP-Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fieldpilot devsim")
    ap.add_argument("kind", choices=["mast", "spraylog"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--token", default="")
    ap.add_argument("--wind", type=float, default=2.1)
    ap.add_argument("--temp", type=float, default=29.0)
    ap.add_argument("--rh", type=float, default=58.0)
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--count", type=int, default=0, help="0 = until Ctrl-C")
    a = ap.parse_args(argv)
    if a.kind == "spraylog":
        if not a.file:
            print("give the SprayLog JSON to upload (a fieldday run writes them under devices/)")
            return 2
        body = json.loads(Path(a.file).read_text(encoding="utf-8"))
        body["device_id"] = "SIM-" + str(body.get("device_id", "SPRAYLOG"))
        print(_post(a.url.rstrip("/") + "/api/devices/spraylog", body, a.token))
        return 0
    rnd = random.Random(1)
    buf: list[float] = []
    n = 0
    while a.count == 0 or n < a.count:
        for _ in range(int(a.every)):
            buf.append(max(0.0, a.wind * (1 + rnd.gauss(0, 0.2))))
        buf = buf[-120:]
        mean = sum(buf) / len(buf)
        gust = max([mean] + [sum(buf[i - 2:i + 1]) / 3 for i in range(2, len(buf))])
        body = {"device_id": "SIM-MAST", "fw": "fieldmast (simulated)", "wind_ms": round(mean, 2),
                "gust_ms": round(gust, 2), "wind_samples": max(30, len(buf)), "temp_c": a.temp + rnd.gauss(0, 0.1),
                "rh_pct": a.rh + rnd.gauss(0, 0.5), "battery_v": 12.4, "rssi": -58}
        try:
            code = _post(a.url.rstrip("/") + "/api/devices/mast", body, a.token)
        except Exception as e:  # noqa: BLE001
            code = f"error {e}"
        print(f"mast {body['wind_ms']} m/s gust {body['gust_ms']} -> {code}")
        n += 1
        if a.count == 0 or n < a.count:
            time.sleep(a.every)
    return 0
