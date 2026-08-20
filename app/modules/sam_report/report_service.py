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
from .excel_writer import TABLE_COLUMNS
from .report_models import ReportModule, ReportProject
from . import service as service_mod
from .service import UPLOAD_META_FILE, _build_table, _upload_dir

MODULES_FILE = "modules.json"
DATASHEETS_FILE = "datasheets.json"
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
                           user: User | None, name: str = "") -> Analysis:
    aid = get_upload_analysis_id(upload_id)
    if aid:
        a = db.get(Analysis, aid)
        if a is not None:
            return a
    return create_analysis(db, project, upload_id, user, name=name)


def _pick_source_workbook(files: list[Path]) -> Path | None:
    """The original SAM runs workbook among a filed revision's stored files: the
    .xlsx/.xlsm that is not the generated '… - Output' derivative."""
    xls = [p for p in files if p.suffix.lower() in (".xlsx", ".xlsm")
           and not p.stem.endswith(" - Output")]
    return xls[0] if xls else None


def recover_analysis_working_dir(db: Session, analysis: Analysis) -> str | None:
    """Rebuild a usable staging dir for an analysis whose working files are gone
    (old/migrated records) from its most recent filed revision — which stored the
    source workbook + pysam. Copies them into a fresh token dir, writes the upload
    meta, points analysis.dir at it, and returns the token; None if nothing is
    recoverable (no revision carrying a source workbook)."""
    for rev in sorted(analysis.revisions, key=lambda r: r.rev_number, reverse=True):
        if not rev.dir:
            continue
        rev_path = Path(settings.data_dir) / rev.dir
        if not rev_path.is_dir():
            continue
        files = [p for p in rev_path.iterdir() if p.is_file()]
        wb = _pick_source_workbook(files)
        if wb is None:
            continue
        pysam = next((p for p in files
                      if p.suffix.lower() == ".json" and p.name != "form.json"), None)
        token = uuid.uuid4().hex[:12]
        dest = Path(settings.upload_dir) / token
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wb, dest / wb.name)
        if pysam:
            shutil.copy2(pysam, dest / pysam.name)
        (dest / UPLOAD_META_FILE).write_text(json.dumps({
            "workbook": wb.name,
            "pysam": pysam.name if pysam else None,
            "project_id": analysis.project_id,
            "analysis_id": analysis.id,
        }), encoding="utf-8")
        analysis.dir = token
        db.add(analysis)
        db.commit()
        return token
    return None


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


def source_files(upload_id: str) -> list[dict]:
    """Everything the engineer supplied for this analysis, grouped by module and
    labelled by what each file is.

    The uploads were always kept — they are copied into every filed revision —
    but nothing on the analysis page offered them back, so the only workbook a
    person could retrieve was the one we generated. Reaching the original meant
    digging through a revision's undifferentiated file list on the project page.
    """
    from app.modules.projects import storage as rev_storage

    dest_dir = _upload_dir(upload_id)
    meta = _load_upload_meta(dest_dir)
    groups: list[dict] = []

    for i, entry in enumerate(load_modules(upload_id)):
        names: list[str] = []

        def add(name):
            if name and name not in names and (dest_dir / name).is_file():
                names.append(name)

        workbook = entry.get("path")
        add(workbook)
        if workbook:                     # the Output copy we wrote alongside it
            wb = Path(workbook)
            add(f"{wb.stem} - Output{wb.suffix}")
        add(entry.get("pysam"))
        add(entry.get("datasheet"))
        if i == 0:
            # Module 1's pysam / datasheet are recorded on the upload itself
            # rather than on its module entry.
            add(meta.get("pysam"))
            add(meta.get("datasheet"))
        if names:
            groups.append({"label": entry.get("label") or f"Module {i + 1}",
                           "files": rev_storage.describe_files(names)})

    # Datasheets attached to the report as a whole (appended to the PDF), which
    # belong to no single module.
    extra = [d["path"] for d in load_datasheets(upload_id)
             if (dest_dir / d["path"]).is_file()]
    if extra:
        groups.append({"label": "Appended datasheets",
                       "files": rev_storage.describe_files(extra)})
    return groups


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
    # Module 1's datasheet is recorded on the upload itself rather than on a
    # module entry, so walking modules[1:] skipped it — the analysis page tells
    # the engineer every uploaded file is kept with each revision, and a
    # third-party datasheet PDF is the least replaceable thing here.
    if meta.get("datasheet"):
        files.append(dest_dir / meta["datasheet"])
    for entry in load_modules(upload_id)[1:]:  # extra module workbooks (+ their pysam)
        files.append(dest_dir / entry["path"])
        if entry.get("pysam"):
            files.append(dest_dir / entry["pysam"])
        if entry.get("datasheet"):
            files.append(dest_dir / entry["datasheet"])
    # Datasheets appended to the report as a whole belong to no single module.
    for sheet in load_datasheets(upload_id):
        files.append(dest_dir / sheet["path"])
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
        entry = {"path": meta["workbook"], "name": meta["workbook"]}
        # Module 1 may have been given a datasheet instead of a pysam — identify
        # it the same way the later modules are.
        if meta.get("datasheet") and not meta.get("pysam"):
            entry.update(_identify_datasheet(dest_dir, meta["datasheet"]))
            if entry.get("module_specs", {}).get("pmax"):
                wattage = int(round(entry["module_specs"]["pmax"]))
        entry["label"] = (entry.get("module_name")
                          or report_builder.default_module_label(wattage, 0))
        entries.append(entry)
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


