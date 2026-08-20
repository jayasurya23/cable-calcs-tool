"""Client and Portfolio routes — the two levels above a project.

A client owns portfolios and may also own projects directly, so a one-off site
needs no invented portfolio. Nothing below Project changed: analyses, revisions
and every existing /projects/{id} link keep working exactly as before.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.deps import require_user
from app.core.models import AuditEvent, Client, Portfolio, Project, User
from app.core.templating import templates

router = APIRouter(prefix="/clients", tags=["clients"])
portfolio_router = APIRouter(prefix="/portfolios", tags=["portfolios"])


# The columns are String(255) / String(64); Postgres rejects anything longer
# with a 500 while SQLite silently accepts it, so a pasted address block would
# work in dev and fail in production. Trim at the boundary instead.
NAME_MAX, CODE_MAX = 255, 64


def _clean(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _name_taken(db: Session, model, name: str, client_id: int | None = None,
                exclude_id: int | None = None) -> bool:
    """Case-insensitive duplicate check. Two clients with the same name are
    indistinguishable in every dropdown, and a second "Unassigned" would shadow
    the one the migration parked projects under."""
    q = select(model.id).where(func.lower(model.name) == name.lower())
    if client_id is not None:
        q = q.where(model.client_id == client_id)
    if exclude_id is not None:
        q = q.where(model.id != exclude_id)
    return db.scalar(q) is not None


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
    name = _clean(name, NAME_MAX)
    if not name:
        return RedirectResponse("/clients?error=name", status_code=303)
    if _name_taken(db, Client, name):
        return RedirectResponse("/clients?error=duplicate", status_code=303)
    obj = Client(name=name, code=_clean(code, CODE_MAX), created_by=user.id)
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
        "adoptable": _adoptable(db, exclude_client=client_id),
    })


@router.post("/{client_id}")
def client_update(request: Request, client_id: int, name: str = Form(""),
                  code: str = Form(""), notes: str = Form(""), status: str = Form("active"),
                  db: Session = Depends(get_db), user: User = Depends(require_user)):
    client = _client(db, client_id)
    new_name = _clean(name, NAME_MAX) or client.name
    if new_name.lower() != client.name.lower() and _name_taken(db, Client, new_name):
        return RedirectResponse(f"/clients/{client_id}?error=duplicate", status_code=303)
    client.name = new_name
    client.code = _clean(code, CODE_MAX)
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
    name = client.name
    # Delete only if still empty AT THE MOMENT OF DELETION. Counting first and
    # deleting after leaves a window in which someone else files a project into
    # it, and the delete would then cascade through their work.
    result = db.execute(
        delete(Client).where(
            Client.id == client_id,
            ~select(Project.id).where(Project.client_id == client_id).exists(),
            ~select(Portfolio.id).where(Portfolio.client_id == client_id).exists(),
        ))
    if not result.rowcount:
        db.rollback()
        return RedirectResponse(f"/clients/{client_id}?error=notempty", status_code=303)
    db.add(AuditEvent(user_id=user.id, action="client.delete", detail=name))
    db.commit()
    return RedirectResponse("/clients", status_code=303)


@router.post("/{client_id}/portfolios")
def portfolio_create(client_id: int, name: str = Form(""), code: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    _client(db, client_id)
    name = _clean(name, NAME_MAX)
    if not name:
        return RedirectResponse(f"/clients/{client_id}?error=name", status_code=303)
    if _name_taken(db, Portfolio, name, client_id=client_id):
        return RedirectResponse(f"/clients/{client_id}?error=duplicate", status_code=303)
    obj = Portfolio(client_id=client_id, name=name, code=_clean(code, CODE_MAX),
                    created_by=user.id)
    db.add(obj)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="portfolio.create",
                      detail=f"{obj.name} (#{obj.id})"))
    db.commit()
    return RedirectResponse(f"/portfolios/{obj.id}", status_code=303)


def _adopt(db: Session, user: User, project_ids: list[str],
           client_id: int, portfolio_id: int | None) -> int:
    """File many projects at once. Returns how many actually moved.

    The migration parks every existing project under "Unassigned"; without this,
    sorting them means opening each project, expanding Move, choosing twice and
    being bounced back to that project — roughly eight clicks each.
    """
    ids = []
    for raw in project_ids or []:
        try:
            ids.append(int(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0
    moved = 0
    for project in db.scalars(select(Project).where(Project.id.in_(ids))).all():
        project.client_id = client_id
        project.portfolio_id = portfolio_id
        moved += 1
    if moved:
        db.add(AuditEvent(user_id=user.id, action="project.move.bulk",
                          detail=f"{moved} project(s) -> client #{client_id}"
                                 + (f", portfolio #{portfolio_id}" if portfolio_id else "")))
        db.commit()
    return moved


def _adoptable(db: Session, exclude_client: int | None = None,
               exclude_portfolio: int | None = None) -> list[Project]:
    """Projects that could be filed here — everything not already in this spot."""
    q = select(Project).options(selectinload(Project.client),
                                selectinload(Project.portfolio))
    if exclude_portfolio is not None:
        q = q.where((Project.portfolio_id.is_(None))
                    | (Project.portfolio_id != exclude_portfolio))
    elif exclude_client is not None:
        q = q.where((Project.client_id.is_(None))
                    | (Project.client_id != exclude_client)
                    | (Project.portfolio_id.isnot(None)))
    return db.scalars(q.order_by(Project.name)).all()


@router.post("/{client_id}/adopt")
def client_adopt(client_id: int, project_ids: list[str] = Form(default=[]),
                 db: Session = Depends(get_db), user: User = Depends(require_user)):
    _client(db, client_id)
    n = _adopt(db, user, project_ids, client_id, None)
    return RedirectResponse(f"/clients/{client_id}?moved={n}", status_code=303)


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
        "adoptable": _adoptable(db, exclude_portfolio=portfolio_id),
    })


@portfolio_router.post("/{portfolio_id}")
def portfolio_update(portfolio_id: int, name: str = Form(""), code: str = Form(""),
                     notes: str = Form(""), status: str = Form("active"),
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    pf = _portfolio(db, portfolio_id)
    new_name = _clean(name, NAME_MAX) or pf.name
    if new_name.lower() != pf.name.lower() and _name_taken(
            db, Portfolio, new_name, client_id=pf.client_id, exclude_id=pf.id):
        return RedirectResponse(f"/portfolios/{portfolio_id}?error=duplicate",
                                status_code=303)
    pf.name = new_name
    pf.code = _clean(code, CODE_MAX)
    pf.notes = notes
    pf.status = "archived" if status == "archived" else "active"
    db.add(AuditEvent(user_id=user.id, action="portfolio.update", detail=pf.name))
    db.commit()
    return RedirectResponse(f"/portfolios/{portfolio_id}?saved=1", status_code=303)


@portfolio_router.post("/{portfolio_id}/adopt")
def portfolio_adopt(portfolio_id: int, project_ids: list[str] = Form(default=[]),
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Filing into a portfolio also sets the project's client, so the two can
    never disagree about who the work belongs to."""
    pf = _portfolio(db, portfolio_id)
    n = _adopt(db, user, project_ids, pf.client_id, portfolio_id)
    return RedirectResponse(f"/portfolios/{portfolio_id}?moved={n}", status_code=303)


@portfolio_router.post("/{portfolio_id}/delete")
def portfolio_delete(portfolio_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    """Only ever removes an EMPTY portfolio — see client_delete."""
    pf = _portfolio(db, portfolio_id)
    client_id, name = pf.client_id, pf.name
    result = db.execute(
        delete(Portfolio).where(
            Portfolio.id == portfolio_id,
            ~select(Project.id).where(Project.portfolio_id == portfolio_id).exists(),
        ))
    if not result.rowcount:
        db.rollback()
        return RedirectResponse(f"/portfolios/{portfolio_id}?error=notempty",
                                status_code=303)
    db.add(AuditEvent(user_id=user.id, action="portfolio.delete", detail=name))
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
