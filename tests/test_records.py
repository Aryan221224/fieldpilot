"""The operator's paperwork, written from the run."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fieldpilot import records, sop
from fieldpilot.records import Application, Drone, Operator, Site

IST = sop.IST
START = datetime(2026, 10, 14, 7, 10, tzinfo=IST)


@pytest.fixture
def run():
    site = Site(farmer="Ramesh Yadav", village="Dankaur", gram_panchayat="Dankaur",
                panchayat_samiti="Dankaur", district="Gautam Buddh Nagar", state="Uttar Pradesh",
                plot_ref="Khasra 214", lat=28.35121, lon=77.55233, area_ha=2.40)
    op = Operator("Sunita Devi", "DGCA/RPC/2025/01234", "+91 98xxxxxx10", "Shakti SHG (Namo Drone Didi)")
    drone = Drone("UA-0123456789", "Kisan-10L hexacopter", tank_l=10, nozzles=4)
    app = Application(crop="rice", pest="stem borer", product="Chlorantraniliprole 18.5% SC",
                      formulation_per_ha=150, unit="ml", volume_l_ha=25, zones=["Z3", "Z5"],
                      treated_ha=0.62, start=START, end=START + timedelta(minutes=14),
                      height_m=2.0, speed_ms=4.8, swath_m=5.0, waiting_days=47,
                      label_source="CIB&RC major uses, ppqs.gov.in",
                      weather={"temp_c": 27.4, "rh_pct": 71, "wind_ms": 1.6, "wind_dir_deg": 290},
                      blanket_ha=2.40, cost_saved_inr=2860)
    return site, op, drone, app


def test_harvest_date_is_spray_date_plus_waiting_period(run):
    assert records.harvest_from(run[3]) == date(2026, 11, 30)


def test_tank_mix_uses_the_drones_water_volume(run):
    assert run[3].per_tank(10) == pytest.approx(60.0)      # not 3 ml, as "label dilution" would give


def test_notice_has_every_field_sop_2021_asks_for(run):
    site, op, drone, app = run
    html = records.notice_html(site, op, drone, app, issued=START - timedelta(hours=30))
    for s in ["Gram Panchayat Dankaur", "Panchayat Samiti", "Agriculture Officer", "14 Oct 2026, 07:10 IST",
              "Khasra 214", "Chlorantraniliprole 18.5% SC", "150 ml/ha in 25 L water/ha", "UA-0123456789",
              "DGCA RPC DGCA/RPC/2025/01234", "100 m", "ड्रोन द्वारा कीटनाशक छिड़काव की पूर्व सूचना"]:
        assert s in html, s
    assert "Not valid as notice" not in html


def test_late_notice_says_it_is_not_valid(run):
    html = records.notice_html(*run, issued=START - timedelta(hours=6))
    assert "Not valid as notice" in html and "6 h before" in html


def test_record_carries_the_annexure_fields(run):
    site, op, drone, app = run
    plan = sop.Plan(crop="rice", when=START, duration_min=14, dvs=0.55)
    html = records.record_html(site, op, drone, app, sop.assess(plan))
    for s in ["60 ml per 10 L tank", "93 ml", "15.5 L", "27 °C", "RH 71 %", "wind 1.6 m/s",
              "height 2 m above canopy", "Goggles", "Overalls", "within seven days",
              "Pre-flight SOP checks"]:
        assert s in html, s


def test_farmer_slip_leads_with_the_harvest_date_in_hindi(run):
    html = records.farmer_slip_html(run[0], run[3])
    assert "कटाई इस तारीख से पहले न करें" in html and "30 November 2026" in html
    assert "0.62 में से 2.40 हेक्टेयर" in html and "74%" in html and "₹2,860" in html


def test_hindi_names_for_crop_and_pest(run):
    html = records.farmer_slip_html(run[0], run[3])
    assert "धान (rice)" in html and "तना छेदक (stem borer)" in html
    assert records.hi("dragonfruit") == "dragonfruit"


def test_unknown_waiting_period_sends_the_farmer_to_the_officer(run):
    run[3].waiting_days = None
    assert "ask the Agriculture Officer" in records.farmer_slip_html(run[0], run[3])


def test_text_is_escaped(run):
    run[0].farmer = "<script>x</script>"
    assert "<script>x</script>" not in records.farmer_slip_html(run[0], run[3])


def test_writes_html(run, tmp_path):
    p = records.write_html(records.farmer_slip_html(run[0], run[3]), tmp_path / "slip.html")
    assert p.read_text(encoding="utf-8").startswith("<!doctype html>")
