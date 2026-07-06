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
6. **Autonomous batching (native rewrite, 2026-07-05):** when Lillian starts a `/run-phase` coordinator session, the **Autonomous batching** amendment in `mac/CLAUDE.md` governs stopping points, deferred manual verification, and Lillian-only calls. Manual runs keep rules 1–5 unchanged.

---

## Phase N1 — Domain models + persistence core (IN PROGRESS 2026-07-06)

**Goal (plan §4/N1):** the data layer exists, tested, before anything sits on it. Port `models.py` → CFDomain structs, `store.py` → CFStore on GRDB per plan §2.3, `resolver.py` + `continuity.py` → CFDomain pure functions (N2 consumes them), settings scaffolding across its three lanes, and the library close→swap→reopen path. Tier: **auto-continue**, manual verify **DEFERRED**.

### Scope

**CFDomain (pure, zero dependencies):**

- `Entities.swift` — field-for-field port of every `models.py` entity: `Source` (+ native `isHDR`/`naturalWidth`/`naturalHeight` per schema §2.3), `Clip` (+ new `boundaryEdited`, `tracks` stays nil until N18), `TracksOverride`/`AudioOverride`/`VideoOverride`/`Overlay` (reserved shape), `ProjectTag`, `Script` (`script` naming per amendment #10), `Project`, `ClipProjectTag`, `AttemptClip`, `Attempt` (+ `needsReview`), `VoiceAnnotation`; enums `Category`, `TagKind` (incl. `.tag`), `TagSource`, `PremadeBucket`, `AttemptSource`. All IDs strings; timestamps stay ISO strings (backup-format parity; native `Date` conversion is a UI concern later). Codable with snake_case CodingKeys + decode-with-defaults — this is the substrate for the N13 backup format and the test-only fixture loader (N3/N9 golden masters).
- `ClipFarmState.swift` — the state container (dictionaries + lists, mirrors the documented JSON shape) + the **uniqueness rule as domain validation**: duplicate `(clip_id, project_id, project_tag_id, category)` throws; `nil` project_tag_id is a value, not a bypass (finding 10 — domain validation is the enforcer, the DB index is the backstop).
- `Whisper.swift` — `WhisperWord`/`WhisperSegment`/`WhisperTranscript` (sidecar shape, schema_version pinned; full validation semantics land with ingest at N3).
- `Identifiers.swift` — clip-ID encoding (`HH-MM-SS.mmm`, `int(round(t*1000))` with **Python-parity half-even rounding**, `__` separator), source-stem validation + sanitized-rename helper, `nextNumericID` allocator (monotonic max+1 over all existing numeric keys, freed slots never reused, non-numeric keys ignored).
- `Resolver.swift` — `resolveAttempt` port: ordered items, tombstone for dangling clip refs, source-bounds clamp (`max(0, start)` / `min(duration ?? ∞, end)`), zero/negative-duration throws, `internal_pause_max_sec` expansion with **strict `>` gap comparison and gap dropped entirely**, word filter `w.start >= start && w.end <= end` ported as-is (straddle fix scheduled N15). **Port adaptation (CFDomain purity):** Python loads transcripts from disk inside the resolver; the Swift resolver takes an injected `transcriptProvider: (Source) -> WhisperTranscript?` closure — same fallback semantics (nil → single un-expanded range + warning). Log warnings become an `onWarning: (ResolverWarning) -> Void` callback (pure; tests capture, N2 logs).
- `Continuity.swift` — `continuityScore` port (runs = same source AND forward progression; score = max-run-runtime / total; empty/all-orphan/zero-runtime throw) + `refreshContinuity(of: inout Attempt, in:)` (degenerate cases → nil, the `refresh_attempt_continuity` port).

**CFStore (the only GRDB seam):**

- `LibrarySchema.swift` — `DatabaseMigrator` with `"v1"` registered from day one: tables exactly per plan §2.3 (`meta`, `sources`, `clips`, `projects`, `project_tags`, `clip_project_tags`, `attempts`, `attempt_clips`, `voice_annotations`, `settings`), **FTS5 external-content table `clips_fts` + insert/update/delete sync triggers**, NULL-proof unique index on `(clip_id, project_id, COALESCE(project_tag_id, ''), category)`. `attempt_clips.clip_id` and `attempts.parent_attempt_id` deliberately NOT FKs (tombstones + dangling fork parents are spec behavior). `attempt_clips.attempt_id` cascades (an attempt's clip list is its own composition). `clips.source_id` / `clip_project_tags.{clip_id,project_id}` / `attempts.project_id` are plain FKs — no cascades; propagation stays explicit op code (N5/N6).
- `LibraryStore.swift` — `open(at:undoManager:now:)` (creates folder, `DatabasePool` WAL, migrate, **refuse a superseded library** (`hasBeenSuperseded` → clear "library requires a newer app" error), write `meta.created_at` once, **source-integrity check on open**), `close()`. Injected `UndoManager?` (Foundation — Kit tests drive it directly; the app vends the window's instance) and injected clock.
- `Records.swift` — GRDB record adapters per table (CFDomain stays GRDB-free). `tracks` persists as a JSON text column (NULL in v0); `projects.script_lines` is a JSON array column, exported as the documented `script: {lines: []}` shape regardless (plan §2.3 note).
- `Snapshots.swift` — `VACUUM INTO .snapshots/<ISO>-<ms>-<token4>__<reason>.db`, prune to 50, partial-file cleanup on failure. `performDestructive(reason:_:)` runs snapshot + mutation **inside one `writeWithoutTransaction` barrier**: VACUUM (which cannot run inside a transaction) first, then the mutating transaction — nothing can interleave (finding 11). Undo of a destructive op does not snapshot (the pre-op snapshot covers that state).
- `StoreMutations.swift` / `StoreReads.swift` — N1 mutation surface: `addSource` (allocates ID), `addClips` (bulk, one undo action), `addClipProjectTag` (domain-validates uniqueness against existing rows), `importState` (whole-library replace: fixture/restore primitive, deliberately not undo-registered — the restore path clears the undo stack by design), `updateLibrarySettings`. Each domain-data mutation registers undo with a named action and a register→undo→redo test. Reads: `fetchState()` (whole-library value snapshot — backup/fixture/test primitive; per-view queries come with ValueObservation at N4+), targeted `source(id:)`/`clip(id:)`.
- `LibrarySettings.swift` — typed per-library settings over the `settings` key/value table (they travel with the library): `silenceThresholdSec` (default 2.0), `tailPolicy` (extend-to-next-word-start default / fixed-padding / word-end per D18), `tailPaddingSec`. Missing keys → defaults; unknown keys ignored.
- `LibraryManager.swift` — the **close→swap→reopen path**: holds current store + the injected UndoManager; `open(at:)`/`close()`/`swap(to:)` clear the undo stack and fire a `storeDidChange` callback (the ValueObservation-restart hook for N4+; snapshot-restore and backup-restore reuse this path at N13).

**CFLLM (settings scaffolding, D22/D23):**

- `TaggingPreferences.swift` — app-level prefs in injected `UserDefaults`: provider (ollama default), ollama model (`llama3.1:8b`), anthropic model (`claude-sonnet-4-6`) + the model-options constant. Invalid stored values fall back to defaults.
- `SecretStore.swift` — `SecretStore` protocol + `KeychainSecretStore` (Security framework, generic password, service `org.duartes.clipfarm`) + `InMemorySecretStore` for tests. The Anthropic key goes here and **only** here — never UserDefaults, never the DB (D23). Live-Keychain verification deferred to N7 (Settings page).

**N0 scaffolding cleanup:** delete `CFDomainModule` / `CFStoreModule` / `CFLLMModule` markers + their smoke tests as real code lands (CFMedia/CFExport markers stay until N2); delete the `precondition(CFDomainModule.name == "CFDomain")` linkage probe in `ClipFarmApp.swift` (real Kit code proves linkage; `precondition` ships in Release). New `CFTestSupport` target (fixture builders shared by test targets — "fixture builders for everything downstream"); Package.swift gains it under the same `kitSwiftSettings`.

### Ambiguities → options → calls (PROVISIONAL where marked)

1. **Are settings writes undoable?** mac/CLAUDE.md says "every store mutation lands with a register→undo→redo test"; macOS convention is that preference/config changes never sit on the document undo stack (no app Cmd+Z's a threshold slider). Options: (a) undo-register settings writes; (b) scope the undo rule to library *content* mutations, settings excluded; (c) defer settings entirely to N3. Implemented **(b)** — most platform-defensible, and D18's re-apply action (the thing that *changes data*) is snapshot-protected + undoable at N3 regardless. **PROVISIONAL** → `QUESTIONS.md`.
2. **Snapshot filename token.** Python used a 4-char *content* hash to avoid same-ms collisions; with `VACUUM INTO` the content isn't knowable before the copy without reading the whole DB. Options: (a) hash the live DB file bytes (reads whole file + WAL, races the checkpoint); (b) random 4-hex token (same collision-avoidance purpose, no content claim); (c) monotonic counter in `meta`. Implemented **(b)** — the spec's stated purpose is collision avoidance, not content addressing. **PROVISIONAL** → `QUESTIONS.md`.
3. **`tailPaddingSec` default.** D18 names the fixed-padding tail policy as "+N ms" without a default N. Options: 0.0 (inert until N3's UI exposes it), 0.25s (a guess), no default (force N3 to decide). Implemented **0.0 (inert)** — scaffolding must not invent a listening-behavior default; N3 owns the real value. **PROVISIONAL** → `QUESTIONS.md`.
4. **Module placement for app prefs + secrets** — CFStore (all persistence) vs CFLLM (the only consumer of provider/model/key). Implemented **CFLLM**: keeps CFStore purely the DB seam, and provider choice stays behind the CFLLM boundary per the invariant. Not marked provisional — module hygiene, no product behavior.
5. **`meta.schema_version`** — GRDB's migrator has its own bookkeeping (`grdb_migrations`); plan §2.3 also names `meta(schema_version)`. Implemented both: the migrator is the enforcer, `meta.schema_version` is informational (inspectability + backup JSON carries it). Kept in sync by the migration itself. Not provisional — plan-specified.

### Recorded divergences from the Python reference (adjudicated against spec/plan)

- `WritesFrozenError` / watcher / conflict-freeze tests: **not ported** (D7 — machinery dissolved).
- `snapshot with no state file → None`: doesn't port — an open library always has a DB file; snapshot always produces a file.
- Atomic-write / tmp-file / concurrent-save-lock tests: superseded by SQLite transactional guarantees + GRDB's serialized writer; concurrency is covered by a serialized-writes test.
- `test_settings` reshapes across three lanes: API key → Keychain contract tests (never at rest in defaults/DB), provider+model → UserDefaults round-trip, segmentation → DB settings table. chmod-0o600 test dies with the file.
- Resolver `KeyError` / `ValueError` → typed thrown errors; caplog assertions → `onWarning` capture.
- `int(round(...))` is half-even in Python 3 — Swift port uses `.toNearestOrEven` explicitly so clip IDs golden-master-match at N3.

### Tests (~95 target; port sources named)

- CFDomainTests: Resolver (14, from `test_resolver.py`), Continuity (9, `test_continuity.py`), ContinuityRefresh (5, `test_continuity_refresh.py`), Identifiers (~9: hms format/rounding/clamp/rollover, clip-ID shape, stem validation + sanitize, allocator max+1/no-reuse/ignores-non-numeric), model defaults (~4, construction half of `test_models_round_trip.py`), Codable decode-with-defaults (~4).
- CFStoreTests: open/schema shape (~8: tables, WAL, meta, FTS exists, clip_id-not-FK pragma, NULL-proof index, reopen persistence), round-trip through DB (~6, disk half of `test_models_round_trip.py`), uniqueness (7, `test_uniqueness_validator.py` + the named index-backstop test), source integrity (3, `test_source_integrity.py`), snapshots (~8, `test_store.py` snapshot suite + barrier sequencing + partial-cleanup + undo-doesn't-snapshot), migrations (~4, `test_migrations.py` adapted to DatabaseMigrator), library settings (~5), FTS trigger sync (~3), undo register→undo→redo per mutation (~6), close→swap→reopen (~4).
- CFLLMTests: preferences + secret-store contract (~6).

### Manual verify — DEFERRED (checklist for the closeout entry)

1. Create a scratch library via a small test or REPL: `sqlite3 <lib>/clipfarm.db .schema` shows the §2.3 shape (FTS table + triggers included).
2. Run a destructive op → a `.snapshots/*.db` file appears *before* the mutation lands; generate >50 → pruned to 50.
3. `swift test` green from `mac/ClipFarmKit`; `xcodebuild build` clean.

### Commit plan

1. this plan entry (committed before implementation);
2. schema + models (CFDomain entities/state/whisper/identifiers, CFStore schema/migrator/records/open/integrity, marker cleanup, their tests) — schema/model changes get their own commit;
3. domain pure functions (resolver + continuity) + tests;
4. store services (snapshots, mutations+undo, settings, manager) + CFLLM scaffolding + tests;
5. closeout docs.

---

## Phase N0 — Built ✅ 2026-07-05 (manual verify DEFERRED per N0 tier)

Toolchain & skeleton for the native rewrite. See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase N0 for the closeout entry, the deferred manual-verify checklist, the four provisional calls (**all resolved — Lillian 2026-07-05: keep as implemented**; `QUESTIONS.md` → Answered), and the cold-review dispositions (7 findings, all accepted, fixed 2026-07-06). Verified in-session: `swift test` 6/6 green, `xcodebuild build` clean + signed with the real Apple Development cert (team `384925MZJ6`), non-sandboxed, buildable-folder stray-file check passed, launch smoke passed. **Next phase: N1** (kickoff queued in `KICKOFF_MESSAGES.md`).

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

## Phase 8.1 — Verified ✅ 2026-05-26

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 8.1.

---

## Phase 9 — Verified ✅ 2026-05-26

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 9. One bug carry to Phase 10 kickoff: cross-source preload fix (~5 lines).

---

## Phase 9.5 — Verified ✅ 2026-05-26

See [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase 9.5. Tagging provider toggle (Ollama / Anthropic API) + four polish items landed in the same session (chmod 0o600, ping error surfacing, "Set without test" tooltip, progress-panel provider chip).

---

## Phase 10a — Built ⏳ 2026-05-26 (awaiting manual verify)

**Goal.** Make the assembly workflow actually usable: scrub inside the preview pane, pause/seek with the keyboard, reorder clips inside an attempt by drag, build attempts from scratch, fork existing attempts. Plus the Phase 9 cross-source preload carry. **This is the phase where "candidate videos you can watch" becomes "candidate videos you can shape."**

10a is one of two parts (split per Lillian's call): 10a is review + basic assembly; 10b is the heavier per-clip editing (trim, replace, tombstone-pick, internal-pause toggle).

### Decisions resolved with Lillian (2026-05-26 before code work)

1. **Split into 10a + 10b.** 10a covers scrubber + reorder + fork + hand-built creation (this phase). 10b covers per-attempt trim + replace-this-clip + tombstone replacement + internal-pause toggle (next phase).
2. **`@dnd-kit/sortable`** for drag-to-reorder. Modern, accessible, ~250 lines of new code total for reorder + future tombstone replace.
3. **Custom scrubber bar + keyboard.** Slim progress bar at the bottom of the PreviewPane, clamped to the current range; click to seek within `[effective_start, effective_end]`. Spacebar = play/pause. Arrow keys = ±5s within range (clamped at both ends).
4. **Hand-built attempt creation in Phase 10a.** "New empty attempt" button on `/attempts` page + an "active attempt" concept so clicking + on a TakeCard appends to the chosen target.

### Verification (manual + automated)

- `uv run pytest` passes (target ~475 tests; 447 current + ~27 new for hand-built create, fork, reorder, scrubber math, validation edge cases from plan review).
- **Preview pane**: progress bar visible at bottom; click anywhere on it → seeks to that point within the current range. Spacebar toggles play/pause. ←/→ seek by 5s (clamped at the range edges). Cross-source transition is now instant on same-source-after-cross-source (Phase 9 carry fix).
- **`/attempts` page**: "New empty attempt" button creates an attempt with 0 clips. Clicking + on a TakeCard (Project / ScriptTOC pages) appends to whichever attempt is "active" (UI selector on each take page).
- **Drag-to-reorder**: in the AttemptSidePanel's clip list, drag a clip handle → reorder. State + continuity_score persist immediately.
- **Fork**: "Fork this attempt" button in AttemptSidePanel → creates a new `source="fork"` attempt with `parent_attempt_id` set and the same clip list. Lands in the Best plausible section.
- **Real-data smoke on btc.0.4 / chrysalis**: with the Anthropic-tagged chrysalis attempts visible, fork the best one, reorder two clips inside, watch the new attempt play through in the preview pane.

### Scope

**Backend — attempt-edit endpoints (`clipfarm/routes/attempts.py`, new):**

- `POST /api/projects/{project_id}/attempts` — create a new hand-built attempt. Body `{name?: string, clips?: list[AttemptClip]}`. Defaults: empty clips, `source="hand-built"`, `premade_bucket=null`, `continuity_score=None`. Returns the new attempt with allocated id. Snapshot + commit.
- `POST /api/attempts/{attempt_id}/fork` — clone the attempt. Sets `source="fork"`, `parent_attempt_id=attempt_id`, `name="fork of {original.name}"`, copies the clip list verbatim, **recomputes `continuity_score`** (in case the original's cache was stale). Snapshot + commit.
- `PATCH /api/attempts/{attempt_id}` — update attempt metadata. Body `{name?: string}`. Used by the inline rename affordance. Doesn't touch clips. Snapshot + commit.
- `PATCH /api/attempts/{attempt_id}/clips` — replace the entire clip list (the simplest semantics for reorder + add + remove + tombstone-drop all at once). Body `{clips: list[AttemptClip]}`. **Validation rules (plan-review #1, #2, #3):**
  1. **PATCH-to-empty is always allowed** (no force flag). Hand-built attempts start empty and the user can drag all clips out and end empty; an empty attempt has `continuity_score=None`.
  2. **New clip_ids must exist in `state.clips`.** A `clip_id` in the request body that's NOT already in the attempt's current clip list AND NOT in `state.clips` is data corruption (the user can't add a non-existent clip on purpose) — return 400 with a clear "unknown clip_id" detail.
  3. **Existing tombstones pass through unchanged.** A `clip_id` that's already in the current attempt and resolves to `None` in `state.clips` (a Phase 4 tombstone) survives reorders. The frontend renders tombstones as non-draggable slots in 10a; Phase 10b adds the replacement flow.
  4. **Tombstones CAN be dropped via PATCH.** The user can prune a tombstone slot by submitting a clip list without it. Different from "replace" (Phase 10b); this is "I gave up on this slot." Tombstone deletion is just a clip-list edit, doesn't require the replace UI.
  5. **`continuity_score` recomputed on every write** (or set to None if empty).
- `DELETE /api/attempts/{attempt_id}` — delete an attempt. Hand-built / fork / ai-premade all allowed; defense-in-depth requires explicit confirmation in the UI but the backend doesn't gate on source. Snapshot + commit. **Forks-of-deleted-parent semantics (plan-review #4):** when the parent of a fork is deleted, the fork's `parent_attempt_id` stays pointing at the now-missing id (dangling reference, matches Phase 4's tombstone-for-deleted-clip pattern). Fork is user work and is preserved; UI can render "fork of [deleted attempt #N]" when the parent isn't found. DELETE never gates on "has forks" — gating would be a UX nag at single-user v0 scale.
- **All five routes set `app.state.dirty = True` inside the `async with save_lock:` block BEFORE the mutation** (plan-review #5). Even though no `asyncio.to_thread` wrap is needed (these are sync local mutations, no LLM calls), the watcher polls every 500ms and in principle could land a poll between mutation and commit. The dirty-before-mutation invariant carries forward from Phase 6 / 8 / 8.1 / 9.5. `mutated`-gated commit + snapshot reason names: `hand-built-create`, `attempt-fork`, `attempt-rename`, `attempt-clips-patch`, `attempt-delete`.

**Backend — continuity refresh helper (`clipfarm/continuity.py`, touched):**

- Existing `compute_continuity_score(state, attempt_clips)` already in place from Phase 8. Add a small `refresh_attempt_continuity(state, attempt)` that wraps it: if attempt has any clips, recompute + write back to `Attempt.continuity_score`; if empty (hand-built starting state), set to `None`. Called from each of the four routes after the mutation.

**Backend — Phase 9 carries (`clipfarm/routes/video.py` + `PreviewPane.tsx`):**

- **Cross-source preload swap fix.** `PreviewPane.tsx` time-update handler currently calls `setActiveIdx` only on the same-source branch; cross-source falls through to `advance()` alone, which means the active element re-fetches the new source while the hidden (preloaded) element gets thrown away. Fix: always `setActiveIdx` on range-end. The previously-active element stays in DOM holding its last frame; the now-active (formerly-hidden) element has the next source already loaded. Saves ~100–300ms per cross-source transition. ~5 lines.
- **Compare `source_id` directly** instead of `v.currentSrc.split("/api/")[1]`. Cleaner; makes the cross-source preload fix above easier to reason about.

**Frontend — preview pane scrubber + keyboard (`web/src/playback/PreviewPane.tsx`, touched):**

- **Custom scrubber bar** at the bottom of the pane, height 6px. Shows the current range progress (filled portion = `currentTime / effective_end_sec` proportion within `[effective_start_sec, effective_end_sec]`). Click anywhere on the bar → seek the active `<video>` element to that proportion within the range. Visual: filled part is white/light, unfilled is `bg-neutral-700`. Hover state: pointer cursor + slight height bump for affordance.
- **Spacebar = play/pause toggle** (calls `pause()` / `resume()` from `PlaybackContext`).
- **Arrow Left / Right = ±5s seek** within `[effective_start, effective_end]` (clamped at edges). Hold Shift = ±15s.
- **Keyboard handler scope**: `document`-level listener, but bail if the active element is an `<input>` / `<textarea>` / `<select>` / `contentEditable` — so typing in the brief or search doesn't seek. Only active when `queue.length > 0 && !dismissed`.
- **Hover/focus state**: pane gets a thin white outline when keyboard focus is on it (set programmatically when user presses any of the handled keys), so the user can see "yes, the pane is listening."

**Frontend — hand-built attempt creation + active attempt picker:**

- **`web/src/playback/active-attempt.tsx`** (new) — small context tracking which attempt the user is currently "adding to." Stored in `localStorage["clipfarm.active_attempt_id"]` so it survives reloads. Reset to null when the user navigates to a different project or deletes the active attempt.
- **`web/src/pages/Attempts.tsx`** (touched) — new "+ New empty attempt" button at the top. POSTs to `/api/projects/{project_id}/attempts`, sets the new attempt as active, lands you in its (empty) side panel.
- **`web/src/pages/Project.tsx` + `ScriptTOC.tsx`** (touched) — small "Adding to: [attempt #N]" pill at the top of the page when an active attempt exists, with a dropdown to switch active attempt or clear. Each TakeCard gains a small **+** button (top-right) that appends that clip to the active attempt. If no active attempt, the + button is replaced with "New attempt with this clip" (creates + adds in one step).
- **`/attempts` page side panel** — "Fork" button at the top of the attempt detail panel, "Delete attempt" button at the bottom (with confirm modal). Hand-built / fork attempts get a small "Active for adding" toggle.

**Frontend — drag-to-reorder (`web/src/pages/Attempts.tsx`, touched):**

- Wrap the attempt's clip list in `@dnd-kit/sortable`'s `<SortableContext>`. Each clip row is a `useSortable` item.
- Drag handle on the left of each row (the `01.` number area becomes the handle, with a `⋮⋮` grip icon on hover).
- On drag-end: send the new full clip-list order to `PATCH /api/attempts/{id}/clips`. Optimistic update: reorder client-side immediately, await server confirmation, revert + toast on failure.
- Tombstone items are non-draggable in 10a (they're slots; Phase 10b handles replacement). They render in their current position with a "▢ tombstone" indicator.
- Keyboard accessibility: dnd-kit's keyboard sensors are wired by default (Tab to focus, Space to pick up, arrow keys to move, Enter to drop, Esc to cancel).

**Tests (~23 new):**

- `tests/test_routes_attempts.py` (~16): create hand-built (empty + with clips), fork (parent_attempt_id set, continuity recomputed), PATCH metadata (`{name}`) round-trip, PATCH clips (reorder, add, remove, all-empty, **reject unknown clip_id**, **preserve existing tombstone**, **allow tombstone-drop**), DELETE (own, **with-forks-still-references-deleted-parent**), 404 on unknown, 409 on writes-frozen, **dirty=True-before-mutation invariant** (Phase 6.1 carry pattern), snapshot+commit-once-per-call.
- `tests/test_continuity_refresh.py` (~4): `refresh_attempt_continuity` for empty-clips (sets None), single-clip, multi-clip, dangling clip (uses existing handling).
- `tests/test_clamp_attempt_trims_for_clip.py` (~5): the Phase 4 stub finally lands its real test. With attempts containing trim offsets, boundary correction that moves base `start_sec` inward past `trim_start_offset` clamps the offset. Same for end. Other clips' offsets unaffected.
- Phase 9 carry tests not added — visual UX (cross-source preload swap) is the verification target, not unit-testable cleanly.

### Plan-review advisory items (fold inline during execution)

These came back from the Phase 10a plan review (2026-05-26). Not blocking but worth landing in code so they don't slip:

- **Optimistic reorder revert pattern**: keep the previous `clips` array in React state (a `previousOrder` ref) until the server confirms. On 4xx/5xx, restore the array + show a toast. Standard pattern; explicit so the implementer doesn't ad-hoc it.
- **`AttemptClip` fields round-trip verbatim** on reorder — `trim_start_offset` / `trim_end_offset` / `internal_pause_max_sec` / `notes` survive reorders untouched. 10a won't set these but 10b will, and hand-edited values must survive.
- **Active-attempt clears on project switch.** The active-attempt context's `useEffect` reads the current state's projects + attempts and clears the active attempt id when it points at a different project (or no longer exists).
- **Duplicate clip via "+" button is allowed.** Spec doesn't restrict and the use case is real (same clip twice for a callback). Optional subtle toast on add: "added (already in this attempt at position #N)" if the clip is already present.
- **Phase 9 cross-source preload fix is visually verified only on multi-source attempts.** btc.0.4 is single-source so this won't surface during dogfood verification of 10a. Same blind-spot pattern as Phase 6 LLM speed + Phase 8 continuity-score formula. Note in commit + COMPLETED entry.

### Decisions locked with this plan

- **Single `PATCH /clips` endpoint replaces the entire clip list.** Cleaner than separate `/reorder` / `/add` / `/remove` endpoints — frontend sends the new list, server validates + recomputes continuity + snapshots. Tradeoff: bigger network payload per edit; in practice the list is small (5–20 clips × ~200 bytes = a few KB).
- **Separate `PATCH /attempts/{id}` endpoint for metadata** (`{name}`). Keeps the clip-list PATCH semantically pure (clips ↔ clips). Inline rename affordance on the AttemptSidePanel: click the title, edit, blur to save. ~15 lines of React.
- **PATCH-to-empty allowed without a force flag** (plan-review #1). Empty drafts are a legitimate intermediate state; `continuity_score=None` when empty.
- **PATCH validation distinguishes preserved-tombstone from new-dangling-id** (plan-review #2). Existing tombstones in the attempt pass through; new clip_ids must exist in `state.clips`. 400 with `unknown clip_id` detail for invalid ones.
- **PATCH can drop tombstones** (plan-review #3). Pruning a slot is a clip-list edit; replacing one is the 10b flow.
- **Fork of deleted parent keeps dangling `parent_attempt_id`** (plan-review #4). Matches Phase 4's tombstone-for-deleted-clip pattern. DELETE doesn't gate on "has forks."
- **All five routes set `dirty=True` inside the save_lock block before mutation** (plan-review #5). Carries the invariant forward despite no `to_thread` wrap.
- **Attempt rename ships in 10a** (plan-review advisory #10): separate `PATCH /attempts/{id}` for `{name}`. Inline-edit affordance on the side panel.
- **Optimistic client-side reorder** on drag-end. Server confirms async. Toast + revert on failure.
- **`@dnd-kit/sortable` keyboard sensors enabled by default.** Tab → Space → arrows → Enter / Esc. Accessibility for free.
- **Active attempt persisted in localStorage** (key `clipfarm.active_attempt_id`), not on `app.state`. Single-user v0; UI state, not domain state. Cleared on project switch (per-project key would be `clipfarm.active_attempt_id.${project_id}` — TBD if dogfood wants multi-project memory).
- **Scrubber seek is range-clamped.** Click before the range's start → seek to start. Click after end → seek to end - 0.05s (so the range-end handler doesn't immediately fire). Same clamp on arrow keys.
- **Spacebar at document level, ignore-if-input-focused.** Standard guard pattern.
- **`continuity_score` recomputed on every edit** — write-after-read inside the route, before the snapshot. The cache stays in sync; readers can still recompute defensively per the data-model invariant.
- **Empty clip list allowed for hand-built attempts.** A freshly-created hand-built starts at zero clips; `continuity_score=None`. The TakeGrid + Attempts UI surface "empty draft" specifically.
- **Fork copies clips verbatim**, then recomputes continuity. The original's `continuity_score` cache might have been stale; fork is a write so we refresh.
- **DELETE allows ai-premade.** Backend doesn't gate by source; UI confirms with "this will delete the AI-generated attempt #N (you can re-generate it via Regenerate)" copy that's specific to ai-premade.

### Out of scope for Phase 10a (defer to 10b or later)

- **Per-attempt trim** (`[` `]` `,` `.` keyboard nudges, Cmd+Alt frame-precise) → Phase 10b.
- **"Tighten internal pauses" toggle** (sets `internal_pause_max_sec`) → Phase 10b.
- **Replace-this-clip action** (siblings popover) → Phase 10b.
- **Tombstone replacement UI** → Phase 10b. 10a renders tombstones as non-draggable slots.
- **Move clips between attempts** (drag from attempt A to attempt B) → Phase 10c or future. v0 path: copy clip-id manually + add to other attempt.
- **Trim Mode auto-replay** (the spec's "enter Trim Mode, loop on a 1-2s window") → Future Ideas per spec; not v0.
- **Attempt rename UI** — landing as a freebie since the create endpoint accepts `name`; a small inline-edit affordance on the side panel name. If trivial, ride along.

### Carries from prior reviews

- **Cross-source preload swap fix** (Phase 9 review) — in scope above.
- **Source-id comparison cleanup** (Phase 9 review observation) — in scope above.
- **Word-filter-at-boundaries for `internal_pause_max_sec`** (Phase 9 review observation) — Phase 10b territory (rides along with the toggle).

---

## Phase 10b — Per-clip editing (trim, replace, internal pauses)

*To be planned after 10a verifies. Advance notes from the original Phase 10 stub:*

- Frame-precise nudge (`Cmd+Alt = ±1 frame`) uses `Source.fps`. If `fps is None` for a source (ffprobe failed in Phase 2), fall back to 30 fps with a one-time UI warning per source. See spec → "Source fps detection."
- "Tighten internal pauses" toggle sets `AttemptClip.internal_pause_max_sec` to a sensible default (start with 0.5s). Single button, no slider — the full per-segment aggressiveness UI is v1. Resolver expansion already landed in Phase 9.
- **Trim-clamp test now lands for real**: with real attempts existing, the Phase 4 `clamp_attempt_trims_for_clip()` stub gets its failing-then-passing test. Boundary correction that moves a clip's `start_sec` inward past an `Attempt.clips[i].trim_start_offset` must clamp the offset, not leave the attempt referencing impossible coordinates. (Plan note: this could ride along on 10a if attempts-with-trims show up by the end of 10a; otherwise 10b.)
- **Tombstone replacement UI** — "▢ Removed clip — pick a replacement" affordance shipped in Phase 9 as a placeholder. 10b wires the picker (select another clip → swap in via `AttemptClip.clip_id` update, drop `needs_review`).
- **Replace-this-clip** — picker pops over the side panel, showing siblings of the same line tag ordered by confidence DESC. One click → swap.
- **Word-filter-at-boundaries for `internal_pause_max_sec`** (Phase 9 polish observation) — the strict `w.start >= effective_start AND w.end <= effective_end` excludes words straddling the trim boundary. Rides along with the toggle.

## Phase 11 — Export

*To be planned before execution.*

---

## Backlog — captured during dogfood, scheduled later

Items surfaced during dogfood that aren't blocking but shouldn't get lost. Pulled into a phase when (a) related work is happening or (b) the friction becomes the trigger.

- **Extend clip end_sec to the next word's start, not the last word's end.** Whisper's `word.end` cuts the audio where it thinks the word's articulation terminates — which lops off the natural tail of speech (breath, mouth-close, pre-silence ambient) and makes clips feel clipped-short on playback. Spec'd behavior is "≥ 2s silence between clips"; that silence is currently part of NEITHER clip. **Better**: each clip's `end_sec` extends to the start of the next word in the same source, so the full silence tail belongs to the preceding clip. Last clip in a source extends to `source.duration_sec` (if known) or stays at last-word-end. **Code location**: `clipfarm/segmentation.py:62` (`cur_end = w.end` → needs the next-word's start). The segmentation function would also need to take the "next word" as lookahead context, so the API changes from "list of (start, end)" to a slightly different shape. **Migration**: existing clips would stay short until re-ingested OR a one-shot widening pass walks `state.clips`, for each clip finds the next word in the source's Whisper sidecar, and updates `end_sec`. Caught by Lillian during Phase 10a dogfood (2026-05-26: "clips are routinely cutting off a little bit short").
- **Phase 9 cross-source preload swap fix** — landed in `bf23703` (hotfix during Phase 10a). Visual verification still pending on a multi-source attempt.
- **Word-filter at boundaries for `internal_pause_max_sec`** — `w.start >= effective_start AND w.end <= effective_end` excludes words straddling the trim boundary from gap detection (Phase 9 review observation). Polish layer; edge case.
- **`untagged_clips` UI tooltip / scope-filter** — counter on Project page chips includes clips from sources unrelated to the project's focus. Carried from Phase 7 review.
- **Cache-Control on `/api/sources/{id}/video` is `no-store`** — correct for dogfood (files can be replaced) but disables browser-side seek optimization. Revisit for v1.
