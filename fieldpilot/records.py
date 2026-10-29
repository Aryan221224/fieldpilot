"""The paperwork a drone spray legally needs, written from the run itself.

    html = records.notice_html(site, operator, drone, plan)      # 24 h before: Gram Panchayat + AO
    html = records.record_html(site, operator, drone, applied)   # within 7 days: spray monitoring
    html = records.farmer_slip_html(site, applied)               # for the farmer, in Hindi first

SOP-2021 asks two documents of every operator: a written intimation to the
Gram Panchayat, the Panchayat Samiti and the Agriculture Officer at least 24
hours before an aerial operation (§2(d)), and a spray-monitoring record within
seven days of it (§7, Annexures I-II). Both are routinely skipped because they
are a chore. FieldPilot already knows everything they ask for - where, when,
what product, what dose, which zones, the weather, the flight parameters - so it
writes them, and the operator only signs.

The third document is not statutory and matters most: a slip for the farmer
saying what was sprayed where, and **the first date the crop may be harvested**
(spray date + the label's waiting period). A farmer who sells a crop before
that date is selling residue; nobody currently tells them the date.

Output is self-contained HTML with print CSS: any browser renders Devanagari
correctly, which most PDF libraries do not. ``write_pdf`` uses Chromium via
Playwright when it is installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .sop import IST, SOP_2021, SOP_2023, Report, tank_mix


@dataclass
class Operator:
    name: str
    rpc_number: str                 # DGCA Remote Pilot Certificate
    phone: str
    organisation: str = ""          # e.g. an SHG under Namo Drone Didi


@dataclass
class Drone:
    uin: str                        # Digital Sky UIN
    model: str
    tank_l: float
    nozzles: int = 4
    nozzle_type: str = "flat fan (anti-drift)"


@dataclass
class Site:
    farmer: str
    village: str
    gram_panchayat: str
    panchayat_samiti: str
    district: str
    state: str
    plot_ref: str                   # khasra / survey number
    lat: float
    lon: float
    area_ha: float
    agriculture_officer: str = "Agriculture Officer"


@dataclass
class Application:
    crop: str
    pest: str
    product: str                    # trade/common name with strength and formulation
    formulation_per_ha: float
    unit: str                       # ml | g
    volume_l_ha: float
    zones: list[str]
    treated_ha: float
    start: datetime
    end: datetime
    height_m: float
    speed_ms: float
    swath_m: float
    waiting_days: float | None
    label_source: str = ""
    batch_expiry: str = ""
    weather: dict = field(default_factory=dict)   # temp_c, rh_pct, wind_ms, wind_dir_deg
    blanket_ha: float | None = None               # what a whole-field spray would have covered
    cost_saved_inr: float | None = None

    def per_tank(self, tank_l: float) -> float:
        """Formulation per tank - the per-hectare dose over the *drone's* water volume."""
        return tank_mix(self.formulation_per_ha, self.volume_l_ha, tank_l)

    @property
    def product_total(self) -> float:
        return self.formulation_per_ha * self.treated_ha


HI_TERMS = {
    "rice": "धान", "paddy": "धान", "wheat": "गेहूँ", "maize": "मक्का", "cotton": "कपास",
    "sugarcane": "गन्ना", "mustard": "सरसों", "soybean": "सोयाबीन", "potato": "आलू",
    "tomato": "टमाटर", "chickpea": "चना", "pigeon pea": "अरहर", "groundnut": "मूँगफली",
    "stem borer": "तना छेदक", "leaf folder": "पत्ती लपेटक", "brown planthopper": "भूरा फुदका",
    "fall armyworm": "फ़ॉल आर्मीवर्म", "bollworm": "सुंडी", "pink bollworm": "गुलाबी सुंडी",
    "aphid": "माहू", "aphids": "माहू", "whitefly": "सफ़ेद मक्खी", "late blight": "पछेती झुलसा",
    "blast": "झोंका (ब्लास्ट)", "sheath blight": "शीथ झुलसा", "termite": "दीमक",
}


def hi(term: str) -> str:
    """'rice' -> 'धान (rice)'; unknown terms stay as written."""
    t = HI_TERMS.get(term.strip().lower())
    return f"{t} ({term})" if t else term


def harvest_from(app: Application) -> date | None:
    """First permissible harvest date: the day after the waiting period ends."""
    if app.waiting_days is None:
        return None
    return (app.end.astimezone(IST) + timedelta(days=float(app.waiting_days))).date()


