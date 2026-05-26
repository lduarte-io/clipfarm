"""POST /api/projects/{project_id}/tag — run a tagging job.

Holds `app.state.save_lock` for the entire batched LLM run, then commits
via the locked-variant helper so mutation + write happen in one critical
section. The synchronous orchestrator runs in a worker thread via
`asyncio.to_thread` so the event loop stays responsive to other routes
(e.g. `GET /api/state`) during the 5-minute LLM run.

**v0 deliberate choice notes** for the next implementer to find:

- Lock-held-for-5min blocks every other mutating route. Boundary
  correction, ingest, project edits all stall behind a tag run. Single-
  user single-tab v0 doesn't trigger this. The same trigger that makes
  it matter (multi-user, multi-tab, progress UI) is the same trigger to
  switch to a background-task model (job_id + polling, or SSE).
- `app.state.dirty = True` BEFORE the orchestrator call, not after. If
  we set it after, the watcher's `has_unsaved_changes()` reads False
  during the run, an external clipfarm.json edit routes to the silent-
  reload path instead of the freeze path, the orchestrator's local
  `state` reference is abandoned, and our end-of-run commit writes the
  reloaded-from-disk state — dropping every tag we just produced.
  Flipping dirty up front routes the watcher event to `on_conflict`
  (freeze + 409), which is the right outcome.
- Freeze-during-tagging: if the watcher fires mid-run (external
  clipfarm.json edit) and `writes_frozen` flips True, the locked commit
  at end-of-run raises `WritesFrozenError` → 409. All in-memory tags
  from this run are lost. User resolves the freeze, retries. This is
  correct behavior, not a bug.
- 502 on Ollama-unreachable first-batch: probed via `ping_ollama` BEFORE
  acquiring the lock so a down LLM doesn't tie up the save lock.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from clipfarm.llm import (
    DEFAULT_MODEL,
    chat_with_json_schema,
    ping_ollama,
)
from clipfarm.models import ClipFarmState
from clipfarm.routes.deps import commit_state_with_snapshot_locked
from clipfarm.store import WritesFrozenError
from clipfarm.tagging import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    TaggingResult,
    tag_project,
)

router = APIRouter(prefix="/api", tags=["tagging"])


@router.post(
    "/projects/{project_id}/tag",
    response_model=TaggingResult,
)
async def tag_route(
    project_id: str,
    request: Request,
    batch_size: int = Query(
        DEFAULT_BATCH_SIZE,
        ge=MIN_BATCH_SIZE,
        le=MAX_BATCH_SIZE,
        description=f"Clips per LLM call. {MIN_BATCH_SIZE}-{MAX_BATCH_SIZE}.",
    ),
    dry_run: bool = Query(
        False,
        description="Skip the LLM call; return batch counts only (no writes).",
    ),
) -> TaggingResult:
    app = request.app
    state: ClipFarmState = app.state.clipfarm

    # 404 on unknown project — front the orchestrator's KeyError before
    # we do anything expensive.
    if project_id not in state.projects:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id: {project_id}"
        )

    # Check freeze before acquiring the save lock — wasting a few seconds
    # on a doomed tag run is silly.
    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit "
                "conflict on clipfarm.json — resolve before tagging"
            ),
        )

    # Pre-flight: empty-brief 400 (the orchestrator raises ValueError
    # here too, but doing it pre-Ollama-ping saves a network call).
    project = state.projects[project_id]
    has_script = project.script is not None and bool(project.script.lines)
    has_sections = any(t.kind == "section" for t in project.tags.values())
    has_tags = any(t.kind == "tag" for t in project.tags.values())
    if not (has_script or has_sections or has_tags):
        raise HTTPException(
            status_code=400,
            detail=(
                f"project {project.name!r} has no script lines, sections, "
                f"or tags — add at least one before tagging"
            ),
        )

    # Ping Ollama before acquiring the save lock. If it's down, return
    # 502 immediately so the user knows to start the service rather
    # than wait through 15 batches of timeouts.
    if not dry_run and not ping_ollama():
        raise HTTPException(
            status_code=502,
            detail=(
                "Ollama is unreachable at OLLAMA_HOST. Is "
                "`brew services start ollama` running?"
            ),
        )

    # Real LLM client wired into the orchestrator. Tests inject a fake
    # by patching `clipfarm.routes.tagging.chat_with_json_schema`.
    def llm_client(messages, schema):
        return chat_with_json_schema(messages, schema, model=DEFAULT_MODEL)

    async with app.state.save_lock:
        # Flip dirty BEFORE the orchestrator runs, not after — see the
        # module docstring for the race this closes. Pre-LLM mutations
        # (stale-row drop) start happening inside `tag_project` itself,
        # so this is also true in the literal sense the moment the
        # orchestrator gets work to do.
        if not dry_run:
            app.state.dirty = True
        try:
            # `tag_project` is synchronous and the inner `httpx.post`
            # calls block for up to ~20s per batch. Running it on a
            # worker thread keeps the event loop free to serve
            # concurrent reads (`GET /api/state`, etc.) — important for
            # the future progress-UI Phase 8 will want. The save_lock
            # is still held across the await; mutation-under-lock holds.
            result = await asyncio.to_thread(
                tag_project,
                state,
                project_id,
                llm_client=llm_client,
                batch_size=batch_size,
                dry_run=dry_run,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if dry_run:
            # No mutation → no commit, no snapshot.
            return result

        if result.mutated:
            try:
                commit_state_with_snapshot_locked(app, "tag-clips")
            except WritesFrozenError as e:
                # Watcher fired mid-run + state was dirty by the time
                # the run finished → freeze flipped, can't commit. All
                # in-memory tags are lost. 409 → user retries after
                # resolving the conflict.
                raise HTTPException(status_code=409, detail=str(e))

    return result
