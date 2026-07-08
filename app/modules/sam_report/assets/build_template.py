"""
Rebuild assets/report_template.docx from the reference Franklin report.

This is a BUILD-TIME tool (not imported by the app). It turns a filled example
report into a «TOKEN» template + «RESULTS_TABLE» marker, switches fonts to Jost,
and compensates for Jost's larger metrics so the Arial-tuned letterhead/cover
don't clip. Run it if the source template changes:

    # 1. unpack with the docx skill (merges runs so tokens are contiguous):
    python <docx-skill>/scripts/office/unpack.py "Franklin- SAM.docx" unpacked/
    # 2. tokenize + fit:
    PYTHONUTF8=1 python build_template.py unpacked/
    # 3. repack:
    python <docx-skill>/scripts/office/pack.py unpacked/ report_template.docx \
        --original "Franklin- SAM.docx"

Token list must stay in sync with docx_fill._tokens().
"""
import re
import sys
from pathlib import Path

U = Path(sys.argv[1]) / "word"
doc = (U / "document.xml").read_text(encoding="utf-8")
hdr = (U / "header1.xml").read_text(encoding="utf-8")


def rep(t, old, new, expect):
    if t.count(old) != expect:
        raise SystemExit(f"expected {expect}x {old!r}, found {t.count(old)}")
    return t.replace(old, new)


# ── document.xml: cover blocks (x2: Choice + Fallback), narrative, project info ──
for old, new, exp in [
    ("FRANKLIN SOLAR, LLC", "«PROJECT_TITLE»", 2),
    ("Birch Creek", "«OWNER_NAME»", 2), ("213-444-7860", "«OWNER_PHONE»", 2),
    ("Boyd Jones Construction", "«EPC_NAME»", 2), ("402-553-1804", "«EPC_PHONE»", 2),
    ("Castillo Engineering Services LLC", "«ENG_NAME»", 2), ("407-289-2575", "«ENG_PHONE»", 2),
    ("EOR: Ermocrates E Castillo, PE091153", "EOR: «EOR»", 2),
    ("Designer: Manjil Puri", "Designer: «DESIGNER»", 2),
    ("Check: Artem Akulov", "Check: «CHECKER»", 2),
    ("26 simulations were conducted based on available data from 1998 to 2024",
     "«NUM_RUNS» simulations were conducted based on available data from «YEAR_START» to «YEAR_END»", 1),
    ("spanning 1998–2024", "spanning «YEAR_RANGE»", 1),
    ("A total of 26 SAM simulations", "A total of «NUM_RUNS» SAM simulations", 1),
    ("CAD Layout – Franklin Solar", "CAD Layout – «PROJECT_NAME»", 1),
    # Inputs reference list: fill the datasheet + weather-data lines from the data.
    ("<w:t>Module Datasheet</w:t>", "<w:t>Module Datasheet: «MODULE_MODEL»</w:t>", 1),
    ('<w:t xml:space="preserve">Inverter Datasheet </w:t>',
     '<w:t xml:space="preserve">Inverter Datasheet: «INVERTER_MODEL»</w:t>', 1),
    ("<w:t>Weather Data:</w:t>", "<w:t>Weather Data: «WEATHER_FILE»</w:t>", 1),
    ("three different modules", "«MODULES_PHRASE»", 1),  # RESULTS intro (dynamic count)
    ("<w:t>Franklin Solar LLC</w:t>", "<w:t>«PROJECT_NAME»</w:t>", 1),
    ("<w:t>41.757669, -79.810780</w:t>", "<w:t>«COORDINATES»</w:t>", 1),
    ("<w:t>0.45</w:t>", "<w:t>«GCR»</w:t>", 1),
]:
    doc = rep(doc, old, new, exp)

tbls = [m.start() for m in re.finditer(r"<w:tbl>", doc)]
s, e = tbls[1], doc.index("</w:tbl>", tbls[1])          # Project Info table
doc = doc[:s] + doc[s:e].replace("<w:t>6</w:t>", "<w:t>«MODULES_PER_STRING»</w:t>", 1) + doc[e:]

# Project Information: add Module / Inverter / DC-AC / System-size rows by
# cloning the "Modules Per String" row (keeps the template's exact cell styling).
_mps = doc.index("Modules Per String")
_tr_s = doc.rfind("<w:tr ", 0, _mps)
_tr_e = doc.index("</w:tr>", _mps) + len("</w:tr>")
_row = doc[_tr_s:_tr_e]
_extra = "".join(
    _row.replace("<w:t>Modules Per String</w:t>", f"<w:t>{label}</w:t>")
        .replace("«MODULES_PER_STRING»", token)
    for label, token in [("Module Model", "«MODULE_MODEL»"),
                         ("Inverter Model", "«INVERTER_MODEL»"),
                         ("DC/AC Ratio", "«DC_AC_RATIO»"),
                         ("System Size (DC)", "«SYSTEM_SIZE»")]
)
doc = doc[:_tr_e] + _extra + doc[_tr_e:]

