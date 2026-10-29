"""Emulated spray hardware: the pump_bridge firmware and the T1's pump and nozzles, in software.

Used by the rehearsal to predict what the bench rig will do, so the real bench run can be
compared against it (``cli deviation``). It speaks the firmware's serial protocol exactly
(``?`` / ``P<pwm>`` -> ``OK <pwm>``, 1.5 s dead-man, FLOW mode duty mapping), so
``fieldpilot.bench`` drives it through the same ``SerialSink`` code as the real Arduino.

Hydraulic model (the T1's pump and four nozzles)
------------------------------------------------
* Pump: 24 V DC diaphragm, 5.5 L/min open flow, 0.8 MPa shut-off (BOM). Treated as a
  linear pump curve whose flow scales with motor speed and pressure with speed squared.
* Nozzles: four, flow proportional to sqrt(pressure). Their constant is set so that at full
  speed the operating point is the sheet's 3.5 L/min system flow - which puts the nozzles
  at 0.29 MPa (2.9 bar), a normal agricultural nozzle pressure.
* By the affinity laws the operating point then scales linearly with motor speed, so
  system flow = 3.5 L/min x motor speed.
* Motor speed from PWM duty: nothing below the stall duty (the motor cannot overcome the
  diaphragm), then linear to full speed at 100 %.
* Dynamics: a dead time (pump to nozzle) and a first-order spin-up/spin-down lag.

The three uncertain parameters (stall duty, lag, dead time) are stated with ranges; the
rehearsal reports the prediction across those ranges, and ``cli deviation`` fits them from
two measurements on the real pump (a jug at full duty and at the spray-speed duty).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

OFF_US, FULL_US, DEADMAN_S = 1100, 1900, 1.5


@dataclass
class PumpParams:
    q_full_l_min: float = 3.5        # sheet: system flow through four nozzles
    open_flow_l_min: float = 5.5     # BOM pump, free flow
    shutoff_mpa: float = 0.8         # BOM pump, maximum pressure
    stall_duty: float = 0.20         # assumption, range 0.12-0.30
    tau_s: float = 0.25              # assumption, range 0.15-0.40
    dead_s: float = 0.15             # assumption, range 0.05-0.30

    @property
    def nozzle_mpa(self) -> float:
        """Nozzle pressure at full speed, where pump curve meets the nozzle curve."""
        return self.shutoff_mpa * (1 - self.q_full_l_min / self.open_flow_l_min)

    def steady_flow(self, duty: float) -> float:
        if duty <= self.stall_duty:
            return 0.0
        return self.q_full_l_min * min(1.0, (duty - self.stall_duty) / (1 - self.stall_duty))


RANGES = {"stall_duty": (0.12, 0.30), "tau_s": (0.15, 0.40), "dead_s": (0.05, 0.30)}


def duty_of(us: int) -> float:
    if us <= OFF_US:
        return 0.0
    if us >= FULL_US:
        return 1.0
    return (us - OFF_US) / (FULL_US - OFF_US)


class PumpBridgeEmulator:
    """Serial-port stand-in for firmware/pump_bridge (FLOW mode) driving the T1's pump."""

    def __init__(self, params: PumpParams | None = None, clock=time.time, step_s: float = 0.02):
        self.p = params or PumpParams()
        self.clock, self.step_s = clock, step_s
        self._out: list[bytes] = []
        self._buf = b""
        self.t = clock()
        self.cmds: list[tuple[float, float]] = [(self.t, 0.0)]     # (time, duty)
        self.last_cmd = None
        self.q = 0.0
        self.volume_l = 0.0
        self.trace: list[tuple[float, float, float, float]] = []   # (t, duty, flow, volume)

    # -- serial API used by fieldpilot.bench.SerialSink -------------------------
    def reset_input_buffer(self) -> None:
        self._out.clear()

    def write(self, data: bytes) -> int:
        self._advance()
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._handle(line.decode("ascii", "replace").strip())
        return len(data)

    def readline(self) -> bytes:
        return self._out.pop(0) if self._out else b""

    def close(self) -> None:
        self._advance()

    # -- firmware ----------------------------------------------------------------
    def _handle(self, line: str) -> None:
        if line == "?":
            self._out.append(b"PUMP_BRIDGE 1 mode=flow pin=5 deadman_ms=1500 (EMULATED)\n")
            return
        if line.startswith("P"):
            try:
                v = int(line[1:])
            except ValueError:
                v = -1
            if 800 <= v <= 2200:
                now = self.clock()
                self.cmds.append((now, duty_of(v)))
                self.last_cmd = now
                self._out.append(f"OK {v}\n".encode())
                return
        self._out.append(f"ERR {line}\n".encode())

    def _duty_at(self, t: float) -> float:
        d = 0.0
        for tc, dc in self.cmds:
            if tc <= t:
                d = dc
            else:
                break
        last = max((tc for tc, _ in self.cmds if tc <= t), default=None)
        if self.last_cmd is not None and last is not None and t - last > DEADMAN_S:
            return 0.0                                  # dead-man: no command for 1.5 s
        return d

    def _advance(self) -> None:
        now = self.clock()
        while self.t + self.step_s <= now:
            self.t += self.step_s
            d = self._duty_at(self.t - self.p.dead_s)
            target = self.p.steady_flow(d)
            self.q += (target - self.q) * (1 - math.exp(-self.step_s / self.p.tau_s))
            self.volume_l += self.q / 60.0 * self.step_s
            self.trace.append((round(self.t, 3), round(d, 3), round(self.q, 4), round(self.volume_l, 5)))

    def finish(self, extra_s: float = 2.0) -> None:
        """Run the model on past the last command so the tail of the flow is counted."""
        end = self.clock() + extra_s
        while self.t + self.step_s <= end:
            self.t += self.step_s
            d = self._duty_at(self.t - self.p.dead_s)
            target = self.p.steady_flow(d)
            self.q += (target - self.q) * (1 - math.exp(-self.step_s / self.p.tau_s))
            self.volume_l += self.q / 60.0 * self.step_s
            self.trace.append((round(self.t, 3), round(d, 3), round(self.q, 4), round(self.volume_l, 5)))

    def summary(self) -> dict:
        flowing = [x for x in self.trace if x[2] > 0.05]
        return {"litres": round(self.volume_l, 3),
                "seconds_flowing": round(len(flowing) * self.step_s, 2),
                "peak_l_min": round(max((x[2] for x in self.trace), default=0.0), 3),
                "nozzle_mpa_at_full": round(self.p.nozzle_mpa, 3),
                "params": self.p.__dict__}


def replay(commands: list[tuple[float, int]], params: PumpParams, end_s: float) -> float:
    """Litres the pump delivers for a PWM command list (t from 0) - used to fit and to bound."""
    clk = [0.0]
    emu = PumpBridgeEmulator(params, clock=lambda: clk[0])
    for t, us in commands:
        clk[0] = t
        emu.write(f"P{us}\n".encode())
        emu.readline()
    clk[0] = end_s
    emu.close()
    return emu.volume_l


def bounds(commands: list[tuple[float, int]], end_s: float, base: PumpParams | None = None) -> dict:
    """Prediction across the stated parameter ranges (every corner of the box)."""
    base = base or PumpParams()
    vals = []
    import itertools
    for sd, tau, dead in itertools.product(*RANGES.values()):
        p = PumpParams(**{**base.__dict__, "stall_duty": sd, "tau_s": tau, "dead_s": dead})
        vals.append(replay(commands, p, end_s))
    return {"litres_min": round(min(vals), 3), "litres_max": round(max(vals), 3),
            "litres_nominal": round(replay(commands, base, end_s), 3)}