def stage_module_info(dest_dir: Path, info: UploadFile) -> dict:
    """Stage a module's optional info file, which may be EITHER a pysam JSON or a
    manufacturer datasheet PDF, and return what it told us.

    pysam  -> {"pysam": rel}                    full system + module parameters
    PDF    -> {"datasheet": rel, "module_name": "Manufacturer — Model", ...}
              identified against the CEC database where possible

    A datasheet names the module and gives its nameplate specs; it CANNOT give
    coordinates, GCR, modules-per-string, system size or DC/AC ratio, which are
    properties of the system design rather than the module.
    """
    safe = Path(info.filename or "info").name
    rel = f"modules/{uuid.uuid4().hex[:8]}_{safe}"
    service_mod.copy_limited(info.file, dest_dir / rel, what="module info file")

    if safe.lower().endswith(".pdf"):
        return _identify_datasheet(dest_dir, rel)
    return {"pysam": rel}


def _identify_datasheet(dest_dir: Path, rel: str) -> dict:
    """Identify the module a staged datasheet describes.

    Shared by module 1 (New-analysis page) and the modules added later, so a
    datasheet behaves identically wherever it is uploaded.
    """
    from . import datasheet_parser
    try:
        found = datasheet_parser.identify(dest_dir / rel)
    except Exception:  # noqa: BLE001 - a bad PDF must not lose the module
        found = None
    if not found or not found.get("display"):
        return {"datasheet": rel, "module_name": "",
                "info_note": "The datasheet couldn't be read (is it a scan?). "
                             "Enter the module name manually."}

    source = found.get("source", "text")
    # "cec" (name matched) and "cec-specs" (nameplate matched, manufacturer
    # confirmed in the text) are authoritative. Anything else is a suggestion.
    authoritative = source in ("cec", "cec-specs")
    out = {"datasheet": rel, "module_name": found["display"],
           "module_specs": found.get("specs") or {}, "info_source": source}
    if not authoritative:
        if source == "ai":
            out["info_note"] = ("Read from the datasheet by AI because this module "
                                "isn't in the CEC database - check the name, then use "
                                "“Save to library” so it's recognised next time.")
        elif source == "cec-specs-ambiguous":
            alts = ", ".join(found.get("alternatives", [])[:3])
            out["info_note"] = (
                "Several manufacturers publish these exact nameplate values, so the "
                "datasheet's numbers alone can't settle which module this is"
                + (f" (e.g. {alts})" if alts else "") + " - check the name.")
        else:
            out["info_note"] = ("Module name read from the datasheet text - please "
                                "check it. Its specs were read successfully.")
    return out

