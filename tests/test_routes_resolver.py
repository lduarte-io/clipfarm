"""Tests for GET /api/attempts/{id}/resolved."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))
    from clipfarm.app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


def _seed_attempt(client: TestClient) -> str:
    """Inject a state with one attempt directly into app state."""
    from clipfarm.models import (
        Attempt, AttemptClip, Clip, ClipFarmState, Source,
    )
    state: ClipFarmState = client.app.state.clipfarm
    state.sources["s1"] = Source(
        filename="alpha.mov", path="/alpha.mov",
        duration_sec=100.0, added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="s1", start_sec=5.0, end_sec=15.0, created_at=_now()
    )
    state.attempts["a1"] = Attempt(
        project_id="p1", name="test", source="hand-built",
        clips=[AttemptClip(clip_id="c0")],
        created_at=_now(),
    )
    return "a1"


def test_resolved_happy_path_response_shape(client: TestClient):
    aid = _seed_attempt(client)
    r = client.get(f"/api/attempts/{aid}/resolved")
    assert r.status_code == 200
    body = r.json()
    assert body["attempt_id"] == aid
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["type"] == "range"
    assert item["clip_id"] == "c0"
    assert item["source_id"] == "s1"
    # Filename + URL both derived server-side.
    assert item["source_filename"] == "alpha.mov"
    assert item["source_url"] == "/api/sources/s1/video"
    assert item["effective_start_sec"] == 5.0
    assert item["effective_end_sec"] == 15.0


def test_resolved_404_unknown_attempt(client: TestClient):
    r = client.get("/api/attempts/nope/resolved")
    assert r.status_code == 404
    assert "unknown attempt_id" in r.json()["detail"]


def test_resolved_tombstone_in_response(client: TestClient):
    """Dangling AttemptClip → tombstone item in the wire response."""
    from clipfarm.models import (
        Attempt, AttemptClip, Clip, ClipFarmState, Source,
    )
    state: ClipFarmState = client.app.state.clipfarm
    state.sources["s1"] = Source(
        filename="alpha.mov", path="/alpha.mov",
        duration_sec=100.0, added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="s1", start_sec=0, end_sec=5, created_at=_now()
    )
    state.attempts["a1"] = Attempt(
        project_id="p1", name="test", source="hand-built",
        clips=[
            AttemptClip(clip_id="c0"),
            AttemptClip(clip_id="c_deleted"),
            AttemptClip(clip_id="c0"),
        ],
        created_at=_now(),
    )
    r = client.get("/api/attempts/a1/resolved")
    body = r.json()
    assert [i["type"] for i in body["items"]] == ["range", "tombstone", "range"]
    assert body["items"][1]["clip_id"] == "c_deleted"
    assert "reason" in body["items"][1]


def test_resolved_route_is_read_only(client: TestClient):
    """No snapshot side effect from a resolve. Same invariant as
    Phase 7's take-grid route."""
    aid = _seed_attempt(client)
    snap_dir = Path(client.app.state.state_path).parent / ".clipfarm" / "snapshots"
    before = len(list(snap_dir.glob("*.json"))) if snap_dir.exists() else 0
    for _ in range(3):
        r = client.get(f"/api/attempts/{aid}/resolved")
        assert r.status_code == 200
    after = len(list(snap_dir.glob("*.json"))) if snap_dir.exists() else 0
    assert after == before
