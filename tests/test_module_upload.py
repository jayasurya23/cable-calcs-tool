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
