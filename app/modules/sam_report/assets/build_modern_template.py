"""
Build assets/report_template_modern.docx from assets/NewCover.docm (the firm's
modern AmpCalc-style report shell) + the section body of the classic template.

This is a BUILD-TIME tool (not imported by the app). What it does:
  1. Flattens every DOCPROPERTY field to a literal «TOKEN» run. The .docm drives
     its cover/letterhead from doc properties (old Bagby project data); if the
     fields survived, the PDF converter's field-update pass would revert every
     value back to Bagby. Flattening removes the field chrome and keeps the
     first result run's formatting.
  2. Replaces the placeholder body ("1  BODY.HEADING.SECTION.TITLE" / "Body.Text"
     + the sample Load-Factors table and charts) with OUR seven report sections,
     transplanted from the classic template and restyled onto the modern styles
     (Heading1 -> BodyHeadingSectionTitle with manual "N<tab>" numbers,
     BodyTextIndent3/unstyled -> BodyText), keeping every «TOKEN» and the
     «RESULTS_TABLE» marker.
  3. Authors a real TOC field (the .docm had only the gold heading textbox) and
     explicit page breaks (after the TOC, before the red back-cover sdt).
  4. Letterhead: Project/Project ID -> tokens, Subject -> "SAM Analysis",
     revision row keeps its 0-6 legend and the report date is printed under the
     active revision column via «REV_DATE_0»..«REV_DATE_6» (+ «REV_LAST» so a
     revision > 6 still renders).
  5. Fonts -> Jost everywhere (Normal was Calibri, theme was Aptos), keeping
     "Jost SemiBold" (cover title) intact.
  6. .docm -> .docx: the zip has NO macros; only the document content-type line
     differs.

Run:  PYTHONUTF8=1 python build_modern_template.py
Reads  assets/NewCover.docm + assets/report_template.docx
Writes assets/report_template_modern.docx
"""
from __future__ import annotations

import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "NewCover.docm"
CLASSIC = HERE / "report_template.docx"
OUT = HERE / "report_template_modern.docx"

# Blank header for the cover + TOC section, so the letterhead grid never lands
# on the Table-of-Contents page.
HEADER_EMPTY = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:p/></w:hdr>'
)


def rep(t: str, old: str, new: str, expect: int) -> str:
    if t.count(old) != expect:
        raise SystemExit(f"expected {expect}x {old!r}, found {t.count(old)}")
    return t.replace(old, new)


def _first_run_rpr(fragment: str) -> str:
    run = re.search(r"<w:r\b[^>]*>.*?</w:r>", fragment, re.DOTALL)
    if not run:
        return ""
    rpr = re.search(r"<w:rPr>.*?</w:rPr>", run.group(0), re.DOTALL)
    return rpr.group(0) if rpr else ""


def _literal_run(rpr: str, value: str) -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{value}</w:t></w:r>'


def flatten_fldsimple(xml: str, prop: str, value: str, expect: int) -> str:
    """Replace <w:fldSimple w:instr="DOCPROPERTY prop ..">…</w:fldSimple> with a
    literal run carrying the first result run's formatting and `value`."""
    out, n, pos = [], 0, 0
    pat = re.compile(r'<w:fldSimple[^>]*w:instr="([^"]*)"[^>]*>(.*?)</w:fldSimple>',
                     re.DOTALL)
    for m in pat.finditer(xml):
        if "DOCPROPERTY" not in m.group(1) or prop not in m.group(1):
            continue
        out.append(xml[pos:m.start()])
        out.append(_literal_run(_first_run_rpr(m.group(2)), value))
        pos = m.end()
        n += 1
    out.append(xml[pos:])
    if n != expect:
        raise SystemExit(f"fldSimple {prop!r}: expected {expect}, flattened {n}")
    return "".join(out)