def add_module(upload_id: str, upload: UploadFile, label: str,
               pysam: UploadFile | None = None) -> list[dict]:
    """Stage an extra module workbook plus its optional info file.

    `pysam` may be EITHER a pysam JSON (full system + module parameters) or a
    manufacturer datasheet PDF (identifies the module and its nameplate specs) —
    see stage_module_info. Raises ValueError if the workbook isn't a SAM export.
    """
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)

    mod_dir = dest_dir / "modules"
    mod_dir.mkdir(exist_ok=True)
    safe = Path(upload.filename or "module.xlsx").name
    rel = f"modules/{uuid.uuid4().hex[:8]}_{safe}"
    service_mod.copy_limited(upload.file, dest_dir / rel, what="workbook")

    # openpyxl raises (BadZipFile etc.) on anything that isn't a real workbook —
    # convert that to a ValueError so callers have ONE failure mode to handle, and
    # always drop the staged file so a rejected upload leaves nothing behind.
    try:
        runs, _ = parser.extract_runs(dest_dir / rel)
    except Exception as exc:  # noqa: BLE001 - any unreadable file is a user error
        (dest_dir / rel).unlink(missing_ok=True)
        raise ValueError(service_mod.friendly_upload_error(exc)) from exc
    if not runs:
        (dest_dir / rel).unlink(missing_ok=True)
        raise ValueError(
            "That workbook doesn't look like a SAM runs export (no "
            "open-circuit / short-circuit sheets found)."
        )

    entry: dict = {"path": rel, "name": safe}
    wattage = None
    if pysam is not None and getattr(pysam, "filename", ""):
        entry.update(stage_module_info(dest_dir, pysam))
        if entry.get("pysam"):
            try:
                wattage = report_builder.prefill_from_pysam(
                    parser.parse_pysam_json(dest_dir / entry["pysam"])).get("module_wattage")
            except Exception:  # noqa: BLE001 - bad pysam still leaves a usable module
                wattage = None
        elif entry.get("module_specs", {}).get("pmax"):
            wattage = int(round(entry["module_specs"]["pmax"]))

    entry["label"] = (label.strip()
                      or entry.get("module_name")
                      or report_builder.default_module_label(wattage, len(entries)))
    entries.append(entry)
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


# ─── datasheet management (PDFs appended to the report PDF) ───────────────────

def _datasheets_meta_path(dest_dir: Path) -> Path:
    return dest_dir / DATASHEETS_FILE


def load_datasheets(upload_id: str) -> list[dict]:
    """Return the datasheet list ([{'path','name','pages'}, ...]); [] if none."""
    path = _datasheets_meta_path(_upload_dir(upload_id))
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return []


def _save_datasheets(dest_dir: Path, entries: list[dict]) -> None:
    _atomic_write(_datasheets_meta_path(dest_dir), json.dumps(entries).encode("utf-8"))


def add_datasheet(upload_id: str, upload: UploadFile) -> list[dict]:
    """Stage a datasheet PDF (appended to the end of the report PDF at generate
    time). Raises ValueError if the file isn't a readable, non-encrypted PDF."""
    dest_dir = _upload_dir(upload_id)
    entries = load_datasheets(upload_id)

    safe = Path(upload.filename or "datasheet.pdf").name
    if not safe.lower().endswith(".pdf"):
        raise ValueError("Datasheets must be PDF files.")

    ds_dir = dest_dir / "datasheets"
    ds_dir.mkdir(exist_ok=True)
    rel = f"datasheets/{uuid.uuid4().hex[:8]}_{safe}"
    service_mod.copy_limited(upload.file, dest_dir / rel, what="datasheet")

    pages = converter.validate_pdf(dest_dir / rel)
    if pages is None:
        (dest_dir / rel).unlink(missing_ok=True)
        raise ValueError(
            "That file couldn’t be read as a PDF (it may be corrupt or "
            "password-protected). Remove any password and try again.")

    entries.append({"path": rel, "name": safe, "pages": pages})
    _save_datasheets(dest_dir, entries)
    return entries


def remove_datasheet(upload_id: str, index: int) -> list[dict]:
    """Remove a datasheet block (index into the datasheet list)."""
    dest_dir = _upload_dir(upload_id)
    entries = load_datasheets(upload_id)
    if 0 <= index < len(entries):
        removed = entries.pop(index)
        if removed.get("path", "").startswith("datasheets/"):
            (dest_dir / removed["path"]).unlink(missing_ok=True)
        _save_datasheets(dest_dir, entries)
    return entries


def datasheet_paths(upload_id: str) -> list[Path]:
    """Existing datasheet files, in upload order (for appending to the PDF)."""
    dest_dir = _upload_dir(upload_id)
    return [dest_dir / e["path"] for e in load_datasheets(upload_id)
            if (dest_dir / e["path"]).is_file()]


# ─── prefill + build ─────────────────────────────────────────────────────────

