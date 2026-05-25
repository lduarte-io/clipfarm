"""Tests that `WhisperTranscript` accepts the real sidecar shape and rejects
unsupported versions / malformed payloads."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipfarm.models import WhisperTranscript

_REAL_TRANSCRIPT = Path(
    "/Users/lillianduarte/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/"
    "Video/Scripts/mp4files/05.19.26/btc.0.4.whisper.json"
)


@pytest.mark.skipif(
    not _REAL_TRANSCRIPT.exists(),
    reason="real btc.0.4.whisper.json not present on this machine",
)
def test_real_btc_transcript_validates():
    """The actual sidecar produced by `transcribe.py` must validate. If this
    breaks, either the sidecar shape drifted or our model did — neither is
    silent-OK."""
    raw = json.loads(_REAL_TRANSCRIPT.read_text(encoding="utf-8"))
    parsed = WhisperTranscript.model_validate(raw)
    assert parsed.schema_version == 1
    assert parsed.segments, "expected at least one segment in btc.0.4"
    first_segment = parsed.segments[0]
    assert first_segment.words, "expected word-level timestamps"
    first_word = first_segment.words[0]
    assert first_word.start <= first_word.end
    # Spec note: faster_whisper words carry a leading space — make sure the
    # raw word string is preserved (we don't strip it).
    assert any(w.word.startswith(" ") for w in first_segment.words), (
        "expected at least one word with a leading space (faster_whisper convention)"
    )


def test_minimal_valid_payload():
    payload = {
        "schema_version": 1,
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "words": [{"start": 0.0, "end": 0.5, "word": " hi"}],
            }
        ],
    }
    parsed = WhisperTranscript.model_validate(payload)
    assert parsed.segments[0].words[0].word == " hi"


def test_missing_segments_field_validates_as_empty():
    """`segments` defaults to `[]` — a sidecar with no segments at all
    parses, but the ingest orchestrator should treat that as 'no clips'."""
    parsed = WhisperTranscript.model_validate({"schema_version": 1})
    assert parsed.segments == []


def test_malformed_segment_missing_start_raises():
    payload = {
        "schema_version": 1,
        "segments": [{"end": 1.0, "words": []}],  # no start
    }
    with pytest.raises(ValidationError):
        WhisperTranscript.model_validate(payload)


def test_malformed_word_missing_word_field_raises():
    payload = {
        "schema_version": 1,
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "words": [{"start": 0.0, "end": 0.5}],  # no word
            }
        ],
    }
    with pytest.raises(ValidationError):
        WhisperTranscript.model_validate(payload)


def test_unknown_top_level_field_is_dropped_not_rejected():
    """`extra="ignore"` is the same policy as the rest of the data model —
    the loader is the one that logs dropped keys. The model itself just
    accepts and strips."""
    payload = {
        "schema_version": 1,
        "segments": [],
        "transcribed_at": "2026-05-19T...",
        "_future_field": "we don't know this one yet",
    }
    parsed = WhisperTranscript.model_validate(payload)
    assert parsed.transcribed_at == "2026-05-19T..."
    # Unknown field is dropped silently at the model level.
    assert "_future_field" not in parsed.model_dump()
