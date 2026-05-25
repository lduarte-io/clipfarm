"""Tests for the pure substring search."""
from __future__ import annotations

import pytest

from clipfarm.models import WhisperSegment, WhisperTranscript, WhisperWord
from clipfarm.search import DEFAULT_CONTEXT_WORDS, search_transcript


def _word(start: float, end: float, text: str) -> WhisperWord:
    return WhisperWord(start=start, end=end, word=text)


def _transcript(*words: WhisperWord, seg_split_at: int | None = None) -> WhisperTranscript:
    """Build a one-or-two-segment transcript from a flat word list. `seg_split_at`
    moves words after that index into a second segment."""
    if seg_split_at is None:
        segs = [WhisperSegment(start=0.0, end=10.0, words=list(words))]
    else:
        first = list(words[:seg_split_at])
        second = list(words[seg_split_at:])
        segs = [
            WhisperSegment(start=first[0].start, end=first[-1].end, words=first),
            WhisperSegment(start=second[0].start, end=second[-1].end, words=second),
        ]
    return WhisperTranscript(schema_version=1, segments=segs)


def test_empty_transcript_returns_no_hits():
    t = WhisperTranscript(schema_version=1, segments=[])
    assert search_transcript(t, "anything") == []


def test_substring_match_within_word():
    """`custo` matches `custody`."""
    t = _transcript(
        _word(0.0, 0.3, " self"),
        _word(0.4, 0.9, " custody"),
        _word(1.0, 1.3, " rules"),
    )
    hits = search_transcript(t, "custo")
    assert len(hits) == 1
    assert hits[0].match == " custody"
    assert hits[0].timestamp_sec == 0.4
    assert hits[0].context_before == " self"
    assert hits[0].context_after == " rules"


def test_case_insensitive():
    t = _transcript(_word(0.0, 0.5, " Bitcoin"))
    assert len(search_transcript(t, "bitcoin")) == 1
    assert len(search_transcript(t, "BITCOIN")) == 1
    assert len(search_transcript(t, "Coin")) == 1


def test_no_match_returns_empty():
    t = _transcript(_word(0.0, 0.5, " hello"), _word(0.6, 1.0, " world"))
    assert search_transcript(t, "ethereum") == []


def test_multi_word_phrase_does_not_match():
    """Spec-locked v0 behavior: word-level substring only. The phrase
    'self custody' across two words is not a match."""
    t = _transcript(
        _word(0.0, 0.3, " self"),
        _word(0.4, 0.9, " custody"),
    )
    assert search_transcript(t, "self custody") == []


def test_multiple_hits_in_order():
    t = _transcript(
        _word(0.0, 0.3, " Bitcoin"),
        _word(0.4, 0.9, " is"),
        _word(1.0, 1.4, " the"),
        _word(1.5, 2.0, " bitcoin"),
        _word(2.1, 2.4, " standard"),
    )
    hits = search_transcript(t, "bitcoin")
    assert [h.timestamp_sec for h in hits] == [0.0, 1.5]
    assert hits[0].context_after == " is the bitcoin standard"


def test_context_words_clamps_at_start_boundary():
    t = _transcript(
        _word(0.0, 0.3, " Bitcoin"),
        _word(0.4, 0.9, " is"),
        _word(1.0, 1.4, " here"),
    )
    hits = search_transcript(t, "bitcoin", context_words=10)
    assert hits[0].context_before == ""
    assert hits[0].context_after == " is here"


def test_context_words_clamps_at_end_boundary():
    t = _transcript(
        _word(0.0, 0.3, " foo"),
        _word(0.4, 0.9, " bar"),
        _word(1.0, 1.4, " baz"),
    )
    hits = search_transcript(t, "baz", context_words=10)
    assert hits[0].context_after == ""
    assert hits[0].context_before == " foo bar"


def test_custom_context_words():
    t = _transcript(
        _word(0.0, 0.1, " a"),
        _word(0.2, 0.3, " b"),
        _word(0.4, 0.5, " target"),
        _word(0.6, 0.7, " c"),
        _word(0.8, 0.9, " d"),
    )
    hits = search_transcript(t, "target", context_words=1)
    assert hits[0].context_before == " b"
    assert hits[0].context_after == " c"


def test_match_spans_segment_boundary():
    """A query that hits a word in segment 2 should still surface context
    that crosses back into segment 1."""
    t = _transcript(
        _word(0.0, 0.3, " word1"),
        _word(0.4, 0.9, " word2"),
        _word(5.0, 5.4, " target"),
        _word(5.5, 6.0, " word4"),
        seg_split_at=2,
    )
    hits = search_transcript(t, "target")
    assert len(hits) == 1
    assert hits[0].context_before == " word1 word2"
    assert hits[0].context_after == " word4"


def test_default_context_words_locked():
    assert DEFAULT_CONTEXT_WORDS == 5


def test_empty_query_raises():
    t = _transcript(_word(0.0, 0.5, " hello"))
    with pytest.raises(ValueError):
        search_transcript(t, "")
    with pytest.raises(ValueError):
        search_transcript(t, "   ")


def test_negative_context_words_raises():
    t = _transcript(_word(0.0, 0.5, " hello"))
    with pytest.raises(ValueError):
        search_transcript(t, "hello", context_words=-1)
