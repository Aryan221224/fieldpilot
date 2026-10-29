"""Is this product registered for this crop and pest, at this dose? Ask the label.

    v = label.verify("Chlorantraniliprole 18.5% SC", "rice", "stem borer",
                     proposed_per_ha=150, tavily_key=KEY, llm=nemotron_json)
    v.ok        # True / False / None (unverifiable -> a human must clear it)
    v.claim     # the CIB&RC major-use row: dose range, water, waiting period, source URL, excerpt

In India a pesticide may only be applied to the crop, against the pest, at the
dose its registration lists - and SOP-2021 §5.2 holds drone operators to exactly
that. The list is the CIB&RC "Major uses of pesticides": one row per product ×
crop × pest with the dose in a.i. g/ha and formulation g-or-ml/ha, the water
volume, and the waiting period before harvest.

How a row is found
------------------
1. **Tavily search**, restricted to the regulator's domains first and to state
   agricultural universities' published copies of the same list second.
2. **Tavily extract** on the hits, with the product × crop × pest as the query,
   so only the relevant chunks come back instead of a 200-page PDF.
   If search finds nothing on the regulator's domain (the list is a set of PDFs that
   search indexes unevenly), **Tavily crawl** walks the regulator's own site, guided by
   natural-language instructions, before any university copy is trusted.
   Every call asks for ``include_usage`` and the verdict reports the Tavily credits spent.
3. **Parse.** A deterministic parser reads the row. If the layout defeats it,
   Nemotron reads the excerpt and returns the row as JSON - and then **every
   number it returns must appear, verbatim, in the row text it quotes, and that
   row text must appear verbatim in the excerpt Tavily returned.** A model that
   invents a dose produces a claim that fails this check and is discarded. The
   model reads; the string match decides.
4. **Compare.** The proposed per-hectare dose must sit inside the registered
   range. The waiting period goes to sop.assess(), which checks it against the
   crop twin's harvest date.

Anything short of a matched, cited row is ``ok=None``: not "probably fine", but
"a human has to look". The agent can never clear it on its own.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable

OFFICIAL = ["ppqs.gov.in", "cibrc.gov.in", "agriwelfare.gov.in"]
MIRRORS = ["agritech.tnau.ac.in", "tnau.ac.in", "pau.edu", "iari.res.in", "icar.org.in",
           "niphm.gov.in", "pjtau.edu.in", "uasbangalore.edu.in", "hau.ac.in"]

LIQUID = {"SC", "EC", "SL", "OD", "EW", "ME", "CS", "SE", "ZC", "FS", "DC", "L"}
SOLID = {"WG", "WDG", "WP", "SG", "GR", "G", "DP", "SP", "DF", "WS", "CG"}

PEST_ALIASES = {
    "dbm": "diamondback moth", "diamond back moth": "diamondback moth",
    "bph": "brown planthopper", "brown plant hopper": "brown planthopper",
    "wbph": "white backed planthopper", "faw": "fall armyworm", "ysb": "yellow stem borer",
    "gall midge": "gall midge", "leaf folder": "leaf folder", "leaffolder": "leaf folder",
}
STOP = {"and", "the", "of", "&", "pest", "pests", "insect", "insects", "larvae", "caterpillar"}

_NUM = r"\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?"


Http = Callable[[str, dict], dict]
Llm = Callable[[str], str]


@dataclass
class LabelClaim:
    product: str
    crop: str
    pest: str
    ai_g_ha: tuple[float, float] | None
    formulation_per_ha: tuple[float, float]
    unit: str                      # "ml" | "g"
    water_l_ha: float | None
    waiting_days: float | None
    row_text: str
    source_url: str
    authority: str                 # "official" | "mirror"
    parsed_by: str                 # "parser" | "nemotron+verified"


@dataclass
class LabelVerdict:
    ok: bool | None
    reason: str
    claim: LabelClaim | None = None
    proposed_per_ha: float | None = None
    citations: list[str] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    tavily_credits: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- parsing

def unit_for(product: str) -> str | None:
    m = re.search(r"\b([A-Z]{1,3})\b\s*$", product.strip().replace("%", " "))
    code = m.group(1) if m else ""
    return "ml" if code in LIQUID else "g" if code in SOLID else None


def _rng(tok: str) -> tuple[float, float]:
    parts = re.split(r"\s*[-–]\s*", tok.strip())
    lo = float(parts[0])
    hi = float(parts[1]) if len(parts) > 1 else lo
    return (min(lo, hi), max(lo, hi))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("|", " ").replace(" ", " ")).strip().lower()


def _product_key(product: str) -> str:
    """'Chlorantraniliprole 18.5% SC' -> 'chlorantraniliprole 18.5'."""
    m = re.match(r"\s*([A-Za-z][A-Za-z\- ]+?)\s+(\d+(?:\.\d+)?)", product)
    return f"{m.group(1).lower()} {m.group(2)}" if m else product.lower().split()[0]


def _section(text: str, product: str) -> str:
    """The stretch of text that belongs to this product: from its heading to the next
    product-like heading (a name followed by a strength and a formulation code)."""
    t = _norm(text)
    key = _product_key(product)
    name, strength = key.rsplit(" ", 1) if " " in key else (key, "")
    pat = re.escape(name) + r"\s+" + re.escape(strength) if strength else re.escape(name)
    m = re.search(pat, t)
    if not m:
        return ""
    rest = t[m.end():]
    nxt = re.search(r"\b[a-z][a-z\-]{4,}(?: [a-z\-]+)?\s+\d+(?:\.\d+)?\s*%?\s*(?:"
                    + "|".join(c.lower() for c in sorted(LIQUID | SOLID, key=len, reverse=True))
                    + r")\b", rest[40:])
    return t[m.start(): m.end() + 40 + (nxt.start() if nxt else min(len(rest) - 40, 4000))]


def parse_rows(text: str, product: str, crop: str) -> list[tuple[str, str]]:
    """(pest text, row text) candidates for ``crop`` inside ``product``'s section.

    Rows look like ``Rice | Stem borer and leaf folder | 30 | 150 | 500 | 47`` -
    crop, pest, a.i. g/ha, formulation g-or-ml/ha, water L/ha, waiting days - with
    pipes, tabs or plain spaces depending on how the PDF was extracted.
    """
    sec = _section(text, product)
    if not sec:
        return []
    crop_l = crop.lower()
    out = []
    for m in re.finditer(r"\b" + re.escape(crop_l) + r"\b", sec):
        tail = sec[m.end(): m.end() + 220]
        nums = list(re.finditer(_NUM, tail))
        if len(nums) < 3:
            continue
        pest = tail[: nums[0].start()].strip(" :-|")
        if not re.search(r"[a-z]{3,}", pest):
            continue
        # a row ends at its waiting-period figure, or "-"/"na" when none is given
        group = nums[:4]
        end = group[-1].end()
        after = tail[end:end + 6]
        if len(group) == 3 and re.match(r"\s*(?:-|na|n\.a\.|nil)\b", after):
            end += len(re.match(r"\s*(?:-|na|n\.a\.|nil)", after).group(0))
        out.append((pest, (crop_l + tail[:end]).strip()))
    return out


def _row_numbers(row: str, crop: str) -> list[str]:
    body = row[len(crop):]
    first = re.search(_NUM, body)
    return [m.group(0) for m in re.finditer(_NUM, body[first.start():])] if first else []


def claim_from_row(product: str, crop: str, pest: str, row: str, url: str, authority: str,
                   parsed_by: str = "parser") -> LabelClaim | None:
    nums = _row_numbers(row, crop.lower())
    if len(nums) < 3:
        return None
    if len(nums) >= 4:
        ai, form, water, wait = nums[:4]
    else:
        ai, form, water, wait = nums[0], nums[1], nums[2], None
    unit = unit_for(product) or "ml"
    return LabelClaim(product=product, crop=crop, pest=pest, ai_g_ha=_rng(ai),
                      formulation_per_ha=_rng(form), unit=unit,
                      water_l_ha=_rng(water)[0], waiting_days=None if wait is None else _rng(wait)[0],
                      row_text=row, source_url=url, authority=authority, parsed_by=parsed_by)


def _alias(s: str) -> str:
    s = _norm(s)
    for k, v in PEST_ALIASES.items():
        s = re.sub(r"\b" + re.escape(k) + r"\b", v, s)
    return s


def pest_matches(asked: str, label: str) -> bool:
    """Same pest, allowing for aliases (DBM), plurals and split compounds ("boll worms")."""
    def words(s: str) -> set[str]:
        return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", _alias(s))} - STOP
    a, b = words(asked), words(label)
    if not a:
        return False
    joined = re.sub(r"[^a-z]", "", _alias(label))
    hits = {w for w in a if w in b or (len(w) >= 5 and w in joined)}
    return len(hits) >= max(1, len(a) - 1)


# --------------------------------------------------------------------------- LLM fallback

LLM_PROMPT = """You are reading an excerpt of the Indian CIB&RC "Major uses of pesticides" list.
Find the ONE row for product "{product}", crop "{crop}", pest "{pest}".
Return JSON only:
{{"found": true|false, "row_text": "<the row, copied exactly as it appears in the excerpt>",
  "pest": "<pest as written in the row>", "ai_g_ha": "<as written>", "formulation_per_ha": "<as written>",
  "water_l_ha": "<as written>", "waiting_days": "<as written, or null>"}}
