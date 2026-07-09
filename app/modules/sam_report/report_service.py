"""
Report orchestration: form prefill, module management, and PDF generation.

State per upload lives in the upload's staging dir:
  upload_meta.json         {"workbook","pysam"}  (written at upload time)
  modules.json             [{"path","name","label"}]  module blocks for the report
  modules/<token>_<name>   extra module workbooks added on the report form
  SAM Report.pdf           the generated report
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.models import Analysis, Project, User
from . import converter, docx_fill, parser, report_builder
from .report_models import ReportModule, ReportProject
from .service import UPLOAD_META_FILE, _upload_dir

MODULES_FILE = "modules.json"
REPORT_PDF_NAME = "SAM Report.pdf"
REPORT_DOCX_NAME = "SAM Report.docx"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file + os.replace so a concurrent reader never sees a
    partially written file (guards modules.json and the generated files)."""
    tmp = path.with_name(f"{path.name}.tmp{uuid.uuid4().hex[:6]}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ─── upload meta / pysam helpers ─────────────────────────────────────────────

def _load_upload_meta(dest_dir: Path) -> dict:
    path = dest_dir / UPLOAD_META_FILE
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def get_upload_project_id(upload_id: str) -> int | None:
    """The project this upload was staged for (written at upload time)."""
    meta = _load_upload_meta(_upload_dir(upload_id))
    pid = meta.get("project_id")
    return int(pid) if pid else None


def _update_upload_meta(upload_id: str, updates: dict) -> None:
    dest = _upload_dir(upload_id)
    meta = _load_upload_meta(dest)
    meta.update(updates)
    _atomic_write(dest / UPLOAD_META_FILE, json.dumps(meta).encode("utf-8"))


# ─── analysis (saved scenario) lifecycle ─────────────────────────────────────

def get_upload_analysis_id(upload_id: str) -> int | None:
    aid = _load_upload_meta(_upload_dir(upload_id)).get("analysis_id")
    return int(aid) if aid else None


def create_analysis(db: Session, project: Project, upload_id: str,
                    user: User | None, name: str = "") -> Analysis:
    """Create a saved Analysis (scenario) referencing this upload's persistent
    working dir; record its id in the upload meta so later steps resolve it."""
    if not name:
        wb = _load_upload_meta(_upload_dir(upload_id)).get("workbook") or ""
        name = Path(wb).stem or "SAM analysis"
    a = Analysis(project_id=project.id, name=name, dir=upload_id,
                 created_by=(user.id if user else None))
    db.add(a)
    db.flush()          # assign a.id
    _update_upload_meta(upload_id, {"analysis_id": a.id})
    db.commit()
    return a


def get_or_create_analysis(db: Session, project: Project, upload_id: str,
                           user: User | None) -> Analysis:
    aid = get_upload_analysis_id(upload_id)
    if aid:
        a = db.get(Analysis, aid)
        if a is not None:
            return a
    return create_analysis(db, project, upload_id, user)


def save_analysis_form(db: Session, analysis: Analysis, form_data: dict) -> None:
    """Persist the last-submitted form so reopening the analysis pre-fills it."""
    analysis.form_json = json.dumps(form_data)
    db.add(analysis)
    db.commit()


# ─── collate: one combined report from several analyses ──────────────────────

COLLATED_NAME = "Combined report"


def get_or_create_collated_analysis(db: Session, project: Project,
                                    user: User | None) -> Analysis:
    """The project's single 'Combined report' scenario (kind=sam_collated), which
    holds the revision history of collated reports. Has its own working dir."""
    a = db.scalar(select(Analysis).where(Analysis.project_id == project.id,
                                         Analysis.kind == "sam_collated"))
    if a is not None:
        return a
    token = uuid.uuid4().hex[:12]
    (Path(settings.upload_dir) / token).mkdir(parents=True, exist_ok=True)
    a = Analysis(project_id=project.id, name=COLLATED_NAME, kind="sam_collated",
                 dir=token, created_by=(user.id if user else None))
    db.add(a)
    db.flush()
    _update_upload_meta(token, {"project_id": project.id, "analysis_id": a.id})
    db.commit()
    return a


def render_collated(combined: Analysis, source_analyses: list[Analysis],
                    project: ReportProject) -> None:
    """Build ONE report whose Results table unions the modules of every source
    analysis (side-by-side), write docx + pdf into the combined analysis's dir."""
    modules: list[ReportModule] = []
    for a in source_analyses:
        try:
            modules.extend(_build_modules(a.dir, {}))
        except Exception:  # noqa: BLE001 - skip an unreadable source, keep the rest
            pass
    if not modules:
        raise ValueError("None of the selected analyses have readable module data.")

    ctx = report_builder.build_context(project, modules)
    dest_dir = _upload_dir(combined.dir)
    docx_path = dest_dir / REPORT_DOCX_NAME
    _atomic_write(docx_path, docx_fill.fill_docx(ctx))
    tmp_pdf = dest_dir / f"{REPORT_PDF_NAME}.tmp{uuid.uuid4().hex[:6]}"
    try:
        converter.docx_to_pdf(docx_path, tmp_pdf)
        os.replace(tmp_pdf, dest_dir / REPORT_PDF_NAME)
    finally:
        Path(tmp_pdf).unlink(missing_ok=True)


def collect_revision_files(upload_id: str) -> list[Path]:
    """Every document worth filing with a revision: the generated report
    (docx + pdf), the standardized Output workbook, and all inputs."""
    dest_dir = _upload_dir(upload_id)
    meta = _load_upload_meta(dest_dir)
    files: list[Path] = [dest_dir / REPORT_DOCX_NAME, dest_dir / REPORT_PDF_NAME]
    if meta.get("workbook"):
        wb = dest_dir / meta["workbook"]
        files.append(wb)
        files.append(wb.with_name(f"{wb.stem} - Output{wb.suffix}"))
    if meta.get("pysam"):
        files.append(dest_dir / meta["pysam"])
    for entry in load_modules(upload_id)[1:]:  # extra module workbooks
        files.append(dest_dir / entry["path"])
    return [f for f in files if f.is_file()]


def _pysam_prefill(dest_dir: Path, meta: dict) -> dict:
    """Always returns the full key set. prefill_from_pysam(None) yields the
    complete defaults dict, so callers never hit a missing key (e.g. an upload
    with no pysam, or a pysam that fails to parse)."""
    if meta.get("pysam"):
        try:
            return report_builder.prefill_from_pysam(
                parser.parse_pysam_json(dest_dir / meta["pysam"]))
        except Exception:  # noqa: BLE001 - fall through to empty defaults
            pass
    return report_builder.prefill_from_pysam(None)


# ─── module management ───────────────────────────────────────────────────────

def _modules_meta_path(dest_dir: Path) -> Path:
    return dest_dir / MODULES_FILE


def load_modules(upload_id: str) -> list[dict]:
    """Return the module list, initializing it from the main workbook on first use."""
    dest_dir = _upload_dir(upload_id)
    path = _modules_meta_path(dest_dir)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    meta = _load_upload_meta(dest_dir)
    entries: list[dict] = []
    if meta.get("workbook"):
        wattage = _pysam_prefill(dest_dir, meta)["module_wattage"]
        entries.append({
            "path": meta["workbook"],
            "name": meta["workbook"],
            "label": report_builder.default_module_label(wattage, 0),
        })
        path.write_text(json.dumps(entries), encoding="utf-8")
    return entries


def _save_modules(dest_dir: Path, entries: list[dict]) -> None:
    _atomic_write(_modules_meta_path(dest_dir), json.dumps(entries).encode("utf-8"))


def save_labels(upload_id: str, labels: dict[int, str]) -> None:
    """Persist edited module labels (so they survive add/remove re-renders)."""
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)
    changed = False
    for i, entry in enumerate(entries):
        if labels.get(i) and labels[i].strip():
            entry["label"] = labels[i].strip()
            changed = True
    if changed:
        _save_modules(dest_dir, entries)


