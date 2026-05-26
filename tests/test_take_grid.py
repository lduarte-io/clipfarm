"""Tests for `clipfarm/take_grid.py` — pure orchestrator over a
synthetic `ClipFarmState`. No FastAPI, no real Whisper sidecars
(the per-source `first_word_index` lookup is exercised end-to-end by
`test_routes_take_grid.py` with real sidecars on disk).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clipfarm.models import (
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Project,
    ProjectTag,
    Script,
    Source,
)
from clipfarm.take_grid import BUCKET_CATEGORIES, build_take_grid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_state(
    *,
    project_id: str = "p1",
    line_tag_ids: tuple[str, ...] = ("t1", "t2"),
    n_clips: int = 4,
) -> ClipFarmState:
    """One source, N clips, one project with N line tags (default 2)."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="src.mov", path="/src.mov", added_at=_now(), unavailable=True
    )
    for i in range(n_clips):
        state.clips[f"c{i}"] = Clip(
            source_id="1",
            start_sec=float(i * 10),
            end_sec=float(i * 10 + 5),
            transcript_text=f"clip {i} text",
            created_at=_now(),
        )
    tags: dict[str, ProjectTag] = {}
    for order_idx, tid in enumerate(line_tag_ids):
        tags[tid] = ProjectTag(
            kind="line", name=f"line {tid}", parent_id=None, order_idx=order_idx
        )
    state.projects[project_id] = Project(
        name="test project",
        brief_md="energy",
        script=Script(lines=[t.name for t in tags.values()]),
        tags=tags,
        created_at=_now(),
    )
    return state


def _add_row(
    state: ClipFarmState,
    *,
    clip_id: str,
    project_id: str = "p1",
    project_tag_id: str | None = "t1",
    category: str = "on-script",
    confidence: float = 0.8,
    stale: bool = False,
) -> None:
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id=clip_id,
            project_id=project_id,
            project_tag_id=project_tag_id,
            category=category,  # type: ignore[arg-type]
            confidence=confidence,
            stale=stale,
        )
    )


# ---------- Basic shape ------------------------------------------------------


def test_empty_project_returns_empty_lines_and_buckets():
    state = _seed_state(n_clips=3)
    view = build_take_grid(state, "p1")
    assert view.project_id == "p1"
    assert view.name == "test project"
    # Lines exist but every row has zero cards.
    assert [r.tag_id for r in view.lines] == ["t1", "t2"]
    assert all(len(r.cards) == 0 for r in view.lines)
    # Buckets present and empty.
    assert set(view.buckets.keys()) == set(BUCKET_CATEGORIES)
    assert all(len(b.cards) == 0 for b in view.buckets.values())
    # Summary.
    assert view.summary.total_tagged == 0
    assert view.summary.untagged_clips == 3
    assert view.summary.stale_clips == 0


def test_unknown_project_raises_keyerror():
    state = _seed_state()
    with pytest.raises(KeyError):
        build_take_grid(state, "p_missing")


# ---------- On-script grouping + sort order ----------------------------------


def test_on_script_cards_grouped_by_project_tag_id():
    state = _seed_state(n_clips=4)
    _add_row(state, clip_id="c0", project_tag_id="t1", confidence=0.7)
    _add_row(state, clip_id="c1", project_tag_id="t1", confidence=0.9)
    _add_row(state, clip_id="c2", project_tag_id="t2", confidence=0.5)

    view = build_take_grid(state, "p1")
    rows_by_id = {r.tag_id: r for r in view.lines}
    assert len(rows_by_id["t1"].cards) == 2
    assert len(rows_by_id["t2"].cards) == 1
    # No on-script row leaked into a bucket.
    for b in view.buckets.values():
        assert b.cards == []


