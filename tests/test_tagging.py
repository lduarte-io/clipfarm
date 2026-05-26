"""Tests for `clipfarm/tagging.py` — synthetic state + fake LLM client."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

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
from clipfarm.tagging import (
    MAX_BATCH_SIZE,
    BatchFailure,
    TaggingResult,
    tag_project,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_with_project(
    *,
    project_id: str = "p1",
    n_clips: int = 5,
    script_lines: list[str] | None = None,
) -> ClipFarmState:
    """Build a state with one source, N clips, and one project with a
    script + one section + one ad-hoc tag."""
    state = ClipFarmState()
    state.sources["1"] = Source(
        filename="src.mov", path="/src.mov", added_at=_now(), unavailable=True
    )
    for i in range(n_clips):
        cid = f"c{i}"
        state.clips[cid] = Clip(
            source_id="1",
            start_sec=float(i),
            end_sec=float(i + 1),
            transcript_text=f"clip {i} transcript text",
            created_at=_now(),
        )
    state.projects[project_id] = Project(
        name="test project",
        brief_md="energy, accessible",
        script=Script(lines=script_lines or ["intro line", "body line"]),
        tags={
            "t1": ProjectTag(kind="line", name="intro line", parent_id=None, order_idx=0),
            "t2": ProjectTag(kind="line", name="body line", parent_id=None, order_idx=1),
            "t3": ProjectTag(kind="section", name="the hook", parent_id=None, order_idx=0),
            "t4": ProjectTag(kind="tag", name="hook", parent_id=None, order_idx=0),
        },
        created_at=_now(),
    )
    return state


def _fake_client(returns: list[Optional[dict[str, Any]]]) -> Callable:
    """Build a fake LLM client that returns each item in `returns` per
    successive call. After the list is exhausted, returns None."""
    iterator = iter(returns)

    def client(messages, schema):
        try:
            return next(iterator)
        except StopIteration:
            return None

    return client


def _row(
    cid: str,
    *,
    line_tag_id: Optional[str] = "t1",
    category: str = "on-script",
    confidence: float = 0.8,
):
    return {
        "clip_id": cid,
        "line_tag_id": line_tag_id,
        "section_tag_id": None,
        "category": category,
        "confidence": confidence,
    }


# ---------- Happy path -------------------------------------------------------


def test_happy_path_tags_all_clips_in_one_batch():
    state = _state_with_project(n_clips=3)
    client = _fake_client([{"results": [_row("c0"), _row("c1"), _row("c2")]}])
    result = tag_project(state, "p1", llm_client=client, batch_size=10)
    assert result.batches == 1
    assert result.clips_tagged == 3
    assert result.clips_skipped == 0
    assert len(state.clip_project_tags) == 3
    assert all(r.project_id == "p1" for r in state.clip_project_tags)
    assert all(r.source == "ai" for r in state.clip_project_tags)
    assert all(r.stale is False for r in state.clip_project_tags)


def test_batches_split_at_batch_size():
    """23 clips, batch_size=10 → batches of 10/10/3 = 3 batches."""
    state = _state_with_project(n_clips=23)
    canned = [
        {"results": [_row(f"c{i}") for i in range(0, 10)]},
        {"results": [_row(f"c{i}") for i in range(10, 20)]},
        {"results": [_row(f"c{i}") for i in range(20, 23)]},
    ]
    result = tag_project(state, "p1", llm_client=_fake_client(canned), batch_size=10)
    assert result.batches == 3
    assert result.clips_tagged == 23


# ---------- Idempotency / stale handling ------------------------------------


def test_idempotency_already_tagged_clips_skipped():
    state = _state_with_project(n_clips=3)
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c0", project_id="p1", project_tag_id="t1",
            category="on-script", stale=False,
        )
    )
    client = _fake_client([{"results": [_row("c1"), _row("c2")]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.clips_skipped == 1
    assert result.clips_tagged == 2
    # The original row is still there.
    assert any(
        r.clip_id == "c0" and r.project_tag_id == "t1"
        for r in state.clip_project_tags
    )


def test_stale_flagged_clips_are_re_tagged():
    state = _state_with_project(n_clips=2)
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c0", project_id="p1", project_tag_id="t1",
            category="on-script", stale=True, confidence=0.3,
        )
    )
    # The retag returns a fresh row with new confidence + non-stale.
    client = _fake_client([
        {"results": [
            _row("c0", line_tag_id="t2", category="related-but-different", confidence=0.9),
            _row("c1"),
        ]}
    ])
    result = tag_project(state, "p1", llm_client=client)
    assert result.clips_skipped == 0
    assert result.clips_tagged == 2
    # The old stale row is gone, replaced by the fresh one.
    c0_rows = [r for r in state.clip_project_tags if r.clip_id == "c0"]
    assert len(c0_rows) == 1
    assert c0_rows[0].project_tag_id == "t2"
    assert c0_rows[0].category == "related-but-different"
    assert c0_rows[0].stale is False
    assert c0_rows[0].confidence == 0.9


def test_no_op_when_all_clips_already_tagged():
    state = _state_with_project(n_clips=2)
    for i in range(2):
        state.clip_project_tags.append(
            ClipProjectTag(
                clip_id=f"c{i}", project_id="p1", project_tag_id="t1",
                category="on-script", stale=False,
            )
        )
    client = _fake_client([])  # Should never be called.
    result = tag_project(state, "p1", llm_client=client)
    assert result.batches == 0
    assert result.clips_tagged == 0
    assert result.clips_skipped == 2


# ---------- Cross-project isolation -----------------------------------------


def test_project_a_tagging_does_not_touch_project_b_rows():
    state = _state_with_project(n_clips=2, project_id="p1")
    state.projects["p2"] = Project(
        name="other", brief_md="", script=Script(lines=["x"]),
        tags={"t9": ProjectTag(kind="line", name="x", order_idx=0)},
        created_at=_now(),
    )
    # p2 has an existing tag for c0; tagging p1 must leave it alone.
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c0", project_id="p2", project_tag_id="t9",
            category="on-script", stale=False,
        )
    )
    client = _fake_client([{"results": [_row("c0"), _row("c1")]}])
    tag_project(state, "p1", llm_client=client)
    p2_rows = [r for r in state.clip_project_tags if r.project_id == "p2"]
    assert len(p2_rows) == 1
    assert p2_rows[0].clip_id == "c0"


# ---------- Validation rules ------------------------------------------------


def test_drops_row_with_unknown_line_tag_id():
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [
        _row("c0", line_tag_id="t999"),  # hallucinated
        _row("c1", line_tag_id="t1"),
    ]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.rows_dropped == 1
    assert result.clips_tagged == 1
    assert {r.clip_id for r in state.clip_project_tags} == {"c1"}


def test_drops_row_with_invalid_category():
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [
        _row("c0", category="not-a-real-category"),
        _row("c1"),
    ]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.rows_dropped == 1
    assert result.clips_tagged == 1


def test_drops_row_with_missing_required_field():
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [
        {"clip_id": "c0", "line_tag_id": "t1"},  # no category, no confidence
        _row("c1"),
    ]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.rows_dropped == 1
    assert result.clips_tagged == 1


def test_clamps_out_of_range_confidence():
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [
        _row("c0", confidence=1.5),    # too high
        _row("c1", confidence=-0.3),   # negative
    ]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.rows_dropped == 0
    assert result.clips_tagged == 2
    confidences = {r.clip_id: r.confidence for r in state.clip_project_tags}
    assert confidences["c0"] == 1.0
    assert confidences["c1"] == 0.0


def test_drops_row_with_clip_id_not_in_batch():
    """Hallucinated clip_ids (LLM made one up) — dropped + logged."""
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [
        _row("c0"),
        _row("c_phantom"),  # not in batch
        _row("c1"),
    ]}])
    result = tag_project(state, "p1", llm_client=client)
    assert result.rows_dropped == 1
    assert result.clips_tagged == 2


def test_batch_size_mismatch_keeps_partial_results():
    """LLM returns N=1 row for a 2-clip batch → keep the 1 (partial wins
    are real); the missing clip just doesn't get tagged this run."""
    state = _state_with_project(n_clips=2)
    client = _fake_client([{"results": [_row("c0")]}])  # only 1 of 2
    result = tag_project(state, "p1", llm_client=client)
    assert result.clips_tagged == 1
    assert result.batches == 1
    # c1 has no row, c0 has one.
    cids_tagged = {r.clip_id for r in state.clip_project_tags}
    assert cids_tagged == {"c0"}