def add_module(upload_id: str, upload: UploadFile, label: str) -> list[dict]:
    """Stage an extra module workbook. Raises ValueError if it isn't a SAM export."""
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)

    mod_dir = dest_dir / "modules"
    mod_dir.mkdir(exist_ok=True)
    safe = Path(upload.filename or "module.xlsx").name
    rel = f"modules/{uuid.uuid4().hex[:8]}_{safe}"
    with open(dest_dir / rel, "wb") as out:
        shutil.copyfileobj(upload.file, out)

    runs, _ = parser.extract_runs(dest_dir / rel)
    if not runs:
        (dest_dir / rel).unlink(missing_ok=True)
        raise ValueError(
            "That workbook doesn't look like a SAM runs export (no "
            "open-circuit / short-circuit sheets found)."
        )

    entries.append({
        "path": rel,
        "name": safe,
        "label": label.strip() or report_builder.default_module_label(None, len(entries)),
    })
    _save_modules(dest_dir, entries)
    return entries


def remove_module(upload_id: str, index: int) -> list[dict]:
    """Remove a module block (index into the module list). Module 0 can't be removed."""
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)
    if 0 < index < len(entries):
        removed = entries.pop(index)
        # Only delete files we staged under modules/ (never the main workbook).
        if removed.get("path", "").startswith("modules/"):
            (dest_dir / removed["path"]).unlink(missing_ok=True)
        _save_modules(dest_dir, entries)
    return entries


