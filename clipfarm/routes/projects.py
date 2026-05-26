"""Project CRUD routes + a read-only parse-preview endpoint.

Five mutating routes (`POST`/`PATCH`/`DELETE`) hold `app.state.save_lock`
during the orchestrator call (Phase 2.1 pattern) and route through
`commit_state_with_snapshot(app, reason=...)` (Phase 4 snapshot-per-op
invariant). Snapshot reasons: `create-project`, `edit-brief`,
`delete-project`.

`POST /api/projects/parse` is read-only — no lock, no snapshot — so the
frontend's debounced live preview can fire on every textarea keystroke
without contention.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clipfarm.brief import BriefParseError, parse_brief
from clipfarm.models import ClipFarmState, StrictModel
from clipfarm.projects import (
    ProjectSummary,
    create_project,
    delete_project,
    list_projects,
    update_project,
)
from clipfarm.routes.deps import commit_state_with_snapshot_locked
from clipfarm.store import WritesFrozenError

router = APIRouter(prefix="/api", tags=["projects"])


# ---------- Request / response models ----------------------------------------


class BriefBody(BaseModel):
    brief_md: str


class CreateProjectResponse(StrictModel):
    project_id: str
    snapshot: Optional[str]


class UpdateProjectResponse(StrictModel):
    project_id: str
    stale_tag_rows: int
    snapshot: Optional[str]


class DeleteProjectResponse(StrictModel):
    project_id: str
    dropped_tag_rows: int
    deleted_attempts: int
    snapshot: Optional[str]


class ParsePreviewResponse(StrictModel):
    name: str
    lines_count: int
    sections: list[str]
    tags: list[str]


class ProjectDetail(StrictModel):
    project_id: str
    name: str
    brief_md: str
    created_at: str
    script_lines: list[str]
    sections: list[str]
    tags: list[str]


# ---------- Helpers -----------------------------------------------------------


def _check_freeze(app) -> None:
    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit conflict"
            ),
        )


def _brief_error_400(e: BriefParseError) -> HTTPException:
    detail = {"error": str(e)}
    if e.line is not None:
        detail["line"] = e.line  # type: ignore[assignment]
    if e.column is not None:
        detail["column"] = e.column  # type: ignore[assignment]
    return HTTPException(status_code=400, detail=detail)


def _commit_with_reason_locked(app, reason: str) -> Optional[str]:
    """Locked-variant commit — caller MUST hold app.state.save_lock."""
    try:
        snap_path = commit_state_with_snapshot_locked(app, reason)
    except WritesFrozenError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap_path.name if snap_path is not None else None


def _project_detail(state: ClipFarmState, project_id: str) -> ProjectDetail:
    proj = state.projects[project_id]
    # Build a name → order_idx lookup once instead of doing the inner
    # linear scan inside `sort(key=...)` (Phase 5 review residue).
    section_order: dict[str, int] = {
        t.name: t.order_idx
        for t in proj.tags.values()
        if t.kind == "section" and t.parent_id is None
    }
    sections = sorted(section_order.keys(), key=lambda n: section_order[n])
    tags = sorted(t.name for t in proj.tags.values() if t.kind == "tag")
    script_lines = proj.script.lines if proj.script is not None else []
    return ProjectDetail(
        project_id=project_id,
        name=proj.name,
        brief_md=proj.brief_md,
        created_at=proj.created_at,
        script_lines=script_lines,
        sections=sections,
        tags=tags,
    )


# ---------- Routes ------------------------------------------------------------


@router.post("/projects/parse", response_model=ParsePreviewResponse)
def parse_preview_route(body: BriefBody) -> ParsePreviewResponse:
    """Read-only preview — runs the same parser the CRUD routes do, but
    doesn't touch state and doesn't snapshot. The frontend calls this on
    debounced textarea changes to show "the parser saw N lines / M sections."
    """
    try:
        parsed = parse_brief(body.brief_md)
    except BriefParseError as e:
        raise _brief_error_400(e)
    return ParsePreviewResponse(
        name=parsed.name,
        lines_count=len(parsed.script.lines) if parsed.script else 0,
        sections=parsed.sections,
        tags=parsed.tags,
    )


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects_route(request: Request) -> list[ProjectSummary]:
    state: ClipFarmState = request.app.state.clipfarm
    return list_projects(state)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project_route(project_id: str, request: Request) -> ProjectDetail:
    state: ClipFarmState = request.app.state.clipfarm
    if project_id not in state.projects:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    return _project_detail(state, project_id)


@router.post("/projects", response_model=CreateProjectResponse)
async def create_project_route(
    body: BriefBody, request: Request
) -> CreateProjectResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    try:
        parsed = parse_brief(body.brief_md)
    except BriefParseError as e:
        raise _brief_error_400(e)

    async with app.state.save_lock:
        pid = create_project(state, parsed, brief_md_source=body.brief_md)
        app.state.dirty = True
        snapshot = _commit_with_reason_locked(app, "create-project")

    return CreateProjectResponse(project_id=pid, snapshot=snapshot)


@router.patch("/projects/{project_id}", response_model=UpdateProjectResponse)
async def update_project_route(
    project_id: str, body: BriefBody, request: Request
) -> UpdateProjectResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    try:
        parsed = parse_brief(body.brief_md)
    except BriefParseError as e:
        raise _brief_error_400(e)

    async with app.state.save_lock:
        try:
            staled = update_project(
                state, project_id, parsed, brief_md_source=body.brief_md
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        app.state.dirty = True
        snapshot = _commit_with_reason_locked(app, "edit-brief")

    return UpdateProjectResponse(
        project_id=project_id, stale_tag_rows=staled, snapshot=snapshot
    )


@router.delete("/projects/{project_id}", response_model=DeleteProjectResponse)
async def delete_project_route(
    project_id: str, request: Request
) -> DeleteProjectResponse:
    app = request.app
    _check_freeze(app)
    state: ClipFarmState = app.state.clipfarm

    async with app.state.save_lock:
        try:
            dropped, deleted = delete_project(state, project_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        app.state.dirty = True
        snapshot = _commit_with_reason_locked(app, "delete-project")

    return DeleteProjectResponse(
        project_id=project_id,
        dropped_tag_rows=dropped,
        deleted_attempts=deleted,
        snapshot=snapshot,
    )
