"""Tests for `clipfarm/propagation.py` — pure rules tested with synthetic
state. Phases 6 (tagging) and 8 (attempts) will be the first real writers
of these structures; locking the rules here means those phases plug in
without re-implementing semantics."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipfarm.models import (
    Attempt,
    AttemptClip,
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Source,
)
from clipfarm.propagation import (
    clamp_attempt_trims_for_clip,
    clone_tags_to_pair,
    drop_tags_for_clip,
    mark_attempts_needs_review_for_clip,
    reassign_attempt_refs,
    union_merge_tags,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_with_clip(
    clip_id: str = "src__00-00-00.000__00-00-10.000",
    *,
    start: float = 0.0,
    end: float = 10.0,
) -> ClipFarmState:
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="src.mov",
        path="/fake/src.mov",
        added_at=_now(),
        unavailable=True,
    )
    state.clips[clip_id] = Clip(
        source_id="1",
        start_sec=start,
        end_sec=end,
        transcript_text="",
        created_at=_now(),
    )
    return state


def _tag(clip_id: str, **overrides) -> ClipProjectTag:
    return ClipProjectTag(
        clip_id=clip_id,
        project_id=overrides.get("project_id", "p1"),
        project_tag_id=overrides.get("project_tag_id", "t1"),
        category=overrides.get("category", "on-script"),
        confidence=overrides.get("confidence", 0.8),
        source=overrides.get("source", "ai"),
        stale=overrides.get("stale", False),
        notes=overrides.get("notes", ""),
    )


def _attempt(name: str, *clip_ids: str, **overrides) -> Attempt:
    return Attempt(
        project_id=overrides.get("project_id", "p1"),
        name=name,
        clips=[
            AttemptClip(
                clip_id=cid,
                trim_start_offset=overrides.get("trim_s", 0.0),
                trim_end_offset=overrides.get("trim_e", 0.0),
            )
            for cid in clip_ids
        ],
        created_at=_now(),
    )


# ---------- Tag propagation ---------------------------------------------------


def test_clone_tags_to_pair_clones_with_stale_flag():
    state = _state_with_clip("c1")
    state.clip_project_tags = [
        _tag("c1", project_tag_id="t1"),
        _tag("c1", project_tag_id="t2"),
    ]
    added = clone_tags_to_pair(state, "c1", ["a", "b"], stale=True)
    assert added == 4  # 2 tags × 2 targets
    new_a = [t for t in state.clip_project_tags if t.clip_id == "a"]
    new_b = [t for t in state.clip_project_tags if t.clip_id == "b"]
    assert len(new_a) == 2 and len(new_b) == 2
    assert all(t.stale for t in new_a + new_b)
    # Original rows are NOT removed by clone — that's a separate helper.
    assert len([t for t in state.clip_project_tags if t.clip_id == "c1"]) == 2


def test_clone_tags_to_pair_no_source_tags_is_noop():
    state = _state_with_clip("c1")
    added = clone_tags_to_pair(state, "c1", ["a", "b"], stale=True)
    assert added == 0
    assert state.clip_project_tags == []


def test_union_merge_tags_dedupes_on_triple():
    state = _state_with_clip("a")
    # Overlapping (project_id, project_tag_id, category) triples across c1 and c2.
    state.clip_project_tags = [
        _tag("c1", project_id="p1", project_tag_id="t1", category="on-script"),
        _tag("c2", project_id="p1", project_tag_id="t1", category="on-script"),  # dup
        _tag("c1", project_id="p1", project_tag_id="t2", category="related-but-different"),
        _tag("c2", project_id="p2", project_tag_id="t1", category="standalone-idea"),
    ]
    kept = union_merge_tags(state, ["c1", "c2"], "a")
    assert kept == 3  # one duplicate removed
    a_rows = [t for t in state.clip_project_tags if t.clip_id == "a"]
    triples = {(t.project_id, t.project_tag_id, t.category) for t in a_rows}
    assert triples == {
        ("p1", "t1", "on-script"),
        ("p1", "t2", "related-but-different"),
        ("p2", "t1", "standalone-idea"),
    }
    # Source rows are gone.
    assert not any(t.clip_id in {"c1", "c2"} for t in state.clip_project_tags)


def test_union_merge_tags_empty_input_is_noop():
    state = _state_with_clip("a")
    state.clip_project_tags = [_tag("other", project_tag_id="t1")]
    kept = union_merge_tags(state, ["c1"], "a")
    assert kept == 0
    # Unrelated tag untouched.
    assert len(state.clip_project_tags) == 1


def test_drop_tags_for_clip_only_removes_matching():
    state = _state_with_clip("c1")
    state.clip_project_tags = [
        _tag("c1", project_tag_id="t1"),
        _tag("c1", project_tag_id="t2"),
        _tag("other", project_tag_id="t1"),
    ]
    dropped = drop_tags_for_clip(state, "c1")
    assert dropped == 2
    assert [t.clip_id for t in state.clip_project_tags] == ["other"]


# ---------- Attempt-ref propagation ------------------------------------------


def test_reassign_attempt_refs_swaps_clip_id():
    state = _state_with_clip("c1")
    state.attempts["1"] = _attempt("a1", "c1", "other")
    state.attempts["2"] = _attempt("a2", "other")
    affected = reassign_attempt_refs(state, "c1", "new", mark_needs_review=True)
    assert affected == 1
    assert state.attempts["1"].clips[0].clip_id == "new"
    assert state.attempts["1"].clips[1].clip_id == "other"
    assert state.attempts["1"].needs_review is True
    # Untouched attempt stays clean.
    assert state.attempts["2"].needs_review is False


def test_reassign_attempt_refs_counts_attempt_not_attemptclip():
    """An attempt that references the same clip twice should count once,
    not twice."""
    state = _state_with_clip("c1")
    state.attempts["1"] = _attempt("a1", "c1", "c1")
    affected = reassign_attempt_refs(state, "c1", "new", mark_needs_review=False)
    assert affected == 1
    assert [ac.clip_id for ac in state.attempts["1"].clips] == ["new", "new"]


def test_reassign_attempt_refs_no_mark_when_flag_false():
    state = _state_with_clip("c1")
    state.attempts["1"] = _attempt("a1", "c1")
    reassign_attempt_refs(state, "c1", "new", mark_needs_review=False)
    assert state.attempts["1"].needs_review is False


def test_mark_attempts_needs_review_for_clip_leaves_ref_dangling():
    """Spec: delete leaves the AttemptClip.clip_id pointing at the deleted
    ID (the dangling tombstone the resolver surfaces in Phase 7+)."""
    state = _state_with_clip("c1")
    state.attempts["1"] = _attempt("a1", "c1", "other")
    state.attempts["2"] = _attempt("a2", "other")

    affected = mark_attempts_needs_review_for_clip(state, "c1")
    assert affected == 1
    assert state.attempts["1"].needs_review is True
    assert state.attempts["1"].clips[0].clip_id == "c1"  # tombstone preserved
    assert state.attempts["2"].needs_review is False


# ---------- Trim clamping (the four cases) -----------------------------------


def _setup_clamp(
    *,
    new_start: float,
    new_end: float,
    trim_s: float,
    trim_e: float,
) -> ClipFarmState:
    """Build a state with one clip and one attempt referencing it. The
    base is at the NEW positions; the test passes the OLD positions to
    `clamp_attempt_trims_for_clip`."""
    state = _state_with_clip("c1", start=new_start, end=new_end)
    state.attempts["1"] = Attempt(
        project_id="p1",
        name="a",
        clips=[
            AttemptClip(
                clip_id="c1", trim_start_offset=trim_s, trim_end_offset=trim_e
            )
        ],
        created_at=_now(),
    )
    return state


def test_clamp_case1_base_start_moves_inward_clamps_start_offset():
    """Old base [10, 20], trim_s=5 → effective start 15. New base [12, 20]
    (moved inward by 2). Trim should clamp to 3 so effective start stays at 15."""
    state = _setup_clamp(new_start=12.0, new_end=20.0, trim_s=5.0, trim_e=0.0)
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 1
    ac = state.attempts["1"].clips[0]
    assert ac.trim_start_offset == pytest.approx(3.0)
    assert ac.trim_end_offset == 0.0


def test_clamp_case1_base_moves_past_effective_start_collapses_to_zero():
    """Old base [10, 20], trim_s=5 → effective start 15. New base [16, 20]
    (overshot). Trim collapses to 0 (effective start == new base start)."""
    state = _setup_clamp(new_start=16.0, new_end=20.0, trim_s=5.0, trim_e=0.0)
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 1
    ac = state.attempts["1"].clips[0]
    assert ac.trim_start_offset == 0.0


def test_clamp_case2_base_end_moves_inward_clamps_end_offset():
    """Old base [10, 20], trim_e=3 → effective end 17. New base [10, 18]
    (moved inward by 2). Trim should clamp to 1 so effective end stays at 17."""
    state = _setup_clamp(new_start=10.0, new_end=18.0, trim_s=0.0, trim_e=3.0)
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 1
    ac = state.attempts["1"].clips[0]
    assert ac.trim_end_offset == pytest.approx(1.0)
    assert ac.trim_start_offset == 0.0


def test_clamp_case3_base_moves_outward_no_change():
    """Old base [10, 20], trim_s=2, trim_e=2 → effective [12, 18]. New
    base [8, 22] (moved outward both sides). Positive trims still inside
    the new base; no clamp needed."""
    state = _setup_clamp(new_start=8.0, new_end=22.0, trim_s=2.0, trim_e=2.0)
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 0
    ac = state.attempts["1"].clips[0]
    assert ac.trim_start_offset == 2.0
    assert ac.trim_end_offset == 2.0


def test_clamp_case4_pathological_collapses_both_offsets(caplog):
    """Old base [10, 20], trim_s=4, trim_e=4 → effective [14, 16]. User
    shrinks new base to [17, 19] — start moved INWARD past effective_end.

    Step by step:
    - Case 1 fires: new_trim_s = max(0, 14 - 17) = 0 (start overshot).
    - Case 2 fires: new_trim_e = max(0, 19 - 16) = 3.
    - effective_start = 17 + 0 = 17, effective_end = 19 - 3 = 16. 17 >= 16
      → pathological collapse, both trims zeroed."""
    state = _setup_clamp(new_start=17.0, new_end=19.0, trim_s=4.0, trim_e=4.0)
    with caplog.at_level(logging.WARNING, logger="clipfarm.propagation"):
        modified = clamp_attempt_trims_for_clip(
            state, "c1", old_start=10.0, old_end=20.0
        )
    assert modified == 1
    ac = state.attempts["1"].clips[0]
    assert ac.trim_start_offset == 0.0
    assert ac.trim_end_offset == 0.0
    # A warning got emitted naming the attempt + clip.
    assert any("c1" in r.getMessage() for r in caplog.records)


def test_clamp_negative_offsets_not_touched():
    """Negative offsets extend past the base into source raw range. They
    aren't clamped against the base — only positive offsets are."""
    state = _setup_clamp(new_start=14.0, new_end=18.0, trim_s=-2.0, trim_e=-2.0)
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 0
    ac = state.attempts["1"].clips[0]
    assert ac.trim_start_offset == -2.0
    assert ac.trim_end_offset == -2.0


def test_clamp_no_attempts_is_noop():
    state = _state_with_clip("c1")
    state.clips["c1"].start_sec = 12.0
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    assert modified == 0


def test_clamp_unknown_clip_returns_zero():
    state = _state_with_clip("c1")
    assert clamp_attempt_trims_for_clip(
        state, "does-not-exist", old_start=0.0, old_end=10.0
    ) == 0


def test_clamp_multiple_attempts_referencing_same_clip():
    state = _setup_clamp(new_start=12.0, new_end=20.0, trim_s=5.0, trim_e=0.0)
    state.attempts["2"] = Attempt(
        project_id="p1",
        name="a2",
        clips=[AttemptClip(clip_id="c1", trim_start_offset=3.0)],
        created_at=_now(),
    )
    modified = clamp_attempt_trims_for_clip(
        state, "c1", old_start=10.0, old_end=20.0
    )
    # Both attempts get adjusted: a1's trim 5→3, a2's trim 3→1.
    assert modified == 2
    assert state.attempts["1"].clips[0].trim_start_offset == pytest.approx(3.0)
    assert state.attempts["2"].clips[0].trim_start_offset == pytest.approx(1.0)
