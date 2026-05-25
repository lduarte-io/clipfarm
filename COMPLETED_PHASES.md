# COMPLETED PHASES — ClipFarm build log

Phases move here from `PHASES.md` once Lillian has manually verified them. Each entry documents what was actually built, what assumptions were made (especially where the spec was ambiguous), and any deviations from the original plan. This file is the artifact that self-assessment and outside-session code review work from.

---

## Phase 2 — Ingest pipeline

**Verified by Lillian:** ⏳ pending

**Built (2026-05-25):**

- **Phase 2 kickoff cleanups** (punch-list residue from the Phase 1 review):
  - New `clipfarm/routes/deps.py` — `get_state`, `commit_state_to_disk`, `commit_state_with_snapshot` all live here. `app.py` and every route imports from `deps.py`; the duplicate `_get_state` in `routes/state.py` is gone.
  - `WATCHDOG_DEBOUNCE_MS = 200` dead constant removed from `store.py` (real interval is `_WATCH_POLL_INTERVAL_SEC = 0.5` in `watcher.py`).
  - `POST /api/test/touch` gated behind `CLIPFARM_TEST_ROUTES=1`. Default OpenAPI surface no longer carries it.
  - One-line comment in `run_source_integrity_check` documenting the `validate_assignment=False` assumption.
  - New `tests/test_models_round_trip.py` (6 tests): defaults for `Clip.tracks`, `Attempt.continuity_score`, `Attempt.premade_bucket`, `Attempt.needs_review`, `AttemptClip.internal_pause_max_sec` serialize as `null` (never `{}`, never missing). Parametrized round-trip through disk confirms exact value preservation.

- **Backend (new for Phase 2):**
  - `clipfarm/ffprobe.py` — `probe_video(path) -> {fps, duration_sec}`. Subprocess wrapper around `ffprobe -show_entries stream=r_frame_rate,duration:format=duration -of json`. Parses fractional fps (`30000/1001` → `29.97`). All failure modes (binary missing, exit nonzero, malformed JSON, `OSError`) return `(None, None)` and log a warning. Never raises.
  - `clipfarm/segmentation.py` — pure `segment_words_by_silence(words, gap_threshold_sec=2.0) -> list[(start, end)]`. No I/O. Tested with the threshold-boundary edge cases (sub-, equal-, super-threshold), empty input, single-word input, custom thresholds, and the spec's locked default of 2.0s.
  - `clipfarm/ingest.py` — orchestrator. Walks the folder, pairs `.mov` + `<stem>.whisper.json`, validates the sidecar via `WhisperTranscript`, rejects `__`-named files with a sanitized-rename suggestion, probes fps/duration, segments words into clips, mutates `state` in place. Returns `IngestResult` summary. **Re-ingest semantics:** new source → add+segment; existing source with transcript newly available → upgrade+segment; otherwise → skip. Sources whose files disappeared aren't auto-removed (integrity check handles them). `duration` policy: prefer sidecar value when present, fall back to ffprobe.
  - `clipfarm/routes/ingest.py` — `POST /api/ingest` with `{folder: <absolute path>}`. 400 on relative or missing path, 409 when `writes_frozen`, 200 with `IngestResult` JSON on success. Persists via `commit_state_to_disk(app)` only when something actually changed.

- **Frontend (`web/src/pages/Library.tsx`):** absolute-path text input + Ingest button. Result summary shows added/updated/skipped/rejected/clip counts, with collapsible rejection + warning details. Source list below: filename (mono), duration, fps, clip count, transcript status (`ok` / `footage-only`), `unavailable` indicator on missing files. Path-picker UX limitation captured: HTML's `<input type="file" webkitdirectory>` can't surface absolute filesystem paths from the browser sandbox, so a text input is the v0 affordance. An Electron-style native picker can land later.

