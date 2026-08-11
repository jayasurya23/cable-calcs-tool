"""Project routes: list/create/edit, detail with revision history, downloads."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.deps import require_user
from app.core.models import Analysis, AuditEvent, Project, Revision, User
from app.core.templating import templates
from . import storage

router = APIRouter(prefix="/projects", tags=["projects"])

_COVER_FIELDS = ["owner_name", "owner_phone", "epc_name", "epc_phone",
                 "eng_firm_name", "eng_firm_phone", "eor", "designer", "checker"]


class ProjectNotFound(Exception):
    """Raised instead of a bare 404 so the handler can render a page with a way out."""


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectNotFound()
    return project


def _same_origin(request: Request) -> bool:
    """Light CSRF guard for destructive POSTs: a browser Origin header (sent on
    same-origin form posts) must match the request host."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients / older agents
    return origin.rstrip("/").endswith(f"//{request.url.netloc}")


@router.get("", response_class=HTMLResponse)
def project_list(request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    """The project list, with per-project state computed in two grouped queries.

    Previously this lazy-loaded `p.analyses` per card (and the creator per card),
    which is one query per project plus a full form_json payload just to render a
    count. Filtering/sorting is done client-side in the template — every project
    is already on the page, so it's instant and keeps this route simple.
    """
    projects = db.scalars(
        select(Project).options(selectinload(Project.creator))
        .order_by(Project.created_at.desc())).all()

    a_rows = db.execute(
        select(Analysis.project_id,
               func.count(Analysis.id),
               func.max(Analysis.updated_at))
        .group_by(Analysis.project_id)).all()
    # Analyses that have actually issued something, and the last time any did.
    r_rows = db.execute(
        select(Revision.project_id,
               func.count(func.distinct(Revision.analysis_id)),
               func.max(Revision.created_at))
        .group_by(Revision.project_id)).all()

    a_stats = {pid: (n, last) for pid, n, last in a_rows}
    r_stats = {pid: (n, last) for pid, n, last in r_rows}
    stats = {}
    for p in projects:
        n_analyses, last_analysis = a_stats.get(p.id, (0, None))
        n_issued, last_issue = r_stats.get(p.id, (0, None))
        stats[p.id] = {
            "analyses": n_analyses,
            "issued": n_issued,
            "last_activity": max([d for d in (last_analysis, last_issue) if d],
                                 default=p.created_at),
        }
    return templates.TemplateResponse(request, "projects/list.html",
                                      {"projects": projects, "stats": stats})


@router.post("")
def project_create(db: Session = Depends(get_db), user: User = Depends(require_user),
                   name: str = Form(...), code: str = Form("")):
    project = Project(name=name.strip(), code=code.strip(), created_by=user.id)
    db.add(project)
    db.flush()
    db.add(AuditEvent(user_id=user.id, project_id=project.id,
                      action="project.create", detail=project.name))
    db.commit()
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    project = _get_project(db, project_id)
    # Most-recently-worked-on first: `updated_at` moves whenever the analysis is
    # edited or re-run, so the one you were just in is at the top.
    analyses = db.scalars(
        select(Analysis).options(selectinload(Analysis.creator))
        .where(Analysis.project_id == project.id)
        .order_by(Analysis.updated_at.desc())).all()

    # One query for every revision on the page (was one per analysis, plus a user
    # lookup per revision), grouped in Python.
    analysis_revs: dict[int, list[Revision]] = {a.id: [] for a in analyses}
    rev_docs: dict[int, dict] = {}   # per-revision: the PDF deliverable vs supporting files
    if analyses:
        revs = db.scalars(
            select(Revision).options(selectinload(Revision.creator))
            .where(Revision.analysis_id.in_([a.id for a in analyses]))
            .order_by(Revision.rev_number.desc())).all()
        for r in revs:
            analysis_revs.setdefault(r.analysis_id, []).append(r)
            files = json.loads(r.files_json or "[]")
            pdf = next((f for f in files if f.lower().endswith(".pdf")), None)
            docx = next((f for f in files if f.lower().endswith(".docx")), None)
            rev_docs[r.id] = {"pdf": pdf, "docx": docx,
                              "others": [f for f in files if f != pdf]}
    return templates.TemplateResponse(request, "projects/detail.html",
                                      {"project": project, "analyses": analyses,
                                       "analysis_revs": analysis_revs,
                                       "rev_docs": rev_docs,
                                       "saved": request.query_params.get("saved")})


@router.post("/{project_id}")
async def project_update(request: Request, project_id: int,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_user)):
    project = _get_project(db, project_id)
    form = await request.form()
    project.name = str(form.get("name", project.name)).strip() or project.name
    project.code = str(form.get("code", project.code)).strip()
    project.notes = str(form.get("notes", project.notes))
    project.status = "archived" if form.get("status") == "archived" else "active"
    for f in _COVER_FIELDS:
        if f in form:
            setattr(project, f, str(form.get(f, "")).strip())
    db.add(AuditEvent(user_id=user.id, project_id=project.id,
                      action="project.update", detail=project.name))
    db.commit()
    return RedirectResponse(f"/projects/{project.id}?saved=1", status_code=303)


@router.post("/{project_id}/delete")
def project_delete(request: Request, project_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    """Permanently delete a project and everything under it."""
    project = _get_project(db, project_id)
    if not _same_origin(request):
        return RedirectResponse(f"/projects/{project_id}", status_code=303)
    storage.delete_project(db, project, user)
    return RedirectResponse("/projects", status_code=303)


@router.post("/{project_id}/analysis/{analysis_id}/delete")
def analysis_delete(request: Request, project_id: int, analysis_id: int,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Permanently delete one analysis (and its revisions) under a project."""
    project = _get_project(db, project_id)
    analysis = db.get(Analysis, analysis_id)
    if analysis is None or analysis.project_id != project.id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if not _same_origin(request):
        return RedirectResponse(f"/projects/{project_id}", status_code=303)
    storage.delete_analysis(db, analysis, user)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/{project_id}/analysis/{analysis_id}/rev/{rev_number}/{filename}")
def revision_download(request: Request, project_id: int, analysis_id: int,
                      rev_number: int, filename: str, db: Session = Depends(get_db),
                      user: User = Depends(require_user)):
    project = _get_project(db, project_id)
    rev = db.scalar(select(Revision).where(Revision.analysis_id == analysis_id,
                                           Revision.rev_number == rev_number))
    path = storage.revision_file(project, rev, filename) if rev and rev.project_id == project.id else None
    if path is None:
        return templates.TemplateResponse(request, "error_page.html", {
            "title": "Document unavailable",
            "message": "This document isn’t on the server anymore. It may have been filed "
                       "before document storage was in place, or the file was removed. "
                       "Other revisions of this analysis may still be downloadable.",
            "back_url": f"/projects/{project_id}",
        }, status_code=404)
    return FileResponse(path, filename=path.name)
