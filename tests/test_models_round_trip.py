"""Round-trip tests for the new optional fields landed in Phase 1.

Locks the spec invariant: v0 writers leave `tracks` as `null`, and the
on-disk JSON for every new optional field serializes as `null` by default —
never as an empty dict, never missing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipfarm.models import (
    Attempt,
    AttemptClip,
    Clip,
    ClipFarmState,
)
from clipfarm.store import load_state, save_state_sync


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_clip_tracks_defaults_to_none():
    c = Clip(source_id="1", start_sec=0.0, end_sec=1.0, created_at=_now())
    assert c.tracks is None
    # And it serializes as JSON null (not {} or missing).
    dumped = c.model_dump(mode="json")
    assert "tracks" in dumped
    assert dumped["tracks"] is None


def test_attempt_continuity_and_premade_bucket_default_to_none():
    a = Attempt(project_id="p1", name="x", created_at=_now())
    assert a.continuity_score is None
    assert a.premade_bucket is None
    assert a.needs_review is False
    dumped = a.model_dump(mode="json")
    assert dumped["continuity_score"] is None
    assert dumped["premade_bucket"] is None
    assert dumped["needs_review"] is False


def test_attempt_clip_internal_pause_default_none():
    ac = AttemptClip(clip_id="cid")
    assert ac.internal_pause_max_sec is None
    dumped = ac.model_dump(mode="json")
    assert dumped["internal_pause_max_sec"] is None


@pytest.mark.parametrize(
    "premade_bucket,continuity_score,internal_pause",
    [
        (None, None, None),
        ("best", 0.92, None),
        ("diagnostic", 0.3, 0.5),
    ],
)
def test_attempt_field_round_trip_through_disk(
    tmp_path: Path,
    premade_bucket,
    continuity_score,
    internal_pause,
):
    """Values for the new fields round-trip through `clipfarm.json` exactly —
    no silent defaulting, no coercion. The integrity-check side-effect is
    avoided by not adding any Sources to this state."""
    state = ClipFarmState()
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="t",
        premade_bucket=premade_bucket,
        continuity_score=continuity_score,
        clips=[AttemptClip(clip_id="cid", internal_pause_max_sec=internal_pause)],
        created_at=_now(),
    )
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)

    loaded = load_state(state_path)
    a = loaded.attempts["1"]
    assert a.premade_bucket == premade_bucket
    assert a.continuity_score == continuity_score
    assert a.clips[0].internal_pause_max_sec == internal_pause

    # Also assert the on-disk JSON literally writes nulls (not empties).
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    written_attempt = on_disk["attempts"]["1"]
    assert written_attempt["premade_bucket"] == premade_bucket
    assert written_attempt["continuity_score"] == continuity_score
    assert written_attempt["clips"][0]["internal_pause_max_sec"] == internal_pause