- **Tests added (75 passing total — was 33 in Phase 1):**
  - `tests/test_ffprobe.py` (9): canned-subprocess tests for clean run, fractional fps, `0/0` fps, missing duration, exit nonzero, binary missing, malformed JSON, `OSError`. Plus a real-file smoke test against `btc.0.4.mov` (skipped if not present).
  - `tests/test_segmentation.py` (11): empty/single/contiguous inputs, above-/exact-/below-threshold gaps, multi-segment, custom thresholds, negative threshold raises, zero threshold splits every word, default-threshold-locked assertion.
  - `tests/test_whisper_validation.py` (6): real `btc.0.4.whisper.json` validates and carries leading-space word convention; minimal valid payload; missing `segments` defaults to `[]`; missing `start` raises; missing `word` field raises; unknown top-level key dropped silently at model boundary.
  - `tests/test_ingest.py` (11): happy path (2 pairs → 4 clips); transcript-less → footage-only; `__` rejection with sanitized rename; `schema_version=2` rejected, batch continues; malformed JSON sidecar rejected, source still added as footage-only; re-ingest idempotent; transcript-appearing-later upgrades source; filenames with spaces and special chars round-trip (`cuddlingchai content.mov`, `is my face crooked??.mov`, `more test videos <3.mov`); `__` in directory path is fine (only filename stem is constrained); dotted stem (`btc.0.4`) handled correctly; not-a-directory raises.
  - `tests/test_routes_ingest.py` (5): happy path through `TestClient` (lifespan runs), relative path → 400, missing folder → 400, freeze → 409, re-ingest through route is idempotent.
  - `tests/test_models_round_trip.py` (6): the kickoff-cleanup test mentioned above.

**Manual verification run (all green):**

Live ingest against the real dogfood folder (`05.19.26/`):

- `POST /api/ingest {"folder": "...05.19.26"}` → **200, 18 sources_added, 157 clips_detected, 0 rejected, ~1.3s total**.
- `btc.0.4.mov`: source_id `"4"`, **fps 30.0, duration 2059.84s (~34 min), 91 clips detected**. First clip starts at 4.37s (`"She makes me smile all the time..."`). This is the empirical clip-count baseline for `btc.0.4` — regressions on the segmentation should be visible against `91`.
- Special-char filenames (`is my face crooked??.mov`, `more test videos <3.mov`) ingested cleanly and round-trip through `clipfarm.json` without escaping issues.
- **Re-ingest idempotency:** second `POST /api/ingest` on the same folder returns `sources_added=[]`, all 18 in `sources_skipped`, `clips_detected=0`.
- **`__` rejection:** synthetic `/tmp/clipfarm_p2_synth/bad__file.mov` rejected with `sanitized_rename: "bad_file.mov"`; the rest of the batch (`good.mov`, `from_future.mov`) still ingested.
- **`schema_version=2` rejection:** synthetic `from_future.whisper.json` rejected with a clear message pointing at `transcribe.py`. The corresponding `from_future.mov` was still added as a footage-only source rather than disappearing.

**Benchmark:** `load_state()` over the full ingested state (18 sources, 157 clips, 88,413-byte `clipfarm.json`): **2.63 ms average over 10 runs** (warm). For the spec's scale concern (a single 30-min recording produces ~350 clips; the 05.19.26 folder hits ~6k clips at full transcription), linear extrapolation puts load time at ~100ms for 6k clips — comfortably within "snappy on startup" budget. **SQLite migration not urgent at the dogfood scale.** Worth re-measuring after one full week of real use.

**Assumptions made + deviations from the original plan:**

