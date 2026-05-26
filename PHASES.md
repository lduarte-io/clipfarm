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

## Phase 6 — Ollama tagging (batched)

**Goal.** Run a project's brief through Llama 3.1 8B and produce `clip_project_tags` rows for every clip the LLM thinks belongs to one of the project's script lines / sections / categories. The first phase where ClipFarm actually uses its local LLM and the first writer of real tag data.

After Phase 6: a brief + ingested footage + one button press produces a tagged library. Phase 7's take grid then reads the tags to lay out the per-line columns.

**Verification at the end of this phase (concrete, no manual UI inspection needed):**

- `uv run pytest` passes (target ~270+ tests; Phase 5's 232 + Phase 6 additions).
- `curl -X POST localhost:8765/api/projects/<id>/tag` against an ingested + brief'd project returns `{batches: N, clips_tagged: M, untagged_batches: list[BatchFailure], duration_sec: float}`. One snapshot per call via `commit_state_with_snapshot(app, reason="tag-clips")`.
- After tagging, `curl localhost:8765/api/state | jq '.clip_project_tags | length'` shows the new tag rows. Every row has a non-null `category` from the 5-value enum and (for `on-script` rows) a `line_tag_id` matching an entry in the project's `Project.tags`.
- **Idempotency**: running the tag route a second time without changes is a no-op — non-stale clips are already tagged for this project and get skipped. Returns `{batches: 0, clips_tagged: 0}`.
- **Stale retag**: edit the project's brief (PATCH triggers `stale: true` on every row), call `/tag` again → the LLM gets re-run only on the stale clips, fresh tag rows replace the old, `stale: true` flips back to `false`.
- **Uniqueness validator fires**: hand-write `clipfarm.json` with two `clip_project_tags` rows that share `(clip_id, project_id, project_tag_id, category)`. Server reload via the watcher → `ValidationError` surfaces in the log + reload aborts, in-memory state stays the old version. Tests assert this end-to-end.
- **JSON-parse retry**: if the LLM returns malformed JSON, the orchestrator retries the same batch once; if the retry still fails the batch lands in `untagged_batches` with a clear reason and the rest of the run continues. The route response shows N successful + K failed batches.
- **Live verification on `05.19.26/`**: create a brief for `btc.0.4`, tag it, manually inspect 5 random clips. The categorizations should make qualitative sense (the LLM isn't deterministic; we're checking it's roughly working, not pixel-perfect output). Empirical baseline (tag count + categories distribution) gets written into `COMPLETED_PHASES.md` for future regression visibility.

### Scope

**Phase 6 kickoff cleanups (the Phase 4 architectural carries + the Phase 5 polish residue):**

These all ride along on Phase 6 because Phase 6 is the first phase where the relevant invariants matter or the new surfaces stress the existing code.

- **`serialize_state` moved inside the save lock** (`store.py`'s `save_state` and `save_state_with_snapshot`). Phase 6 introduces long-running mutation routes (tagging holds work for tens of seconds across batched LLM calls); concurrent route handlers across that window are the first plausible reproducer for the race the Phase 4 reviewer flagged. One-line move; tests don't change behaviorally.
- **Mutation + commit inside one locked critical section, across ALL six mutating routes.** Today's `routes/*.py` pattern is "acquire lock → mutate → release lock → commit acquires lock again." Two separate lock acquisitions. Fix:
  - Add `commit_state_with_snapshot_locked(app, reason)` (snapshot variant) AND `commit_state_to_disk_locked(app)` (no-snapshot variant) — both assume the caller already holds `app.state.save_lock`.
  - Migrate **every** mutating route to the new pattern: `async with save_lock: { mutate; await <locked variant> }`. That's the Phase 4 clips routes (5) + Phase 5 projects routes (3) + the Phase 2 **ingest route**. Ingest joins the new pattern instead of staying behind — leaving two coexisting patterns is the silent-inconsistency footgun the next implementer trips on.
  - Existing tests already assert lock-held-during-orchestrator; they keep passing. New test: a single test asserts the *commit* also happens inside the same lock (counts lock acquire/release events around the route call).
- **Activate `ClipProjectTag` uniqueness validator** (the Phase 1 stub). `ClipFarmState._check_clip_project_tag_uniqueness` currently has an early-return + commented seen-set check. Uncomment, drop the early return. Tests cover: duplicate triple on load raises; duplicate triple constructed in-memory raises on next validation pass.
- **Phase 5 polish residue from the reviewer's pass:**
  - `_project_detail`'s O(N²) section sort → build a `name→order_idx` map once, look up in the key. ~3 lines.
  - `_full_brief_md` reconstruction branch gets a one-line comment noting it's reachable only via direct API (programmatic creation in tests), not the routes — which always pass `brief_md_source=body.brief_md`.
  - `_build_tags_from_brief`'s `"<unknown>"` fallback for missing-parent-section lookups is unreachable under v0 (lines have `parent_id=None`); add a test that exercises it with a synthesized "future hierarchy" state OR remove it with a TODO comment. **Recommendation: keep the fallback (defensive for Phase 7's hierarchy), add a test.**

**Backend — Ollama client (`clipfarm/llm.py`):**

- Thin `httpx` wrapper around Ollama's `/api/chat` endpoint. No third-party Ollama SDK — the surface is small enough that direct HTTP is cleaner and one fewer dep.
- `chat_with_json_schema(messages, schema, *, model="llama3.1:8b", host="http://localhost:11434", timeout=60.0) -> dict | None` returns the parsed JSON or `None` on any failure (HTTP error, non-JSON response, schema mismatch). Never raises.
- The `format` parameter in the request body carries the JSON schema (Ollama supports this natively for JSON-schema-constrained output).
- `OLLAMA_HOST` env var overrides the default `localhost:11434`.
- Logs at INFO level the prompt length + response length + latency per call so the dogfood run produces readable timing data.

**Backend — Tagging orchestrator (`clipfarm/tagging.py`):**

- Pure orchestration. Takes `(state, project_id)` + an `llm_client` callable (so tests can inject a fake). Mutates `state.clip_project_tags` in place. Returns a `TaggingResult`.
- **What gets tagged in one run:**
  - Clips on every source in `state.sources` (no per-project source scoping in v0 — every ingested clip is a candidate for every project).
  - Filtered down to: clips with NO existing `clip_project_tags` row for this project_id, OR clips with at least one row that has `stale=True`.
  - `stale=True` clips are re-tagged: drop the existing rows for `(clip_id, project_id)` before the fresh tag write.
- **Prompt structure:**
  - System prompt: project name, "what's good" body, the script lines with their tag IDs, the sections with their tag IDs, ad-hoc tags with their tag IDs. Compact, ~300-500 tokens.
  - User prompt: a JSON array of `{clip_id, transcript_text}` for the batch (default size 10). The model returns a JSON array of `{clip_id, line_tag_id, section_tag_id, category, confidence}`.
- **Output schema** (locked):
  - `clip_id: str` — the clip this row tags. The LLM is asked to echo it back so we can match rows to clips even on batch-size mismatch.
  - `line_tag_id: Optional[str]` — the ProjectTag ID of a `kind="line"` entry. Null when the clip doesn't match a script line (every category except `on-script`).
  - `section_tag_id: Optional[str]` — Null in v0 (flat lines mean no section-to-clip linking yet); reserved for Phase 7+'s hierarchy.
  - `category: Literal["on-script", "related-but-different", "standalone-idea", "off-topic", "fragment"]`.
  - `confidence: float` (0.0–1.0).
- **Batching:** default 10 clips per batch. Configurable via `batch_size` query param on the route (**1-30 range**). At 30 clips × ~200 words ≈ 6K tokens per batch + prompt overhead; the model handles that fine and per-clip output quality stays solid. Higher batches (50+) trade thoroughness for getting through the array — not worth the speed.
- **Confidence threshold**: **v0 accepts every row regardless of confidence**. The score gets surfaced visually in Phase 7's take grid for the user to filter manually if they want. Don't auto-drop low-confidence rows — the LLM's confidence score isn't reliable enough to trust as a threshold.
- **LLM-output validation rules** (per row, applied in `_validate_llm_row` before writing to state):
  - **Unknown `line_tag_id`** (doesn't match any ProjectTag in the project) → drop row, log a warning naming the clip + the hallucinated ID.
  - **Invalid `category`** (not in the 5-value enum) → drop row + log.
  - **Missing required field** (no `category`, no `confidence`, no `clip_id`) → drop row + log.
  - **Out-of-range `confidence`** (> 1.0 or < 0.0) → **clamp to [0, 1] + log a warning**, don't drop. The LLM is usually right about the row's contents and just bad at the scalar; clamping preserves the signal.
  - **Unexpected `clip_id`** (not in the batch we sent) → drop row + log. Hallucinated clips do nothing useful.
- **Batch-size mismatch policy** (LLM returns N ≠ requested batch_size): **try clip_id reconstruction first.** Walk the response, keep every row whose `clip_id` is in the batch we sent + passes the per-row validation rules above. If the resulting set is non-empty, write those rows; the missing clips just don't get tagged this run (no retry — partial wins are real and re-running tagging is cheap). If the set is empty (LLM completely off-script), treat as malformed → retry once → bucket on second failure.
- **Retry policy:** if the LLM returns malformed JSON, OR the post-validation row set is empty, **retry once** with the same batch. If the retry also fails, the batch lands in `result.untagged_batches: list[BatchFailure]` with `{clip_ids, reason: str, raw_response_excerpt: str}` and the orchestrator continues to the next batch. **Never aborts the whole run on a single batch failure** — that would punish the user for one bad LLM call.
- **Tag write:** for each successful clip, write a new `ClipProjectTag` row. The uniqueness validator enforces `(clip_id, project_id, project_tag_id, category)` uniqueness at the model level. If a duplicate would be created (shouldn't happen given the pre-filter, but defense in depth), drop the new write and log a warning.
- **Voice annotations stay closed.** The `VoiceAnnotation` model is in the data model but Phase 6 ignores it entirely — that's a v2+ feature, the Phase 6 advance note explicitly flags it as scope-creep to avoid.

**Backend — Route (`clipfarm/routes/tagging.py`):**

- **`POST /api/projects/{project_id}/tag`** with optional query params `?batch_size=10` (default 10, **1-30 range**), `?dry_run=false`.
- **Empty-brief 400**: a project with `name` but no `script.lines`, no `sections`, and no ad-hoc `tags` has nothing for the LLM to match against. The route returns 400 with detail `"project '{name}' has no script lines, sections, or tags — add at least one before tagging."` before any LLM call. Cheap defense against confused UX (a 20-second round trip producing zero useful output).
- **Synchronous** for the duration of the run. The reviewer's call: at v0 scale (~150 clips, ~15 batches, ~20s total) the simpler architecture is correct. If a project ever needs minutes, Phase 9+ can swap in a background-task model. Document this with a comment so the next implementer knows the seam is on purpose.
- Holds `app.state.save_lock` across the entire run. The `dirty` flag is set as soon as the first batch lands; commit happens once at the end (one snapshot per `/tag` call, not one per batch). This is the new locked pattern from the kickoff cleanups. **Side-effect to document in a route comment**: any other mutating route (boundary correction, ingest, brief edit) called concurrently will stall behind the tag run. v0 single-user-single-tab doesn't trigger this; the same trigger that makes that matter (multi-user, multi-tab, progress UI) is the same trigger to move to a background-task model.
- 404 unknown `project_id`. 400 on empty brief (see above). 409 if `writes_frozen` (also: if the watcher fires mid-tag and `writes_frozen` flips True during the run, the locked commit at end-of-run raises `WritesFrozenError` → 409; all in-memory tags are lost. User resolves the freeze and retries. This is correct behavior — documented in a route comment so it doesn't look like a bug). 502 if Ollama is unreachable on the first batch (so the user knows the LLM endpoint is down, rather than waiting through 15 retries). Subsequent batch failures inside a run go into `untagged_batches` instead.
- `dry_run=true` runs the batching + skips the LLM call (no mutation, no snapshot). Useful for debugging the batch composition.

**Frontend (small, the real UI is Phase 7):**

- On the **Brief page**, when an existing project is selected, add a "Tag clips" button near Save/Delete.
- Button text shows the count of clips needing tagging: `Tag 47 clips` (untagged + stale). When 0 → button disabled with text "All clips tagged for this project."
- On click → `POST /api/projects/{id}/tag` → spinner + progress text ("Tagging…" — synchronous so no real-time progress in v0). On success: toast with `clips_tagged` count + how many batches failed. On 502: toast "Ollama unreachable. Is `brew services start ollama` running?"
- The actual tag inspection / per-line categorization view is **Phase 7** — Phase 6 ships the action and verifies via `/api/state`.

**Tests (~40 new):**

- `tests/test_llm.py` (~8): patched `httpx.post` returning canned JSON; happy-path schema-constrained response; malformed JSON returns None; HTTP 500 returns None; HTTP timeout returns None; ollama-host env var override; the JSON `format` field is correctly populated with the requested schema.
- `tests/test_tagging.py` (~15): pure orchestrator with a fake `llm_client`.
  - Clips not yet tagged → tagged (counts match).
  - Already-tagged + non-stale → skipped (idempotency).
  - Stale-flagged → existing rows dropped + fresh written, stale flips back to False.
  - Cross-project: project A's tagging doesn't touch project B's rows.
  - Batch boundaries respected (N=23, batch_size=10 → batches of 10/10/3).
  - Batch failure: fake client returns malformed JSON → retry-once → on retry fail, batch lands in `untagged_batches` and the run continues.
  - Output schema validation: an LLM hallucination that returns an unknown `line_tag_id` → that row is dropped + logged.
  - Output schema validation: invalid `category` → row dropped + logged.
- `tests/test_uniqueness_validator.py` (~5): the activated `ClipFarmState._check_clip_project_tag_uniqueness`. Constructing state with duplicate triples raises; loading `clipfarm.json` with duplicates raises during the model_validate pass; uniqueness key is `(clip_id, project_id, project_tag_id, category)` — same `project_tag_id` with different `category` is NOT a duplicate.
- `tests/test_routes_tagging.py` (~10): one happy-path through TestClient with a mocked LLM client; 404 unknown project; 400 empty brief (no script, no sections, no tags); 409 freeze; lock-held assertion; **commit-also-inside-lock assertion** (the new invariant — single critical section per op); snapshot-count-equals-op-count (one snapshot per `/tag` call regardless of batch count); idempotency (second call to same project returns 0); 502 when Ollama is unreachable; `dry_run=true` mode produces no mutation + no snapshot.
- `tests/test_store.py` enhancements (~3 new): serialize-inside-lock test, locked-commit variants for both `commit_state_to_disk_locked` + `commit_state_with_snapshot_locked`, behavior under concurrent locked mutations.

### Decisions locked with this plan

- **Synchronous route, no background-task system.** v0 scale (~20s on dogfood) makes the simpler architecture correct. Background-task / polling / SSE land later if a project ever takes minutes — the reviewer's three-option analysis is documented but the polling/SSE options are not built.
- **Retry-once on malformed LLM response**, then bucket the failed batch into `untagged_batches` and continue. Don't punish the user for one bad call.
- **Batch-size mismatch → clip-ID reconstruction.** When the LLM returns N ≠ batch_size rows, walk the response and keep every row that matches a `clip_id` in our batch + passes validation. Partial wins are real. Only treat as malformed if the resulting valid set is empty.
- **Per-row validation rules locked**: unknown `line_tag_id` / invalid `category` / missing required field → drop + log. Out-of-range `confidence` → **clamp to [0, 1] + log**, don't drop.
- **No confidence threshold at write time.** Every row that passes validation gets written. Phase 7's take grid surfaces confidence visually for manual filtering.
- **Empty brief rejected with 400** before any LLM call.
- **Uniqueness validator activated.** Duplicate `(clip_id, project_id, project_tag_id, category)` is a hard error at the model level — the loader rejects, in-memory mutations reject.
- **Tagging ignores `VoiceAnnotation` entirely.** The model exists; the feature is v2+. Touching it in Phase 6 is scope creep that the advance note explicitly flagged.
- **Stale flag drives re-tagging.** No separate "force retag" affordance in v0 — if a brief edit set `stale=true`, the next `/tag` call drops + rewrites those rows. If the user wants to retag clean rows too, they can hand-set `stale=true` in `clipfarm.json` (the file watcher reloads).
- **`section_tag_id` is reserved but null in v0.** Phase 5's flat-lines simplification carries: lines have `parent_id=None`, so `section_tag_id` on tagging output stays null until the brief format gains section→line hierarchy in Phase 7+.
- **Phase 4 architectural carries finally land — applied to ALL six mutating routes.** `serialize_state` inside the lock; new `commit_state_to_disk_locked` + `commit_state_with_snapshot_locked` variants. Phase 4 clips, Phase 5 projects, AND Phase 2 ingest all get migrated to the one-critical-section-per-op pattern in the same pass. Tests cover the new seam (lock held across both mutation + commit, single acquire/release per route).
- **batch_size capped at 30**, not 50. Output quality degrades on larger batches as the model trades thoroughness for completing the array.

### Out of scope for Phase 6 (explicit)

- The take grid view (Phase 7 — Phase 6 ships the writes; Phase 7 ships the reads).
- The Script TOC view (Phase 7b).
- Premade attempts (Phase 8).
- Live preview (Phase 9).
- Per-clip / per-batch progress UI — v0 synchronous run shows a spinner only.
- Background task system / SSE / polling — synchronous only.
- "Force retag everything" affordance — stale-flag-driven only.
- Voice annotations — explicit scope creep watchout.
- Ollama model selection in Settings — hard-coded `llama3.1:8b` for v0.

### Notes carried into later phases

- **Phase 7** (take grid) reads `clip_project_tags` to build the per-line columns. The schema Phase 6 writes (`category`, `line_tag_id`, `confidence`) is what Phase 7 sorts and groups by.
- **Phase 7b** (Script TOC view) reads the same tag rows from a different lens.
- **Settings page** finally gets real content in a future polish pass: Ollama host + model + batch size. v0 hard-codes via env vars + a constant.
- **Tagging quality observation hook**: the live verification on btc.0.4 writes a "5 sampled clips + their categorizations" snippet into `COMPLETED_PHASES.md`. If Llama 3.1 8B's quality is poor in real use, that's the data point for the spec's "revisit if tagging quality is inadequate" decision — the trigger to bump to Qwen 2.5 14B.

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