# --------------------------------------------------------------------------- page shell

CSS = """
@page { size: A4; margin: 16mm 15mm; }
* { box-sizing: border-box; }
body { font-family: "Nirmala UI", "Noto Sans Devanagari", "Noto Sans", "Segoe UI", Arial, sans-serif;
       color: #111; font-size: 10.5pt; line-height: 1.45; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 11.5pt; margin: 6mm 0 2mm; border-bottom: 0.4mm solid #222; padding-bottom: 1mm; }
.hi { font-size: 11pt; }
h1.hi { font-size: 17pt; }
.sub { color: #444; font-size: 9pt; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0; }
td, th { border: 0.3mm solid #888; padding: 1.6mm 2.2mm; vertical-align: top; text-align: left; }
th { background: #f0f0ee; width: 34%; font-weight: 600; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }
.box { border: 0.5mm solid #111; padding: 3mm 4mm; margin: 3mm 0; }
.big { font-size: 20pt; font-weight: 700; }
.sign { margin-top: 14mm; display: grid; grid-template-columns: 1fr 1fr; gap: 10mm; }
.sign div { border-top: 0.3mm solid #111; padding-top: 1mm; font-size: 9pt; }
.tick td:first-child { width: 7mm; text-align: center; }
.foot { margin-top: 6mm; color: #555; font-size: 8pt; }
@media screen { body { max-width: 190mm; margin: 10mm auto; } .noprint { display: block; }
  button { font: inherit; padding: 2mm 5mm; } }
@media print { .noprint { display: none; } }
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='hi'><meta charset='utf-8'><title>{escape(title)}</title>"
            f"<style>{CSS}</style><body>"
            "<div class='noprint' style='text-align:right'><button onclick='print()'>Print / प्रिंट</button></div>"
            f"{body}<div class='foot'>Generated by FieldPilot from the approved run. "
            "Checked and signed by the operator; FieldPilot does not file it.</div></body></html>")


def _rows(rows: list[tuple[str, str]]) -> str:
    return "<table>" + "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>"
                               for k, v in rows) + "</table>"


def _t(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def _dose(app: Application) -> str:
    return f"{app.formulation_per_ha:g} {app.unit}/ha in {app.volume_l_ha:g} L water/ha"


# --------------------------------------------------------------------------- 1. notice

def notice_html(site: Site, op: Operator, drone: Drone, app: Application,
                issued: datetime | None = None) -> str:
    issued = issued or datetime.now(IST)
    hours = (app.start - issued).total_seconds() / 3600
    warn = "" if hours >= 24 else (
        f"<div class='box'><b>Not valid as notice:</b> issued {hours:.0f} h before the operation; "
        "SOP-2021 §2(d) requires at least 24 h.</div>")
    to = (f"The Executive Officer, Gram Panchayat {site.gram_panchayat} and Panchayat Samiti "
          f"{site.panchayat_samiti}; the {site.agriculture_officer}, {site.district}")
    to_hi = (f"कार्यकारी अधिकारी, ग्राम पंचायत {site.gram_panchayat} एवं पंचायत समिति "
             f"{site.panchayat_samiti}; कृषि अधिकारी, {site.district}")
    window = f"{_t(app.start)} – {app.end.astimezone(IST):%H:%M IST}"
    where = f"{site.village}, plot {site.plot_ref}, {site.district}, {site.state} ({site.lat:.5f}, {site.lon:.5f})"
    en = _rows([
        ("Date and time", window), ("Location", where),
        ("Farmer", site.farmer), ("Crop / target pest", f"{app.crop} / {app.pest}"),
        ("Pesticide", f"{app.product} — {_dose(app)}"),
        ("Area to be sprayed", f"{app.treated_ha:.2f} ha (zones {', '.join(app.zones)}) of {site.area_ha:.2f} ha"),
        ("Drone", f"{drone.model}, UIN {drone.uin}"),
        ("Remote pilot", f"{op.name}, DGCA RPC {op.rpc_number}" + (f", {op.organisation}" if op.organisation else "")),
        ("Contact", op.phone)])
    hi_rows = _rows([
        ("दिनांक व समय", window), ("स्थान", where), ("किसान", site.farmer),
        ("फ़सल / कीट", f"{hi(app.crop)} / {hi(app.pest)}"),
        ("कीटनाशक", f"{app.product} — {app.formulation_per_ha:g} {app.unit} प्रति हेक्टेयर, "
                    f"{app.volume_l_ha:g} लीटर पानी प्रति हेक्टेयर"),
        ("छिड़काव क्षेत्र", f"{app.treated_ha:.2f} हेक्टेयर (ज़ोन {', '.join(app.zones)})"),
        ("ड्रोन", f"{drone.model}, UIN {drone.uin}"),
        ("पायलट", f"{op.name}, DGCA प्रमाणपत्र {op.rpc_number}"), ("संपर्क", op.phone)])
    body = f"""
