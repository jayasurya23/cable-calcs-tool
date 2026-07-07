"""Auth dependencies: resolve the session cookie to a user; gate routes.

`require_user` / `require_admin` raise NeedsLogin / Forbidden — main.py installs
handlers that redirect browsers to /login (and send HX-Redirect for HTMX).
"""
from __future__ import annotations

import datetime

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import AuthSession, User, utcnow
from app.core.security import token_hash

SESSION_COOKIE = "cable_session"


class NeedsLogin(Exception):
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


class Forbidden(Exception):
    pass


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.get(AuthSession, token_hash(token))
    if sess is None or sess.expires_at < utcnow():
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    request.state.user = user
    return user


def require_user(request: Request, user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        # Only GET targets are safe/meaningful post-login redirects (a POST path
        # would 405 when the browser GETs it after sign-in). Keep the query
        # string — /sam needs ?project=N.
        if request.method == "GET":
            next_url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        else:
            next_url = "/"
        raise NeedsLogin(next_url=next_url)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise Forbidden()
    return user


def create_session(db: Session, user: User, token: str, ttl_hours: int) -> None:
    # Opportunistic cleanup so the sessions table doesn't grow unbounded.
    db.query(AuthSession).filter(AuthSession.expires_at < utcnow()).delete()
    db.add(AuthSession(
        token_hash=token_hash(token),
        user_id=user.id,
        expires_at=utcnow() + datetime.timedelta(hours=ttl_hours),
    ))
    user.last_login = utcnow()
    db.commit()


def revoke_user_sessions(db: Session, user_id: int) -> None:
    """Kill every live session for a user (password reset / deactivation)."""
    db.query(AuthSession).filter(AuthSession.user_id == user_id).delete()
    db.commit()


def destroy_session(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.get(AuthSession, token_hash(token))
    if sess:
        db.delete(sess)
        db.commit()
