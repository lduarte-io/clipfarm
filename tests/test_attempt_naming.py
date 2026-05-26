"""Tests for `clipfarm/attempt_naming.py` — batched LLM naming with
per-strategy canned fallback."""
from __future__ import annotations

from clipfarm.attempt_naming import (
    AttemptNameSummary,
    MAX_NAME_LENGTH,
    name_attempts,
)
from clipfarm.strategies import STRATEGY_CANNED_NAMES


def _summary(strategy_id: str, hint: str = "hint", **kw) -> AttemptNameSummary:
    return AttemptNameSummary(
        strategy_id=strategy_id,
        name_hint=hint,
        continuity_score=kw.get("continuity_score", 0.9),
        clip_count=kw.get("clip_count", 5),
        transcript_preview=kw.get("transcript_preview", "..."),
    )


def test_empty_summaries_returns_empty():
    assert name_attempts([], llm_client=None) == []


def test_llm_none_uses_canned_for_every_attempt():
    """`llm_client=None` is the no-LLM mode (tests, or by-design skips).
    Every attempt gets its strategy's canned name."""
    summaries = [
        _summary("best_per_line_in_script_order"),
        _summary("longest_contiguous_take"),
    ]
    out = name_attempts(summaries, llm_client=None)
    assert len(out) == 2
    assert out[0].name == STRATEGY_CANNED_NAMES["best_per_line_in_script_order"]
    assert out[1].name == STRATEGY_CANNED_NAMES["longest_contiguous_take"]
    assert all(n.name_source == "canned" for n in out)


def test_llm_returns_all_names_marks_source_llm():
    """Happy path: LLM returns N valid names for N attempts."""
    summaries = [
        _summary("best_per_line_in_script_order"),
        _summary("longest_contiguous_take"),
    ]
    canned_response = {
        "names": ["the all-line greatest hits", "the one-take wonder"]
    }
    client_calls: list[tuple[list, dict]] = []
    def fake(messages, schema):
        client_calls.append((messages, schema))
        return canned_response

    out = name_attempts(summaries, llm_client=fake)
    assert len(client_calls) == 1, "must be one batched call, not per-attempt"
    assert [n.name for n in out] == ["the all-line greatest hits", "the one-take wonder"]
    assert all(n.name_source == "llm" for n in out)


def test_partial_success_mixes_llm_and_canned():
    """LLM returns 3 names but 1 is empty → that one gets the canned
    name, the others stay LLM-sourced. Overall response is mixed."""
    summaries = [
        _summary("best_per_line_in_script_order"),
        _summary("longest_contiguous_take"),
        _summary("near_one_take"),
    ]
    response = {"names": ["good one", "", "another good one"]}
    out = name_attempts(summaries, llm_client=lambda m, s: response)
    assert out[0].name == "good one"
    assert out[0].name_source == "llm"
    assert out[1].name == STRATEGY_CANNED_NAMES["longest_contiguous_take"]
    assert out[1].name_source == "canned"
    assert out[2].name == "another good one"
    assert out[2].name_source == "llm"


def test_llm_returns_none_falls_back_to_canned():
    """Connection error / malformed JSON → orchestrator-side client
    returns None → all canned."""
    summaries = [_summary("best_per_line_in_script_order")]
    out = name_attempts(summaries, llm_client=lambda m, s: None)
    assert out[0].name == STRATEGY_CANNED_NAMES["best_per_line_in_script_order"]
    assert out[0].name_source == "canned"


def test_llm_returns_malformed_response_falls_back_to_canned():
    """`names` field missing or wrong type → all canned."""
    summaries = [_summary("longest_contiguous_take")]
    out = name_attempts(
        summaries, llm_client=lambda m, s: {"wrong_field": ["x"]}
    )
    assert out[0].name_source == "canned"


def test_llm_returns_too_few_names_pads_with_canned():
    """LLM returns 2 names for 3 attempts → 3rd uses canned."""
    summaries = [
        _summary("best_per_line_in_script_order"),
        _summary("longest_contiguous_take"),
        _summary("near_one_take"),
    ]
    out = name_attempts(
        summaries, llm_client=lambda m, s: {"names": ["a", "b"]}
    )
    assert [n.name_source for n in out] == ["llm", "llm", "canned"]
    assert out[2].name == STRATEGY_CANNED_NAMES["near_one_take"]


def test_overly_long_name_is_truncated_not_rejected():
    """LLM occasionally appends justification; truncate with ellipsis
    instead of dropping the whole row."""
    too_long = "x" * (MAX_NAME_LENGTH + 50)
    out = name_attempts(
        [_summary("near_one_take")],
        llm_client=lambda m, s: {"names": [too_long]},
    )
    assert len(out[0].name) == MAX_NAME_LENGTH
    assert out[0].name.endswith("…")
    assert out[0].name_source == "llm"


def test_llm_client_raises_falls_back_to_canned():
    """Exceptions from the LLM client are caught and fall through."""
    def broken(messages, schema):
        raise RuntimeError("connection refused")
    out = name_attempts(
        [_summary("ad_libbed")], llm_client=broken
    )
    assert out[0].name_source == "canned"
    assert out[0].name == STRATEGY_CANNED_NAMES["ad_libbed"]
