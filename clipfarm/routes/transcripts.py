"""GET /api/sources/{source_id}/transcript — the raw transcript for a single
source, annotated with the clip ranges detected for that source.

The response shape combines a slimmed copy of the validated
`WhisperTranscript` (with the per-word `probability` field stripped — the
frontend doesn't use it) with the auto-detected `clips` ranges from
`state.clips` so the frontend can mark clip boundaries inline without a
second round-trip.

Why strip `probability`: for a 34-minute recording (btc.0.4) the full
transcript is ~4700 words. Including `probability` on every word adds
~50% to the response payload for no consumer benefit. Phase 3 review #3.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from clipfarm.models import ClipFarmState, StrictModel
from clipfarm.routes.deps import get_state
from clipfarm.transcripts import load_transcript_for_source

router = APIRouter(prefix="/api", tags=["transcripts"])


class WhisperWordLite(StrictModel):
    """Response-only word shape — `probability` dropped vs the full
    `WhisperWord` model. See module docstring."""

    start: float
    end: float
    word: str


class WhisperSegmentLite(StrictModel):
    id: Optional[int] = None
    start: float
    end: float
    text: Optional[str] = None
    words: list[WhisperWordLite]


class ClipRange(StrictModel):
    clip_id: str
    start_sec: float
    end_sec: float


class TranscriptView(StrictModel):
    source_id: str
    filename: str
    duration_sec: Optional[float] = None
    segments: list[WhisperSegmentLite]
    clips: list[ClipRange]


@router.get("/sources/{source_id}/transcript", response_model=TranscriptView)
def get_source_transcript(
    source_id: str,
    state: ClipFarmState = Depends(get_state),
) -> TranscriptView:
    source = state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"unknown source_id: {source_id}")
    if source.transcript_path is None:
        # Frontend uses 422 to distinguish "footage-only source" from
        # "missing source" (404).
        raise HTTPException(
            status_code=422,
            detail=f"source {source_id} ({source.filename}) has no transcript",
        )

    transcript = load_transcript_for_source(source)
    if transcript is None:
        # Sidecar exists according to state, but reading/parsing failed
        # (file deleted, permissions, etc.). Surface as 500 — this is a
        # state-vs-disk drift, not a user-input problem.
        raise HTTPException(
            status_code=500,
            detail=(
                f"failed to load transcript at {source.transcript_path} — "
                f"file may have moved or been corrupted"
            ),
        )

    # Project full Whisper segments into the lite response shape, dropping
    # per-word probability. Cheap (~4700 iterations on btc.0.4).
    lite_segments: list[WhisperSegmentLite] = [
        WhisperSegmentLite(
            id=seg.id,
            start=seg.start,
            end=seg.end,
            text=seg.text,
            words=[
                WhisperWordLite(start=w.start, end=w.end, word=w.word)
                for w in seg.words
            ],
        )
        for seg in transcript.segments
    ]

    clips: list[ClipRange] = [
        ClipRange(clip_id=cid, start_sec=clip.start_sec, end_sec=clip.end_sec)
        for cid, clip in state.clips.items()
        if clip.source_id == source_id
    ]
    clips.sort(key=lambda c: c.start_sec)

    return TranscriptView(
        source_id=source_id,
        filename=source.filename,
        duration_sec=source.duration_sec,
        segments=lite_segments,
        clips=clips,
    )
