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

## Phase 1 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 1 + Phase 1.1.

---

## Phase 2 — Ingest pipeline

**Goal.** Point the app at a folder of `.mov` files (with their `.whisper.json` sidecars), have it produce `sources` + `clips` entries in `clipfarm.json`. After Phase 2, the Library page shows the ingested footage with clip counts; the raw-transcript browser comes in Phase 3.

**Verification at the end of this phase (concrete, no manual UI inspection needed):**

- `uv run pytest` passes (target ~40+ tests; Phase 1's 27 + Phase 2 additions).
- `curl -X POST localhost:8765/api/ingest -H 'content-type: application/json' -d '{"folder":"/Users/lillianduarte/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/Video/Scripts/mp4files/05.19.26"}'` returns `{"sources_added": N, "clips_detected": M, "rejected": [], "warnings": [...]}` and `clipfarm.json` now contains those sources + clips with the right shape.
- After ingest: `curl localhost:8765/api/state | jq '.sources | length, [.clips | to_entries[] | .value.source_id] | unique | length'` shows the right counts.
- Specifically for `btc.0.4.mov` (the dogfood video): a known number of clips show up (we record the empirical count in `COMPLETED_PHASES.md` after first ingest so regressions are visible).
- **Filename edge-case check**: every `.mov` from `05.19.26/` (including `cuddlingchai content.mov`, `is my face crooked??.mov`, `more test videos <3.mov`) successfully ingests — their `Source.path` round-trips through `clipfarm.json` and the file is still resolvable on re-load (`unavailable: false` after the integrity check).
- **`__`-filename rejection**: a renamed `bad__file.mov` (created by hand for the test) gets refused with a clear error message; the rest of the folder still ingests; the rejected entry appears in the response under `rejected`.
- **Schema-version refusal**: a sidecar with `schema_version: 2` causes the corresponding source to land in `rejected` with a message pointing at `transcribe.py`. The rest of the folder still ingests.
- **Re-ingest idempotency**: running the ingest twice against the same folder is a no-op the second time (no duplicate sources, no double-counted clips).
- **Benchmark line written**: after the first full ingest of `05.19.26/`, the time for `load_state()` end-to-end (read → migrate → validate → integrity-check) is captured and written into the Phase 2 entry of `COMPLETED_PHASES.md`. This is the empirical data point for "when does SQLite need to come?"

### Scope

**Backend cleanups (Phase 2 kickoff — folding in the Phase 1 review punch-list):**

These are small and benefit Phase 2's new code, so they ride along on this phase rather than spinning a separate 1.2 pass.

- New `clipfarm/routes/deps.py` holding `get_state(request)`, `commit_state_to_disk(app)`, and `commit_state_with_snapshot(app, reason)`. Routes (`state.py`, the new `ingest.py`, future ones) import from here. Removes the duplicate `_get_state` in `routes/state.py`. (Punch-list #3.)
- Remove the dead `WATCHDOG_DEBOUNCE_MS = 200` constant from `store.py` + `__all__`. (Punch-list #4.)
- Gate `POST /api/test/touch` behind `os.environ.get("CLIPFARM_TEST_ROUTES") == "1"`. The Phase 1 verification flow used it; future phases shouldn't expose it by default. (Punch-list #6.)
- Add a one-line comment in `run_source_integrity_check` documenting the `validate_assignment=False` assumption. (Punch-list #7.)
- New `tests/test_models_round_trip.py`: round-trip defaults for `Attempt`, `AttemptClip`, `Clip` — assert `continuity_score`, `premade_bucket`, `internal_pause_max_sec`, `tracks` all serialize as `null` by default, never an empty dict / missing field. Locks the spec invariant "v0 writers leave `tracks` null." (Punch-list #8.)

**Backend (`clipfarm/` package — new for Phase 2):**

- `clipfarm/ffprobe.py` — thin subprocess wrapper.
  - `probe_video(path: Path) -> dict` returns `{"fps": float|None, "duration_sec": float|None}`. On any subprocess failure (FFmpeg missing, file unreadable, malformed metadata) returns both fields as `None` and logs a warning naming the file.
  - Uses `ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,duration -of json <path>`. Parses `r_frame_rate` (often `"60/1"` or `"30000/1001"` → divide), and `duration` as a float.
  - Pure-Python parsing of the JSON output; no third-party dependency. Tests can patch subprocess.

- `clipfarm/segmentation.py` — pure function.
  - `segment_words_by_silence(words: list[WhisperWord], gap_threshold: float = 2.0) -> list[tuple[float, float]]` returns `(start_sec, end_sec)` ranges. A "silence gap" is `current_word.start - previous_word.end >= gap_threshold`. Each contiguous run becomes one range, bounded by the first word's `start` and the last word's `end`.
  - Pure function: no I/O, no state. Tests cover edge cases (empty list, single word, all-one-segment, contiguous-but-just-under-threshold, etc.).
  - Returns ranges, not Clip objects — keeps the segmentation logic decoupled from the model construction (the orchestrator builds `Clip`s).

- `clipfarm/ingest.py` — orchestrator.
  - `IngestRequest = TypedDict-ish` (or Pydantic) with `folder: Path`. (Future ideas: glob patterns, recursive flag.)
  - `IngestResult` Pydantic model:
    ```python
    class IngestResult(StrictModel):
        sources_added: list[str]      # filename
        sources_skipped: list[str]    # already-ingested
        sources_updated: list[str]    # transcript newly available
        rejected: list[IngestRejection]
        warnings: list[str]
        clips_detected: int
    class IngestRejection(StrictModel):
        filename: str
        reason: str                   # "filename-contains-__" | "schema-version-mismatch" | "transcript-malformed"
        sanitized_rename: Optional[str]
    ```
  - `ingest_folder(state: ClipFarmState, folder: Path) -> IngestResult` is the pure orchestration: walks the folder for `*.mov`, pairs each with its sibling `<stem>.whisper.json`, validates the sidecar through `WhisperTranscript`, rejects filenames containing `__`, probes fps/duration via `ffprobe`, segments transcripts into clips, and mutates `state` in-place. Returns the result summary.
  - **Re-ingest semantics:**
    - If `state.sources` already contains a source with this `path`, and it had `transcript_path is None`, and a transcript now exists → segment now, add the resulting clips. Mark `sources_updated`.
    - If it had a transcript already → skip entirely (no re-segment). Mark `sources_skipped`.
    - If brand new → add Source + segment + clips. Mark `sources_added`.
    - Sources whose files no longer exist in the folder are NOT removed (handled by the integrity check on load).
  - **Source-ID generation:** spec example uses string keys ("1", "2", ...). Use monotonically-increasing string integers starting from `max(existing_ids) + 1`. Phase 2 doesn't need UUIDs — keys are opaque.
  - **Clip-ID generation:** per the spec, `{source_stem}__{start_hms}__{end_hms}` where HMS is `HH-MM-SS.mmm`. The encoded form is for human readability at creation time; the ID is opaque afterward.
  - **`__` rejection:** a `.mov` whose **filename stem** (without extension) contains `__` is rejected with `sanitized_rename` proposing the stem with `__` → `_`. The rest of the batch still ingests. No auto-rename on disk — the user fixes it and re-runs.
  - **Whisper `duration` policy** (resolving the deferred question from the Phase 1 review): if the sidecar has `duration` set, use it; if missing, fall back to `ffprobe`'s `duration_sec`. If both are missing/None, log a warning, store `Source.duration_sec = None`, and continue. The decision is: don't fail loudly on a missing `duration` since `ffprobe` is the more authoritative source anyway — but always prefer the sidecar's value when present, so `transcribe.py`'s timing window matches what ClipFarm displays.

- `clipfarm/routes/ingest.py` — `POST /api/ingest`.
  - Body: `{"folder": "/absolute/path"}`.
  - Resolves the folder path, refuses if not absolute or not a directory (400 with a clear message).
  - Calls `ingest_folder(state, folder)`.
  - Persists via `commit_state_to_disk(app)` (the existing locked-save path).
  - Returns the `IngestResult` as JSON.
  - 409 if `app.state.writes_frozen` is set (defer to the existing freeze surface).

**Frontend (`web/`):**

- Library page (`web/src/pages/Library.tsx`) gets a real first version:
  - Text input for "absolute folder path" + "Ingest" button.
  - On submit, POSTs to `/api/ingest`, shows result summary (added/updated/skipped/rejected counts).
  - Lists existing sources from `/api/state` below the form: filename, fps, duration, clip count, `unavailable` indicator. Sortable by recording date later — for now, original insertion order.
  - No raw-transcript browser (Phase 3) — clicking a source does nothing yet.
  - Path-pick UX limitation noted in COMPLETED_PHASES.md: HTML's `<input type="file" webkitdirectory>` can't surface absolute filesystem paths from the browser sandbox, so a text input is the v0 choice. Phase 2.5+ could add a native Electron-style folder picker; not blocking.
- All other pages stay placeholders.

**Tests:**

- `tests/test_segmentation.py` — pure-function tests for `segment_words_by_silence`. Cover: empty input, single word, two words with sub-threshold gap (one range), two words above threshold (two ranges), boundary case (exactly `gap_threshold`), longer chain.
- `tests/test_ffprobe.py` — patches `subprocess.run` to return canned JSON. Covers: valid 60fps mov, 30000/1001 fractional fps, missing duration, ffprobe exit-nonzero, ffprobe binary missing.
- `tests/test_whisper_validation.py` — loads one real `.whisper.json` from `05.19.26/` and validates through `WhisperTranscript`. Plus a synthetic sidecar with `schema_version: 2` raises. Plus a malformed sidecar (missing `segments`) raises a Pydantic `ValidationError`.
- `tests/test_ingest.py` — uses a `tmp_path` folder with synthetic `.mov`s + sidecars (binary fixture mov files: zero-byte placeholders are fine, `ffprobe` will fail and we'll get `fps=None`/`duration_sec=None` which is the documented fallback). Covers:
  - Happy path: 2 paired mov+sidecar files ingest into 2 sources + N clips.
  - Transcript-less mov: ingests as Source with `transcript_path=None` and zero clips.
  - `__`-named mov: ends up in `rejected` with a sanitized-rename suggestion, rest of batch still works.
  - `schema_version: 2` sidecar: source goes to `rejected`, rest still works.
  - Re-ingest: second run is a no-op (counts unchanged).
  - Transcript-available-now (was None before): second run upgrades the source.
  - Filenames with spaces and special chars (synthesize names like `weird !? <3.mov`): round-trip through `clipfarm.json` cleanly.
- `tests/test_models_round_trip.py` — punch-list #8 cleanup; covered above.
- `tests/test_routes_ingest.py` — uses `httpx.AsyncClient` against the FastAPI app: 400 on relative path, 400 on missing folder, 200 with a real (synthetic) folder. Plus a 409-when-frozen case.

### Open questions / assumptions

- **Folder path UX.** Locked: absolute-path text input on the Library page. Browser sandbox makes a real OS folder picker tricky for v0; a personal localhost tool can accept typed paths. Future ideas can add an Electron-style picker.
- **Re-ingest is conservative.** Locked: never re-segments an already-segmented source. The user can delete clips manually (Phase 4) and re-ingest if they really want a fresh run. Avoids losing manual boundary corrections.
- **Source ID format.** Locked: monotonic string integers ("1", "2", ...). Phase 2 doesn't need UUIDs; if we ever need them, that's a migration.
- **`duration` source-of-truth.** Locked: sidecar `duration` preferred, falls back to ffprobe, may be `None`.
- **`__` rejection is at the filename stem level.** A path like `/Users/foo__bar/my.mov` doesn't trip the check — only the filename matters. Directory components can contain whatever.
- **Frame-rate parsing.** `ffprobe`'s `r_frame_rate` can be `"60/1"`, `"30000/1001"`, or numeric. Always parse as a fraction (split on `/`, divide). Store as float.

### Out of scope for Phase 2 (explicit)

- The raw-transcript browser UI (Phase 3).
- Boundary correction operations (Phase 4).
- Project creation (Phase 5).
- LLM tagging (Phase 6).
- The conflict-resolution modal (Phase 2's `POST /api/ingest` returns 409 if frozen; the modal lands later — for now, the user resolves by manually fixing the file).
- Drag-and-drop folder picker UI — text input is the v0 affordance.
- Re-segmenting an already-segmented source on re-ingest (user-driven workflow, Phase 4+).

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
