"""Tests for store.py — atomic save round-trip, snapshot helper, and
concurrent-save serialization under the asyncio.Lock."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipfarm.models import (
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Source,
)
from clipfarm.store import (
    SNAPSHOT_DIR,
    SNAPSHOT_LIMIT,
    WritesFrozenError,
    hash_serialized,
    list_snapshots,
    load_state,
    save_state,
    save_state_sync,
    save_state_with_snapshot,
    serialize_state,
    snapshot_before_destructive,
)


def _make_state(clip_count: int = 1) -> ClipFarmState:
    now = datetime.now(timezone.utc).isoformat()
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="fake.mov",
        path="/nonexistent/fake.mov",
        added_at=now,
        # The integrity check on load will flip this to True (path missing),
        # so the round-trip only matches if we mark it up front.
        unavailable=True,
    )
    for i in range(clip_count):
        cid = f"fake__00-00-{i:02d}__00-00-{i+1:02d}"
        state.clips[cid] = Clip(
            source_id="1",
            start_sec=float(i),
            end_sec=float(i + 1),
            transcript_text=f"line {i}",
            created_at=now,
        )
        state.clip_project_tags.append(
            ClipProjectTag(
                clip_id=cid,
                project_id="p1",
                project_tag_id=None,
                category="standalone-idea",
                source="user",
            )
        )
    return state


def test_save_sync_then_load_round_trip(tmp_path: Path):
    state = _make_state(clip_count=3)
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)
    loaded = load_state(state_path)
    assert loaded.model_dump() == state.model_dump()


def test_load_missing_file_returns_empty_state(tmp_path: Path):
    loaded = load_state(tmp_path / "does-not-exist.json")
    assert loaded.version == 1
    assert loaded.clips == {}
    assert loaded.sources == {}


def test_atomic_write_does_not_leave_tmp_file(tmp_path: Path):
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"unexpected tmp leftovers: {leftover}"


def test_snapshot_writes_and_keeps_pre_state(tmp_path: Path):
    state = _make_state(clip_count=2)
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)

    snap = snapshot_before_destructive(state_path, "split-clip")
    assert snap is not None
    assert snap.parent == tmp_path / SNAPSHOT_DIR
    assert snap.is_file()
    # The snapshot is the *pre-change* file — equal bytes.
    assert snap.read_bytes() == state_path.read_bytes()


def test_snapshot_with_no_state_file_is_noop(tmp_path: Path):
    state_path = tmp_path / "clipfarm.json"
    assert snapshot_before_destructive(state_path, "anything") is None


def test_snapshot_pruning_keeps_last_n(tmp_path: Path):
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)

    # Generate well over the limit; pruning trims to SNAPSHOT_LIMIT.
    # Mutate the file between snapshots so the 4-char hash varies — without
    # this, every snapshot would have the same hash + ms and collide.
    overshoot = SNAPSHOT_LIMIT + 5
    for i in range(overshoot):
        # Append a meaningless whitespace tweak to the state file so the
        # hash differs per snapshot. Doesn't change the round-trippable state.
        body = state_path.read_text(encoding="utf-8")
        state_path.write_text(body + (" " * (i + 1)), encoding="utf-8")
        snapshot_before_destructive(state_path, f"op-{i}")

    snaps = list_snapshots(state_path)
    assert len(snaps) == SNAPSHOT_LIMIT


def test_snapshot_reason_label_sanitized(tmp_path: Path):
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)
    snap = snapshot_before_destructive(state_path, "split clip / mid sentence!")
    assert snap is not None
    # No spaces or slashes in the filename.
    assert " " not in snap.name
    assert "/" not in snap.name


def test_snapshots_in_same_millisecond_get_distinct_filenames(tmp_path: Path):
    """The hash suffix exists so a tight loop of snapshots in one ms still
    produces distinct filenames. Verify by snapshotting two different file
    contents back-to-back."""
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text('{"version": 1, "marker": "a"}', encoding="utf-8")
    snap_a = snapshot_before_destructive(state_path, "op")
    state_path.write_text('{"version": 1, "marker": "b"}', encoding="utf-8")
    snap_b = snapshot_before_destructive(state_path, "op")
    assert snap_a is not None and snap_b is not None
    assert snap_a.name != snap_b.name


@pytest.mark.asyncio
async def test_concurrent_saves_serialize_through_lock(tmp_path: Path):
    """Two concurrent `await save_state(...)` calls both complete; the final
    file is valid JSON containing one of the two payloads (no half-write, no
    interleaved bytes)."""
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()

    state_a = _make_state(clip_count=1)
    state_b = _make_state(clip_count=2)

    results = await asyncio.gather(
        save_state(state_a, state_path, lock),
        save_state(state_b, state_path, lock),
    )

    assert all(isinstance(r, str) and r for r in results)
    # The final on-disk JSON must be parseable and one of the two payloads.
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    clip_count = len(on_disk["clips"])
    assert clip_count in (1, 2), f"corrupt or unexpected clip count: {clip_count}"


@pytest.mark.asyncio
async def test_save_state_raises_when_writes_frozen(tmp_path: Path):
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()

    with pytest.raises(WritesFrozenError):
        await save_state(state, state_path, lock, writes_frozen=True)
    assert not state_path.exists(), "frozen save must not have touched disk"


# --- Phase 1.1: post_write installs the hash inside the lock -----------------


@pytest.mark.asyncio
async def test_post_write_called_inside_lock_with_correct_hash(tmp_path: Path):
    """The watcher's hash install must run inside the save's critical section.
    Otherwise the polling observer can fire between the lock release and the
    hash install, see an 'external' change, and spuriously freeze writes.

    Verify with two asserts: (a) the callback got the right hash, and (b) the
    lock was held when the callback ran (`asyncio.Lock.locked()` returns
    True while held by any task).
    """
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()

    received_hashes: list[str] = []
    lock_state_during_callback: list[bool] = []

    def post_write(h: str) -> None:
        lock_state_during_callback.append(lock.locked())
        received_hashes.append(h)

    serialized = await save_state(state, state_path, lock, post_write=post_write)

    expected_hash = hash_serialized(serialized)
    assert received_hashes == [expected_hash]
    assert lock_state_during_callback == [True], (
        "post_write fired with the lock released — the race window is open"
    )


@pytest.mark.asyncio
async def test_post_write_not_called_when_frozen(tmp_path: Path):
    """If writes_frozen raises, post_write must never run — the watcher must
    not learn about a write that didn't happen."""
    state = _make_state()
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()

    received: list[str] = []
    with pytest.raises(WritesFrozenError):
        await save_state(
            state,
            state_path,
            lock,
            writes_frozen=True,
            post_write=received.append,
        )
    assert received == []


