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

## Phase 8 — Premade attempts generation (DRAFT — awaiting Lillian's decisions)

**Goal.** First write-side phase after Phase 6. Generates the named candidate attempts the spec calls out (best-per-line in script order, longest-contiguous, near-one-take, shortest-complete, energy-shift) by filtering `clip_project_tags` into ordered clip lists, persisting them in `state.attempts` with `premade_bucket="best"`, and surfacing them in a new Attempts page. **This is the moment a project goes from "a labeled library" to "candidate videos you can pick from."**

### Open decisions for Lillian (resolve before code work)

1. **Diagnostic groupings — ship in Phase 8 or defer?** Spec lists three (started-with-X, skipped-line-N, ad-lib-heavy) flagged as "useful for browsing, not for shipping." The dogfood goal is btc.0.4 → MP4; that path only needs the **best plausible** 5. **Recommendation:** ship the 5 ship-worthy strategies + the schema field; defer diagnostic groupings to Phase 8.5 or until after dogfood. The data shape already supports them (`premade_bucket="diagnostic"`); UI surfaces an empty Diagnostic panel until we fill it.
2. **LLM naming or canned names?** Spec says one LLM call per attempt to name it naturally ("the 3 times you said it in almost one take"). At ~30s per call, 5 calls is ~2.5 min. **Recommendation:** ship LLM naming — batch all 5 attempts into ONE call (JSON-schema constrained, like Phase 6's tagging batches) so the round-trip is ~30s, not 5×30. Canned fallback names if the LLM call fails entirely so the run doesn't fail end-to-end.
3. **"Energy picked up" strategy — words-per-second heuristic, or skip until audio analysis exists?** The spec's example uses volume / pace; v0 has no audio pipeline so volume is out. Words-per-second is computable from Whisper timestamps and is a real signal. **Recommendation:** ship words-per-second as "energy" with a `# v0 heuristic — revisit when audio lands` comment. If the picks feel wrong on dogfood, that's the signal to defer the strategy entirely (4-strategy "best" bucket instead of 5).
4. **Regenerate behavior on re-click.** If "Generate premade attempts" is clicked twice, what happens? **Recommendation:** **replace** — drop existing `source="ai-premade"` attempts, regenerate. Hand-built attempts (`source="hand-built"`) and forks (`source="fork"`) are never touched. Button label flips from "Generate" → "Regenerate" when premade attempts exist.
5. **New Attempts page, or panel on Project page?** Spec lists Attempts as a separate page. **Recommendation:** new `/attempts` page + nav item between "Script" and "Brief". Project page (Take Grid) gets a "Generate premade attempts" CTA at the top when no attempts exist for the active project; clicking it kicks the orchestrator and lands you on `/attempts`.

If any of these recommendations is wrong, redirect me before code work starts. The plan below assumes all five recommendations stand.

### Verification (manual)

- `uv run pytest` passes (target ~360 tests; 322 current + ~35 new for strategies, continuity score, route, and orchestrator).
- `curl localhost:8765/api/projects/<id>/premade-attempts -X POST` against a real tagged project returns the new attempts in JSON with `premade_bucket="best"`, computed `continuity_score`, named clip lists. State file gains entries under `attempts`.
- Re-running the POST replaces the ai-premade attempts (count stays at 5, names + clip lists may change).
- **Real-data smoke on btc.0.4** (after Phase 6 tagging): all 5 strategies produce a non-empty attempt. Each has a distinct clip list. `continuity_score` for the "longest contiguous take" attempt is ≥ 0.8 (sanity check on the formula).
- Frontend: new `/attempts` page lists attempts, grouped by `premade_bucket`. Each card shows name, continuity-score bar, clip count, total runtime. Clicking expands the clip list (filenames + timestamps). Project page (`/project`) shows a "Generate premade attempts" CTA when none exist; once present, the CTA flips to "Regenerate".

### Scope

**Pure-function strategy module: `clipfarm/strategies.py`**

Each strategy is a pure function `Strategy(state, project_id) → list[AttemptClip] | None`. Returns `None` when the project's tagged-clip set isn't rich enough to satisfy the strategy (e.g., no on-script clips for any line). The orchestrator calls every strategy and skips ones that return `None`.

- `best_per_line_in_script_order(state, project_id)` — for each line tag in `order_idx` order, pick the highest-confidence on-script clip. Skip lines with no on-script match. Result is ordered by script-line order. Usually low continuity, high completeness.
- `longest_contiguous_take(state, project_id)` — over every (source, contiguous-clip-run) window in the project's on-script clips, find the run that maximizes total runtime AND covers the most distinct line tags. Return that run as-is in source-order. By definition continuity ≈ 1.0.
- `near_one_take(state, project_id)` — find the top 3 contiguous-in-source runs that hit ≥ 70% of script lines on-script with ≤ 2 restarts (fragment clips) inside; return one combined attempt that concatenates them (script-order across the 3 runs). Spec wording: "the 3 times I said it in almost one take."
- `shortest_complete(state, project_id)` — like `best_per_line` but picks the SHORTEST on-script clip per line instead of highest-confidence. Result: every line covered, minimum total runtime. For length-constrained cuts and shorts.
- `energy_shift(state, project_id)` — compute words-per-second for each on-script clip from the Whisper word timestamps (cached). For each line, pick the clip with the highest words-per-second. **Marked `# v0 heuristic — revisit when audio analysis lands` in code.** If this strategy feels wrong on dogfood, deferring is one line in the orchestrator's strategy list.

**Continuity score function: `clipfarm/continuity.py`**

- `compute_continuity_score(state, attempt_clips) → float in [0.0, 1.0]` — pure function. Walks the attempt's clip list, groups consecutive clips into "runs" where each run satisfies: same `source_id` AND the next clip's `start_sec` ≥ current clip's `end_sec` (i.e., progressing forward in source-time, no jumping back). Sum the runtime of each run; continuity = `max_run_runtime / total_attempt_runtime`. 1.0 = entirely one run; 0.0 not actually achievable (a single clip is itself a run of 1, giving runtime / runtime = 1.0; so the minimum on a 2+-clip attempt is `min_clip_runtime / total_runtime`).
- Recomputed on attempt write (the orchestrator caches into `Attempt.continuity_score`). Recomputed by future Phase 10 attempt-edits. The on-disk field is a cache per the data-model invariant ("readers should be willing to recompute").

**LLM naming: `clipfarm/attempt_naming.py`**

- `name_attempts(attempts_summary, llm_client) → dict[strategy_id → name]` — single LLM call with all 5 attempts in one prompt. The summary per attempt: strategy id, ordered list of `(line_name, clip_transcript_first_30_chars)` plus `continuity_score`. The prompt asks for 5 names in Lillian's voice (5–12 words each), JSON-schema constrained.
- Fallback canned names (used when the LLM call fails entirely) — one per strategy, matching the spec's examples verbatim. Keeps the run end-to-end even when Ollama is down.

**Orchestrator: `clipfarm/premade.py`**

- `generate_premade_attempts(state, project_id, *, llm_client, replace_existing=True) → list[Attempt]` — runs every strategy, computes continuity, batches into one LLM-naming call, builds `Attempt` objects with `source="ai-premade"`, `premade_bucket="best"`, names from the LLM (or canned fallback). Replaces existing `source="ai-premade"` attempts if `replace_existing=True`. Mutation only; the route layer handles snapshot + lock + commit (Phase 6 pattern).
- Raises `KeyError` on unknown project, `ValueError` if the project has zero on-script tags (route → 400 with a clear "tag clips first" message).

**Route: `clipfarm/routes/premade.py`**

- `POST /api/projects/{project_id}/premade-attempts` → returns the generated `Attempt` objects + their `premade_bucket` field. Same pattern as `POST /api/projects/{id}/tag`:
  - 404 unknown project, 400 empty-script / no-tags, 502 Ollama unreachable, 409 writes frozen.
  - `async with save_lock: { mutate via asyncio.to_thread; commit_with_snapshot_locked }`. Snapshot reason `"premade-attempts"`.
  - `app.state.dirty = True` flips inside the lock-held block BEFORE the orchestrator runs (Phase 6.1 bug #1 rule carries forward as the project pattern).

**Frontend: `web/src/pages/Attempts.tsx` (new) + `web/src/pages/Project.tsx` (touched)**

- New page at `/attempts`. Lists attempts for the active project. Two sections: **Best plausible** (`premade_bucket="best"` + hand-built/forks) and **Diagnostic** (empty in Phase 8; gets populated in Phase 8.5).
- Per-attempt card: name, `source` badge (`ai-premade` / `hand-built` / `fork`), continuity-score horizontal bar (color: green ≥ 0.8, amber 0.4–0.8, red < 0.4), clip count, total runtime.
- Click an attempt → expanded view: full ordered clip list (filename + timestamp per row, transcript snippet on hover). No editing UI yet (Phase 10).
- "Regenerate premade attempts" button at the top with a confirmation prompt when ai-premade attempts already exist.
- Project page (Take Grid) gets a top-bar "Generate premade attempts" CTA when no attempts exist for the active project — clicking POSTs to `/api/projects/{id}/premade-attempts` then navigates to `/attempts`.
- Empty states: no projects → link to Brief; no tags → link to Brief's "Tag clips"; no attempts → CTA to generate.

**Tests (~35 new):**

- `tests/test_strategies.py` (~15): each of the 5 strategies tested in isolation against synthetic state. Covers happy path, empty inputs, edge cases (e.g., one source has all the on-script clips contiguously → that's the longest-contiguous winner; partial line coverage; tied confidences).
- `tests/test_continuity.py` (~6): pure formula tests — single-clip attempt = 1.0, two consecutive same-source clips = 1.0, two clips from different sources = `max / total`, three clips with one re-ordering = correctly identifies the largest forward run.
- `tests/test_premade.py` (~6): orchestrator end-to-end with a fake `llm_client`. Covers replace-existing-on-rerun, hand-built/fork preservation (never deleted), name application (LLM vs canned fallback), KeyError on unknown project, ValueError on empty tags.
- `tests/test_routes_premade.py` (~8): TestClient happy path + 404/400/409/502 mirrors + the Phase 6.1 invariants (`dirty=True` before run, no event-loop block via to_thread, mutated-gates-commit, snapshot-once-per-call, watcher-race coverage matching the Phase 6 pattern).

### Decisions locked with this plan (subject to the open-decisions section above)

- **Best-plausible bucket has 5 strategies, Diagnostic bucket has 0 in Phase 8.** Diagnostic strategies (started-with-X, skipped-line-N, ad-lib-heavy) defer to Phase 8.5. Schema field exists; UI panel renders empty in Phase 8.
- **Continuity score is a derived cache.** Written by the orchestrator into `Attempt.continuity_score`. Recomputed on every attempt write. The data-model invariant ("readers should be willing to recompute") is honored.
- **One batched LLM call for all 5 names, not 5 sequential calls.** Cuts wall-clock from ~2.5 min to ~30s. Failure falls back to canned names so the run completes.
- **`source="ai-premade"` attempts are replaced on re-generation.** Hand-built (`source="hand-built"`) and forks (`source="fork"`) are never touched — those are user work, not ours to overwrite.
- **Words-per-second is the v0 "energy" heuristic.** Computed from Whisper word timestamps in the transcript cache (cheap). Marked in code as v0; revisit when audio analysis lands. If dogfood says it's wrong, drop the strategy.
- **All five strategies run independently and tolerate skipping.** A strategy that can't produce a meaningful result (e.g., zero on-script clips for any line) returns `None`; the orchestrator filters those out so the final attempt list is 1–5 entries, never an error.
- **`AttemptClip.trim_start_offset` / `trim_end_offset` left at 0.0** for all premade attempts. Per-attempt trim is Phase 10 territory; the resolver uses the clip's base `start_sec`/`end_sec` directly until then.
- **`internal_pause_max_sec` left at `null` for all premade attempts.** The "tighten internal pauses" toggle ships in Phase 10.

### Carry from Phase 7 review

- **Advisory: `untagged_clips` UI semantics on the Take Grid summary chip.** Counter includes clips from sources unrelated to the project's current focus (the multi-project spec). For a single-source project the chip can read confusingly. Worth adding a tooltip ("across your full library") OR a "scope to source(s)" filter on the Project page chips — **ride along on Phase 8** as a small UI polish item if there's frontend work in the area anyway. Otherwise defer to Phase 8.5.

### Out of scope for Phase 8 (explicit)

- Editing an attempt (reorder, fork, replace clip, trim) — Phase 10.
- Live preview of an attempt — Phase 9.
- Diagnostic groupings (started-with-X, skipped-line-N, ad-lib-heavy) — Phase 8.5 or after dogfood.
- Continuity-score recomputation on attempt edits — Phase 10 (no edit path exists yet).
- Premade-attempt naming with audio/energy analysis beyond words-per-second — when audio pipeline lands.

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
