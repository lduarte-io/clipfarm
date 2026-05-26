"""Tests for `clipfarm/premade.py` — end-to-end orchestrator with
synthetic state + fake LLM client."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clipfarm.models import (
    Attempt,
    AttemptClip,
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Project,
    ProjectTag,
    Script,
    Source,
)
from clipfarm.premade import generate_premade_attempts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_realistic(line_count: int = 3) -> ClipFarmState:
    """A state rich enough to exercise multiple strategies."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="src.mov",
        path="/src.mov",
        added_at=_now(),
        unavailable=True,
    )
    tags = {
        f"L{i}": ProjectTag(kind="line", name=f"line {i}", order_idx=i)
        for i in range(line_count)
    }
    state.projects["p1"] = Project(
        name="t", brief_md="", script=Script(lines=[t.name for t in tags.values()]),
        tags=tags, created_at=_now(),
    )
    # Take 1: full coverage, all 3 lines.
    for i in range(line_count):
        cid = f"t1_L{i}"
        state.clips[cid] = Clip(
            source_id="1", start_sec=i * 5.0, end_sec=(i + 1) * 5.0,
            transcript_text=f"line {i} take 1", created_at=_now(),
        )
        state.clip_project_tags.append(ClipProjectTag(
            clip_id=cid, project_id="p1", project_tag_id=f"L{i}",
            category="on-script", confidence=0.8,
        ))
    # Off-topic between takes.
    state.clips["ot"] = Clip(
        source_id="1", start_sec=20, end_sec=22, created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="ot", project_id="p1", project_tag_id=None,
        category="off-topic", confidence=0.5,
    ))
    # Take 2: also full coverage, different confidences + durations.
    for i in range(line_count):
        cid = f"t2_L{i}"
        state.clips[cid] = Clip(
            source_id="1", start_sec=30 + i * 3.0, end_sec=30 + (i + 1) * 3.0,
            transcript_text=f"line {i} take 2", created_at=_now(),
        )
        state.clip_project_tags.append(ClipProjectTag(
            clip_id=cid, project_id="p1", project_tag_id=f"L{i}",
            category="on-script", confidence=0.95,
        ))
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Happy path + bucket population
# ─────────────────────────────────────────────────────────────────────────────


def test_generate_produces_attempts_with_correct_buckets():
    state = _seed_realistic()
    result = generate_premade_attempts(state, "p1", llm_client=None)
    assert result.generated_count > 0
    assert result.mutated is True

    # Every new attempt is on the right project + has source="ai-premade".
    for aid in result.new_attempt_ids:
        att = state.attempts[aid]
        assert att.project_id == "p1"
        assert att.source == "ai-premade"
        assert att.premade_bucket in ("best", "diagnostic")
        assert att.continuity_score is not None
        assert 0.0 <= att.continuity_score <= 1.0

    # At least one best-plausible attempt.
    buckets = {state.attempts[aid].premade_bucket for aid in result.new_attempt_ids}
    assert "best" in buckets


def test_naming_source_canned_when_no_llm_client():
    state = _seed_realistic()
    result = generate_premade_attempts(state, "p1", llm_client=None)
    assert result.naming_source == "canned"
    # Names came from STRATEGY_CANNED_NAMES.
    from clipfarm.strategies import STRATEGY_CANNED_NAMES
    canned_values = set(STRATEGY_CANNED_NAMES.values())
    for aid in result.new_attempt_ids:
        assert state.attempts[aid].name in canned_values


def test_naming_source_llm_when_llm_returns_valid_names():
    state = _seed_realistic()
    # Fake LLM returns a generic-but-valid name for every input.
    def fake(messages, schema):
        n = schema["properties"]["names"]["maxItems"]
        return {"names": [f"generated name {i}" for i in range(n)]}
    result = generate_premade_attempts(state, "p1", llm_client=fake)
    assert result.naming_source == "llm"
    assert state.attempts[result.new_attempt_ids[0]].name.startswith("generated name")


# ─────────────────────────────────────────────────────────────────────────────
# Replace-existing behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_replace_drops_existing_ai_premade_keeps_hand_built_and_forks():
    state = _seed_realistic()
    # Pre-populate: 1 ai-premade (should be dropped), 1 hand-built
    # (should survive), 1 fork (should survive).
    now = _now()
    state.attempts["100"] = Attempt(
        project_id="p1", name="old ai premade", source="ai-premade",
        premade_bucket="best",
        clips=[AttemptClip(clip_id="t1_L0")], created_at=now,
    )
    state.attempts["101"] = Attempt(
        project_id="p1", name="hand-built", source="hand-built",
        clips=[AttemptClip(clip_id="t1_L0")], created_at=now,
    )
    state.attempts["102"] = Attempt(
        project_id="p1", name="fork", source="fork",
        clips=[AttemptClip(clip_id="t1_L0")], created_at=now,
    )

    result = generate_premade_attempts(state, "p1", llm_client=None)
    assert result.replaced_count == 1
    # 101 + 102 survive.
    assert "101" in state.attempts
    assert "102" in state.attempts
    # 100 gone.
    assert "100" not in state.attempts


