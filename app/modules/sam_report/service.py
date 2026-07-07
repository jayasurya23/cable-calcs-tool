"""
Orchestration for the SAM report: upload staging -> parse -> report + Output workbook.

Keeps the router thin. When the workbook is a recognizable SAM runs export, this
builds the standardized per-year table (max Isc / max Voc / max 3-hr rolling
average of string Isc) and writes it into an "Output" sheet of a downloadable
copy of the workbook. Otherwise it degrades gracefully to the raw sheet preview.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import settings
from app.shared.engine import ENGINES_AVAILABLE
from . import parser
from .excel_writer import TABLE_COLUMNS, write_output_workbook
from .models import Equipment, ReportTableRow, SamReport, SamRun


EQUIPMENT_OVERRIDES_FILE = "equipment_overrides.json"
UPLOAD_META_FILE = "upload_meta.json"


def _upload_dir(upload_id: str) -> Path:
    """Resolve and validate an upload's staging dir (guards against traversal)."""
    upload_root = Path(settings.upload_dir).resolve()
    if not upload_id.isalnum():
        raise ValueError("invalid upload id")
    path = (upload_root / upload_id).resolve()
    if upload_root not in path.parents or not path.is_dir():
        raise ValueError("unknown upload")
    return path


def save_equipment_names(upload_id: str, module_name: str, inverter_name: str) -> None:
    """Persist engineer-entered product names alongside the upload's specs.

    Written to the upload's staging dir so the later report-generation step
    reads the confirmed names next to the SAM-derived model type + nameplate.
    """
    path = _upload_dir(upload_id)
    (path / EQUIPMENT_OVERRIDES_FILE).write_text(
        json.dumps({
            "module_model": module_name.strip() or None,
            "inverter_model": inverter_name.strip() or None,
        }),
        encoding="utf-8",
    )


def _load_equipment_overrides(dest_dir: Path) -> dict:
    path = dest_dir / EQUIPMENT_OVERRIDES_FILE
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


# Filenames the app itself writes into staging/revision dirs — user uploads may
# not collide with them (an upload named "form.json" would clobber app state).
_RESERVED_NAMES = {"upload_meta.json", "modules.json", "equipment_overrides.json",
                   "form.json", "sam report.docx", "sam report.pdf"}


def _stage_upload(upload: UploadFile, dest_dir: Path) -> Path:
    """Persist an UploadFile to disk, returning its path."""
    safe_name = Path(upload.filename or "upload").name
    if safe_name.lower() in _RESERVED_NAMES:
        safe_name = f"src_{safe_name}"
    dest = dest_dir / safe_name
    with open(dest, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return dest


def _build_table(runs: list[SamRun]) -> list[ReportTableRow]:
    """Rows for the web report — 2-dp display, plus the Maximum footer row."""
    rows = [
        ReportTableRow(cells={
            TABLE_COLUMNS[0]: run.year,
            TABLE_COLUMNS[1]: f"{run.max_isc_a:.2f}",
            TABLE_COLUMNS[2]: f"{run.max_voc_v:.2f}",
            TABLE_COLUMNS[3]: f"{run.max_isc_rolling_avg_a:.2f}",
        })
        for run in runs
    ]
    rows.append(ReportTableRow(cells={
        TABLE_COLUMNS[0]: "Maximum",
        TABLE_COLUMNS[1]: f"{max(x.max_isc_a for x in runs):.2f}",
        TABLE_COLUMNS[2]: f"{max(x.max_voc_v for x in runs):.2f}",
        TABLE_COLUMNS[3]: f"{max(x.max_isc_rolling_avg_a for x in runs):.2f}",
    }))
    return rows


def process_upload(workbook: UploadFile, pysam: UploadFile | None,
                   project_id: int | None = None) -> SamReport:
    """Stage the uploaded files, parse them, and build the report."""
    upload_id = uuid.uuid4().hex[:12]
    dest_dir = Path(settings.upload_dir) / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []

    workbook_path = _stage_upload(workbook, dest_dir)
    sheets = parser.preview_workbook(workbook_path)

    pysam_path = None
    equipment: Equipment | None = None
    if pysam is not None and pysam.filename:
        pysam_path = _stage_upload(pysam, dest_dir)
        try:
            pysam_data = parser.parse_pysam_json(pysam_path)
            equipment = parser.extract_equipment(pysam_data)
            if not equipment.has_content:
                warnings.append(
                    "pysam JSON parsed, but no recognizable module/inverter "
                    "definition was found."
                )
            overrides = _load_equipment_overrides(dest_dir)
            if overrides.get("module_model"):
                equipment.module_model = overrides["module_model"]
            if overrides.get("inverter_model"):
                equipment.inverter_model = overrides["inverter_model"]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not parse pysam JSON: {exc}")

    runs, parse_warnings = parser.extract_runs(workbook_path)
    warnings.extend(parse_warnings)

    table_rows: list[ReportTableRow] = []
    output_filename: str | None = None
    if runs:
        table_rows = _build_table(runs)
        output_filename = f"{workbook_path.stem} - Output{workbook_path.suffix}"
        try:
            write_output_workbook(workbook_path, runs, dest_dir / output_filename)
        except Exception as exc:  # noqa: BLE001
            output_filename = None
            warnings.append(f"Could not write the Output workbook: {exc}")

    # Record what was uploaded so the report builder can find the main workbook
    # and pysam later (filenames aren't otherwise distinguishable on disk).
    (dest_dir / UPLOAD_META_FILE).write_text(
        json.dumps({
            "workbook": workbook_path.name,
            "pysam": (pysam_path.name if pysam_path else None),
            "project_id": project_id,
        }),
        encoding="utf-8",
    )

    return SamReport(
        upload_id=upload_id,
        source_workbook=workbook_path.name,
        source_pysam=(pysam_path.name if pysam_path else None),
        engines_available=ENGINES_AVAILABLE,
        sheets=sheets,
        runs=runs,
        equipment=equipment,
        table_columns=(TABLE_COLUMNS if runs else []),
        table_rows=table_rows,
        output_filename=output_filename,
        warnings=warnings,
    )