def flatten_fldchar(xml: str, prop: str, value: str | None, expect: int) -> str:
    """Flatten a begin/separate/end DOCPROPERTY field. value=None keeps the
    cached result runs verbatim (static firm data); else they're replaced by one
    literal run with `value`."""
    n = 0
    while True:
        found = None
        for m in re.finditer(r"<w:instrText[^>]*>([^<]*)</w:instrText>", xml):
            if "DOCPROPERTY" in m.group(1) and prop in m.group(1):
                found = m
                break
        if found is None:
            break
        ipos = found.start()
        begin_fld = xml.rfind('<w:fldChar w:fldCharType="begin"', 0, ipos)
        begin_run = max(xml.rfind("<w:r>", 0, begin_fld), xml.rfind("<w:r ", 0, begin_fld))
        sep_fld = xml.find('fldCharType="separate"', ipos)
        end_fld = xml.find('<w:fldChar w:fldCharType="end"', ipos)
        if begin_fld == -1 or sep_fld == -1 or end_fld == -1 or sep_fld > end_fld:
            raise SystemExit(f"fldChar {prop!r}: malformed field structure")
        sep_run_close = xml.find("</w:r>", sep_fld) + len("</w:r>")
        end_run_start = max(xml.rfind("<w:r>", 0, end_fld), xml.rfind("<w:r ", 0, end_fld))
        end_run_close = xml.find("</w:r>", end_fld) + len("</w:r>")
        result = xml[sep_run_close:end_run_start]
        if value is None:
            replacement = result           # keep cached literal, drop field chrome
        else:
            replacement = _literal_run(_first_run_rpr(result), value)
        xml = xml[:begin_run] + replacement + xml[end_run_close:]
        n += 1
    if n != expect:
        raise SystemExit(f"fldChar {prop!r}: expected {expect}, flattened {n}")
    return xml


# ─────────────────────────────────────────────────────────────────────────────
z = zipfile.ZipFile(SRC)
names = z.namelist()
assert not any("vbaProject" in n for n in names), "unexpected macros in .docm"
doc = z.read("word/document.xml").decode("utf-8")
hdr = z.read("word/header1.xml").decode("utf-8")
numbering = z.read("word/numbering.xml").decode("utf-8")
ctypes = z.read("[Content_Types].xml").decode("utf-8")
core = z.read("docProps/core.xml").decode("utf-8")
settings = z.read("word/settings.xml").decode("utf-8")

# ── 1. document.xml: flatten cover fields to tokens (x2 = Choice + Fallback) ──
for prop, tok in [
    ("Subject", "«PROJECT_TITLE»"),
    ("Owner_Name", "«OWNER_NAME»"),
    ("Designed by", "«DESIGNER»"),
    ("Checked by", "«CHECKER»"),
    ("EPC_Name", "«EPC_NAME»"),
    ("EPC_Phone", "«EPC_PHONE»"),
    ("Date completed", "«REPORT_DATE»"),
    ("Engineer_Name", "«ENG_NAME»"),
    ("Engineer_Phone", "«ENG_PHONE»"),
]:
    doc = flatten_fldsimple(doc, prop, tok, 2)
doc = flatten_fldchar(doc, "Owner_Phone", "«OWNER_PHONE»", 2)
doc = flatten_fldchar(doc, "Engineer_Email", None, 2)     # static firm data stays
doc = flatten_fldchar(doc, "Engineer_Address", None, 2)
assert "DOCPROPERTY" not in doc, "document.xml still has live DOCPROPERTY fields"

# ── 2. classic body slice -> modern styles ──
classic = zipfile.ZipFile(CLASSIC).read("word/document.xml").decode("utf-8")
h1 = classic.find('<w:pStyle w:val="Heading1"/>')
start = max(classic.rfind("<w:p ", 0, h1), classic.rfind("<w:p>", 0, h1))
sect = classic.rfind("<w:sectPr")
end = classic.rfind("</w:p>", 0, sect) + len("</w:p>")
sec = classic[start:end]

# split around the Project Information table so its cell paragraphs are untouched
assert sec.count("<w:tbl>") == 1, "expected exactly the Project Info table in slice"
t0, t1 = sec.find("<w:tbl>"), sec.find("</w:tbl>") + len("</w:tbl>")
before, table, after = sec[:t0], sec[t0:t1], sec[t1:]

