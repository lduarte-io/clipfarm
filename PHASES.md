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

## Phase 7b — Built ⏳ 2026-05-25 (awaiting manual verify)

**Goal.** Same data as Phase 7's take grid, vertical outline layout. The script displayed top-to-bottom with each line as a collapsible `<details>` showing that line's takes inside. Lillian's "browse the script as the script" view. The Take Grid (7) is for cross-line scanning; the TOC (7b) is the assembly workflow even though assembly itself ships in Phase 8.

**Scope:** frontend-only.

- `web/src/pages/ScriptTOC.tsx` — new page.
  - Reuses `/api/projects/{project_id}/take-grid` — no backend changes.
  - Vertical outline: each `LineRow` is a `<details>` element. Header shows line number + name + tag id + take-count badge. Expanded body lists takes vertically (compact card per take, ~280-char transcript snippet visible without scrolling).
  - All lines collapsed by default. Outline state is component-local; reload = fresh-everything-collapsed.
  - **Buckets section at the bottom**: same collapsible 4-bucket layout as Phase 7. Same visual pattern so users build one mental model across both pages.
  - **Side panel** (right side, sticky): same shape as Phase 7's. Selected card shows full transcript + filename + timestamp + "Open in Library" deep-link.
- `web/src/App.tsx` — add `/script` route + nav item ("Script") between "Project" and "Brief".
- **No new tests** — `build_take_grid` is already exhaustively covered by Phase 7's 14 orchestrator tests + 7 route tests. Phase 7b is a re-layout of the same data; no new backend surface.

**Decisions locked with this plan:**

- **No "Pick this take" button in v0.** Visible-but-disabled UI is a nag; we'll add the assembly action in Phase 8 when there's actually a target attempt for it to write to.
- **All lines collapsed by default.** The script structure is the read; takes expand on demand.
- **Card layout for TOC is vertical-stack, not horizontal-strip.** Phase 7's horizontal strip is good for scanning across deliveries of one line; the TOC's vertical-stack-per-line is good for working one line top-to-bottom. Different layouts, same data, same Card shape underneath.
- **Reuse Phase 7's Card + SidePanel components by duplication, not extraction.** Three would be the abstraction trigger; two is fine to duplicate. Phase 9 will swap the SidePanel body for a live `<video>` preview — that's the natural extraction point.
- **Empty-line rows are de-emphasized** (italic, neutral-500 text) but still appear in the outline so the gap is visible. Same UX principle as Phase 7's empty rows.
- **Line numbering shown.** The outline gets `01. line text`, `02. ...` to anchor the user's mental model of script order. Tag ID still shown in mono next to the name for debugging.

**Verification (manual):**

- `npm run build` succeeds.
- Nav has a new "Script" item between "Project" and "Brief"; routes to `/script`.
- Loads the same Take Grid data; renders the script as a top-to-bottom outline.
- Clicking a take opens the side panel; "Open in Library" deep-links with `?source=&word=` exactly as on Phase 7.
- Empty / no-projects / no-tags states surface with the same copy as Phase 7.

**Out of scope:**

- Reorderable outline (the spec's "reorderable script outline"): Phase 10+ (drag-and-drop reordering of script lines mutates the brief; that's an edit operation).
- "Pick this take" assembly action: Phase 8.
- Inline preview: Phase 9.

## Phase 8 — Premade attempts generation

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Two buckets: `premade_bucket="best"` (3–5 ship-worthy) and `premade_bucket="diagnostic"` (browse-only). UI surfaces them separately.
- Compute and store `continuity_score` for each generated attempt; treat the stored value as a cache (recompute on edit, never trust blindly).
- **Advisory carry from Phase 7 review**: `untagged_clips` UI semantics — the counter includes clips from sources unrelated to the current project's focus. Worth either a tooltip ("across your full library") or a "scope to source(s)" filter on the Take Grid summary chips. Polish, not blocking.

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
