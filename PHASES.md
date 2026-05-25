# PHASES — ClipFarm build plan

The ClipFarm build order from `clipfarm-spec.md` is executed as discrete phases. **One phase at a time.** Stop after each for manual verification by Lillian. Each phase's plan is written here *before* execution; once verified, the entry moves to `COMPLETED_PHASES.md` with assumptions captured.

Phase numbering matches the spec's build order (steps 0–11).

---

## Workflow rules

1. **Plan before executing** non-trivial phases. Write the plan into the phase entry below: scope, files touched, assumptions, what's deferred, how to verify.
2. **Stop after each phase.** Wait for Lillian to verify before starting the next.
3. **Document assumptions in `COMPLETED_PHASES.md`** when moving an entry over — not just what was built, but what was assumed where the spec was ambiguous.
4. **Trivial phases still get moved** to `COMPLETED_PHASES.md` (even without a written plan) so the audit trail is complete.
5. **Each completed phase will be reviewed** by both a self-assessment in this session and a separate Claude code-review session. `COMPLETED_PHASES.md` is the artifact those reviews read.

---

## Phase 1 — FastAPI backend + frontend skeleton + JSON schema + safety scaffolding

> **⚠️ Spec + plan revised mid-Phase-1 (2026-05-25).** Re-read this whole entry, the spec's "Decisions locked" section, and CLAUDE.md's "Data model invariants" before resuming. Key changes since the original plan: single `load_state()` entry; `asyncio.Lock` around saves; snapshot filename gets ms + hash; `extra="ignore"` not `extra="forbid"` (log+strip on load); explicit `app.state`+DI, no module globals; concrete `curl`-based verification; stubbed `ClipProjectTag` uniqueness validator. Spec also gained four product fields (`continuity_score`, `premade_bucket`, `internal_pause_max_sec`, expanded `Attempt`/`AttemptClip`) and a `WhisperTranscript` model that Phase 2 will use — the Phase 1 models should already declare them.

**Goal.** A booting end-to-end stack: FastAPI on `localhost:8765` serving a React SPA, with all Pydantic models in place for the full data model, atomic JSON load/save, snapshot helper, watchdog file watcher, migrations runner, and source-file integrity check. No features yet — just the foundation everything else lands on.

