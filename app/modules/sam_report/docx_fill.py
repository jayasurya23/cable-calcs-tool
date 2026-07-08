"""
Fill the Word template (assets/report_template.docx) with report data.

The template carries «TOKEN» placeholders (tokenized from the real Franklin
report) and a «RESULTS_TABLE» marker paragraph. We do string substitution on
word/document.xml + word/header1.xml inside the .docx zip and inject a generated
Results table. Fonts are already Jost in the template.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from .report_models import ReportContext

TEMPLATE_PATH = Path(__file__).parent / "assets" / "report_template.docx"

# Results table column widths (dxa) per module: Year | 3-hr-avg Isc | Max Voc.
_COL_YEAR, _COL_ISC, _COL_VOC = 706, 800, 1012
_MOD_WIDTH = _COL_YEAR + _COL_ISC + _COL_VOC
_BLUE, _YELLOW = "00B0F0", "FFFF00"


def _esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _modules_phrase(n: int) -> str:
    """RESULTS intro: 'module' for one, else 'three different modules'."""
    if n == 1:
        return "module"
    return f"{_COUNT_WORDS.get(n, n)} different modules"


def _tokens(ctx: ReportContext) -> dict[str, str]:
    p = ctx.project
    return {
        "«PROJECT_TITLE»": _esc(p.project_name.upper()),
        "«PROJECT_NAME»": _esc(p.project_name),
        "«PROJECT_ID»": _esc(p.project_id),
        "«REPORT_DATE»": _esc(p.date),
        "«OWNER_NAME»": _esc(p.owner_name),
        "«OWNER_PHONE»": _esc(p.owner_phone),
        "«EPC_NAME»": _esc(p.epc_name),
        "«EPC_PHONE»": _esc(p.epc_phone),
        "«ENG_NAME»": _esc(p.eng_firm_name),
        "«ENG_PHONE»": _esc(p.eng_firm_phone),
        "«EOR»": _esc(p.eor),
        "«DESIGNER»": _esc(p.designer),
        "«CHECKER»": _esc(p.checker),
        "«COORDINATES»": _esc(p.coordinates),
        "«GCR»": _esc(p.gcr),
        "«MODULES_PER_STRING»": _esc(p.modules_per_string),
        "«MODULE_MODEL»": _esc(p.module_model),
        "«INVERTER_MODEL»": _esc(p.inverter_model),
        "«DC_AC_RATIO»": _esc(p.dc_ac_ratio),
        "«SYSTEM_SIZE»": _esc(p.system_size_dc),
        "«ALBEDO_TEXT»": _esc(p.albedo_text),
        "«WEATHER_FILE»": _esc(p.weather_file),
        "«REVISION»": _esc(p.revision),
        "«MODULES_PHRASE»": _esc(_modules_phrase(len(ctx.modules))),
        "«NUM_RUNS»": _esc(ctx.num_runs),
        "«YEAR_START»": _esc(ctx.year_start if ctx.year_start is not None else ""),
        "«YEAR_END»": _esc(ctx.year_end if ctx.year_end is not None else ""),
        "«YEAR_RANGE»": _esc(ctx.year_range),
    }


# ── Results table XML generation ─────────────────────────────────────────────

_BORDERS = ('<w:tcBorders>'
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '</w:tcBorders>')


def _cell(text: str, width: int, *, fill: str | None = None, bold: bool = False,
          gridspan: int = 1, sz: int = 15) -> str:
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    gs = f'<w:gridSpan w:val="{gridspan}"/>' if gridspan > 1 else ""
    rpr = (f'<w:rPr><w:rFonts w:ascii="Jost" w:hAnsi="Jost" w:cs="Jost"/>'
           f'{"<w:b/>" if bold else ""}<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>')
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{gs}{_BORDERS}{shd}'
            f'<w:vAlign w:val="center"/></w:tcPr>'
            f'<w:p><w:pPr><w:jc w:val="center"/>{rpr}</w:pPr>'
            f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r></w:p></w:tc>')


def _row(cells: str) -> str:
    return f'<w:tr><w:trPr><w:jc w:val="center"/></w:trPr>{cells}</w:tr>'


def results_table_xml(ctx: ReportContext) -> str:
    modules = ctx.modules
    if not modules:
        return '<w:p/>'
    nrows = max(len(m.rows) for m in modules)

    grid = "".join(f'<w:gridCol w:w="{w}"/>'
                   for _ in modules for w in (_COL_YEAR, _COL_ISC, _COL_VOC))
    total_w = _MOD_WIDTH * len(modules)

    # Row 1: module labels (blue, gridSpan 3).
    r1 = "".join(_cell(m.label, _MOD_WIDTH, fill=_BLUE, bold=True, gridspan=3, sz=18)
                 for m in modules)
    # Row 2: sub-headers (yellow).
    r2 = "".join(
        _cell("Year", _COL_YEAR, fill=_YELLOW, bold=True)
        + _cell("Max of 3 Hr Avg Isc", _COL_ISC, fill=_YELLOW, bold=True)
        + _cell("Max Voc", _COL_VOC, fill=_YELLOW, bold=True)
        for _ in modules)
    # Data rows (pad shorter modules with blank cells so years never drop).
    data_rows = []
    for i in range(nrows):
        cells = []
        for m in modules:
            if i < len(m.rows):
                r = m.rows[i]
                cells.append(_cell(str(r.year), _COL_YEAR)
                             + _cell(f"{r.isc_3hr_avg:.2f}", _COL_ISC)
                             + _cell(f"{r.voc:.2f}", _COL_VOC))
            else:
                cells.append(_cell("", _COL_YEAR) + _cell("", _COL_ISC) + _cell("", _COL_VOC))
        data_rows.append(_row("".join(cells)))
    # Max row (yellow).
    rmax = "".join(
        _cell("Max", _COL_YEAR, fill=_YELLOW, bold=True)
        + _cell(f"{m.max_isc_3hr:.2f}", _COL_ISC, fill=_YELLOW, bold=True)
        + _cell(f"{m.max_voc:.2f}", _COL_VOC, fill=_YELLOW, bold=True)
        for m in modules)

    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>'
        f'<w:jc w:val="center"/>'
        f'<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" '
        f'w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
        f'<w:tblGrid>{grid}</w:tblGrid>'
        f'{_row(r1)}{_row(r2)}{"".join(data_rows)}{_row(rmax)}</w:tbl>'
    )


def _replace_marker(doc_xml: str, table_xml: str) -> str:
    """Replace the whole <w:p> that holds «RESULTS_TABLE» with the generated table.

    The marker's own paragraph start is the CLOSEST "<w:p>"/"<w:p " before the
    marker — take max() of both, otherwise "<w:p " (attributed paragraphs) would
    match the *preceding* paragraph and delete it (e.g. the RESULTS intro line).
    """
    idx = doc_xml.index("«RESULTS_TABLE»")
    p_start = max(doc_xml.rfind("<w:p ", 0, idx), doc_xml.rfind("<w:p>", 0, idx))
    p_end = doc_xml.index("</w:p>", idx) + len("</w:p>")
    return doc_xml[:p_start] + table_xml + doc_xml[p_end:]


_TOKEN_RE = re.compile(r"«[A-Z_]+»")


def _apply_tokens(text: str, tokens: dict[str, str]) -> str:
    """Single-pass token substitution — a value that itself contains a «TOKEN»
    string is never re-scanned, so it can't double-substitute."""
    return _TOKEN_RE.sub(lambda m: tokens.get(m.group(0), m.group(0)), text)


def fill_docx(ctx: ReportContext) -> bytes:
    """Return the filled .docx bytes."""
    tokens = _tokens(ctx)
    table_xml = results_table_xml(ctx)

    src = zipfile.ZipFile(TEMPLATE_PATH, "r")
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename in ("word/document.xml", "word/header1.xml"):
                text = _apply_tokens(data.decode("utf-8"), tokens)
                if item.filename == "word/document.xml":
                    text = _replace_marker(text, table_xml)
                data = text.encode("utf-8")
            out.writestr(item, data)
    src.close()
    return out_buf.getvalue()
