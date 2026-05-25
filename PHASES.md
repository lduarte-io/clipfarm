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

## Phase 2 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 2 + Phase 2.1.

---

## Phase 3 — Library page (raw transcript browser)

**Goal.** Make `clipfarm.json`'s clips legible: pick a source from a left-rail list, see its whole raw transcript on the right with the auto-detected clip boundaries marked inline, and full-text-search across every transcript with click-to-jump results. After Phase 3, scanning `btc.0.4` to find a specific line is a couple of keystrokes instead of a 34-minute watch — the spec's "meaningful unblock for btc.0.4" moment.

**Verification at the end of this phase (concrete, no manual UI inspection needed):**

- `uv run pytest` passes (target ~90+ tests; Phase 2's 77 + Phase 3 additions).
- `curl localhost:8765/api/sources/<id>/transcript` returns a JSON shape with `{source_id, filename, segments[{start, end, words[{start, end, word}]}], clips[{clip_id, start_sec, end_sec}]}`. 404 for a missing source ID. 422 (or a documented sentinel) for a source whose `transcript_path is None`.
- `curl 'localhost:8765/api/search?q=self-custody'` returns `{hits: [{source_id, filename, clip_id, timestamp_sec, context_before, match, context_after}, ...], query: "self-custody", total: N}`. Case-insensitive substring. Empty query → 400. No-match query → `total: 0, hits: []`.
- **Real-data smoke test on btc.0.4:** with the dogfood folder ingested, `q=Bitcoin` (or another known-recurring word — verify against the actual transcript text) returns multiple hits, each carrying the right source filename + a timestamp that lines up with a real clip in the source.
- **Frontend smoke check** (manual, after `npm run build`): Library page now has a left rail with the source list and a main panel showing the selected source's transcript with clip boundaries visually marked. Search input above the rail; results list when a query is active; clicking a result navigates to the source + scrolls to the matching word. Sources_skipped on the ingest result gets a `<details>` expansion (carry from the Phase 2 reviewer).

### Scope

**Phase 3 kickoff cleanups (Phase 2 review residue):**

These are small and Phase 3 touches the Library UI anyway, so they ride along.

- Add `<details>` expansion for `sources_skipped` in `Library.tsx`'s ingest-result summary (matches `rejected` + `warnings` UX). Tiny.
- Recursive-folder-walk decision **locked here**: `ingest_folder` stays **flat** for v0 (matches the dogfood `05.19.26/` shape, which is flat). When Lillian organizes by date subfolders later, the recursive option is a Phase 5+ addition with an explicit `?recursive=true` flag — no auto-detection. Documented in the spec → "First-run / startup behavior" and called out in this phase's notes.
- The `_log_unknown_keys` dict-of-model heuristic refactor (Phase 1 follow-up #5) is **still deferred to Phase 5 kickoff** — Phase 3 doesn't add nested-model shapes that stress it, and Phase 5's `Script` model is the natural place to lock it in.

**Backend (`clipfarm/` package — new for Phase 3):**

- `clipfarm/transcripts.py` — sidecar loading + caching.
  - `load_transcript_for_source(source: Source) -> WhisperTranscript | None` reads + validates the sidecar via the existing `WhisperTranscript` model. Returns `None` if `source.transcript_path is None` or the file is unreadable (logs a warning).
  - An in-process LRU cache keyed by `(transcript_path, mtime)` so repeat fetches within a session are O(1). Cache cap: 32 transcripts (enough for the 18-source dogfood folder + headroom). Tests cover cache hit/miss + invalidation on mtime change.
  - Pure-ish: no I/O leakage into the orchestrator, all errors return `None` + log rather than raising.

- `clipfarm/search.py` — pure substring search.
  - `search_transcript(transcript: WhisperTranscript, query: str, *, context_words: int = 5) -> list[SearchHit]`. Case-insensitive substring against each word's text (after stripping the faster_whisper leading-space convention). Each hit carries: `word_index`, `timestamp_sec` (the matched word's `start`), `context_before` (joined N preceding words), `match` (the matched word's text), `context_after` (joined N following words).
  - **Word-level match, not segment-level.** Searching `"self-custody"` against a recording that says `"self custody"` won't match — that's the spec's locked v0 behavior ("substring match is fine"). Multi-word phrase matching is a Future Idea. The model: each word's text (post-strip) is checked with `query.lower() in word.lower()`. So `"custo"` matches `"custody"`; `"self custody"` (two words) does NOT match.
  - `SearchHit` is a Pydantic model on the orchestrator side; `clipfarm/search.py` returns it.

- `clipfarm/routes/transcripts.py` — `GET /api/sources/{source_id}/transcript`.
  - Loads via `transcripts.load_transcript_for_source`. 404 if `source_id` missing; 422 if the source has no transcript (frontend uses this to show "no transcript yet").
  - Returns `{source_id, filename, segments, clips}`. `clips` is computed from `state.clips` filtered by `source_id`, sorted by `start_sec`. Lets the frontend draw boundary markers without a second round-trip.

- `clipfarm/routes/search.py` — `GET /api/search?q=...&source_id=optional`.
  - Iterates over all sources (or the one named in `source_id`), loads each transcript via the cache, calls `search_transcript`, collects hits with the source's filename+source_id stamped on each.
  - 400 if `q` is empty or whitespace-only.
  - Optional `?limit=N` (default 200) to cap response size — easy guard against a query like `"e"` returning thousands of matches.
  - **Hits-not-found-fast handling:** searches that hit nothing return `{hits: [], total: 0}` cleanly. No special case in the frontend.

**Frontend (`web/`):**

- `Library.tsx` is restructured into a **two-column layout**:
  - **Left rail** (~280px): collapsible "Ingest" section at top (keeps the absolute-path input + button + result summary that exists today, but tucked into a `<details>` so it doesn't dominate the rail). Below it, the source list. Sources are clickable rows; the selected one is visually distinguished. Source rows show filename + clip count + duration + unavailable indicator (same data as today, denser).
  - **Main panel**: the raw transcript of the selected source.
    - Words rendered inline as a continuous flow (CSS `white-space: pre-wrap` so we keep the leading-space convention rather than splitting on spaces).
    - Each word is span-wrapped with `data-start` / `data-end` for click-to-preview later (Phase 9 hooks).
    - Clip boundaries marked as inline pill-shaped backgrounds: each clip's word range gets a tinted background + a visible left border at the start of the range. Adjacent clips get adjacent (but distinct) tints — alternating two colors so boundaries are obvious without being noisy.
    - When a clip is selected (clicked, or arrived from a search result), it gets a stronger highlight + scrolls into view.
    - "No source selected" empty state if nothing's selected.
    - "No transcript yet" empty state if the source has `transcript_path is None`.
  - **Top bar (above the two columns)**: search input + count of hits.
    - On every keystroke, debounced 200ms, fire `GET /api/search?q=...`.
    - Results appear inline below the search bar (not in a modal — flowing list with source filename + timestamp + context highlighting the match).
    - Click a hit: select that source in the left rail + scroll the main panel to the matched word + flash-highlight it.
- The other three pages (Project, Brief, Settings) stay placeholders.

**Tests:**

- `tests/test_transcripts.py` (~6): load_transcript_for_source returns parsed shape for a real path; returns None for missing path; returns None for unreadable file; cache hits don't re-read disk (use mtime stat as the cache key, prove with a patched `read_text`); cache evicts on mtime change; cache cap respected.
- `tests/test_search.py` (~10): empty transcript returns no hits; case-insensitive match; substring inside a word matches; multi-segment match; context_words bounds (3 words before/after even at segment boundaries); leading-space convention doesn't break the match (strip before compare); empty query raises ValueError (the route returns 400 from this); custom context_words.
- `tests/test_routes_transcripts.py` (~5): 404 for missing source_id; 422 for transcript-less source; happy path returns shape with words + clips; clips are sorted by start_sec; large source returns the expected segment count.
- `tests/test_routes_search.py` (~6): 400 on empty `q`; happy path with real ingest + synthetic transcript; case-insensitive; source_id filter narrows results; `limit` cap respected; no-match returns `{hits: [], total: 0}`.

### Open questions / assumptions

- **Substring vs token match.** Locked: word-level substring. `"custo"` matches `"custody"`. `"self custody"` (two-word phrase) does NOT match. Multi-word phrase + semantic search are Future Ideas.
- **Search-result context size.** Locked: 5 words before + 5 after by default. `?context_words=` lets the frontend ask for more if needed (none of the v0 UI uses it yet).
- **Cache strategy.** Locked: in-process LRU keyed by `(transcript_path, mtime)`, capacity 32. Cheap, no third-party dep. Invalidation on mtime change means if `transcribe.py` re-runs and the sidecar changes, the next search sees the new content. Server restart also clears it.
- **Recursive folder walk for ingest.** Locked: **flat-only for v0**. Matches the dogfood folder. Recursive flag (`?recursive=true`) is a Phase 5+ addition.
- **`_log_unknown_keys` heuristic refactor.** Still deferred to Phase 5 kickoff. Phase 3's new shapes (search hits, transcript view) aren't loaded through `load_state` — they're computed at request time, so the heuristic doesn't apply.
- **Hit click → source jump.** Selecting a hit is what changes the left-rail selection. No URL routing change (we stay on `/library` and use component state). Keeps the React Router footprint small.

### Out of scope for Phase 3 (explicit)

- Boundary correction (Phase 4) — clicking a clip in the transcript view highlights it; it does NOT yet open split/merge controls.
- Project creation (Phase 5).
- Live preview / video playback from a clicked clip (Phase 9).
- Semantic search via embeddings (Future Ideas).
- Multi-word phrase search (Future Ideas).
- Ingest history persistence (would deduplicate repeated rejection-noise on re-ingest — flagged in the Phase 2 review). Phase 3 keeps the rejection list scoped to the current ingest response.
- Browser-side highlighting / regex search (substring only).

### Notes carried into later phases

- **Phase 4** will hang split/merge/extend/shrink controls off the existing transcript view: clicking between two words opens a "split here?" popover, etc. The clip-range span wrapping in Phase 3's frontend should make this additive, not a rewrite.
- **Phase 9** will use the `data-start` / `data-end` attributes on each word `<span>` to seek the preview pane when a word is clicked. Don't strip those attributes in Phase 3.
- **Ingest-history persistence** (the Phase 2 reviewer's polish item) becomes relevant when Phase 3+ surfaces past ingest results in the UI. Defer until then.

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