def test_replace_existing_false_appends_without_dropping():
    state = _seed_realistic()
    state.attempts["100"] = Attempt(
        project_id="p1", name="prev", source="ai-premade",
        premade_bucket="best", clips=[AttemptClip(clip_id="t1_L0")],
        created_at=_now(),
    )
    result = generate_premade_attempts(
        state, "p1", llm_client=None, replace_existing=False
    )
    assert result.replaced_count == 0
    assert "100" in state.attempts  # original survives


# ─────────────────────────────────────────────────────────────────────────────
# Error paths
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_project_raises():
    state = _seed_realistic()
    with pytest.raises(KeyError):
        generate_premade_attempts(state, "missing", llm_client=None)


def test_no_on_script_tags_raises():
    state = ClipFarmState()
    state.projects["p1"] = Project(
        name="empty", script=Script(lines=["x"]),
        tags={"L0": ProjectTag(kind="line", name="x", order_idx=0)},
        created_at=_now(),
    )
    with pytest.raises(ValueError, match="no on-script tag rows"):
        generate_premade_attempts(state, "p1", llm_client=None)


# ─────────────────────────────────────────────────────────────────────────────
# Dedup
# ─────────────────────────────────────────────────────────────────────────────


def test_dedup_drops_strategies_with_identical_clip_lists():
    """A 1-clip project: best_per_line and shortest_complete will
    produce identical results (the same single clip per line). The
    second should drop, leaving one entry from that pair."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="1", start_sec=0, end_sec=5, transcript_text="hi",
        created_at=_now(),
    )
    state.projects["p1"] = Project(
        name="x", script=Script(lines=["one"]),
        tags={"L0": ProjectTag(kind="line", name="one", order_idx=0)},
        created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="c0", project_id="p1", project_tag_id="L0",
        category="on-script", confidence=0.9,
    ))
    result = generate_premade_attempts(state, "p1", llm_client=None)
    # best_per_line wins (first in strategy order); shortest_complete drops.
    clip_lists = [
        tuple(ac.clip_id for ac in state.attempts[aid].clips)
        for aid in result.new_attempt_ids
    ]
    # No duplicates.
    assert len(clip_lists) == len(set(clip_lists))


# ─────────────────────────────────────────────────────────────────────────────
# _next_attempt_id allocation
# ─────────────────────────────────────────────────────────────────────────────


def test_next_attempt_id_does_not_collide_across_two_runs():
    """Two consecutive runs with replace_existing=False generate fresh
    IDs without collision. Direct test of the _next_attempt_id pattern."""
    state = _seed_realistic()
    first = generate_premade_attempts(
        state, "p1", llm_client=None, replace_existing=False
    )
    second = generate_premade_attempts(
        state, "p1", llm_client=None, replace_existing=False
    )
    all_ids = first.new_attempt_ids + second.new_attempt_ids
    assert len(all_ids) == len(set(all_ids))


def test_next_attempt_id_skips_existing_higher_ids():
    """If state already contains attempt id '999', the next allocation
    is '1000', not '1'."""
    state = _seed_realistic()
    state.attempts["999"] = Attempt(
        project_id="p1", name="pre-existing", source="hand-built",
        clips=[AttemptClip(clip_id="t1_L0")], created_at=_now(),
    )
    result = generate_premade_attempts(state, "p1", llm_client=None)
    new_ids = [int(aid) for aid in result.new_attempt_ids]
    assert all(aid > 999 for aid in new_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Empty-result path (200 OK with reason — plan-review item #3)
# ─────────────────────────────────────────────────────────────────────────────


def test_zero_result_returns_reason_not_exception():
    """A project with on-script tags but degenerate clips (zero-runtime)
    produces zero attempts — orchestrator returns generated_count=0
    with a reason string, NOT a raised exception."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True,
    )
    # Zero-runtime clip — continuity_score will reject it.
    state.clips["c0"] = Clip(
        source_id="1", start_sec=5.0, end_sec=5.0,  # zero duration
        transcript_text="", created_at=_now(),
    )
    state.projects["p1"] = Project(
        name="x", script=Script(lines=["one"]),
        tags={"L0": ProjectTag(kind="line", name="one", order_idx=0)},
        created_at=_now(),
    )
    state.clip_project_tags.append(ClipProjectTag(
        clip_id="c0", project_id="p1", project_tag_id="L0",
        category="on-script", confidence=0.9,
    ))
    result = generate_premade_attempts(state, "p1", llm_client=None)
    assert result.generated_count == 0
    assert result.mutated is False
    assert result.reason != ""
