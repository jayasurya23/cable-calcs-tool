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


def init_db() -> None:
    """Create tables on startup (import models first so they register).

    Multiple gunicorn workers call this concurrently; on a fresh Postgres DB two
    `create_all`s would race and one would crash with DuplicateTable. A
    transaction-scoped advisory lock serializes it (the 2nd worker then sees the
    tables already exist and no-ops). SQLite is single-worker locally, so skip.
    """
    from app.core import models  # noqa: F401
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT pg_advisory_xact_lock(727274)")
            Base.metadata.create_all(conn)
    else:
        Base.metadata.create_all(engine)
