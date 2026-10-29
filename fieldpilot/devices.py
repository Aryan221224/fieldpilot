"""Field devices: the weather mast, the as-applied logger and the battery board.

FieldPilot's plan is only as good as two things it cannot see from a forecast and a closed
flight controller: the air over the field right now, and what actually came out of the
nozzles. Two small ESP32 devices measure them (firmware/fieldmast, firmware/spraylog), and the
crew keeps the battery board on their phone.

* **FieldMast** posts the 2-minute mean wind, 3-second gust, temperature, RH and Delta T every
  10 s. The server judges each reading against the rule pack itself (the device's own
  verdict is only for its LEDs). A fresh NO-GO from the mast blocks sortie approval exactly
  as a BLOCKED SOP verdict does.
* **SprayLog** uploads one record per sortie: flow, cumulative litres and position at 1 Hz.
  The server attributes every increment of litres to the zone it fell in, and reports litres
  and L/ha per zone against the plan, plus any spray outside the treated zones or outside
  the field.
* **Battery board**: each pack's state (charged → flying → cooling → charging), with the time
  in that state and flights today. Battery sets are what limits the day, so they are tracked.

Everything received is appended to ``runs/devices/`` so the record survives a restart.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

PACK_STATES = ("charged", "flying", "cooling", "charging")
MAST_FRESH_S = 600          # a mast reading older than this does not gate approval
MAST_NEAR_KM = 2.0          # a mast further than this from the field does not gate it
MIN_WIND_SAMPLES = 30       # the mast needs 30 s of wind before its mean means anything


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _num(v, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or not lo <= f <= hi:
        return None
    return f


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class DeviceHub:
    def __init__(self, store: Path, clock=time.time):
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.Lock()
        self.mast: dict[str, dict] = {}
        self.mast_hist: dict[str, deque] = {}
        self.sessions: dict[str, dict] = {}
        self.packs: dict[str, dict] = {}
        self.seen: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        for p in sorted(self.store.glob("spraylog_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                self.sessions[d["session_id"]] = d
            except Exception:  # noqa: BLE001
                continue
        pb = self.store / "packs.json"
        if pb.exists():
            try:
                self.packs = json.loads(pb.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.packs = {}

    def _append(self, name: str, row: dict) -> None:
        with (self.store / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _touch(self, device_id: str, kind: str, body: dict) -> None:
        self.seen[device_id] = {"device_id": device_id, "kind": kind, "t": self.clock(),
                                "fw": str(body.get("fw") or "")[:40],
                                "battery_v": _num(body.get("battery_v"), 0, 80),
                                "rssi": _num(body.get("rssi"), -120, 0),
                                "simulated": device_id.upper().startswith("SIM-") or
                                "simulated" in str(body.get("fw") or "").lower()}

    # ------------------------------------------------------------------ FieldMast
    def ingest_mast(self, body: dict, pack_id: str) -> dict:
        from .rules import delta_t, pack
        dev = str(body.get("device_id") or "MAST")[:40]
        wind = _num(body.get("wind_ms"), 0, 60)
        gust = _num(body.get("gust_ms"), 0, 80)
        temp = _num(body.get("temp_c"), -30, 60)
        rh = _num(body.get("rh_pct"), 0, 100)
        n = int(_num(body.get("wind_samples"), 0, 100000) or 0)
        lat = _num(body.get("lat"), -90, 90)
        lon = _num(body.get("lon"), -180, 180)
        p = pack(pack_id)
        if wind is None or temp is None or rh is None:
            ok, reasons = None, ["mast sensor fault: wind, temperature or humidity missing"]
        elif n < MIN_WIND_SAMPLES:
            ok, reasons = None, [f"mast warming up ({n} s of wind)"]
        else:
            reasons = p.site_reasons(wind, temp, rh)
            ok = not reasons
        now = self.clock()
        r = {"device_id": dev, "t": now, "utc": _utc(now), "pack": p.pack_id,
             "wind_ms": wind, "gust_ms": gust, "temp_c": temp, "rh_pct": rh,
             "delta_t": round(delta_t(temp, rh), 2) if temp is not None and rh is not None else None,
             "wind_samples": n, "lat": lat if lat else None, "lon": lon if lon else None,
             "ok": ok, "reasons": reasons}
        with self.lock:
            self.mast[dev] = r
            self.mast_hist.setdefault(dev, deque(maxlen=360)).append(
                {k: r[k] for k in ("t", "wind_ms", "gust_ms", "temp_c", "rh_pct", "ok")})
            self._touch(dev, "mast", body)
        self._append(f"mast_{_utc(now)[:10]}.jsonl", r)
        return r

    def site(self, near: tuple[float, float] | None = None, max_age_s: float = MAST_FRESH_S) -> dict | None:
        """The freshest mast reading at (or not placed away from) the field."""
        now = self.clock()
        best = None
        with self.lock:
            for r in self.mast.values():
                age = now - r["t"]
                if age > max_age_s:
                    continue
                if near and r.get("lat") and r.get("lon") and _km(near, (r["lat"], r["lon"])) > MAST_NEAR_KM:
                    continue
                if best is None or r["t"] > best["t"]:
                    best = r
        return None if best is None else best | {"age_s": round(now - best["t"], 1)}

    def site_gate(self, near: tuple[float, float] | None = None) -> dict:
        """ok True/False from a fresh mast; None = no mast, or it cannot judge yet."""
        r = self.site(near)
        if r is None:
            return {"ok": None, "reasons": ["no FieldMast reading at this field in the last "
                                            f"{MAST_FRESH_S // 60} min"], "reading": None}
        return {"ok": r["ok"], "reasons": r["reasons"], "reading": r}

    # ------------------------------------------------------------------ SprayLog
    def ingest_spraylog(self, body: dict, run_id: str | None = None) -> dict:
        sid = str(body.get("session_id") or "")[:80]
        dev = str(body.get("device_id") or "SPRAYLOG")[:40]
        if not sid:
            raise ValueError("session_id missing")
        rows = []
        for s in body.get("samples") or []:
            if not isinstance(s, (list, tuple)) or len(s) < 5:
                continue
            t = _num(s[0], 0, 86400)
            lat = _num(s[1], -90, 90)
            lon = _num(s[2], -180, 180)
            lpm = _num(s[3], 0, 100) or 0.0
            lit = _num(s[4], 0, 1000)
            spd = _num(s[5], 0, 100) if len(s) > 5 else None
            q = int(_num(s[6], 0, 10) or 0) if len(s) > 6 else (1 if lat is not None else 0)
            if t is None or lit is None:
                continue
            rows.append([t, lat, lon, lpm, lit, spd, q])
        rows.sort(key=lambda r: r[0])
        now = self.clock()
        d = {"session_id": sid, "device_id": dev, "received_utc": _utc(now), "t": now,
             "run_id": run_id, "fw": str(body.get("fw") or "")[:40],
             "k_pulses_per_l": _num(body.get("k_pulses_per_l"), 0, 1e6),
             "litres_total": _num(body.get("litres_total"), 0, 1000),
             "samples": rows}
        if d["litres_total"] is None and rows:
            d["litres_total"] = rows[-1][4]
        with self.lock:
            self.sessions[sid] = d
            self._touch(dev, "spraylog", body)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sid)
        (self.store / f"spraylog_{safe}.json").write_text(json.dumps(d), encoding="utf-8")
        return {"session_id": sid, "samples": len(rows), "litres_total": d["litres_total"],
                "run_id": run_id}

    def sessions_for(self, run_id: str | None) -> list[dict]:
        with self.lock:
            ss = [s for s in self.sessions.values() if run_id is None or s.get("run_id") == run_id]
        return sorted(ss, key=lambda s: s["t"])

    # ------------------------------------------------------------------ battery board
    def pack_event(self, pack_id: str, state: str, note: str = "") -> dict:
        pack_id = str(pack_id)[:20]
        if state not in PACK_STATES:
            raise ValueError(f"state must be one of {PACK_STATES}")
        now = self.clock()
        today = _utc(now)[:10]
        with self.lock:
            p = self.packs.get(pack_id) or {"pack_id": pack_id, "flights": {}, "state": None}
            if state == "flying" and p.get("state") != "flying":
                p["flights"][today] = p["flights"].get(today, 0) + 1
            p.update(state=state, since=now, note=note[:80])
            self.packs[pack_id] = p
            (self.store / "packs.json").write_text(json.dumps(self.packs), encoding="utf-8")
        self._append("packs.jsonl", {"pack_id": pack_id, "state": state, "t": now, "note": note[:80]})
        return self.pack_view(p)

    def pack_view(self, p: dict) -> dict:
        now = self.clock()
        return {"pack_id": p["pack_id"], "state": p.get("state"),
                "minutes_in_state": round((now - p["since"]) / 60.0, 1) if p.get("since") else None,
                "flights_today": p.get("flights", {}).get(_utc(now)[:10], 0), "note": p.get("note", "")}

    def packs_view(self) -> list[dict]:
        with self.lock:
            return [self.pack_view(p) for _, p in sorted(self.packs.items())]

    # ------------------------------------------------------------------ status
    def status(self) -> list[dict]:
        now = self.clock()
        with self.lock:
            return [d | {"last_seen_s": round(now - d["t"], 1)} for d in
                    sorted(self.seen.values(), key=lambda d: d["device_id"])]

    def mast_history(self, device_id: str | None = None) -> list[dict]:
        with self.lock:
            if device_id is None and self.mast:
                device_id = max(self.mast.values(), key=lambda r: r["t"])["device_id"]
            return list(self.mast_hist.get(device_id or "", []))


# ---------------------------------------------------------------------- as-applied

def planned_litres(field_, mission, spec) -> dict[str, float]:
    from shapely.geometry import Polygon
    out = {}
    for z in mission.zones:
        poly = Polygon([field_.frame.to_local(a, b) for a, b in field_.zone(z).polygon])
        out[z] = round(poly.area * spec.spray_rate_l_ha / 10_000.0, 4)
    return out


def as_applied(sessions: list[dict], field_, planned: dict[str, float],
               tolerance_pct: float = 15.0) -> dict:
    """Litres per zone from the logged flow and positions, against the plan.

    Each increment of litres between two samples is placed at the midpoint of their positions
    and assigned to the zone containing it, to "field, untreated zone" if it is inside the
    geofence but in no planned zone, or to "outside field".
    """
    from shapely.geometry import Point, Polygon
    fr = field_.frame
    zones = {z.zone_id: Polygon([fr.to_local(a, b) for a, b in z.polygon]) for z in field_.zones}
    fence = Polygon([fr.to_local(a, b) for a, b in field_.geofence])
    applied: dict[str, float] = {}
    off_zone = outside = unlocated = 0.0
    total = 0.0
    secs = 0.0
    peak = 0.0
    track = []
    for s in sessions:
        rows = s.get("samples") or []
        total += float(s.get("litres_total") or (rows[-1][4] if rows else 0.0))
        if rows and rows[0][4] > 0:                       # litres before the first sample
            unlocated += rows[0][4]
        for a, b in zip(rows, rows[1:]):
            dl = max(0.0, b[4] - a[4])
            if b[3] > 0.05:
                secs += b[0] - a[0]
            peak = max(peak, b[3])
            if dl <= 0:
                continue
            if None in (a[1], a[2], b[1], b[2]) or (b[6] == 0):
                unlocated += dl
                continue
            x, y = fr.to_local((a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
            pt = Point(x, y)
            hit = next((zid for zid, poly in zones.items() if poly.contains(pt)), None)
            if hit is not None and hit in planned:
                applied[hit] = applied.get(hit, 0.0) + dl
            elif fence.contains(pt):
                if hit is not None:
                    applied[hit] = applied.get(hit, 0.0) + dl     # an untreated zone got spray
                off_zone += dl
            else:
                outside += dl
        track += [[round(r[1], 7), round(r[2], 7), round(r[3], 2)] for r in rows[::2]
                  if r[1] is not None and r[2] is not None]
    rows_out = []
    flags = []
    for z, pl in planned.items():
        got = applied.get(z, 0.0)
        area_ha = zones[z].area / 10_000.0
        dev = (got - pl) / pl * 100.0 if pl else None
        rows_out.append({"zone_id": z, "planned_l": round(pl, 3), "applied_l": round(got, 3),
                         "deviation_pct": round(dev, 1) if dev is not None else None,
                         "applied_l_ha": round(got / area_ha, 1) if area_ha else None})
        if dev is not None and abs(dev) > tolerance_pct:
            flags.append(f"zone {z}: {got:.2f} L applied vs {pl:.2f} L planned ({dev:+.0f} %)")
    for z, got in applied.items():
        # below 0.05 L is within GPS attribution noise at a zone border (u-blox class, ~0.7 m)
        if z not in planned and got > 0.05:
            flags.append(f"zone {z} was not to be treated but received {got:.3f} L")
    if outside > 0.01:
        flags.append(f"{outside:.3f} L sprayed outside the field")
    attributed = sum(applied.get(z, 0.0) for z in planned)
    return {"sessions": [s["session_id"] for s in sessions], "zones": rows_out,
            "litres_total": round(total, 3), "litres_in_planned_zones": round(attributed, 3),
            "litres_in_field_untreated": round(off_zone, 3), "litres_outside_field": round(outside, 3),
            "litres_unlocated": round(unlocated, 3), "spray_seconds": round(secs, 1),
            "peak_lpm": round(peak, 3),
            "on_target_pct": round(attributed / total * 100.0, 1) if total else None,
            "flags": flags, "ok": not flags, "track": track[:3000]}