def test_line_cards_sorted_confidence_desc_then_start_asc():
    state = _seed_state(n_clips=4)
    # All under t1, mixed confidence and start_sec.
    _add_row(state, clip_id="c2", project_tag_id="t1", confidence=0.5)
    _add_row(state, clip_id="c0", project_tag_id="t1", confidence=0.9)  # high, early
    _add_row(state, clip_id="c3", project_tag_id="t1", confidence=0.9)  # high, late
    _add_row(state, clip_id="c1", project_tag_id="t1", confidence=0.7)

    view = build_take_grid(state, "p1")
    t1 = next(r for r in view.lines if r.tag_id == "t1")
    # Confidence DESC then start_sec ASC.
    assert [c.clip_id for c in t1.cards] == ["c0", "c3", "c1", "c2"]


def test_bucket_cards_sorted_by_start_sec_asc_only():
    state = _seed_state(n_clips=3)
    # Mixed confidence in the bucket — must NOT be a sort key.
    _add_row(
        state, clip_id="c2", project_tag_id=None,
        category="standalone-idea", confidence=0.1,  # late + low confidence
    )
    _add_row(
        state, clip_id="c0", project_tag_id=None,
        category="standalone-idea", confidence=0.99,  # early + high
    )
    _add_row(
        state, clip_id="c1", project_tag_id=None,
        category="standalone-idea", confidence=0.4,  # middle
    )

    view = build_take_grid(state, "p1")
    bucket = view.buckets["standalone-idea"]
    assert [c.clip_id for c in bucket.cards] == ["c0", "c1", "c2"]


# ---------- Buckets ----------------------------------------------------------


def test_buckets_populated_by_category():
    state = _seed_state(n_clips=5)
    _add_row(state, clip_id="c0", project_tag_id=None, category="off-topic")
    _add_row(state, clip_id="c1", project_tag_id=None, category="fragment")
    _add_row(state, clip_id="c2", project_tag_id=None, category="standalone-idea")
    _add_row(
        state, clip_id="c3", project_tag_id=None, category="related-but-different"
    )

    view = build_take_grid(state, "p1")
    assert {c.clip_id for c in view.buckets["off-topic"].cards} == {"c0"}
    assert {c.clip_id for c in view.buckets["fragment"].cards} == {"c1"}
    assert {c.clip_id for c in view.buckets["standalone-idea"].cards} == {"c2"}
    assert {c.clip_id for c in view.buckets["related-but-different"].cards} == {
        "c3"
    }


def test_lines_appear_in_order_idx_order():
    """Even when tags are inserted in non-order_idx order, the view sorts
    them by `order_idx`. Important for the script-line view to read top-
    to-bottom in script order."""
    state = _seed_state()
    # Add a third line tag with a smaller order_idx than the existing t2.
    state.projects["p1"].tags["t3"] = ProjectTag(
        kind="line", name="line t3 (should come first)", order_idx=-1
    )
    view = build_take_grid(state, "p1")
    assert [r.tag_id for r in view.lines] == ["t3", "t1", "t2"]


# ---------- Summary math -----------------------------------------------------


def test_summary_counts_untagged_stale_total():
    state = _seed_state(n_clips=5)
    # c0: one fresh row
    _add_row(state, clip_id="c0", project_tag_id="t1", confidence=0.8)
    # c1: one stale row only
    _add_row(state, clip_id="c1", project_tag_id="t1", stale=True)
    # c2: stale + fresh (counts in BOTH stale_clips and total_tagged)
    _add_row(
        state, clip_id="c2", project_tag_id="t1",
        category="on-script", stale=True,
    )
    _add_row(
        state, clip_id="c2", project_tag_id=None,
        category="standalone-idea", stale=False,
    )
    # c3, c4: no rows → untagged.

    view = build_take_grid(state, "p1")
    assert view.summary.total_tagged == 3   # c0, c1, c2
    assert view.summary.stale_clips == 2    # c1, c2
    assert view.summary.untagged_clips == 2  # c3, c4


def test_summary_disjointness_not_required():
    """A clip with one stale + one fresh row counts toward BOTH
    `total_tagged` AND `stale_clips`. Documents the spec-locked decision
    in PHASES.md."""
    state = _seed_state(n_clips=1)
    _add_row(state, clip_id="c0", project_tag_id="t1", stale=True)
    _add_row(
        state, clip_id="c0", project_tag_id=None,
        category="off-topic", stale=False,
    )
    view = build_take_grid(state, "p1")
    assert view.summary.total_tagged == 1
    assert view.summary.stale_clips == 1
    assert view.summary.untagged_clips == 0


