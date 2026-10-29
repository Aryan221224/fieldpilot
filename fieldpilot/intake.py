"""Booking intake: a farmer's message -> a spray job the operator can schedule.

Spray-drone operators everywhere take bookings the same way: a phone call, a WhatsApp or
WeChat message, a voice note, in the farmer's language and the farmer's unit ("dhai bigha
aalu", "3 mu de arroz", "20 rai", "5 alqueires de soja"). Nemotron on Nebius Token Factory
reads the message and returns structured fields. The model is trusted with language, not
with numbers:

* the area must be written in the message itself, as digits or as a number word that maps
  to the same value -- otherwise it is marked UNVERIFIED and the operator confirms it;
* the unit is converted in code. Units whose size changes by region (bigha, alqueire,
  manzana in some countries) take the operator's local size and are flagged for
  confirmation;
* the farm is the farmer's location pin when there is one; otherwise the place name is
  geocoded (Open-Meteo geocoding, no key) in the operator's country, nearest to the base,
  and flagged for confirmation.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field

# m2 per unit. Fixed-size units only; region-dependent ones come from the operator.
SQM_PER = {"hectare": 10_000.0, "acre": 4046.86, "are": 100.0, "m2": 1.0,
           "mu": 666.67,            # China 亩
           "rai": 1600.0,           # Thailand ไร่
           "feddan": 4200.83,       # Egypt, Sudan
           "dunam": 1000.0,         # Levant, Turkey (metric dönüm)
           "kanal": 505.86, "marla": 25.29, "guntha": 101.17, "cent": 40.47, "biswa": 126.45}
REGIONAL_UNITS = {"bigha": 2529.0,      # UP/Bihar 2529, Punjab/Haryana ~1012, Bengal 1338
                  "alqueire": 24200.0,  # Brazil: paulista 24 200, mineiro/goiano 48 400
                  "manzana": 6987.0,    # Central America, ~0.7 ha
                  "tarea": 628.9}       # Dominican Republic
UNITS = sorted(set(SQM_PER) | set(REGIONAL_UNITS))
DEFAULT_BIGHA_M2 = REGIONAL_UNITS["bigha"]

NUMBER_WORDS = {
    # Hindi / Hinglish
    "aadha": 0.5, "adha": 0.5, "आधा": 0.5, "dedh": 1.5, "derh": 1.5, "डेढ़": 1.5, "डेढ": 1.5,
    "dhai": 2.5, "dhaai": 2.5, "ढाई": 2.5, "ek": 1, "एक": 1, "do": 2, "दो": 2, "teen": 3,
    "tin": 3, "तीन": 3, "char": 4, "chaar": 4, "चार": 4, "paanch": 5, "panch": 5, "पांच": 5,
    "पाँच": 5, "chhe": 6, "che": 6, "छह": 6, "छः": 6, "saat": 7, "सात": 7, "aath": 8, "आठ": 8,
    "nau": 9, "नौ": 9, "das": 10, "दस": 10,
    # English
    "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
    # Spanish / Portuguese
    "medio": 0.5, "meio": 0.5, "meia": 0.5, "uno": 1, "una": 1, "um": 1, "uma": 1, "dos": 2,
    "dois": 2, "duas": 2, "tres": 3, "três": 3, "cuatro": 4, "quatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "sete": 7, "ocho": 8, "oito": 8, "nueve": 9, "nove": 9, "diez": 10,
    "dez": 10,
    # French
    "demi": 0.5, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10,
    # Chinese
    "半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
    "九": 9, "十": 10,
}
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९๐๑๒๓๔๕๖๗๘๙", "01234567890123456789")

SYSTEM = """You read a farmer's booking for an agricultural drone spray (any language; often a
transcribed voice note) and return ONLY a JSON object, no prose:
{"farmer": str|null, "phone": str|null, "village": str|null, "district": str|null,
 "state": str|null, "crop": str|null, "problem": str|null, "area_value": number|null,
 "area_unit": one of [""" + ", ".join(f'"{u}"' for u in UNITS) + """] or null,
 "area_quote": str|null, "product": str|null, "preferred_day": str|null,
 "language": ISO 639-1 code}
