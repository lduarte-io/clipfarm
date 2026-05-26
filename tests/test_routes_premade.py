"""Tests for POST /api/projects/{id}/premade-attempts.

Uses `TestClient` for FastAPI lifespan support (per Phase 2's
ASGITransport finding). The Phase 6.1 race-coverage test uses
ThreadPoolExecutor to verify event-loop responsiveness.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_BRIEF = """---
name: premade test project
script:
  - intro line
  - body line
---

energy
"""


def _write_pair(folder: Path, stem: str, words: list[tuple[float, float, str]]) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    payload = {
        "schema_version": 1,
        "duration": 30.0,
        "segments": [{
            "id": 0,
            "start": words[0][0],
            "end": words[-1][1],
            "words": [{"start": s, "end": e, "word": w} for (s, e, w) in words],
        }],
    }
    (folder / f"{stem}.whisper.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))

    from clipfarm.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        c.state_path = state_path
        c.snapshot_dir = state_path.parent / ".clipfarm" / "snapshots"
        yield c


def _seed_tagged(client: TestClient, tmp_path: Path) -> tuple[str, list[str]]:
    """Ingest, create project, manually inject on-script tags so the
    orchestrator has something to chew on. Returns
    `(project_id, clip_ids)`."""
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
    r = client.post("/api/projects", json={"brief_md": _BRIEF})
    pid = r.json()["project_id"]

    state = client.get("/api/state").json()
    clip_ids = sorted(state["clips"].keys())
    # Find the line tag ids (first two line tags in the project).
    line_tags = [
        tid for tid, t in state["projects"][pid]["tags"].items()
        if t["kind"] == "line"
    ]
    # Manually inject on-script rows (skip the LLM tagging step).
    from clipfarm.models import ClipProjectTag
    client.app.state.clipfarm.clip_project_tags.extend([
        ClipProjectTag(
            clip_id=clip_ids[0], project_id=pid,
            project_tag_id=line_tags[0], category="on-script",
            confidence=0.9,
        ),
        ClipProjectTag(
            clip_id=clip_ids[1], project_id=pid,
            project_tag_id=line_tags[1], category="on-script",
            confidence=0.8,
        ),
    ])
    return pid, clip_ids


def _count_snapshots(client: TestClient) -> int:
    if not client.snapshot_dir.exists():
        return 0
    return len(list(client.snapshot_dir.glob("*.json")))


# ---------- Happy path ------------------------------------------------------


def test_premade_happy_path_with_canned_naming(
    client: TestClient, tmp_path: Path
):
    pid, _ = _seed_tagged(client, tmp_path)
    # Patch the LLM client to return None (forces canned fallback).
    with patch(
        "clipfarm.routes.premade.chat_with_json_schema",
        return_value=None,
    ):
        r = client.post(f"/api/projects/{pid}/premade-attempts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated_count"] > 0
    assert body["naming_source"] == "canned"
    assert len(body["new_attempt_ids"]) == body["generated_count"]
    # Returned attempts subset matches the new IDs.
    assert set(body["attempts"].keys()) == set(body["new_attempt_ids"])


def test_premade_happy_path_with_llm_naming(
    client: TestClient, tmp_path: Path
):
    pid, _ = _seed_tagged(client, tmp_path)
    def fake_chat(messages, schema, model=None):
        n = schema["properties"]["names"]["maxItems"]
        return {"names": [f"AI name {i}" for i in range(n)]}
    with patch(
        "clipfarm.routes.premade.chat_with_json_schema",
        side_effect=fake_chat,
    ):
        r = client.post(f"/api/projects/{pid}/premade-attempts")
    assert r.status_code == 200
    body = r.json()
    assert body["naming_source"] == "llm"
    first_id = body["new_attempt_ids"][0]
    assert body["attempts"][first_id]["name"].startswith("AI name")


# ---------- Error paths ----------------------------------------------------


def test_premade_404_unknown_project(client: TestClient, tmp_path: Path):
    _seed_tagged(client, tmp_path)
    r = client.post("/api/projects/9999/premade-attempts")
    assert r.status_code == 404


def test_premade_400_when_no_on_script_tags(
    client: TestClient, tmp_path: Path
):
    """Project exists but has zero on-script rows."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha", [(1.0, 1.5, " hi")])
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})
    pid = client.post("/api/projects", json={"brief_md": _BRIEF}).json()["project_id"]
    # No tagging step → no on-script rows.
    r = client.post(f"/api/projects/{pid}/premade-attempts")
    assert r.status_code == 400
    assert "on-script" in r.json()["detail"]