- **`duration` policy decided.** Sidecar's `duration` (from `transcribe.py`) wins when present; falls back to `ffprobe` duration; otherwise `None`. The deferred question from the Phase 1 review is now answered explicitly in code + spec-aligned. btc.0.4 stored as `2059.84s` — the sidecar value, not ffprobe's.
- **Sidecar problems don't kill the source.** A malformed or wrong-schema-version sidecar adds the source to `rejected` but ALSO registers the `.mov` as a footage-only source. Rationale: the user almost always wants to keep the source entry and re-run `transcribe.py` later. Losing the source on a sidecar problem would be surprising.
- **Acceptable video extensions widened to `{.mov, .mp4, .m4v, .mkv}`.** The spec calls out `.mov` for the dogfood folder but doesn't constrain the set. Phase 2 tolerates the common siblings — only `.mov` exists in `05.19.26/`, so no behavior change in practice, but the next folder Lillian drops might not be `.mov`-pure.
- **Source ID format locked.** Monotonic string integers (`"1"`, `"2"`, ...). Adequate for the scale; if we ever need UUIDs, that's a migration.
- **Clip-ID encoded form uses `HH-MM-SS.mmm` (hyphens, not colons).** Dashes are filesystem-safe; colons make some tools unhappy. The ID is opaque after creation either way, but the encoded form needs to round-trip through JSON keys + URL slugs eventually.
- **Frontend's path input is a text field, not a folder picker.** Browser sandbox can't supply absolute paths from `<input type=file>`. Documented as a known v0 constraint; an Electron wrapper or a native picker addon can land later. Not blocking for dogfood.
- **`test_routes_ingest.py` uses `TestClient` not `httpx.AsyncClient`.** Tried `ASGITransport` first — lifespan doesn't run with it, so `app.state.writes_frozen` was undefined. `TestClient` runs lifespan correctly. The async-ness of routes is still tested via `pytest-asyncio` for the lower-level store/save pieces.

**Open follow-ups for the reviewer to evaluate:**

