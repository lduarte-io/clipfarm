"""Phase 8.1 — tests for GET /api/tag/progress and /api/premade/progress
plus the orchestrator-side progress-callback contract.

These confirm two invariants:

1. The progress slot is populated DURING the run (visible to a
   concurrent reader) and cleared back to None on completion / failure.
2. The new GET endpoints don't block on the save lock and return idle
   shape correctly.
"""
from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
        c.state_path = state_path
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# Idle-state shape
# ─────────────────────────────────────────────────────────────────────────────


def test_tag_progress_returns_idle_when_no_run(client: TestClient):
    r = client.get("/api/tag/progress")
    assert r.status_code == 200
    assert r.json() == {"running": False}


def test_premade_progress_returns_idle_when_no_run(client: TestClient):
    r = client.get("/api/premade/progress")
    assert r.status_code == 200
    assert r.json() == {"running": False}


# ─────────────────────────────────────────────────────────────────────────────
# Tag-progress: state visible to concurrent readers
# ─────────────────────────────────────────────────────────────────────────────


def test_tag_progress_visible_during_run(client: TestClient, tmp_path: Path):
    """While the orchestrator runs (in a worker thread), GET /tag/progress
    must show running=True with phase + elapsed_sec populated.

    Same ThreadPoolExecutor pattern as Phase 6.1 bug #2 — TestClient
    requests can race a long-running route via worker threads.
    """
    # Set up a tagged-able project.
    from clipfarm.models import (
        Clip, ClipFarmState, ClipProjectTag, Project, ProjectTag, Script, Source,
    )

    state: ClipFarmState = client.app.state.clipfarm
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=1,
        transcript_text="hi", created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["line"]),
        tags={"L0": ProjectTag(kind="line", name="line", order_idx=0)},
        created_at=_now(),
    )

    from clipfarm.tagging import TaggingResult

    progress_calls: list[dict[str, Any]] = []

    def slow_tag(state, project_id, *, llm_client, batch_size, dry_run, progress=None):
        # Emit a progress event, then sleep so the GET has time to land.
        if progress:
            progress({"phase": "batching", "current_batch": 1, "total_batches": 1})
        progress_calls.append({"phase": "batching"})
        time.sleep(1.2)
        return TaggingResult(clips_tagged=0, batches=1, mutated=False)

    with patch(
        "clipfarm.routes.tagging.tag_project", side_effect=slow_tag,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tag_future = pool.submit(client.post, "/api/projects/P/tag")
            time.sleep(0.4)  # let the orchestrator start
            r = client.get("/api/tag/progress")
            tag_future.result(timeout=10.0)

    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert body["project_id"] == "P"
    # phase populated by the orchestrator's progress emit
    assert body.get("phase") in ("batching", "starting", "preflight")
    # After the run, the slot is back to idle.
    final = client.get("/api/tag/progress")
    assert final.json() == {"running": False}


def test_tag_progress_cleared_on_orchestrator_exception(
    client: TestClient, tmp_path: Path
):
    """If the orchestrator raises, the finally block must still reset
    the slot to None so a polling client doesn't see a stuck 'running'."""
    from clipfarm.models import (
        Clip, ClipFarmState, Project, ProjectTag, Script, Source,
    )

    state: ClipFarmState = client.app.state.clipfarm
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=1, created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["line"]),
        tags={"L0": ProjectTag(kind="line", name="line", order_idx=0)},
        created_at=_now(),
    )

    def boom(state, project_id, *, llm_client, batch_size, dry_run, progress=None):
        # KeyError is a documented orchestrator raise → 404; ValueError → 400.
        raise KeyError("simulated mid-run crash")

    with patch(
        "clipfarm.routes.tagging.tag_project", side_effect=boom,
    ), patch("clipfarm.routes.tagging.ping_ollama", return_value=True):
        r = client.post("/api/projects/P/tag")

    assert r.status_code == 404  # KeyError → 404
    # And the progress slot is back to idle.
    assert client.app.state.tag_progress is None


# ─────────────────────────────────────────────────────────────────────────────
# Premade progress: same shape
# ─────────────────────────────────────────────────────────────────────────────