def test_premade_409_when_writes_frozen(client: TestClient, tmp_path: Path):
    pid, _ = _seed_tagged(client, tmp_path)
    client.app.state.writes_frozen = True
    try:
        r = client.post(f"/api/projects/{pid}/premade-attempts")
    finally:
        client.app.state.writes_frozen = False
    assert r.status_code == 409


# ---------- Phase 6.1 invariants ------------------------------------------


def test_dirty_flag_is_true_when_orchestrator_runs(
    client: TestClient, tmp_path: Path
):
    """Bug #1 invariant carried forward: dirty=True BEFORE the
    orchestrator runs. Locked the race in Phase 6.1 + 7; locked again
    here for the new route."""
    pid, _ = _seed_tagged(client, tmp_path)
    observed: list[bool] = []

    from clipfarm.premade import PremadeResult

    def fake_gen(state, project_id, *, llm_client, replace_existing, progress=None):
        observed.append(client.app.state.dirty)
        return PremadeResult(generated_count=0, mutated=False)

    with patch(
        "clipfarm.routes.premade.generate_premade_attempts",
        side_effect=fake_gen,
    ):
        client.post(f"/api/projects/{pid}/premade-attempts")

    assert observed == [True]


def test_no_commit_when_orchestrator_did_not_mutate(
    client: TestClient, tmp_path: Path
):
    """Bug #1 cosmetic #3 invariant: result.mutated=False → no snapshot."""
    pid, _ = _seed_tagged(client, tmp_path)
    before = _count_snapshots(client)

    from clipfarm.premade import PremadeResult

    def no_op(state, project_id, *, llm_client, replace_existing, progress=None):
        return PremadeResult(
            generated_count=0, mutated=False,
            reason="every strategy returned empty",
        )

    with patch(
        "clipfarm.routes.premade.generate_premade_attempts",
        side_effect=no_op,
    ):
        r = client.post(f"/api/projects/{pid}/premade-attempts")

    assert r.status_code == 200
    assert r.json()["generated_count"] == 0
    assert r.json()["reason"]
    assert _count_snapshots(client) == before


def test_commit_when_orchestrator_mutated(client: TestClient, tmp_path: Path):
    """Mutated=True → snapshot once."""
    pid, _ = _seed_tagged(client, tmp_path)
    before = _count_snapshots(client)
    with patch(
        "clipfarm.routes.premade.chat_with_json_schema",
        return_value=None,
    ):
        r = client.post(f"/api/projects/{pid}/premade-attempts")
    assert r.status_code == 200
    assert _count_snapshots(client) == before + 1


def test_event_loop_responsive_during_long_premade_run(
    client: TestClient, tmp_path: Path
):
    """Bug #2: orchestrator wrapped in asyncio.to_thread keeps event
    loop free. Uses ThreadPoolExecutor (avoiding the AsyncClient
    lifespan trap from Phase 2's review)."""
    import concurrent.futures
    import time

    pid, _ = _seed_tagged(client, tmp_path)

    from clipfarm.premade import PremadeResult

    def slow_gen(state, project_id, *, llm_client, replace_existing, progress=None):
        time.sleep(1.5)
        return PremadeResult(generated_count=0, mutated=False)

    with patch(
        "clipfarm.routes.premade.generate_premade_attempts",
        side_effect=slow_gen,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            gen_future = pool.submit(
                client.post, f"/api/projects/{pid}/premade-attempts"
            )
            time.sleep(0.2)
            t0 = time.perf_counter()
            r = client.get("/api/state")
            elapsed = time.perf_counter() - t0
            gen_future.result(timeout=10.0)

    assert r.status_code == 200
    assert elapsed < 1.0, (
        f"/api/state took {elapsed:.2f}s while a premade run was in flight — "
        f"event loop blocked. Phase 6.1 bug #2 regression."
    )
