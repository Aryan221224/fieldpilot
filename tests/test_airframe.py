"""The project airframe (T1 10 L quad, JIYI K3-A Pro): the profile, its checks, the hand-off."""
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import date

import pytest

from fieldpilot import airframe
from fieldpilot.bench import BenchLog, Bridge, DrySink, flow_pwm
from fieldpilot.drone import make_backend
from fieldpilot.field import grid_field
from fieldpilot.k3card import card_html, flight_card, mission_kml
from fieldpilot.mission import build_mission, drone_spec


@pytest.fixture(scope="module")
def t1():
    return drone_spec("t1_10l")


@pytest.fixture(scope="module")
def hectare():
    return grid_field("T1 test", sw_corner=(28.45, 77.58), width_m=100, height_m=100, cols=2, rows=2,
                      crop="potato", variety="Potato_701", sowing_date=date(2026, 10, 20), max_alt_m=12)


def test_default_airframe_is_the_t1(t1):
    assert drone_spec(None).name == t1.name and t1.tank_ml == 10_000 and t1.nozzles == 4
    assert t1.swath_m == 4.0 and t1.pump_max_l_min == 3.5 and t1.mtow_kg == 28.0


def test_profile_reproduces_the_spec_sheet():
    # one number fitted (12 min at 24 kg); the other hover figure and the work rate are predictions
    assert airframe.predicted_hover_min(24.0) == pytest.approx(12.0)
    assert airframe.predicted_hover_min(14.0) == pytest.approx(28.0, rel=0.06)
    w = airframe.work_rate()
    # With braking and turning at every line end (the K3 twin's kinematics) the planner lands
    # below the brochure's 10 acres/h, which counts no line-end time; within 20 % of it.
    assert 0.8 * airframe.SHEET["work_rate_acres_h"] <= w["acres_per_h"] <= airframe.SHEET["work_rate_acres_h"]


def test_lighter_aircraft_uses_less_power(t1):
    assert t1.hover_w(0) < t1.hover_w(5000) < t1.hover_w(10_000) == pytest.approx(t1.hover_power_w)


def test_kit_as_shipped_is_charger_bound():
    k = airframe.kit_rate()
    assert k["ha_per_h"] < k["aircraft_only_ha_per_h"] / 3       # the aircraft mostly waits
    more = airframe.kit_rate(battery_sets=4, chargers=2)
    assert more["sorties"] > k["sorties"]


def test_pump_flow_and_speed_are_checked(hectare, t1):
    m = build_mission(hectare, hectare.zone_ids(), [], t1)
    names = {c["check"]: c for c in m.checks}
    assert names["pump_flow"]["ok"] and names["spray_speed"]["ok"] and names["takeoff_mass"]["ok"]
    assert "small" in names["takeoff_mass"]["detail"]
    fast = replace(t1, spray_speed_ms=8.0)                         # needs 3.84 L/min > 3.5
    bad = {c["check"]: c for c in build_mission(hectare, hectare.zone_ids(), [], fast).checks}
    assert not bad["pump_flow"]["ok"] and not bad["spray_speed"]["ok"]


def test_airframe_temperature_envelope(hectare, t1):
    from fieldpilot.mission import safety_checks
    m = build_mission(hectare, hectare.zone_ids(), [], t1)
    hot = {"go": True, "reasons": [], "now": {"temp_c": 43.0}}
    c = {x["check"]: x for x in safety_checks(hectare, m, t1, hot)}
    assert not c["airframe_temperature"]["ok"]


def test_ardupilot_sprayer_matches_the_sheet_law(t1):
    p = airframe.ardupilot_params(t1)
    assert p["SERVO9_FUNCTION"] == 22 and p["SPRAY_SPEED_MIN"] == 50
    # SPRAY_PUMP_RATE % per m/s x spray speed = the share of the 3.5 L/min pump needed
    assert p["SPRAY_PUMP_RATE"] / 100 * t1.spray_speed_ms * t1.pump_max_l_min == pytest.approx(
        t1.flow_l_min(), rel=0.01)


