"""Tests for `clipfarm/boundary.py` — pure orchestration. Each test
builds synthetic state, calls the boundary op, and asserts on the
mutated state shape. No HTTP, no disk, no snapshots (the route layer
handles those).

Where tag + attempt propagation is exercised, the synthetic shapes are
constructed inline so Phase 6 / Phase 8 can plug their real writers
into the same code paths without re-validating semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clipfarm.boundary import (
    adjust_clip_boundaries,
    create_clip_from_range,
    delete_clip,
    merge_clips,
    split_clip,
)
from clipfarm.models import (
    Attempt,
    AttemptClip,
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Source,
    WhisperSegment,
    WhisperTranscript,
    WhisperWord,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_state(*, duration: float = 100.0) -> ClipFarmState:
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="src.mov",
        path="/fake/src.mov",
        duration_sec=duration,
        added_at=_now(),
        unavailable=True,
    )
    return state


def _add_clip(
    state: ClipFarmState,
    clip_id: str,
    start: float,
    end: float,
    text: str = "",
) -> None:
    state.clips[clip_id] = Clip(
        source_id="1",
        start_sec=start,
        end_sec=end,
        transcript_text=text,
        created_at=_now(),
    )


def _transcript_with_words(*words: tuple[float, float, str]) -> WhisperTranscript:
    if not words:
        return WhisperTranscript(schema_version=1, segments=[])
    seg = WhisperSegment(
        start=words[0][0],
        end=words[-1][1],
        words=[WhisperWord(start=s, end=e, word=w) for (s, e, w) in words],
    )
    return WhisperTranscript(schema_version=1, segments=[seg])


# ---------- split_clip --------------------------------------------------------


def test_split_clip_produces_two_clips_with_correct_boundaries():
    state = _make_state()
    _add_clip(state, "src__00-00-00.000__00-00-10.000", 0.0, 10.0)
    transcript = _transcript_with_words(
        (1.0, 1.5, " hello"),
        (6.0, 6.5, " world"),
    )

    c1, c2 = split_clip(
        state, "src__00-00-00.000__00-00-10.000", 5.0, transcript
    )

    assert c1 != c2
    assert "src__00-00-00.000__00-00-10.000" not in state.clips
    assert state.clips[c1].start_sec == 0.0
    assert state.clips[c1].end_sec == 5.0
    assert state.clips[c1].transcript_text == "hello"
    assert state.clips[c2].start_sec == 5.0
    assert state.clips[c2].end_sec == 10.0
    assert state.clips[c2].transcript_text == "world"


def test_split_clip_unknown_id_raises():
    state = _make_state()
    with pytest.raises(KeyError):
        split_clip(state, "nope", 5.0, None)


@pytest.mark.parametrize("split_at", [-1.0, 0.0, 10.0, 11.0])
def test_split_clip_at_or_outside_range_raises(split_at):
    state = _make_state()
    _add_clip(state, "c1", 0.0, 10.0)
    with pytest.raises(ValueError):
        split_clip(state, "c1", split_at, None)


def test_split_clip_with_footage_only_source_uses_empty_text():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 10.0)
    c1, c2 = split_clip(state, "c1", 5.0, transcript=None)
    assert state.clips[c1].transcript_text == ""
    assert state.clips[c2].transcript_text == ""


def test_split_clip_clones_tags_to_both_with_stale_flag():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 10.0)
    state.clip_project_tags = [
        ClipProjectTag(
            clip_id="c1", project_id="p1", project_tag_id="t1",
            category="on-script", source="ai",
        )
    ]
    c1, c2 = split_clip(state, "c1", 5.0, None)
    # Two cloned rows (one per new clip), original dropped.
    assert {t.clip_id for t in state.clip_project_tags} == {c1, c2}
    assert all(t.stale for t in state.clip_project_tags)


def test_split_clip_reassigns_attempts_to_first_half_with_review_flag():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 10.0)
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="a1",
        clips=[AttemptClip(clip_id="c1"), AttemptClip(clip_id="other")],
        created_at=_now(),
    )
    c1, _c2 = split_clip(state, "c1", 5.0, None)
    assert state.attempts["1"].clips[0].clip_id == c1  # to first half
    assert state.attempts["1"].clips[1].clip_id == "other"  # untouched
    assert state.attempts["1"].needs_review is True


# ---------- merge_clips -------------------------------------------------------


def test_merge_two_adjacent_clips_into_one():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 5.0, 10.0)
    transcript = _transcript_with_words(
        (1.0, 1.5, " first"),
        (6.0, 6.5, " second"),
    )
    new_id = merge_clips(state, ["c1", "c2"], transcript)
    assert "c1" not in state.clips and "c2" not in state.clips
    assert state.clips[new_id].start_sec == 0.0
    assert state.clips[new_id].end_sec == 10.0
    assert state.clips[new_id].transcript_text == "first second"


def test_merge_three_clips_with_silence_gaps_folds_silence_in():
    """Locked policy: any gap is folded into the merged range."""
    state = _make_state()
    _add_clip(state, "c1", 0.0, 2.0)
    _add_clip(state, "c2", 4.0, 6.0)  # 2-sec gap
    _add_clip(state, "c3", 8.0, 10.0)  # 2-sec gap
    new_id = merge_clips(state, ["c1", "c2", "c3"], None)
    assert state.clips[new_id].start_sec == 0.0
    assert state.clips[new_id].end_sec == 10.0


def test_merge_out_of_order_input_gets_sorted():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 5.0, 10.0)
    new_id = merge_clips(state, ["c2", "c1"], None)
    assert state.clips[new_id].start_sec == 0.0
    assert state.clips[new_id].end_sec == 10.0


def test_merge_overlapping_clips_raises():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 6.0)
    _add_clip(state, "c2", 5.0, 10.0)  # overlap on [5, 6)
    with pytest.raises(ValueError, match="overlap"):
        merge_clips(state, ["c1", "c2"], None)


def test_merge_cross_source_raises():
    state = _make_state()
    state.sources["2"] = Source(
        filename="src2.mov",
        path="/fake/src2.mov",
        added_at=_now(),
        unavailable=True,
    )
    state.clips["c1"] = Clip(
        source_id="1", start_sec=0.0, end_sec=5.0, created_at=_now()
    )
    state.clips["c2"] = Clip(
        source_id="2", start_sec=5.0, end_sec=10.0, created_at=_now()
    )
    with pytest.raises(ValueError, match="same source"):
        merge_clips(state, ["c1", "c2"], None)


def test_merge_single_clip_raises():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    with pytest.raises(ValueError, match=">= 2"):
        merge_clips(state, ["c1"], None)


def test_merge_duplicate_clip_id_raises():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    with pytest.raises(ValueError, match="unique"):
        merge_clips(state, ["c1", "c1"], None)


def test_merge_unions_and_dedupes_tags():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 5.0, 10.0)
    state.clip_project_tags = [
        ClipProjectTag(
            clip_id="c1", project_id="p1", project_tag_id="t1",
            category="on-script",
        ),
        ClipProjectTag(
            clip_id="c2", project_id="p1", project_tag_id="t1",
            category="on-script",
        ),  # duplicate triple
        ClipProjectTag(
            clip_id="c2", project_id="p1", project_tag_id="t2",
            category="related-but-different",
        ),
    ]
    new_id = merge_clips(state, ["c1", "c2"], None)
    rows = [t for t in state.clip_project_tags if t.clip_id == new_id]
    triples = {(t.project_id, t.project_tag_id, t.category) for t in rows}
    assert triples == {
        ("p1", "t1", "on-script"),
        ("p1", "t2", "related-but-different"),
    }


def test_merge_reassigns_attempt_refs_without_review_flag():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 5.0, 10.0)
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="a",
        clips=[AttemptClip(clip_id="c1"), AttemptClip(clip_id="c2")],
        created_at=_now(),
    )
    new_id = merge_clips(state, ["c1", "c2"], None)
    assert [ac.clip_id for ac in state.attempts["1"].clips] == [new_id, new_id]
    # Merge is a clean substitution — no review flag.
    assert state.attempts["1"].needs_review is False


# ---------- adjust_clip_boundaries -------------------------------------------


def test_adjust_boundaries_extends_clip_id_unchanged():
    state = _make_state()
    _add_clip(state, "c1", 4.0, 6.0)
    transcript = _transcript_with_words((1.0, 1.5, " w"))
    adjust_clip_boundaries(state, "c1", 2.0, 8.0, transcript)
    # Same clip ID even though boundaries changed (spec invariant).
    assert "c1" in state.clips
    assert state.clips["c1"].start_sec == 2.0
    assert state.clips["c1"].end_sec == 8.0


def test_adjust_boundaries_recomputes_transcript_text():
    state = _make_state()
    _add_clip(state, "c1", 4.0, 6.0, text="(old)")
    transcript = _transcript_with_words(
        (3.0, 3.5, " before"),
        (5.0, 5.5, " mid"),
        (7.0, 7.5, " after"),
    )
    adjust_clip_boundaries(state, "c1", 2.0, 8.0, transcript)
    assert state.clips["c1"].transcript_text == "before mid after"


def test_adjust_boundaries_invalid_range_raises():
    state = _make_state()
    _add_clip(state, "c1", 4.0, 6.0)
    with pytest.raises(ValueError):
        adjust_clip_boundaries(state, "c1", 8.0, 5.0, None)


def test_adjust_boundaries_negative_start_raises():
    state = _make_state()
    _add_clip(state, "c1", 4.0, 6.0)
    with pytest.raises(ValueError):
        adjust_clip_boundaries(state, "c1", -1.0, 5.0, None)


def test_adjust_boundaries_past_source_duration_raises():
    state = _make_state(duration=50.0)
    _add_clip(state, "c1", 40.0, 45.0)
    with pytest.raises(ValueError, match="exceeds source duration"):
        adjust_clip_boundaries(state, "c1", 40.0, 100.0, None)


def test_adjust_boundaries_overlap_with_neighbor_raises():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 10.0, 15.0)
    # Extending c1 to [0, 11) overlaps c2's [10, 15).
    with pytest.raises(ValueError, match="overlaps existing clip"):
        adjust_clip_boundaries(state, "c1", 0.0, 11.0, None)


def test_adjust_boundaries_touching_neighbor_is_ok():
    """Half-open intervals: c1 ending exactly where c2 starts is not an
    overlap (touching at the shared endpoint is fine)."""
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    _add_clip(state, "c2", 10.0, 15.0)
    adjust_clip_boundaries(state, "c1", 0.0, 10.0, None)
    assert state.clips["c1"].end_sec == 10.0


def test_adjust_boundaries_calls_clamp_for_attempts():
    state = _make_state()
    _add_clip(state, "c1", 10.0, 20.0)
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="a",
        clips=[AttemptClip(clip_id="c1", trim_start_offset=5.0)],
        created_at=_now(),
    )
    # Move start inward by 2: 10 → 12.
    adjust_clip_boundaries(state, "c1", 12.0, 20.0, None)
    # Trim should clamp from 5 to 3 (preserves absolute effective at 15).
    assert state.attempts["1"].clips[0].trim_start_offset == pytest.approx(3.0)


# ---------- create_clip_from_range -------------------------------------------


def test_create_clip_from_range_happy_path():
    state = _make_state()
    transcript = _transcript_with_words((1.0, 1.5, " hello"))
    new_id = create_clip_from_range(state, "1", 0.0, 5.0, transcript)
    assert new_id in state.clips
    assert state.clips[new_id].start_sec == 0.0
    assert state.clips[new_id].end_sec == 5.0
    assert state.clips[new_id].transcript_text == "hello"


def test_create_clip_from_range_footage_only_has_empty_text():
    state = _make_state()
    new_id = create_clip_from_range(state, "1", 0.0, 5.0, transcript=None)
    assert state.clips[new_id].transcript_text == ""


def test_create_clip_from_range_overlap_allowed(caplog):
    """Phase 10a dogfood revision (2026-05-26): overlap with an
    existing clip on the same source is ALLOWED. Spec updated to
    match — merge still rejects overlap, create_clip does not.
    Logs an INFO note about the overlap for observability."""
    state = _make_state()
    _add_clip(state, "c1", 2.0, 8.0)
    with caplog.at_level("INFO", logger="clipfarm.boundary"):
        new_id = create_clip_from_range(state, "1", 5.0, 10.0, None)
    # The new clip exists with a distinct ID from the overlapping one.
    assert new_id in state.clips
    assert new_id != "c1"
    assert state.clips[new_id].start_sec == 5.0
    assert state.clips[new_id].end_sec == 10.0
    # Original clip untouched.
    assert "c1" in state.clips
    # Overlap logged.
    assert any("overlaps existing clip" in m for m in caplog.messages)


def test_create_clip_from_range_exact_duplicate_still_rejected():
    """Same source + same exact start + same exact end → clip-ID
    collision (encoded ID is identical) → still rejected. Spec note:
    'duplicate-identical clip — still rejected. The encoded ID
    format keeps these unique by construction.' Creates via the
    public function twice so the second call hits the ID-collision
    check rather than the seed-helper bypass."""
    state = _make_state()
    create_clip_from_range(state, "1", 5.0, 10.0, None)
    with pytest.raises(ValueError, match="would collide"):
        create_clip_from_range(state, "1", 5.0, 10.0, None)


def test_create_clip_from_range_unknown_source_raises():
    state = _make_state()
    with pytest.raises(KeyError):
        create_clip_from_range(state, "99", 0.0, 5.0, None)


def test_create_clip_from_range_invalid_range_raises():
    state = _make_state()
    with pytest.raises(ValueError):
        create_clip_from_range(state, "1", 5.0, 5.0, None)
    with pytest.raises(ValueError):
        create_clip_from_range(state, "1", -1.0, 5.0, None)


# ---------- delete_clip -------------------------------------------------------


def test_delete_clip_removes_from_state():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    dropped, affected = delete_clip(state, "c1")
    assert "c1" not in state.clips
    assert dropped == 0
    assert affected == 0


def test_delete_clip_drops_tag_rows():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    state.clip_project_tags = [
        ClipProjectTag(
            clip_id="c1", project_id="p1", project_tag_id="t1",
            category="on-script",
        ),
        ClipProjectTag(
            clip_id="c1", project_id="p1", project_tag_id="t2",
            category="related-but-different",
        ),
        ClipProjectTag(
            clip_id="other", project_id="p1", project_tag_id="t1",
            category="on-script",
        ),
    ]
    dropped, _ = delete_clip(state, "c1")
    assert dropped == 2
    assert [t.clip_id for t in state.clip_project_tags] == ["other"]


def test_delete_clip_marks_attempts_needs_review_and_leaves_ref_dangling():
    state = _make_state()
    _add_clip(state, "c1", 0.0, 5.0)
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="a",
        clips=[AttemptClip(clip_id="c1")],
        created_at=_now(),
    )
    dropped, affected = delete_clip(state, "c1")
    assert dropped == 0
    assert affected == 1
    # Tombstone: the AttemptClip still points at the deleted ID.
    assert state.attempts["1"].clips[0].clip_id == "c1"
    assert state.attempts["1"].needs_review is True


def test_delete_clip_unknown_raises():
    state = _make_state()
    with pytest.raises(KeyError):
        delete_clip(state, "nope")
