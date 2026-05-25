"""POST /api/ingest — point the app at an absolute folder path, return the
ingest summary, persist the resulting state.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipfarm.ingest import IngestResult, ingest_folder
from clipfarm.routes.deps import commit_state_to_disk
from clipfarm.store import WritesFrozenError

router = APIRouter(prefix="/api", tags=["ingest"])


class IngestRequest(BaseModel):
    folder: str = Field(
        ...,
        description="Absolute filesystem path to a directory of .mov files. "
        "Browser sandbox can't supply this, so the v0 UX is a text input.",
    )


@router.post("/ingest", response_model=IngestResult)
async def ingest_route(body: IngestRequest, request: Request) -> IngestResult:
    folder = Path(body.folder)
    if not folder.is_absolute():
        raise HTTPException(
            status_code=400,
            detail=f"folder must be an absolute path; got {body.folder!r}",
        )
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"folder does not exist or is not a directory: {folder}",
        )

    app = request.app
    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit "
                "conflict on clipfarm.json — resolve before re-ingesting"
            ),
        )

    state = app.state.clipfarm
    result = ingest_folder(state, folder)

    if (
        result.sources_added
        or result.sources_updated
        or result.clips_detected
    ):
        app.state.dirty = True
        try:
            await commit_state_to_disk(app)
        except WritesFrozenError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return result
