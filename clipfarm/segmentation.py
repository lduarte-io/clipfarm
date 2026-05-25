"""Silence-gap clip segmentation — pure function, no I/O.

The 2-second silence-gap heuristic is the spec's default for splitting a
recording into candidate clips. Boundary correction (Phase 4) handles the
inevitable cases where this heuristic gets it wrong.

This module is a single pure function so it can be tested without spinning
up the rest of ClipFarm. The orchestrator in `ingest.py` calls it with the
words from a validated `WhisperTranscript`.
"""
from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class _HasWordTiming(Protocol):
    """Anything with `start: float` / `end: float` works. Lets tests pass
    bare dataclasses; production code passes `WhisperWord` instances."""

    start: float
    end: float


DEFAULT_GAP_THRESHOLD_SEC = 2.0


def segment_words_by_silence(
    words: Iterable[_HasWordTiming],
    gap_threshold_sec: float = DEFAULT_GAP_THRESHOLD_SEC,
) -> list[tuple[float, float]]:
    """Group `words` into clip ranges. A new clip starts when the gap from
    the previous word's `end` to the current word's `start` is **>=** the
    threshold.

    Returns `[(start_sec, end_sec), ...]` where each range's `start` is the
    first word's `start` and `end` is the last word's `end`. Empty input
    returns an empty list.

    Pure — no I/O, no mutation of inputs.
    """
    if gap_threshold_sec < 0:
        raise ValueError(f"gap_threshold_sec must be >= 0, got {gap_threshold_sec}")

    iterator = iter(words)
    try:
        first = next(iterator)
    except StopIteration:
        return []

    ranges: list[tuple[float, float]] = []
    cur_start: float = first.start
    cur_end: float = first.end
    prev_end: float = first.end

    for w in iterator:
        gap = w.start - prev_end
        if gap >= gap_threshold_sec:
            # Close the current range and start a new one.
            ranges.append((cur_start, cur_end))
            cur_start = w.start
        cur_end = w.end
        prev_end = w.end

    ranges.append((cur_start, cur_end))
    return ranges


__all__ = ["DEFAULT_GAP_THRESHOLD_SEC", "segment_words_by_silence"]
