"""Tests for `clipfarm/routes/tagging.py` — POST /api/projects/{id}/tag.

Uses `TestClient` so the lifespan runs (`app.state.save_lock` etc.).
Mocks the LLM client + ping by patching at the route module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_BRIEF_V1 = """---
name: tag test project
script:
  - intro line
  - body line
sections:
  - the hook
tags:
  - hook
---

what's good: energy
"""


def _write_pair(folder: Path, stem: str, words: list[tuple[float, float, str]]) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    payload = {
        "schema_version": 1,
        "duration": 30.0,
        "segments": [
            {
                "id": 0,
                "start": words[0][0],
                "end": words[-1][1],
                "words": [{"start": s, "end": e, "word": w} for (s, e, w) in words],
            }
        ],
    }
    (folder / f"{stem}.whisper.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))

    from clipfarm.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        c.state_path = state_path
        c.snapshot_dir = state_path.parent / ".clipfarm" / "snapshots"
        yield c


def _seed(client: TestClient, tmp_path: Path) -> tuple[str, list[str]]:
    """Ingest one source with two paired clips, then create a project.
    Returns `(project_id, clip_ids)`."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha", [
        (1.0, 1.5, " intro"),
        (5.0, 5.5, " body"),
    ])
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})

    r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    pid = r.json()["project_id"]
    state = client.get("/api/state").json()
    clip_ids = list(state["clips"].keys())
    return pid, clip_ids


def _llm_response(clip_ids: list[str]) -> dict:
    """Build a canned LLM response with one row per clip."""
    return {
        "results": [
            {
                "clip_id": cid,
                "line_tag_id": None,
                "section_tag_id": None,
                "category": "standalone-idea",
                "confidence": 0.7,
            }
            for cid in clip_ids
        ]
    }


def _count_snapshots(client: TestClient) -> int:
    if not client.snapshot_dir.exists():
        return 0
    return len(list(client.snapshot_dir.glob("*.json")))


# ---------- Happy path ------------------------------------------------------


def test_tag_route_happy_path(client: TestClient, tmp_path: Path):
    pid, clip_ids = _seed(client, tmp_path)
    canned = _llm_response(clip_ids)

    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
        return_value=canned,
    ), patch(
        "clipfarm.routes.tagging.ping_ollama",
        return_value=True,
    ):
        r = client.post(f"/api/projects/{pid}/tag")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clips_tagged"] == 2
    assert body["untagged_batches"] == []
    # State now has tag rows.
    state = client.get("/api/state").json()
    assert len(state["clip_project_tags"]) == 2


def test_tag_route_writes_one_snapshot(client: TestClient, tmp_path: Path):
    pid, clip_ids = _seed(client, tmp_path)
    before = _count_snapshots(client)

    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
        return_value=_llm_response(clip_ids),
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        client.post(f"/api/projects/{pid}/tag")

    after = _count_snapshots(client)
    assert after - before == 1
    new_snap = max(client.snapshot_dir.glob("*.json"), key=lambda p: p.name)
    assert "tag-clips" in new_snap.name


# ---------- Failure paths ----------------------------------------------------


def test_tag_route_404_unknown_project(client: TestClient, tmp_path: Path):
    _seed(client, tmp_path)
    r = client.post("/api/projects/9999/tag")
    assert r.status_code == 404


def test_tag_route_400_empty_brief(client: TestClient, tmp_path: Path):
    """A project with name but no script / sections / tags → 400 before
    any LLM call."""
    # Seed with a clip but an empty-ish project.
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha", [(1.0, 1.5, " hi")])
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})

    empty_brief = "---\nname: empty\n---\n"
    pid = client.post("/api/projects", json={"brief_md": empty_brief}).json()["project_id"]

    with patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post(f"/api/projects/{pid}/tag")
    assert r.status_code == 400
    assert "no script lines" in r.json()["detail"]


def test_tag_route_409_when_writes_frozen(client: TestClient, tmp_path: Path):
    pid, _ = _seed(client, tmp_path)
    client.app.state.writes_frozen = True
    try:
        r = client.post(f"/api/projects/{pid}/tag")
    finally:
        client.app.state.writes_frozen = False
    assert r.status_code == 409


def test_tag_route_502_when_ollama_unreachable(client: TestClient, tmp_path: Path):
    pid, _ = _seed(client, tmp_path)
    with patch("clipfarm.routes.tagging.ping_ollama", return_value=False):
        r = client.post(f"/api/projects/{pid}/tag")
    assert r.status_code == 502


# ---------- Lock + commit assertions ----------------------------------------


def test_tag_route_holds_lock_during_orchestrator(
    client: TestClient, tmp_path: Path
):
    """Phase 2.1 pattern — and Phase 6's "single critical section per
    op": lock must be held when `tag_project` runs."""
    pid, clip_ids = _seed(client, tmp_path)
    observed: list[bool] = []

    from clipfarm.tagging import TaggingResult

    def fake_tag(state, project_id, *, llm_client, batch_size, dry_run):
        observed.append(client.app.state.save_lock.locked())
        return TaggingResult()

    with patch(
        "clipfarm.routes.tagging.tag_project",
        side_effect=fake_tag,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        client.post(f"/api/projects/{pid}/tag")

    assert observed == [True]


def test_tag_route_commit_inside_same_lock_scope(
    client: TestClient, tmp_path: Path
):
    """Phase 6 invariant: mutation + commit happen in ONE critical
    section. Patching the locked commit helper, the lock must still be
    held when it's invoked."""
    pid, clip_ids = _seed(client, tmp_path)
    observed_lock_state: list[bool] = []

    real_commit = None

    def spy_commit(app, reason):
        observed_lock_state.append(app.state.save_lock.locked())
        # Defer to the real impl so the snapshot actually lands.
        return real_commit(app, reason)

    from clipfarm.routes import tagging as tagging_module
    real_commit = tagging_module.commit_state_with_snapshot_locked

    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
        return_value=_llm_response(clip_ids),
    ), patch(
        "clipfarm.routes.tagging.ping_ollama", return_value=True
    ), patch(
        "clipfarm.routes.tagging.commit_state_with_snapshot_locked",
        side_effect=spy_commit,
    ):
        r = client.post(f"/api/projects/{pid}/tag")

    assert r.status_code == 200
    assert observed_lock_state == [True], (
        "commit ran without `save_lock` held — mutation + commit are NOT in "
        "one critical section"
    )


# ---------- Idempotency + dry_run ------------------------------------------


def test_tag_route_idempotent_second_call_returns_zero(
    client: TestClient, tmp_path: Path
):
    pid, clip_ids = _seed(client, tmp_path)
    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
        return_value=_llm_response(clip_ids),
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        first = client.post(f"/api/projects/{pid}/tag")
        second = client.post(f"/api/projects/{pid}/tag")
    assert first.json()["clips_tagged"] == 2
    assert second.json()["clips_tagged"] == 0
    assert second.json()["clips_skipped"] == 2


def test_tag_route_dry_run_writes_nothing(
    client: TestClient, tmp_path: Path
):
    pid, clip_ids = _seed(client, tmp_path)
    before_snaps = _count_snapshots(client)
    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post(f"/api/projects/{pid}/tag?dry_run=true")
    assert r.status_code == 200
    body = r.json()
    # 1 batch composed (2 clips, batch_size=10 → 1 batch), 0 tagged.
    assert body["batches"] == 1
    assert body["clips_tagged"] == 0
    # No state mutation.
    state = client.get("/api/state").json()
    assert state["clip_project_tags"] == []
    # No snapshot.
    assert _count_snapshots(client) == before_snaps
