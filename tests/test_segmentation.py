"""Tests for the pure silence-gap segmentation function."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from clipfarm.segmentation import (
    DEFAULT_GAP_THRESHOLD_SEC,
    segment_words_by_silence,
)


@dataclass
class W:
    """Test stand-in for `WhisperWord`. Only needs start/end."""

    start: float
    end: float


def test_empty_input_returns_empty_list():
    assert segment_words_by_silence([]) == []


def test_single_word_returns_one_range():
    words = [W(0.0, 0.5)]
    assert segment_words_by_silence(words) == [(0.0, 0.5)]


def test_contiguous_words_become_one_range():
    """Sub-threshold gaps stay in the same range."""
    words = [W(0.0, 0.5), W(0.6, 1.0), W(1.1, 1.5), W(1.7, 2.0)]
    assert segment_words_by_silence(words) == [(0.0, 2.0)]


def test_above_threshold_gap_starts_new_range():
    words = [W(0.0, 0.5), W(3.0, 3.5)]
    # gap = 3.0 - 0.5 = 2.5 sec → above 2.0 default → split.
    assert segment_words_by_silence(words) == [(0.0, 0.5), (3.0, 3.5)]


def test_exact_threshold_gap_splits():
    """A gap of *exactly* `gap_threshold_sec` triggers a split (>= boundary)."""
    words = [W(0.0, 0.5), W(2.5, 3.0)]
    # gap = 2.5 - 0.5 = 2.0 sec → ≥ threshold → split.
    assert segment_words_by_silence(words, gap_threshold_sec=2.0) == [
        (0.0, 0.5),
        (2.5, 3.0),
    ]


def test_just_under_threshold_does_not_split():
    words = [W(0.0, 0.5), W(2.49, 3.0)]
    # gap = 2.49 - 0.5 = 1.99 → below 2.0 → no split.
    assert segment_words_by_silence(words, gap_threshold_sec=2.0) == [(0.0, 3.0)]


def test_multiple_segments():
    words = [
        W(0.0, 0.5),
        W(1.0, 1.5),
        # gap of 3.0 → split
        W(4.5, 5.0),
        W(5.5, 6.0),
        # gap of 5.0 → split
        W(11.0, 11.5),
    ]
    assert segment_words_by_silence(words) == [
        (0.0, 1.5),
        (4.5, 6.0),
        (11.0, 11.5),
    ]


def test_custom_threshold():
    words = [W(0.0, 0.5), W(1.0, 1.5)]
    # gap = 0.5 → split with threshold 0.4
    assert segment_words_by_silence(words, gap_threshold_sec=0.4) == [
        (0.0, 0.5),
        (1.0, 1.5),
    ]


def test_negative_threshold_raises():
    with pytest.raises(ValueError):
        segment_words_by_silence([W(0.0, 0.5)], gap_threshold_sec=-0.1)


def test_zero_threshold_splits_every_word():
    """A gap_threshold of 0 means any non-overlapping word starts its own
    range. Useful as a boundary case."""
    words = [W(0.0, 0.5), W(0.5, 1.0), W(1.0, 1.5)]
    # gap == 0 between each pair → all "≥" 0 → every word becomes its own range.
    assert segment_words_by_silence(words, gap_threshold_sec=0.0) == [
        (0.0, 0.5),
        (0.5, 1.0),
        (1.0, 1.5),
    ]


def test_default_threshold_matches_spec():
    """Lock the spec's 2.0s default in a test so a typo can't silently
    drift it."""
    assert DEFAULT_GAP_THRESHOLD_SEC == 2.0
