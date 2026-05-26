"""Tests for `clipfarm/strategies.py` — 8 pure-function strategies."""
from __future__ import annotations

from datetime import datetime, timezone

from clipfarm.models import (
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Project,
    ProjectTag,
    Script,
    Source,
)
from clipfarm.strategies import (
    DIAGNOSTIC_CAP,
    ad_libbed,
    best_per_line_in_script_order,
    energy_shift,
    longest_contiguous_take,
    near_one_take,
    shortest_complete,
    skipped_line,
    started_with_line,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_with_project(
    *,
    line_count: int = 3,
    sources: list[str] | None = None,
) -> ClipFarmState:
    """Build a state with one project + N line tags + listed sources.
    No clips yet — tests add them."""
    if sources is None:
        sources = ["1"]
    state = ClipFarmState()
    for sid in sources:
        state.sources[sid] = Source(
            filename=f"src{sid}.mov",
            path=f"/src{sid}.mov",
            added_at=_now(),
            unavailable=True,
        )
    tags = {
        f"L{i}": ProjectTag(
            kind="line", name=f"line {i}", parent_id=None, order_idx=i
        )
        for i in range(line_count)
    }
    state.projects["p1"] = Project(
        name="test project",
        brief_md="energy",
        script=Script(lines=[t.name for t in tags.values()]),
        tags=tags,
        created_at=_now(),
    )
    return state


def _add_clip(
    state: ClipFarmState,
    cid: str,
    source_id: str,
    start: float,
    end: float,
) -> None:
    state.clips[cid] = Clip(
        source_id=source_id, start_sec=start, end_sec=end, created_at=_now()
    )


def _tag(
    state: ClipFarmState,
    cid: str,
    *,
    category: str = "on-script",
    line_tag_id: str | None = "L0",
    confidence: float = 0.8,
    project_id: str = "p1",
) -> None:
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id=cid,
            project_id=project_id,
            project_tag_id=line_tag_id,
            category=category,  # type: ignore[arg-type]
            confidence=confidence,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# best_per_line_in_script_order (3 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_best_per_line_picks_highest_confidence_per_line():
    state = _state_with_project(line_count=2)
    _add_clip(state, "c0", "1", 0, 5)  # L0 low confidence
    _add_clip(state, "c1", "1", 6, 10)  # L0 high
    _add_clip(state, "c2", "1", 11, 15)  # L1 only
    _tag(state, "c0", line_tag_id="L0", confidence=0.4)
    _tag(state, "c1", line_tag_id="L0", confidence=0.9)
    _tag(state, "c2", line_tag_id="L1", confidence=0.7)

    [result] = best_per_line_in_script_order(state, "p1")
    assert result.strategy_id == "best_per_line_in_script_order"
    assert result.premade_bucket == "best"
    # c1 (high conf for L0) + c2 (L1), in script order.
    assert [ac.clip_id for ac in result.clips] == ["c1", "c2"]


def test_best_per_line_skips_lines_with_no_matches_and_marks_coverage():
    """A 3-line script where line 1 has no on-script match → 2-clip
    attempt. Name hint includes the coverage suffix."""
    state = _state_with_project(line_count=3)
    _add_clip(state, "c0", "1", 0, 5)
    _add_clip(state, "c2", "1", 10, 15)
    _tag(state, "c0", line_tag_id="L0")
    _tag(state, "c2", line_tag_id="L2")
    # L1 has no on-script clip.

    [result] = best_per_line_in_script_order(state, "p1")
    assert [ac.clip_id for ac in result.clips] == ["c0", "c2"]
    assert "2 of 3" in result.name_hint


def test_best_per_line_returns_empty_when_no_on_script_tags():
    state = _state_with_project()
    _add_clip(state, "c0", "1", 0, 5)
    _tag(state, "c0", category="off-topic", line_tag_id=None)
    assert best_per_line_in_script_order(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# longest_contiguous_take (3 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_longest_contiguous_picks_take_with_most_line_coverage():
    state = _state_with_project(line_count=3)
    # Source 1: take A covers L0, L1 (2 lines, fragments allowed but none here)
    _add_clip(state, "c0", "1", 0, 5)
    _add_clip(state, "c1", "1", 5, 10)
    _tag(state, "c0", line_tag_id="L0")
    _tag(state, "c1", line_tag_id="L1")
    # Off-topic clip breaks the take.
    _add_clip(state, "c2", "1", 11, 12)
    _tag(state, "c2", category="off-topic", line_tag_id=None)
    # Source 1: take B covers L0, L1, L2 (3 lines)
    _add_clip(state, "c3", "1", 20, 25)
    _add_clip(state, "c4", "1", 26, 30)
    _add_clip(state, "c5", "1", 31, 35)
    _tag(state, "c3", line_tag_id="L0")
    _tag(state, "c4", line_tag_id="L1")
    _tag(state, "c5", line_tag_id="L2")

    [result] = longest_contiguous_take(state, "p1")
    # Take B wins.
    assert [ac.clip_id for ac in result.clips] == ["c3", "c4", "c5"]


def test_longest_contiguous_tiebreaks_on_runtime():
    """Two takes covering the same lines; the longer one wins."""
    state = _state_with_project(line_count=2)
    # Take A: 2s + 2s, covers L0+L1.
    _add_clip(state, "a0", "1", 0, 2)
    _add_clip(state, "a1", "1", 2, 4)
    _tag(state, "a0", line_tag_id="L0")
    _tag(state, "a1", line_tag_id="L1")
    # Break.
    _add_clip(state, "ot", "1", 5, 6)
    _tag(state, "ot", category="off-topic", line_tag_id=None)
    # Take B: 5s + 5s, also covers L0+L1.
    _add_clip(state, "b0", "1", 10, 15)
    _add_clip(state, "b1", "15", 15, 20) if False else None  # placeholder
    _add_clip(state, "b1", "1", 15, 20)
    _tag(state, "b0", line_tag_id="L0")
    _tag(state, "b1", line_tag_id="L1")

    [result] = longest_contiguous_take(state, "p1")
    assert [ac.clip_id for ac in result.clips] == ["b0", "b1"]


def test_longest_contiguous_returns_empty_when_no_takes():
    state = _state_with_project()
    # Only off-topic and fragment — no take qualifies.
    _add_clip(state, "c0", "1", 0, 5)
    _tag(state, "c0", category="off-topic", line_tag_id=None)
    assert longest_contiguous_take(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# near_one_take (3 tests) — the contested strategy from plan review #1
# ─────────────────────────────────────────────────────────────────────────────


def test_near_one_take_returns_separate_attempts_not_one_splice():
    """The plan-review fix: each qualifying take is its OWN attempt,
    not concatenated together. Continuity ≈ 1.0 per attempt."""
    state = _state_with_project(line_count=2)
    # Take A: covers L0+L1, contiguous in source 1.
    _add_clip(state, "a0", "1", 0, 5)
    _add_clip(state, "a1", "1", 5, 10)
    _tag(state, "a0", line_tag_id="L0")
    _tag(state, "a1", line_tag_id="L1")
    # Break (off-topic).
    _add_clip(state, "ot", "1", 11, 12)
    _tag(state, "ot", category="off-topic", line_tag_id=None)
    # Take B: also covers L0+L1, separate.
    _add_clip(state, "b0", "1", 20, 25)
    _add_clip(state, "b1", "1", 25, 30)
    _tag(state, "b0", line_tag_id="L0")
    _tag(state, "b1", line_tag_id="L1")

    results = near_one_take(state, "p1")
    assert len(results) == 2
    # Each result is a single take, ordered by score (both cover same
    # lines, longer runtime wins — actually equal here, so first found).
    assert all(r.strategy_id == "near_one_take" for r in results)
    assert all(r.premade_bucket == "best" for r in results)
    # Two separate clip lists.
    clip_sets = [tuple(ac.clip_id for ac in r.clips) for r in results]
    assert set(clip_sets) == {("a0", "a1"), ("b0", "b1")}


def test_near_one_take_caps_at_three_results():
    """If 5 takes qualify, only the top 3 are returned."""
    state = _state_with_project(line_count=2)
    for i in range(5):
        offset = i * 100.0
        _add_clip(state, f"t{i}_a", "1", offset, offset + 5)
        _add_clip(state, f"t{i}_b", "1", offset + 5, offset + 10)
        _tag(state, f"t{i}_a", line_tag_id="L0")
        _tag(state, f"t{i}_b", line_tag_id="L1")
        # Break between takes.
        _add_clip(state, f"break{i}", "1", offset + 11, offset + 12)
        _tag(state, f"break{i}", category="off-topic", line_tag_id=None)

    results = near_one_take(state, "p1")
    assert len(results) == 3


def test_near_one_take_returns_empty_when_coverage_below_threshold():
    """A take that covers only 1 of 5 lines (< 70%) doesn't qualify."""
    state = _state_with_project(line_count=5)
    _add_clip(state, "c0", "1", 0, 5)
    _tag(state, "c0", line_tag_id="L0")
    # 1/5 = 20% coverage, below 70% threshold.
    assert near_one_take(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# shortest_complete (2 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_shortest_complete_picks_shortest_per_line():
    state = _state_with_project(line_count=2)
    # L0: two candidates, c0 is shorter.
    _add_clip(state, "c0", "1", 0, 2)      # 2s
    _add_clip(state, "c1", "1", 10, 20)    # 10s
    _tag(state, "c0", line_tag_id="L0", confidence=0.5)
    _tag(state, "c1", line_tag_id="L0", confidence=0.9)  # higher conf but longer
    # L1: one candidate.
    _add_clip(state, "c2", "1", 30, 35)
    _tag(state, "c2", line_tag_id="L1")

    [result] = shortest_complete(state, "p1")
    # Picks c0 (shortest) despite c1 having higher confidence.
    assert [ac.clip_id for ac in result.clips] == ["c0", "c2"]


def test_shortest_complete_returns_empty_when_no_on_script_tags():
    state = _state_with_project()
    _add_clip(state, "c0", "1", 0, 5)
    _tag(state, "c0", category="fragment", line_tag_id=None)
    assert shortest_complete(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# energy_shift (2 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_energy_shift_falls_back_to_confidence_when_no_transcripts():
    """All sources have transcript_path=None → wps unavailable → ranking
    falls back to confidence."""
    state = _state_with_project(line_count=2)
    _add_clip(state, "c0", "1", 0, 5)
    _add_clip(state, "c1", "1", 6, 10)
    _tag(state, "c0", line_tag_id="L0", confidence=0.3)
    _tag(state, "c1", line_tag_id="L0", confidence=0.9)
    _add_clip(state, "c2", "1", 11, 15)
    _tag(state, "c2", line_tag_id="L1", confidence=0.5)

    [result] = energy_shift(state, "p1")
    # No transcripts → fallback to confidence → c1 wins for L0.
    assert [ac.clip_id for ac in result.clips] == ["c1", "c2"]


def test_energy_shift_returns_empty_when_no_on_script_tags():
    state = _state_with_project()
    assert energy_shift(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# started_with_line (3 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_started_with_line_groups_takes_by_opening_line():
    """Two takes started with L0, one with L1. Only the L0 group has
    ≥ 2 takes and qualifies."""
    state = _state_with_project(line_count=2)
    # Take A starts with L0, covers L0+L1.
    _add_clip(state, "a0", "1", 0, 5)
    _add_clip(state, "a1", "1", 5, 10)
    _tag(state, "a0", line_tag_id="L0")
    _tag(state, "a1", line_tag_id="L1")
    # Break.
    _add_clip(state, "ot1", "1", 11, 12)
    _tag(state, "ot1", category="off-topic", line_tag_id=None)
    # Take B starts with L0, covers L0+L1.
    _add_clip(state, "b0", "1", 20, 25)
    _add_clip(state, "b1", "1", 25, 30)
    _tag(state, "b0", line_tag_id="L0")
    _tag(state, "b1", line_tag_id="L1")
    # Break.
    _add_clip(state, "ot2", "1", 31, 32)
    _tag(state, "ot2", category="off-topic", line_tag_id=None)
    # Take C starts with L1.
    _add_clip(state, "c0", "1", 40, 45)
    _add_clip(state, "c1", "1", 45, 50)
    _tag(state, "c0", line_tag_id="L1")
    _tag(state, "c1", line_tag_id="L0")

    results = started_with_line(state, "p1")
    # Only the L0 group (2 takes) qualifies — L1 group has 1 take.
    assert len(results) == 1
    assert results[0].premade_bucket == "diagnostic"
    assert results[0].strategy_id == "started_with_line"
    # Combined clip list includes both takes' clips.
    clip_ids = {ac.clip_id for ac in results[0].clips}
    assert {"a0", "a1", "b0", "b1"}.issubset(clip_ids)


def test_started_with_line_skips_singleton_groups():
    """A group with only 1 take doesn't qualify."""
    state = _state_with_project(line_count=2)
    _add_clip(state, "a0", "1", 0, 5)
    _add_clip(state, "a1", "1", 5, 10)
    _tag(state, "a0", line_tag_id="L0")
    _tag(state, "a1", line_tag_id="L1")
    # No second take started-with-L0.
    assert started_with_line(state, "p1") == []


def test_started_with_line_caps_at_three_groups():
    """If 4 different opening lines each had ≥ 2 takes, only the top 3
    by group size are kept."""
    state = _state_with_project(line_count=4)
    # For each line 0..3, create 2 takes that start with that line.
    base = 0
    for line in range(4):
        for take in range(2):
            cid_a = f"L{line}_T{take}_a"
            cid_b = f"L{line}_T{take}_b"
            _add_clip(state, cid_a, "1", base, base + 5)
            _add_clip(state, cid_b, "1", base + 5, base + 10)
            _tag(state, cid_a, line_tag_id=f"L{line}")
            # Second clip uses a different line so the take has 2 distinct lines.
            other_line = (line + 1) % 4
            _tag(state, cid_b, line_tag_id=f"L{other_line}")
            # Break.
            _add_clip(state, f"break_{line}_{take}", "1", base + 11, base + 12)
            _tag(
                state, f"break_{line}_{take}", category="off-topic",
                line_tag_id=None,
            )
            base += 20

    results = started_with_line(state, "p1")
    assert len(results) == DIAGNOSTIC_CAP == 3


# ─────────────────────────────────────────────────────────────────────────────
# skipped_line (2 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_skipped_line_finds_takes_that_skipped_a_line():
    """A 3-line script. Take A covers L0+L2 (skipped L1). Take B covers
    L0+L1+L2 (no skip). Strategy should surface L1-skipped."""
    state = _state_with_project(line_count=3)
    # Take A skips L1.
    _add_clip(state, "a0", "1", 0, 5)
    _add_clip(state, "a1", "1", 5, 10)
    _tag(state, "a0", line_tag_id="L0")
    _tag(state, "a1", line_tag_id="L2")
    # Break.
    _add_clip(state, "ot", "1", 11, 12)
    _tag(state, "ot", category="off-topic", line_tag_id=None)
    # Take B covers all.
    _add_clip(state, "b0", "1", 20, 25)
    _add_clip(state, "b1", "1", 25, 30)
    _add_clip(state, "b2", "1", 30, 35)
    _tag(state, "b0", line_tag_id="L0")
    _tag(state, "b1", line_tag_id="L1")
    _tag(state, "b2", line_tag_id="L2")

    results = skipped_line(state, "p1")
    # L1 was skipped by 1 take (A); L0 and L2 weren't skipped.
    assert len(results) == 1
    assert results[0].premade_bucket == "diagnostic"
    # Take A's clips are surfaced.
    assert {ac.clip_id for ac in results[0].clips} == {"a0", "a1"}


def test_skipped_line_returns_empty_for_single_line_script():
    """< 2 script lines → 'skipped' isn't meaningful."""
    state = _state_with_project(line_count=1)
    _add_clip(state, "c0", "1", 0, 5)
    _tag(state, "c0", line_tag_id="L0")
    assert skipped_line(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# ad_libbed (2 tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_ad_libbed_finds_takes_with_ad_libs():
    """A take with 2 standalone-idea clips inside its window qualifies."""
    state = _state_with_project(line_count=2)
    # The take.
    _add_clip(state, "on0", "1", 0, 5)
    _add_clip(state, "on1", "1", 5, 10)
    _tag(state, "on0", line_tag_id="L0")
    _tag(state, "on1", line_tag_id="L1")
    # Ad-libs inside the take's window.
    _add_clip(state, "ad0", "1", 4, 4.5)
    _add_clip(state, "ad1", "1", 7, 7.5)
    _tag(state, "ad0", category="standalone-idea", line_tag_id=None)
    _tag(state, "ad1", category="related-but-different", line_tag_id=None)

    results = ad_libbed(state, "p1")
    assert len(results) == 1
    assert results[0].premade_bucket == "diagnostic"
    # All four clips present in source-time order.
    clip_ids = [ac.clip_id for ac in results[0].clips]
    assert "ad0" in clip_ids
    assert "ad1" in clip_ids
    assert "on0" in clip_ids
    assert "on1" in clip_ids


def test_ad_libbed_returns_empty_when_no_takes_have_enough_ad_libs():
    """A take with only 1 ad-lib doesn't qualify (threshold = 2)."""
    state = _state_with_project()
    _add_clip(state, "on0", "1", 0, 5)
    _tag(state, "on0", line_tag_id="L0")
    _add_clip(state, "ad0", "1", 3, 3.5)
    _tag(state, "ad0", category="standalone-idea", line_tag_id=None)
    assert ad_libbed(state, "p1") == []


# ─────────────────────────────────────────────────────────────────────────────
# Defensive / cross-cutting
# ─────────────────────────────────────────────────────────────────────────────


def test_all_strategies_return_empty_on_empty_state():
    """A project with no tagged clips at all → every strategy returns []."""
    state = _state_with_project()
    from clipfarm.strategies import ALL_STRATEGIES
    for strat in ALL_STRATEGIES:
        result = strat(state, "p1")
        assert result == [], f"{strat.__name__} should return [] on empty state, got {result}"
