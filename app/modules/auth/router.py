"""Auth routes: login/logout (Entra ID or local), first-run setup, user admin.

Entra mode uses the OAuth2 authorization-code flow via MSAL. Users are
JIT-provisioned on first sign-in; emails in ADMIN_EMAILS become admins.
Local mode is the standalone fallback: the first run creates the admin, who
then creates engineer accounts on /admin/users.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.db import get_db
from app.core.deps import (SESSION_COOKIE, create_session, destroy_session,
                           require_admin, revoke_user_sessions)
from app.core.models import AuditEvent, User
from app.core.security import hash_password, new_session_token, verify_password
from app.core.templating import templates

# Constant-time-ish dummy verify target so unknown emails cost the same as a
# real PBKDF2 check (prevents user enumeration via response timing).
_DUMMY_HASH = hash_password("this-is-not-a-real-password")

router = APIRouter(tags=["auth"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])

_STATE_COOKIE = "oauth_state"
_ENTRA_SCOPES = ["User.Read"]


def _safe_next(next_url: str | None) -> str:
    """Only allow same-site relative redirect targets."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _same_origin(request: Request) -> bool:
    """Light CSRF guard for auth POSTs: if the browser sent an Origin header,
    it must match the request host."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients / older agents
    return origin.rstrip("/").endswith(f"//{request.url.netloc}")


def _msal_app():
    import msal
    return msal.ConfidentialClientApplication(
        settings.entra_client_id,
        client_credential=settings.entra_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
    )


def _no_users(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is None


def _provision(db: Session, email: str, name: str, provider: str) -> User | None:
    """Find-or-create a user on sign-in. Returns None if deactivated."""
    email = email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        role = "admin" if (email in settings.admin_email_list or _no_users(db)) else "engineer"
        user = User(email=email, name=name or email, role=role, auth_provider=provider)
        db.add(user)
        db.commit()
    return user if user.active else None


def _login_ok(request: Request, db: Session, user: User, next_url: str) -> RedirectResponse:
    token = new_session_token()
    create_session(db, user, token, settings.session_ttl_hours)
    db.add(AuditEvent(user_id=user.id, action="login", detail=user.email))
    db.commit()
    resp = RedirectResponse(_safe_next(next_url), status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    secure=settings.cookie_secure,
                    max_age=settings.session_ttl_hours * 3600)
    return resp


# ─── login / logout ──────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db), next: str = "/"):
    if settings.auth_mode == "local" and _no_users(db):
        return RedirectResponse("/setup", status_code=303)
    if settings.auth_mode == "entra":
        state = secrets.token_urlsafe(16)
        url = _msal_app().get_authorization_request_url(
            _ENTRA_SCOPES, state=state, redirect_uri=settings.entra_redirect_uri)
        resp = RedirectResponse(url, status_code=303)
        resp.set_cookie(_STATE_COOKIE, f"{state}|{_safe_next(next)}",
                        httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=600)
        return resp
    return templates.TemplateResponse(request, "auth/login.html",
                                      {"next": _safe_next(next), "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_local(request: Request, db: Session = Depends(get_db),
                email: str = Form(...), password: str = Form(...),
                next: str = Form("/")):
    # Password sign-in exists only in local mode — in Entra mode it would
    # bypass the org's MFA / conditional-access policies.
    if settings.auth_mode != "local" or not _same_origin(request):
        return RedirectResponse("/login", status_code=303)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.active or not user.password_hash:
        verify_password(password, _DUMMY_HASH)  # equalize timing (no enumeration)
        ok = False
    else:
        ok = verify_password(password, user.password_hash)
    if not ok:
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"next": _safe_next(next), "error": "Invalid email or password."},
            status_code=401)
    return _login_ok(request, db, user, next)


@router.get("/auth/callback")
def entra_callback(request: Request, db: Session = Depends(get_db),
                   code: str = "", state: str = ""):
    cookie = request.cookies.get(_STATE_COOKIE, "")
    saved_state, _, next_url = cookie.partition("|")
    if not code or not state or state != saved_state:
        return RedirectResponse("/login", status_code=303)
    result = _msal_app().acquire_token_by_authorization_code(
        code, scopes=_ENTRA_SCOPES, redirect_uri=settings.entra_redirect_uri)
    claims = result.get("id_token_claims") or {}
    email = claims.get("preferred_username") or claims.get("email") or ""
    if "error" in result or not email:
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"next": "/", "error": f"Microsoft sign-in failed: {result.get('error_description', 'no email claim')[:200]}"},
            status_code=401)
    user = _provision(db, email, claims.get("name", ""), "entra")
    if user is None:
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"next": "/", "error": "Your account has been deactivated."}, status_code=403)
    resp = _login_ok(request, db, user, next_url or "/")
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """POST-only: a state change must not be triggerable by a cross-site GET."""
    if _same_origin(request):
        destroy_session(db, request.cookies.get(SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ─── first-run setup (local mode) ────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if settings.auth_mode != "local" or not _no_users(db):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "auth/setup.html", {"error": None})


@router.post("/setup", response_class=HTMLResponse)
def setup_create(request: Request, db: Session = Depends(get_db),
                 name: str = Form(...), email: str = Form(...),
                 password: str = Form(...)):
    if settings.auth_mode != "local" or not _no_users(db):
        return RedirectResponse("/login", status_code=303)
    if len(password) < 8:
        return templates.TemplateResponse(request, "auth/setup.html",
                                          {"error": "Password must be at least 8 characters."})
    pw_hash = hash_password(password)  # slow (PBKDF2) — happens before the recheck
    if not _no_users(db):              # TOCTOU: another first-run submission won
        return RedirectResponse("/login", status_code=303)
    user = User(email=email.strip().lower(), name=name.strip(), role="admin",
                auth_provider="local", password_hash=pw_hash)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/login", status_code=303)
    return _login_ok(request, db, user, "/")


# ─── user management (admin) ─────────────────────────────────────────────────

@admin_router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_admin)):
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return templates.TemplateResponse(request, "auth/users.html",
                                      {"users": users, "auth_mode": settings.auth_mode,
                                       "error": request.query_params.get("error")})


@admin_router.post("/users/create")
def user_create(db: Session = Depends(get_db), admin: User = Depends(require_admin),
                name: str = Form(...), email: str = Form(...),
                password: str = Form(...), role: str = Form("engineer")):
    if settings.auth_mode != "local":
        return RedirectResponse("/admin/users?error=Password+accounts+are+disabled+in+Entra+mode",
                                status_code=303)
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        return RedirectResponse("/admin/users?error=Email+already+exists", status_code=303)
    if len(password) < 8:
        return RedirectResponse("/admin/users?error=Password+too+short+(min+8)", status_code=303)
    db.add(User(email=email, name=name.strip(),
                role=("admin" if role == "admin" else "engineer"),
                auth_provider="local", password_hash=hash_password(password)))
    db.add(AuditEvent(user_id=admin.id, action="user.create", detail=email))
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@admin_router.post("/users/{user_id}/role")
def user_role(user_id: int, db: Session = Depends(get_db),
              admin: User = Depends(require_admin), role: str = Form(...)):
    target = db.get(User, user_id)
    if target and not (target.id == admin.id and role != "admin"):  # can't demote self
        target.role = "admin" if role == "admin" else "engineer"
        db.add(AuditEvent(user_id=admin.id, action="user.role",
                          detail=f"{target.email} -> {target.role}"))
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@admin_router.post("/users/{user_id}/active")
def user_active(user_id: int, db: Session = Depends(get_db),
                admin: User = Depends(require_admin), active: str = Form(...)):
    target = db.get(User, user_id)
    if target and target.id != admin.id:  # can't deactivate self
        target.active = active == "1"
        db.add(AuditEvent(user_id=admin.id, action="user.active",
                          detail=f"{target.email} -> {target.active}"))
        db.commit()
        if not target.active:
            revoke_user_sessions(db, target.id)  # kill live sessions immediately
    return RedirectResponse("/admin/users", status_code=303)


@admin_router.post("/users/{user_id}/password")
def user_password(user_id: int, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), password: str = Form(...)):
    target = db.get(User, user_id)
    if target and target.auth_provider == "local" and len(password) >= 8:
        target.password_hash = hash_password(password)
        db.add(AuditEvent(user_id=admin.id, action="user.password", detail=target.email))
        db.commit()
        revoke_user_sessions(db, target.id)  # credential rotation kills sessions
        return RedirectResponse("/admin/users", status_code=303)
    return RedirectResponse("/admin/users?error=Reset+failed+(local+users,+min+8+chars)",
                            status_code=303)
