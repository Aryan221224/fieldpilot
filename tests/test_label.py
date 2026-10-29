"""Label verification: Tavily finds the CIB&RC row, the parser or Nemotron reads it,
and a string match decides whether the reading is allowed to count."""
from __future__ import annotations

import json

import pytest

from fieldpilot import label

# The layout of the CIB&RC "Major uses" tables as published copies extract to text
# (rows from the TNAU copy; header verbatim).
MAJOR_USES = """INSECTICIDES
Chlorantraniliprole 18.5% SC
Crop | Common name of the pest | a.i (gm) | Formulation (gm/ml) | Dilution in Water (Litre) | Waiting Period (days)
Rice | Stem borer and leaf folder | 30 | 150 | 500 | 47
Cabbage | Diamond back Moth | 10 | 50 | 500 | 3
Cotton | American bollworm Spotted bollworm Tobacco caterpillar | 30 | 150 | 500 | 9
Emamectin Benzoate 5% SG
Crop | Common name of the pest | a.i (gm) | Formulation (gm/ml) | Dilution in Water (Litre) | Waiting Period (days)
Cotton | Boll worms | 9.5-11.0 | 190-220 | 500 | 10
Okra | Fruit & Shoot Borer | 6.75-8.5 | 135-170 | 500 | 5
Cabbage | DBM | 7.5-10 | 150-200 | 500 | 3
"""

OFFICIAL_URL = "https://ppqs.gov.in/sites/default/files/major_uses_insecticides.pdf"
MIRROR_URL = "https://agritech.tnau.ac.in/crop_protection/pdf/5_Major_use_insecticides.pdf"


class FakeTavily:
    def __init__(self, official=MAJOR_USES, mirror=MAJOR_USES, fail_official=False, crawl=""):
        self.pages = {OFFICIAL_URL: official, MIRROR_URL: mirror}
        self.fail_official = fail_official
        self.crawl = crawl
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if path == "search":
            off = "ppqs.gov.in" in payload["include_domains"]
            if off and self.fail_official:
                raise TimeoutError("tavily timed out")
            url = OFFICIAL_URL if off else MIRROR_URL
            if not self.pages[url]:
                return {"results": []}
            assert payload["include_domains_mode"] == "restrict"
            return {"results": [{"url": url, "content": "Major uses of pesticides"}]}
        if path == "extract":
            return {"results": [{"url": u, "raw_content": self.pages[u]} for u in payload["urls"]],
                    "usage": {"credits": 2}}
        if path == "crawl":
            assert payload["url"] == label.REGISTRY_CRAWL_ROOT and payload["allow_external"] is False
            assert payload["include_usage"] is True and "instructions" in payload
            res = [{"url": OFFICIAL_URL, "raw_content": self.crawl}] if self.crawl else []
            return {"results": res, "usage": {"credits": 4}}
        raise AssertionError(path)


def test_registered_dose_in_range_is_ok_and_cited():
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150, http=FakeTavily())
    assert v.ok is True
    c = v.claim
    assert c.formulation_per_ha == (150.0, 150.0) and c.unit == "ml"
    assert c.waiting_days == 47 and c.water_l_ha == 500
    assert c.authority == "official" and v.citations == [OFFICIAL_URL]
    assert "within the registered 150 ml/ha" in v.reason and "47 d" in v.reason


def test_overdose_is_refused():
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 300, http=FakeTavily())
    assert v.ok is False and "above the registered 150 ml/ha" in v.reason


def test_dose_ranges():
    t = FakeTavily()
    assert label.verify("Emamectin Benzoate 5% SG", "cotton", "bollworm", 200, http=t).ok is True
    v = label.verify("Emamectin Benzoate 5% SG", "cotton", "bollworm", 150, http=t)
    assert v.ok is False and "below" in v.reason and v.claim.unit == "g"


def test_rows_are_read_inside_their_own_product_section():
    v = label.verify("Chlorantraniliprole 18.5% SC", "cabbage", "diamondback moth", 50, http=FakeTavily())
    assert v.ok is True and v.claim.formulation_per_ha == (50.0, 50.0)     # not emamectin's 150-200
    w = label.verify("Emamectin Benzoate 5% SG", "cabbage", "diamondback moth", 175, http=FakeTavily())
    assert w.ok is True and w.claim.formulation_per_ha == (150.0, 200.0)   # "DBM" alias