village: the place name as written. crop and problem in plain English, lower case
(e.g. "potato", "late blight"). area_quote: copy the exact words that state the area.
Never guess and never convert units. If something is not in the message, use null."""

_S, _N = {"type": ["string", "null"]}, {"type": ["number", "null"]}
BOOKING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"farmer": _S, "phone": _S, "village": _S, "district": _S, "state": _S,
                   "crop": _S, "problem": _S, "area_value": _N,
                   "area_unit": {"type": ["string", "null"], "enum": UNITS + [None]},
                   "area_quote": _S, "product": _S, "preferred_day": _S, "language": _S},
    "required": ["farmer", "phone", "village", "district", "state", "crop", "problem",
                 "area_value", "area_unit", "area_quote", "product", "preferred_day", "language"],
}


@dataclass
class Job:
    job_id: str
    message: str
    farmer: str | None = None
    phone: str | None = None
    village: str | None = None
    district: str | None = None
    state: str | None = None
    crop: str | None = None
    problem: str | None = None
    product: str | None = None
    preferred_day: str | None = None
    area_value: float | None = None
    area_unit: str | None = None
    area_ha: float | None = None
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    confirm: list[str] = field(default_factory=list)     # what the operator must check on the call
    model: str | None = None
    protocol: str = "prompt"          # json_schema when Token Factory constrained the output
    extracted: dict = field(default_factory=dict)        # the model's raw fields (for the flywheel)

    def to_dict(self) -> dict:
        return asdict(self)


UNIT_ALIASES = {
    "bigha": "bigha", "bighas": "bigha", "beegha": "bigha", "बीघा": "bigha", "बीघे": "bigha",
    "acre": "acre", "acres": "acre", "ekad": "acre", "ekar": "acre", "एकड़": "acre", "एकड": "acre",
    "hectare": "hectare", "hectares": "hectare", "ha": "hectare", "hectar": "hectare",
    "हेक्टेयर": "hectare", "hectárea": "hectare", "hectáreas": "hectare", "hectare(s)": "hectare",
    "alqueire": "alqueire", "alqueires": "alqueire", "biswa": "biswa", "बिस्वा": "biswa",
    "kanal": "kanal", "कनाल": "kanal", "marla": "marla", "मरला": "marla", "guntha": "guntha",
    "गुंठा": "guntha", "mu": "mu", "亩": "mu", "rai": "rai", "ไร่": "rai", "feddan": "feddan",
    "manzana": "manzana", "manzanas": "manzana", "tarea": "tarea", "tareas": "tarea",
}


def norm_unit(u: str | None) -> str | None:
    """The model's or the farmer's unit word -> FieldPilot's unit name (None if unknown)."""
    if not u:
        return None
    u = u.strip().lower().rstrip(".")
    return UNIT_ALIASES.get(u, u if u in UNITS else None)


def area_from_text(message: str) -> tuple[float, str] | None:
    """Deterministic fallback: "<number or number word> <unit>" written in the message."""
    text = re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", _norm(message))
    toks = re.findall(r"\d+(?:\.\d+)?|[\w\u0900-\u097F\u0E00-\u0E7F]+|[\u4e00-\u9fff]", text)
    found = []
    for a, b in zip(toks, toks[1:]):
        unit = norm_unit(b)
        if unit is None:
            continue
        try:
            val = float(a)
        except ValueError:
            val = NUMBER_WORDS.get(a)
        if val:
            found.append((float(val), unit))
    return found[0] if len(found) == 1 else None


def _norm(s: str) -> str:
    return s.translate(DEVANAGARI_DIGITS).lower()


def area_in_message(message: str, value: float, quote: str | None) -> bool:
    """Is ``value`` written in the message (digits or a number word), not inferred?"""
    text = re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", _norm(message))      # 1,5 ha -> 1.5
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if any(abs(n - value) < 1e-6 for n in nums):
        return True
    if quote and _norm(quote) not in _norm(message):
        return False
    words = re.findall(r"[\w\u0900-\u097F]+", text)
    hits = [NUMBER_WORDS[w] for w in words if w in NUMBER_WORDS]
    hits += [NUMBER_WORDS[c] for c in text if "\u4e00" <= c <= "\u9fff" and c in NUMBER_WORDS]
    return any(abs(h - value) < 1e-6 for h in hits)


def to_hectares(value: float, unit: str, bigha_m2: float = DEFAULT_BIGHA_M2,
                local_units: dict | None = None) -> float:
    unit = (unit or "").lower().strip()
    local = dict(REGIONAL_UNITS) | {"bigha": bigha_m2} | {k.lower(): float(v)
                                                          for k, v in (local_units or {}).items()}
    if unit in local:
        return value * local[unit] / 10_000
    if unit not in SQM_PER:
        raise ValueError(f"unknown area unit {unit!r}")
    return value * SQM_PER[unit] / 10_000