1. **`_log_unknown_keys` dict-of-model heuristic** (deferred from Phase 1 review #5) is still in place. Phase 2 didn't add nested-shape models that would stress it (WhisperTranscript is consumed at the model boundary in ingest, not loaded through `load_state`). Worth doing before Phase 5 ships the `Script` model.
2. **Per-clip transcript text quality.** v0 strips leading/trailing whitespace on the assembled clip text. Faster_whisper's leading-space convention means we lose the leading word's space — that's intentional for display but worth flagging if Phase 3's transcript browser needs the raw form. The full transcript stays in `<stem>.whisper.json` and the by-source view (Phase 3) reads from there directly, so this is cosmetic on the clip card, not lossy.
3. **No clip-overlap invariant tested.** Adjacent segmentation ranges don't overlap by construction (each word belongs to exactly one range), but there's no explicit test asserting that across the full 05.19.26 ingest. Worth adding in Phase 3 or as part of Phase 4's boundary-correction tests.

**Files touched in Phase 2:**

```
NEW:
  clipfarm/ffprobe.py
  clipfarm/segmentation.py
  clipfarm/ingest.py
  clipfarm/routes/ingest.py
  clipfarm/routes/deps.py
  tests/test_ffprobe.py
  tests/test_segmentation.py
  tests/test_whisper_validation.py
  tests/test_ingest.py
  tests/test_routes_ingest.py
  tests/test_models_round_trip.py

MODIFIED (kickoff cleanups):
  clipfarm/app.py       — imports from deps.py; removed inline get_state and commit helpers; route inclusion adds ingest
  clipfarm/store.py     — removed dead WATCHDOG_DEBOUNCE_MS; documented integrity-check assumption
  clipfarm/routes/state.py  — uses deps.get_state; test/touch gated by CLIPFARM_TEST_ROUTES env var
  web/src/pages/Library.tsx  — first real implementation (was placeholder)
  web/dist/...          — rebuilt
```

---

## Phase 1.1 — Race fix + atomic snapshot-then-save

**Verified by Lillian:** ✅ 2026-05-25 (folded in alongside Phase 1).

**Two fixes from the reviewer's pass on Phase 1:**

1. **Hash-install race in `commit_state_to_disk` closed.** Original flow released the lock between writing the file and installing the new hash on the watcher; if the 0.5s poll fell in that window, the watcher saw an "external" change and would freeze writes. Fix: `save_state()` now takes an optional `post_write` callback that runs **inside the lock** with the serialized form's hash. `commit_state_to_disk` passes `watcher.update_last_known_hash` as `post_write`, so the hash install and the write are one critical section.
2. **`save_state_with_snapshot()` added.** Spec invariant says snapshot-then-save is a single locked critical section. Original `snapshot_before_destructive()` was sync and called outside the lock. New helper acquires the lock once, snapshots the pre-change on-disk file, atomic-writes the new state, installs the hash via `post_write` — all inside one `async with lock:` block. Routes that mutate base clips (Phase 4's first user) call this via `commit_state_with_snapshot(app, reason)` on `app.py`.

**Tests added (`tests/test_store.py`, 6 new — 27 total passing):**

- `test_post_write_called_inside_lock_with_correct_hash` — asserts the callback receives `hash_serialized(serialized)` and that `lock.locked()` returns True while the callback runs.
- `test_post_write_not_called_when_frozen` — if `WritesFrozenError` raises, the callback never fires (the watcher must not learn about a write that didn't happen).
- `test_save_with_snapshot_writes_old_state_to_snapshot_then_new_to_main` — establishes a baseline, applies a destructive save, asserts the snapshot file has the OLD content and the main file has the NEW content.
- `test_save_with_snapshot_no_baseline_returns_none_snapshot` — fresh file → snapshot returns None and the new state still lands.
- `test_save_with_snapshot_post_write_inside_lock` — same lock-held + correct-hash assertions for the snapshot variant.
- `test_save_with_snapshot_raises_when_frozen` — freeze blocks both the snapshot AND the write; neither side-effect occurs.

**Live re-verification:** 40 concurrent `POST /api/test/touch` against the new code → all 200s, file valid JSON, **zero "external write" events in the watcher log** (vs. the original code where the race window could trip the freeze). The reviewer's deferred punch-list items (`WATCHDOG_DEBOUNCE_MS` constant, duplicate `_get_state`, dict-of-model heuristic, `/api/test/touch` env-gate, integrity-check mutation comment, round-trip test for new optional fields, `WhisperTranscript.duration` policy) are out of scope for this cleanup — flagged for either a focused follow-up or Phase 2 kickoff.

**Files touched in 1.1:**

```
clipfarm/store.py     — added `post_write` param to save_state; new save_state_with_snapshot()
clipfarm/app.py       — commit_state_to_disk uses post_write; new commit_state_with_snapshot()
tests/test_store.py   — six new tests
```

---

## Phase 1 — FastAPI backend + frontend skeleton + JSON schema + safety scaffolding

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

- **Backend package `clipfarm/`** wired up against the revised spec + plan:
  - `models.py` — every entity from the data model, all with `extra="ignore"`. New product fields are declared: `Attempt.continuity_score`, `Attempt.premade_bucket`, `Attempt.needs_review`, `AttemptClip.internal_pause_max_sec`. `WhisperTranscript` (+ `WhisperWord`, `WhisperSegment`) declared for Phase 2 ingest. `ClipFarmState` carries a stubbed `model_validator(mode="after")` for `ClipProjectTag` uniqueness — early-returns at v0, one-line activation in Phase 6.
  - `store.py` — single entry point for `clipfarm.json`. `load_state()` reads → migrates → log+drops unknown keys → validates → integrity-checks. Two save APIs: `save_state(state, path, lock, *, writes_frozen=False)` (async, takes the lock, raises `WritesFrozenError` when frozen) for routes, and `save_state_sync()` for tests/startup. `snapshot_before_destructive()` writes to `.clipfarm/snapshots/<ISO>-<ms>-<hash4>__<reason>.json` and prunes to 50.
  - `watcher.py` — `PollingObserver` (not `Observer`) with a 0.5s poll interval. Self-write filtered by comparing the file's hash to the in-memory `last_known_hash`. Conflict path is exposed via the `WatcherCallbacks.on_conflict` callback — `app.py`'s impl flips `app.state.writes_frozen` and pushes onto a `queue.Queue`.
  - `app.py` — lifespan installs `app.state.{clipfarm, save_lock, writes_frozen, dirty, conflict_events, watcher}`. `commit_state_to_disk(app)` is the single seam routes use to persist; it respects the freeze flag. `get_state(request)` is the DI provider (also re-exported as a local `_get_state` proxy inside `routes/state.py` to avoid the import cycle with `app.py`).
  - `routes/state.py` — `GET /api/state`, `GET /api/health`, `GET /api/conflicts/pending` (counter + frozen flag for the Phase 2 modal to surface), `POST /api/test/touch` (used by the concurrent-save verification — bumps an off-schema counter on `app.state._touch_counter` and saves).
  - `migrations/__init__.py` — `CURRENT_VERSION = 1`, empty `_MIGRATIONS` list, `run_migrations()` runner. `v1_to_v2.py` placeholder.

- **Frontend `web/`** — Vite + React + Tailwind scaffold built to `web/dist/`. Four routed pages (Library / Project / Brief / Settings) with placeholder content. `vite.config.ts` proxies `/api/*` to `:8765` for dev mode. FastAPI mounts `web/dist/assets/` and serves `index.html` via the catch-all so React Router handles refreshes.

- **Tests (21 passing):**
  - `test_store.py` (10): atomic-save round-trip, atomic-write leaves no `.tmp`, empty-state on missing file, snapshot writes pre-state bytes, snapshot no-op on missing file, pruning keeps last `SNAPSHOT_LIMIT`, label sanitization, same-millisecond distinct filenames, **concurrent saves serialize under `asyncio.Lock`**, frozen save raises `WritesFrozenError`.
  - `test_load_unknown_keys.py` (2): top-level + nested unknown keys load successfully, warning emitted naming each, round-tripped state contains no unknowns.
  - `test_migrations.py` (4): no-op at current version, `needs_migration` helper, refuses downgrade, chained migrations apply in order.
  - `test_source_integrity.py` (3): missing source flips `unavailable=True`, restored source flips back, end-to-end through `load_state`.
  - `test_conflict_freeze.py` (2): `writes_frozen=True` blocks save, post-resolution unfrozen save writes.

- **Repo plumbing:** `.gitignore` covers `.DS_Store`, `clipfarm.json`, `.clipfarm/`, `web/node_modules/`, `web/dist/`, `__pycache__/`, `.venv/`. `.DS_Store` removed from tracking via `git rm --cached`. `README.md` covers prerequisites + dev commands.

**Manual verification run (all green):**

- `uv run uvicorn clipfarm.app:app --port 8765` boots cleanly.
- `GET /` returns the React shell (asset 200).
- `GET /api/state` returns the empty default shape when `clipfarm.json` is absent.
- External-edit reload check: three sequential edits to the on-disk JSON were each picked up within ~1.5s; the second edit added an unknown top-level key and the third added a nested unknown — both got `WARNING` log lines from `clipfarm.store` naming the exact dotted path of each dropped key (`_lillian_note`, `projects.3._secret_field`).
- Concurrent-save check: 20 parallel `POST /api/test/touch` calls all returned 200 with counters 1→20; the final on-disk file is valid JSON.
- `pytest`: 21 passed.

**Assumptions made + deviations from the original plan:**

- **`PollingObserver` over the default `Observer` on macOS.** The native FSEvents-backed observer is unreliable for rapid back-to-back single-file changes — verification on this machine showed the second edit never firing. PollingObserver with a 0.5s interval gives a deterministic per-poll diff at trivial cost (single `stat()` per cycle). Locked the choice in the watcher and called it out in a comment so it doesn't drift back. **Recommend the reviewer flag whether this should be promoted into the spec's "Decisions locked" section.**
- **`threading.RLock` (not `Lock`) inside the watcher.** Found during verification: `_maybe_fire_change` holds the lock then invokes `on_external_change`, which calls back into `update_last_known_hash`, which tries to re-acquire the same lock. With `threading.Lock` that's a permanent deadlock — the first event succeeded but the watchdog thread hung indefinitely afterward, so no subsequent edit was ever detected. RLock is reentrant; the only externally observable difference is that the thread doesn't hang. Comment in the constructor explains the why.
- **`StrictModel` keeps its name despite switching to `extra="ignore"`.** The name now slightly misleads (the model is no longer "strict" in the Pydantic sense). Left for now because every model in the file inherits from it and the rename is a churn-only edit — propose renaming in a focused PR if the reviewer cares.
- **`AsyncIO.Lock` not extended to the snapshot helper directly.** Spec says snapshot-then-save are one critical section. `save_state()` acquires the lock, but `snapshot_before_destructive()` is synchronous and currently called *outside* the lock by future destructive routes. Phase 4 (boundary correction) is the first place that needs that coupling — will tighten the API then (likely a `save_with_snapshot()` helper that acquires the lock once and does both inside). Phase 1 doesn't expose any destructive routes yet, so this seam doesn't matter at v0; flagged so the reviewer doesn't miss it.
- **`POST /api/test/touch` is shipped as a real route, not feature-flagged.** It mutates an off-schema counter (`app.state._touch_counter`) and persists via `commit_state_to_disk()`, so it doesn't dirty the JSON schema. It's tagged `[test]` in the OpenAPI doc. Will remove once the Phase 1 concurrent-save verification is no longer needed — flagged in a comment on the handler.
- **`asyncio_mode = "auto"`** in `pyproject.toml` so async tests don't need explicit `@pytest.mark.asyncio` decorations everywhere. Was an open call in the plan; locked here.
- **Snapshot pruning test had to mutate the file between snapshots.** Without changing the file content, every snapshot in a tight loop has the same `(ms, hash4)` tuple and collapses to one filename. Added a per-iteration whitespace tweak so the hash varies. Documented in the test. Not a behavior bug — just a test-construction note.
- **Test for `Source` round-trip explicitly sets `unavailable=True`.** The integrity check correctly flips `unavailable` to `True` on load for fake paths; the round-trip equality only holds if the in-memory side already reflects that. Test fixture sets it up front; the comment explains why.

**Open follow-ups for the reviewer to evaluate:**

1. Add the `PollingObserver` decision to the spec's "Decisions locked" if accepted.
2. Decide whether `StrictModel` rename is worth doing in a focused pass before Phase 2 lands more models.
3. Confirm the `commit_state_to_disk(app)` + `app.state` shape is the API the implementer should be using for the next phase's mutating routes — alternative would be a tighter `state_service` wrapper, but Phase 4 is the right point to introduce that if it's wanted.

**Files touched:**

```
pyproject.toml, .python-version, .gitignore, README.md
clipfarm/__init__.py
clipfarm/models.py
clipfarm/store.py
clipfarm/watcher.py
clipfarm/app.py
clipfarm/routes/__init__.py
clipfarm/routes/state.py
clipfarm/migrations/__init__.py
clipfarm/migrations/v1_to_v2.py
tests/__init__.py
tests/test_store.py
tests/test_migrations.py
tests/test_source_integrity.py
tests/test_load_unknown_keys.py
tests/test_conflict_freeze.py
web/package.json, web/index.html, web/vite.config.ts, web/tsconfig.json
web/tailwind.config.js, web/postcss.config.js
web/src/main.tsx, web/src/App.tsx, web/src/index.css
web/src/pages/{Library,Project,Brief,Settings}.tsx
```

---

## Phase 0 — Environment setup

**Verified by Lillian:** ✅ 2026-05-25 (low-stakes; commands were `brew install ollama ffmpeg uv && brew services start ollama && ollama pull llama3.1:8b`).

**Done (2026-05-25):**

- `brew install ollama ffmpeg uv` — installed all three. `ffprobe` ships in the same FFmpeg bundle (needed for Phase 2 fps probing).
- `brew services start ollama` — the ollama daemon is running on `localhost:11434`.
- `ollama pull llama3.1:8b` — model downloaded (~4.7GB, Q4_K_M quant). `curl localhost:11434/api/tags` returns the model in the list. Not exercised yet beyond presence — first real LLM call lands in Phase 6.
- `uv` is on PATH and used by `uv sync` to manage the Python environment.

**Note:** the spec said Python 3.11, the machine has 3.12 via pyenv. Spec + CLAUDE.md were updated to Python 3.12 in the same session before any code was written. No reason to install a second Python.

**Assumptions:** Lillian's existing `transcribe.py` continues to produce the sidecar shape pinned in spec → "Whisper transcript schema." Verified visually by sampling one of the `05.19.26/*.whisper.json` files; the full model-level validation happens in Phase 2.