def prefill_project(upload_id: str) -> ReportProject:
    """Defaults for the form: today's date + Project Info / Inputs from pysam.

    Manual module/inverter names saved on the upload page (equipment_overrides)
    take precedence over the pysam-derived model strings.
    """
    dest_dir = _upload_dir(upload_id)
    meta = _load_upload_meta(dest_dir)
    # Shared fields come from the primary pysam (the upload's, or the first module
    # that has one) — coordinates/GCR/mps/inverter/weather are the same for all modules.
    pf = _pysam_prefill(dest_dir, {"pysam": _primary_pysam_rel(upload_id)})

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


def _primary_pysam_rel(upload_id: str) -> str | None:
    """The pysam that supplies the SHARED (project-level) Project-Info fields —
    coordinates, GCR, modules-per-string, inverter, weather, albedo (same for
    every module): the upload's own pysam, else the first module that carries one."""
    meta = _load_upload_meta(_upload_dir(upload_id))
    if meta.get("pysam"):
        return meta["pysam"]
    for e in load_modules(upload_id):
        if e.get("pysam"):
            return e["pysam"]
    return None


def _build_modules(upload_id: str, labels: dict[int, str]) -> list[ReportModule]:
    dest_dir = _upload_dir(upload_id)
    entries = load_modules(upload_id)
    meta = _load_upload_meta(dest_dir)
    modules: list[ReportModule] = []
    for i, e in enumerate(entries):
        label = (labels.get(i) or e.get("label") or "").strip() \
            or report_builder.default_module_label(None, i)
        # Module 0's pysam is the upload's (meta); extra modules carry their own.
        pysam_rel = meta.get("pysam") if i == 0 else e.get("pysam")
        pysam_path = (dest_dir / pysam_rel) if pysam_rel else None
        m = report_builder.module_from_workbook(dest_dir / e["path"], label, pysam_path)
        # No pysam, but a datasheet identified the module -> use that name.
        if not m.module_model and e.get("module_name"):
            m.module_model = e["module_name"]
        modules.append(m)
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


def modules_preview(upload_id: str) -> tuple[list[dict], dict]:
    """Per-module on-page tables + the derived stats, parsing each workbook once.

    Each preview carries the SAME table the single-module page shows (Year / Max
    Isc / Max Voc / 3-hr rolling, with a Maximum footer row), so every module's
    calcs are visible on the report form — not just the first upload's.
    Returns (previews, stats).
    """
    dest_dir = _upload_dir(upload_id)
    meta = _load_upload_meta(dest_dir)
    entries = load_modules(upload_id)
    previews: list[dict] = []
    years: set[int] = set()
    for i, e in enumerate(entries):
        runs, _ = parser.extract_runs(dest_dir / e["path"])
        years.update(r.year for r in runs)
        has_pysam = bool(e.get("pysam")) or (i == 0 and bool(meta.get("pysam")))
        previews.append({
            "label": e.get("label") or report_builder.default_module_label(None, i),
            "name": e.get("name") or Path(e["path"]).name,
            "has_pysam": has_pysam,
            "columns": list(TABLE_COLUMNS) if runs else [],
            "rows": _build_table(runs) if runs else [],
        })
    ys = sorted(years)
    stats = {"year_start": ys[0] if ys else None, "year_end": ys[-1] if ys else None,
             "num_runs": len(ys), "num_modules": len(entries)}
    return previews, stats


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

    # 2. Convert to PDF (LibreOffice on Azure; Word COM locally). Then append any
    #    uploaded datasheet PDFs to the end. Write via a temp file + atomic replace
    #    so a concurrent download never sees a partial. (The .docx deliverable is
    #    the report only — PDFs can't be appended to a Word file.)
    tmp_pdf = dest_dir / f"{REPORT_PDF_NAME}.tmp{uuid.uuid4().hex[:6]}"
    merged_pdf = dest_dir / f"{REPORT_PDF_NAME}.merged{uuid.uuid4().hex[:6]}"
    try:
        converter.docx_to_pdf(docx_path, tmp_pdf)
        extras = datasheet_paths(upload_id)
        if extras:
            converter.append_pdfs(tmp_pdf, extras, merged_pdf)
            os.replace(merged_pdf, dest_dir / REPORT_PDF_NAME)
        else:
            os.replace(tmp_pdf, dest_dir / REPORT_PDF_NAME)
    finally:
        Path(tmp_pdf).unlink(missing_ok=True)
        Path(merged_pdf).unlink(missing_ok=True)

    return REPORT_DOCX_NAME, REPORT_PDF_NAME
