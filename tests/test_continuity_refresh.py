"""Tests for `attempts_ops.refresh_attempt_continuity` — the helper
that recomputes `Attempt.continuity_score` after every clip-list
mutation. Wraps `compute_continuity_score` with degenerate-case
handling (empty / all-orphan / zero-runtime → None instead of raise).
"""
from __future__ import annotations

from datetime import datetime, timezone

from clipfarm.attempts_ops import refresh_attempt_continuity
from clipfarm.models import (
    Attempt, AttemptClip, Clip, ClipFarmState, Source,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_with_clips(specs: list[tuple[str, str, float, float]]) -> ClipFarmState:
    """specs = [(clip_id, source_id, start, end), ...]"""
    state = ClipFarmState()
    seen_sources: set[str] = set()
    for _, sid, _, _ in specs:
        if sid not in seen_sources:
            state.sources[sid] = Source(
                filename=f"src{sid}.mov", path=f"/src{sid}.mov",
                added_at=_now(), unavailable=True,
            )
            seen_sources.add(sid)
    for cid, sid, s, e in specs:
        state.clips[cid] = Clip(
            source_id=sid, start_sec=s, end_sec=e, created_at=_now(),
        )
    return state


def _attempt(clip_ids: list[str], *, initial_score: float | None = 0.5) -> Attempt:
    return Attempt(
        project_id="p1", name="x", source="hand-built",
        clips=[AttemptClip(clip_id=cid) for cid in clip_ids],
        continuity_score=initial_score, created_at=_now(),
    )


def test_empty_clips_sets_score_to_none():
    """An empty clip list (e.g. freshly-created hand-built draft, or
    user dragged all clips out) → continuity_score = None."""
    state = _state_with_clips([])
    attempt = _attempt([], initial_score=0.9)
    refresh_attempt_continuity(state, attempt)
    assert attempt.continuity_score is None


def test_single_clip_yields_score_one():
    """A single live clip is its own run, continuity = 1.0."""
    state = _state_with_clips([("c0", "s1", 0.0, 5.0)])
    attempt = _attempt(["c0"], initial_score=0.5)
    refresh_attempt_continuity(state, attempt)
    assert attempt.continuity_score == 1.0


def test_all_orphan_clips_falls_back_to_none():
    """If every clip is a tombstone (referenced clip_id is missing
    from state.clips), the underlying compute_continuity_score raises
    'every clip in the attempt is missing from state.clips'. The
    helper catches and sets score to None instead of raising."""
    state = _state_with_clips([])  # no clips at all
    attempt = _attempt(["c_missing", "c_also_missing"], initial_score=0.9)
    refresh_attempt_continuity(state, attempt)
    assert attempt.continuity_score is None


def test_mixed_live_and_orphan_clips_still_computes():
    """One live + one orphan → live clip provides runtime, orphan is
    a zero-runtime run break. Score reflects the live clip share."""
    state = _state_with_clips([("c0", "s1", 0.0, 10.0)])
    attempt = _attempt(["c0", "c_orphan"], initial_score=None)
    refresh_attempt_continuity(state, attempt)
    # c0 contributes 10s; c_orphan contributes 0s. Total 10s, max run 10s.
    # Score = 10/10 = 1.0.
    assert attempt.continuity_score == 1.0


def test_zero_runtime_clip_yields_none():
    """A single clip with start==end has zero runtime; compute_
    continuity_score raises 'zero total runtime'. The helper catches
    and sets None."""
    state = _state_with_clips([("c0", "s1", 5.0, 5.0)])
    attempt = _attempt(["c0"], initial_score=0.7)
    refresh_attempt_continuity(state, attempt)
    assert attempt.continuity_score is None
