"""Tests for the source-file integrity check on load."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from clipfarm.models import ClipFarmState, Source
from clipfarm.store import load_state, run_source_integrity_check, save_state_sync


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_missing_source_file_flips_unavailable(tmp_path: Path):
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="ghost.mov",
        path=str(tmp_path / "definitely-not-here.mov"),
        added_at=_now(),
        unavailable=False,
    )
    checked = run_source_integrity_check(state)
    assert checked.sources["1"].unavailable is True


def test_existing_source_file_flips_back_available(tmp_path: Path):
    real_mov = tmp_path / "real.mov"
    real_mov.write_bytes(b"\x00\x00")
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="real.mov",
        path=str(real_mov),
        added_at=_now(),
        unavailable=True,  # pretend a previous run flagged it missing
    )
    checked = run_source_integrity_check(state)
    assert checked.sources["1"].unavailable is False


def test_load_runs_integrity_check(tmp_path: Path):
    """End-to-end: save with a path that doesn't exist; load flags it."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="ghost.mov",
        path=str(tmp_path / "ghost.mov"),  # file not written
        added_at=_now(),
    )
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)
    loaded = load_state(state_path)
    assert loaded.sources["1"].unavailable is True
