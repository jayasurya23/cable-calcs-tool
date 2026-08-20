"""Client and Portfolio routes — the two levels above a project.

A client owns portfolios and may also own projects directly, so a one-off site
needs no invented portfolio. Nothing below Project changed: analyses, revisions
and every existing /projects/{id} link keep working exactly as before.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.deps import require_user
from app.core.models import AuditEvent, Client, Portfolio, Project, User
from app.core.templating import templates

router = APIRouter(prefix="/clients", tags=["clients"])
portfolio_router = APIRouter(prefix="/portfolios", tags=["portfolios"])


def _client(db: Session, client_id: int) -> Client:
    obj = db.get(Client, client_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return obj


def _portfolio(db: Session, portfolio_id: int) -> Portfolio:
    obj = db.get(Portfolio, portfolio_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return obj


def _counts(db: Session) -> tuple[dict, dict, dict]:
    """Project and portfolio counts per client, and projects per portfolio.

    Three grouped queries rather than touching .projects on every row, which
    would be one query per client and per portfolio on a page that lists many.
    """
    proj_by_client = dict(db.execute(
        select(Project.client_id, func.count(Project.id)).group_by(Project.client_id)).all())
    pf_by_client = dict(db.execute(
        select(Portfolio.client_id, func.count(Portfolio.id))
        .group_by(Portfolio.client_id)).all())
    proj_by_pf = dict(db.execute(
        select(Project.portfolio_id, func.count(Project.id))
        .where(Project.portfolio_id.isnot(None))
        .group_by(Project.portfolio_id)).all())
    return proj_by_client, pf_by_client, proj_by_pf


# ── clients ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def client_list(request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_user)):
    clients = db.scalars(select(Client).order_by(Client.status, Client.name)).all()
    proj_by_client, pf_by_client, _ = _counts(db)
    return templates.TemplateResponse(request, "clients/list.html", {
        "clients": clients,
        "project_counts": proj_by_client,
        "portfolio_counts": pf_by_client,
        "unfiled": db.scalar(select(func.count(Project.id))
                             .where(Project.client_id.is_(None))) or 0,
    })


@router.post("")
def client_create(request: Request, name: str = Form(""), code: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_user)):
    name = name.strip()
    if not name:
        return RedirectResponse("/clients?error=name", status_code=303)
    obj = Client(name=name, code=code.strip(), created_by=user.id)
    db.add(obj)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="client.create",
                      detail=f"{obj.name} (#{obj.id})"))
    db.commit()
    return RedirectResponse(f"/clients/{obj.id}", status_code=303)


@router.get("/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    client = _client(db, client_id)
    portfolios = db.scalars(select(Portfolio)
                            .where(Portfolio.client_id == client_id)
                            .order_by(Portfolio.status, Portfolio.name)).all()
    # Projects sitting directly under the client — the "no portfolio" case.
    direct = db.scalars(select(Project)
                        .where(Project.client_id == client_id,
                               Project.portfolio_id.is_(None))
                        .order_by(Project.name)).all()
    _, _, proj_by_pf = _counts(db)
    return templates.TemplateResponse(request, "clients/detail.html", {
        "client": client, "portfolios": portfolios, "direct_projects": direct,
        "portfolio_project_counts": proj_by_pf,
        "total_projects": db.scalar(select(func.count(Project.id))
                                    .where(Project.client_id == client_id)) or 0,
    })


@router.post("/{client_id}")
def client_update(request: Request, client_id: int, name: str = Form(""),
                  code: str = Form(""), notes: str = Form(""), status: str = Form("active"),
                  db: Session = Depends(get_db), user: User = Depends(require_user)):
    client = _client(db, client_id)
    client.name = name.strip() or client.name
    client.code = code.strip()
    client.notes = notes
    client.status = "archived" if status == "archived" else "active"
    db.add(AuditEvent(user_id=user.id, action="client.update", detail=client.name))
    db.commit()
    return RedirectResponse(f"/clients/{client_id}?saved=1", status_code=303)


@router.post("/{client_id}/delete")
def client_delete(client_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    """Only ever removes an EMPTY client.

    Deleting a client that still holds work would cascade into projects,
    analyses, revisions and their filed documents — far more destruction than
    the button implies. Move its projects out first.
    """
    client = _client(db, client_id)
    n = db.scalar(select(func.count(Project.id)).where(Project.client_id == client_id)) or 0
    p = db.scalar(select(func.count(Portfolio.id))
                  .where(Portfolio.client_id == client_id)) or 0
    if n or p:
        return RedirectResponse(f"/clients/{client_id}?error=notempty", status_code=303)
    db.delete(client)
    db.add(AuditEvent(user_id=user.id, action="client.delete", detail=client.name))
    db.commit()
    return RedirectResponse("/clients", status_code=303)


@router.post("/{client_id}/portfolios")
def portfolio_create(client_id: int, name: str = Form(""), code: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    _client(db, client_id)
    name = name.strip()
    if not name:
        return RedirectResponse(f"/clients/{client_id}?error=name", status_code=303)
    obj = Portfolio(client_id=client_id, name=name, code=code.strip(), created_by=user.id)
    db.add(obj)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="portfolio.create",
                      detail=f"{obj.name} (#{obj.id})"))
    db.commit()
    return RedirectResponse(f"/portfolios/{obj.id}", status_code=303)


# ── portfolios ───────────────────────────────────────────────────────────

@portfolio_router.get("/{portfolio_id}", response_class=HTMLResponse)
def portfolio_detail(request: Request, portfolio_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    pf = _portfolio(db, portfolio_id)
    projects = db.scalars(select(Project)
                          .where(Project.portfolio_id == portfolio_id)
                          .order_by(Project.name)).all()
    return templates.TemplateResponse(request, "portfolios/detail.html", {
        "portfolio": pf, "client": pf.client, "projects": projects,
    })


@portfolio_router.post("/{portfolio_id}")
def portfolio_update(portfolio_id: int, name: str = Form(""), code: str = Form(""),
                     notes: str = Form(""), status: str = Form("active"),
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    pf = _portfolio(db, portfolio_id)
    pf.name = name.strip() or pf.name
    pf.code = code.strip()
    pf.notes = notes
    pf.status = "archived" if status == "archived" else "active"
    db.add(AuditEvent(user_id=user.id, action="portfolio.update", detail=pf.name))
    db.commit()
    return RedirectResponse(f"/portfolios/{portfolio_id}?saved=1", status_code=303)


@portfolio_router.post("/{portfolio_id}/delete")
def portfolio_delete(portfolio_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    """Only ever removes an EMPTY portfolio — see client_delete."""
    pf = _portfolio(db, portfolio_id)
    n = db.scalar(select(func.count(Project.id))
                  .where(Project.portfolio_id == portfolio_id)) or 0
    if n:
        return RedirectResponse(f"/portfolios/{portfolio_id}?error=notempty", status_code=303)
    client_id = pf.client_id
    db.delete(pf)
    db.add(AuditEvent(user_id=user.id, action="portfolio.delete", detail=pf.name))
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