# ---------- Retry + bucket ---------------------------------------------------


def test_retry_once_on_empty_result():
    state = _state_with_project(n_clips=2)
    # First call: all hallucinated → empty after validation → retry.
    # Second call: clean.
    client = _fake_client([
        {"results": [_row("c_x"), _row("c_y")]},
        {"results": [_row("c0"), _row("c1")]},
    ])
    result = tag_project(state, "p1", llm_client=client)
    assert result.clips_tagged == 2
    assert result.untagged_batches == []


def test_retry_failure_buckets_batch_and_continues():
    state = _state_with_project(n_clips=4)
    # Batch 1 (clips c0, c1, c2, c3) → all hallucinated → retry → still bad → bucket.
    # But it's only one batch when batch_size=10. To exercise "continue with
    # next batch" we use batch_size=2 so we get two batches.
    client = _fake_client([
        # Batch 1 first try → all unknown clip_ids:
        {"results": [_row("c_x"), _row("c_y")]},
        # Batch 1 retry → still all unknown:
        {"results": [_row("c_x"), _row("c_y")]},
        # Batch 2 first try → clean:
        {"results": [_row("c2"), _row("c3")]},
    ])
    result = tag_project(state, "p1", llm_client=client, batch_size=2)
    assert result.batches == 2
    assert result.clips_tagged == 2
    assert len(result.untagged_batches) == 1
    assert set(result.untagged_batches[0].clip_ids) == {"c0", "c1"}


