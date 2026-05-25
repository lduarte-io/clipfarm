"""Spec → "Conflict policy on external edit": when an external write to
`clipfarm.json` lands while the in-memory state is dirty, writes freeze. No
auto-resolve.

Phase 1 verifies the data-plumbing: `save_state(writes_frozen=True)` refuses
to write. The watcher + app wiring that flips the flag is integration-tested
manually via the curl verification in `PHASES.md`."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipfarm.models import ClipFarmState, Source
from clipfarm.store import WritesFrozenError, save_state


def _make_state() -> ClipFarmState:
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="ghost.mov",
        path="/nope/ghost.mov",
        added_at=datetime.now(timezone.utc).isoformat(),
    )
    return state


@pytest.mark.asyncio
async def test_writes_frozen_blocks_save(tmp_path: Path):
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()
    with pytest.raises(WritesFrozenError):
        await save_state(_make_state(), state_path, lock, writes_frozen=True)
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_unfrozen_save_after_resolution_writes_normally(tmp_path: Path):
    """Simulating the after-resolution path: writes_frozen flips back to
    False, save_state writes again."""
    state_path = tmp_path / "clipfarm.json"
    lock = asyncio.Lock()
    state = _make_state()

    # First call frozen → raise.
    with pytest.raises(WritesFrozenError):
        await save_state(state, state_path, lock, writes_frozen=True)

    # Caller flips the flag back (in real life, after the user resolves the
    # conflict modal in Phase 2).
    serialized = await save_state(state, state_path, lock, writes_frozen=False)
    assert state_path.exists()
    assert serialized
