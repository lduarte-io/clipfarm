"""GET /api/search?q=...&source_id=&limit= — substring search across one or
all transcripts.

The route walks `state.sources` (or just one source if filtered), loads each
transcript via the LRU cache in `clipfarm/transcripts.py`, and applies
`clipfarm.search.search_transcript` to every match. Each hit is stamped with
`source_id` + `filename` + the `clip_id` it falls inside (for the frontend
to jump to the right transcript range).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from clipfarm.models import ClipFarmState, StrictModel
from clipfarm.routes.deps import get_state
from clipfarm.search import SearchHit, search_transcript
from clipfarm.transcripts import load_transcript_for_source

router = APIRouter(prefix="/api", tags=["search"])

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


class StampedHit(StrictModel):
    source_id: str
    filename: str
    clip_id: Optional[str] = None  # the clip the matched word falls inside, if any
    word_index: int
    timestamp_sec: float
    context_before: str
    match: str
    context_after: str


class SearchResponse(StrictModel):
    query: str
    total: int
    hits: list[StampedHit]
    truncated: bool


def _clip_id_for_timestamp(
    state: ClipFarmState, source_id: str, t: float
) -> Optional[str]:
    """First clip on this source whose `[start_sec, end_sec)` contains `t`.

    Half-open interval: a timestamp `t` exactly equal to `c.end_sec` belongs
    to the NEXT clip (the one whose `start_sec == t`), not this one. That
    keeps the invariant "a single timestamp belongs to exactly one clip"
    intact and avoids the off-by-one ambiguity at boundaries. Phase 4's
    extend / shrink operations must preserve this — see PHASES.md Phase 4.

    Linear scan — under v0 scale (~150 clips total) this is cheap; if it
    matters later, bucket clips by source_id in a startup index.
    """
    for cid, c in state.clips.items():
        if c.source_id == source_id and c.start_sec <= t < c.end_sec:
            return cid
    return None


@router.get("/search", response_model=SearchResponse)
def search_route(
    q: str = Query(..., description="Substring to find. Empty/whitespace → 400."),
    source_id: Optional[str] = Query(
        None, description="Narrow to one source. Omit to search every source."
    ),
    limit: int = Query(
        DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
        description="Cap on returned hits. Default 200, max 1000.",
    ),
    state: ClipFarmState = Depends(get_state),
) -> SearchResponse:
    if not q.strip():
        raise HTTPException(status_code=400, detail="query 'q' must not be empty")

    if source_id is not None:
        source_entries = (
            [(source_id, state.sources[source_id])]
            if source_id in state.sources
            else []
        )
        if not source_entries:
            raise HTTPException(status_code=404, detail=f"unknown source_id: {source_id}")
    else:
        source_entries = list(state.sources.items())

    all_hits: list[StampedHit] = []
    for sid, source in source_entries:
        if source.transcript_path is None:
            continue
        transcript = load_transcript_for_source(source)
        if transcript is None:
            continue
        for h in search_transcript(transcript, q):
            all_hits.append(
                StampedHit(
                    source_id=sid,
                    filename=source.filename,
                    clip_id=_clip_id_for_timestamp(state, sid, h.timestamp_sec),
                    word_index=h.word_index,
                    timestamp_sec=h.timestamp_sec,
                    context_before=h.context_before,
                    match=h.match,
                    context_after=h.context_after,
                )
            )

    total = len(all_hits)
    truncated = total > limit
    return SearchResponse(
        query=q,
        total=total,
        hits=all_hits[:limit],
        truncated=truncated,
    )