Copy text exactly. Do not compute, convert or correct anything. If there is no such row, return {{"found": false}}.

EXCERPT:
{excerpt}
"""


def llm_claim(llm: Llm, product: str, crop: str, pest: str, excerpt: str, url: str,
              authority: str) -> LabelClaim | None:
    """Nemotron's reading of the excerpt, kept only if every figure is in the excerpt."""
    try:
        raw = llm(LLM_PROMPT.format(product=product, crop=crop, pest=pest, excerpt=excerpt[:6000]))
        j = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except (ValueError, TypeError):
        return None
    if not j.get("found"):
        return None
    row = _norm(str(j.get("row_text", "")))
    if not row or row not in _norm(excerpt):
        return None                                  # quoted a row that is not there
    for k in ("ai_g_ha", "formulation_per_ha", "water_l_ha", "waiting_days"):
        v = j.get(k)
        if v in (None, "", "null"):
            if k == "waiting_days":
                continue
            return None
        if _norm(str(v)) not in row:
            return None                              # a number the row does not contain
    try:
        wait = j.get("waiting_days")
        return LabelClaim(product=product, crop=crop, pest=str(j.get("pest", pest)),
                          ai_g_ha=_rng(str(j["ai_g_ha"])),
                          formulation_per_ha=_rng(str(j["formulation_per_ha"])),
                          unit=unit_for(product) or "ml", water_l_ha=_rng(str(j["water_l_ha"]))[0],
                          waiting_days=None if wait in (None, "", "null") else _rng(str(wait))[0],
                          row_text=row, source_url=url, authority=authority,
                          parsed_by="nemotron+verified")
    except (ValueError, KeyError):
        return None


