"""Drone backends: a kinematic simulator, a MAVLink (PX4 / ArduPilot) link, and a hand-off
backend for closed agricultural controllers (the T1's JIYI K3-A Pro)."""
from __future__ import annotations

from .base import DroneBackend, Telemetry


def make_backend(kind: str, **kw) -> DroneBackend:
    if kind == "k3":
        from .k3 import K3HandoffBackend
        return K3HandoffBackend(**kw)
    if kind == "mavlink":
        from .mavlink import MavlinkBackend
        return MavlinkBackend(**kw)
    from .sim import SimBackend
    return SimBackend(**kw)


__all__ = ["DroneBackend", "Telemetry", "make_backend"]
