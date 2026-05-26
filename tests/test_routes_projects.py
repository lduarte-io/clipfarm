"""Tests for `clipfarm/routes/projects.py` — 6 routes (5 CRUD + parse
preview).

Carries Phase 4's `_count_snapshots_after_op` helper to enforce the
snapshot-per-op invariant on each mutating route. Lock-held assertion
on at least one mutating route per the Phase 2.1 pattern.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_BRIEF_V1 = """---
name: btc explainer v0.4
script:
  - intro line
  - body line
sections:
  - the hook
tags:
  - hook
---

# What's good

Energy.
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))

    from clipfarm.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        c.state_path = state_path
        c.snapshot_dir = state_path.parent / ".clipfarm" / "snapshots"
        yield c


def _count_snapshots_after_op(
    client: TestClient, op: Callable[[], None], *, expected_reason: str
) -> Path:
    before = set(client.snapshot_dir.glob("*.json")) if client.snapshot_dir.exists() else set()
    op()
    after = set(client.snapshot_dir.glob("*.json"))
    new = after - before
    assert len(new) == 1, (
        f"expected exactly 1 new snapshot, got {len(new)}"
    )
    snap_path = next(iter(new))
    assert expected_reason in snap_path.name
    return snap_path


# ---------- POST /api/projects/parse (read-only) ------------------------------


def test_parse_preview_happy_path(client: TestClient):
    r = client.post("/api/projects/parse", json={"brief_md": _BRIEF_V1})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "btc explainer v0.4"
    assert body["lines_count"] == 2
    assert body["sections"] == ["the hook"]
    assert body["tags"] == ["hook"]


def test_parse_preview_400_on_malformed_brief(client: TestClient):
    r = client.post("/api/projects/parse", json={"brief_md": "no frontmatter"})
    assert r.status_code == 400


def test_parse_preview_400_carries_line_info(client: TestClient):
    bad = "---\nname: ok\nscript: [unclosed\n---\n"
    r = client.post("/api/projects/parse", json={"brief_md": bad})
    assert r.status_code == 400
    detail = r.json()["detail"]
    # The exact position depends on PyYAML's reporting; just confirm we
    # surface something useful.
    assert "error" in detail


def test_parse_preview_does_not_snapshot(client: TestClient):
    before = (
        len(list(client.snapshot_dir.glob("*.json")))
        if client.snapshot_dir.exists()
        else 0
    )
    client.post("/api/projects/parse", json={"brief_md": _BRIEF_V1})
    after = (
        len(list(client.snapshot_dir.glob("*.json")))
        if client.snapshot_dir.exists()
        else 0
    )
    assert after == before  # read-only must not snapshot


# ---------- POST /api/projects (create) --------------------------------------


def test_create_happy_path_writes_one_snapshot(client: TestClient):
    # Seed the state file so there's a "prior" to snapshot. Without this,
    # the snapshot helper correctly returns None (Phase 1 behavior: can't
    # snapshot what doesn't exist), and the test would see 0 new files.
    client.post("/api/projects", json={"brief_md": _BRIEF_V1.replace("v0.4", "seed")})

    result_holder = {}

    def op():
        r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
        assert r.status_code == 200, r.text
        result_holder["body"] = r.json()

    _count_snapshots_after_op(client, op, expected_reason="create-project")
    body = result_holder["body"]
    # Second project gets ID "2" (seed was "1").
    assert body["project_id"] == "2"


def test_create_400_on_malformed_brief(client: TestClient):
    r = client.post("/api/projects", json={"brief_md": "no frontmatter"})
    assert r.status_code == 400


def test_create_409_when_writes_frozen(client: TestClient):
    client.app.state.writes_frozen = True
    try:
        r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    finally:
        client.app.state.writes_frozen = False
    assert r.status_code == 409


# ---------- GET /api/projects + /api/projects/{id} ---------------------------


def test_list_projects_after_create(client: TestClient):
    client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    r = client.get("/api/projects")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "btc explainer v0.4"


def test_get_project_returns_full_detail(client: TestClient):
    r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    pid = r.json()["project_id"]
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "btc explainer v0.4"
    assert body["script_lines"] == ["intro line", "body line"]
    assert body["sections"] == ["the hook"]
    assert body["tags"] == ["hook"]
    assert "Energy" in body["brief_md"]


def test_get_project_404_unknown(client: TestClient):
    r = client.get("/api/projects/9999")
    assert r.status_code == 404


# ---------- PATCH /api/projects/{id} -----------------------------------------


def test_update_happy_path_writes_one_snapshot(client: TestClient):
    # The first create primes the state file; the subsequent update is
    # the destructive op that snapshots.
    pid = client.post("/api/projects", json={"brief_md": _BRIEF_V1}).json()["project_id"]
    new_brief = _BRIEF_V1.replace("body line", "new body line")

    def op():
        r = client.patch(f"/api/projects/{pid}", json={"brief_md": new_brief})
        assert r.status_code == 200, r.text

    _count_snapshots_after_op(client, op, expected_reason="edit-brief")


def test_update_404_unknown(client: TestClient):
    r = client.patch("/api/projects/9999", json={"brief_md": _BRIEF_V1})
    assert r.status_code == 404


# ---------- DELETE /api/projects/{id} ----------------------------------------


def test_delete_happy_path_writes_one_snapshot(client: TestClient):
    # First create primes the state file; delete is the destructive op
    # that snapshots.
    pid = client.post("/api/projects", json={"brief_md": _BRIEF_V1}).json()["project_id"]

    result_holder = {}

    def op():
        r = client.delete(f"/api/projects/{pid}")
        assert r.status_code == 200, r.text
        result_holder["body"] = r.json()

    _count_snapshots_after_op(client, op, expected_reason="delete-project")
    body = result_holder["body"]
    assert body["dropped_tag_rows"] == 0
    assert body["deleted_attempts"] == 0


def test_delete_404_unknown(client: TestClient):
    r = client.delete("/api/projects/9999")
    assert r.status_code == 404


# ---------- Lock invariant (Phase 2.1 pattern) -------------------------------


def test_create_route_holds_save_lock_during_orchestrator(client: TestClient):
    observed: list[bool] = []

    def fake_create(state, parsed, *, brief_md_source=None):
        observed.append(client.app.state.save_lock.locked())
        return "1"

    with patch("clipfarm.routes.projects.create_project", side_effect=fake_create):
        client.post("/api/projects", json={"brief_md": _BRIEF_V1})

    assert observed == [True], (
        "create_project ran without `save_lock` held — the mutation seam is open"
    )


# ---------- Snapshot count == op count ---------------------------------------


def test_snapshot_count_equals_op_count(client: TestClient):
    """One of each mutating op → 3 new snapshots (after the state file is
    seeded). Phase 1 invariant: the FIRST op on a never-existed state
    file has nothing to snapshot — that's correct. Seed with a throwaway
    create to put a baseline file on disk; then measure."""
    client.post("/api/projects", json={"brief_md": _BRIEF_V1.replace("v0.4", "seed")})

    starting = len(list(client.snapshot_dir.glob("*.json")))
    r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    pid = r.json()["project_id"]
    client.patch(
        f"/api/projects/{pid}",
        json={"brief_md": _BRIEF_V1.replace("body line", "new body")},
    )
    client.delete(f"/api/projects/{pid}")
    after = len(list(client.snapshot_dir.glob("*.json")))
    assert after - starting == 3
