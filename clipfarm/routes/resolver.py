"""GET /api/attempts/{attempt_id}/resolved — Phase 9 read-only endpoint.

Resolves an attempt into the ordered playback queue the frontend's
preview pane consumes. Each `range` item carries `source_url` (so the
`<video>` element can fetch directly) + `source_filename` (so the pane
can label what's playing) — both derived server-side to save the
frontend a state-fetch round trip.

Pure read: no lock, no snapshot, no mutation.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from clipfarm.models import ClipFarmState, StrictModel
from clipfarm.resolver import (
    ResolvedItem,
    ResolvedRange,
    TombstoneRange,
    resolve_attempt,
)
from clipfarm.routes.deps import get_state

router = APIRouter(prefix="/api", tags=["resolver"])


class ResolvedRangeOut(StrictModel):
    """Wire shape for a `ResolvedRange` — adds `source_url` and
    `source_filename` derived server-side so the frontend doesn't have
    to join against `/api/state`."""

    type: str = "range"
    clip_id: str
    source_id: str
    source_filename: str
    source_url: str
    effective_start_sec: float
    effective_end_sec: float


class TombstoneRangeOut(StrictModel):
    type: str = "tombstone"
    clip_id: str
    reason: str


class AttemptResolvedResponse(StrictModel):
    attempt_id: str
    items: list[ResolvedRangeOut | TombstoneRangeOut] = Field(default_factory=list)


@router.get(
    "/attempts/{attempt_id}/resolved",
    response_model=AttemptResolvedResponse,
)
def get_attempt_resolved(
    attempt_id: str,
    state: ClipFarmState = Depends(get_state),
) -> AttemptResolvedResponse:
    try:
        items: list[ResolvedItem] = resolve_attempt(state, attempt_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown attempt_id: {attempt_id}"
        )

    out: list[ResolvedRangeOut | TombstoneRangeOut] = []
    for item in items:
        if isinstance(item, TombstoneRange):
            out.append(TombstoneRangeOut(clip_id=item.clip_id, reason=item.reason))
            continue
        # ResolvedRange. Look up filename for label.
        assert isinstance(item, ResolvedRange)
        source = state.sources.get(item.source_id)
        filename = source.filename if source is not None else "(unknown source)"
        out.append(ResolvedRangeOut(
            clip_id=item.clip_id,
            source_id=item.source_id,
            source_filename=filename,
            source_url=f"/api/sources/{item.source_id}/video",
            effective_start_sec=item.effective_start_sec,
            effective_end_sec=item.effective_end_sec,
        ))
    return AttemptResolvedResponse(attempt_id=attempt_id, items=out)
