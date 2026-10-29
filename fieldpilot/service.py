"""Wiring: config -> field, models, drone; runs; approval; audit log.

Approval is the only code path that reaches the motors, and it re-checks
everything the mission was validated against -- weather and preflight are
fetched again at the moment of approval, because a mission that was safe ten
minutes ago is not evidence that it is safe now.
"""
from __future__ import annotations

import os

import json
import logging
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import context
from .agent import StubLLM, run_agent
from .config import Config, load
from .drone import make_backend
from .field import Field
from .mission import sortie_mission, DroneSpec, recheck
from .perception import ConformalGate, Perception
from .tools import RunState, Toolbox, sop_report

log = logging.getLogger(__name__)


class FieldPilot:
    def __init__(self, cfg: Config | None = None, today: date | None = None,
                 ensemble: tuple[int, int] = (5, 3), sim_time_scale: float = 4.0):
        self.cfg = cfg or load()
        self.field = Field.load(self.cfg.field_path)
        if self.cfg.crop:
            self.field.crop = self.cfg.crop
        if self.cfg.variety:
            self.field.variety = self.cfg.variety
        self.today = today or date.today()
        self.ensemble = ensemble
        # The project airframe (T1 10 L quad, fieldpilot/airframe.py) unless FP_DRONE_SPEC
        # names another preset (bench_quad, agri_10l, agri_40l).
        import os
        from .mission import drone_spec
        self.spec = drone_spec(os.environ.get("FP_DRONE_SPEC") or None)
        self.gate = ConformalGate.load(self.cfg.runs_dir.parent / "calibration.json")
        self.cfg.runs_dir.mkdir(parents=True, exist_ok=True)
        self.runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        # Field devices: FieldMast (site weather), SprayLog (as applied), battery board
        from .devices import DeviceHub
        self.devices = DeviceHub(self.cfg.runs_dir / "devices")
        self.rule_pack = os.environ.get("FP_RULE_PACK") or "IN-SOP-2023"
        self.llm = None
        if self.cfg.live_llm:
            from .llm import TokenFactory
            self.llm = TokenFactory(self.cfg)
        kw = dict(home=self.field.home, spec=self.spec, time_scale=sim_time_scale)
        if self.cfg.drone == "mavlink":
            kw = dict(url=self.cfg.mavlink_url, spec=self.spec, payload_servo=self.cfg.payload_servo,
                      pwm_on=self.cfg.payload_pwm_on, pwm_off=self.cfg.payload_pwm_off,
                      spray_cmd=self.cfg.spray_cmd)
        elif self.cfg.drone == "k3":
            kw = dict(spec=self.spec, field=self.field, out_dir=self.cfg.runs_dir)
        self.drone = make_backend(self.cfg.drone, **kw)
        self.drone_error = None
        try:
            self.drone.connect()
        except Exception as e:  # noqa: BLE001
            self.drone_error = f"{type(e).__name__}: {e}"
            log.warning("drone not connected: %s", self.drone_error)

    # -- runs ---------------------------------------------------------------------
    def new_run(self, images: dict[str, list[bytes]], note: str = "",
                emit: Callable[[dict], None] | None = None) -> RunState:
        unknown = [z for z in images if z not in self.field.zone_ids()]
        if unknown:
            raise ValueError(f"unknown zones {unknown}; field zones are {self.field.zone_ids()}")
        s = RunState(run_id="R-" + datetime.now(timezone.utc).strftime("%H%M%S") + "-" +
                     uuid.uuid4().hex[:4], field=self.field, images=images,
                     today=self.today, mode=self.cfg.mode())
        with self._lock:
            self.runs[s.run_id] = s
        self._execute(s, emit, note)
        return s

    def _execute(self, s: RunState, emit, note: str = "") -> None:
        perception = Perception(llm=self.llm if (self.llm and self._vision_ok()) else None,
                                gate=self.gate, crop=self.field.crop)
        tb = Toolbox(s, perception, drone_backend=self.drone, drone_spec=self.spec,
                     tavily_key=self.cfg.tavily_api_key, live_weather=self.cfg.live_llm,
                     ensemble=self.ensemble)
        llm = self.llm or StubLLM(lambda: s)
        if self.cfg.rehearsal:
            from . import rehearsal
            rehearsal.bind(lambda: s)        # the stand-in answers for this run
        run_agent(s, tb, llm, emit=emit, user_note=note)
        self.save(s)

    def replan(self, run_id: str, emit=None, note: str = "re-plan after human review") -> RunState:
        """Run the agent again on the same visit, keeping human adjudications."""
        s = self.runs[run_id]
        if s.proposal and s.proposal.get("status") == "dispatched":
            raise ValueError("this run's mission was already dispatched")
        s.proposal, s.mission, s.flight = None, None, None
        s.sop, s.label = None, None
        s.evals.clear()
        s.plans.clear()
        s.trace.append({"type": "status", "message": "re-plan requested"})
        self._execute(s, emit, note)
        return s

    def _vision_ok(self) -> bool:
        try:
            return bool(self.llm.resolve().get("vision"))
        except Exception:  # noqa: BLE001
            return False

    def save(self, s: RunState) -> Path:
        p = self.cfg.runs_dir / f"{s.run_id}.json"
        d = s.summary()
        d["trace"] = s.trace
        p.write_text(json.dumps(d, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    # -- human decisions ------------------------------------------------------------
    def approve(self, run_id: str, operator: str = "operator", override_reason: str = "") -> dict:
        """Dispatch only if the safety re-check passes AND the SOP re-check is CLEAR.

        The SOP is assessed again now, with a fresh forecast. BLOCKED never dispatches.
        NEEDS_HUMAN dispatches only with a written ``override_reason`` from the operator
        (e.g. "notice sent on paper 26 Oct, copy in file"); the reason goes in the record.
        """
        s = self.runs[run_id]
        if not s.proposal or not s.proposal.get("mission_id") or s.mission is None:
            raise ValueError("this run has no mission to approve")
        if s.proposal.get("status") != "awaiting_approval":
            raise ValueError(f"proposal is {s.proposal.get('status')}, not awaiting approval")
        w = context.flight_conditions(self.field.latitude, self.field.longitude,
                                      live=self.cfg.live_llm)
        if s.mission.next_sortie > 0 and hasattr(self.drone, "swap_battery"):
            self.drone.swap_battery()            # simulator only; a real crew swaps by hand
        pre = self.drone.preflight()
        m = recheck(self.field, s.mission, self.spec, w, pre)
        sop_now = sop_report(s, self.spec, live=self.cfg.live_llm)
        s.sop = sop_now
        site = self.devices.site_gate(near=self.field.home)
        record = {"operator": operator, "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "checks": m.checks, "sop_verdict": sop_now["verdict"],
                  "site_mast": {"ok": site["ok"], "reasons": site["reasons"],
                                "device": (site["reading"] or {}).get("device_id"),
                                "wind_ms": (site["reading"] or {}).get("wind_ms"),
                                "temp_c": (site["reading"] or {}).get("temp_c"),
                                "rh_pct": (site["reading"] or {}).get("rh_pct")},
                  "override_reason": override_reason.strip() or None}
        sop_block = None
        if sop_now["verdict"] == "BLOCKED":
            sop_block = "SOP BLOCKED: " + sop_now["headline"]
        elif site["ok"] is False:
            # Measured at the field beats the forecast: a NO-GO from the mast is a hard block.
            r = site["reading"]
            sop_block = (f"FIELD MAST NO-GO ({r['device_id']}, {r['age_s']:.0f} s ago): "
                         + "; ".join(site["reasons"]))
        elif sop_now["verdict"] == "NEEDS_HUMAN" and len(override_reason.strip()) < 10:
            sop_block = ("SOP needs a human: " + sop_now["headline"] +
                         ". Give a written override reason (>= 10 characters) to dispatch.")
        if sop_block:
            s.proposal["status"] = "awaiting_approval"
            s.proposal.setdefault("refusals", []).append(record | {"reason": sop_block})
            self.save(s)
            return {"dispatched": False, "reason": sop_block, "sop": sop_now, "site_mast": site,
                    "earliest_clear": sop_now.get("earliest_clear"),
                    "failed": [c for c in sop_now["checks"] if c["hard"] and c["ok"] is not True]}
        if not m.ok:
            s.proposal["status"] = "blocked_at_approval"
            s.proposal["approval"] = record
            self.save(s)
            return {"dispatched": False, "reason": "safety re-check failed at approval",
                    "failed": [c for c in m.checks if not c["ok"]]}
        n = max(1, len(m.sorties))
        k = m.next_sortie
        fly = sortie_mission(m, k) if n > 1 else m
        if hasattr(self.drone, "drift"):
            # closed controller: hand the pilot drift-compensated corners when the mast measured
            # the wind and the forecast gives its direction
            self.drone.drift = None
            r = (site.get("reading") or {})
            wd = self._forecast_wind_dir()
            if r.get("wind_ms") is not None and wd is not None:
                from .drift import compensated_field
                cf, info = compensated_field(self.field, r["wind_ms"], wd)
                if info.get("applied"):
                    self.drone.drift = (cf, info)
                    record["drift_compensation"] = info
        self.drone.upload(fly)
        self.drone.start()
        m.next_sortie = k + 1
        s.proposal.setdefault("approvals", []).append(record | {"sortie": k + 1, "of": n})
        s.proposal["approval"] = record
        if m.next_sortie < n:
            # Each sortie is its own approval: the crew swaps the battery and refills the tank,
            # and weather, preflight and the SOP are checked again before the next take-off.
            s.proposal["status"] = "awaiting_approval"
            s.proposal["sorties_flown"] = m.next_sortie
            self.save(s)
            nxt = m.load["rows"][m.next_sortie] if m.load.get("rows") else {}
            return {"dispatched": True, "mission_id": fly.mission_id, "sortie": k + 1, "of": n,
                    "next": (f"after landing: swap battery, refill {nxt.get('water_l')} L"
                             + (f" + {nxt.get('product')} {m.load.get('unit') or 'g'} product"
                                if nxt.get("product") else "")
                             + f", then approve sortie {k + 2} of {n}"),
                    "checks": m.checks}
        s.proposal["status"] = "dispatched"
        self.save(s)
        return {"dispatched": True, "mission_id": fly.mission_id, "sortie": k + 1, "of": n,
                "checks": m.checks}

    def _forecast_wind_dir(self) -> float | None:
        """Wind direction (from, degrees) for this hour at the field, from the forecast."""
        if not self.cfg.live_llm:
            return None
        try:
            from .sop import fetch_forecast
            now = datetime.now(timezone.utc)
            hs = fetch_forecast(self.field.latitude, self.field.longitude, days=1)
            h = min(hs, key=lambda x: abs((x.time - now).total_seconds()))
            return h.wind_from_deg
        except Exception:  # noqa: BLE001
            return None

    def flying_run_id(self) -> str | None:
        """The run whose mission was approved most recently (a SprayLog upload belongs to it)."""
        best, t = None, ""
        for rid, s in self.runs.items():
            ap = (s.proposal or {}).get("approvals") or []
            if ap and ap[-1].get("utc", "") >= t:
                best, t = rid, ap[-1]["utc"]
        return best

    def as_applied(self, run_id: str | None = None) -> dict | None:
        from .devices import as_applied, planned_litres
        rid = run_id or self.flying_run_id()
        if not rid or rid not in self.runs or self.runs[rid].mission is None:
            return None
        sessions = self.devices.sessions_for(rid)
        if not sessions:
            return None
        rep = as_applied(sessions, self.field, planned_litres(self.field, self.runs[rid].mission, self.spec))
        return rep | {"run_id": rid, "mission_id": self.runs[rid].mission.mission_id}

    def reject(self, run_id: str, reason: str = "") -> dict:
        s = self.runs[run_id]
        if s.proposal:
            s.proposal["status"] = "rejected"
            s.proposal["rejection_reason"] = reason
            self.save(s)
        return {"ok": True}

    def abort(self) -> dict:
        self.drone.abort()
        return {"ok": True, "message": "RTL commanded"}

    def adjudicate(self, run_id: str, zone_id: str, category: str, label: str,
                   severity: int) -> dict:
        """A human settles a review-flagged zone. The label also feeds calibration."""
        s = self.runs[run_id]
        o = s.observations[zone_id]
        o.findings = [{"category": category, "label": label, "severity": int(severity),
                       "confidence": 1.0, "evidence": "human adjudication"}]
        o.needs_review, o.review_reasons = False, []
        o.source += " + human"
        o.prediction_set = [label]
        s.evals.pop(zone_id, None)
        with open(self.cfg.runs_dir / "adjudications.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": run_id, "zone_id": zone_id, "category": category,
                                 "label": label, "severity": severity}) + "\n")
        self.save(s)
        return {"ok": True, "zone": zone_id}
