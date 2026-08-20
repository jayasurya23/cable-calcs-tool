"""Durable revision storage: every generated document set is filed under
data/projects/<project_id>/rev<N>/ with a DB record + audit trail."""
from __future__ import annotations

import json
import re
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

# Everything a revision can hold, in the order an engineer wants to see it.
# A revision folder is a flat list of filenames, which told the reader nothing:
# "SAM excel output.xlsx" and "SAM excel output - Output.xlsx" sat side by side
# with no hint that one is what they uploaded and the other is what we produced.
_OUTPUT_SUFFIX = " - Output"


def describe_file(name: str, stored: str | None = None,
                  siblings: set[str] | None = None) -> dict:
    """What a filed document IS, for labelling a download link.

    Returns {kind, label, icon, rank} — `rank` orders a revision's files so the
    issued report comes first and raw inputs last.

    `stored` is the name on disk when it differs from the display name; the
    issued-report test uses it, because an engineer's own file named
    "<hash>_SAM Report.pdf" strips to "SAM Report.pdf" and was being mistaken for
    the deliverable. `siblings` is the display names of the other files filed
    alongside, used to tell a generated "- Output" copy from a source workbook
    the engineer happened to name that way.
    """
    low = (name or "").lower()
    stem = Path(name or "").stem

    if (stored if stored is not None else name) in REQUIRED_DOCS:
        if low.endswith(".pdf"):
            return {"kind": "report-pdf", "label": "Report (PDF)", "icon": "📕", "rank": 0}
        return {"kind": "report-docx", "label": "Report (Word)", "icon": "📘", "rank": 1}

    if low.endswith((".xlsx", ".xlsm")):
        # We generate "<source stem> - Output<ext>" NEXT TO the source, so it is
        # only the generated copy when that source is also here. Otherwise the
        # engineer simply named their own workbook that way, and calling it our
        # output meant no source workbook was offered at all.
        if stem.endswith(_OUTPUT_SUFFIX):
            origin = stem[: -len(_OUTPUT_SUFFIX)] + Path(name).suffix
            if siblings is None or origin in siblings:
                return {"kind": "output-workbook",
                        "label": "Workbook with Output sheet", "icon": "📊", "rank": 2}
        return {"kind": "source-workbook",
                "label": "Source workbook (as uploaded)", "icon": "📗", "rank": 3}

    if low.endswith(".json"):
        return {"kind": "pysam", "label": "pysam inputs (JSON)", "icon": "🧾", "rank": 4}
    if low.endswith(".pdf"):
        return {"kind": "datasheet", "label": "Datasheet (PDF)", "icon": "📄", "rank": 5}
    return {"kind": "other", "label": "Supporting file", "icon": "📎", "rank": 6}


# Extra module workbooks are staged (and filed) as "<8 hex>_<original name>".
# That prefix keeps two modules with the same filename apart on disk; it is not
# something an engineer should have to read.
_STAGED_PREFIX = re.compile(r"^[0-9a-f]{8}_")


def display_name(name: str) -> str:
    return _STAGED_PREFIX.sub("", Path(name or "").name)


def describe_files(names) -> list[dict]:
    """Every filed document, labelled and ordered.

    Each item carries `name` (what to show) and `path` (what to ask for). They
    differ for an extra module's workbook, which is stored under a subdirectory
    and a hashed filename.
    """
    names = list(names or [])
    shown_all = {display_name(n) for n in names}
    out = []
    for i, n in enumerate(names):
        shown = display_name(n)
        out.append({**describe_file(shown, stored=Path(n).name, siblings=shown_all),
                    "name": shown, "path": n, "order": i})
    # Displayed grouped by kind; `order` preserves how they were filed, which is
    # the only thing that identifies module 1's workbook among several sources.
    out.sort(key=lambda d: (d["rank"], d["name"].lower()))
    return out


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
