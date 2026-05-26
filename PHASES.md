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

## Phase 3 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 3 + Phase 3.1.

---

## Phase 4 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 4.

---

## Phase 5 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 5.

---

## Phase 6 — Verified ✅ 2026-05-25 (6.1 carries landed in Phase 7)

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 6. All five 6.1 carries (two bugs + three cosmetics) fixed in the Phase 7 kickoff cleanup pass.

---

## Phase 7 — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 7.

---

## Phase 7b — Verified ✅ 2026-05-25

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 7b.

---

## Phase 8 — Verified ✅ 2026-05-26

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 8.

---

## Phase 8.1 — Long-run progress UI (DRAFT — addresses Phase 6 open follow-up)

**Goal.** Make the tagging and premade-attempt runs visible while they're running. Both ops can take ~30s–5min on Llama 3.1 8B; the current UI is opaque "spinner that might be alive."

This was Phase 6's open follow-up #1 (*"Per-clip-batch progress UI — at 5.5 min for 91 clips, the synchronous spinner is genuinely uncomfortable. Real-world runtime suggests Lillian will hit this discomfort on dogfood."*) Lillian hit it on 2026-05-26.

### Scope

**Pattern**: progress lives on `app.state` as a single global slot per op type (single-user v0 — only one run at a time anyway since both ops hold the save lock). Orchestrators take an optional `progress: Callable[[dict], None]` callback that writes to that slot. New `GET` endpoints return the current value. Frontend polls every 2s while a button is in the run state.

**Backend:**

- **`clipfarm/app.py`** — initialize `app.state.tag_progress = None` and `app.state.premade_progress = None` in lifespan. None = idle; populated dict = running.
- **`clipfarm/tagging.py`** — `tag_project` gains `progress: Optional[ProgressCallback] = None` param. Calls `progress({"phase": "preflight"})` once before batching; `progress({"phase": "batching", "current_batch": i, "total_batches": N, "elapsed_sec": ...})` per batch start; `progress({"phase": "committing"})` after batches. Orchestrator never crashes from progress callback exceptions (swallow + log).
- **`clipfarm/premade.py`** — same shape: `progress` callback called at `preflight` → `running_strategies` → `naming` → `persisting`.
- **`clipfarm/routes/tagging.py`** — wraps progress writes:
  - Set `app.state.tag_progress = {"project_id": ..., "started_at": now, "phase": "starting", "elapsed_sec": 0}` before `to_thread`.
  - Pass `progress=lambda info: app.state.tag_progress.update(info) if app.state.tag_progress else None`.
  - Finally: `app.state.tag_progress = None` (resets to idle).
- **`clipfarm/routes/premade.py`** — same pattern for `premade_progress`.
- **New endpoints** (idempotent reads, no lock):
  - `GET /api/tag/progress` → `{"running": bool, ...info if running}`
  - `GET /api/premade/progress` → same shape
  - Both return `{"running": false}` when idle. Cheap, no snapshot side-effect.

**Frontend:**

- **`web/src/pages/Brief.tsx`** — when "Tag clips" is firing:
  - Poll `/api/tag/progress` every 2 seconds. Render a small progress panel below the button: `"Batch 3 of 10 · elapsed 1m 24s · ETA ~3m 30s"`. ETA = `elapsed * (total - current) / current` (rough).
  - Phase label maps to friendly text: `preflight` → "Pinging Ollama…", `batching` → batch N/M counter, `committing` → "Saving tags…".
  - Stop polling on POST resolve.
- **`web/src/pages/Attempts.tsx`** — when "Generate" / "Regenerate" is firing: poll `/api/premade/progress` every 2s, render a similar panel.
- **`web/src/pages/Project.tsx`** — same, when its CTA fires.

**Tests (~10 new):**

- `tests/test_tagging.py` — `progress` callback called per batch with correct shape; callback exceptions swallowed.
- `tests/test_premade.py` — same for premade orchestrator.
- `tests/test_routes_tagging.py` — `app.state.tag_progress` populated during run; resets to None on completion; resets on exception (try/finally).
- `tests/test_routes_premade.py` — same.
- A new `tests/test_routes_progress.py` (~4): both endpoints return `{running: false}` when idle; happy-path running state shape; concurrent reads don't block writes (via ThreadPoolExecutor, same pattern as Phase 6.1).

### Decisions locked

