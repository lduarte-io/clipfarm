"""Tests for `POST /api/ingest` — uses FastAPI's `TestClient` so the
lifespan (and therefore `app.state.writes_frozen`, the watcher, etc.) runs
end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _write_pair(folder: Path, stem: str) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    sidecar = {
        "schema_version": 1,
        "duration": 30.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 0.5,
                "words": [{"start": 0.0, "end": 0.5, "word": " hi"}],
            }
        ],
    }
    (folder / f"{stem}.whisper.json").write_text(json.dumps(sidecar), encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Build a TestClient that uses an isolated state file. Re-imports the
    app so the lifespan picks up the env var (the module-level `app` only
    binds env at startup time)."""
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))

    # Re-import the app fresh so its lifespan sees the new env var. The
    # `clipfarm.app` module sets `state_path` at lifespan-startup time, not
    # at import time, so a fresh TestClient is enough.
    from clipfarm.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        c.state_path = state_path  # attach for tests that want to inspect the file
        yield c


def test_ingest_route_happy_path(client: TestClient, tmp_path: Path):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")
    _write_pair(folder, "beta")

    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 60.0, "duration_sec": 12.0},
    ):
        response = client.post("/api/ingest", json={"folder": str(folder)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["sources_added"]) == ["alpha.mov", "beta.mov"]
    assert body["clips_detected"] == 2
    # Persisted.
    on_disk = json.loads(client.state_path.read_text(encoding="utf-8"))
    assert len(on_disk["sources"]) == 2
    assert len(on_disk["clips"]) == 2


def test_relative_folder_rejected(client: TestClient):
    response = client.post("/api/ingest", json={"folder": "media"})
    assert response.status_code == 400
    assert "absolute" in response.json()["detail"]


def test_nonexistent_folder_rejected(client: TestClient, tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    response = client.post("/api/ingest", json={"folder": str(missing)})
    assert response.status_code == 400


def test_ingest_refused_when_writes_frozen(client: TestClient, tmp_path: Path):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")

    client.app.state.writes_frozen = True
    try:
        response = client.post("/api/ingest", json={"folder": str(folder)})
    finally:
        client.app.state.writes_frozen = False
    assert response.status_code == 409


def test_re_ingest_through_route_is_idempotent(
    client: TestClient, tmp_path: Path
):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")

    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 60.0, "duration_sec": 12.0},
    ):
        first = client.post("/api/ingest", json={"folder": str(folder)})
        second = client.post("/api/ingest", json={"folder": str(folder)})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["sources_added"] == ["alpha.mov"]
    assert second.json()["sources_added"] == []
    assert second.json()["sources_skipped"] == ["alpha.mov"]


# --- Phase 2.1: mutation-under-lock seam --------------------------------------


def test_ingest_holds_save_lock_during_orchestrator_call(
    client: TestClient, tmp_path: Path
):
    """Structural assertion: the route must hold `app.state.save_lock` when
    `ingest_folder` runs. Without the lock the source-ID allocator
    (`max(existing) + 1`) is racy under any future async mutation. Mock the
    orchestrator and inspect the lock state at the moment it's called."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")

    from clipfarm.ingest import IngestResult

    observed_locked: list[bool] = []

    def fake_ingest(state, folder_arg):
        observed_locked.append(client.app.state.save_lock.locked())
        return IngestResult()  # no mutations; route should still hold lock

    with patch("clipfarm.routes.ingest.ingest_folder", side_effect=fake_ingest):
        response = client.post("/api/ingest", json={"folder": str(folder)})

    assert response.status_code == 200
    assert observed_locked == [True], (
        "ingest_folder ran without `save_lock` held — the mutation seam is open"
    )


def test_concurrent_ingest_produces_consistent_state(
    client: TestClient, tmp_path: Path
):
    """Fire multiple concurrent `/api/ingest` calls against the same folder;
    the final state must have each source exactly once with a unique ID,
    regardless of ordering. Today's sync `ingest_folder` can't actually race
    under asyncio, but this is the regression guard for the day it goes
    async (e.g. async ffprobe) — without the lock, two handlers could both
    compute `_next_source_id == "1"` and one would silently lose."""
    import concurrent.futures

    folder = tmp_path / "media"
    folder.mkdir()
    for stem in ("alpha", "beta", "gamma"):
        _write_pair(folder, stem)

    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 60.0, "duration_sec": 12.0},
    ):
        # Three concurrent ingests of the same folder. First call adds
        # every source; the others should land in `sources_skipped`.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            results = list(
                ex.map(
                    lambda _: client.post("/api/ingest", json={"folder": str(folder)}),
                    range(3),
                )
            )

    for r in results:
        assert r.status_code == 200, r.text

    # State invariants: each filename appears exactly once across sources;
    # every source ID is unique; clip count matches what one ingest would
    # have produced (concurrent runs must not double-segment).
    state_response = client.get("/api/state")
    assert state_response.status_code == 200
    state = state_response.json()
    filenames = [s["filename"] for s in state["sources"].values()]
    assert sorted(filenames) == ["alpha.mov", "beta.mov", "gamma.mov"]
    assert len(set(state["sources"].keys())) == 3, "duplicate source IDs"
    # 3 sources × 1 clip each (default sidecar fixture has 1 word) = 3 clips.
    assert len(state["clips"]) == 3
