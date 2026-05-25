"""GET /api/state — return the current in-memory `ClipFarmState` as JSON.

Also conditionally exposes `POST /api/test/touch` for the Phase 1
concurrent-save verification. Gated behind `CLIPFARM_TEST_ROUTES=1` so it
doesn't sit in the openapi docs for normal use.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from clipfarm.models import ClipFarmState
from clipfarm.routes.deps import commit_state_to_disk, get_state

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state", response_model=ClipFarmState)
def get_state_route(state: ClipFarmState = Depends(get_state)) -> ClipFarmState:
    return state


@router.get("/health")
def health() -> dict:
    """Trivial liveness probe — also useful as a 'is the server up?' check."""
    return {"ok": True}


@router.get("/conflicts/pending", tags=["state"])
def pending_conflicts(request: Request) -> dict:
    """Count of unresolved external-edit conflict events sitting in the queue.

    Phase 1 surfaces this as a number; Phase 2 will replace it with a stream
    + a modal. Useful right now for the verification flow.
    """
    q = request.app.state.conflict_events
    return {
        "pending": q.qsize(),
        "writes_frozen": bool(request.app.state.writes_frozen),
    }


# --- Test-only route, gated by env var ---------------------------------------

# Set CLIPFARM_TEST_ROUTES=1 to expose `POST /api/test/touch`. Used by the
# concurrent-save verification in PHASES.md → Phase 1; not part of the
# production surface.

if os.environ.get("CLIPFARM_TEST_ROUTES") == "1":

    @router.post("/test/touch", tags=["test"])
    async def test_touch(request: Request) -> dict:
        """[test] Bump a counter on `app.state` and persist. Verifies the
        `asyncio.Lock` serializes concurrent saves under load.

        Writes to an off-schema `_touch_counter` on `app.state` rather than
        mutating `ClipFarmState`, so it doesn't dirty the JSON schema."""
        from clipfarm.store import WritesFrozenError

        app = request.app
        if not hasattr(app.state, "_touch_counter"):
            app.state._touch_counter = 0
        app.state._touch_counter += 1
        app.state.dirty = True

        try:
            await commit_state_to_disk(app)
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return {
            "counter": app.state._touch_counter,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