PARA = re.compile(r"<w:p\b[^>]*/>|<w:p\b[^>]*>.*?</w:p>", re.DOTALL)

def _restyle(part: str) -> str:
    def fix(m: re.Match) -> str:
        p = m.group(0)
        if "«RESULTS_TABLE»" in p or p.endswith("/>"):
            return p                                   # marker ¶ / empty self-closed
        if "<w:numPr>" not in p:
            # drop direct indents so every body ¶ takes BodyText's uniform indent
            # (classic mixed indented/flush direct formatting; the modern design
            # indents body text under flush headings). Lists keep numbering ind.
            p = re.sub(r"<w:ind [^>]*/>", "", p)
        if 'w:val="Heading1"' in p:
            if "<w:t" not in p:
                # the empty Heading1 spacer holds the page break before RESULTS —
                # keep the break as a plain paragraph (no heading style, so it
                # stays out of the TOC and the numbering pass)
                if 'w:type="page"' in p:
                    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
                return ""
            return p                                   # numbered later, whole-slice pass
        if "<w:pStyle" in p:
            return p                                   # BodyTextIndent3 handled globally
        if "<w:pPr>" in p:
            return p.replace("<w:pPr>", '<w:pPr><w:pStyle w:val="BodyText"/>', 1)
        m2 = re.match(r"(<w:p\b[^>]*>)", p)
        return p.replace(m2.group(1), m2.group(1) + '<w:pPr><w:pStyle w:val="BodyText"/></w:pPr>', 1)
    return PARA.sub(fix, part)

before, after = _restyle(before), _restyle(after)
sec = before + table + after
sec = sec.replace('<w:pStyle w:val="BodyTextIndent3"/>', '<w:pStyle w:val="BodyText"/>')
assert 'BodyTextIndent3' not in sec

# headings: Heading1 -> BodyHeadingSectionTitle + manual "N<tab>" + outlineLvl 0
sec = sec.replace('<w:pStyle w:val="Heading1"/>', '<w:pStyle w:val="BodyHeadingSectionTitle"/>')
count = [0]
def _mark_heading(m: re.Match) -> str:
    p = m.group(0)
    if 'w:val="BodyHeadingSectionTitle"' not in p:
        return p
    count[0] += 1
    # outlineLvl goes in pPr, before its rPr if present (schema order) — kept so
    # Word's live TOC still collects these headings. No section numbers (matches
    # the engineer's TwoBlues reference).
    ppr_end = p.find("</w:pPr>")
    rpr_in_ppr = p.find("<w:rPr>", 0, ppr_end)
    ins = rpr_in_ppr if rpr_in_ppr != -1 else ppr_end
    return p[:ins] + '<w:outlineLvl w:val="0"/>' + p[ins:]
sec = PARA.sub(_mark_heading, sec)
assert count[0] == 7, f"expected 7 section headings, found {count[0]}"

# the classic equation picture references rId8, which in the MODERN package is
# the cover photo — remap to a fresh relationship + carry the image over
sec = rep(sec, 'r:embed="rId8"', 'r:embed="rId901"', 1)
EQ_IMAGE = zipfile.ZipFile(CLASSIC).read("word/media/image1.png")

# ── 3. body surgery: placeholder region -> TOC field + breaks + our sections ──
cut_from = doc.index('<w:p w14:paraId="5A83BCF7"')
assert doc.count("<w:sdt>") == 1
cut_to = doc.index("<w:sdt>")
# The TOC is a real Word field (Word refreshes it live on open), but LibreOffice —
# the Azure converter — does NOT refresh a content index on headless convert, so
# it renders the field's CACHED RESULT. We therefore bake the actual entries in as
# that cached result (the SAM report's 7 sections are fixed, and its page geometry
# is stable — Results is always ~1 page of a 26-row table). Page numbers here match
# what Word computes; Word overwrites them anyway when a user opens the .docx.
_TOC_RPR = '<w:rPr><w:rFonts w:ascii="Jost" w:hAnsi="Jost"/></w:rPr>'
_TOC_PPR = ('<w:pPr><w:pStyle w:val="TOC1"/>'
            '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="11239"/></w:tabs>'
            f'{_TOC_RPR}</w:pPr>')