# ─── prefill + build ─────────────────────────────────────────────────────────

def prefill_project(upload_id: str) -> ReportProject:
    """Defaults for the form: today's date + Project Info / Inputs from pysam.

    Manual module/inverter names saved on the upload page (equipment_overrides)
    take precedence over the pysam-derived model strings.
    """
    dest_dir = _upload_dir(upload_id)
    meta = _load_upload_meta(dest_dir)
    pf = _pysam_prefill(dest_dir, meta)

    from .service import _load_equipment_overrides  # avoid import cycle at module load
    overrides = _load_equipment_overrides(dest_dir)
    module_model = overrides.get("module_model") or pf["module_model"]
    inverter_model = overrides.get("inverter_model") or pf["inverter_model"]

    return ReportProject(
        date=datetime.date.today().strftime("%m/%d/%Y"),
        coordinates=pf["coordinates"],
        gcr=pf["gcr"],
        modules_per_string=pf["modules_per_string"],
        module_model=module_model,
        inverter_model=inverter_model,
        dc_ac_ratio=pf["dc_ac_ratio"],
        system_size_dc=pf["system_size_dc"],
        albedo_text=pf["albedo_text"],
        # Actual NSRDB weather filename when pysam is present, else a generic
        # reference so the Inputs "Weather Data:" line is never left dangling.
        weather_file=pf["weather_file"] or "National Solar Radiation Database (NSRDB)",
    )


def _build_modules(upload_id: str, labels: dict[int, str]) -> list[ReportModule]:
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)
    modules: list[ReportModule] = []
    for i, e in enumerate(entries):
        label = (labels.get(i) or e.get("label") or "").strip() \
            or report_builder.default_module_label(None, i)
        modules.append(report_builder.module_from_workbook(dest_dir / e["path"], label))
    return modules


def derived_stats(upload_id: str) -> dict:
    """Auto-derived year range / run count / module count for the form banner."""
    modules = _build_modules(upload_id, {})
    ctx = report_builder.build_context(ReportProject(), modules)
    return {
        "year_start": ctx.year_start,
        "year_end": ctx.year_end,
        "num_runs": ctx.num_runs,
        "num_modules": len(modules),
    }


def generate_report(upload_id: str, project: ReportProject,
                    labels: dict[int, str]) -> tuple[str, str]:
    """Fill the Word template, convert to PDF, and save both in the upload dir.

    Returns (docx_name, pdf_name). Runs the (sync, subprocess-based) DOCX->PDF
    conversion — call from a threadpool.
    """
    dest_dir = _upload_dir(upload_id)
    save_labels(upload_id, labels)  # persist edited labels back into modules.json
    modules = _build_modules(upload_id, labels)
    ctx = report_builder.build_context(project, modules)

    # 1. Fill the .docx template (editable Word deliverable).
    docx_bytes = docx_fill.fill_docx(ctx)
    docx_path = dest_dir / REPORT_DOCX_NAME
    _atomic_write(docx_path, docx_bytes)

    # 2. Convert to PDF (LibreOffice on Azure; Word COM locally). Write via a
    #    temp file + atomic replace so a concurrent download never sees a partial.
    tmp_pdf = dest_dir / f"{REPORT_PDF_NAME}.tmp{uuid.uuid4().hex[:6]}"
    try:
        converter.docx_to_pdf(docx_path, tmp_pdf)
        os.replace(tmp_pdf, dest_dir / REPORT_PDF_NAME)
    finally:
        Path(tmp_pdf).unlink(missing_ok=True)

    return REPORT_DOCX_NAME, REPORT_PDF_NAME
