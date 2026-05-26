"""GET /api/projects/{project_id}/take-grid — Phase 7 read-only endpoint.

Returns the laid-out take grid (script-line rows + off-line buckets +
summary counters) for one project. Pure read:

- No mutation, no snapshot, no save-lock acquisition.
- Cheap to call repeatedly; the underlying Whisper sidecars are cached
  by `clipfarm.transcripts.load_transcript_for_source` keyed on
  (path, mtime_ns), so the first call after a `Tag clips` run warms the
  cache and every subsequent grid build is sub-millisecond.

If we ever want client-side caching, an ETag derived from
`(state.version, len(state.clip_project_tags))` plus the project's
`stale` count would be cheap to compute. v0 doesn't need it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from clipfarm.models import ClipFarmState
from clipfarm.routes.deps import get_state
from clipfarm.take_grid import TakeGridView, build_take_grid

router = APIRouter(prefix="/api", tags=["take-grid"])


@router.get(
    "/projects/{project_id}/take-grid",
    response_model=TakeGridView,
)
def get_take_grid(
    project_id: str,
    state: ClipFarmState = Depends(get_state),
) -> TakeGridView:
    try:
        return build_take_grid(state, project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id: {project_id}"
        )