<h1>Prior intimation of drone pesticide application</h1>
<div class="hi"><b>ड्रोन द्वारा कीटनाशक छिड़काव की पूर्व सूचना</b></div>
<div class="sub">Under {escape(SOP_2021)}, §2(d): written intimation at least 24 hours in advance.
Issued {escape(_t(issued))}.</div>
{warn}
<h2>To / सेवा में</h2><p>{escape(to)}<br><span class="hi">{escape(to_hi)}</span></p>
<h2>Details</h2>{en}
<h2>विवरण</h2>{hi_rows}
<h2>Precautions for the public / जनता के लिए सावधानियाँ</h2>
<ul>
<li>No people or animals in the field during and immediately after spraying.<br>
<span class="hi">छिड़काव के दौरान और तुरंत बाद खेत में मनुष्य और पशु प्रवेश न करें।</span></li>
<li>Beekeepers nearby: please close or move hives for the spray window.<br>
<span class="hi">आस-पास के मधुमक्खी पालक छिड़काव के समय बक्से बंद रखें या दूर रखें।</span></li>
<li>At least 100 m will be kept from water bodies, houses and schools.<br>
<span class="hi">जल स्रोतों, घरों और विद्यालयों से कम से कम 100 मीटर की दूरी रखी जाएगी।</span></li>
<li>Spraying stops if wind exceeds 3 m/s or rain begins.<br>
<span class="hi">हवा 3 मीटर/सेकंड से तेज़ होने या बारिश शुरू होने पर छिड़काव रोक दिया जाएगा।</span></li>
</ul>
<div class="sign"><div>Remote pilot / पायलट: {escape(op.name)}</div><div>Received / प्राप्तकर्ता (seal, date)</div></div>
"""
    return _page("Drone spray intimation", body)


# --------------------------------------------------------------------------- 2. record

def record_html(site: Site, op: Operator, drone: Drone, app: Application,
                sop_report: Report | None = None, efficacy: str = "", non_target: str = "",
                incidents: str = "None") -> str:
    w = app.weather or {}
    wx = ", ".join(x for x in [
        f"{w['temp_c']:.0f} °C" if "temp_c" in w else "",
        f"RH {w['rh_pct']:.0f} %" if "rh_pct" in w else "",
        f"wind {w['wind_ms']:.1f} m/s" if "wind_ms" in w else "",
        f"from {w['wind_dir_deg']:.0f}°" if "wind_dir_deg" in w else ""] if x) or "not recorded"
    mix = f"{app.per_tank(drone.tank_l):.0f} {app.unit} per {drone.tank_l:g} L tank"
    rows = _rows([
        ("Location", f"{site.village}, plot {site.plot_ref}, {site.district}, {site.state} "
                     f"({site.lat:.5f}, {site.lon:.5f})"),
        ("Date and time", f"{_t(app.start)} – {app.end.astimezone(IST):%H:%M IST}"),
        ("Crop", app.crop), ("Pest / disease", app.pest),
        ("Pesticide (name, strength, formulation)", app.product),
        ("Batch expiry", app.batch_expiry or "— (operator to fill)"),
        ("Dose", f"{_dose(app)}; tank mix {mix}"),
        ("Label reference", app.label_source or "—"),
        ("Area treated", f"{app.treated_ha:.2f} ha — zones {', '.join(app.zones)} "
                         f"(field {site.area_ha:.2f} ha)"),
        ("Product used (total)", f"{app.product_total:.0f} {app.unit}"),
        ("Water used (total)", f"{app.volume_l_ha * app.treated_ha:.1f} L"),
        ("Weather at application", wx),
        ("Sprayer", f"{drone.model}, UIN {drone.uin}, {drone.nozzles} × {drone.nozzle_type}"),
        ("Flight", f"height {app.height_m:g} m above canopy, speed {app.speed_ms:g} m/s, swath {app.swath_m:g} m"),
        ("Remote pilot", f"{op.name}, DGCA RPC {op.rpc_number}"),
        ("Efficacy (observed after spraying)", efficacy or "— (operator to fill at follow-up)"),
        ("Non-target effects", non_target or "— (operator to fill)"),
        ("Safety incidents", incidents)])
    ppe = "".join(f"<tr><td>☐</td><td>{p}</td></tr>" for p in
                  ["Goggles", "Mask / respirator", "Gloves", "Overalls", "Boots"])
    sop_rows = ""
    if sop_report is not None:
        sop_rows = ("<h2>Pre-flight SOP checks (FieldPilot)</h2><table>"
                    + "".join(f"<tr><th>{escape(c.rule)}</th><td>{escape(c.status)}: "
                              f"{escape(c.value)} (limit {escape(c.limit)})</td></tr>"
                              for c in sop_report.checks) + "</table>")
    body = f"""
