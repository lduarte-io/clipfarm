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

## Phase 5 — Brief editor + project creation

**Goal.** Land the *write* side of the project layer: a markdown brief editor that creates / updates / deletes `Project` records, with the script as a typed model (no more `script_json: dict`) and sections + lines materialized as `ProjectTag` entries ready for Phase 6's LLM to write `clip_project_tags` against. Phase 5 doesn't tag anything itself; it builds the structure tagging will populate.

**Verification at the end of this phase (concrete, no manual UI inspection needed):**

- `uv run pytest` passes (target ~210+ tests; Phase 4's 182 + Phase 5 additions).
- `curl -X POST localhost:8765/api/projects -H 'content-type: application/json' -d '{"brief_md":"..."}'` parses a valid brief, creates a `Project` entry with name + `script.lines` + `ProjectTag` entries for sections + lines, returns the new `project_id`.
- `curl -X PATCH localhost:8765/api/projects/<id>` with a modified brief: re-parses, updates the Project + ProjectTag entries, **marks every `clip_project_tags` row pointing at this project as `stale: true`** per the spec's "explicit retag" rule. **One snapshot per brief edit** via `commit_state_with_snapshot(app, reason="edit-brief")` — the snapshot-per-destructive-op invariant from Phase 4 carries over.
- `curl -X DELETE localhost:8765/api/projects/<id>`: removes the project + its `tags` + every `clip_project_tags` row for the project. Writes a snapshot (`reason="delete-project"`). Affected attempts (if any — Phase 8+) get `needs_review=true`.
- **Malformed-brief test**: a brief with invalid YAML frontmatter or missing required fields → 400 with a clear error pointing at the problem (line + column when available). State unchanged.
- **Hand-edit roundtrip**: write a `Project` with `script: {lines: [...]}` and `tags: {"1": ProjectTag(...), ...}` directly into `clipfarm.json`, reload, confirm the Project + tags load cleanly through the new model.
- **Unknown-key handling** through the refactored `_log_unknown_keys`: an unknown key at top level, inside a `Project`, inside a `ProjectTag`, AND inside the `dict[str, ProjectTag]` value position all get logged with their full dotted path. Locks the heuristic kill in `test_load_unknown_keys.py`.

### Scope

**Phase 5 kickoff cleanups (Phase 4 review residue + the Phase 1 follow-up that's now load-bearing):**

These ride along on Phase 5 the way Phase 1 punch-list rode in on Phase 2.

- **`_log_unknown_keys` heuristic refactor** (deferred since the Phase 1 review). `Project.tags: dict[str, ProjectTag]` is the dict-of-model stress case the heuristic was guessing at. Replace `_looks_like_dict_of_model` with explicit `typing.get_origin` + `typing.get_args` annotation inspection: walk `dict[str, X]` differently from `Optional[X]` / `list[X]` / direct `X`. Determinism over inference. Add test cases covering each annotation shape against `ClipFarmState`'s new Project structure.
- **(Phase 4 architectural carry — flagged but NOT fixed here.)** `serialize_state` is called outside the save lock in `store.py` (`save_state` and `save_state_with_snapshot`); mutation + commit are two locked sections in `routes/clips.py`. Both unreachable under v0's single-user-UI fire-one-mutation-at-a-time pattern. Phase 6 (LLM tagging — long-running mutations) is the right time to land the fix because that's the first phase where the race could actually trigger. Phase 5 leaves them as-is to keep scope contained.

**Brief markdown format (locking with this plan):**

YAML frontmatter for structured metadata + markdown body for prose. The body becomes `Project.brief_md` (Phase 6's LLM context); the frontmatter parses into typed sub-fields.

```markdown
---
name: btc explainer v0.4
script:
  - Hey, today I want to talk about Bitcoin self-custody.
  - The reason it matters is...
  - And here's how to actually do it.
sections:
  - the hook
  - the why
  - the how
tags:
  - hook
  - self-custody
  - mistakes
---

# What's good

Energy, not over-rehearsed. Tone: smart but accessible. Length: under 90s
ideally, hard cap at 2 min.

# Notes

- Avoid the "OK so" intro
- Spend more time on the why than the how
```

Rationale: matches the spec/CLAUDE.md pattern (structured frontmatter + free-prose body), well-known format, easy to hand-edit. YAML's whitespace sensitivity is a minor pitfall worth documenting in the editor's inline help.

**Alternatives considered + rejected:**

- *Pure markdown with H2 anchors* — order-sensitive, bespoke parsing, fragile to user edits.
- *JSON code blocks in markdown* — JSON's strict quoting is annoying for natural prose like script lines with quotes.

**Backend (`clipfarm/` package — new for Phase 5):**

- **Model changes in `clipfarm/models.py`:**
  - **New** `Script(StrictModel)` with `lines: list[str]`. The advance note from Phase 1.
  - **Replace** `Project.script_json: dict` with `Project.script: Optional[Script] = None`. Brief-less projects (rare; the explicit "no brief" path) have `script=None`.
  - **Sections are ProjectTags.** Sections in the brief become `ProjectTag(kind="section", name=...)` entries in `Project.tags`; script lines become `ProjectTag(kind="line", name=..., parent_id=<section-tag-id-or-None>, order_idx=...)`. No new Section model — the existing ProjectTag hierarchy handles it via `parent_id`. **Migration concern**: existing `clipfarm.json` files don't have `Project` entries yet (Phase 5 is the first writer), so no `v1_to_v2.py` migration needed. The empty migration placeholder stays empty.

- **`clipfarm/brief.py`** — pure parser.
  - `parse_brief(text: str) -> ParsedBrief` returns a structured Pydantic model: `name: str` (**required, non-empty string** — empty / non-string / missing raises), `script: Optional[Script]`, `sections: list[str]`, `tags: list[str]` (user-defined ad-hoc tags), `body_md: str` (the markdown after the frontmatter).
  - Uses `PyYAML` (added to `pyproject.toml`) for the frontmatter. Strict mode (`yaml.safe_load`) — no arbitrary Python objects.
  - On any YAML error: raise `BriefParseError` with the YAML library's line/column info. Route layer turns it into a 400 with the position info preserved.
  - **`name` validation**: must be present in the frontmatter, must be a string, must be non-empty after `.strip()`. Pydantic + a custom validator enforce this; missing or empty `name` raises `BriefParseError`.
  - **No body-only path.** A brief without YAML frontmatter (or without a `name` field) is invalid. Frontmatter-with-just-a-`name` is the minimal valid brief; pure prose without metadata is "not yet a project" — keeping the door closed prevents Phase 6's tagging code from having to special-case projects with missing structure.
  - **Duplicate script lines are tolerated** (don't dedupe, don't reject). The parser keeps them; their `order_idx` differentiates them at the ProjectTag level; the LLM in Phase 6 matches by content + position.
  - **Tests cover**: valid full brief; valid minimal brief (just `name`); missing `name` raises with a clear error; empty / non-string `name` raises; malformed YAML raises with position; nested frontmatter shapes (sections + tags) parse correctly; duplicate script lines preserved as separate entries.

- **`clipfarm/projects.py`** — pure orchestration (matches `boundary.py` pattern).
  - `create_project(state: ClipFarmState, parsed: ParsedBrief) -> str` — allocates a new `project_id` (monotonic string-int like `Source`), builds Project + ProjectTag entries from `parsed`, mutates state. Returns the project_id.
  - `update_project(state: ClipFarmState, project_id: str, parsed: ParsedBrief) -> int` — replaces the Project's `name`, `brief_md`, `script`, `tags` using the **name-keyed merge** (see locked decisions below). Sets `stale: true` on every `clip_project_tags` row referencing this project. Returns the count of tag rows flipped to stale.
  - **`POST /api/projects/parse`** is a read-only preview endpoint that runs the same `parse_brief` and returns a small summary `{name, lines_count, sections, tags}` — no state mutation, no snapshot. Used by the frontend's debounced live preview so there's a single parser implementation (Python), not a duplicated `js-yaml` ruleset that can drift.
  - `delete_project(state: ClipFarmState, project_id: str) -> tuple[int, int]` — removes the Project + its `tags` (in the Project's own dict) + every `clip_project_tags` row for this project (in the top-level state list). Marks any attempts for this project with `needs_review=True`, then drops them too (see open questions). Returns `(dropped_tag_rows, deleted_attempts)`.
  - `list_projects(state: ClipFarmState) -> list[ProjectSummary]` — used by `GET /api/projects`. Returns name, project_id, created_at, line count, section count, tag count.

- **`clipfarm/routes/projects.py`** — 6 routes (5 CRUD + 1 read-only parse preview):
  - `POST /api/projects` body `{"brief_md": "..."}` → 200 with `{project_id, snapshot}`. 400 on parse error (returns the parser's line/column info verbatim where available). Snapshot reason: `"create-project"`.
  - `GET /api/projects` → list of project summaries.
  - `GET /api/projects/{id}` → full project shape (name, brief_md, script, tags). 404 unknown.
  - `PATCH /api/projects/{id}` body `{"brief_md": "..."}` → re-parses, updates, returns `{project_id, stale_tag_rows, snapshot}`. Snapshot reason: `"edit-brief"`.
  - `DELETE /api/projects/{id}` → returns `{project_id, dropped_tag_rows, deleted_attempts, snapshot}`. Snapshot reason: `"delete-project"`. Same forward-compatible-counts pattern as `delete_clip`.
  - **`POST /api/projects/parse` body `{"brief_md": "..."}` → 200 with `{name, lines_count, sections, tags}` or 400 with parse error.** Read-only, no lock, no snapshot. The frontend's debounced live preview calls this on every textarea change.
  - Every mutating route holds `app.state.save_lock` around the orchestrator call (Phase 2.1 pattern) and routes through `commit_state_with_snapshot`.

**Frontend (`web/`):**

- **`web/src/pages/Brief.tsx`** — replace the Phase 1 placeholder with a real editor:
  - **Left rail**: project list + "New project" button.
  - **Main panel**: when a project is selected (or "new" clicked), shows a `<textarea>` with the brief markdown + a "Save" button + a "Delete project" button (with always-confirm dialog).
  - **Live parse preview** (small, below the textarea): shows the parsed name, line count, section count, tag count — updates on debounce (200ms). Tells the user "the parser saw 12 lines and 3 sections" without committing.
  - **Save**: POSTs `/api/projects` (new) or PATCHes `/api/projects/{id}` (existing). On 400, surfaces the parser error inline.
  - **Inline help** below the textarea: a `<details>` block with a minimal example brief, so the YAML frontmatter expectation is discoverable.
- The **other pages stay placeholders** — Phase 5 doesn't wire projects into the Library or Take Grid (those are Phase 7).

**Tests (target ~30 new):**

- `tests/test_brief.py` (~10): parse a valid full brief; minimal (just `name`); missing `name` raises with a clear error; malformed YAML raises with position; body-only (no frontmatter) is OK with empty structured fields; nested frontmatter shapes (sections + tags) parse correctly; round-trip a known brief through dump + parse.
- `tests/test_projects.py` (~10): `create_project` allocates monotonic IDs; `update_project` flips all matching `clip_project_tags` rows to stale (synthetic tags injected to prove the rule fires); `update_project` name-stable merge preserves existing ProjectTag IDs when names change; `delete_project` drops the project + its ProjectTags + every clip_project_tags row (synthetic data again) + hard-deletes attempts (synthetic attempts).
- `tests/test_routes_projects.py` (~8): 5 happy-path tests (one per route, each running through `_count_snapshots_after_op` to assert exactly one new snapshot with the right reason on mutating ones). 400 on malformed brief. 404 on unknown project. 409 on freeze. Lock-held assertion on at least one mutating route (carry from Phase 2.1).
- `tests/test_load_unknown_keys.py` enhancements (~4 new cases): unknown key inside a `dict[str, ProjectTag]` value, unknown key inside a top-level `dict[str, Project]`, unknown key inside the `Script` model, unknown key inside an `Optional[Script]` field. Each test asserts the warning carries the correct dotted path (e.g. `projects.1.tags.2._unknown`).
- `tests/test_models_round_trip.py` enhancements (~2 new cases): Project with `script=None` round-trips; Project with full `Script(lines=[...])` round-trips.

### Decisions locked with this plan

- **Snapshot on every Phase-5 mutating route** (create / update / delete). Spec's "every destructive op writes a snapshot first" invariant — broadly read. Failing to snapshot a botched brief edit would lose the prior version with no recovery path.
- **Name-keyed tag merge on `update_project`** — locking the **name-stable** (not position-stable) identity rule. Reordering script lines is the common case during brief iteration; renames are the deliberate "this is a different point now" case. The rule:
  - **Section identity:** `name`. Reordering sections preserves IDs; renaming a section creates a new ProjectTag for the renamed one and removes the old. Clip refs to the removed section's ProjectTag become dangling tombstones (`clip_project_tags.project_tag_id` left pointing at the deleted ID, `stale: true` flipped — same pattern as the Phase 4 delete-clip tombstone).
  - **Line identity:** `(parent_section_name, line_text, occurrence_index_in_section)`. Reordering lines within a section preserves their IDs; renaming a line creates a new one; moving a line between sections creates a new one. `occurrence_index` differentiates duplicate lines (chorus / repeated phrase) — the first occurrence of `" hook line "` in section `intro` is index 0, the second is index 1, etc.
  - Tags that no longer appear in the new brief get removed; their `clip_project_tags` rows flip to `stale: true` with `project_tag_id` left as the dangling tombstone. The user's next "Retag" run (Phase 6) is the explicit-action moment when those dangling refs get rewritten.
- **`Project.brief_md` carries the full original markdown** (frontmatter + body) — canonical source for the editor to re-render. Parsed structured fields are derived; can always be rebuilt by re-parsing.
- **PyYAML is the dependency** (well-established, ~120KB, only used here).
- **Project deletion hard-deletes the project's attempts** (Phase 8+ is the first writer of attempts — no live data lost in v0). Snapshot is the safety net.
- **Sections are ProjectTags, not a separate model.** Existing `ProjectTag.parent_id` hierarchy is enough; the brief parser builds the tree from the YAML.
- **YAML escaping is documented, not bypassed.** Script lines that start with `-`, `#`, or contain `:` need to be quoted with `'...'` (standard YAML). The Brief.tsx inline help block calls this out with one short example. Lillian can absorb that friction; redesigning the brief format to dodge it isn't worth the cost.

### Out of scope for Phase 5 (explicit)

- LLM tagging (Phase 6 — Phase 5 just creates the `ProjectTag` shells).
- Project-aware UI on the Library page (Phase 7).
- Brief editor with live transcript preview / link-from-line-to-clip (Phase 7+).
- Voice-tag projects (Future Ideas).
- Multi-author / shared briefs (not a v0 concern — single user).
- Cross-project tag transfer ("apply this project's tags to that project") — Phase 6+ if ever.
- WYSIWYG markdown editor — v0 ships a textarea. Markdown rendering as a preview is acceptable polish if cheap; not blocking.

### Notes carried into later phases

- **Phase 6** (LLM tagging) writes `clip_project_tags` rows pointing at the `ProjectTag` IDs Phase 5 creates. The shape is locked here; Phase 6 doesn't touch the Project / ProjectTag schemas.
- **Phase 7** (take grid) reads `Project.script` and `Project.tags` to build the per-line row layout.
- **Phase 8** (premade attempts) writes `state.attempts` with `project_id` set. The deletion-cleanup path Phase 5 ships for "delete project" already wires in the attempt cleanup (synthetic-tested here).
- **The serialize-outside-lock + two-section route pattern issues** are flagged from the Phase 4 review for Phase 6's kickoff. Phase 6 is the first phase where the race window could matter (long-running LLM calls in mutation paths).

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