- **Single global progress slot per op type, not per-project.** Single-user v0 only ever has one tag run at a time (save_lock enforces). If Phase 12+ adds multi-user, switch to a `dict[project_id, progress]` then.
- **Polling, not SSE.** Polling is simpler, the existing event-loop responsiveness guarantee (Phase 6.1 bug #2) makes the GETs cheap. SSE is a Future Idea if dogfood feedback says 2s polling feels laggy.
- **Progress callback never raises into the orchestrator.** Exceptions are logged and swallowed — progress is observability, not correctness.
- **Phase labels carry user-facing text in the frontend, not the backend.** Backend emits machine-readable phase keys (`"preflight"`, `"batching"`, `"committing"`); frontend maps them to display strings. Keeps the backend free of UX copy.
- **No "Cancel" button in 8.1.** Cancelling mid-batch requires job tracking + interrupt-safe orchestrators — real work, not scope here. Add when dogfood says it's needed.

### Out of scope for 8.1 (explicit)

- Job IDs / multi-user concurrency / background-task architecture — Phase 12+ if needed.
- Cancel button.
- Per-clip granularity inside a batch — we know batch N of M, not which clip inside batch N.
- Progress for short ops (ingest, boundary correction). Add only if they ever become slow.

**Verification:** click "Tag clips" → see "Batch 1 of N · elapsed 0:05" within ~5 seconds. Watch the counter advance. Same for "Generate premade attempts."

**Goal.** First write-side phase after Phase 6. Generates the named candidate attempts the spec calls out — five ship-worthy strategies in the Best plausible bucket and three diagnostic groupings in the Diagnostic bucket — by filtering `clip_project_tags` into ordered clip lists, persisting them in `state.attempts` with the appropriate `premade_bucket`, and surfacing them in a new Attempts page (with a compact summary on the Project page). **This is the moment a project goes from "a labeled library" to "candidate videos you can pick from."**

### Decisions locked (resolved with Lillian 2026-05-25 before code work)

1. **Ship all 8 strategies in Phase 8** — both Best plausible (5) and Diagnostic (3). The data model already supports `premade_bucket={"best","diagnostic"}`; ship both panels populated rather than placeholder-then-fill.
2. **One batched LLM call for all 8 names, with canned fallback** — single Ollama call with all attempts in one prompt, JSON-schema constrained. Canned spec-example names ("best take of each line, in script order", etc.) used only if the LLM call fails entirely. ~30s round-trip instead of 8×30s.
3. **"Energy picked up" = words-per-second from Whisper timestamps**. Marked `# v0 heuristic — revisit when audio analysis lands` in code. Drop-the-strategy fallback if dogfood feedback says picks feel wrong.
4. **Regenerate replaces existing ai-premade attempts; never touches hand-built / forks.** Button label flips "Generate" → "Regenerate" when ai-premade attempts exist.
5. **Both — `/attempts` page + compact summary on `/project`.** Spec lists Attempts as a separate page; full list at `/attempts` (nav item between Script and Brief), compact 8-attempt summary panel on Project page that links over.

### Verification (manual)

- `uv run pytest` passes (target ~375 tests; 322 current + ~50 new for 8 strategies, continuity score, naming, orchestrator, route).
- `curl localhost:8765/api/projects/<id>/premade-attempts -X POST` against a real tagged project returns the new attempts in JSON with their `premade_bucket`, computed `continuity_score`, named clip lists. State file gains entries under `attempts`.
- Re-running the POST replaces the ai-premade attempts (count may stay the same or drop if a diagnostic grouping no longer applies; names + clip lists may change).
- **Real-data smoke on btc.0.4** (after Phase 6 tagging): all 4 single-result strategies (best_per_line, longest_contiguous, shortest_complete, energy_shift) produce a non-empty attempt. `near_one_take` produces ≥ 1 separate attempt (likely 1–3). Each best-plausible attempt has a distinct clip list. `continuity_score` for "longest contiguous take" is ≥ 0.8 AND for every `near_one_take` attempt is ≥ 0.9 (sanity check that the near-one-take strategy is preserving contiguity by returning separate attempts rather than splicing). At least one diagnostic grouping populates (likely `ad_libbed` since btc.0.4 has off-script clips around on-script takes).
- Frontend: new `/attempts` page lists attempts grouped by `premade_bucket` ("Best plausible" + "Diagnostic"). Each card shows name, source badge, continuity-score bar, clip count, total runtime. Clicking expands the clip list (filenames + timestamps). Project page (`/project`) shows a "Generate premade attempts" CTA when none exist; once present, the CTA flips to "Regenerate" and a compact 8-row summary panel appears with a "See all" link to `/attempts`.

### Scope

**Pure-function strategy module: `clipfarm/strategies.py`**

Each strategy is a pure function returning `list[StrategyResult]` where each `StrategyResult` carries `(name_hint, ordered_attempt_clips, premade_bucket)`. Returns `[]` when the project's tagged-clip set isn't rich enough. The orchestrator collects all results, deduplicates trivially-equal clip lists across strategies, and passes them to the naming + persistence step.

**Best plausible (5 strategies, `premade_bucket="best"`):**

- `best_per_line_in_script_order(state, project_id)` — for each line tag in `order_idx` order, pick the highest-confidence on-script clip. Skip lines with no on-script match. Result is ordered by script-line order. Usually low continuity, high completeness. **Always returns at most 1 result.**
- `longest_contiguous_take(state, project_id)` — over every (source, contiguous-clip-run) window in the project's on-script clips, find the run that maximizes total runtime AND covers the most distinct line tags. Return that run as-is in source-order. By definition continuity ≈ 1.0. **Always at most 1 result.**
- `near_one_take(state, project_id)` — find contiguous-in-source runs that hit ≥ 70% of script lines on-script with ≤ 2 fragment clips inside. **Return up to 3 SEPARATE attempts**, one per qualifying run, ranked by line-coverage then runtime. Each attempt's clip list is the source-order clip sequence of that single run — so each carries continuity ≈ 1.0 (the whole point: high-continuity straight-throughs). Spec wording matches: *"the 3 TIMES I said it in almost one take"* — plural takes, three viewable candidates, not a Frankensteined splice that would tank continuity. **Result count 0–3.** Ripple effect: best-plausible bucket can have 4–7 attempts, not always 5 (4 single-result strategies + 0–3 near-one-take). Naming differentiates them: "the near-one-take from N:NN-N:NN" or similar.
- `shortest_complete(state, project_id)` — like `best_per_line` but picks the SHORTEST on-script clip per line. Every line covered, minimum total runtime. For length-constrained cuts and shorts. **Always at most 1 result.**
- `energy_shift(state, project_id)` — compute words-per-second for each on-script clip from the Whisper word timestamps (cached). For each line, pick the clip with the highest words-per-second. **Marked `# v0 heuristic — revisit when audio analysis lands` in code.** **Always at most 1 result.**

**Diagnostic groupings (3 strategies, `premade_bucket="diagnostic"`):**

- `started_with_line(state, project_id)` — for each line tag, find every source where the FIRST on-script clip in source-time has that `project_tag_id`. Each non-empty cluster becomes ONE diagnostic attempt: concatenate those takes' full clip-runs in source-order. Cap at the top 3 lines by cluster size to avoid surfacing a "started with line 7 once" cluster of just one take. **Variable result count (0–3).**
- `skipped_line(state, project_id)` — for each line tag N, find sources where the project has on-script clips for at least 60% of OTHER lines but NOT line N. Each non-empty grouping becomes ONE diagnostic attempt: the takes-that-skipped-line-N concatenated in source-order. Cap at top 3 most-skipped lines. **Variable result count (0–3).**
- `ad_libbed(state, project_id)` — find sources where there's an on-script run + substantial off-line content (related-but-different / standalone-idea clips) intermixed. Build an attempt per source-take that has ≥ 2 such ad-lib clips around its on-script clips. Concatenate in source-order so the user sees the on-script delivery WITH its ad-libs preserved. Cap at top 3 by ad-lib clip count. **Variable result count (0–3).**

Diagnostic strategies are bounded at 3 results each → max 9 diagnostic attempts. Best-plausible can have up to 7 attempts (4 single-result strategies + 3 near-one-take), so the ceiling is **16 attempts per project**. Reality on a single-source project (btc.0.4) is likely 4–6 best-plausible + 1–3 diagnostic = 5–9 total attempts.

**Continuity score function: `clipfarm/continuity.py`**

- `compute_continuity_score(state, attempt_clips) → float in [0.0, 1.0]` — pure function. Walks the attempt's clip list, groups consecutive clips into "runs" where each run satisfies: same `source_id` AND the next clip's `start_sec` ≥ current clip's `end_sec` (i.e., progressing forward in source-time, no jumping back). Sum the runtime of each run; continuity = `max_run_runtime / total_attempt_runtime`. 1.0 = entirely one run; 0.0 not actually achievable (a single clip is itself a run of 1, giving runtime / runtime = 1.0; so the minimum on a 2+-clip attempt is `min_clip_runtime / total_runtime`).
- Recomputed on attempt write (the orchestrator caches into `Attempt.continuity_score`). Recomputed by future Phase 10 attempt-edits. The on-disk field is a cache per the data-model invariant ("readers should be willing to recompute").

**LLM naming: `clipfarm/attempt_naming.py`**

- `name_attempts(attempts_summary, llm_client) → dict[attempt_index → name]` — single LLM call with ALL generated attempts (typically 5–14) in one prompt. The summary per attempt: strategy id, `premade_bucket`, ordered list of `(line_name, clip_transcript_first_30_chars)`, `continuity_score`. The prompt asks for one 5–12-word name per attempt in Lillian's voice, JSON-schema constrained.
- Per-strategy canned fallback names — one canned name per strategy id, matching the spec's examples verbatim. Used when the LLM call fails entirely OR for any individual attempt whose LLM-generated name fails validation (empty string, > 200 chars, etc.). Keeps the run end-to-end even when Ollama is down.

**Orchestrator: `clipfarm/premade.py`**

- `generate_premade_attempts(state, project_id, *, llm_client, replace_existing=True) → list[Attempt]` — runs every strategy, computes continuity, batches into one LLM-naming call, builds `Attempt` objects with `source="ai-premade"`, `premade_bucket="best"`, names from the LLM (or canned fallback). Replaces existing `source="ai-premade"` attempts if `replace_existing=True`. Mutation only; the route layer handles snapshot + lock + commit (Phase 6 pattern).
- Raises `KeyError` on unknown project, `ValueError` if the project has zero on-script tags (route → 400 with a clear "tag clips first" message).

**Route: `clipfarm/routes/premade.py`**

- `POST /api/projects/{project_id}/premade-attempts` → response shape:
  ```json
  {
    "generated_count": 0-16,
    "reason": "...",
    "attempts": [Attempt, ...]
  }
  ```
  `reason` is always present (empty string when generation succeeded normally; a user-facing message when `generated_count == 0`).
- **Error / edge-case mapping:**
  - **404** unknown project.
  - **400** project has zero `clip_project_tags` rows OR zero on-script tags. Detail: "tag clips first — no on-script matches exist for this project."
  - **200 with `generated_count: 0`** — the project has on-script tags but every strategy returned `[]` after dedup. Theoretical edge (with on-script tags, `best_per_line` and `shortest_complete` will almost always produce something) but we don't 4xx for "you did everything right, the data is just sparse." `reason` field carries a user-facing message like "Only X on-script clips found; need ≥ Y for premade attempts. Tag more clips and re-run." Frontend reads `reason` for the empty-state copy.
  - **502** Ollama unreachable (only checked when we're about to make the naming call; canned fallback still works without LLM, so this only fires if we want LLM names AND ping fails — actually, since canned fallback is the safety net, the route SKIPS the Ollama ping entirely and lets `attempt_naming` fall back to canned on connection failure. **No 502 from this route.** Single tradeoff: user might not realize their attempts have canned names instead of LLM names; the response includes `"naming_source": "llm" | "canned"` so the frontend can surface it.).
  - **409** writes frozen.
- `async with save_lock: { mutate via asyncio.to_thread; commit_with_snapshot_locked }`. Snapshot reason `"premade-attempts"`.
- `app.state.dirty = True` flips inside the lock-held block BEFORE the orchestrator runs (Phase 6.1 bug #1 rule carries forward as the project pattern).
- `mutated` gate (Phase 6.1 cosmetic #3): only commit when at least one attempt was actually written. `generated_count == 0` skips the snapshot.

**Frontend: `web/src/pages/Attempts.tsx` (new) + `web/src/pages/Project.tsx` (touched) + `web/src/App.tsx` (route + nav)**

- **New page at `/attempts`.** Lists attempts for the active project. Two sections: **Best plausible** (`premade_bucket="best"` + hand-built/forks with `premade_bucket=null`) and **Diagnostic** (`premade_bucket="diagnostic"`).
  - Per-attempt card: name, `source` badge (`ai-premade` / `hand-built` / `fork`), `premade_bucket` chip, continuity-score horizontal bar (color: green ≥ 0.8, amber 0.4–0.8, red < 0.4), clip count, total runtime.
  - Click an attempt → expanded view: full ordered clip list (filename + timestamp per row, transcript snippet on hover). No editing UI yet (Phase 10).
  - "Regenerate premade attempts" button at the top with a confirmation modal when ai-premade attempts already exist ("This will replace the 8 existing AI-generated attempts. Hand-built attempts and forks are not touched. Continue?").
- **`web/src/App.tsx`** — `/attempts` route + new "Attempts" nav item between "Script" and "Brief".
- **`web/src/pages/Project.tsx` (touched)** — when ai-premade attempts exist for the active project, render a **compact summary panel** above the Take Grid showing **best-plausible attempts only** (4–7 rows depending on what strategies produced). Diagnostic attempts are intentionally NOT in this panel — diagnostic is browse-only exploration ("what patterns did I record?"), Project page is the assembly workflow ("what candidates are ready?"); mixing them muddies the mental model. Each row: name + continuity-bar + clip count, clicking navigates to `/attempts` and scrolls to that attempt. A "See all attempts (including diagnostic) →" link at the bottom of the panel jumps to `/attempts`. When NO attempts exist, render a single "Generate premade attempts" CTA instead — clicking POSTs to `/api/projects/{id}/premade-attempts` then navigates to `/attempts`.
- Empty states: no projects → link to Brief; no tags → link to Brief's "Tag clips"; no attempts → CTA to generate.

**Tests (~50 new):**

- `tests/test_strategies.py` (~24): each of the 8 strategies tested in isolation against synthetic state. ~3 tests per strategy on average. Covers happy path, empty inputs, edge cases (one source has all the on-script clips contiguously → that's the longest-contiguous winner; partial line coverage; tied confidences; diagnostic caps at 3 results each).
- `tests/test_continuity.py` (~6): pure formula tests — single-clip attempt = 1.0, two consecutive same-source clips = 1.0, two clips from different sources = `max / total`, three clips with one re-ordering = correctly identifies the largest forward run, zero-duration attempt handled.
- `tests/test_attempt_naming.py` (~4): batched name extraction from canned LLM response, schema validation (empty name → fallback), partial-success (LLM returns 6 of 8 names, 2 missing → canned fallback fills the gaps), LLM-returns-None → all canned.
- `tests/test_premade.py` (~7): orchestrator end-to-end with a fake `llm_client`. Replace-existing-on-rerun, hand-built/fork preservation (never deleted), name application (LLM vs canned fallback), KeyError on unknown project, ValueError on no on-script tags, deduplication when two strategies produce identical clip lists, both buckets populated correctly.
- `tests/test_routes_premade.py` (~9): TestClient happy path + 404/400/409/502 mirrors + the Phase 6.1 invariants (`dirty=True` before run, no event-loop block via to_thread, mutated-gates-commit, snapshot-once-per-call, watcher-race coverage matching the Phase 6 pattern).

### Decisions locked with this plan (all 5 open decisions resolved with Lillian)

- **Best-plausible bucket has 5 strategies, Diagnostic bucket has 3 strategies.** Schema field `premade_bucket` is the only thing distinguishing them in storage; UI separates them into two panels on `/attempts`.
- **Continuity score is a derived cache.** Written by the orchestrator into `Attempt.continuity_score`. Recomputed on every attempt write. The data-model invariant ("readers should be willing to recompute") is honored.
- **One batched LLM call for all attempt names, not N sequential calls.** Cuts wall-clock from ~4–7 min to ~30s. Failure falls back to canned names per-strategy so the run completes.
- **`source="ai-premade"` attempts are replaced on re-generation.** Hand-built (`source="hand-built"`) and forks (`source="fork"`) are never touched — those are user work, not ours to overwrite.
- **Words-per-second is the v0 "energy" heuristic.** Computed from Whisper word timestamps in the transcript cache (cheap). Marked in code as v0; revisit when audio analysis lands. If dogfood says it's wrong, drop the strategy.
- **Diagnostic strategies cap at 3 results each.** Prevents a "started with line 7 once" cluster of just one take from polluting the Diagnostic panel. Total attempts per project bounded at 14 (5 best + 9 diagnostic).
- **All eight strategies run independently and tolerate skipping.** A strategy that can't produce a meaningful result returns `[]`; the orchestrator filters those out.
- **Dedup across strategies.** If two strategies produce identical clip lists (e.g., `best_per_line` and `shortest_complete` agree on a small project), keep the first by strategy order, drop the second. Documented; tested.
- **`AttemptClip.trim_start_offset` / `trim_end_offset` left at 0.0** for all premade attempts. Per-attempt trim is Phase 10 territory; the resolver uses the clip's base `start_sec`/`end_sec` directly until then.
- **`internal_pause_max_sec` left at `null` for all premade attempts.** The "tighten internal pauses" toggle ships in Phase 10.

### Phase 8 plan-review advisory items (resolve inline during execution, not pre-code)

These came back from the Phase 8 plan review (2026-05-25). Not blocking but worth landing in code so they don't slip:

- **Regenerate confirmation modal — variable count.** The modal copy should be `"This will replace the ${count} existing AI-generated attempts."`, not hardcoded "8". Attempts vary 4–16; hardcoding lies.
- **`best_per_line` name suffix when coverage is incomplete.** A 10-line script where 3 lines have no on-script match → 7-clip attempt. Generated name should suffix `(7 of 10 lines covered)` so the user sees the gap. One line in the namer.
- **`_next_attempt_id` allocator.** Phase 8 is the first writer of `state.attempts`; the monotonic-string-int pattern from `_next_source_id` / `_next_project_id` extends here. Explicit in the orchestrator + one test that two consecutive `generate_premade_attempts` calls don't collide on IDs.
- **Attempts.tsx click-to-expand uses the side-panel pattern**, NOT an in-place expand or modal. Consistent with Project.tsx / ScriptTOC.tsx; forward-compat with Phase 9's live-preview pane swap (which becomes the third use of the SidePanel pattern → extraction trigger).

### Carry from Phase 7 review

- **Advisory: `untagged_clips` UI semantics on the Take Grid summary chip.** Counter includes clips from sources unrelated to the project's current focus (the multi-project spec). For a single-source project the chip can read confusingly. Worth adding a tooltip ("across your full library") OR a "scope to source(s)" filter on the Project page chips — **ride along on Phase 8** as a small UI polish item if there's frontend work in the area anyway. Otherwise defer to Phase 8.5.

### Out of scope for Phase 8 (explicit)

- Editing an attempt (reorder, fork, replace clip, trim) — Phase 10.
- Live preview of an attempt — Phase 9.
- Continuity-score recomputation on attempt edits — Phase 10 (no edit path exists yet).
- Premade-attempt naming with audio/energy analysis beyond words-per-second — when audio pipeline lands.
- Per-attempt-clip `trim_*_offset` populating from any strategy — all premade attempts use base clip boundaries verbatim.

---

## Phase 9 — Live preview

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- **Cross-source preview blind spot**: btc.0.4 dogfood is single-source, so the alternating-`<video>` swap won't hit its worst case (~100–300ms cross-source latency) during early dogfooding. First multi-source attempt is the real stress test for whether MSE needs to come sooner than Stage 2. Don't declare success on btc.0.4 alone.
- `internal_pause_max_sec` on `AttemptClip`: when set, the resolver expands one attempt-clip into multiple `(start, end)` sub-ranges (each interior gap > max collapses to `max`). The swap-on-`ended` trick handles them the same as separate clips. Document this in the resolver so Phase 11 export doesn't reimplement the rule.
- **Card + SidePanel extraction trigger.** Phase 9's `<video>`-embedded side panel becomes the third use of the side-panel pattern (Project.tsx, ScriptTOC.tsx, Attempts.tsx). That's the moment to extract to `web/src/components/`.

## Phase 10 — Attempt editing

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Frame-precise nudge (`Cmd+Alt = ±1 frame`) uses `Source.fps`. If `fps is None` for a source (ffprobe failed in Phase 2), fall back to 30 fps with a one-time UI warning per source. See spec → "Source fps detection."
- "Tighten internal pauses" toggle sets `AttemptClip.internal_pause_max_sec` to a sensible default (start with 0.5s). Single button, no slider — the full per-segment aggressiveness UI is v1.
- **Trim-clamp test now lands for real**: with real attempts existing, the Phase 4 `clamp_attempt_trims_for_clip()` stub gets its failing-then-passing test. Boundary correction that moves a clip's `start_sec` inward past an `Attempt.clips[i].trim_start_offset` must clamp the offset, not leave the attempt referencing impossible coordinates.
- `continuity_score` recomputation on edits — call `compute_continuity_score` after every clip-list mutation; the on-disk cache stays in sync.

## Phase 11 — Export

*To be planned before execution.*
