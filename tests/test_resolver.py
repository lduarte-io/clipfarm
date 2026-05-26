"""Tests for `clipfarm/resolver.py` — pure attempt → playback resolver."""
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
    Source,
)
from clipfarm.resolver import (
    ResolvedRange,
    TombstoneRange,
    resolve_attempt,
)
from clipfarm.transcripts import cache


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state(
    *,
    sources: list[tuple[str, float | None]] | None = None,
    clips: list[tuple[str, str, float, float]] | None = None,
    attempt_id: str = "a1",
    attempt_clips: list[AttemptClip] | None = None,
) -> ClipFarmState:
    """sources = [(source_id, duration_sec_or_None), ...]
    clips = [(clip_id, source_id, start, end), ...]
    attempt_clips = list[AttemptClip]"""
    state = ClipFarmState()
    for sid, dur in (sources or [("s1", 100.0)]):
        state.sources[sid] = Source(
            filename=f"src{sid}.mov",
            path=f"/src{sid}.mov",
            duration_sec=dur,
            added_at=_now(),
            unavailable=True,
        )
    for cid, sid, s, e in (clips or []):
        state.clips[cid] = Clip(
            source_id=sid, start_sec=s, end_sec=e, created_at=_now()
        )
    state.attempts[attempt_id] = Attempt(
        project_id="p1",
        name="test",
        source="hand-built",
        clips=attempt_clips or [],
        created_at=_now(),
    )
    return state


@pytest.fixture(autouse=True)
def _clear_transcript_cache():
    cache().clear()
    yield
    cache().clear()


# ─────────────────────────────────────────────────────────────────────────────
# Happy path + trim
# ─────────────────────────────────────────────────────────────────────────────


def test_single_clip_no_trim_emits_one_range():
    state = _state(
        clips=[("c0", "s1", 5.0, 15.0)],
        attempt_clips=[AttemptClip(clip_id="c0")],
    )
    items = resolve_attempt(state, "a1")
    assert len(items) == 1
    r = items[0]
    assert isinstance(r, ResolvedRange)
    assert r.clip_id == "c0"
    assert r.source_id == "s1"
    assert r.effective_start_sec == 5.0
    assert r.effective_end_sec == 15.0


def test_trim_start_offset_advances_effective_start():
    """Positive trim_start_offset moves the start FORWARD."""
    state = _state(
        clips=[("c0", "s1", 5.0, 15.0)],
        attempt_clips=[AttemptClip(clip_id="c0", trim_start_offset=2.0)],
    )
    [r] = resolve_attempt(state, "a1")
    assert isinstance(r, ResolvedRange)
    assert r.effective_start_sec == 7.0
    assert r.effective_end_sec == 15.0


def test_trim_end_offset_retracts_effective_end():
    """Positive trim_end_offset moves the end BACKWARD."""
    state = _state(
        clips=[("c0", "s1", 5.0, 15.0)],
        attempt_clips=[AttemptClip(clip_id="c0", trim_end_offset=3.0)],
    )
    [r] = resolve_attempt(state, "a1")
    assert isinstance(r, ResolvedRange)
    assert r.effective_end_sec == 12.0


# ─────────────────────────────────────────────────────────────────────────────
# Source-bounds clamping (plan-review #2)
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_effective_start_clamped_to_zero():
    """Negative trim_start_offset that pushes start past source 0 is clamped."""
    state = _state(
        clips=[("c0", "s1", 5.0, 15.0)],
        # trim_start_offset = -10 → raw_start = -5; clamp to 0
        attempt_clips=[AttemptClip(clip_id="c0", trim_start_offset=-10.0)],
    )
    [r] = resolve_attempt(state, "a1")
    assert isinstance(r, ResolvedRange)
    assert r.effective_start_sec == 0.0  # clamped


def test_effective_end_past_source_duration_clamped(caplog):
    """Negative trim_end_offset that pushes end past source duration is clamped."""
    state = _state(
        sources=[("s1", 20.0)],
        clips=[("c0", "s1", 5.0, 15.0)],
        # trim_end_offset = -10 → raw_end = 25; clamp to source duration 20
        attempt_clips=[AttemptClip(clip_id="c0", trim_end_offset=-10.0)],
    )
    with caplog.at_level("WARNING", logger="clipfarm.resolver"):
        [r] = resolve_attempt(state, "a1")
    assert isinstance(r, ResolvedRange)
    assert r.effective_end_sec == 20.0  # clamped to source duration
    assert any("clamped" in m for m in caplog.messages)


def test_unknown_source_duration_treated_as_infinity():
    """If `source.duration_sec is None` (ffprobe failed), no end-clamp fires."""
    state = _state(
        sources=[("s1", None)],
        clips=[("c0", "s1", 5.0, 15.0)],
        attempt_clips=[AttemptClip(clip_id="c0", trim_end_offset=-100.0)],
    )
    [r] = resolve_attempt(state, "a1")
    assert isinstance(r, ResolvedRange)
    # raw_end = 115; source.duration_sec is None → treated as +inf, no clamp.
    assert r.effective_end_sec == 115.0


def test_zero_duration_after_clamp_raises():
    """If trim collapses the range to ≤ 0 effective duration, raise."""
    state = _state(
        clips=[("c0", "s1", 5.0, 6.0)],
        # 1-second clip; trim 2s from start → effective_start (5+2=7) > end (6)
        attempt_clips=[AttemptClip(clip_id="c0", trim_start_offset=2.0)],
    )
    with pytest.raises(ValueError, match="zero/negative effective duration"):
        resolve_attempt(state, "a1")


# ─────────────────────────────────────────────────────────────────────────────
# Tombstone (dangling clip)
# ─────────────────────────────────────────────────────────────────────────────