# (title, page) — headings are unnumbered, so the TOC entries are too.
_TOC_ENTRIES = [
    ("DESCRIPTION / PURPOSE", "2"), ("METHOD OF ANALYSIS", "2"),
    ("INPUTS", "2"), ("PROJECT INFORMATION", "3"),
    ("RESULTS", "4"), ("CONCLUSION", "4"), ("APPENDIX", "5"),
]


def _toc_paragraph(title: str, page: str, first: bool, last: bool) -> str:
    # \u collects outline-level paragraphs (our headings use BodyHeadingSectionTitle
    # + outlineLvl 0, so \o alone would miss them).
    begin = ('<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
             '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-1" \\h \\z \\u </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r>') if first else ""
    end = '<w:r><w:fldChar w:fldCharType="end"/></w:r>' if last else ""
    body = (f'<w:r>{_TOC_RPR}<w:t xml:space="preserve">{title}</w:t></w:r>'
            f'<w:r>{_TOC_RPR}<w:tab/></w:r><w:r>{_TOC_RPR}<w:t>{page}</w:t></w:r>')
    return f'<w:p>{_TOC_PPR}{begin}{body}{end}</w:p>'


TOC_FIELD = "".join(
    _toc_paragraph(t, p, first=(i == 0), last=(i == len(_TOC_ENTRIES) - 1))
    for i, (t, p) in enumerate(_TOC_ENTRIES))
PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
# The cover + TOC form their own section whose default header is BLANK (rId950 ->
# the new empty header part), so the letterhead grid never lands on the TOC page.
# A section-break paragraph carrying that sectPr sits between the TOC and the body;
# the body keeps the grid via the final (section-2) sectPr. Section 1 copies the
# main section properties verbatim (keeps titlePg -> the cover stays header/footer
# free on page 1) and only swaps its default header to the empty part.
main_sect = re.search(r"<w:sectPr\b.*?</w:sectPr>", doc, re.DOTALL).group(0)
assert 'r:id="rId950"' not in main_sect
sect1 = main_sect.replace('r:id="rId15"', 'r:id="rId950"', 1)
SECTION_BREAK = f'<w:p><w:pPr>{sect1}</w:pPr></w:p>'
doc = doc[:cut_from] + TOC_FIELD + SECTION_BREAK + sec + PAGE_BREAK + doc[cut_to:]
# Section 2 (body) drops titlePg so its very first page also shows the grid, and
# drops pgNumType so it CONTINUES the page count from section 1 instead of
# restarting at 0 (page numbering must stay continuous cover->appendix).
last = doc.rfind("<w:sectPr")
tail = (doc[last:].replace("<w:titlePg/>", "", 1)
        .replace('<w:pgNumType w:start="0"/>', "", 1))
doc = doc[:last] + tail

# sample charts (rId12/rId13) lived only in the deleted region -> drop media+rels
assert doc.count("rId12") == 0 and doc.count("rId13") == 0
rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
for rid in ("rId12", "rId13"):
    rels = re.sub(rf'<Relationship Id="{rid}"[^>]*/>', "", rels, count=1)
assert 'Id="rId901"' not in rels
assert 'Id="rId950"' not in rels
rels = rels.replace("</Relationships>",
                    '<Relationship Id="rId901" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                    'Target="media/imageEq1.png"/>'
                    '<Relationship Id="rId950" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
                    'Target="headerEmpty.xml"/></Relationships>')
DROP = {"word/media/image3.png", "word/media/image4.jpeg"}

