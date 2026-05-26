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

**Goal.** The manual escape hatch when the 2-second silence heuristic gets it wrong: split / merge / extend / shrink / create / delete on base clips, with the propagation rules from spec → "Fix segmentation when the AI gets it wrong" enforced for `clip_project_tags` (tags) and `attempts[*].clips` (attempt references). After Phase 4, segmentation mistakes on `btc.0.4` are fixable in-app instead of requiring a manual `clipfarm.json` edit.

Tag and attempt pipelines don't exist yet (Phase 6 and Phase 8 respectively), so Phase 4 tests the propagation rules against **synthetic** `clip_project_tags` / `attempts` injected into state. That locks the write-side semantics before the read-side machinery shows up — both phases later become much smaller because the rules are already a single tested code path.

**Verification at the end of this phase (concrete, no manual UI inspection needed):**

- `uv run pytest` passes (target ~170+ tests; Phase 3.1's 111 + ~60 Phase 4 additions).
- `curl -X POST localhost:8765/api/clips/<clip_id>/split -d '{"split_at_sec": ...}'` produces two new clips with proper IDs, removes the original, and writes exactly one snapshot file to `.clipfarm/snapshots/`. State round-trips through reload.
- Each of `POST /api/clips/{id}/split`, `POST /api/clips/merge`, `PATCH /api/clips/{id}/boundaries`, `POST /api/sources/{id}/clips`, `DELETE /api/clips/{id}` exercises `commit_state_with_snapshot()` exactly once per call. **Snapshot count == op count** is a tested invariant.
- **Live verification on `btc.0.4`:** ingest, pick a mis-segmented clip (one we identify by inspection — the 91-clip baseline lets us pick a clip and split/merge it without losing track), apply a split via curl, confirm: (a) original clip gone, (b) two new clips with adjacent boundaries summing to the original, (c) snapshot file present in `.clipfarm/snapshots/`, (d) `curl /api/sources/4/transcript` reflects the change.
- **Synthetic-tag + synthetic-attempt propagation tests pass.** Each propagation rule from the spec is tested with hand-built `ClipProjectTag` + `Attempt` data injected into state.

### Scope

**Backend (`clipfarm/` package — new for Phase 4):**

- **`clipfarm/boundary.py`** — pure orchestration functions for each operation. All take `(state: ClipFarmState, ...args)` and mutate state in place; **none touch disk and none load transcripts** — every clip-producing function takes the source's already-loaded `WhisperTranscript` as a parameter (the route layer calls `load_transcript_for_source` once and passes it in). Keeps `boundary.py` I/O-free, mirrors the Phase 2/3 architectural seam.
  - `split_clip(state, clip_id, split_at_sec, transcript) -> tuple[str, str]` — split one clip into two at the given timestamp. Validates `clip.start_sec < split_at_sec < clip.end_sec` (raises `ValueError` otherwise — surfaced as 400). Allocates two new clip IDs via the same `{stem}__HH-MM-SS.mmm__HH-MM-SS.mmm` encoding as ingest. Removes the original clip from `state.clips`. Recomputes `transcript_text` for each new range from the supplied `transcript` (or stores `""` if `transcript is None` — footage-only). Runs tag propagation (clone with `stale=True`) and attempt propagation (assign to first half + `needs_review=True`).
  - `merge_clips(state, clip_ids: list[str], transcript) -> str` — merge ≥ 2 clips on the same source into one. Validates: same `source_id`, **no overlap** (each next clip's `start_sec >= prev.end_sec` after sorting — silence between clips is folded into the merged range; that's the spec's "merged because you didn't pause long enough" path). The merged range is `(min_start, max_end)`. Allocates one new clip ID, recomputes `transcript_text` over the new range from the supplied `transcript`, removes the originals, runs union-merge tag propagation (dedupe on `(project_id, project_tag_id, category)`) and attempt reassignment.
  - `adjust_clip_boundaries(state, clip_id, start_sec, end_sec, transcript) -> None` — extend or shrink an existing clip's `start_sec`/`end_sec`. Clip ID stays the same (spec invariant: ID is opaque after creation). Validates: `start_sec < end_sec`, both within source duration if known, **hard 400 on any overlap with adjacent clips on the same source** (no auto-shrink of neighbors — frontend handles "shrink neighbor and retry?" UX deliberately). Recomputes `transcript_text` for the new range. Calls `clamp_attempt_trims_for_clip(state, clip_id)` to keep per-attempt offsets coherent (see precise rules in `propagation.py` below).
  - `create_clip_from_range(state, source_id, start_sec, end_sec, transcript) -> str` — new clip with a fresh ID. Validates the range doesn't overlap an existing clip on the same source (hard 400 on overlap). Recomputes `transcript_text` from the supplied `transcript`. **Footage-only behavior**: if the source has `transcript_path is None` (or the route layer passes `transcript=None`), `transcript_text=""` and the clip is still created. The spec allows manual clip creation on transcript-less sources via numeric range input.
  - `delete_clip(state, clip_id) -> None` — remove clip from `state.clips`, drop matching `clip_project_tags`, mark every attempt that referenced this clip with `needs_review=True` but **leave the attempt's `clips[i].clip_id` pointing at the now-deleted ID**. The resolver / render layer (Phase 7+) detects `state.clips.get(clip_id) is None` and renders a "removed — pick a replacement" placeholder. (Don't silently drop the attempt-clip — the user needs to know there's a hole.) No transcript needed.

- **`clipfarm/propagation.py`** — pure helpers shared by `boundary.py`.
  - `clone_tags_to_pair(state, src_clip_id, dst_clip_ids: list[str], *, stale: bool) -> int` — clones every `clip_project_tags` row pointing at `src_clip_id` to each of `dst_clip_ids` with `stale=stale`. Returns count of rows cloned.
  - `union_merge_tags(state, src_clip_ids: list[str], dst_clip_id: str) -> int` — union-merge: collect every tag row across `src_clip_ids`, point them at `dst_clip_id`, dedupe on `(project_id, project_tag_id, category)`. Returns count after dedupe.
  - `drop_tags_for_clip(state, clip_id) -> int` — removes every `clip_project_tags` row pointing at `clip_id`.
  - `reassign_attempt_refs(state, src_clip_id: str, dst_clip_id: str, *, mark_needs_review: bool) -> int` — walks every `Attempt.clips[i]`, swaps `src_clip_id` → `dst_clip_id`. If `mark_needs_review=True`, also flips `Attempt.needs_review=True` on every affected attempt. Returns count of attempts touched.
  - `mark_attempts_needs_review_for_clip(state, clip_id) -> int` — for delete: don't reassign, just flip `needs_review=True` on every attempt referencing this clip. Returns count.
  - `clamp_attempt_trims_for_clip(state, clip_id) -> int` — Phase 1 advance-note stub goes live. For every `Attempt.clips[i]` referencing `clip_id`, clamps `trim_start_offset` / `trim_end_offset` so the effective range stays inside the (now updated) base `start_sec` / `end_sec`. Returns count of attempt-clips whose offsets were modified.
    - **Trim convention** (per spec): `trim_start_offset` is added to `clip.start_sec` to get the effective start. **Negative** values extend past the base into raw source range; **positive** values shrink inward. Same for `trim_end_offset` (negative extends past `end_sec`; positive shrinks). Negative offsets are bounded by source duration, not by the base clip — they don't need clamping against base boundaries.
    - **Clamp rules** — exactly four cases for what an `adjust_clip_boundaries` call can do to existing positive offsets:
      - **Base `start_sec` moves inward (later).** If `trim_start_offset > 0`, the effective start was already inward of the old base; the new base may now overshoot it. Clamp `trim_start_offset = max(0, old_effective_start - new_base_start)` so effective start still ≥ new base. If the old effective start was already ≥ the new base, the offset becomes the difference; if it was < the new base (because the user dragged the base past it), clamp to 0 (the trim collapses; effective start == new base).
      - **Base `end_sec` moves inward (earlier).** Symmetric. Clamp `trim_end_offset = max(0, new_base_end - old_effective_end)`.
      - **Base moves outward** (either side). Positive offsets are still fully inside the new base — no clamping needed. Negative offsets are still bounded by source duration — also unaffected. No-op for this side.
      - **Pathological clamp: effective_start ≥ effective_end after clamping.** Means the new base is so narrow that the existing positive trims collide. **Locked behavior: collapse both offsets to 0 for that `AttemptClip` (effective range == new base), emit a `WARNING` log naming `(attempt_id, clip_id)`, and continue.** The boundary adjustment is the user's explicit ask; we don't fail it on a downstream trim conflict. The flipped `Attempt.needs_review` flag (set by the same op) gives the UI a hook to surface "trim was reset on this attempt" later.

- **`clipfarm/routes/clips.py`** — new route module for the five operations.
  - `POST /api/clips/{clip_id}/split` body `{"split_at_sec": float}` → 200 with `{old_clip_id, new_clip_ids: [a, b], snapshot}` or 400 on invalid range, 404 on unknown clip, 409 on freeze.
  - `POST /api/clips/merge` body `{"clip_ids": [str, ...]}` → 200 with `{new_clip_id, merged: [...], snapshot}` or 400 if not same-source / not adjacent, 404, 409.
  - `PATCH /api/clips/{clip_id}/boundaries` body `{"start_sec": float, "end_sec": float}` → 200 with `{clip_id, start_sec, end_sec, snapshot}` or 400 on invalid range / overlap, 404, 409.
  - `POST /api/sources/{source_id}/clips` body `{"start_sec": float, "end_sec": float}` → 200 with `{new_clip_id, snapshot}` or 400 on overlap, 404, 409.
  - `DELETE /api/clips/{clip_id}` → 200 with `{deleted_clip_id, affected_attempts: int, dropped_tag_rows: int, snapshot}` or 404, 409.
  - Every route routes through `commit_state_with_snapshot(app, reason=...)` (the helper from Phase 1.1) — that's how the **snapshot-per-op invariant** is enforced. `reason` strings are kebab-case + searchable, matching the route name root: `"split-clip"`, `"merge-clips"`, `"adjust-boundaries"`, `"create-clip"`, `"delete-clip"`.

- **Shared helper extracted from `ingest.py`** — `transcript_text_for_range(transcript, start_sec, end_sec) -> str` becomes a public function used by both ingest (during initial segmentation) and `boundary.create_clip_from_range`. No behavior change; just stops the helper being private + duplicated.

**Frontend (`web/src/pages/Library.tsx`):**

- Hook off the existing `data-word-index` / `data-start` / `data-end` / `data-clip-id` attributes left in the transcript view.
- **Split UI:** clicking the gap *between* two words (a tiny hit area between word spans) opens a small popover anchored there: "Split clip here?" + a confirm button + Esc-to-cancel. On confirm, POST to `/api/clips/{id}/split` with `split_at_sec = (prev_word.end + next_word.start) / 2`. State refresh on success; toast + revert on failure.
- **Merge UI:** click a clip to select it, shift-click additional clips to multi-select, then press `m` to merge the selection. No "find the adjacent next clip" auto-detection — the user explicitly picks what's getting merged. Same shape as the backend (`clip_ids: list[str]`). A "Merge N clips" button appears in the panel when ≥ 2 clips on the same source are selected.
- **Extend / shrink UI:** select a clip, then `[` and `]` shift the *start* boundary by one word; `,` and `.` shift the *end* boundary by one word. (Symbol choice matches the Trim Mode future-ideas note in the spec.) Per-press: PATCH the new boundaries. Visual: the clip's left/right edge slides to the new word.
- **Create-from-scratch UI:** drag-select a word range in the transcript (uses the native browser text selection over the spans); a "Create clip from selection" button appears in a corner; click → POST to `/api/sources/{id}/clips`. v0 limitation: the selection has to be inside the transcript area; we don't try to handle cross-source selections.
- **Delete UI:** select a clip, then `Cmd`/`Ctrl + Backspace` → confirmation dialog ("you can restore from the .clipfarm/snapshots/ folder if you regret this") → DELETE. **v0: always confirm**; the "disable confirmation" setting is deferred until Settings has real persistence (currently a placeholder page). TODO comment in the handler points at the future hook.
- All five operations show a small toast on success ("Split → 2 clips" / "Merged 3 clips" / etc.) and on failure ("Server returned 400: clips not adjacent").
- After every op: `refetch /api/state` + `refetch /api/sources/{currentSource}/transcript` (which is cache-fast since the transcript itself didn't change). Selected clip becomes the new clip on split/merge/create, or `null` on delete.

**Tests:**

- `tests/test_boundary.py` (~25): the pure functions in `boundary.py` and `propagation.py`. No HTTP, no disk. Synthetic state with hand-built `Source`, `Clip`, `ClipProjectTag`, `Attempt` entries.
  - `split_clip`: in-range split produces 2 clips with right boundaries; midword split via `split_at_sec` is fine (we don't snap to word boundaries — that's frontend UX); out-of-range raises; original clip removed.
  - `merge_clips`: two-adjacent merge produces 1 clip with summed range; three-clip merge works; non-adjacent raises; cross-source raises; single-clip raises; out-of-order input gets sorted.
  - `adjust_clip_boundaries`: extend in both directions stays in place (ID-stable); shrink works; overlap with neighbor raises; out-of-bounds raises.
  - `create_clip_from_range`: new ID, no inbound refs, transcript_text populated from sidecar.
  - `delete_clip`: removes clip + tag rows + flips needs_review on affected attempts.
- `tests/test_propagation.py` (~15): synthetic tags + attempts; each propagation helper tested in isolation.
  - Split: `clone_tags_to_pair` with 3 tags on the original → 6 tag rows after (3 per new clip), all with `stale=True`; one attempt referencing original → both new clips ignored at first, then `reassign_attempt_refs` to the first half with `mark_needs_review=True`.
  - Merge: `union_merge_tags` dedupes on `(project_id, project_tag_id, category)` — two clips with overlapping tag sets produce N unique rows, not 2N. `reassign_attempt_refs` retargets every reference.
  - Delete: `drop_tags_for_clip` removes only matching rows. `mark_attempts_needs_review_for_clip` flips the flag on every affected attempt but leaves the attempt's `clips[i].clip_id` pointing at the now-deleted ID (the dangling-tombstone case the spec calls for).
  - Boundary adjust: `clamp_attempt_trims_for_clip` with synthetic attempts (no attempts in real state) — verify that an attempt with `trim_start_offset=5.0` referencing a clip whose `start_sec` just moved inward by 3.0 sees the offset clamped to `2.0`, not left dangling. **This is the test the Phase 10 advance note calls out as landing for real here, with synthetic data.**
- `tests/test_routes_clips.py` (~20): one happy-path + error-mode test per route.
  - **Snapshot invariant** is its own helper: `_count_snapshots_after_op(client, op_fn)` that wraps any route call, asserts exactly one new file appeared in `.clipfarm/snapshots/` matching the op's reason segment (`"split"`, `"merge"`, etc.). Every route's happy-path test runs through this helper. **Snapshot count == op count** is enforced uniformly.
  - 400s on invalid input (out-of-range split, non-adjacent merge, overlap).
  - 404s on unknown clip/source IDs.
  - 409s when `app.state.writes_frozen` is set.
  - Lock invariant carried from Phase 2.1: each route holds `app.state.save_lock` during the orchestrator call (use the same patch-and-inspect technique).
- `tests/test_ingest.py` gets one new case: extracted `transcript_text_for_range` is unchanged in behavior (regression check — ingest's clip texts before and after the extraction are byte-identical).

### Decisions locked from plan review

All six open questions resolved during plan review; behavior is locked in the function/route descriptions above:

1. **Split-at:** frontend hands a precise `split_at_sec` (typically the gap midpoint between two word spans). Server takes it at face value; no server-side word-boundary snapping.
2. **Merge:** accepts any non-overlapping ordering on the same source. Silence between clips is folded into the merged range. The merged clip's `transcript_text` is recomputed over `(min_start, max_end)`.
3. **Overlap policy on adjust/create:** hard 400 on any overlap with an adjacent clip. Frontend offers "shrink neighbor and retry?" as a deliberate UX, not a silent server behavior.
4. **Delete leaves attempt refs dangling:** `clips[i].clip_id` stays pointing at the deleted ID; affected attempts get `needs_review=True`. The "removed — pick a replacement" placeholder lands in Phase 7+.
5. **Snapshot cap stays at 50.** ~4.4MB at current scale; revisit if a regret-snapshot is ever lost.
6. **`delete_clip` route emits `affected_attempts` and `dropped_tag_rows` even when both are 0.** Forward-compatible response shape; avoids a future schema bump.

Architectural clarifications from plan review:

7. **`WhisperTranscript` is a parameter, not a side-channel.** Every boundary function that produces a clip whose `transcript_text` needs computing (`split_clip`, `merge_clips`, `adjust_clip_boundaries`, `create_clip_from_range`) takes `transcript: Optional[WhisperTranscript]` as an explicit argument. The route layer loads via `load_transcript_for_source` once and passes it in. `boundary.py` stays I/O-free — no cache imports, no sidecar reads — same seam as Phase 2/3.
8. **Footage-only clip creation supported.** When `transcript is None` (source has `transcript_path is None`), `transcript_text=""` and the clip is created normally. Matches the spec's allowance for manual clip creation on transcript-less sources.
9. **Pathological-clamp behavior locked.** When `clamp_attempt_trims_for_clip` produces `effective_start >= effective_end` for an attempt-clip, **both offsets collapse to 0** (effective range == new base), a `WARNING` log line names `(attempt_id, clip_id)`, and the boundary adjustment succeeds. The `Attempt.needs_review` flag (already set by the same op) gives the UI a future hook for "trim was reset on this attempt."

### Out of scope for Phase 4 (explicit)

- Per-attempt trim adjustments (Phase 10's `[` `]` `,` `.` keys on AttemptClip — different operation, different target).
- Real attempts or real tags being mutated by the app (Phases 6 + 8 — Phase 4 only tests the propagation rules with synthetic data).
- Auto-snap-to-word for split/create (frontend picks the timestamp; server takes it at face value).
- Trim Mode auto-replay (Future Ideas; Phase 4 ships the static keyboard nudges only).
- Undo beyond file-level snapshot revert (the spec's locked decision; no in-app undo system).
- Cross-source operations (merge across sources, etc. — every op is scoped to one source).
- Bulk operations (multi-clip delete, etc. — Future Ideas).

### Notes carried into later phases

- **Phase 6** (tagging) will write into `state.clip_project_tags` for the first time. The propagation helpers from `propagation.py` are ready and tested with synthetic data; Phase 6 just provides the writes, no re-implementation.
- **Phase 8** (premade attempts) will write into `state.attempts` for the first time. Same story — `reassign_attempt_refs` and friends are tested with synthetic attempts in Phase 4.
- **Phase 9** (live preview) will need the dangling-clip tombstone handling. The placeholder render path is sketched in Phase 4 but only takes effect once attempts exist — Phase 8/9 land the real UI.
- **Phase 10** (attempt editing) will exercise `clamp_attempt_trims_for_clip` with real attempts. The Phase 4 test asserts the function does the right thing on synthetic data; Phase 10 tests it end-to-end through the attempt-trim UI.

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