def test_llm_client_returns_none_buckets_after_retry():
    """LLM client returns None (Ollama unreachable, malformed JSON, etc.)
    twice → batch bucketed."""
    state = _state_with_project(n_clips=2)
    client = _fake_client([None, None])
    result = tag_project(state, "p1", llm_client=client)
    assert result.clips_tagged == 0
    assert len(result.untagged_batches) == 1


# ---------- Empty-brief rejection -------------------------------------------


def test_empty_brief_raises():
    state = ClipFarmState()
    state.projects["p1"] = Project(
        name="empty", brief_md="", script=None, tags={}, created_at=_now()
    )
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True
    )
    state.clips["c0"] = Clip(source_id="1", start_sec=0, end_sec=1, created_at=_now())
    with pytest.raises(ValueError, match="has no script lines"):
        tag_project(state, "p1", llm_client=_fake_client([]))


def test_unknown_project_raises():
    state = ClipFarmState()
    with pytest.raises(KeyError):
        tag_project(state, "nope", llm_client=_fake_client([]))


# ---------- Dry run + batch_size validation ----------------------------------


def test_dry_run_writes_no_rows():
    state = _state_with_project(n_clips=3)
    client = _fake_client([{"results": [_row("c0"), _row("c1"), _row("c2")]}])
    result = tag_project(state, "p1", llm_client=client, dry_run=True)
    assert result.batches == 1
    assert result.clips_tagged == 0
    assert state.clip_project_tags == []


def test_batch_size_out_of_range_raises():
    state = _state_with_project(n_clips=1)
    with pytest.raises(ValueError):
        tag_project(state, "p1", llm_client=_fake_client([]), batch_size=0)
    with pytest.raises(ValueError):
        tag_project(state, "p1", llm_client=_fake_client([]), batch_size=MAX_BATCH_SIZE + 1)