# Inputs albedo: replace the fixed "conservative approach…values:" + two bullets
# with a single «ALBEDO_TEXT» run (derived from the pysam monthly albedo).
doc = rep(doc, "A conservative approach was applied by using uniform albedo values:",
          "«ALBEDO_TEXT»", 1)
for _b in ("0.8 for months with a high probability of snow (September through April), "
           "to account for enhanced reflection from accumulated snow.",
           "0.2 for months with a low probability of snow (May through August)."):
    _bi = doc.index(_b)
    doc = doc[:doc.rfind("<w:p ", 0, _bi)] + doc[doc.index("</w:p>", _bi) + len("</w:p>"):]

# RESULTS starts a new page. Add pageBreakBefore to the heading and strip the
# page break from the standalone break-paragraph before it, so the break is on
# the heading itself (cleaner; combined with the concise Project Info values in
# docx_fill this keeps INPUTS+PROJECT INFO on one page and avoids a blank page).
_ri = doc.index("RESULTS</w:t>")
_rp = doc.rfind("<w:p ", 0, _ri)
_pb = doc.rfind('<w:br w:type="page"/>', 0, _rp)
if _pb != -1:
    doc = doc[:_pb] + doc[_pb + len('<w:br w:type="page"/>'):]
_ri = doc.index("RESULTS</w:t>")
_rp = doc.rfind("<w:p ", 0, _ri)
doc = doc[:_rp] + doc[_rp:_ri].replace(
    '<w:pStyle w:val="Heading1"/>',
    '<w:pStyle w:val="Heading1"/><w:pageBreakBefore/>', 1) + doc[_ri:]

a = doc.index("460 W Module")                           # Results table -> marker
cover_end = doc.index("</w:tbl>") + len("</w:tbl>")
doc = (doc[:doc.rfind("<w:tbl>", 0, a)]
       + '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>«RESULTS_TABLE»</w:t></w:r></w:p>'
       + doc[doc.index("</w:tbl>", a) + len("</w:tbl>"):])

# ── header: PROJECT ID (split runs), PROJECT name, DATE; merge REVISION runs ──
hdr = re.sub(r"(PROJECT ID:.*?)<w:t>2</w:t>(.*?)<w:t>59-034</w:t>",
             r"\1<w:t></w:t>\2<w:t>«PROJECT_ID»</w:t>", hdr, count=1, flags=re.DOTALL)
hdr = rep(hdr, "<w:t>Franklin Solar, LLC</w:t>", "<w:t>«PROJECT_NAME»</w:t>", 1)
hdr = rep(hdr, "<w:t>10/09/2025</w:t>", "<w:t>«REPORT_DATE»</w:t>", 1)
hdr = hdr.replace("<w:t>REVISIO</w:t>", "<w:t>REVISION</w:t>", 1).replace("<w:t>N</w:t>", "<w:t></w:t>")
# The header REVISION area was a static 0|1|2|3 legend grid; the four value cells
# are merged into ONE gridSpan-4 cell holding «REVISION» so the header shows the
# actual filed revision number (works for any revision, not just 0-3). Applied by
# direct patch on the built template (the Franklin source is gone); mirror it here
# if the template is ever rebuilt from source.

# ── Jost-metric fit: shrink header font (fits fixed noWrap cells) ──
for a_, b_ in [('16', '13'), ('20', '16'), ('18', '15'), ('28', '24')]:
    hdr = hdr.replace(f'<w:sz w:val="{a_}"/>', f'<w:sz w:val="{b_}"/>') \
             .replace(f'<w:szCs w:val="{a_}"/>', f'<w:szCs w:val="{b_}"/>')

# ── Jost-metric fit: shrink cover-region explicit sizes ~18% ──
def shrink(m):
    return f'{m.group(1)}"{max(12, round(int(m.group(2)) * 0.80))}"'
doc = re.sub(r'(<w:sz w:val=)"(\d+)"', shrink, doc[:cover_end]) \
      + doc[cover_end:]
# grow cover text frames so no line (e.g. "Check:") clips
doc = doc.replace("<a:noAutofit/>", "<a:spAutoFit/>") \
         .replace("<v:textbox>", '<v:textbox style="mso-fit-shape-to-text:t">')

(U / "document.xml").write_text(doc, encoding="utf-8")
(U / "header1.xml").write_text(hdr, encoding="utf-8")

# ── Fonts -> Jost everywhere (attribute values only; ArialMT before Arial) ──
FONTS = [("ArialMT", "Jost"), ("Arial", "Jost"), ("Aptos Narrow", "Jost"),
         ("Aptos", "Jost"), ("Calibri Light", "Jost"), ("Calibri", "Jost"),
         ("Times New Roman", "Jost")]
for xml in U.glob("*.xml"):
    t = xml.read_text(encoding="utf-8")
    for old, new in FONTS:
        t = t.replace(f'"{old}"', f'"{new}"')
    xml.write_text(t, encoding="utf-8")
print("template tokenized + Jost + fit adjustments applied")