def test_dangling_clip_emits_tombstone():
    """An AttemptClip pointing at a missing clip ID emits a tombstone."""
    state = _state(
        clips=[("c0", "s1", 0, 5)],
        attempt_clips=[
            AttemptClip(clip_id="c0"),
            AttemptClip(clip_id="c_deleted"),  # missing from state.clips
            AttemptClip(clip_id="c0"),
        ],
    )
    items = resolve_attempt(state, "a1")
    assert len(items) == 3
    assert isinstance(items[0], ResolvedRange)
    assert isinstance(items[1], TombstoneRange)
    assert items[1].clip_id == "c_deleted"
    assert isinstance(items[2], ResolvedRange)


# ─────────────────────────────────────────────────────────────────────────────
# internal_pause_max_sec gap-drop expansion (plan-review #1)
# ─────────────────────────────────────────────────────────────────────────────


def _state_with_transcript(
    tmp_path: Path, words: list[tuple[float, float, str]]
) -> ClipFarmState:
    """Build a state with one source whose Whisper sidecar has the
    given words. `words` is `[(start, end, text), ...]`."""
    transcript_path = tmp_path / "src.whisper.json"
    transcript_path.write_text(json.dumps({
        "schema_version": 1,
        "duration": words[-1][1] + 1.0 if words else 1.0,
        "segments": [{
            "id": 0,
            "start": words[0][0] if words else 0,
            "end": words[-1][1] if words else 0,
            "words": [{"start": s, "end": e, "word": w} for (s, e, w) in words],
        }],
    }), encoding="utf-8")
    state = ClipFarmState()
    state.sources["s1"] = Source(
        filename="src.mov",
        path=str(tmp_path / "src.mov"),
        duration_sec=words[-1][1] + 1.0 if words else 1.0,
        transcript_path=str(transcript_path),
        added_at=_now(),
        unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="s1",
        start_sec=words[0][0] if words else 0,
        end_sec=words[-1][1] if words else 0,
        created_at=_now(),
    )
    state.attempts["a1"] = Attempt(
        project_id="p1", name="test", source="hand-built",
        clips=[AttemptClip(clip_id="c0", internal_pause_max_sec=0.5)],
        created_at=_now(),
    )
    return state


def test_internal_pause_no_gaps_returns_single_range(tmp_path):
    """Words flow with small gaps (< 0.5s) → single range, no split."""
    state = _state_with_transcript(tmp_path, [
        (0.0, 0.5, " hello"),
        (0.6, 1.0, " world"),
        (1.1, 1.5, " again"),
    ])
    items = resolve_attempt(state, "a1")
    assert len(items) == 1
    assert isinstance(items[0], ResolvedRange)


def test_internal_pause_one_gap_over_max_splits_in_two(tmp_path):
    """One inter-word gap > 0.5s → two sub-ranges, gap dropped entirely."""
    state = _state_with_transcript(tmp_path, [
        (0.0, 0.5, " hello"),
        (0.6, 1.0, " world"),
        (3.0, 3.5, " again"),  # 2-second gap before "again"
        (3.6, 4.0, " more"),
    ])
    items = resolve_attempt(state, "a1")
    assert len(items) == 2
    a, b = items
    assert isinstance(a, ResolvedRange)
    assert isinstance(b, ResolvedRange)
    # First sub-range ends at 1.0 (the previous word's end).
    assert abs(a.effective_end_sec - 1.0) < 1e-6
    # Second sub-range starts at 3.0 (the next word's start). Gap is GONE.
    assert abs(b.effective_start_sec - 3.0) < 1e-6


def test_internal_pause_gap_exactly_at_max_does_not_split(tmp_path):
    """Gap of EXACTLY 0.5s with max=0.5 → no split (uses strict `>`)."""
    state = _state_with_transcript(tmp_path, [
        (0.0, 0.5, " hello"),
        (1.0, 1.5, " world"),  # gap = 0.5, exactly max
    ])
    items = resolve_attempt(state, "a1")
    assert len(items) == 1


def test_internal_pause_with_missing_transcript_falls_back(tmp_path, caplog):
    """Source has no transcript_path → fallback to single un-expanded range
    + warning."""
    state = ClipFarmState()
    state.sources["s1"] = Source(
        filename="src.mov", path="/src.mov",
        duration_sec=20.0, transcript_path=None,
        added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="s1", start_sec=0, end_sec=10, created_at=_now()
    )
    state.attempts["a1"] = Attempt(
        project_id="p1", name="test", source="hand-built",
        clips=[AttemptClip(clip_id="c0", internal_pause_max_sec=0.5)],
        created_at=_now(),
    )
    with caplog.at_level("WARNING", logger="clipfarm.resolver"):
        items = resolve_attempt(state, "a1")
    assert len(items) == 1
    assert isinstance(items[0], ResolvedRange)
    assert any("transcript unavailable" in m for m in caplog.messages)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-clip ordering + unknown attempt
# ─────────────────────────────────────────────────────────────────────────────


def test_multi_clip_attempt_preserves_order():
    state = _state(
        clips=[
            ("c0", "s1", 0, 5),
            ("c1", "s1", 10, 15),
            ("c2", "s1", 20, 25),
        ],
        attempt_clips=[
            AttemptClip(clip_id="c2"),
            AttemptClip(clip_id="c0"),
            AttemptClip(clip_id="c1"),
        ],
    )
    items = resolve_attempt(state, "a1")
    assert [i.clip_id for i in items] == ["c2", "c0", "c1"]


def test_unknown_attempt_raises_keyerror():
    state = _state()
    with pytest.raises(KeyError):
        resolve_attempt(state, "missing")
