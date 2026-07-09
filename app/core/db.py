"""SQLAlchemy engine/session. SQLite (standalone) by default; DATABASE_URL swaps it."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
# pool_pre_ping: App Service / Azure Postgres drop idle connections; without this
# the first query after an idle period fails ("server closed the connection").
engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=not _is_sqlite,
    pool_recycle=(280 if not _is_sqlite else -1),  # < Azure's ~4-min idle cutoff
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate(conn) -> None:
    """Idempotent, dialect-aware schema migration (no Alembic). Runs inside the
    same transaction/advisory-lock as create_all so concurrent workers serialize.

    Brings a live `revisions` table (originally per-project) up to the
    per-analysis model: adds the analysis_id column, backfills one "Default
    analysis" per project for existing rows, and swaps the unique constraint from
    (project_id, rev_number) to (analysis_id, rev_number). Safe to re-run — every
    step is guarded, so once migrated it no-ops.
    """
    from sqlalchemy import text
    d = conn.dialect.name

    # 1) revisions.analysis_id column.
    if d == "postgresql":
        conn.exec_driver_sql(
            "ALTER TABLE revisions ADD COLUMN IF NOT EXISTS analysis_id INTEGER "
            "REFERENCES analyses(id)")
    else:  # sqlite has no ADD COLUMN IF NOT EXISTS
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(revisions)").fetchall()}
        if "analysis_id" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE revisions ADD COLUMN analysis_id INTEGER REFERENCES analyses(id)")

    # 2) Backfill: one Default analysis per project that still has orphan revisions.
    now_fn = "now()" if d == "postgresql" else "CURRENT_TIMESTAMP"
    orphan_pids = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT project_id FROM revisions WHERE analysis_id IS NULL")).fetchall()]
    for pid in orphan_pids:
        ins = text(
            "INSERT INTO analyses (project_id, name, status, kind, dir, form_json, "
            f"created_at, updated_at) VALUES (:pid, 'Default analysis', 'active', "
            f"'sam_report', '', '{{}}', {now_fn}, {now_fn})"
            + (" RETURNING id" if d == "postgresql" else ""))
        if d == "postgresql":
            aid = conn.execute(ins, {"pid": pid}).scalar()
        else:
            conn.execute(ins, {"pid": pid})
            aid = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        conn.execute(text(
            "UPDATE revisions SET analysis_id = :aid "
            "WHERE project_id = :pid AND analysis_id IS NULL"), {"aid": aid, "pid": pid})

    # 3) Swap the unique constraint (Postgres only; a fresh SQLite table already
    #    declares uq_analysis_rev via the model metadata). After backfill the data
    #    is unique on (analysis_id, rev_number), so ADD CONSTRAINT can't fail.
    if d == "postgresql":
        conn.exec_driver_sql("ALTER TABLE revisions DROP CONSTRAINT IF EXISTS uq_project_rev")
        if not conn.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'uq_analysis_rev'")).fetchone():
            conn.exec_driver_sql(
                "ALTER TABLE revisions ADD CONSTRAINT uq_analysis_rev "
                "UNIQUE (analysis_id, rev_number)")


def init_db() -> None:
    """Create tables + run migrations on startup (import models first so they register).

    Multiple gunicorn workers call this concurrently; on a fresh Postgres DB two
    `create_all`s would race and one would crash with DuplicateTable. A
    transaction-scoped advisory lock serializes it (the 2nd worker then sees the
    tables already exist and no-ops). SQLite is single-worker locally.
    """
    from app.core import models  # noqa: F401
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT pg_advisory_xact_lock(727274)")
            Base.metadata.create_all(conn)
            _migrate(conn)
    else:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            _migrate(conn)