**Verification at the end of this phase (concrete, no UI dependency):**
- `uv run uvicorn clipfarm.app:app --reload --port 8765` starts cleanly.
- `http://localhost:8765/` returns the React shell with empty routed pages (Library, Project, Brief editor, Settings) visible and navigable.
- `curl localhost:8765/api/state` returns the current `clipfarm.json` shape (or a synthesized empty default if the file doesn't exist).
- **External-edit reload check**: `curl /api/state` → hand-edit `clipfarm.json` (e.g. add a project) → wait ~500ms → `curl /api/state` again → second response reflects the edit. No UI inspection needed.
- **Unknown-key tolerance check**: hand-add `"_lillian_note": "hi"` to the top level → reload → server logs a warning naming the dropped key → next save round-trips without the key. No load error.
- **Concurrent-save serialization check**: hit a write endpoint (a tiny test-only `POST /api/test/touch` that bumps a timestamp is fine) twice concurrently → both saves complete, final file is valid, no half-written state, no missed write.
- `pytest` passes (initial tests cover atomic-save under the lock, snapshot helper + filename uniqueness under ms-resolution collision, migration runner, source-integrity check, log-and-strip behavior on unknown keys).

### Scope

**Backend (`clipfarm/` package):**

- `clipfarm/app.py` — FastAPI entry. Static files mount for the built React SPA. SPA-catch-all route for client-side routing. Single `/api/state` GET endpoint for now (returns the loaded `clipfarm.json` shape). Startup hook loads JSON, runs migrations if needed, runs source integrity check, starts watchdog, **installs the state container and the watchdog observer onto `app.state` (NOT module-level globals)**. Shutdown hook stops watchdog. A `get_state()` dependency provider reads `app.state.clipfarm` and is used by every route — no route ever calls `load_state()` directly after startup.
- `clipfarm/models.py` — Pydantic models for every entity in the data model. All models inherit `StrictModel` configured with **`extra="ignore"`** (the load_state diff-and-log pass surfaces dropped keys). Includes:
  - `Source`, `Clip`, `Project`, `ProjectTag`, `ClipProjectTag`, `Attempt`, `AttemptClip`, `VoiceAnnotation`, and the top-level `ClipFarmState` container.
  - **New product fields** (spec-revised): `Attempt.continuity_score: Optional[float]`, `Attempt.premade_bucket: Optional[Literal["best", "diagnostic"]]`, `Attempt.needs_review: bool`, `AttemptClip.internal_pause_max_sec: Optional[float]`.
  - `WhisperTranscript` model that pins the sidecar schema (`schema_version: int`, `segments[*].words[*]`) — declared in Phase 1 but only *used* by Phase 2 ingest. Lives here so the shape is defined before ingest reaches for it.
  - Categories as a `Literal["on-script", "related-but-different", "standalone-idea", "off-topic", "fragment"]`.
  - Tag kinds as `Literal["section", "line"]`.
  - `tracks: Optional[TracksOverride] = None` reserved per spec — `TracksOverride` defined but unused in v0 writers.
  - **Stubbed root_validator on `ClipFarmState`** for `ClipProjectTag` uniqueness on `(clip_id, project_id, project_tag_id, category)`. Empty/pass-through in Phase 1 (no tags exist yet); activated in Phase 6 when tags get written. Stub exists so the activation in P6 is one-line.
- `clipfarm/store.py` — JSON I/O, **single entry point for all `clipfarm.json` access**. Exports `load_state()` (read → run migrations → log+drop unknown keys → validate → return `ClipFarmState`) and `save_state(state)` (validate → serialize → atomic write under an `asyncio.Lock`). Nothing else opens the file. Pre-write snapshot helper `snapshot_before_destructive(reason: str)` runs inside the same lock as the save it precedes; copies the current file to `.clipfarm/snapshots/<ISO-timestamp>-<ms>-<hash4>__<reason>.json` and prunes to last 50. `<hash4>` is a 4-char hash of the file contents — defends against same-ms collisions in tests. Source integrity check sets `unavailable` on missing source files. `WATCHDOG_DEBOUNCE_MS = 200` to coalesce rapid filesystem events.
- `clipfarm/watcher.py` — `watchdog` observer wrapping a callback. Tracks the last successfully-written file hash; ignores self-writes by comparison. **Conflict detection**: if in-memory state has unsaved changes when an external write lands, the watcher (a) emits a conflict event onto an in-process channel for the UI (Phase 2 reads this), (b) sets `app.state.writes_frozen = True` so `save_state()` refuses to write until the user resolves. Phase 1 just *logs* the freeze + conflict; the modal is Phase 2.
- `clipfarm/migrations/__init__.py` — version constant `CURRENT_VERSION = 1` and the migration runner. Runner imports migration functions, runs them in version order, returns the migrated dict. Called from `load_state()`, not from routes.
- `clipfarm/migrations/v1_to_v2.py` — placeholder function `def migrate(d: dict) -> dict: return d`. Stays empty until we actually bump.
- `clipfarm/routes/state.py` — `GET /api/state` returns the current `ClipFarmState` as JSON via the `get_state` dependency. Future phases add more routes here.
- `pyproject.toml` — uv-managed Python project. Deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `watchdog`, `pytest`, `pytest-asyncio`, `httpx` (for test client). Phase 6+ deps (`ollama`, `sentence-transformers`, `rapidfuzz`) deferred — don't install yet.

**Frontend (`web/`):**

- Vite + React + Tailwind scaffold.
- React Router with four empty pages: `/library`, `/project`, `/brief`, `/settings`. Each renders a placeholder `<h1>` + a sentence saying the page is not yet implemented.
- A simple top nav with the four links + the app name.
- `npm run build` outputs to `web/dist/`. FastAPI mounts that path as static files.
- Dev workflow: `vite dev` proxies `/api/*` to `localhost:8765` so the React dev server can hot-reload while talking to the Python backend.

**Tests (`tests/`):**

- `test_store.py` — atomic save creates the file; round-trip serializes/deserializes through Pydantic; snapshot helper writes to `.clipfarm/snapshots/`; pruning keeps last 50; **two snapshots taken in the same millisecond produce distinct filenames** (hash suffix does its job); **two concurrent `await save_state(...)` calls both complete with a valid final file** (lock serializes them).
- `test_load_unknown_keys.py` — `clipfarm.json` with an extra `"_lillian_note": "..."` field at top level and inside a nested model loads without raising; warnings are emitted (caplog); the round-tripped state does not contain the unknown keys.
- `test_migrations.py` — runner with no migrations is a no-op; runner with one migration bumps version; refuses to downgrade.
- `test_source_integrity.py` — missing source file flips `unavailable: true`; restoring file flips it back.
- `test_conflict_freeze.py` — simulate an external write while in-memory is dirty → `app.state.writes_frozen` becomes `True` → subsequent `save_state()` raises (or returns a documented sentinel) instead of overwriting.

**Repo plumbing:**

- `.gitignore` — `.DS_Store`, `clipfarm.json`, `.clipfarm/`, `web/node_modules/`, `web/dist/`, `__pycache__/`, `.venv/`, `*.pyc`, `.python-version` (decide). Run `git rm --cached .DS_Store` once to stop tracking the existing one.
- `README.md` — one-paragraph project description + how to run dev mode locally.
- Phase 0 deliverables: ollama installed, llama3.1:8b pulled, ffmpeg installed (with `ffprobe`, which comes bundled), uv installed. Phase 0 is verified by `ollama list | grep llama3.1`, `ffmpeg -version`, and `ffprobe -version`.

### Open questions / assumptions

- **`clipfarm.json` location.** Assumption: it lives at the repo root (`/Users/lillianduarte/Desktop/clipfarm/clipfarm.json`). Spec says "single `clipfarm.json` at the project root" — confirmed.
- **Default `clipfarm.json` on first launch.** Assumption: if no file exists at startup, the app boots with an in-memory empty state but does NOT write the file until the first ingest or other write. This matches the spec's "drop zone on first launch" intent — the file's existence signals "real state exists."
- **Snapshot filename format.** Locked: `.clipfarm/snapshots/<YYYY-MM-DDTHH-MM-SS>-<mmm>-<hash4>__<reason>.json` (e.g. `2026-05-25T18-30-45-812-a3f2__split-clip.json`). Colons are filesystem-hostile; ms + hash defend against same-second + same-ms collisions; reason is a short kebab-case label.
- **Watchdog self-write filtering.** Locked: track the last successfully-written file hash in `app.state`; when watchdog fires, read + hash + compare. Match = self-write, ignore. Mismatch = external edit. If in-memory state is dirty: freeze writes + emit conflict event + log. If clean: reload silently.
- **Conflict UX surface.** Locked: Phase 1 detects, freezes writes, logs, and exposes the conflict event on a channel. The UI modal lands in Phase 2 along with the first user-facing routes.
- **Save concurrency.** Locked: an `asyncio.Lock` lives on `app.state.save_lock`; `save_state()` and `snapshot_before_destructive()` both acquire it. Pre-write snapshot + write happen inside one critical section.
- **Models config.** Locked: `extra="ignore"` everywhere; the loader does the diff-and-log pass. See spec → "Unknown-key tolerance."

### Out of scope for Phase 1 (explicit)

- Folder picker / ingest UI (Phase 2).
- Any LLM calls (Phase 6+).
- Any video preview or FFmpeg work (Phase 9, 11).
- Real page content beyond placeholder shells.
- The conflict-resolution UI modal (Phase 2 — Phase 1 just freezes + logs).
- Activating the `ClipProjectTag` uniqueness validator (Phase 6 — Phase 1 just stubs it).

---

## Phase 2 — Ingest pipeline

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Use the `WhisperTranscript` Pydantic model defined in Phase 1 to validate every sidecar at load. Refuse with a clear error if `schema_version != 1`.
- Probe `fps` and `duration_sec` via `ffprobe` (spawn as subprocess) for every source at ingest. On probe failure, log + record `fps=None` and continue.
- Reject source filenames containing `__` at ingest with a clear error; offer a sanitized-rename action ("`my__file.mov` → `my_file.mov`?"). See spec → "Source filename constraint."
- Transcript-less `.mov` ingest is supported (Source with `transcript_path=None`, no auto-detected clips). See spec → "Transcript-less sources are still ingestable."
- **Benchmark hook**: after ingesting the full `05.19.26/mp4/` folder once, time `load_state()` end-to-end (read + migrate + validate). Write the number to `COMPLETED_PHASES.md`. Cheapest empirical data on whether SQLite migration timing is "now" or "later."
- **Filename edge cases observed in the sample folder**: spaces (`cuddlingchai content.mov`), special chars (`is my face crooked??.mov`, `more test videos <3.mov`). All paths must survive shell-escape and JSON round-trip cleanly. Test with these names, not just clean ones.

## Phase 3 — Library page (raw transcript browser)

*To be planned before execution.*

## Phase 4 — Boundary correction

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Split / merge / extend / shrink / create / delete must each route through `snapshot_before_destructive()` before mutating state. Test that every op writes a snapshot.
- **Trim-clamp stub**: define a `clamp_attempt_trims_for_clip(state, clip_id)` function that walks every `Attempt.clips` referencing `clip_id` and clamps `trim_start_offset` / `trim_end_offset` to the new base bounds. Phase 4 calls it from extend/shrink (no attempts exist yet, so it's a no-op in practice), and Phase 10 has the failing test that proves it does the right thing once attempts exist. Stubbing it here means the call site already exists when attempts arrive.
- **Tag propagation tests** (no real tags yet, but the rule is testable with synthetic data): split clones tags with `stale=true`; merge unions and dedupes on `(project_id, project_tag_id, category)`; delete drops tag rows and sets `needs_review=true` on affected attempts.
- **Dangling-clip tombstone test**: an attempt whose `clip_id` no longer exists in `state.clips` must validate, load, and surface a "removed — pick a replacement" placeholder at render time (Phase 7+). The resolver detects it by `state.clips.get(clip_id) is None`. Test the round-trip + the resolver fallback.

## Phase 5 — Brief editor + project creation

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Replace `Project.script_json: dict` with a typed `Script` model: `class Script(StrictModel): lines: list[str]` (and a future-optional `sections` grouping). Spec's data-model example shows the loose shape, but typing prevents Phase 6 from drifting on what "the script" looks like.

## Phase 6 — Ollama tagging (batched)

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Activate the `ClipProjectTag` uniqueness root_validator stubbed in Phase 1. Once tags get written, enforce uniqueness on `(clip_id, project_id, project_tag_id, category)` at the model level.
- Plan for malformed LLM responses: retry-once on JSON parse failure, then mark the batch as "untagged — retry available" rather than aborting the whole tagging run. Don't pretend Ollama's JSON-schema mode is 100% reliable.
- **Voice annotation scope creep watchout**: the `VoiceAnnotation` model exists. The *feature* is v2+. Phase 6 should not start hooking it up.

## Phase 7 — Take grid view

*To be planned before execution.*

## Phase 7b — Script TOC view (primary assembly workflow)

*Promoted to v0 — see spec build order. To be planned before execution. Reuses Phase 7's data; different layout.*

## Phase 8 — Premade attempts generation

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Two buckets: `premade_bucket="best"` (3–5 ship-worthy) and `premade_bucket="diagnostic"` (browse-only). UI surfaces them separately.
- Compute and store `continuity_score` for each generated attempt; treat the stored value as a cache (recompute on edit, never trust blindly).

## Phase 9 — Live preview

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- **Cross-source preview blind spot**: btc.0.4 dogfood is single-source, so the alternating-`<video>` swap won't hit its worst case (~100–300ms cross-source latency) during early dogfooding. First multi-source attempt is the real stress test for whether MSE needs to come sooner than Stage 2. Don't declare success on btc.0.4 alone.
- `internal_pause_max_sec` on `AttemptClip`: when set, the resolver expands one attempt-clip into multiple `(start, end)` sub-ranges (each interior gap > max collapses to `max`). The swap-on-`ended` trick handles them the same as separate clips. Document this in the resolver so Phase 11 export doesn't reimplement the rule.

## Phase 10 — Attempt editing

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Frame-precise nudge (`Cmd+Alt = ±1 frame`) uses `Source.fps`. If `fps is None` for a source (ffprobe failed in Phase 2), fall back to 30 fps with a one-time UI warning per source. See spec → "Source fps detection."
- "Tighten internal pauses" toggle sets `AttemptClip.internal_pause_max_sec` to a sensible default (start with 0.5s). Single button, no slider — the full per-segment aggressiveness UI is v1.
- **Trim-clamp test now lands for real**: with real attempts existing, the Phase 4 `clamp_attempt_trims_for_clip()` stub gets its failing-then-passing test. Boundary correction that moves a clip's `start_sec` inward past an `Attempt.clips[i].trim_start_offset` must clamp the offset, not leave the attempt referencing impossible coordinates.

## Phase 11 — Export

*To be planned before execution.*
