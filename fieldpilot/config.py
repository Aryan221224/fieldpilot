"""Runtime configuration, read once from the environment.

Every external dependency has an offline fallback so the whole loop runs on a
laptop with no keys and no drone. Offline pieces are *labelled* everywhere they
surface -- a stubbed answer that looks like a real one is a trap, so the UI and
every trace event carry the mode they were produced in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import ROOT


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no interpolation, env wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():   # Notepad may add a BOM
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# FP_ENV_FILE picks another env file, e.g. .env.local for the lab PC's own GPU.
_load_dotenv(ROOT / os.environ.get("FP_ENV_FILE", ".env"))


def _is_nebius(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("nebius.com") or host.endswith("nebius.cloud") or host.endswith("nebius.ai")


def _is_local(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.startswith("192.168.")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "")
    return v if v != "" else default


@dataclass(frozen=True)
class Config:
    nebius_api_key: str = field(default_factory=lambda: _env("NEBIUS_API_KEY"))
    nebius_base_url: str = field(default_factory=lambda: _env(
        "NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"))
    agent_model: str = field(default_factory=lambda: _env("FP_AGENT_MODEL"))
    fast_model: str = field(default_factory=lambda: _env("FP_FAST_MODEL"))
    vision_model: str = field(default_factory=lambda: _env("FP_VISION_MODEL"))
    vision_base_url: str = field(default_factory=lambda: _env("FP_VISION_BASE_URL"))
    vision_api_key: str = field(default_factory=lambda: _env("FP_VISION_API_KEY"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))

    drone: str = field(default_factory=lambda: _env("FP_DRONE", "sim"))
    mavlink_url: str = field(default_factory=lambda: _env("FP_MAVLINK_URL", "udpin:0.0.0.0:14550"))
    payload_servo: int = field(default_factory=lambda: int(_env("FP_PAYLOAD_SERVO", "9")))
    payload_pwm_on: int = field(default_factory=lambda: int(_env("FP_PAYLOAD_PWM_ON", "1900")))
    payload_pwm_off: int = field(default_factory=lambda: int(_env("FP_PAYLOAD_PWM_OFF", "1100")))
    # servo: DO_SET_SERVO on FP_PAYLOAD_SERVO (relay / bench). sprayer: DO_SPRAYER, for an
    # ArduPilot FC with SERVOn_FUNCTION=22 (speed-proportional flow, cut below 0.5 m/s).
    spray_cmd: str = field(default_factory=lambda: _env("FP_SPRAY_CMD", "servo"))

    field_path: Path = field(default_factory=lambda: ROOT / _env("FP_FIELD", "examples/field_demo.json"))
    crop: str = field(default_factory=lambda: _env("FP_CROP", "potato"))
    variety: str = field(default_factory=lambda: _env("FP_VARIETY", "Potato_701"))
    runs_dir: Path = field(default_factory=lambda: ROOT / _env("FP_RUNS_DIR", "runs"))
    vendor_dir: Path = ROOT / "vendor"

    @property
    def live_llm(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def live_search(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def rehearsal(self) -> bool:
        """Running against the local Token Factory stand-in (fieldpilot/rehearsal.py)."""
        return _env("FP_REHEARSAL") == "1"

    @property
    def on_nebius(self) -> bool:
        """True only when the agent really calls Nebius Token Factory.

        A local OpenAI-compatible server (Ollama on the lab PC's GPU) is useful
        for free iteration, but a run made there is not a Nebius run and must
        never be labelled or counted as one.
        """
        return self.live_llm and not self.rehearsal and _is_nebius(self.nebius_base_url)

    def mode(self) -> dict[str, str]:
        """What is real and what is stubbed, for the UI badge and every trace."""
        if not self.live_llm:
            llm = "OFFLINE-STUB"
        elif self.rehearsal:
            llm = "REHEARSAL (mock Token Factory, not Nebius)"
        elif self.on_nebius:
            llm = "nebius-token-factory"
        else:
            llm = "LOCAL-GPU (not Nebius): " + self.nebius_base_url
        if self.vision_base_url:
            vision = ("LOCAL-GPU (not Nebius): " + self.vision_base_url
                      if _is_local(self.vision_base_url) else "nebius-serverless-endpoint")
        else:
            vision = llm if self.live_llm else "OFFLINE-STUB"
        return {
            "llm": llm,
            "vision": vision,
            "search": "tavily" if self.live_search else "OFFLINE-CACHE",
            "drone": self.drone,
        }


def load() -> Config:
    return Config()