# ---------- Stale surfacing --------------------------------------------------


def test_stale_cards_still_appear_in_grid_with_flag_set():
    """Stale rows are surfaced (with the flag), not filtered out — the
    Brief page's `Tag clips` action is the user-driven way to clear
    them. Spec → 'Stale handling: surface, don't filter'."""
    state = _seed_state(n_clips=2)
    _add_row(state, clip_id="c0", project_tag_id="t1", stale=True)
    _add_row(state, clip_id="c1", project_tag_id="t1", stale=False)
    view = build_take_grid(state, "p1")
    t1 = next(r for r in view.lines if r.tag_id == "t1")
    cards_by_id = {c.clip_id: c for c in t1.cards}
    assert cards_by_id["c0"].stale is True
    assert cards_by_id["c1"].stale is False


# ---------- Multi-project isolation ------------------------------------------


def test_multi_project_isolation():
    """`build_take_grid('p1')` must not surface clips tagged for `p2`."""
    state = _seed_state(n_clips=3, project_id="p1")
    state.projects["p2"] = Project(
        name="other", brief_md="", script=Script(lines=["x"]),
        tags={"u1": ProjectTag(kind="line", name="x", order_idx=0)},
        created_at=_now(),
    )
    # p2 has rows; p1 does not.
    _add_row(state, clip_id="c0", project_id="p2", project_tag_id="u1")
    _add_row(state, clip_id="c1", project_id="p2", project_tag_id="u1")

    view = build_take_grid(state, "p1")
    # No cards visible on p1.
    assert all(len(r.cards) == 0 for r in view.lines)
    assert all(len(b.cards) == 0 for b in view.buckets.values())
    assert view.summary.total_tagged == 0
    assert view.summary.untagged_clips == 3
    # And the reverse — p2's grid sees its rows.
    view_p2 = build_take_grid(state, "p2")
    u1_cards = next(r for r in view_p2.lines if r.tag_id == "u1").cards
    assert {c.clip_id for c in u1_cards} == {"c0", "c1"}


# ---------- Filename resolution + footage-only fallback ----------------------


def test_card_carries_filename_from_source():
    state = _seed_state(n_clips=1)
    state.sources["1"].filename = "btc.0.4.mov"
    _add_row(state, clip_id="c0", project_tag_id="t1")
    view = build_take_grid(state, "p1")
    card = next(r for r in view.lines if r.tag_id == "t1").cards[0]
    assert card.filename == "btc.0.4.mov"


def test_first_word_index_none_when_no_transcript():
    """Footage-only sources (no sidecar) yield `first_word_index=None`.
    The frontend treats None as 'navigate to source without word focus'."""
    state = _seed_state(n_clips=1)
    # Source has no transcript_path → load_transcript_for_source returns
    # None, lookup yields None.
    assert state.sources["1"].transcript_path is None
    _add_row(state, clip_id="c0", project_tag_id="t1")
    view = build_take_grid(state, "p1")
    card = next(r for r in view.lines if r.tag_id == "t1").cards[0]
    assert card.first_word_index is None


# ---------- Defensive: orphan tag row ----------------------------------------


def test_tag_row_pointing_at_missing_clip_is_skipped_silently():
    """A tag row referencing a clip not in `state.clips` shouldn't crash
    the grid build. Boundary correction cleans these up on delete, but
    defense in depth matters."""
    state = _seed_state(n_clips=1)
    _add_row(state, clip_id="c_phantom", project_tag_id="t1")
    view = build_take_grid(state, "p1")
    # Grid built without crashing; phantom row didn't surface anywhere.
    assert all(c.clip_id != "c_phantom" for r in view.lines for c in r.cards)
    for b in view.buckets.values():
        assert all(c.clip_id != "c_phantom" for c in b.cards)
