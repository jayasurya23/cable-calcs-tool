"""ORM models: users/sessions (auth), projects, document revisions, audit log."""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16), default="engineer")  # engineer | admin
    auth_provider: Mapped[str] = mapped_column(String(16), default="local")  # local | entra
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Client(Base):
    """Who the work is for. The top of the hierarchy: a client owns portfolios,
    and may also own one-off projects directly (see Project.portfolio_id)."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    notes: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    creator: Mapped[User | None] = relationship()
    portfolios: Mapped[list["Portfolio"]] = relationship(
        back_populates="client", order_by="Portfolio.name")
    projects: Mapped[list["Project"]] = relationship(
        back_populates="client", order_by="Project.name")


class Portfolio(Base):
    """A group of projects for one client. Optional: a project may sit directly
    under its client, so a one-off site needs no invented portfolio."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    creator: Mapped[User | None] = relationship()
    client: Mapped["Client"] = relationship(back_populates="portfolios")
    projects: Mapped[list["Project"]] = relationship(
        back_populates="portfolio", order_by="Project.name")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable in the schema so the migration can add the column and backfill it;
    # every project gets a client (an "Unassigned" one if nothing better is known).
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id"), nullable=True, index=True)
    # Optional level: a one-off site hangs straight off its client.
    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), default="")          # e.g. "259-034"
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    notes: Mapped[str] = mapped_column(Text, default="")

    # Cover-sheet defaults — prefill every report form for this project.
    owner_name: Mapped[str] = mapped_column(String(255), default="")
    owner_phone: Mapped[str] = mapped_column(String(64), default="")
    epc_name: Mapped[str] = mapped_column(String(255), default="")
    epc_phone: Mapped[str] = mapped_column(String(64), default="")
    eng_firm_name: Mapped[str] = mapped_column(String(255), default="Castillo Engineering Services LLC")
    eng_firm_phone: Mapped[str] = mapped_column(String(64), default="407-289-2575")
    eor: Mapped[str] = mapped_column(String(255), default="")
    designer: Mapped[str] = mapped_column(String(255), default="")
    checker: Mapped[str] = mapped_column(String(255), default="")

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    creator: Mapped[User | None] = relationship()
    client: Mapped["Client | None"] = relationship(back_populates="projects")
    portfolio: Mapped["Portfolio | None"] = relationship(back_populates="projects")
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="project", order_by="Revision.rev_number",
        cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="project", order_by="Analysis.created_at",
        cascade="all, delete-orphan")


class Analysis(Base):
    """A saved, editable SAM analysis (scenario) under a project. A project can
    hold several (e.g. different modules/configs); each has its own revision
    history (R0, R1, …). kind='sam_collated' is the auto scenario that holds
    combined reports built from several analyses."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")          # "Boviet 615W"
    status: Mapped[str] = mapped_column(String(16), default="active")   # active | archived
    kind: Mapped[str] = mapped_column(String(32), default="sam_report") # sam_report | sam_collated
    dir: Mapped[str] = mapped_column(String(512), default="")           # working+storage root under data_dir
    form_json: Mapped[str] = mapped_column(Text, default="{}")          # last-saved form snapshot (rehydration)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="analyses")
    creator: Mapped[User | None] = relationship()
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="analysis", order_by="Revision.rev_number",
        cascade="all, delete-orphan")


class Revision(Base):
    """One issued document set (SAM report) for an analysis: R0, R1, …"""
    __tablename__ = "revisions"
    # Rev numbers restart per analysis. (Legacy rows are backfilled to a default
    # analysis by the startup migration; see app.core.db._migrate.)
    __table_args__ = (UniqueConstraint("analysis_id", "rev_number", name="uq_analysis_rev"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # Nullable so the column can be added to the live table before backfill.
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id"), nullable=True, index=True)
    rev_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default="sam_report")
    label: Mapped[str] = mapped_column(String(255), default="")
    form_json: Mapped[str] = mapped_column(Text, default="{}")   # form-data snapshot
    files_json: Mapped[str] = mapped_column(Text, default="[]")  # stored filenames
    dir: Mapped[str] = mapped_column(String(512), default="")    # path under data_dir
    reissue_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="revisions")
    analysis: Mapped["Analysis | None"] = relationship(back_populates="revisions")
    creator: Mapped[User | None] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64))   # login, project.create, revision.create, …
    detail: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User | None] = relationship()
