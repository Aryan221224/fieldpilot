"""Booking intake: the model reads the language, code checks the numbers."""
import json

import pytest

from fieldpilot.intake import area_in_message, parse_booking, to_hectares


class FakeLLM:
    agent_model = "nvidia/nemotron-3-super-120b-a12b"

    def __init__(self, answer: dict):
        self.answer = answer
        self.seen = None

    def text(self, messages, **_):
        self.seen = messages
        return json.dumps(self.answer, ensure_ascii=False)


def geo(name):
    return [{"name": "Dankaur", "lat": 28.351, "lon": 77.555, "admin1": "Uttar Pradesh",
             "admin2": "Gautam Buddh Nagar"},
            {"name": "Dankaur", "lat": 26.9, "lon": 80.9, "admin1": "Uttar Pradesh", "admin2": "x"}]


MSG = "Didi namaste, main Ramesh, Dankaur se. Dhai bigha aalu mein jhulsa lag gaya hai, kal spray kar dena"


def test_hinglish_booking_becomes_a_job():
    llm = FakeLLM({"farmer": "Ramesh", "village": "Dankaur", "crop": "potato",
                   "problem": "late blight", "area_value": 2.5, "area_unit": "bigha",
                   "area_quote": "Dhai bigha", "preferred_day": "tomorrow", "language": "mixed"})
    j = parse_booking(MSG, llm, base=(28.45, 77.58), geocoder=geo)
    assert j.crop == "potato" and j.area_ha == pytest.approx(2.5 * 2529 / 10_000, abs=1e-3)
    assert (j.lat, j.lon) == (28.351, 77.555)                   # nearest match to the base
    assert any("bigha" in c for c in j.confirm)
    assert j.model.startswith("nvidia/")


def test_area_the_model_made_up_is_not_used():
    llm = FakeLLM({"farmer": "Ramesh", "village": "Dankaur", "crop": "potato",
                   "problem": "late blight", "area_value": 4, "area_unit": "acre"})
    j = parse_booking(MSG, llm, base=(28.45, 77.58), geocoder=geo)
    assert j.area_ha is None
    assert any("not written in the message" in c for c in j.confirm)


def test_phone_number_must_be_in_the_message():
    llm = FakeLLM({"farmer": "Ramesh", "phone": "9876543210", "village": "Dankaur",
                   "crop": "potato", "area_value": 2.5, "area_unit": "bigha"})
    j = parse_booking(MSG, llm, geocoder=geo)
    assert j.phone is None


def test_number_words_and_devanagari():
    assert area_in_message("ढाई बीघा आलू", 2.5, None)
    assert area_in_message("३ एकड़ गेहूं", 3, None)
    assert area_in_message("do acre sarson", 2, "do acre")
    assert not area_in_message("thoda sa khet", 2, None)


def test_units():
    assert to_hectares(1, "acre") == pytest.approx(0.4047, abs=1e-4)
    assert to_hectares(8, "kanal") == pytest.approx(0.4047, abs=1e-3)
    assert to_hectares(1, "bigha", bigha_m2=1012) == pytest.approx(0.1012)
    with pytest.raises(ValueError):
        to_hectares(1, "killa")


def test_code_reads_an_area_the_model_missed():
    from fieldpilot.intake import area_from_text, norm_unit
    assert area_from_text("झाझर से गीता। ढाई बीघा आलू, पत्ते सूख रहे हैं।") == (2.5, "bigha")
    assert area_from_text("sitio 3,5 alqueires soja") == (3.5, "alqueire")
    assert area_from_text("2 bigha here and 3 acre there") is None      # ambiguous: ask
    assert area_from_text("aalu mein rog hai") is None                   # nothing written
    assert norm_unit("बीघा") == "bigha" and norm_unit("Acres") == "acre" and norm_unit("xyz") is None
