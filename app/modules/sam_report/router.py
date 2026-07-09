"""Routes for the SAM report module."""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.models import Analysis, Project, Revision, User
from app.core.templating import templates
from app.modules.projects import storage
from . import report_service, service
from .report_models import ReportProject

router = APIRouter(prefix="/sam", tags=["sam-report"])


def _require_upload(upload_id: str) -> None:
    """404 unless upload_id is a known staging dir (also blocks path traversal)."""
    try:
        service._upload_dir(upload_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")


def _labels_from_form(form) -> dict[int, str]:
    labels: dict[int, str] = {}
    for key, value in form.items():
        if key.startswith("module_label_"):
            try:
                labels[int(key[len("module_label_"):])] = str(value)
            except ValueError:
                continue
    return labels


def _project_from_form(form) -> ReportProject:
    def g(name: str, default: str = "") -> str:
        return str(form.get(name, default) or default)

    return ReportProject(
        project_name=g("project_name"),
        project_id=g("project_id"),
        date=g("date"),
        revision=g("revision", "0") or "0",
        coordinates=g("coordinates"),
        gcr=g("gcr"),
        modules_per_string=g("modules_per_string"),
        module_model=g("module_model"),
        inverter_model=g("inverter_model"),
        dc_ac_ratio=g("dc_ac_ratio"),
        system_size_dc=g("system_size_dc"),
        albedo_text=g("albedo_text"),
        weather_file=g("weather_file"),
        owner_name=g("owner_name"), owner_phone=g("owner_phone"),
        epc_name=g("epc_name"), epc_phone=g("epc_phone"),
        eng_firm_name=g("eng_firm_name"), eng_firm_phone=g("eng_firm_phone"),
        eor=g("eor"), designer=g("designer"), checker=g("checker"),
    )


_COVER_FIELDS = ["owner_name", "owner_phone", "epc_name", "epc_phone",
                 "eng_firm_name", "eng_firm_phone", "eor", "designer", "checker"]


def _apply_project_defaults(prefill, proj_rec) -> None:
    """Project record fields override/prefill the report form."""
    prefill.project_name = proj_rec.name
    prefill.project_id = proj_rec.code
    for f in _COVER_FIELDS:
        val = getattr(proj_rec, f, "")
        if val:
            setattr(prefill, f, val)


@router.get("", response_class=HTMLResponse)
def upload_page(request: Request, project: int = 0,
                db: Session = Depends(get_db)):
    """The SAM upload form — always scoped to a project."""
    proj_rec = db.get(Project, project) if project else None
    if proj_rec is None:
        return RedirectResponse("/projects", status_code=303)
    return templates.TemplateResponse(request, "sam_report/upload.html",
                                      {"project_rec": proj_rec})


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
    workbook: UploadFile = File(..., description="SAM runs Excel (.xlsx)"),
    pysam: UploadFile | None = File(None, description="Optional pysam JSON"),
    project_id: int = Form(0),
):
    """Handle the upload, then render the report fragment (HTMX swaps it in).

    When the workbook is a valid SAM export we also render the report form inline
    (hidden) so "Generate report" can reveal it instantly — no second request.
    """
    proj_rec = db.get(Project, project_id) if project_id else None
    if proj_rec is None:
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": "No project selected — open the analysis from a project page."},
            status_code=400)
    try:
        report = service.process_upload(workbook, pysam, project_id=proj_rec.id)
    except Exception as exc:  # noqa: BLE001 - surface any parse failure to the user
        return templates.TemplateResponse(
            request,
            "sam_report/_error.html",
            {"message": str(exc)},
            status_code=400,
        )
    ctx = {"report": report, "upload_id": report.upload_id, "project_rec": proj_rec}
    if report.runs:
        analysis = report_service.get_or_create_analysis(db, proj_rec, report.upload_id, user)
        prefill = report_service.prefill_project(report.upload_id)
        _apply_project_defaults(prefill, proj_rec)
        revisions = db.scalars(select(Revision)
                               .where(Revision.analysis_id == analysis.id)
                               .order_by(Revision.rev_number)).all()
        next_rev = storage.next_rev_number(db, analysis)
        ctx.update(project=prefill, analysis=analysis,
                   modules=report_service.load_modules(report.upload_id),
                   stats=report_service.derived_stats(report.upload_id),
                   revisions=revisions, next_rev=next_rev)
    return templates.TemplateResponse(request, "sam_report/_report.html", ctx)


@router.post("/equipment/{upload_id}", response_class=HTMLResponse)
def save_equipment(
    upload_id: str,
    module_name: str = Form(""),
    inverter_name: str = Form(""),
):
    """Persist engineer-entered module/inverter product names for this upload."""
    try:
        service.save_equipment_names(upload_id, module_name, inverter_name)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse('<span class="saved">✓ Saved</span>')


