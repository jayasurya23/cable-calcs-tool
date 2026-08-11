"""Multi-module upload: adding modules to an analysis, and how bad input fails.

Regression cover for a launch bug: a file that isn't a real workbook made
openpyxl raise BadZipFile out of add_module. That isn't a ValueError, so the
upload route's handler missed it and the whole multi-module upload 500'd —
losing the good modules along with the bad one.
"""
from __future__ import annotations

import json

import pytest

from app.modules.sam_report import report_service


class _Upload:
    """Minimal stand-in for Starlette's UploadFile (filename + .file)."""

    def __init__(self, path, name=None):
        self.filename = name or path.name
        self.file = open(path, "rb")  # noqa: SIM115 - closed by the test process


@pytest.fixture
def staged(tmp_path, sam_workbook, monkeypatch):
    """An upload staging dir holding the main workbook, like process_upload makes."""
    from app.config import settings

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(settings, "upload_dir", str(uploads))

    token = "testupload01"
    dest = uploads / token
    dest.mkdir()
    (dest / sam_workbook.name).write_bytes(sam_workbook.read_bytes())
    (dest / report_service.UPLOAD_META_FILE).write_text(
        json.dumps({"workbook": sam_workbook.name, "pysam": None}), encoding="utf-8")
    report_service.load_modules(token)          # initialise module 0
    return token, dest


def test_add_module_appends_a_second_module(staged, sam_workbook):
    token, _ = staged

    entries = report_service.add_module(token, _Upload(sam_workbook), "245 W Module")

    assert len(entries) == 2
    assert entries[1]["label"] == "245 W Module"
    assert entries[1]["path"].startswith("modules/")


def test_add_module_rejects_a_file_that_is_not_a_workbook(staged, tmp_path):
    """The failure must be a ValueError (one failure mode for callers), carry a
    message an engineer can act on, and leave no half-staged file behind."""
    token, dest = staged
    junk = tmp_path / "notes.xlsx"
    junk.write_bytes(b"this is plainly not a zip/xlsx")

    with pytest.raises(ValueError) as excinfo:
        report_service.add_module(token, _Upload(junk), "Bad module")

    assert "readable .xlsx" in str(excinfo.value)
    assert report_service.load_modules(token) == report_service.load_modules(token)
    assert len(report_service.load_modules(token)) == 1      # module 0 only
    staged_files = list((dest / "modules").iterdir()) if (dest / "modules").is_dir() else []
    assert staged_files == [], "a rejected upload must not leave a staged file"


def test_add_module_rejects_a_workbook_with_no_sam_runs(staged, tmp_path):
    from openpyxl import Workbook

    token, dest = staged
    empty = tmp_path / "empty.xlsx"
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.save(empty)

    with pytest.raises(ValueError, match="SAM runs export"):
        report_service.add_module(token, _Upload(empty), "Empty")

    assert len(report_service.load_modules(token)) == 1


# ── Datasheet PDF as an alternative to a pysam ──────────────────────────────

def _datasheet_pdf(tmp_path, name, model_lines):
    """A small text-based datasheet PDF, built the way a vendor's would read."""
    import subprocess
    html = tmp_path / f"{name}.html"
    html.write_text("<html><body>" + model_lines + "</body></html>", encoding="utf-8")
    for exe in (r"C:\Program Files\LibreOffice\program\soffice.exe", "soffice"):
        try:
            subprocess.run([exe, "--headless", "--convert-to", "pdf",
                            "--outdir", str(tmp_path), str(html)],
                           check=True, capture_output=True, timeout=120)
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    pdf = tmp_path / f"{name}.pdf"
    if not pdf.is_file():
        pytest.skip("LibreOffice unavailable to build the fixture PDF")
    return pdf


def test_datasheet_identifies_the_module_against_the_cec_database(tmp_path):
    from app.modules.sam_report import datasheet_parser
    pdf = _datasheet_pdf(tmp_path, "qcells",
                         "<h1>Qcells North America</h1><h2>Q.PRO-G3 245</h2>"
                         "<p>Open Circuit Voltage (Voc) 37.56 V</p>"
                         "<p>Short Circuit Current (Isc) 8.85 A</p>")
    found = datasheet_parser.identify(pdf)
    assert found["source"] == "cec"                 # authoritative, not guessed
    assert "Qcells North America" in found["display"]
    assert "245" in found["display"]
    assert found["specs"]["voc"] == 37.56


def test_datasheet_for_an_unknown_module_falls_back_to_its_own_text(tmp_path):
    from app.modules.sam_report import datasheet_parser
    pdf = _datasheet_pdf(tmp_path, "unknown",
                         "<h1>Nonesuch Solar</h1><h2>NS-9999X-777</h2>"
                         "<p>Maximum Power (Pmax) 777 W</p>")
    found = datasheet_parser.identify(pdf)
    assert found["source"] == "text"
    assert "777" in found["display"] or found["specs"].get("pmax") == 777.0


def test_unreadable_pdf_is_reported_rather_than_raising(tmp_path):
    from pypdf import PdfWriter
    from app.modules.sam_report import datasheet_parser
    blank = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(blank, "wb") as fh:
        w.write(fh)
    assert datasheet_parser.identify(blank) is None


def test_add_module_accepts_a_datasheet_instead_of_a_pysam(staged, sam_workbook, tmp_path):
    """The datasheet names the module; system-design values stay empty because a
    datasheet cannot supply them."""
    token, _ = staged
    pdf = _datasheet_pdf(tmp_path, "q245",
                         "<h1>Qcells North America</h1><h2>Q.PRO-G3 245</h2>"
                         "<p>Open Circuit Voltage (Voc) 37.56 V</p>")
    entries = report_service.add_module(
        token, _Upload(sam_workbook, "second.xlsx"), "", pysam=_Upload(pdf))
    entry = entries[-1]
    assert entry.get("datasheet") and not entry.get("pysam")
    assert "Qcells North America" in entry["module_name"]
    assert entry["label"] == entry["module_name"]        # auto-labelled from it

    module = report_service._build_modules(token, {})[-1]
    assert "Qcells North America" in module.module_model
    assert module.system_size_dc == ""                   # needs a pysam
