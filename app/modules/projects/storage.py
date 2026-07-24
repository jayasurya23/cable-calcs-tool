"""Durable revision storage: every generated document set is filed under
data/projects/<project_id>/rev<N>/ with a DB record + audit trail."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.models import Analysis, AuditEvent, Project, Revision, User

# A filed revision must at minimum contain the issued report documents.
REQUIRED_DOCS = {"SAM Report.docx", "SAM Report.pdf"}


def revision_dir(analysis: Analysis, rev_number: int) -> Path:
    return (Path(settings.data_dir) / "projects" / str(analysis.project_id)
            / "analyses" / str(analysis.id) / f"rev{rev_number}")


def next_rev_number(db: Session, analysis: Analysis) -> int:
    """Next revision number for THIS analysis — each scenario restarts at R0."""
    last = db.scalar(select(Revision.rev_number)
                     .where(Revision.analysis_id == analysis.id)
                     .order_by(Revision.rev_number.desc()).limit(1))
    return 0 if last is None else last + 1


def save_revision(db: Session, project: Project, user: User | None,
                  files: list[Path], form_data: dict,
                  reissue_rev: int | None = None, label: str = "",
                  new_rev: int | None = None,
                  analysis: Analysis | None = None) -> Revision:
    """File a document set as a new revision (default) or re-issue an existing one,
    under the given `analysis` (scenario) — revision numbers restart per analysis.

    Files are staged into a temp directory and atomically swapped in, so a
    mid-copy failure can never destroy a previously issued document set.

    new_rev pins the number for the *new* path: the caller may bake this number
    into the documents (e.g. the report header) before calling, so we file under
    exactly it — and if a concurrent save already took it, we raise rather than
    silently filing under a different number that would mismatch the documents.
    """
    if analysis is None:
        raise ValueError("save_revision requires an analysis")
    present = {Path(f).name for f in files if Path(f).is_file()}
    missing = REQUIRED_DOCS - present
    if missing:
        raise ValueError(f"Cannot file revision — missing document(s): {', '.join(sorted(missing))}")

    if reissue_rev is not None:
        rev = db.scalar(select(Revision).where(Revision.analysis_id == analysis.id,
                                               Revision.rev_number == reissue_rev))
        if rev is None:
            raise ValueError(f"Revision R{reissue_rev} does not exist")
        rev.reissue_count += 1
        action = "revision.reissue"
    elif new_rev is not None:
        # Pinned number (baked into the documents). File under exactly it; on a
        # concurrent collision, fail loudly so the caller regenerates rather than
        # filing docs whose header says a different revision.
        rev = Revision(project_id=project.id, analysis_id=analysis.id,
                       rev_number=new_rev, created_by=(user.id if user else None))
        db.add(rev)
        try:
            db.flush()  # trips uq_analysis_rev on collision
        except IntegrityError:
            db.rollback()
            raise ValueError(
                f"Revision R{new_rev} was just filed by another save — "
                "please regenerate the report so its header matches.")
        action = "revision.create"
    else:
        # SELECT-max then INSERT can race under concurrency; the unique
        # constraint backstops it — retry with a fresh number.
        for attempt in range(3):
            rev = Revision(project_id=project.id, analysis_id=analysis.id,
                           rev_number=next_rev_number(db, analysis),
                           created_by=(user.id if user else None))
            db.add(rev)
            try:
                db.flush()  # assign rev.id; trips uq_analysis_rev on collision
                break
            except IntegrityError:
                db.rollback()
                if attempt == 2:
                    raise
        action = "revision.create"

    dest = revision_dir(analysis, rev.rev_number)

    # Stage everything first; only then swap directories.
    staging = dest.with_name(f"{dest.name}.new{uuid.uuid4().hex[:6]}")
    staging.mkdir(parents=True)
    stored: list[str] = []
    try:
        for f in files:
            f = Path(f)
            if f.is_file():
                shutil.copy2(f, staging / f.name)
                stored.append(f.name)
        (staging / "form.json").write_text(json.dumps(form_data, indent=2),
                                           encoding="utf-8")
        backup = dest.with_name(f"{dest.name}.bak{uuid.uuid4().hex[:6]}")
        if dest.exists():
            dest.rename(backup)
        try:
            staging.rename(dest)
        except OSError:
            if backup.exists():          # restore the prior document set
                backup.rename(dest)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    rev.label = label.strip() or rev.label
    rev.form_json = json.dumps(form_data)
    rev.files_json = json.dumps(stored)
    rev.dir = str(dest.relative_to(settings.data_dir))
    db.add(AuditEvent(user_id=(user.id if user else None), project_id=project.id,
                      revision_id=rev.id, action=action,
                      detail=f"R{rev.rev_number}: {', '.join(stored)}"))
    db.commit()
    return rev


def _safe_rmtree(path: Path, must_be_under: Path) -> None:
    """rm -rf a directory, but only if it resolves strictly inside
    `must_be_under` — never the root itself or anything outside it (guards
    against a blank/garbage token wiping an unexpected location)."""
    try:
        root = must_be_under.resolve()
        target = path.resolve()
    except OSError:
        return
    if target == root or root not in target.parents:
        return
    shutil.rmtree(target, ignore_errors=True)


def delete_analysis(db: Session, analysis: Analysis, user: User | None) -> None:
    """Delete one analysis (scenario): its DB rows (revisions cascade) plus its
    filed revision directory and its upload working dir. The parent project and
    its other analyses are untouched."""
    pid = analysis.project_id
    name = analysis.name or "analysis"
    _safe_rmtree(Path(settings.data_dir) / "projects" / str(pid)
                 / "analyses" / str(analysis.id), Path(settings.data_dir))
    if analysis.dir:
        _safe_rmtree(Path(settings.upload_dir) / analysis.dir, Path(settings.upload_dir))
    db.delete(analysis)  # cascade removes its revisions
    db.add(AuditEvent(user_id=(user.id if user else None), project_id=pid,
                      action="analysis.delete", detail=name))
    db.commit()


def delete_project(db: Session, project: Project, user: User | None) -> None:
    """Delete a project and everything under it — analyses, revisions, and all
    generated documents on disk. Irreversible."""
    pid = project.id
    name = project.name
    for a in list(project.analyses):        # each analysis's upload working dir
        if a.dir:
            _safe_rmtree(Path(settings.upload_dir) / a.dir, Path(settings.upload_dir))
    _safe_rmtree(Path(settings.data_dir) / "projects" / str(pid), Path(settings.data_dir))
    # Unlink audit history first so the project row can be deleted (audit_events
    # has a FK to projects.id, enforced on Postgres — keep the log, drop the link).
    db.query(AuditEvent).filter(AuditEvent.project_id == pid).update(
        {AuditEvent.project_id: None}, synchronize_session=False)
    db.delete(project)  # cascade removes analyses + revisions
    db.add(AuditEvent(user_id=(user.id if user else None),
                      action="project.delete", detail=name))
    db.commit()


def revision_file(project: Project, rev: Revision, filename: str) -> Path | None:
    """Resolve a stored file safely inside the revision directory
    (basename only — no traversal)."""
    base = (Path(settings.data_dir) / rev.dir).resolve()
    path = (base / Path(filename).name).resolve()
    return path if (path.parent == base and path.is_file()) else None
