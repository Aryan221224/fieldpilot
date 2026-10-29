"""Hand-off backend for a closed agricultural flight controller (the T1's JIYI K3-A Pro).

The K3-A Pro is flown from JIYI's own app and has no open command link, so this backend
never moves the aircraft. Approve still does everything it does for a MAVLink vehicle -
fresh weather, the rule pack, the safety re-check, one approval per sortie - and then
*issues* the sortie: a flight card and a KML the pilot loads into the JIYI app. The pilot
flies it, holding the remote; one-key return on the remote is the abort.

    FP_DRONE=k3 python -m uvicorn fieldpilot.api:app --port 8080
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from .base import DroneBackend, Telemetry

PILOT_CHECKLIST = [
    "JIYI app shows GPS lock (RTK fixed if fitted) and no red warnings",
    "Fresh pack fitted and reads >= 95 %; spare on charge",
    "Tank loaded with exactly the water on the card",
    "Props unfolded and locked; nobody inside the boundary",
    "Remote in hand; one-key return checked",
]


class K3HandoffBackend(DroneBackend):
    name = "k3"

    def __init__(self, spec=None, field=None, out_dir: str | Path = "runs", **_):
        from ..mission import drone_spec
        self.spec = spec or drone_spec(None)
        self.field = field
        self.out = Path(out_dir)
        self.t = Telemetry(backend="k3", mode="PILOT (JIYI app)",
                           message="K3-A Pro: closed controller, flown by the pilot from a FieldPilot card")
        self.issued: list[dict] = []
        self._pending = None
        self.drift = None          # (moved field, info) set at approval when the wind is measured

    def connect(self) -> Telemetry:
        return self.t

    def preflight(self) -> dict:
        # FieldPilot cannot read this aircraft. The pilot's checklist is the preflight; it is
        # printed on the card and must be ticked before take-off. Nothing here can pass a
        # weather, rule or safety check on the aircraft's behalf - those ran already.
        return {"ok": True, "issues": [], "source": "pilot checklist (K3-A Pro has no open link)",
                "pilot_checklist": PILOT_CHECKLIST}

    def upload(self, mission) -> None:
        self._pending = mission
        self.t.state = "uploaded"

    def start(self) -> None:
        m, self._pending = self._pending, None
        if m is None:
            raise RuntimeError("nothing to issue")
        rec = {"mission_id": m.mission_id, "utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if self.field is not None:
            from ..k3card import card_html, flight_card, mission_kml
            self.out.mkdir(parents=True, exist_ok=True)
            fld, info = getattr(self, "drift", None) or (self.field, None)
            card = flight_card(fld, m, self.spec, drift=info)
            card["pilot_checklist"] = PILOT_CHECKLIST
            html_p = self.out / f"card_{m.mission_id}.html"
            kml_p = self.out / f"card_{m.mission_id}.kml"
            html_p.write_text(card_html(card), encoding="utf-8")
            kml_p.write_text(mission_kml(fld, m), encoding="utf-8")
            rec |= {"card": str(html_p), "kml": str(kml_p)}
        self.issued.append(rec)
        self.t.state = "handed_to_pilot"
        self.t.mission_total = len(m.items)
        self.t.message = f"sortie {m.mission_id} issued to the pilot ({time.strftime('%H:%M')})"

    def abort(self) -> None:
        self.t.state = "aborted"
        self.t.message = "ABORT: tell the pilot - one-key return on the JIYI remote"

    def telemetry(self) -> Telemetry:
        return self.t
