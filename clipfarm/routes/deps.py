"""Dependency providers + commit helpers shared by every route module.

Why this lives here instead of in `app.py`:
- `app.py` imports route modules at the bottom (via `include_router`); having
  routes reach back into `app.py` for `get_state` / `commit_state_to_disk`
  creates a real import cycle. `deps.py` is the safe seam.
- It also makes "what does a route get to call?" explicit. New routes import
  from here, not from `app.py`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request

from clipfarm.models import ClipFarmState
from clipfarm.store import (
    save_state,
    save_state_locked,
    save_state_with_snapshot,
    save_state_with_snapshot_locked,
)
from clipfarm.watcher import StateFileWatcher


def get_state(request: Request) -> ClipFarmState:
    """`Depends(get_state)` in any route handler reads the in-memory
    `ClipFarmState` off `app.state.clipfarm`. Holding the single-
    `load_state()`-entry-point invariant: routes never open
    `clipfarm.json` directly after startup."""
    return request.app.state.clipfarm  # type: ignore[no-any-return]


async def commit_state_to_disk(app: FastAPI) -> None:
    """Save the current `app.state.clipfarm` under the save lock. The
    watcher's `last_known_hash` is installed inside the same critical
    section via `post_write`, closing the race where a poll between
    lock-release and hash-install would see an "external" change and
    spuriously flip `writes_frozen`.

    Raises `WritesFrozenError` if `app.state.writes_frozen` is set.
    """
    watcher: StateFileWatcher = app.state.watcher
    await save_state(
        app.state.clipfarm,
        app.state.state_path,
        app.state.save_lock,
        writes_frozen=app.state.writes_frozen,
        post_write=watcher.update_last_known_hash,
    )
    app.state.dirty = False


async def commit_state_with_snapshot(app: FastAPI, reason: str) -> Path | None:
    """Locked snapshot-then-save, used by destructive routes (split, merge,
    delete, etc. — first user lands in Phase 4). Same race-closure as
    `commit_state_to_disk`: the snapshot, write, and hash install all happen
    inside the same `asyncio.Lock` critical section.

    Returns the snapshot path (or None if no on-disk state existed yet).
    Raises `WritesFrozenError` if `app.state.writes_frozen` is set.
    """
    watcher: StateFileWatcher = app.state.watcher
    snap_path, _ = await save_state_with_snapshot(
        app.state.clipfarm,
        app.state.state_path,
        app.state.save_lock,
        reason,
        writes_frozen=app.state.writes_frozen,
        post_write=watcher.update_last_known_hash,
    )
    app.state.dirty = False
    return snap_path


def commit_state_to_disk_locked(app: FastAPI) -> None:
    """Caller-already-holds-lock variant of `commit_state_to_disk`. Used
    by routes that do `async with save_lock: { mutate; await commit_locked }`
    — mutation + commit in ONE critical section, not two. The Phase 6
    architectural fix carried from the Phase 4 review: no other route
    can interleave between our mutate and our write.

    Callers MUST hold `app.state.save_lock`.
    """
    watcher: StateFileWatcher = app.state.watcher
    save_state_locked(
        app.state.clipfarm,
        app.state.state_path,
        writes_frozen=app.state.writes_frozen,
        post_write=watcher.update_last_known_hash,
    )
    app.state.dirty = False


def commit_state_with_snapshot_locked(app: FastAPI, reason: str) -> Path | None:
    """Caller-already-holds-lock variant of `commit_state_with_snapshot`.
    Snapshot + write + hash-install inside the caller's existing critical
    section.

    Callers MUST hold `app.state.save_lock`.
    """
    watcher: StateFileWatcher = app.state.watcher
    snap_path, _ = save_state_with_snapshot_locked(
        app.state.clipfarm,
        app.state.state_path,
        reason,
        writes_frozen=app.state.writes_frozen,
        post_write=watcher.update_last_known_hash,
    )
    app.state.dirty = False
    return snap_path


__all__ = [
    "commit_state_to_disk",
    "commit_state_to_disk_locked",
    "commit_state_with_snapshot",
    "commit_state_with_snapshot_locked",
    "get_state",
]
