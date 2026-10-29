"""The agent's tools, and the policy that sits between the model and the drone.

Every tool is deterministic code. The model chooses *which* tool to call and
*how to explain* the result; it cannot choose a dose, invent a yield, or fly.
Three rules are enforced here, in code, not in the prompt:

1. **No plan without evidence.** A zone can only be simulated after it has been
   inspected, and only a plan the twin produced (it has a ``plan_id``) can be
   put in a mission.
2. **Only the twin's pick, only if it is drone-deliverable, only if the
   observation passed its gate.** A zone flagged for human review is never
   treated, whatever the model argues.
3. **The model cannot actuate.** ``submit_proposal`` ends the run; the only
   path to the motors is a human pressing Approve, which re-runs the safety
   checks against fresh weather and a fresh preflight.

Violations come back to the model as tool errors, so it can correct course --
and they are logged, because an agent that keeps trying to break a rule is a
finding in itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from . import context, label, sop, twin
from .field import Field
from .mission import DroneSpec, Mission, build_mission
from .perception import Observation, Perception


@dataclass
class RunState:
    run_id: str
    field: Field
    images: dict[str, list[bytes]]
    today: date
    mode: dict
    observations: dict[str, Observation] = field(default_factory=dict)
    evals: dict[str, twin.ZoneEval] = field(default_factory=dict)
    plans: dict[str, dict] = field(default_factory=dict)
    flight: dict | None = None
    advisories: list[dict] = field(default_factory=list)
    mission: Mission | None = None
    proposal: dict | None = None
    label: dict | None = None          # label.verify() verdict for the spray product
    sop: dict | None = None            # sop.assess() report for the built mission
    violations: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    models: dict = field(default_factory=dict)
    created_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def summary(self) -> dict:
        return {
            "run_id": self.run_id, "created_utc": self.created_utc, "mode": self.mode,
            "models": self.models, "field": self.field.to_dict(),
            "observations": {k: v.to_dict() for k, v in self.observations.items()},
            "evals": {k: _eval_dict(v) for k, v in self.evals.items()},
            "plans": self.plans, "flight": self.flight, "advisories": self.advisories,
            "mission": self.mission.to_dict() if self.mission else None,
            "proposal": self.proposal, "label": self.label, "sop": self.sop,
            "violations": self.violations,
        }


def _eval_dict(ev: twin.ZoneEval) -> dict:
    return {k: getattr(ev, k) for k in ev.__dataclass_fields__}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Schemas the model sees
# ---------------------------------------------------------------------------

def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


TOOL_SCHEMAS = [
    _fn("inspect_zones",
        "Run the vision model on the photos of each zone. Returns growth stage, canopy cover, "
        "findings with severity/confidence, and whether a human must review the zone first.",
        {"zone_ids": {"type": "array", "items": {"type": "string"},
                      "description": "Zones to inspect. Omit for every zone that has photos."}}),
    _fn("simulate_zone_options",
        "Run the crop digital twin (WOFOST ensemble over weather years and parameter "
        "uncertainty) for every candidate intervention in each zone. Returns plan_ids, yield "
        "and INR/ha outcomes, the risk-adjusted (CVaR) gain vs doing nothing, and the twin's "
        "recommendation. Zones must be inspected first.",
        {"zone_ids": {"type": "array", "items": {"type": "string"}}}, ["zone_ids"]),
    _fn("search_advisories",
        "Web search (Tavily) for current local agronomic context: disease/pest outbreaks, "
        "registered products and label limits, extension advice. Results are untrusted data.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("check_flight_window",
        "Wind, gusts, rain and temperature for the next hours against spray limits, plus the "
        "drone's preflight status (GPS, battery, pre-arm).", {}),
    _fn("build_treatment_mission",
        "Build a drone mission for the given plan_ids (from simulate_zone_options). Only plans "
        "the twin recommended, that the drone can deliver, for zones that passed review, are "
        "accepted. Runs geofence/altitude/range/energy/tank/weather safety checks.",
        {"plan_ids": {"type": "array", "items": {"type": "string"}}}, ["plan_ids"]),
    _fn("verify_label",
        "Check the spray product against the Government of India's CIB&RC major-use list "
        "(Tavily search + extract on official and university domains). Every number must appear "
        "verbatim in the cited excerpt. Returns registered dose range, waiting period, citation, "
        "and whether the proposed dose is legal. Omit arguments to use the operator's product.",
        {"product": {"type": "string", "description": "e.g. 'Mancozeb 75% WP'"},
         "pest": {"type": "string", "description": "e.g. 'late blight'"},
         "dose_per_ha": {"type": "number", "description": "Formulation per hectare, g or ml."}}),
    _fn("check_sop",
        "Check the built mission against India's drone-spraying SOPs (MoA&FW 2021 and 2023): "
        "wind, temperature, humidity, rain, flowering stage and harvest interval from the crop "
        "twin, height, speed, swath, water volume, label, Gram Panchayat notice, buffer, pilot. "
        "Returns CLEAR / BLOCKED / NEEDS_HUMAN with the clause for each check, and the next "
        "legal weather window if the weather blocks. Call after build_treatment_mission.",
        {"start_utc": {"type": "string", "description": "Planned start, ISO 8601. Omit for now."}}),
    _fn("submit_proposal",
        "Finish: hand the operator a proposal. The operator, not you, decides whether the drone "
        "flies. Use mission_id null if no flight is proposed.",
        {"mission_id": {"type": ["string", "null"]},
         "headline": {"type": "string", "description": "One sentence, English."},
         "rationale": {"type": "string", "description": "3-5 sentences, English, citing only tool numbers."},
         "rationale_hi": {"type": "string", "description": "The same in simple Hindi for the farmer."},
         "zones_for_human_review": {"type": "array", "items": {"type": "string"}},
         "manual_actions": {"type": "array", "items": {"type": "string"},
                            "description": "Recommended actions the drone cannot do (e.g. irrigate)."}},
        ["headline", "rationale"]),
]


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

class Toolbox:
    def __init__(self, state: RunState, perception: Perception, drone_backend=None,
                 drone_spec: DroneSpec | None = None, tavily_key: str = "",
                 live_weather: bool = False, ensemble: tuple[int, int] = (5, 3)):
        self.s = state
        self.perception = perception
        self.drone = drone_backend
        self.spec = drone_spec or _project_spec()
        self.tavily_key = tavily_key
        self.live_weather = live_weather
        self.ensemble = ensemble
        self._base_weather = None
        self.label_llm = None      # optional text model that reads a label excerpt (verified after)
        self.registry: dict[str, Callable[..., Any]] = {
            "inspect_zones": self.inspect_zones,
            "simulate_zone_options": self.simulate_zone_options,
            "search_advisories": self.search_advisories,
            "check_flight_window": self.check_flight_window,
            "build_treatment_mission": self.build_treatment_mission,
            "verify_label": self.verify_label,
            "check_sop": self.check_sop,
            "submit_proposal": self.submit_proposal,
        }

    def call(self, name: str, args: dict) -> dict:
        if name not in self.registry:
            self._violation(name, args, f"unknown tool {name!r}")
            raise ToolError(f"unknown tool {name!r}; available: {sorted(self.registry)}")
        return self.registry[name](**(args or {}))

    def _violation(self, tool: str, args: dict, why: str) -> None:
        self.s.violations.append({"tool": tool, "args": args, "why": why,
                                  "t": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # 1 ---------------------------------------------------------------------
    def inspect_zones(self, zone_ids: list[str] | None = None) -> dict:
        zone_ids = zone_ids or sorted(self.s.images)
        out = []
        for z in zone_ids:
            if z not in self.s.images:
                out.append({"zone": z, "error": "no photos for this zone"})
                continue
            prev = self.s.observations.get(z)
            if prev is not None and prev.source.endswith("+ human"):
                o = prev                      # a human already settled this zone
            else:
                o = self.perception.observe(z, self.s.images[z])
            self.s.observations[z] = o
            p = o.primary()
            out.append({"zone": z, "crop": o.crop_detected, "bbch": o.bbch,
                        "canopy_cover_pct": o.canopy_cover_pct,
                        "canopy_cover_pixels_pct": o.canopy_cover_exg_pct,
                        "finding": p.get("label"), "category": p.get("category"),
                        "severity": p.get("severity"), "confidence": p.get("confidence"),
                        "evidence": p.get("evidence", ""),
                        "needs_human_review": o.needs_review, "review_reasons": o.review_reasons,
                        "gate": o.gate, "source": o.source})
        return {"zones": out, "zones_without_photos": [z for z in self.s.field.zone_ids()
                                                       if z not in self.s.images]}

    # 2 ---------------------------------------------------------------------
    def simulate_zone_options(self, zone_ids: list[str]) -> dict:
        missing = [z for z in zone_ids if z not in self.s.observations]
        if missing:
            self._violation("simulate_zone_options", {"zone_ids": zone_ids},
                            f"not inspected: {missing}")
            raise ToolError(f"zones {missing} have not been inspected; call inspect_zones first")
        f = self.s.field
        if self._base_weather is None:
            self._base_weather = twin.base_weather(f.latitude, f.longitude, self.s.today,
                                                   self.live_weather)
        cache: dict[tuple, twin.ZoneEval] = {}
        out = []
        for z in zone_ids:
            o = self.s.observations[z]
            p = o.primary()
            key = (p["category"], int(p["severity"]), int(o.bbch))
            if key not in cache:
                cache[key] = twin.evaluate(
                    f.crop, f.variety, f.latitude, f.longitude, self.s.today,
                    bbch=o.bbch, category=p["category"], severity=int(p["severity"]),
                    zone_id=z, base=self._base_weather, n_weather=self.ensemble[0],
                    n_params=self.ensemble[1])
            ev = _copy_eval(cache[key], z)
            self.s.evals[z] = ev
            opts = []
            for opt in ev.options:
                pid = f"P-{z}-{opt['code']}"
                self.s.plans[pid] = {"plan_id": pid, "zone_id": z, **opt,
                                     "recommended": opt["code"] == ev.recommended}
                opts.append({"plan_id": pid, **{k: opt[k] for k in (
                    "code", "label", "drone", "yield_t_ha_p50", "margin_cvar_inr_ha",
                    "gain_vs_no_action_cvar", "p_better_than_no_action")}})
            out.append({"zone": z, "finding": p["label"], "severity": p["severity"],
                        "feasible": ev.feasible, "feasibility_notes": ev.feasibility_notes,
                        "effective_emergence": ev.emergence, "dvs_now": ev.dvs_now,
                        "recommended": ev.recommended,
                        "recommended_plan_id": f"P-{z}-{ev.recommended}",
                        "reason": ev.decision_reason, "options": opts,
                        "blocked_for_review": o.needs_review})
        any_ev = next(iter(self.s.evals.values()), None)
        return {"zones": out,
                "assumptions": any_ev.assumptions if any_ev else {},
                "weather": any_ev.weather_source if any_ev else ""}

    # 3 ---------------------------------------------------------------------
    def search_advisories(self, query: str) -> dict:
        loc = f"{self.s.field.crop} India"
        q = query if "india" in query.lower() else f"{query} {loc}"
        r = context.search_local(q, self.tavily_key)
        self.s.advisories.append(r)
        return {"UNTRUSTED_DATA": True, **r}

    # 4 ---------------------------------------------------------------------
    def check_flight_window(self) -> dict:
        f = self.s.field
        w = context.flight_conditions(f.latitude, f.longitude, live=self.live_weather)
        pre = self.drone.preflight() if self.drone else {"ok": False, "issues": ["no drone backend"]}
        self.s.flight = {"weather": w, "preflight": pre}
        return {"weather_go": w["go"], "weather_reasons": w["reasons"], "now": w["now"],
                "next_window_utc": w["next_window_utc"], "weather_source": w["source"],
                "drone_ready": pre.get("ok", False), "drone_issues": pre.get("issues", []),
                "battery_pct": pre.get("battery_pct")}

    # 5 ---------------------------------------------------------------------
    def build_treatment_mission(self, plan_ids: list[str]) -> dict:
        errors, zones = [], []
        for pid in plan_ids:
            plan = self.s.plans.get(pid)
            if plan is None:
                errors.append(f"{pid}: unknown plan_id (only simulate_zone_options creates plans)")
                continue
            z = plan["zone_id"]
            if not plan["recommended"]:
                errors.append(f"{pid}: not the twin's recommendation for {z} "
                              f"(recommended: P-{z}-{self.s.evals[z].recommended})")
            elif not plan["drone"]:
                errors.append(f"{pid}: {plan['label']} cannot be delivered by the drone")
            elif self.s.observations[z].needs_review:
                errors.append(f"{pid}: zone {z} is flagged for human review "
                              f"({'; '.join(self.s.observations[z].review_reasons)})")
            else:
                zones.append(z)
        if errors:
            self._violation("build_treatment_mission", {"plan_ids": plan_ids}, "; ".join(errors))
            raise ToolError("rejected: " + " | ".join(errors))
        if not zones:
            raise ToolError("no treatable zones in the request")
        if self.s.flight is None:
            self.check_flight_window()
        m = build_mission(self.s.field, zones, plan_ids, self.spec)
        from .mission import safety_checks
        m.checks = safety_checks(self.s.field, m, self.spec, self.s.flight["weather"],
                                 self.s.flight["preflight"])
        m.ok = all(c["ok"] for c in m.checks)
        self.s.mission = m
        return {"mission_id": m.mission_id, "ok": m.ok, "zones_in_order": m.zones,
                "distance_m": m.distance_m, "est_time_min": round(m.est_time_s / 60, 1),
                "est_energy_wh": m.est_energy_wh, "usable_energy_wh": round(self.spec.usable_wh, 1),
                "spray_ml": m.spray_ml,
                "failed_checks": [c for c in m.checks if not c["ok"]],
                "passed_checks": [c["check"] for c in m.checks if c["ok"]]}

    # 6 ---------------------------------------------------------------------
    def verify_label(self, product: str = "", pest: str = "",
                     dose_per_ha: float | None = None) -> dict:
        c = self.s.field.compliance
        product = product or c.get("product", "")
        pest = pest or c.get("pest", "")
        if dose_per_ha is None:
            dose_per_ha = c.get("dose_per_ha")
        if not product or not pest:
            raise ToolError("no product/pest given and none on the operator's record "
                            "(field compliance.product / compliance.pest)")
        v = label.verify(product, self.s.field.crop, pest,
                         float(dose_per_ha) if dose_per_ha is not None else None,
                         tavily_key=self.tavily_key or None, llm=self.label_llm)
        d = v.to_dict()
        self.s.label = d
        cl = v.claim
        return {"UNTRUSTED_SOURCE_TEXT_VERIFIED": True, "ok": v.ok, "reason": v.reason,
                "product": product, "crop": self.s.field.crop, "pest": pest,
                "proposed_per_ha": dose_per_ha,
                "registered_per_ha": list(cl.formulation_per_ha) if cl else None,
                "unit": cl.unit if cl else None,
                "waiting_days": cl.waiting_days if cl else None,
                "parsed_by": cl.parsed_by if cl else None,
                "citations": v.citations}

    # 7 ---------------------------------------------------------------------
    def check_sop(self, start_utc: str | None = None) -> dict:
        if self.s.mission is None:
            self._violation("check_sop", {"start_utc": start_utc}, "no mission")
            raise ToolError("build_treatment_mission first; the SOP is checked against a mission")
        when = None
        if start_utc:
            try:
                when = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise ToolError(f"bad start_utc: {e}") from e
        rep = sop_report(self.s, self.spec, live=self.live_weather, when=when)
        self.s.sop = rep
        return {"verdict": rep["verdict"], "headline": rep["headline"],
                "failed": [f"{c['rule']}: {c['value']} vs {c['limit']} ({c['source']})"
                           for c in rep["checks"] if c["hard"] and c["ok"] is False],
                "unverified": [f"{c['rule']}: {c['value']}" for c in rep["checks"]
                               if c["hard"] and c["ok"] is None],
                "advisory": [f"{c['rule']}: {c['value']} vs {c['limit']}" for c in rep["checks"]
                             if not c["hard"] and c["ok"] is False],
                "earliest_clear_utc": rep["earliest_clear"],
                "forecast": rep["forecast_source"],
                "note": "BLOCKED cannot be overridden. NEEDS_HUMAN: the operator can clear it with "
                        "a written reason; you cannot."}

    # 8 ---------------------------------------------------------------------
    def submit_proposal(self, headline: str, rationale: str, mission_id: str | None = None,
                        rationale_hi: str = "", zones_for_human_review: list[str] | None = None,
                        manual_actions: list[str] | None = None) -> dict:
        if isinstance(mission_id, str) and mission_id.strip().lower() in ("none", "null", ""):
            mission_id = None                 # models often write JSON null as the string "None"
        if mission_id and (self.s.mission is None or self.s.mission.mission_id != mission_id):
            self._violation("submit_proposal", {"mission_id": mission_id}, "unknown mission_id")
            raise ToolError(f"unknown mission_id {mission_id!r}; build the mission first "
                            "or submit with mission_id null")
        if mission_id and self.s.sop is None:
            self._violation("submit_proposal", {"mission_id": mission_id}, "SOP not checked")
            raise ToolError("call check_sop before proposing a flight")
        review = sorted(set(zones_for_human_review or []) |
                        {z for z, o in self.s.observations.items() if o.needs_review})
        self.s.proposal = {
            "mission_id": mission_id if mission_id else None,
            "mission_ok": bool(self.s.mission and self.s.mission.ok) if mission_id else None,
            "headline": headline.strip()[:240], "rationale": rationale.strip()[:2000],
            "rationale_hi": (rationale_hi or "").strip()[:2000],
            "zones_for_human_review": review,
            "manual_actions": manual_actions or [],
            "sop_verdict": self.s.sop["verdict"] if (mission_id and self.s.sop) else None,
            "sop_headline": self.s.sop["headline"] if (mission_id and self.s.sop) else None,
            "label": self.s.label,
            "status": "awaiting_approval" if mission_id else "advice_only",
            "submitted_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return {"accepted": True, "status": self.s.proposal["status"],
                "note": "The operator decides. You cannot start the drone."}


def _copy_eval(ev: twin.ZoneEval, zone_id: str) -> twin.ZoneEval:
    import copy
    e = copy.deepcopy(ev)
    e.zone_id = zone_id
    return e


def to_json(obj: Any, limit: int = 7000) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + ' ... [truncated]"'


# ---------------------------------------------------------------------------
# SOP assessment of a built mission (used by the tool and again at approval)
# ---------------------------------------------------------------------------

CANOPY_HEIGHT_M = {"potato": 0.5, "wheat": 0.9, "rice": 0.9, "maize": 2.0, "cotton": 1.2,
                   "sugarcane": 2.5, "soybean": 0.7, "chickpea": 0.5, "mustard": 1.4}


def offline_forecast(now: datetime, hours: int = 72, wind_ms: float | None = None) -> list:
    """Synthetic calm hours for offline runs -- labelled as such everywhere it appears.

    ``wind_ms`` (or env FIELDPILOT_OFFLINE_WIND) raises the wind for the first 4 h so the
    offline demo can show a weather refusal and the next legal window.
    """
    import math
    import os
    w = wind_ms if wind_ms is not None else float(os.environ.get("FIELDPILOT_OFFLINE_WIND", "0") or 0)
    t0 = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    from datetime import timedelta
    out = []
    for h in range(hours):
        base = 2.0 + 0.5 * math.sin(h / 3)
        out.append(sop.Hour(t0 + timedelta(hours=h), w if (w and h < 4) else base,
                            29.0 + 3 * math.sin(h / 4), 62.0, 0.0, 5.0))
    return out


def sop_report(s: RunState, spec: DroneSpec, live: bool, when: datetime | None = None,
               forecast: list | None = None) -> dict:
    f, m, c = s.field, s.mission, s.field.compliance
    now = datetime.now(timezone.utc)
    when = when or now
    src = "supplied"
    if forecast is None:
        if live:
            try:
                forecast = sop.fetch_forecast(f.latitude, f.longitude)
                src = "open-meteo forecast"
            except Exception as e:  # noqa: BLE001
                forecast, src = [], f"forecast unavailable ({type(e).__name__})"
        else:
            forecast, src = offline_forecast(now), "OFFLINE (synthetic, NOT a forecast)"
    zones = m.zones if m else []
    evs = [s.evals[z] for z in zones if z in s.evals]
    dvs = max((e.dvs_now for e in evs), default=None)
    mats = [e.maturity for e in evs if e.maturity]
    maturity = (datetime.fromisoformat(min(mats)).replace(hour=6, tzinfo=timezone.utc)
                if mats else None)
    lab = s.label or {}
    claim = lab.get("claim") or {}
    waiting = claim.get("waiting_days")
    if waiting is None:
        waiting = c.get("waiting_days")
    notice = c.get("notice_sent_utc")
    notice_dt = None
    if notice:
        notice_dt = datetime.fromisoformat(str(notice).replace("Z", "+00:00"))
        if notice_dt.tzinfo is None:
            notice_dt = notice_dt.replace(tzinfo=timezone.utc)
    canopy = CANOPY_HEIGHT_M.get(f.crop.lower(), 0.8)
    plan = sop.Plan(
        crop=f.crop, when=when, duration_min=max(1.0, (m.est_time_s if m else 60) / 60),
        dvs=dvs, maturity=maturity,
        height_m=round(f.treat_alt_m - canopy, 2), speed_ms=spec.spray_speed_ms,
        swath_m=spec.swath_m, volume_l_ha=spec.spray_rate_l_ha,
        waiting_days=float(waiting) if waiting is not None else None,
        label_ok=lab.get("ok") if lab else None, label_note=lab.get("reason", "") if lab else "",
        notice_sent=notice_dt, pilot_dgca=c.get("pilot_dgca"),
        buffer_m=c.get("buffer_m"), forecast=forecast)
    rep = sop.assess(plan, now=now)
    d = rep.to_dict()
    d["headline"] = rep.headline()
    d["forecast_source"] = src
    d["planned_start_utc"] = when.isoformat(timespec="minutes")
    d["canopy_height_m_assumed"] = canopy
    return d


def _project_spec():
    """The project airframe (T1 10 L quad) unless a spec is passed."""
    from fieldpilot.mission import drone_spec as _ds
    return _ds(None)