# --------------------------------------------------------------------------- Tavily

def tavily_http(key: str, timeout_s: float = 25.0) -> Http:
    def post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(f"https://api.tavily.com/{path}",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))
    return post


REGISTRY_CRAWL_ROOT = "https://ppqs.gov.in"


def _credits(res: dict) -> float:
    u = (res or {}).get("usage") or {}
    try:
        return float(u.get("credits") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _crawl(http: Http, product: str, crop: str, pest: str, searched: list[str],
           spent: list[float]) -> list[tuple[str, str]]:
    """Walk the regulator's own site for the major-uses list when search misses it."""
    instr = (f"Find the CIB&RC 'Major uses of pesticides' list (insecticides, fungicides, "
             f"herbicides) that has the row for {product} on {crop} against {pest}: dose, "
             f"dilution in water and waiting period.")
    searched.append(f"crawl[{REGISTRY_CRAWL_ROOT}]: {instr}")
    res = http("crawl", {"url": REGISTRY_CRAWL_ROOT, "instructions": instr, "max_depth": 2,
                         "max_breadth": 30, "limit": 40, "allow_external": False,
                         "extract_depth": "advanced", "format": "text", "chunks_per_source": 5,
                         "include_usage": True})
    spent.append(_credits(res))
    return [(r["url"], r.get("raw_content") or "") for r in res.get("results", [])
            if r.get("url") and r.get("raw_content")]


def _find(http: Http, product: str, crop: str, pest: str, domains: list[str],
          searched: list[str], spent: list[float] | None = None) -> list[tuple[str, str]]:
    """(url, relevant text) for each hit on ``domains``."""
    q = f"{product} {crop} {pest} major uses dosage formulation waiting period"
    searched.append(f"search[{','.join(domains[:3])}{'…' if len(domains) > 3 else ''}]: {q}")
    res = http("search", {"query": q, "search_depth": "advanced", "max_results": 5,
                          "include_domains": domains, "include_domains_mode": "restrict",
                          "chunks_per_source": 3, "include_usage": True})
    if spent is not None:
        spent.append(_credits(res))
    urls = [r["url"] for r in res.get("results", []) if r.get("url")]
    hits = {r["url"]: r.get("content", "") for r in res.get("results", []) if r.get("url")}
    if urls:
        ex = http("extract", {"urls": urls[:5], "query": f"{product} {crop} {pest}",
                              "chunks_per_source": 5, "extract_depth": "advanced",
                              "format": "text", "include_usage": True})
        if spent is not None:
            spent.append(_credits(ex))
        for r in ex.get("results", []):
            if r.get("raw_content"):
                hits[r["url"]] = r["raw_content"] + "\n" + hits.get(r["url"], "")
    return list(hits.items())


def verify(product: str, crop: str, pest: str, proposed_per_ha: float | None,
           tavily_key: str | None = None, llm: Llm | None = None, http: Http | None = None,
           accept_mirrors: bool = True) -> LabelVerdict:
    if http is None:
        if not tavily_key:
            return LabelVerdict(None, "no Tavily key - label cannot be checked", None, proposed_per_ha)
        http = tavily_http(tavily_key)
    searched: list[str] = []
    spent: list[float] = []
    tiers = [("official", OFFICIAL, "search"), ("official", OFFICIAL, "crawl")] + \
        ([("mirror", MIRRORS, "search")] if accept_mirrors else [])
    near_misses: list[str] = []
    official_seen = False
    for authority, domains, how in tiers:
        if how == "crawl" and official_seen:
            continue                                  # search already reached the regulator
        try:
            pages = (_crawl(http, product, crop, pest, searched, spent) if how == "crawl" else
                     _find(http, product, crop, pest, domains, searched, spent))
        except Exception as e:                        # network, quota, 5xx
            searched.append(f"  failed: {type(e).__name__}: {e}")
            continue
        if authority == "official" and pages:
            official_seen = True
        for url, text in pages:
            claim = None
            for label_pest, row in parse_rows(text, product, crop):
                if pest_matches(pest, label_pest):
                    claim = claim_from_row(product, crop, label_pest, row, url, authority)
                    if claim:
                        break
                near_misses.append(f"{crop} / {label_pest} at {url}")
            if claim is None and llm is not None and _section(text, product):
                claim = llm_claim(llm, product, crop, pest, _section(text, product), url, authority)
                if claim and not pest_matches(pest, claim.pest):
                    near_misses.append(f"{crop} / {claim.pest} at {url}")
                    claim = None
            if claim:
                v = _judge(claim, proposed_per_ha, searched)
                v.tavily_credits = round(sum(spent), 2)
                return v
    credits = round(sum(spent), 2)
    if near_misses:
        return LabelVerdict(None, f"{product} is listed for {crop}, but not against '{pest}' in the "
                            f"rows found: " + "; ".join(sorted(set(near_misses))[:3]),
                            None, proposed_per_ha, [], searched, credits)
    return LabelVerdict(None, f"no major-use row found for {product} on {crop} against {pest}",
                        None, proposed_per_ha, [], searched, credits)


def _judge(c: LabelClaim, proposed: float | None, searched: list[str]) -> LabelVerdict:
    lo, hi = c.formulation_per_ha
    rng = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
    cite = [c.source_url]
    src = "CIB&RC" if c.authority == "official" else "published copy of the CIB&RC list"
    wait = "" if c.waiting_days is None else f", waiting period {c.waiting_days:g} d"
    if proposed is None:
        return LabelVerdict(True, f"registered for {c.crop} / {c.pest}: {rng} {c.unit}/ha{wait} ({src})",
                            c, None, cite, searched)
    tol = 0.005 * max(hi, 1e-9)
    if lo - tol <= proposed <= hi + tol:
        return LabelVerdict(True, f"{proposed:g} {c.unit}/ha is within the registered {rng} {c.unit}/ha"
                            f"{wait} ({src})", c, proposed, cite, searched)
    side = "above" if proposed > hi else "below"
    return LabelVerdict(False, f"{proposed:g} {c.unit}/ha is {side} the registered {rng} {c.unit}/ha "
                        f"for {c.crop} / {c.pest} ({src})", c, proposed, cite, searched)