@router.post("/report/{upload_id}/module", response_class=HTMLResponse)
async def add_module(request: Request, upload_id: str):
    """Add another module (its own Excel) and re-render the modules section."""
    _require_upload(upload_id)
    form = await request.form()
    report_service.save_labels(upload_id, _labels_from_form(form))

    error = None
    upload = form.get("new_module_file")
    label = str(form.get("new_module_label", "") or "")
    if upload is not None and getattr(upload, "filename", ""):
        try:
            report_service.add_module(upload_id, upload, label)
        except ValueError as exc:
            error = str(exc)
    else:
        error = "Choose a workbook to add as a module."

    return templates.TemplateResponse(
        request, "sam_report/_report_form_modules.html",
        {
            "upload_id": upload_id,
            "modules": report_service.load_modules(upload_id),
            "stats": report_service.derived_stats(upload_id),
            "error": error,
        },
    )


@router.post("/report/{upload_id}/module/remove/{index}", response_class=HTMLResponse)
async def remove_module(request: Request, upload_id: str, index: int):
    """Remove a module block and re-render the modules section."""
    _require_upload(upload_id)
    form = await request.form()
    report_service.save_labels(upload_id, _labels_from_form(form))
    report_service.remove_module(upload_id, index)
    return templates.TemplateResponse(
        request, "sam_report/_report_form_modules.html",
        {
            "upload_id": upload_id,
            "modules": report_service.load_modules(upload_id),
            "stats": report_service.derived_stats(upload_id),
            "error": None,
        },
    )


@router.get("/analysis/{analysis_id}", response_class=HTMLResponse)
def open_analysis(request: Request, analysis_id: int,
                  db: Session = Depends(get_db),
                  user: User | None = Depends(get_current_user)):
    """Reopen a saved analysis: re-render the report + form pre-filled from its
    last saved state so it can be edited and re-run (files a NEW revision)."""
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    proj_rec = db.get(Project, analysis.project_id)
    upload_id = analysis.dir  # persistent working-dir token
    if not upload_id or not (Path(settings.upload_dir) / upload_id).is_dir():
        raise HTTPException(status_code=404, detail="Analysis working files are unavailable")
    try:
        report = service.rehydrate_report(upload_id)
    except Exception:  # noqa: BLE001 - working dir present but meta/workbook missing or corrupt
        raise HTTPException(status_code=404,
                            detail="Analysis working files are missing or unreadable")
    saved = json.loads(analysis.form_json or "{}")
    if saved:
        prefill = _project_from_form(saved)  # rebuild from the last-run form
    else:
        prefill = report_service.prefill_project(upload_id)
        _apply_project_defaults(prefill, proj_rec)
    revisions = db.scalars(select(Revision).where(Revision.analysis_id == analysis.id)
                           .order_by(Revision.rev_number)).all()
    next_rev = storage.next_rev_number(db, analysis)
    return templates.TemplateResponse(request, "sam_report/analysis.html", {
        "report": report, "upload_id": upload_id, "project_rec": proj_rec,
        "project": prefill, "analysis": analysis,
        "modules": report_service.load_modules(upload_id),
        "stats": report_service.derived_stats(upload_id),
        "revisions": revisions, "next_rev": next_rev,
    })


@router.post("/analysis/{analysis_id}/rename", response_class=HTMLResponse)
def rename_analysis(analysis_id: int, name: str = Form(""),
                    db: Session = Depends(get_db),
                    user: User | None = Depends(get_current_user)):
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if name.strip():
        analysis.name = name.strip()
        db.add(analysis)
        db.commit()
    return HTMLResponse('<span class="saved">✓ Saved</span>')


