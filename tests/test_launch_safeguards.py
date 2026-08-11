"""Safeguards added for the engineer/intern handover.

These cover the three failure modes that lose work or confuse a new user:
draft autosave, the in-app help page, and the concurrent-edit guard.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An isolated app instance: its own DB and upload/data dirs.

    Patches the live `settings` object and the db module's engine/SessionLocal in
    place rather than reloading modules — every app module holds a reference to
    the same settings instance, and `get_db` looks SessionLocal up at call time.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.db as dbmod
    from app.config import settings
    from app.core.models import Base
    from app.main import app as fastapi_app

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(settings, "upload_dir", str(uploads))
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "auth_mode", "local")

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}",
                           connect_args={"check_same_thread": False})
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    Base.metadata.create_all(engine)

    with TestClient(fastapi_app) as c:
        c.post("/setup", data={"name": "T", "email": "t@t.com", "password": "password123"})
        c.post("/projects", data={"name": "P", "code": "P-1"})
        yield c


def _session():
    import app.core.db as dbmod
    return dbmod.SessionLocal()


def _upload(client, sam_workbook):
    """Create an analysis from a workbook; returns (upload_id, html)."""
    from app.core.models import Project
    with _session() as db:
        pid = db.query(Project).first().id
    with open(sam_workbook, "rb") as fh:
        r = client.post("/sam/upload",
                        data={"project_id": str(pid), "analysis_name": "A"},
                        files={"workbook": ("runs.xlsx", fh.read(),
                                            "application/vnd.openxmlformats-"
                                            "officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    return re.search(r"/sam/report/([0-9a-f]{6,})/", r.text).group(1), r.text


def test_help_page_renders_the_user_manual(client):
    r = client.get("/help")
    assert r.status_code == 200
    # Rendered markdown, not raw source.
    assert "<h1" in r.text or "<h2" in r.text
    assert "# " not in r.text.split("<article")[-1][:200]


def test_draft_autosave_persists_and_prefills(client, sam_workbook):
    from app.core.models import Analysis
    _upload(client, sam_workbook)
    with _session() as db:
        aid = db.query(Analysis).first().id

    r = client.post(f"/sam/analysis/{aid}/draft",
                    data={"project_name": "Draft Co", "eor": "A. Engineer, PE 1"})
    assert r.status_code == 204

    with _session() as db:
        saved = json.loads(db.get(Analysis, aid).form_json or "{}")
    assert saved["project_name"] == "Draft Co"
    assert saved["eor"] == "A. Engineer, PE 1"

    # Reopening the analysis pre-fills from that draft.
    html = client.get(f"/sam/analysis/{aid}").text
    assert 'value="Draft Co"' in html


def test_report_form_carries_the_concurrency_token(client, sam_workbook):
    _, html = _upload(client, sam_workbook)
    assert 'name="expected_rev"' in html
    assert "data-draft-url" in html


def test_stale_form_is_refused_instead_of_clobbering(client, sam_workbook):
    upload_id, _ = _upload(client, sam_workbook)
    base = {"project_name": "P", "project_id": "P-1",
            "date": "01/01/2026", "save_as": "new"}

    # First generate files R0.
    r = client.post(f"/sam/report/{upload_id}/generate", data={**base, "expected_rev": "0"})
    assert r.status_code == 200

    # A second submit from the SAME (now stale) form must be refused, not filed.
    r = client.post(f"/sam/report/{upload_id}/generate", data={**base, "expected_rev": "0"})
    assert r.status_code == 409
    assert "changed while you were editing" in r.text

    # A form rendered fresh (expecting R1) proceeds.
    r = client.post(f"/sam/report/{upload_id}/generate", data={**base, "expected_rev": "1"})
    assert r.status_code == 200


def test_reissue_is_not_blocked_by_the_guard(client, sam_workbook):
    """Re-issuing an existing revision deliberately targets an older number, so
    the stale-form check must not fire on it."""
    upload_id, _ = _upload(client, sam_workbook)
    base = {"project_name": "P", "project_id": "P-1", "date": "01/01/2026"}
    client.post(f"/sam/report/{upload_id}/generate",
                data={**base, "save_as": "new", "expected_rev": "0"})
    r = client.post(f"/sam/report/{upload_id}/generate",
                    data={**base, "save_as": "0", "expected_rev": "0"})
    assert r.status_code == 200
