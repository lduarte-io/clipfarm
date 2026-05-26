"""FastAPI entrypoint.

Wires together the data-model invariants from CLAUDE.md:

- `app.state.clipfarm` — the in-memory `ClipFarmState`. Routes never call
  `load_state()` directly after startup; they read this via the `get_state`
  dependency provider in `clipfarm/routes/deps.py`.
- `app.state.save_lock` — single `asyncio.Lock` serializing all saves.
- `app.state.writes_frozen` — flipped True by the watcher when an external
  edit conflicts with in-memory dirty state. Phase 2 lands the modal that
  resolves it; Phase 1 just freezes + logs.
- `app.state.conflict_events` — thread-safe `queue.Queue` the watcher pushes
  conflict events onto. Phase 2's modal route reads it.
- `app.state.watcher` — the `StateFileWatcher`.

Routes that mutate state must:
  1. Check `app.state.writes_frozen` (or let `save_state()` raise).
  2. Mutate `app.state.clipfarm`.
  3. `await commit_state_to_disk(app)` — handles the save + the watcher
     last-known-hash update so the next watchdog event is filtered.

Frontend hosting: the built React SPA at `web/dist/` is mounted as static
files. The catch-all GET route serves `index.html` for any unknown non-API
path so client-side routing works on refresh. `/api/*` matches first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from clipfarm.models import ClipFarmState
from clipfarm.routes import clips as clips_routes
from clipfarm.routes import ingest as ingest_routes
from clipfarm.routes import projects as projects_routes
from clipfarm.routes import search as search_routes
from clipfarm.routes import premade as premade_routes
from clipfarm.routes import state as state_routes
from clipfarm.routes import tagging as tagging_routes
from clipfarm.routes import take_grid as take_grid_routes
from clipfarm.routes import transcripts as transcripts_routes
from clipfarm.routes.deps import (
    commit_state_to_disk,
    commit_state_with_snapshot,
    get_state,
)
from clipfarm.store import (
    DEFAULT_STATE_FILENAME,
    hash_serialized,
    load_state,
    serialize_state,
)
from clipfarm.watcher import StateFileWatcher, WatcherCallbacks

log = logging.getLogger("clipfarm")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = REPO_ROOT / "web" / "dist"


def _resolve_state_path() -> Path:
    override = os.environ.get("CLIPFARM_STATE_PATH")
    if override:
        return Path(override).resolve()
    return (REPO_ROOT / DEFAULT_STATE_FILENAME).resolve()


# --- Lifespan -----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    state_path = _resolve_state_path()
    app.state.state_path = state_path
    app.state.save_lock = asyncio.Lock()
    app.state.writes_frozen = False
    app.state.dirty = False
    app.state.conflict_events: queue.Queue = queue.Queue()

    log.info("clipfarm: loading state from %s", state_path)
    cf_state: ClipFarmState = load_state(state_path)
    app.state.clipfarm = cf_state

    last_known_hash = (
        hash_serialized(serialize_state(cf_state)) if state_path.exists() else None
    )

    def on_external_change(_: Path) -> None:
        try:
            new_state = load_state(state_path)
        except Exception:
            log.exception("clipfarm: failed to reload after external change")
            return
        app.state.clipfarm = new_state
        app.state.dirty = False
        new_hash = hash_serialized(serialize_state(new_state))
        watcher.update_last_known_hash(new_hash)
        log.info("clipfarm: reloaded state from disk")

    def on_conflict(path: Path) -> None:
        # Phase 1: freeze writes + push event + log. Phase 2 lands the modal.
        app.state.writes_frozen = True
        app.state.conflict_events.put(
            {"type": "external-edit-conflict", "path": str(path)}
        )
        log.warning(
            "clipfarm: external write to %s while in-memory state is dirty — "
            "writes frozen, awaiting user resolution",
            path,
        )

    def has_unsaved_changes() -> bool:
        return bool(app.state.dirty)

    watcher = StateFileWatcher(
        state_path,
        WatcherCallbacks(
            on_external_change=on_external_change,
            on_conflict=on_conflict,
            has_unsaved_changes=has_unsaved_changes,
        ),
    )
    watcher.update_last_known_hash(last_known_hash)
    watcher.start()
    app.state.watcher = watcher

    try:
        yield
    finally:
        watcher.stop()


app = FastAPI(title="ClipFarm", lifespan=lifespan)
app.include_router(state_routes.router)
app.include_router(ingest_routes.router)
app.include_router(transcripts_routes.router)
app.include_router(search_routes.router)
app.include_router(clips_routes.router)
app.include_router(projects_routes.router)
app.include_router(tagging_routes.router)
app.include_router(take_grid_routes.router)
app.include_router(premade_routes.router)


# --- Frontend hosting ---------------------------------------------------------

# `/assets` (Vite's hashed bundles) gets mounted directly when the build
# exists. The SPA catch-all below serves `index.html` for everything else,
# letting React Router handle the actual route.

if WEB_DIST.exists():
    assets_dir = WEB_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_catch_all(full_path: str):
    """Serve the React SPA. /api/* is matched first by the router include
    order; this only fires for non-API paths."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")
    index_file = WEB_DIST / "index.html"
    if not index_file.exists():
        return {
            "error": "frontend not built",
            "hint": "cd web && npm install && npm run build",
        }
    # If the requested path resolves to a real built file under WEB_DIST
    # (e.g. /favicon.svg), serve it; otherwise fall through to index.html so
    # client-side routing works on refresh.
    if full_path:
        candidate = (WEB_DIST / full_path).resolve()
        try:
            candidate.relative_to(WEB_DIST.resolve())
        except ValueError:
            candidate = None
        if candidate and candidate.is_file():
            return FileResponse(candidate)
    return FileResponse(index_file)


__all__ = [
    "app",
    "commit_state_to_disk",
    "commit_state_with_snapshot",
    "get_state",
]