# The cover's PE-seal stamp is media/image2.png (a floating "[DATE] / Signature /
# <seal>" image). Word + newer LibreOffice don't render it, but Azure's older
# LibreOffice (25.2) does, so it stamped every PDF. Replacing the bytes with a
# 1x1 transparent PNG makes it render as nothing everywhere — no XML surgery, so
# the drawing anchor (referenced from both the DrawingML Choice and VML Fallback)
# stays valid. Base64 of a 1x1 fully transparent PNG:
import base64
STAMP_IMAGE = "word/media/image2.png"
TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# ── 4. numbering: bring over the classic Inputs list definitions ──
cnum = zipfile.ZipFile(CLASSIC).read("word/numbering.xml").decode("utf-8")
m = re.search(r'<w:abstractNum w:abstractNumId="26".*?</w:abstractNum>', cnum, re.DOTALL)
n12 = re.search(r'<w:num w:numId="12".*?</w:num>', cnum, re.DOTALL)
assert m and n12, "classic numbering definitions not found"
assert 'w:abstractNumId="26"' not in numbering and 'w:numId="12"' not in numbering
first_num = numbering.find("<w:num ")
numbering = numbering[:first_num] + m.group(0) + numbering[first_num:]
numbering = numbering.replace("</w:numbering>", n12.group(0) + "</w:numbering>")

# ── 5. header1.xml: letterhead tokens + revision/date grid ──
hdr = flatten_fldchar(hdr, "Subject", "«PROJECT_NAME»", 1)
hdr = flatten_fldsimple(hdr, "Project", "«PROJECT_ID»", 1)
assert "DOCPROPERTY" not in hdr
hdr = rep(hdr, "Underground Circuit Thermal Ampacity Study", "SAM Analysis", 1)

DATE_PARA_IDS = ["22124233", "31826DE1", "73BE11EC", "5DC28BFC",
                 "0B812921", "75137DA6", "07C83F05"]
last = -1
for i, pid in enumerate(DATE_PARA_IDS):
    at = hdr.find(f'w14:paraId="{pid}"')
    assert at > last, f"date cell {pid} missing or out of order"
    last = at
    close = hdr.index("</w:p>", at)
    # 7pt so MM/DD/YYYY fits the ~0.75" revision column on one line
    hdr = (hdr[:close]
           + f'<w:r><w:rPr><w:sz w:val="14"/><w:szCs w:val="14"/></w:rPr>'
             f'<w:t>«REV_DATE_{i}»</w:t></w:r>'
           + hdr[close:])
    # center the date under its revision column
    ppr_end = hdr.index("</w:pPr>", at)
    rpr = hdr.find("<w:rPr>", at, ppr_end)
    ins = rpr if rpr != -1 else ppr_end
    hdr = hdr[:ins] + '<w:jc w:val="center"/>' + hdr[ins:]

# legend's last cell "6" -> «REV_LAST» (renders "6" unless revision > 6)
r_leg = hdr.index("<w:t>Revision:</w:t>")
r_date = hdr.index("<w:t>Date:</w:t>")
legend = hdr[r_leg:r_date]
legend = rep(legend, "<w:t>6</w:t>", "<w:t>«REV_LAST»</w:t>", 1)
hdr = hdr[:r_leg] + legend + hdr[r_date:]

