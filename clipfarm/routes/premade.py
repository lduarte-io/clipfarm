"""POST /api/projects/{project_id}/premade-attempts — Phase 8 write
route.

Runs the premade-attempts orchestrator under the save lock, snapshots
once, returns the generated attempt IDs + a `naming_source` flag.

**Phase 6 invariants carried forward (locked patterns):**

- `app.state.dirty = True` flips BEFORE the orchestrator runs (Phase 6.1
  bug #1). An external `clipfarm.json` edit during the LLM-naming call
  routes to the freeze path → 409.
- Orchestrator wraps in `asyncio.to_thread` so the event loop stays
  responsive during the ~30s naming call (Phase 6.1 bug #2).
- `result.mutated` gates snapshot+commit (Phase 6.1 cosmetic #3) —
  no spurious snapshots when `generated_count == 0`.
- Single critical section: mutation + commit + watcher-hash install
  all happen inside one `async with save_lock` (Phase 6 architectural
  carry).

**No 502 from this route.** Canned-fallback naming makes Ollama
optional — if the LLM is down, every attempt gets its strategy's
canned name and `naming_source="canned"` lets the frontend surface
the difference. Skipping the ping avoids tying up the save lock on
a network roundtrip when we'd succeed anyway.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from clipfarm.llm import DEFAULT_MODEL, chat_with_json_schema
from clipfarm.llm_anthropic import chat_with_json_schema_anthropic
from clipfarm.models import Attempt, ClipFarmState, StrictModel
from clipfarm.premade import generate_premade_attempts
from clipfarm.routes.deps import commit_state_with_snapshot_locked
from clipfarm.settings import load_settings
from clipfarm.store import WritesFrozenError

router = APIRouter(prefix="/api", tags=["premade"])


class PremadeAttemptsResponse(StrictModel):
    """Response shape for POST /api/projects/{id}/premade-attempts.

    `generated_count == 0` is a NORMAL response — it means the project
    has on-script tags but no strategy produced anything (extremely
    sparse data). `reason` carries a user-facing message in that case
    (empty string on a normal run).
    """

    generated_count: int
    replaced_count: int
    new_attempt_ids: list[str] = Field(default_factory=list)
    naming_source: str  # "llm" / "canned" / "mixed"
    reason: str = ""
    attempts: dict[str, Attempt] = Field(default_factory=dict)


@router.post(
    "/projects/{project_id}/premade-attempts",
    response_model=PremadeAttemptsResponse,
)
async def premade_attempts_route(
    project_id: str,
    request: Request,
    replace_existing: bool = Query(
        True,
        description=(
            "When True (default), drop existing source='ai-premade' "
            "attempts for this project before generating fresh ones. "
            "Hand-built and forks are never touched."
        ),
    ),
) -> PremadeAttemptsResponse:
    app = request.app
    state: ClipFarmState = app.state.clipfarm

    if project_id not in state.projects:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id: {project_id}"
        )

    if app.state.writes_frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "writes are frozen due to an unresolved external-edit "
                "conflict on clipfarm.json — resolve before regenerating"
            ),
        )

    # Pre-flight: same check as the orchestrator, but turning the
    # ValueError into a clean 400 BEFORE we acquire the save lock.
    has_on_script = any(
        r.project_id == project_id and r.category == "on-script"
        for r in state.clip_project_tags
    )
    if not has_on_script:
        raise HTTPException(
            status_code=400,
            detail=(
                "project has no on-script tag rows — tag clips first "
                "(Brief page → Tag clips) before generating premade attempts"
            ),
        )

    # Pick the LLM client based on user settings. Same provider toggle
    # as the tagging route. Unlike tagging, this route does NOT ping
    # Ollama beforehand — the canned-fallback naming path handles
    # connection failures cleanly (canned names per strategy).
    settings = load_settings()
    tagging_settings = settings.tagging
    if (
        tagging_settings.provider == "anthropic"
        and tagging_settings.anthropic_api_key
    ):
        api_key = tagging_settings.anthropic_api_key
        anthropic_model = tagging_settings.anthropic_model

        def llm_client(messages, schema):
            return chat_with_json_schema_anthropic(
                messages, schema,
                api_key=api_key, model=anthropic_model,
            )
    else:
        ollama_model = tagging_settings.ollama_model

        def llm_client(messages, schema):
            return chat_with_json_schema(messages, schema, model=ollama_model)

    # Phase 8.1 — progress callback merges partial updates into the
    # global slot on app.state. Tolerant of mid-run resets (returns
    # without writing if the slot has already been wiped).
    def write_progress(info: dict) -> None:
        cur = app.state.premade_progress
        if cur is None:
            return
        cur.update(info)

    async with app.state.save_lock:
        # Phase 6.1 bug #1: flip dirty BEFORE the orchestrator runs so
        # a mid-run external edit routes to the freeze path.
        app.state.dirty = True
        # Phase 8.1: initialize progress slot. try/finally below wipes
        # it AFTER the commit so the UI doesn't see an idle gap.
        app.state.premade_progress = {
            "project_id": project_id,
            "phase": "starting",
            "elapsed_sec": 0.0,
        }
        try:
            # Phase 6.1 bug #2: orchestrator on a worker thread keeps
            # the event loop free.
            result = await asyncio.to_thread(
                generate_premade_attempts,
                state,
                project_id,
                llm_client=llm_client,
                replace_existing=replace_existing,
                progress=write_progress,
            )
            # Phase 6.1 cosmetic #3: only commit when there's something to write.
            if result.mutated:
                try:
                    commit_state_with_snapshot_locked(app, "premade-attempts")
                except WritesFrozenError as e:
                    raise HTTPException(status_code=409, detail=str(e))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            app.state.premade_progress = None

    # Build the attempts subset for the response (so the frontend can
    # render immediately without a follow-up state fetch).
    attempts_subset = {
        aid: state.attempts[aid]
        for aid in result.new_attempt_ids
        if aid in state.attempts
    }

    return PremadeAttemptsResponse(
        generated_count=result.generated_count,
        replaced_count=result.replaced_count,
        new_attempt_ids=result.new_attempt_ids,
        naming_source=result.naming_source,
        reason=result.reason,
        attempts=attempts_subset,
    )


@router.get("/premade/progress")
def get_premade_progress(request: Request) -> dict:
    """Returns the current premade-run progress, or {running: false} when idle.

    Polled by the Attempts page (and Project page CTA) every ~2s while
    a generate / regenerate is in flight (Phase 8.1).
    """
    info = request.app.state.premade_progress
    if info is None:
        return {"running": False}
    return {"running": True, **info}
