"""GET /api/sources/{source_id}/transcript — the raw transcript for a single
source, annotated with the clip ranges detected for that source.

The response shape combines the validated `WhisperTranscript` payload with
the auto-detected `clips` ranges from `state.clips` so the frontend can mark
clip boundaries inline without a second round-trip.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from clipfarm.models import (
    ClipFarmState,
    StrictModel,
    WhisperSegment,
)
from clipfarm.routes.deps import get_state
from clipfarm.transcripts import load_transcript_for_source

router = APIRouter(prefix="/api", tags=["transcripts"])


class ClipRange(StrictModel):
    clip_id: str
    start_sec: float
    end_sec: float


class TranscriptView(StrictModel):
    source_id: str
    filename: str
    duration_sec: Optional[float] = None
    segments: list[WhisperSegment]
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
        segments=transcript.segments,
        clips=clips,
    )