# --- Phase 1.1: save_state_with_snapshot couples both under one lock ---------


@pytest.mark.asyncio
async def test_save_with_snapshot_writes_old_state_to_snapshot_then_new_to_main(
    tmp_path: Path,
):
    """save_state_with_snapshot must snapshot the PRE-CHANGE on-disk state
    and then write the NEW state — both inside one critical section."""
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()

    # Establish a baseline on disk.
    old_state = _make_state(clip_count=1)
    save_state_sync(old_state, state_path)

    # Now apply a destructive change with snapshot.
    new_state = _make_state(clip_count=3)
    snap_path, serialized = await save_state_with_snapshot(
        new_state, state_path, lock, "test-destructive"
    )

    assert snap_path is not None and snap_path.is_file()
    # Snapshot has the OLD content; main file has the NEW content.
    loaded_from_snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    loaded_from_main = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(loaded_from_snapshot["clips"]) == 1, "snapshot must be pre-change"
    assert len(loaded_from_main["clips"]) == 3, "main must reflect the new write"
    # The reason label is in the snapshot filename.
    assert "test-destructive" in snap_path.name
    # Returned serialized form matches what was written.
    assert json.loads(serialized) == loaded_from_main


@pytest.mark.asyncio
async def test_save_with_snapshot_no_baseline_returns_none_snapshot(tmp_path: Path):
    """If the state file doesn't exist yet, the snapshot is a no-op (None
    returned) and the new state still lands."""
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()
    new_state = _make_state(clip_count=2)

    snap_path, _ = await save_state_with_snapshot(
        new_state, state_path, lock, "create"
    )
    assert snap_path is None
    assert state_path.is_file()


@pytest.mark.asyncio
async def test_save_with_snapshot_post_write_inside_lock(tmp_path: Path):
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()
    save_state_sync(_make_state(clip_count=1), state_path)

    lock_state: list[bool] = []
    received: list[str] = []

    def post_write(h: str) -> None:
        lock_state.append(lock.locked())
        received.append(h)

    new_state = _make_state(clip_count=2)
    _, serialized = await save_state_with_snapshot(
        new_state, state_path, lock, "split", post_write=post_write
    )

    assert lock_state == [True]
    assert received == [hash_serialized(serialized)]


@pytest.mark.asyncio
async def test_save_with_snapshot_raises_when_frozen(tmp_path: Path):
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()
    save_state_sync(_make_state(), state_path)
    pre_change_body = state_path.read_bytes()

    with pytest.raises(WritesFrozenError):
        await save_state_with_snapshot(
            _make_state(clip_count=99),
            state_path,
            lock,
            "split",
            writes_frozen=True,
        )

    # Neither the snapshot nor the main write should have happened.
    assert state_path.read_bytes() == pre_change_body
    snap_dir = state_path.parent / SNAPSHOT_DIR
    assert not snap_dir.exists() or not any(snap_dir.iterdir())
