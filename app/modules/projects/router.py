"""Project routes: list/create/edit, detail with revision history, downloads."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_user
from app.core.models import AuditEvent, Project, Revision, User
from app.core.templating import templates
from . import storage

router = APIRouter(prefix="/projects", tags=["projects"])

_COVER_FIELDS = ["owner_name", "owner_phone", "epc_name", "epc_phone",
                 "eng_firm_name", "eng_firm_phone", "eor", "designer", "checker"]


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_class=HTMLResponse)
def project_list(request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return templates.TemplateResponse(request, "projects/list.html",
                                      {"projects": projects})


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
    revisions = db.scalars(select(Revision).where(Revision.project_id == project.id)
                           .order_by(Revision.rev_number.desc())).all()
    rev_files = {r.id: json.loads(r.files_json or "[]") for r in revisions}
    return templates.TemplateResponse(request, "projects/detail.html",
                                      {"project": project, "revisions": revisions,
                                       "rev_files": rev_files,
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


@router.get("/{project_id}/rev/{rev_number}/{filename}")
def revision_download(project_id: int, rev_number: int, filename: str,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_user)):
    project = _get_project(db, project_id)
    rev = db.scalar(select(Revision).where(Revision.project_id == project.id,
                                           Revision.rev_number == rev_number))
    if rev is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    path = storage.revision_file(project, rev, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name)