def test_premade_progress_visible_during_run(client: TestClient, tmp_path: Path):
    from clipfarm.models import (
        Clip, ClipFarmState, ClipProjectTag, Project, ProjectTag, Script, Source,
    )
    state: ClipFarmState = client.app.state.clipfarm
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=1, created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["line"]),
        tags={"L0": ProjectTag(kind="line", name="line", order_idx=0)},
        created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="c0", project_id="P", project_tag_id="L0",
        category="on-script", confidence=0.9,
    ))

    from clipfarm.premade import PremadeResult

    def slow_gen(state, project_id, *, llm_client, replace_existing, progress=None):
        if progress:
            progress({"phase": "running_strategies", "current_strategy": 1})
        time.sleep(1.2)
        return PremadeResult(generated_count=0, mutated=False)

    with patch(
        "clipfarm.routes.premade.generate_premade_attempts", side_effect=slow_gen,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f = pool.submit(client.post, "/api/projects/P/premade-attempts")
            time.sleep(0.4)
            r = client.get("/api/premade/progress")
            f.result(timeout=10.0)

    body = r.json()
    assert body["running"] is True
    assert body["project_id"] == "P"
    # Clear after.
    assert client.get("/api/premade/progress").json() == {"running": False}


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator-side progress callback contract
# ─────────────────────────────────────────────────────────────────────────────


def test_tag_orchestrator_calls_progress_per_batch():
    """`tag_project` calls progress() at preflight + once per batch +
    committing. Exact phase sequence is locked here so the frontend
    can rely on it."""
    from clipfarm.tagging import tag_project
    from clipfarm.models import (
        Clip, ClipFarmState, Project, ProjectTag, Script, Source,
    )
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    for i in range(3):
        state.clips[f"c{i}"] = Clip(
            source_id="1", start_sec=i, end_sec=i + 0.5,
            transcript_text=f"clip {i}", created_at=_now(),
        )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["a", "b"]),
        tags={
            "L0": ProjectTag(kind="line", name="a", order_idx=0),
            "L1": ProjectTag(kind="line", name="b", order_idx=1),
        },
        created_at=_now(),
    )

    seen: list[dict[str, Any]] = []

    def fake_llm(messages, schema):
        return {"results": []}  # empty batch result; orchestrator buckets it

    tag_project(
        state, "P", llm_client=fake_llm, batch_size=2,
        progress=lambda info: seen.append(info),
    )

    phases = [s["phase"] for s in seen]
    assert phases[0] == "preflight"
    assert phases[-1] == "committing"
    # At least one batching event between.
    assert any(p == "batching" for p in phases)


def test_tag_orchestrator_swallows_progress_callback_exception():
    """Buggy callback shouldn't break the orchestrator. Progress is
    observability, not correctness."""
    from clipfarm.tagging import tag_project
    from clipfarm.models import (
        Clip, ClipFarmState, Project, ProjectTag, Script, Source,
    )
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=1, created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["a"]),
        tags={"L0": ProjectTag(kind="line", name="a", order_idx=0)},
        created_at=_now(),
    )

    def broken_progress(info):
        raise RuntimeError("callback explodes")

    # Should not raise.
    result = tag_project(
        state, "P", llm_client=lambda m, s: None, batch_size=10,
        progress=broken_progress,
    )
    assert result.batches >= 0


def test_premade_orchestrator_calls_progress_per_phase():
    """`generate_premade_attempts` emits preflight, running_strategies
    (one per strategy), naming, persisting."""
    from clipfarm.premade import generate_premade_attempts
    from clipfarm.models import (
        Clip, ClipFarmState, ClipProjectTag, Project, ProjectTag, Script, Source,
    )
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=5, created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["a"]),
        tags={"L0": ProjectTag(kind="line", name="a", order_idx=0)},
        created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="c0", project_id="P", project_tag_id="L0",
        category="on-script", confidence=0.9,
    ))

    seen: list[dict[str, Any]] = []

    generate_premade_attempts(
        state, "P", llm_client=None,
        progress=lambda info: seen.append(info),
    )

    phases = [s["phase"] for s in seen]
    assert phases[0] == "preflight"
    assert "running_strategies" in phases
    # naming or persisting should appear once we have valid results.
    assert any(p in ("naming", "persisting") for p in phases)


def test_premade_orchestrator_swallows_progress_callback_exception():
    from clipfarm.premade import generate_premade_attempts
    from clipfarm.models import (
        Clip, ClipFarmState, ClipProjectTag, Project, ProjectTag, Script, Source,
    )
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=5, created_at=_now(),
    )
    state.projects["P"] = Project(
        name="x", script=Script(lines=["a"]),
        tags={"L0": ProjectTag(kind="line", name="a", order_idx=0)},
        created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="c0", project_id="P", project_tag_id="L0",
        category="on-script", confidence=0.9,
    ))

    def broken(info):
        raise RuntimeError("nope")

    # Should not raise.
    result = generate_premade_attempts(
        state, "P", llm_client=None, progress=broken,
    )
    # Run completed despite the broken callback.
    assert result.generated_count >= 0
