"""Attempt CRUD routes — create / fork / rename / patch-clips / delete.

Pure local mutations (no LLM calls, no Whisper reads), so unlike the
tagging / premade routes there's no `asyncio.to_thread` wrap — these
are fast enough to run synchronously inside the save lock. The Phase 6.1
`dirty=True`-before-mutation invariant still applies (watcher polls
every 500ms and could in principle land between mutation and commit
even on a sync route).

Pattern (matches Phase 5 projects routes + Phase 8 premade route):

```python
async with app.state.save_lock:
    app.state.dirty = True
    try:
        attempt = attempts_ops.<op>(state, ...)
    except KeyError as e:
        raise HTTPException(404, ...)
    except ValueError as e:
        raise HTTPException(400, ...)
    commit_state_with_snapshot_locked(app, "<reason>")
```

Routes:
- `POST /api/projects/{project_id}/attempts` → create hand-built.
- `POST /api/attempts/{attempt_id}/fork`     → clone with source=fork.
- `PATCH /api/attempts/{attempt_id}`         → rename (metadata only).
- `PATCH /api/attempts/{attempt_id}/clips`   → replace clip list.
- `DELETE /api/attempts/{attempt_id}`        → remove from state.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from clipfarm import attempts_ops
from clipfarm.models import Attempt, AttemptClip, ClipFarmState, StrictModel
from clipfarm.routes.deps import commit_state_with_snapshot_locked
from clipfarm.store import WritesFrozenError

router = APIRouter(prefix="/api", tags=["attempts"])


# ─────────────────────────────────────────────────────────────────────────────
# Request / response shapes
# ─────────────────────────────────────────────────────────────────────────────


class CreateAttemptBody(StrictModel):
    name: Optional[str] = None
    clips: Optional[list[AttemptClip]] = None


class AttemptResponse(StrictModel):
    attempt_id: str
    attempt: Attempt


class RenameAttemptBody(StrictModel):
    name: str = Field(..., min_length=1)


class PatchClipsBody(StrictModel):
    """Full replacement of the clip list. Empty `clips` is allowed
    (the attempt becomes an empty draft with `continuity_score=None`)."""

    clips: list[AttemptClip] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _check_writes_frozen(app) -> None:
    """Standard 409 short-circuit if the watcher froze writes."""
    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit "
                "conflict on clipfarm.json — resolve before continuing"
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/attempts",
    response_model=AttemptResponse,
)
async def create_attempt_route(
    project_id: str, body: CreateAttemptBody, request: Request,
) -> AttemptResponse:
    app = request.app
    state: ClipFarmState = app.state.clipfarm
    _check_writes_frozen(app)

    async with app.state.save_lock:
        # Phase 6.1 invariant: dirty=True before mutation, even for sync
        # routes — the watcher race window exists regardless of to_thread.
        app.state.dirty = True
        try:
            aid, attempt = attempts_ops.create_hand_built_attempt(
                state, project_id, name=body.name, clips=body.clips,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            commit_state_with_snapshot_locked(app, "hand-built-create")
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return AttemptResponse(attempt_id=aid, attempt=attempt)


@router.post(
    "/attempts/{attempt_id}/fork",
    response_model=AttemptResponse,
)
async def fork_attempt_route(
    attempt_id: str, request: Request,
) -> AttemptResponse:
    app = request.app
    state: ClipFarmState = app.state.clipfarm
    _check_writes_frozen(app)

    async with app.state.save_lock:
        app.state.dirty = True
        try:
            aid, attempt = attempts_ops.fork_attempt(state, attempt_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        try:
            commit_state_with_snapshot_locked(app, "attempt-fork")
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return AttemptResponse(attempt_id=aid, attempt=attempt)


@router.patch(
    "/attempts/{attempt_id}",
    response_model=AttemptResponse,
)
async def rename_attempt_route(
    attempt_id: str, body: RenameAttemptBody, request: Request,
) -> AttemptResponse:
    """Rename — metadata only. Separate from PATCH /clips so the
    clip-list route stays semantically pure (clips ↔ clips)."""
    app = request.app
    state: ClipFarmState = app.state.clipfarm
    _check_writes_frozen(app)

    async with app.state.save_lock:
        app.state.dirty = True
        try:
            attempt = attempts_ops.rename_attempt(state, attempt_id, body.name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            commit_state_with_snapshot_locked(app, "attempt-rename")
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return AttemptResponse(attempt_id=attempt_id, attempt=attempt)


@router.patch(
    "/attempts/{attempt_id}/clips",
    response_model=AttemptResponse,
)
async def patch_attempt_clips_route(
    attempt_id: str, body: PatchClipsBody, request: Request,
) -> AttemptResponse:
    """Replace the attempt's clip list wholesale.

    Validation per the plan-review-locked rules (see `attempts_ops.
    replace_attempt_clips` for the full description):

    - PATCH-to-empty allowed (no force flag).
    - Existing tombstones pass through.
    - New clip_ids must exist in `state.clips`; unknown → 400.
    - Tombstones can be dropped from the list.

    Recomputes `continuity_score` on success.
    """
    app = request.app
    state: ClipFarmState = app.state.clipfarm
    _check_writes_frozen(app)

    async with app.state.save_lock:
        app.state.dirty = True
        try:
            attempt = attempts_ops.replace_attempt_clips(
                state, attempt_id, body.clips,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            commit_state_with_snapshot_locked(app, "attempt-clips-patch")
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return AttemptResponse(attempt_id=attempt_id, attempt=attempt)


@router.delete(
    "/attempts/{attempt_id}",
    status_code=200,
)
async def delete_attempt_route(
    attempt_id: str, request: Request,
) -> dict:
    """Delete an attempt. Hand-built / fork / ai-premade all allowed
    (backend doesn't gate on source; UI surfaces a confirmation modal
    specific to ai-premade). Forks whose `parent_attempt_id` pointed
    at this attempt are NOT cascaded — their parent_attempt_id becomes
    a dangling reference, matching Phase 4's tombstone pattern.
    """
    app = request.app
    state: ClipFarmState = app.state.clipfarm
    _check_writes_frozen(app)

    async with app.state.save_lock:
        app.state.dirty = True
        try:
            deleted = attempts_ops.delete_attempt(state, attempt_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        try:
            commit_state_with_snapshot_locked(app, "attempt-delete")
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return {
        "attempt_id": attempt_id,
        "deleted_name": deleted.name,
    }
