"""Boundary correction routes — split / merge / adjust / create / delete.

Each route:
- Validates input and reads the affected clip(s) under the save lock.
- Loads the source's WhisperTranscript via the cache, passes it as an
  explicit parameter to the orchestrator (boundary.py stays I/O-free).
- Holds `app.state.save_lock` across the orchestrator call to serialize
  mutations (same pattern as Phase 2.1's ingest route).
- Calls `commit_state_with_snapshot(app, reason=<kebab-case>)` after the
  mutation completes — that's how the snapshot-per-op invariant is
  enforced. One op → one snapshot file with a searchable reason segment.
- Maps domain errors to HTTP codes: `KeyError` → 404, `ValueError` →
  400, `WritesFrozenError` → 409.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipfarm.boundary import (
    adjust_clip_boundaries,
    create_clip_from_range,
    delete_clip,
    merge_clips,
    split_clip,
)
from clipfarm.models import ClipFarmState, StrictModel
from clipfarm.routes.deps import commit_state_with_snapshot
from clipfarm.store import WritesFrozenError
from clipfarm.transcripts import load_transcript_for_source

router = APIRouter(prefix="/api", tags=["clips"])


# ---------- Request / response models ----------------------------------------


class SplitRequest(BaseModel):
    split_at_sec: float = Field(..., description="Strictly inside the clip's [start, end).")


class SplitResponse(StrictModel):
    old_clip_id: str
    new_clip_ids: tuple[str, str]
    snapshot: Optional[str]


class MergeRequest(BaseModel):
    clip_ids: list[str] = Field(..., min_length=2)


class MergeResponse(StrictModel):
    new_clip_id: str
    merged: list[str]
    snapshot: Optional[str]


class AdjustRequest(BaseModel):
    start_sec: float
    end_sec: float


class AdjustResponse(StrictModel):
    clip_id: str
    start_sec: float
    end_sec: float
    snapshot: Optional[str]


class CreateRequest(BaseModel):
    start_sec: float
    end_sec: float


class CreateResponse(StrictModel):
    new_clip_id: str
    snapshot: Optional[str]


class DeleteResponse(StrictModel):
    deleted_clip_id: str
    dropped_tag_rows: int
    affected_attempts: int
    snapshot: Optional[str]


# ---------- Helpers -----------------------------------------------------------


def _check_freeze(app) -> None:
    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit conflict"
            ),
        )


def _transcript_for_clip(state: ClipFarmState, clip_id: str):
    clip = state.clips.get(clip_id)
    if clip is None:
        return None
    source = state.sources.get(clip.source_id)
    if source is None:
        return None
    return load_transcript_for_source(source)


def _transcript_for_source_id(state: ClipFarmState, source_id: str):
    source = state.sources.get(source_id)
    if source is None:
        return None
    return load_transcript_for_source(source)


async def _commit_with_reason(app, reason: str) -> Optional[str]:
    """Wrap `commit_state_with_snapshot`, returning the snapshot filename
    for the response (or None if the state file didn't exist yet)."""
    try:
        snap_path = await commit_state_with_snapshot(app, reason)
    except WritesFrozenError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap_path.name if snap_path is not None else None


# ---------- Routes ------------------------------------------------------------


@router.post("/clips/{clip_id}/split", response_model=SplitResponse)
async def split_route(clip_id: str, body: SplitRequest, request: Request) -> SplitResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        transcript = _transcript_for_clip(state, clip_id)
        try:
            c1, c2 = split_clip(state, clip_id, body.split_at_sec, transcript)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.dirty = True

    snapshot = await _commit_with_reason(app, "split-clip")
    return SplitResponse(
        old_clip_id=clip_id, new_clip_ids=(c1, c2), snapshot=snapshot
    )


@router.post("/clips/merge", response_model=MergeResponse)
async def merge_route(body: MergeRequest, request: Request) -> MergeResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        # All clips must exist and live on the same source — load the
        # transcript via the first clip's source.
        first = state.clips.get(body.clip_ids[0]) if body.clip_ids else None
        transcript = (
            _transcript_for_source_id(state, first.source_id) if first else None
        )
        try:
            new_id = merge_clips(state, body.clip_ids, transcript)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.dirty = True

    snapshot = await _commit_with_reason(app, "merge-clips")
    return MergeResponse(
        new_clip_id=new_id, merged=list(body.clip_ids), snapshot=snapshot
    )


@router.patch("/clips/{clip_id}/boundaries", response_model=AdjustResponse)
async def adjust_route(
    clip_id: str, body: AdjustRequest, request: Request
) -> AdjustResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        transcript = _transcript_for_clip(state, clip_id)
        try:
            adjust_clip_boundaries(
                state, clip_id, body.start_sec, body.end_sec, transcript
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.dirty = True

    snapshot = await _commit_with_reason(app, "adjust-boundaries")
    return AdjustResponse(
        clip_id=clip_id,
        start_sec=body.start_sec,
        end_sec=body.end_sec,
        snapshot=snapshot,
    )


@router.post("/sources/{source_id}/clips", response_model=CreateResponse)
async def create_route(
    source_id: str, body: CreateRequest, request: Request
) -> CreateResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        transcript = _transcript_for_source_id(state, source_id)
        try:
            new_id = create_clip_from_range(
                state, source_id, body.start_sec, body.end_sec, transcript
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.dirty = True

    snapshot = await _commit_with_reason(app, "create-clip")
    return CreateResponse(new_clip_id=new_id, snapshot=snapshot)


@router.delete("/clips/{clip_id}", response_model=DeleteResponse)
async def delete_route(clip_id: str, request: Request) -> DeleteResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        try:
            dropped, affected = delete_clip(state, clip_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        app.state.dirty = True

    snapshot = await _commit_with_reason(app, "delete-clip")
    return DeleteResponse(
        deleted_clip_id=clip_id,
        dropped_tag_rows=dropped,
        affected_attempts=affected,
        snapshot=snapshot,
    )
