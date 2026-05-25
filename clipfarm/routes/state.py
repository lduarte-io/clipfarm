"""GET /api/state — return the current in-memory `ClipFarmState` as JSON.

Also exposes `POST /api/test/touch` for the Phase 1 concurrent-save
verification listed in `PHASES.md`. Removable after Phase 2 stabilizes —
flagged with a `[test]` tag so it's obvious in `/docs`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from clipfarm.models import ClipFarmState

router = APIRouter(prefix="/api", tags=["state"])


def _get_state(request: Request) -> ClipFarmState:
    # Local proxy for `clipfarm.app.get_state` — avoids the import cycle
    # (`app.py` includes this router at import time).
    return request.app.state.clipfarm  # type: ignore[no-any-return]


@router.get("/state", response_model=ClipFarmState)
def get_state_route(state: ClipFarmState = Depends(_get_state)) -> ClipFarmState:
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


@router.post("/test/touch", tags=["test"])
async def test_touch(request: Request) -> dict:
    """[test] Bump a counter on `app.state` and persist. Used to verify the
    `asyncio.Lock` serializes concurrent saves under load.

    Implementation note: writes to a dedicated `_touch_counter` field on
    `app.state` rather than mutating `ClipFarmState` (which would dirty the
    schema with a test-only key)."""
    from clipfarm.app import commit_state_to_disk
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
