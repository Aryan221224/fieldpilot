"""Firmware: the shared logic is unit-tested on the PC, matches the Python rules, and both
sketches compile against Arduino API stand-ins (syntax and types; flash with the ESP32 core)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fieldpilot.rules import delta_t, pack

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"
GPP = shutil.which("g++")
needs_gpp = pytest.mark.skipif(GPP is None, reason="g++ not installed")


def test_sketch_copies_of_the_logic_are_identical():
    common = (FW / "common" / "fieldpilot_logic.h").read_bytes()
    for s in ("fieldmast", "spraylog"):
        assert (FW / s / "src" / "fieldpilot_logic.h").read_bytes() == common, \
            f"copy firmware/common/fieldpilot_logic.h to firmware/{s}/src/"


def test_firmware_limits_match_the_rule_packs():
    h = (FW / "common" / "fieldpilot_logic.h").read_text()
    p = pack("IN-SOP-2023")
    assert f'{{"IN-SOP-2023", {p.wind_max_ms}f, 0.0f, {p.temp_max_c}f, {p.rh_min_pct}f, NAN, NAN}}' in h
    g = pack("GOOD-PRACTICE")
    assert (f'{{"GOOD-PRACTICE", {g.wind_max_ms}f, {g.wind_min_ms}f, NAN, NAN, '
            f'{g.delta_t_range[0]}f, {g.delta_t_range[1]}f}}') in h


@needs_gpp
def test_logic_unit_tests_pass_on_the_host(tmp_path):
    exe = tmp_path / "t"
    subprocess.run([GPP, "-std=c++11", "-Wall", "-Wextra", "-o", str(exe),
                    str(ROOT / "tests" / "firmware" / "test_logic.cpp"), "-lm"], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    # Delta T in C++ (float) equals rules.py (double) to 0.01
    out = subprocess.run([str(exe), "deltat"], capture_output=True, text=True, check=True).stdout
    for line in out.split("\n"):
        if line.strip():
            t, rh, dt = line.split()
            assert float(dt) == pytest.approx(delta_t(float(t), float(rh)), abs=0.01)


@needs_gpp
@pytest.mark.parametrize("sketch", ["fieldmast", "spraylog"])
def test_sketches_compile(sketch, tmp_path):
    stub = ROOT / "tests" / "firmware" / "arduino_stub"
    r = subprocess.run([GPP, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        "-I", str(stub), "-x", "c++", str(FW / sketch / f"{sketch}.ino"),
                        "-x", "c++", str(stub / "stub_main.cpp"), "-o", str(tmp_path / sketch), "-lm"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
