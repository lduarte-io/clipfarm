"""Pure substring search over a Whisper transcript's word stream.

v0 behavior (locked in spec → "Library page"):
- Word-level case-insensitive substring. `"custo"` matches the word
  `"custody"`; `"self custody"` (two-word phrase) does NOT match — that's a
  Future Idea (semantic search via embeddings is in the same bucket).
- The faster_whisper leading-space convention (`" custody"`) is stripped
  before comparison. Surface text in the hit retains the leading space so
  the frontend can render context faithfully.

Pure module: no I/O, no FastAPI, no state. The route layer feeds it a
loaded transcript and maps the resulting hits onto its response shape.
"""
from __future__ import annotations

from typing import Optional

from clipfarm.models import StrictModel, WhisperTranscript, WhisperWord

DEFAULT_CONTEXT_WORDS = 5


class SearchHit(StrictModel):
    """One match inside one transcript.

    `word_index` is the index into the flattened word list (all segments
    concatenated). `timestamp_sec` is `word.start` for the matched word —
    the play-head position the UI should seek to on click.
    """

    word_index: int
    timestamp_sec: float
    context_before: str
    match: str
    context_after: str


def _flatten_words(transcript: WhisperTranscript) -> list[WhisperWord]:
    out: list[WhisperWord] = []
    for seg in transcript.segments:
        out.extend(seg.words)
    return out


def _join_words(words: list[WhisperWord]) -> str:
    """Faster_whisper words carry leading spaces — concat as-is."""
    return "".join(w.word for w in words)


def search_transcript(
    transcript: WhisperTranscript,
    query: str,
    *,
    context_words: int = DEFAULT_CONTEXT_WORDS,
) -> list[SearchHit]:
    """Find every word whose stripped text contains `query` (case-insensitive).
    Returns a list of `SearchHit`s in transcript order.

    `query` must be non-empty after stripping — empty/whitespace queries
    raise `ValueError`. The route layer surfaces that as a 400.

    `context_words` is the number of words pulled on each side. Clamped at
    the transcript boundaries (no out-of-range).
    """
    q = query.strip()
    if not q:
        raise ValueError("search query must not be empty")
    if context_words < 0:
        raise ValueError(f"context_words must be >= 0, got {context_words}")
    q_lower = q.lower()

    all_words = _flatten_words(transcript)
    hits: list[SearchHit] = []
    for i, w in enumerate(all_words):
        # Strip the leading-space convention before comparing.
        stripped = w.word.strip().lower()
        if q_lower in stripped:
            before = all_words[max(0, i - context_words) : i]
            after = all_words[i + 1 : i + 1 + context_words]
            hits.append(
                SearchHit(
                    word_index=i,
                    timestamp_sec=w.start,
                    context_before=_join_words(before),
                    match=w.word,
                    context_after=_join_words(after),
                )
            )
    return hits


__all__ = ["DEFAULT_CONTEXT_WORDS", "SearchHit", "search_transcript"]
