"""The contract every drone backend implements.

The agent never talks to a backend. Only the operator console's Approve
handler does, after the safety checks pass a second time. That separation is
the core safety property of the system, so it lives in the architecture rather
than in a prompt.
"""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field


@dataclass
class Telemetry:
    connected: bool = False
    armed: bool = False
    mode: str = "UNKNOWN"
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    battery_pct: float = -1.0
    gps_fix: int = 0
    satellites: int = 0
    groundspeed_ms: float = 0.0
    mission_seq: int = -1
    mission_total: int = 0
    spraying: bool = False
    zone_id: str = ""
    state: str = "idle"          # idle | uploaded | flying | returning | landed | aborted | error
    message: str = ""
    backend: str = ""
    trail: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trail"] = d["trail"][-400:]
        return d


class DroneBackend(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def connect(self) -> Telemetry: ...

    @abc.abstractmethod
    def preflight(self) -> dict:
        """{'ok': bool, 'issues': [str], ...} -- armability, GPS, battery."""

    @abc.abstractmethod
    def upload(self, mission) -> None: ...

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def abort(self) -> None:
        """Return to launch immediately."""

    @abc.abstractmethod
    def telemetry(self) -> Telemetry: ...
