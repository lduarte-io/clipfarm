"""Tests for `clipfarm/continuity.py` — pure formula."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clipfarm.continuity import compute_continuity_score
from clipfarm.models import AttemptClip, Clip, ClipFarmState, Source


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_with_clips(clip_specs: list[tuple[str, str, float, float]]) -> ClipFarmState:
    """Build a state. `clip_specs` is a list of
    `(clip_id, source_id, start_sec, end_sec)`."""
    state = ClipFarmState()
    sids: set[str] = set()
    for _, sid, _, _ in clip_specs:
        sids.add(sid)
    for sid in sids:
        state.sources[sid] = Source(
            filename=f"src{sid}.mov",
            path=f"/src{sid}.mov",
            added_at=_now(),
            unavailable=True,
        )
    for cid, sid, s, e in clip_specs:
        state.clips[cid] = Clip(
            source_id=sid, start_sec=s, end_sec=e, created_at=_now()
        )
    return state


def _ac(cid: str) -> AttemptClip:
    return AttemptClip(clip_id=cid)


def test_single_clip_attempt_score_is_one():
    """A single clip is its own run covering 100% of the attempt."""
    state = _state_with_clips([("c0", "1", 0.0, 10.0)])
    score = compute_continuity_score(state, [_ac("c0")])
    assert score == 1.0


def test_two_consecutive_same_source_clips_score_is_one():
    """Two clips from the same source progressing forward = one run."""
    state = _state_with_clips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "1", 5.0, 10.0),
    ])
    score = compute_continuity_score(state, [_ac("c0"), _ac("c1")])
    assert score == 1.0


def test_two_clips_different_sources_max_over_total():
    """Different sources → two runs of 5s each → max 5 / total 10 = 0.5."""
    state = _state_with_clips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "2", 0.0, 5.0),
    ])
    score = compute_continuity_score(state, [_ac("c0"), _ac("c1")])
    assert score == 0.5


def test_backward_jump_in_source_breaks_run():
    """Two clips from the same source but the second starts before the
    first ends → run break. Equivalent to two-different-sources."""
    state = _state_with_clips([
        ("c0", "1", 10.0, 20.0),  # 10s
        ("c1", "1", 0.0, 5.0),    # 5s, BEFORE c0 → run break
    ])
    score = compute_continuity_score(state, [_ac("c0"), _ac("c1")])
    # max 10 / total 15 ≈ 0.667
    assert abs(score - (10.0 / 15.0)) < 1e-9


def test_three_clips_two_in_one_run_one_separate():
    """c0, c1 in source 1 contiguous (10s); c2 in source 2 (4s).
    Run 1 = 10s, run 2 = 4s. max 10 / total 14 ≈ 0.714."""
    state = _state_with_clips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "1", 5.0, 10.0),
        ("c2", "2", 0.0, 4.0),
    ])
    score = compute_continuity_score(state, [_ac("c0"), _ac("c1"), _ac("c2")])
    assert abs(score - (10.0 / 14.0)) < 1e-9


def test_empty_attempt_clips_raises():
    state = _state_with_clips([])
    with pytest.raises(ValueError, match="undefined for empty"):
        compute_continuity_score(state, [])


def test_zero_runtime_attempt_raises():
    """Every clip has start == end (degenerate). The formula has no
    meaningful value; orchestrator should never produce this."""
    state = _state_with_clips([("c0", "1", 5.0, 5.0)])
    with pytest.raises(ValueError, match="zero total runtime"):
        compute_continuity_score(state, [_ac("c0")])


def test_orphan_clip_id_handled():
    """An AttemptClip pointing at a missing clip is treated as a
    run-breaker (zero runtime, can't be contiguous). If ALL clips are
    orphans, raise; if some are valid, the valid ones still produce
    a real score."""
    state = _state_with_clips([("c0", "1", 0.0, 5.0)])
    # All orphans → raise.
    with pytest.raises(ValueError):
        compute_continuity_score(state, [_ac("missing1"), _ac("missing2")])
    # Mixed: c0 (valid, 5s run) + orphan → the orphan breaks the run
    # AND contributes 0s, so total = 5s, max = 5s, score = 1.0.
    score = compute_continuity_score(state, [_ac("c0"), _ac("missing")])
    assert score == 1.0


def test_trim_offsets_shrink_runtime():
    """trim_start_offset + trim_end_offset positive subtract from runtime."""
    state = _state_with_clips([
        ("c0", "1", 0.0, 10.0),  # 10s base
        ("c1", "2", 0.0, 10.0),  # 10s base
    ])
    # Trim c0 down to 4s (trim 3 from each end); c1 stays at 10s.
    # Runs: 4s, 10s. max 10 / total 14 ≈ 0.714.
    ac0 = AttemptClip(clip_id="c0", trim_start_offset=3.0, trim_end_offset=3.0)
    score = compute_continuity_score(state, [ac0, _ac("c1")])
    assert abs(score - (10.0 / 14.0)) < 1e-9