def geocode(name: str, country: str = "", timeout_s: float = 8.0) -> list[dict]:
    """Open-Meteo geocoding (GeoNames), optionally limited to one ISO-2 country. No key."""
    q = {"name": name, "count": 10, "language": "en"}
    if country:
        q["countryCode"] = country.upper()
    q = urllib.parse.urlencode(q)
    with urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{q}",
                                timeout=timeout_s) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [{"name": x.get("name"), "lat": x["latitude"], "lon": x["longitude"],
             "admin1": x.get("admin1"), "admin2": x.get("admin2")} for x in d.get("results", [])]


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def parse_booking(message: str, llm, base: tuple[float, float] | None = None,
                  bigha_m2: float = DEFAULT_BIGHA_M2, geocoder=geocode,
                  max_km: float = 60.0, pin: list | tuple | None = None,
                  local_units: dict | None = None, country: str = "") -> Job:
    """One booking message -> Job. ``llm`` is the Token Factory client (``.text``)."""
    from .llm import extract_json
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": message}]
    protocol = "prompt"
    try:
        if hasattr(llm, "json"):
            d, protocol = llm.json(msgs, BOOKING_SCHEMA, "booking", max_tokens=600,
                                   purpose="intake")
        else:
            d = extract_json(llm.text(msgs, max_tokens=600, temperature=0.0)) or {}
    except Exception:  # noqa: BLE001
        d = {}
    if not isinstance(d, dict):
        d = {}
    j = Job(job_id="J-" + uuid.uuid4().hex[:5].upper(), message=message,
            model=(llm.model_for("intake") if hasattr(llm, "model_for") else getattr(llm, "agent_model", None)), protocol=protocol,
            extracted={k: d.get(k) for k in BOOKING_SCHEMA["properties"]})
    for k in ("farmer", "phone", "village", "district", "state", "crop", "problem", "product",
              "preferred_day"):
        v = d.get(k)
        setattr(j, k, str(v).strip() if v not in (None, "") else None)
    if j.phone and not re.sub(r"\D", "", j.phone) in re.sub(r"\D", "", _norm(message)):
        j.confirm.append(f"phone {j.phone} is not in the message")
        j.phone = None
    # area: the number must be in the message; the unit is converted here, not by the model
    val, unit = d.get("area_value"), norm_unit(d.get("area_unit"))
    try:
        val = float(val) if val is not None else None
    except (TypeError, ValueError):
        val = None
    if val is None or unit is None:
        hit = area_from_text(message)            # the model missed it: read it in code, if unambiguous
        if hit:
            val, unit = hit
            j.confirm.append(f"area {val:g} {unit} read from the message by code - confirm")
    if val is None or unit is None:
        j.confirm.append("area not stated - ask the farmer")
    elif not area_in_message(message, val, d.get("area_quote")):
        j.confirm.append(f"area {val:g} {unit} is not written in the message - confirm")
    else:
        try:
            j.area_value, j.area_unit = val, unit
            local = {"bigha": bigha_m2} | {k.lower(): float(v) for k, v in (local_units or {}).items()}
            j.area_ha = round(to_hectares(val, unit, bigha_m2, local_units), 3)
            if unit in REGIONAL_UNITS or unit in local:
                size = local.get(unit, REGIONAL_UNITS.get(unit))
                j.confirm.append(f"{unit} taken as {size:.0f} m2 (local size varies) - confirm")
        except ValueError as e:
            j.confirm.append(str(e))
    if not j.crop:
        j.confirm.append("crop not stated")
    if not j.problem:
        j.confirm.append("problem not stated - a scan of the field decides what to spray")
    # place: the farmer's own location pin wins; otherwise look the village up
    if pin:
        j.lat, j.lon = round(float(pin[0]), 6), round(float(pin[1]), 6)
        j.place = "farmer's location pin"
    elif j.village:
        try:
            hits = (geocoder(j.village, country) if country else geocoder(j.village)) or []
        except Exception as e:  # noqa: BLE001
            hits = []
            j.confirm.append(f"village lookup failed ({type(e).__name__})")
        if base and hits:
            hits.sort(key=lambda h: _km(base, (h["lat"], h["lon"])))
            hits = [h for h in hits if _km(base, (h["lat"], h["lon"])) <= max_km]
        if hits:
            h = hits[0]
            j.lat, j.lon = round(h["lat"], 5), round(h["lon"], 5)
            j.place = ", ".join(x for x in (h["name"], h.get("admin2"), h.get("admin1")) if x)
            j.confirm.append(f"location taken as {j.place} - confirm or drop a pin")
        else:
            j.confirm.append(f"village '{j.village}' not found near the base - drop a pin")
    else:
        j.confirm.append("village not stated")
    return j
