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


def copy_limited(src, dest: Path, what: str = "file") -> None:
    """Stream an upload to `dest`, refusing anything over settings.max_upload_bytes.

    Copied in chunks with a running total so an oversized (or malicious) upload is
    rejected as it arrives rather than after it has filled the disk. The partial
    file is removed on refusal. Raises ValueError with a user-facing message.
    """
    limit = settings.max_upload_bytes
    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError(
                        f"That {what} is larger than the "
                        f"{round(limit / (1024 * 1024))} MB upload limit.")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _stage_upload(upload: UploadFile, dest_dir: Path) -> Path:
    """Persist an UploadFile to disk, returning its path."""
    safe_name = Path(upload.filename or "upload").name
    if safe_name.lower() in _RESERVED_NAMES:
        safe_name = f"src_{safe_name}"
    dest = dest_dir / safe_name
    copy_limited(upload.file, dest, what="file")
    return dest


def friendly_upload_error(exc: Exception) -> str:
    """Plain-English version of the common upload failures.

    openpyxl raises "File is not a zip file" for anything that isn't a real
    .xlsx (a PDF or CSV renamed, a Numbers export, a corrupt download) — which
    means nothing to an engineer. Pass through our own ValueErrors unchanged;
    they are already written for the user.
    """
    msg = str(exc)
    if isinstance(exc, ValueError):
        return msg
    low = msg.lower()
    if "not a zip file" in low or "badzipfile" in low:
        return ("That file isn't a readable .xlsx workbook. It may be a PDF or CSV "
                "renamed to .xlsx, or an incomplete download — re-export the SAM "
                "runs workbook from SAM and try again.")
    if "no such file" in low or "cannot find" in low:
        return "That file could not be read — please try uploading it again."
    if "permission" in low:
        return "That file is locked (is it open in Excel?). Close it and try again."
    return msg


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


def rehydrate_report(upload_id: str) -> SamReport:
    """Rebuild the SamReport from an already-staged upload dir (reopening a saved
    analysis) — re-parses the persisted workbook/pysam; no re-staging or re-upload."""
    dest_dir = _upload_dir(upload_id)
    meta = json.loads((dest_dir / UPLOAD_META_FILE).read_text(encoding="utf-8"))
    workbook_path = dest_dir / meta["workbook"]
    warnings: list[str] = []
    sheets = parser.preview_workbook(workbook_path)

    equipment: Equipment | None = None
    pysam_name = meta.get("pysam")
    if pysam_name and (dest_dir / pysam_name).is_file():
        try:
            equipment = parser.extract_equipment(parser.parse_pysam_json(dest_dir / pysam_name))
            overrides = _load_equipment_overrides(dest_dir)
            if overrides.get("module_model"):
                equipment.module_model = overrides["module_model"]
            if overrides.get("inverter_model"):
                equipment.inverter_model = overrides["inverter_model"]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not parse pysam JSON: {exc}")

    runs, parse_warnings = parser.extract_runs(workbook_path)
    warnings.extend(parse_warnings)
    table_rows = _build_table(runs) if runs else []
    output_filename = f"{workbook_path.stem} - Output{workbook_path.suffix}"
    if not (dest_dir / output_filename).is_file():
        output_filename = None
    return SamReport(
        upload_id=upload_id, source_workbook=workbook_path.name, source_pysam=pysam_name,
        engines_available=ENGINES_AVAILABLE, sheets=sheets, runs=runs, equipment=equipment,
        table_columns=(TABLE_COLUMNS if runs else []), table_rows=table_rows,
        output_filename=output_filename, warnings=warnings,
    )


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
