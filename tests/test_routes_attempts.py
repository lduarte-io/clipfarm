"""Tests for `clipfarm/routes/attempts.py` — CRUD routes.

End-to-end through FastAPI TestClient, same fixture pattern as the
other route tests. Covers the validation rules locked in Phase 10a
plan-review: preserved tombstones, new-clip_id existence checks,
PATCH-to-empty, fork-of-deleted-parent, dirty=True invariant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(settings_path))
    from clipfarm.app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        c.state_path = state_path
        c.snapshot_dir = state_path.parent / ".clipfarm" / "snapshots"
        yield c


def _seed(client: TestClient) -> tuple[str, list[str]]:
    """Inject a state with one project + 3 clips. Returns
    (project_id, clip_ids)."""
    from clipfarm.models import (
        Clip, ClipFarmState, Project, ProjectTag, Script, Source,
    )
    state: ClipFarmState = client.app.state.clipfarm
    state.sources["s1"] = Source(
        filename="x.mov", path="/x.mov",
        duration_sec=100.0, added_at=_now(), unavailable=True,
    )
    for i, (s, e) in enumerate([(0, 10), (10, 20), (20, 30)]):
        state.clips[f"c{i}"] = Clip(
            source_id="s1", start_sec=float(s), end_sec=float(e),
            created_at=_now(),
        )
    state.projects["p1"] = Project(
        name="test", script=Script(lines=["a"]),
        tags={"L0": ProjectTag(kind="line", name="a", order_idx=0)},
        created_at=_now(),
    )
    return "p1", ["c0", "c1", "c2"]


def _count_snapshots(client: TestClient) -> int:
    if not client.snapshot_dir.exists():
        return 0
    return len(list(client.snapshot_dir.glob("*.json")))


# ─────────────────────────────────────────────────────────────────────────────
# Create hand-built attempt
# ─────────────────────────────────────────────────────────────────────────────


def test_create_empty_hand_built_attempt(client: TestClient):
    pid, _ = _seed(client)
    r = client.post(f"/api/projects/{pid}/attempts", json={"name": "draft"})
    assert r.status_code == 200
    body = r.json()
    assert body["attempt_id"] == "1"
    assert body["attempt"]["name"] == "draft"
    assert body["attempt"]["source"] == "hand-built"
    assert body["attempt"]["clips"] == []
    assert body["attempt"]["continuity_score"] is None


def test_create_hand_built_with_seed_clips(client: TestClient):
    pid, cids = _seed(client)
    r = client.post(
        f"/api/projects/{pid}/attempts",
        json={"name": "first", "clips": [
            {"clip_id": cids[0]}, {"clip_id": cids[1]},
        ]},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["attempt"]["clips"]) == 2
    # Continuity computed from the two seed clips.
    assert body["attempt"]["continuity_score"] is not None
    assert 0.0 <= body["attempt"]["continuity_score"] <= 1.0


def test_create_rejects_unknown_clip_id(client: TestClient):
    pid, _ = _seed(client)
    r = client.post(
        f"/api/projects/{pid}/attempts",
        json={"clips": [{"clip_id": "ghost"}]},
    )
    assert r.status_code == 400
    assert "unknown clip_id" in r.json()["detail"]


def test_create_404_unknown_project(client: TestClient):
    _seed(client)
    r = client.post("/api/projects/missing/attempts", json={})
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Fork
# ─────────────────────────────────────────────────────────────────────────────


def test_fork_clones_clip_list_with_parent_id(client: TestClient):
    pid, cids = _seed(client)
    orig_resp = client.post(
        f"/api/projects/{pid}/attempts",
        json={"name": "orig", "clips": [{"clip_id": cids[0]}]},
    )
    orig_id = orig_resp.json()["attempt_id"]
    r = client.post(f"/api/attempts/{orig_id}/fork")
    assert r.status_code == 200
    body = r.json()
    new_id = body["attempt_id"]
    assert new_id != orig_id
    assert body["attempt"]["source"] == "fork"
    assert body["attempt"]["parent_attempt_id"] == orig_id
    assert body["attempt"]["name"] == "fork of orig"
    assert len(body["attempt"]["clips"]) == 1
    assert body["attempt"]["clips"][0]["clip_id"] == cids[0]
    # Continuity recomputed (not blindly copied from original).
    assert body["attempt"]["continuity_score"] is not None


def test_fork_404_unknown_attempt(client: TestClient):
    _seed(client)
    r = client.post("/api/attempts/9999/fork")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Rename
# ─────────────────────────────────────────────────────────────────────────────


def test_rename_attempt(client: TestClient):
    pid, _ = _seed(client)
    aid = client.post(f"/api/projects/{pid}/attempts", json={"name": "old"}).json()["attempt_id"]
    r = client.patch(f"/api/attempts/{aid}", json={"name": "renamed v2"})
    assert r.status_code == 200
    assert r.json()["attempt"]["name"] == "renamed v2"


def test_rename_empty_name_rejected(client: TestClient):
    pid, _ = _seed(client)
    aid = client.post(f"/api/projects/{pid}/attempts", json={"name": "old"}).json()["attempt_id"]
    # min_length=1 → 422 from Pydantic.
    r = client.patch(f"/api/attempts/{aid}", json={"name": ""})
    assert r.status_code == 422


def test_rename_whitespace_only_rejected(client: TestClient):
    pid, _ = _seed(client)
    aid = client.post(f"/api/projects/{pid}/attempts", json={"name": "old"}).json()["attempt_id"]
    # Pydantic accepts "   " (length > 0) but `rename_attempt` strips
    # and rejects.
    r = client.patch(f"/api/attempts/{aid}", json={"name": "   "})
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# PATCH clips — the validation-heavy route
# ─────────────────────────────────────────────────────────────────────────────


def test_patch_clips_reorder(client: TestClient):
    pid, cids = _seed(client)
    aid = client.post(
        f"/api/projects/{pid}/attempts",
        json={"clips": [{"clip_id": c} for c in cids]},
    ).json()["attempt_id"]
    # Reverse the order.
    r = client.patch(f"/api/attempts/{aid}/clips", json={
        "clips": [{"clip_id": c} for c in reversed(cids)],
    })
    assert r.status_code == 200
    assert [ac["clip_id"] for ac in r.json()["attempt"]["clips"]] == list(reversed(cids))


def test_patch_clips_to_empty_allowed(client: TestClient):
    """PATCH-to-empty is always allowed per plan-review #1.
    Continuity drops to None."""
    pid, cids = _seed(client)
    aid = client.post(
        f"/api/projects/{pid}/attempts",
        json={"clips": [{"clip_id": cids[0]}]},
    ).json()["attempt_id"]
    r = client.patch(f"/api/attempts/{aid}/clips", json={"clips": []})
    assert r.status_code == 200
    assert r.json()["attempt"]["clips"] == []
    assert r.json()["attempt"]["continuity_score"] is None


def test_patch_clips_rejects_unknown_clip_id(client: TestClient):
    """Plan-review #2: a clip_id in the body that's NOT already in
    the attempt AND NOT in state.clips is data corruption → 400."""
    pid, _ = _seed(client)
    aid = client.post(f"/api/projects/{pid}/attempts", json={}).json()["attempt_id"]
    r = client.patch(f"/api/attempts/{aid}/clips", json={
        "clips": [{"clip_id": "definitely-not-a-real-clip"}],
    })
    assert r.status_code == 400
    assert "unknown clip_id" in r.json()["detail"]


def test_patch_clips_preserves_existing_tombstone(client: TestClient):
    """Plan-review #2: existing tombstones (in current attempt +
    missing from state.clips) pass through PATCH unchanged."""
    pid, cids = _seed(client)
    # Inject an attempt that ALREADY references a clip_id not in
    # state.clips (simulating the Phase 4 tombstone state).
    from clipfarm.models import Attempt, AttemptClip
    state = client.app.state.clipfarm
    state.attempts["100"] = Attempt(
        project_id=pid, name="has-tombstone", source="hand-built",
        clips=[
            AttemptClip(clip_id=cids[0]),
            AttemptClip(clip_id="c_deleted"),  # tombstone
            AttemptClip(clip_id=cids[1]),
        ],
        created_at=_now(),
    )
    # Reorder but KEEP the tombstone in the list — should be allowed.
    r = client.patch("/api/attempts/100/clips", json={
        "clips": [
            {"clip_id": cids[1]},
            {"clip_id": "c_deleted"},  # tombstone preserved
            {"clip_id": cids[0]},
        ],
    })
    assert r.status_code == 200
    new_clips = r.json()["attempt"]["clips"]
    assert [c["clip_id"] for c in new_clips] == [cids[1], "c_deleted", cids[0]]


def test_patch_clips_allows_dropping_tombstone(client: TestClient):
    """Plan-review #3: tombstones CAN be dropped via PATCH. Replace
    UI is Phase 10b; dropping a slot is a clip-list edit."""
    pid, cids = _seed(client)
    from clipfarm.models import Attempt, AttemptClip
    state = client.app.state.clipfarm
    state.attempts["100"] = Attempt(
        project_id=pid, name="x", source="hand-built",
        clips=[
            AttemptClip(clip_id=cids[0]),
            AttemptClip(clip_id="c_deleted"),
        ],
        created_at=_now(),
    )
    # Drop the tombstone.
    r = client.patch("/api/attempts/100/clips", json={
        "clips": [{"clip_id": cids[0]}],
    })
    assert r.status_code == 200
    assert [c["clip_id"] for c in r.json()["attempt"]["clips"]] == [cids[0]]


def test_patch_clips_404_unknown_attempt(client: TestClient):
    _seed(client)
    r = client.patch("/api/attempts/9999/clips", json={"clips": []})
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_attempt(client: TestClient):
    pid, _ = _seed(client)
    aid = client.post(f"/api/projects/{pid}/attempts", json={"name": "x"}).json()["attempt_id"]
    r = client.delete(f"/api/attempts/{aid}")
    assert r.status_code == 200
    assert r.json()["attempt_id"] == aid
    # Subsequent fetch is gone.
    assert aid not in client.get("/api/state").json()["attempts"]


def test_delete_fork_parent_leaves_dangling_parent_id(client: TestClient):
    """Plan-review #4: deleting a fork's parent doesn't cascade and
    doesn't null out parent_attempt_id. The fork keeps a dangling
    parent reference (UI renders "fork of [deleted attempt #N]")."""
    pid, cids = _seed(client)
    parent_id = client.post(
        f"/api/projects/{pid}/attempts",
        json={"name": "parent", "clips": [{"clip_id": cids[0]}]},
    ).json()["attempt_id"]
    fork_id = client.post(f"/api/attempts/{parent_id}/fork").json()["attempt_id"]
    # Delete the parent.
    r = client.delete(f"/api/attempts/{parent_id}")
    assert r.status_code == 200
    # Fork survives with dangling parent_attempt_id.
    state = client.get("/api/state").json()
    assert fork_id in state["attempts"]
    fork = state["attempts"][fork_id]
    assert fork["parent_attempt_id"] == parent_id  # still pointing at the now-gone parent


def test_delete_404_unknown_attempt(client: TestClient):
    _seed(client)
    r = client.delete("/api/attempts/9999")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 409 writes_frozen
# ─────────────────────────────────────────────────────────────────────────────


def test_create_409_when_writes_frozen(client: TestClient):
    pid, _ = _seed(client)
    client.app.state.writes_frozen = True
    try:
        r = client.post(f"/api/projects/{pid}/attempts", json={})
    finally:
        client.app.state.writes_frozen = False
    assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6.1 dirty=True invariant (plan-review #5)
# ─────────────────────────────────────────────────────────────────────────────


def test_dirty_flag_set_before_mutation_on_create(client: TestClient):
    """Even though create_hand_built_attempt is sync (no to_thread),
    dirty=True must flip BEFORE the mutation so the watcher-race
    window is closed."""
    pid, _ = _seed(client)
    observed: list[bool] = []
    from clipfarm.attempts_ops import create_hand_built_attempt as real_create

    def spy(state, project_id, **kw):
        observed.append(client.app.state.dirty)
        return real_create(state, project_id, **kw)

    with patch("clipfarm.routes.attempts.attempts_ops.create_hand_built_attempt", side_effect=spy):
        client.post(f"/api/projects/{pid}/attempts", json={})

    assert observed == [True]


def test_snapshot_per_mutation_after_state_exists_on_disk(client: TestClient):
    """Each successful mutation writes one snapshot — once the state
    file exists on disk. Phase 1 invariant: the FIRST mutation against
    a fresh-state-not-yet-on-disk writes the state file but produces
    no snapshot (can't snapshot what doesn't exist yet). Subsequent
    mutations snapshot normally."""
    pid, cids = _seed(client)
    # First mutation — writes state to disk for the first time; no snapshot.
    aid = client.post(
        f"/api/projects/{pid}/attempts",
        json={"clips": [{"clip_id": cids[0]}]},
    ).json()["attempt_id"]
    before = _count_snapshots(client)

    # Subsequent mutations should each snapshot once.
    client.patch(f"/api/attempts/{aid}/clips", json={"clips": []})
    after_patch = _count_snapshots(client)
    assert after_patch == before + 1

    client.delete(f"/api/attempts/{aid}")
    after_delete = _count_snapshots(client)
    assert after_delete == after_patch + 1
