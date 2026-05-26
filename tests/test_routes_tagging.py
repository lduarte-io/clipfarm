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


# ---------- Phase 6.1 bug carries -------------------------------------------


def test_dirty_flag_is_true_when_orchestrator_runs(
    client: TestClient, tmp_path: Path
):
    """Bug #1 precondition: `app.state.dirty` must be True *during* the
    orchestrator call, not after. If it were set after, the watcher's
    `has_unsaved_changes()` would read False during the LLM run, route
    an external clipfarm.json edit to silent-reload, abandon the local
    state pointer, and the end-of-run commit would overwrite the just-
    reloaded state — losing every tag we just produced.

    The precondition test here catches obvious regressions; the next
    test (`test_watcher_during_tag_run_flips_writes_frozen`) exercises
    the actual race the bug fix protects against.
    """
    pid, clip_ids = _seed(client, tmp_path)
    observed: list[bool] = []

    from clipfarm.tagging import TaggingResult

    def fake_tag(state, project_id, *, llm_client, batch_size, dry_run):
        observed.append(client.app.state.dirty)
        # Pretend we did some work so the route hits its commit branch.
        return TaggingResult(clips_tagged=0, batches=1, mutated=False)

    with patch(
        "clipfarm.routes.tagging.tag_project",
        side_effect=fake_tag,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        client.post(f"/api/projects/{pid}/tag")

    assert observed == [True], (
        "`app.state.dirty` was False when `tag_project` ran — the watcher "
        "would have routed an external edit to silent-reload, dropping "
        "every fresh tag at commit time. This is bug #1."
    )


def test_watcher_during_tag_run_flips_writes_frozen_and_returns_409(
    client: TestClient, tmp_path: Path
):
    """Bug #1 race coverage. Simulate the actual scenario the fix
    protects against:

    1. Tag run starts → `app.state.dirty = True` (the fix).
    2. Mid-batch, the watcher's `on_external_change` callback fires
       (someone hand-edited `clipfarm.json`).
    3. Because `dirty=True`, the watcher routes the event to
       `on_conflict`, NOT silent-reload → `writes_frozen` flips True.
    4. The orchestrator finishes; the locked commit raises
       `WritesFrozenError` → HTTP 409.

    Pre-fix, step 2 would have routed to silent-reload, the local state
    pointer would have been abandoned, and the commit would have
    overwritten the just-reloaded state — silently dropping every tag.
    """
    pid, clip_ids = _seed(client, tmp_path)

    from clipfarm.tagging import TaggingResult

    def fake_tag(state, project_id, *, llm_client, batch_size, dry_run):
        # Mid-run: invoke the watcher's conflict path the way the real
        # watcher would when it detects an external edit + dirty state.
        # `_callbacks` is internal but it's the only handle to the wired
        # `on_conflict` closure — the alternative (touching the file on
        # disk and waiting for the 500ms poll) is brittle in CI.
        client.app.state.watcher._callbacks.on_conflict(
            client.app.state.state_path
        )
        # We still 'wrote' a row in-memory; the commit will reject it.
        return TaggingResult(clips_tagged=1, batches=1, mutated=True)

    with patch(
        "clipfarm.routes.tagging.tag_project",
        side_effect=fake_tag,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post(f"/api/projects/{pid}/tag")

    try:
        assert r.status_code == 409, r.text
        assert client.app.state.writes_frozen is True
    finally:
        client.app.state.writes_frozen = False


def test_event_loop_responsive_during_long_tag_run(
    client: TestClient, tmp_path: Path
):
    """Bug #2: wrap the orchestrator in `asyncio.to_thread` so the
    event loop stays free during the long LLM run.

    NOTE — uses `concurrent.futures.ThreadPoolExecutor`, NOT
    `httpx.AsyncClient`. Phase 2's review captured that
    `ASGITransport` skips FastAPI lifespan, leaving
    `app.state.writes_frozen` undefined and breaking these tests; the
    implementer switched to `TestClient` after burning time on it.
    Same trap here.

    Strategy: fire `/tag` in a worker thread (orchestrator sleeps 2s
    simulating a real batch); from the main thread immediately fire
    `GET /api/state` and time it. Pre-fix the GET would block on the
    save_lock + blocking httpx for the full 2s; post-fix it returns
    promptly because the orchestrator runs off-loop.
    """
    import concurrent.futures
    import time

    pid, clip_ids = _seed(client, tmp_path)

    from clipfarm.tagging import TaggingResult

    def slow_tag(state, project_id, *, llm_client, batch_size, dry_run):
        time.sleep(2.0)  # simulate a real LLM batch
        return TaggingResult(clips_tagged=0, batches=1, mutated=False)

    with patch(
        "clipfarm.routes.tagging.tag_project",
        side_effect=slow_tag,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tag_future = pool.submit(
                client.post, f"/api/projects/{pid}/tag"
            )
            # Give the tag worker a moment to enter the lock + the
            # blocking `tag_project` call.
            time.sleep(0.3)
            t0 = time.perf_counter()
            r = client.get("/api/state")
            elapsed = time.perf_counter() - t0
            # Drain the tag run so the fixture teardown doesn't dangle.
            tag_future.result(timeout=10.0)

    assert r.status_code == 200
    assert elapsed < 1.0, (
        f"/api/state took {elapsed:.2f}s while a tag run was in flight — "
        f"the event loop is being blocked by the synchronous orchestrator. "
        f"Bug #2 (asyncio.to_thread wrap) regressed."
    )


def test_no_commit_when_orchestrator_did_not_mutate(
    client: TestClient, tmp_path: Path
):
    """Cosmetic carry #3: the commit-condition tightening. When the
    orchestrator runs but reports `mutated=False` (e.g. every batch
    failed validation AND there were no stale rows to drop), the route
    must skip the snapshot+commit — there's nothing to write."""
    pid, _ = _seed(client, tmp_path)
    before_snaps = _count_snapshots(client)

    from clipfarm.tagging import TaggingResult

    def no_op(state, project_id, *, llm_client, batch_size, dry_run):
        return TaggingResult(clips_tagged=0, batches=1, mutated=False)

    with patch(
        "clipfarm.routes.tagging.tag_project",
        side_effect=no_op,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post(f"/api/projects/{pid}/tag")

    assert r.status_code == 200
    # mutated=False → no snapshot.
    assert _count_snapshots(client) == before_snaps


def test_commit_when_orchestrator_mutated(
    client: TestClient, tmp_path: Path
):
    """The flip side: `mutated=True` must trigger the commit."""
    pid, clip_ids = _seed(client, tmp_path)
    before_snaps = _count_snapshots(client)

    with patch(
        "clipfarm.routes.tagging.chat_with_json_schema",
        return_value=_llm_response(clip_ids),
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post(f"/api/projects/{pid}/tag")

    assert r.status_code == 200
    assert r.json()["mutated"] is True
    assert _count_snapshots(client) == before_snaps + 1