def test_flight_card_and_kml_for_the_k3(hectare, t1):
    m = build_mission(hectare, hectare.zone_ids(), [], t1)
    assert len(m.sorties) > 1                                     # 1 ha at 20 L/ha = 2 tanks
    card = flight_card(hectare, m, t1, approved_by="Sunita")
    s = card["app_settings"]
    assert s["line_spacing_m"] == 4.0 and s["dose_l_per_mu"] == pytest.approx(1.33, abs=0.01)
    assert sum(x["load_water_l"] for x in card["sorties"]) == pytest.approx(m.spray_ml / 1000, rel=0.01)
    assert card["sorties"][1]["starts_at"] is not None
    html = card_html(card)
    assert "L/mu" in html and "Sunita" in html and "GO" in html
    root = ET.fromstring(mission_kml(hectare, m).encode())
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    assert len(root.findall(".//k:Polygon", ns)) == len(m.zones)
    assert len(root.findall(".//k:LineString", ns)) == len(m.sorties)


def test_k3_backend_issues_cards_and_never_flies(hectare, t1, tmp_path):
    d = make_backend("k3", spec=t1, field=hectare, out_dir=tmp_path)
    d.connect()
    assert d.preflight()["ok"] and d.preflight()["pilot_checklist"]
    m = build_mission(hectare, hectare.zone_ids(), [], t1)
    d.upload(m)
    d.start()
    assert d.telemetry().state == "handed_to_pilot"
    assert (tmp_path / f"card_{m.mission_id}.html").exists() and (tmp_path / f"card_{m.mission_id}.kml").exists()
    d.abort()
    assert "one-key return" in d.telemetry().message


def test_bench_flow_law_follows_ground_speed(t1):
    assert flow_pwm(t1, 0.3) == 1100                             # below the 0.5 m/s cut-off
    assert 1100 < flow_pwm(t1, 2.0) < flow_pwm(t1, 4.5) < 1900
    assert flow_pwm(t1, 9.0) == 1900

    class Src:                                                   # mission says spray; speed varies
        def __init__(self):
            self.speeds, self.last_t, self.speed = [4.5, 4.5, 0.2, 0.2, 4.5], 0.0, None

        def read(self):
            if not self.speeds:
                return None
            self.speed = self.speeds.pop(0)
            return 1900

        def zone(self):
            return "Z1"

    sink = DrySink()
    log = BenchLog("s", "dry", 9, 1500)
    br = Bridge(Src(), sink, log, echo=lambda *_: None, flow_law=t1)
    for _ in range(5):
        br.step()
    assert [e.kind for e in log.events] == ["pump_on", "pump_off", "pump_on"]
    assert sink.history[0] == flow_pwm(t1, 4.5)


def test_console_source_reads_the_simulator_and_refuses_remote_or_real():
    from fieldpilot.bench import BenchRefused, ConsoleSource

    class Resp:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, seq):
            self.seq = list(seq)

        def get(self, url):
            return Resp(self.seq.pop(0) if len(self.seq) > 1 else self.seq[0])

    with pytest.raises(BenchRefused):
        ConsoleSource("http://192.168.1.4:8080", client=Client([{}]))
    src = ConsoleSource("http://127.0.0.1:8080", client=Client([
        {"backend": "sim", "spraying": True, "groundspeed_ms": 4.5, "zone_id": "A1"},
        {"backend": "sim", "spraying": False, "groundspeed_ms": 6.0, "zone_id": ""}]))
    assert src.read(0) == 1900 and src.speed == 4.5 and src.zone() == "A1"
    assert src.read(0) == 1100
    real = ConsoleSource("http://localhost:8080", client=Client([{"backend": "k3", "spraying": True}]))
    with pytest.raises(BenchRefused):
        real.read(0)


def test_simulator_slows_into_turns():
    from fieldpilot.drone.sim import SimBackend
    prof = SimBackend._profile(40.0, 4.5, 2.0)
    speeds = [s for _, s in prof]
    assert max(speeds) == pytest.approx(4.5) and speeds[0] < 1.0 and speeds[-1] == 0.0
    assert prof[-1][0] == 1.0 and all(b[0] >= a[0] for a, b in zip(prof, prof[1:]))