def test_registered_for_the_crop_but_not_the_pest_needs_a_human():
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "brown planthopper", 150, http=FakeTavily())
    assert v.ok is None
    assert "listed for rice, but not against 'brown planthopper'" in v.reason


def test_falls_back_to_a_published_copy_and_says_so():
    t = FakeTavily(official="")
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150, http=t)
    assert v.ok is True and v.claim.authority == "mirror"
    assert "published copy of the CIB&RC list" in v.reason
    domains = [p["include_domains"] for path, p in t.calls if path == "search"]
    assert "ppqs.gov.in" in domains[0] and "agritech.tnau.ac.in" in domains[1]
    assert label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150,
                        http=FakeTavily(official=""), accept_mirrors=False).ok is None


def test_crawls_the_regulator_before_trusting_a_copy():
    t = FakeTavily(official="", crawl=MAJOR_USES)
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150, http=t)
    assert v.ok is True and v.claim.authority == "official" and v.claim.source_url == OFFICIAL_URL
    assert [p for p, _ in t.calls] == ["search", "crawl"]
    assert v.tavily_credits == 4.0 and any(s.startswith("crawl[") for s in v.searched)


def test_no_crawl_when_search_already_reached_the_regulator():
    t = FakeTavily()
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150, http=t)
    assert v.ok is True and "crawl" not in [p for p, _ in t.calls]
    assert v.tavily_credits == 2.0


def test_a_failing_tier_does_not_end_the_search():
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150,
                     http=FakeTavily(fail_official=True))
    assert v.ok is True and v.claim.authority == "mirror"
    assert any("failed: TimeoutError" in s for s in v.searched)


def test_without_a_key_nothing_is_cleared():
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150)
    assert v.ok is None and "no Tavily key" in v.reason


# A layout the parser cannot read: figures first, crop last.
SCRAMBLED = """Chlorantraniliprole 18.5% SC
a.i | formulation | water | wait | crop | pest
30 | 150 | 500 | 47 | Rice | Stem borer and leaf folder
"""


def honest(prompt):
    return json.dumps({"found": True, "row_text": "30 150 500 47 rice stem borer and leaf folder",
                       "pest": "Stem borer and leaf folder", "ai_g_ha": "30",
                       "formulation_per_ha": "150", "water_l_ha": "500", "waiting_days": "47"})


def liar(prompt):
    j = json.loads(honest(prompt))
    j["formulation_per_ha"] = "300"                  # a dose the row does not contain
    return json.dumps(j)


def fabricator(prompt):
    return json.dumps({"found": True, "row_text": "rice stem borer 30 300 500 47", "pest": "stem borer",
                       "ai_g_ha": "30", "formulation_per_ha": "300", "water_l_ha": "500",
                       "waiting_days": "47"})


def test_nemotron_reading_counts_only_when_every_figure_is_in_the_source():
    t = FakeTavily(official=SCRAMBLED, mirror="")
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150, http=t, llm=honest)
    assert v.ok is True and v.claim.parsed_by == "nemotron+verified" and v.claim.waiting_days == 47


def test_a_model_that_changes_a_number_is_discarded():
    t = FakeTavily(official=SCRAMBLED, mirror="")
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 300, http=t, llm=liar)
    assert v.ok is None


def test_a_model_that_invents_a_row_is_discarded():
    t = FakeTavily(official=SCRAMBLED, mirror="")
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 300, http=t, llm=fabricator)
    assert v.ok is None


def test_plain_space_layout_parses():
    plain = MAJOR_USES.replace(" | ", " ")
    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer", 150,
                     http=FakeTavily(official=plain))
    assert v.ok is True and v.claim.waiting_days == 47


@pytest.mark.parametrize("product,unit", [("Chlorantraniliprole 18.5% SC", "ml"),
                                          ("Emamectin Benzoate 5% SG", "g"),
                                          ("Imidacloprid 17.8% SL", "ml"),
                                          ("Thiamethoxam 25% WG", "g")])
def test_unit_follows_the_formulation_code(product, unit):
    assert label.unit_for(product) == unit