<h1>Spray monitoring record — drone application</h1>
<div class="sub">{escape(SOP_2021)}, §7 and Annexures I–II: to be submitted within seven days of
application. Flight parameters per {escape(SOP_2023)}.</div>
<h2>Application</h2>{rows}
<h2>Protective equipment worn (tick)</h2><table class="tick">{ppe}</table>
{sop_rows}
<div class="sign"><div>Remote pilot: {escape(op.name)}</div><div>Farmer: {escape(site.farmer)}</div></div>
"""
    return _page("Spray monitoring record", body)


# --------------------------------------------------------------------------- 3. farmer slip

def farmer_slip_html(site: Site, app: Application) -> str:
    h = harvest_from(app)
    h_line = (f"<div class='box'><div>कटाई इस तारीख से पहले न करें / Do not harvest before</div>"
              f"<div class='big'>{h:%d %B %Y}</div>"
              f"<div class='sub'>Spray date + the label's waiting period of {app.waiting_days:g} days.</div></div>"
              if h else "<div class='box'>Waiting period not known — ask the Agriculture Officer "
                        "before harvesting. / कटाई से पहले कृषि अधिकारी से पूछें।</div>")
    saved = ""
    if app.blanket_ha:
        frac = 1 - app.treated_ha / app.blanket_ha
        saved = (f"<p class='hi'>पूरे खेत की जगह सिर्फ़ ज़रूरी हिस्से में छिड़काव हुआ: "
                 f"<b>{app.treated_ha:.2f} में से {app.blanket_ha:.2f} हेक्टेयर</b>"
                 f" — लगभग {frac:.0%} कम दवा।"
                 + (f" बचत लगभग ₹{app.cost_saved_inr:,.0f}।" if app.cost_saved_inr else "") + "</p>"
                 f"<p>Only the zones that needed it were sprayed: {app.treated_ha:.2f} of "
                 f"{app.blanket_ha:.2f} ha, about {frac:.0%} less chemical than a whole-field spray"
                 + (f", saving about ₹{app.cost_saved_inr:,.0f}." if app.cost_saved_inr else ".") + "</p>")
    body = f"""
<h1 class="hi">आपके खेत में आज क्या हुआ</h1>
<div class="sub">What was done in your field today · {escape(site.farmer)} · {escape(site.village)},
plot {escape(site.plot_ref)}</div>
{h_line}
{_rows([("फ़सल / Crop", hi(app.crop)), ("समस्या / Problem", hi(app.pest)),
        ("दवा / Product", app.product),
        ("मात्रा / Dose", f"{app.formulation_per_ha:g} {app.unit} प्रति हेक्टेयर / per ha"),
        ("कहाँ / Where", f"ज़ोन / zones {', '.join(app.zones)}"),
        ("कब / When", _t(app.start))])}
{saved}
<h2 class="hi">सावधानी / Take care</h2>
<ul class="hi">
<li>आज खेत में पशु न चराएँ, बच्चों को खेत से दूर रखें। <span class="sub">No grazing today; keep children out.</span></li>
<li>दवा के खाली डिब्बे न जलाएँ, न दोबारा इस्तेमाल करें। <span class="sub">Do not burn or reuse empty containers.</span></li>
<li>कोई असर न दिखे तो 7 दिन बाद पायलट को बताएँ। <span class="sub">No improvement after 7 days? Tell the pilot.</span></li>
</ul>
"""
    return _page("Farmer slip", body)


# --------------------------------------------------------------------------- output

def write_html(html: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def write_pdf(html: str, path: Path) -> Path | None:
    """A4 PDF through Chromium, so Devanagari is shaped correctly. None if unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.set_content(html, wait_until="load")
        pg.pdf(path=str(path), format="A4", print_background=True)
        b.close()
    return path