@router.get("/collate/{project_id}", response_class=HTMLResponse)
def collate_page(request: Request, project_id: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    """Picker: choose 2+ analyses to combine into one report."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    analyses = db.scalars(select(Analysis).where(Analysis.project_id == project_id,
                          Analysis.kind == "sam_report").order_by(Analysis.created_at)).all()
    return templates.TemplateResponse(request, "sam_report/collate.html",
                                      {"project_rec": project, "analyses": analyses})


@router.post("/collate/{project_id}", response_class=HTMLResponse)
async def collate_generate(request: Request, project_id: int,
                           db: Session = Depends(get_db),
                           user: User | None = Depends(get_current_user)):
    """Build ONE combined report from the selected analyses and file it under the
    project's 'Combined report' scenario (its own revision history)."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    form = await request.form()
    ids = [int(x) for x in form.getlist("analysis_ids") if str(x).isdigit()]
    source = [db.get(Analysis, i) for i in ids]
    source = [a for a in source if a and a.project_id == project.id and a.kind == "sam_report"]
    if len(source) < 2:
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": "Select at least two analyses to combine."}, status_code=400)
    primary_id = form.get("primary")
    primary = next((a for a in source if str(a.id) == str(primary_id)), source[0])

    # Seed the combined report's project/cover/inputs from the primary analysis's
    # last-run form; fall back to the project's cover-sheet defaults.
    saved = json.loads(primary.form_json or "{}")
    if saved:
        project_fields = _project_from_form(saved)
    else:
        project_fields = ReportProject()
        _apply_project_defaults(project_fields, project)

    combined = report_service.get_or_create_collated_analysis(db, project, user)
    target_rev = storage.next_rev_number(db, combined)
    project_fields.revision = str(target_rev)
    try:
        await run_in_threadpool(report_service.render_collated, combined, source, project_fields)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": f"Combined report generation failed: {exc}"}, status_code=500)

    form_data = {**saved, "collated_from": ",".join(str(a.id) for a in source),
                 "primary_analysis": str(primary.id),
                 "combined_of": ", ".join(a.name for a in source)}
    try:
        rev = await run_in_threadpool(
            partial(storage.save_revision, db, project, user,
                    report_service.collect_revision_files(combined.dir), form_data,
                    None, str(form.get("rev_label", "")),
                    new_rev=target_rev, analysis=combined))
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": f"Combined report generated but filing failed: {exc}"}, status_code=500)
    return templates.TemplateResponse(
        request, "sam_report/_report_preview.html",
        {"upload_id": combined.dir, "rev": rev, "project_rec": project, "analysis": combined})


@router.post("/report/{upload_id}/generate", response_class=HTMLResponse)
async def generate_report(request: Request, upload_id: str,
                          db: Session = Depends(get_db),
                          user: User | None = Depends(get_current_user)):
    """Generate the report (docx + pdf), file it as a project revision, and
    return the preview fragment."""
    _require_upload(upload_id)
    form = await request.form()
    project = _project_from_form(form)
    labels = _labels_from_form(form)

    # Resolve project + analysis (scenario) + the revision this will be filed as
    # BEFORE generating, so the report header shows the actual number. It's baked
    # into the docx here and pinned when filing (save_revision(new_rev=...)) so the
    # header can never disagree with the revision folder it lands in.
    pid = report_service.get_upload_project_id(upload_id)
    proj_rec = db.get(Project, pid) if pid else None
    if proj_rec is None:
        # Never silently skip filing — the whole point is that documents are saved.
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": "The upload is not linked to a project, so the report "
                        "could not be filed. Start the analysis from a project "
                        "page and try again."},
            status_code=500)
    analysis = report_service.get_or_create_analysis(db, proj_rec, upload_id, user)
    save_as = str(form.get("save_as", "new"))
    reissue = int(save_as) if save_as.isdigit() else None
    target_rev = reissue if reissue is not None else storage.next_rev_number(db, analysis)
    project.revision = str(target_rev)
    form_data = {k: str(v) for k, v in form.items() if not hasattr(v, "filename")}

    rev = None
    try:
        # DOCX->PDF conversion is sync (subprocess); run off the event loop.
        await run_in_threadpool(
            report_service.generate_report, upload_id, project, labels
        )
    except Exception as exc:  # noqa: BLE001 - surface generation failures
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": f"Report generation failed: {exc}"}, status_code=500,
        )

    # ── Version control: file the document set under the analysis ──
    try:
        rev = await run_in_threadpool(
            partial(
                storage.save_revision, db, proj_rec, user,
                report_service.collect_revision_files(upload_id), form_data,
                reissue, str(form.get("rev_label", "")),
                new_rev=(None if reissue is not None else target_rev),
                analysis=analysis,
            )
        )
    except Exception as exc:  # noqa: BLE001 - surface but keep the preview usable
        return templates.TemplateResponse(
            request, "sam_report/_error.html",
            {"message": f"Report generated but filing the revision failed: {exc}"},
            status_code=500)
    # Persist the submitted form so reopening this analysis pre-fills the last run.
    report_service.save_analysis_form(db, analysis, form_data)
    return templates.TemplateResponse(
        request, "sam_report/_report_preview.html",
        {"upload_id": upload_id, "rev": rev, "project_rec": proj_rec, "analysis": analysis},
    )


@router.get("/report/{upload_id}/document.pdf")
def report_pdf(upload_id: str, download: int = 0):
    """Serve the generated report PDF (inline for preview, attachment to download)."""
    _require_upload(upload_id)
    path = service._upload_dir(upload_id) / report_service.REPORT_PDF_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="SAM Report.pdf",
        content_disposition_type=("attachment" if download else "inline"),
    )


@router.get("/report/{upload_id}/document.docx")
def report_docx(upload_id: str):
    """Serve the editable Word version of the report."""
    _require_upload(upload_id)
    path = service._upload_dir(upload_id) / report_service.REPORT_DOCX_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="SAM Report.docx",
        content_disposition_type="attachment",
    )


@router.get("/download/{upload_id}/{filename}")
def download(upload_id: str, filename: str):
    """Serve a generated workbook from an upload's staging directory."""
    upload_root = Path(settings.upload_dir).resolve()
    # upload_id is a hex token we minted; filename must stay within its folder.
    if not upload_id.isalnum():
        raise HTTPException(status_code=404, detail="Not found")
    path = (upload_root / upload_id / Path(filename).name).resolve()
    if not path.is_file() or upload_root not in path.parents:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