# ── 6. package-wide passes ──
ctypes = rep(ctypes,
             "application/vnd.ms-word.document.macroEnabled.main+xml",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml", 1)
ctypes = ctypes.replace(
    "</Types>",
    '<Override PartName="/word/headerEmpty.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    "</Types>")
core = re.sub(r"<dc:title>[^<]*</dc:title>", "<dc:title>SAM Report</dc:title>", core)
core = re.sub(r"<dc:subject>[^<]*</dc:subject>", "<dc:subject>SAM Report</dc:subject>", core)
settings = re.sub(r"<w:documentProtection[^>]*/>", "", settings)
# Make LibreOffice (the Azure converter) refresh the TOC field on load, so the
# generated PDF shows real entries + page numbers instead of the placeholder run.
if "<w:updateFields" not in settings:
    si = settings.find(">", settings.find("<w:settings")) + 1
    settings = settings[:si] + '<w:updateFields w:val="true"/>' + settings[si:]

# ── 7. fonts + styles ──
# FIDELITY POLICY: do NOT touch the .docm's Calibri references. The cover's
# layout hangs off paragraph-relative anchors whose empty paragraphs get their
# line height from Normal (= Calibri); swapping fonts reflows the cover and
# spills its lower blocks onto page 2. All *visible* template text already uses
# explicit Jost/Jost SemiBold styles. On Azure, LibreOffice substitutes Carlito
# (metric-identical to Calibri; installed by the Dockerfile) so the geometry
# matches Word exactly. Only the THEME latin fonts (Aptos) map to Jost, so the
# transplanted classic sections' theme-inheriting runs render Jost everywhere.
theme = z.read("word/theme/theme1.xml").decode("utf-8")
theme = re.sub(r'<a:latin typeface="Aptos[^"]*"', '<a:latin typeface="Jost"', theme)

# Word regenerates TOC entries with the TOC1 style; define it (Jost) so the
# generated entries match the report instead of falling back to Calibri.
styles = z.read("word/styles.xml").decode("utf-8")
assert 'w:styleId="TOC1"' not in styles
styles = styles.replace(
    "</w:styles>",
    '<w:style w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/>'
    '<w:basedOn w:val="Normal"/><w:autoRedefine/><w:uiPriority w:val="39"/>'
    '<w:unhideWhenUsed/><w:pPr><w:spacing w:after="100"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Jost" w:hAnsi="Jost"/><w:sz w:val="22"/></w:rPr>'
    "</w:style></w:styles>")

# ── Formatting to match the engineer's reference (TwoBlues.docx) ──
# Full paragraph-style-definition swaps (unique; the linked *Char styles are left
# alone since our headings/body apply these as PARAGRAPH styles).
#
# Cover title + "SAM REPORT" (Title style) -> WHITE so they read on the red banner.
styles = rep(styles,
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:link w:val="TitleChar"/><w:autoRedefine/><w:uiPriority w:val="10"/>'
    '<w:qFormat/><w:rsid w:val="000B74DE"/><w:pPr><w:spacing w:after="80"/><w:contextualSpacing/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Jost SemiBold" w:eastAsiaTheme="majorEastAsia" w:hAnsi="Jost SemiBold" '
    'w:cstheme="majorBidi"/><w:b/><w:color w:val="000000" w:themeColor="text1"/><w:spacing w:val="20"/>'
    '<w:kern w:val="28"/><w:sz w:val="48"/><w:szCs w:val="56"/><w14:ligatures w14:val="standardContextual"/></w:rPr></w:style>',
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:link w:val="TitleChar"/><w:autoRedefine/><w:uiPriority w:val="10"/>'
    '<w:qFormat/><w:rsid w:val="000B74DE"/><w:pPr><w:spacing w:after="80"/><w:contextualSpacing/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Jost SemiBold" w:eastAsiaTheme="majorEastAsia" w:hAnsi="Jost SemiBold" '
    'w:cstheme="majorBidi"/><w:b/><w:color w:val="FFFFFF" w:themeColor="background1"/><w:spacing w:val="20"/>'
    '<w:kern w:val="28"/><w:sz w:val="48"/><w:szCs w:val="56"/><w14:ligatures w14:val="standardContextual"/></w:rPr></w:style>', 1)
# Section headings: 16pt, slate accent, space above, keep-with-next, no underline.
styles = rep(styles,
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BodyHeadingSectionTitle"><w:name w:val="Body.Heading.Section.Title"/>'
    '<w:basedOn w:val="Normal"/><w:link w:val="BodyHeadingSectionTitleChar"/><w:autoRedefine/><w:qFormat/>'
    '<w:rsid w:val="006C49F6"/><w:rPr><w:rFonts w:ascii="Jost" w:eastAsia="Arial" w:hAnsi="Jost"/><w:caps/><w:noProof/><w:u w:val="single"/></w:rPr></w:style>',
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BodyHeadingSectionTitle"><w:name w:val="Body.Heading.Section.Title"/>'
    '<w:basedOn w:val="Normal"/><w:link w:val="BodyHeadingSectionTitleChar"/><w:autoRedefine/><w:qFormat/>'
    '<w:rsid w:val="006C49F6"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="80"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Jost" w:eastAsia="Arial" w:hAnsi="Jost"/><w:caps/><w:noProof/>'
    '<w:color w:val="0F4761" w:themeColor="accent1"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>', 1)
# Body paragraphs: 11pt, justified, 6pt before/after, flush (drop the 0.5" indent).
styles = rep(styles,
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BodyText"><w:name w:val="Body.Text"/><w:basedOn w:val="Normal"/>'
    '<w:link w:val="BodyTextChar"/><w:autoRedefine/><w:qFormat/><w:rsid w:val="006C49F6"/>'
    '<w:pPr><w:ind w:left="720"/></w:pPr><w:rPr><w:rFonts w:ascii="Jost" w:hAnsi="Jost"/></w:rPr></w:style>',
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BodyText"><w:name w:val="Body.Text"/><w:basedOn w:val="Normal"/>'
    '<w:link w:val="BodyTextChar"/><w:autoRedefine/><w:qFormat/><w:rsid w:val="006C49F6"/>'
    '<w:pPr><w:spacing w:before="120" w:after="120"/><w:jc w:val="both"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Jost" w:hAnsi="Jost"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>', 1)

# ── 8. write + validate ──
edited = {
    "word/document.xml": doc,
    "word/header1.xml": hdr,
    "word/numbering.xml": numbering,
    "word/styles.xml": styles,
    "word/theme/theme1.xml": theme,
    "[Content_Types].xml": ctypes,
    "docProps/core.xml": core,
    "word/settings.xml": settings,
    "word/_rels/document.xml.rels": rels,
}
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
    for item in z.infolist():
        if item.filename in DROP:
            continue
        data = edited.get(item.filename)
        if data is not None:
            ET.fromstring(data)               # well-formedness gate
            out.writestr(item, data.encode("utf-8"))
        elif item.filename == STAMP_IMAGE:
            out.writestr(item, TRANSPARENT_PNG)   # blank out the PE-seal stamp
        else:
            out.writestr(item, z.read(item.filename))
    out.writestr("word/media/imageEq1.png", EQ_IMAGE)
    ET.fromstring(HEADER_EMPTY)                    # well-formedness gate
    out.writestr("word/headerEmpty.xml", HEADER_EMPTY.encode("utf-8"))
OUT.write_bytes(buf.getvalue())

final = zipfile.ZipFile(OUT)
fdoc = final.read("word/document.xml").decode("utf-8")
fhdr = final.read("word/header1.xml").decode("utf-8")
for bad in ("BAGBY", "Generate Capital", "Recon Corporation", "Body.Heading",
            "Load Factor", "DOCPROPERTY", "Avinash", "December 9, 2025"):
    assert bad not in fdoc and bad not in fhdr, f"leftover source text: {bad}"
toks = set(re.findall(r"«[A-Z0-9_]+»", fdoc)) | set(re.findall(r"«[A-Z0-9_]+»", fhdr))
expected = {"«PROJECT_TITLE»", "«PROJECT_NAME»", "«PROJECT_ID»", "«REPORT_DATE»",
            "«OWNER_NAME»", "«OWNER_PHONE»", "«EPC_NAME»", "«EPC_PHONE»",
            "«ENG_NAME»", "«ENG_PHONE»", "«DESIGNER»", "«CHECKER»",
            "«COORDINATES»", "«GCR»", "«MODULES_PER_STRING»", "«MODULE_MODEL»",
            "«INVERTER_MODEL»", "«DC_AC_RATIO»", "«SYSTEM_SIZE»", "«ALBEDO_TEXT»",
            "«WEATHER_FILE»", "«MODULES_PHRASE»", "«NUM_RUNS»", "«YEAR_START»",
            "«YEAR_END»", "«YEAR_RANGE»", "«RESULTS_TABLE»", "«REV_LAST»",
            *(f"«REV_DATE_{i}»" for i in range(7))}
missing, extra = expected - toks, toks - expected
assert not missing and not extra, f"token mismatch: missing={missing} extra={extra}"
print(f"OK -> {OUT.name} ({OUT.stat().st_size:,} bytes), {len(toks)} tokens, "
      f"7 sections, TOC field + page breaks in place")
