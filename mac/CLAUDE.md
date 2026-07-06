# CLAUDE.md — ClipFarm native (mac/)

This file provides guidance to Claude Code when working on the Swift macOS app. It governs everything under `mac/`. (The repo-root `CLAUDE.md` describes the frozen Python/React reference implementation.)

## Source of truth (authoritative)

These documents define ClipFarm. When implementing, refactoring, or making decisions, treat them as canonical:

- **Vision & product behavior**: `../clipfarm-spec.md` — canonical; carries the 2026-07-05 native amendment set at its top.
- **Detailed build plan + target architecture**: `../NATIVE_REWRITE_PLAN.md` — phases N0–N19, deliberately written in full up front (Fable won't always be available). Living document, not frozen.
- **Decision log**: `../NATIVE_REWRITE_DECISIONS.md` — every decision with rationale and status. Never silently contradict a `LOCKED`/`RESOLVED` entry; propose a change to it instead.
- **Execution state**: `../PHASES.md` (current-phase detail) and `../COMPLETED_PHASES.md` (closeouts — the audit trail).
- **Reference implementation**: the Python/React code at the repo root — frozen; *reference, not oracle* (see Testing).

If code conflicts with spec/plan/decisions, call it out explicitly and align the implementation — or propose a deliberate amendment. **Spec drift is the failure mode this project defends against.**

## Core product principles (non-negotiable)

1. **Library, not timeline.** The Library is usable before any project exists.
2. **Provenance forever.** Source video name + timestamp range on every clip, everywhere, never anonymized.
3. **AI suggests, you pick.** Soft categories, multiple premade attempts, no destructive auto-edits, explicit retag.
4. **Multi-project tagging is the engine.** A clip can carry independent `(section, line, category)` tag sets per project; new briefs re-mine the existing library. Protect against future "simplify" pressure.
5. **Canonical persisted truth lives in the GRDB library database**, accessed only through CFStore. UI state is a **projection**, never a source of truth. Never denormalize names/labels across entities.
6. **WYSIWYG.** Preview == export, always — micro-fades, trims, pause-tightening, color. If the user heard/saw it in preview, the file has it.

## Invariants (enforced in CFDomain/CFStore, tested as pure rules)

- **Clip IDs are opaque after creation** (encode `source__start__end` at birth; never re-derived; boundary edits mutate `start_sec`/`end_sec` without changing the ID).
- **Source filename stems cannot contain `__`** (reserved clip-ID separator; hard reject at ingest with rename offer).
- **Base clips are immutable from per-attempt operations.** Per-attempt trim = offsets on `attempt_clips`; only boundary correction mutates the base.
- **Boundary correction propagates**: clone tags `stale=true` on split; union-merge with dedup on `(project_id, project_tag_id, category)` (first-wins) on merge; `needs_review` on affected attempts; tombstones stay dangling by design (attempt clip refs are deliberately not FKs). Every hand correction sets `boundary_edited` (re-apply-segmentation skips those clips).
- **Trim offsets are clamped on boundary correction** (the four-case rule, incl. the pathological zero-both-and-warn case; negative offsets never clamped against base).
- **Every destructive operation**: DB snapshot (`VACUUM INTO`, prune to 50) **and** UndoManager registration with a named action. Both, always.
- **Seams are absolute**: all DB access through CFStore; all AVFoundation through CFMedia; provider choice never leaks past the CFLLM dispatcher; ffmpeg only via `FFmpegLocator`. If you're importing GRDB or AVFoundation outside its module, you're bypassing the seam.
- **Time policy**: `Double` seconds at rest; convert once at the CFMedia boundary; all media arithmetic in `CMTime`; frame math from `minFrameDuration`, never `nominalFrameRate`.
- **Load-bearing comparisons** (tested by name): silence segmentation splits when gap `>=` threshold; internal-pause expansion splits when gap `>` max (strict); overlap checks are half-open `[s, e)`.
- **`continuity_score` is a derived cache**, recomputed on every clip-list write; readers may recompute defensively.
- **The FTS5 search index follows every clip mutation** (external-content table + sync triggers) — search must never surface deleted clips or stale transcript text.
- **Overlap policy (D33)**: create AND adjust allow overlapping clips on a source; only merge rejects overlapping ranges.
- **`tracks` stays `NULL` until phase N18.**
- **Schema is versioned via `DatabaseMigrator` from day one**; schema changes get their own commit before dependent feature work.
- **No global singletons.** The store is created in the `App` struct and injected via `Environment`; services are injected, not reached for.

## Architecture rules (locked — see decisions D1/D10/D19/D20/D21)

- **SwiftUI shell + AppKit at exactly three hot spots**: the raw NSTextView/TextKit 2 transcript view (**contained behind the `TranscriptViewAdapter` seam** — nothing outside the wrapper file touches the text view directly; D20 was flipped away from STTextView for license reasons), the NSEvent local monitor for modal trim-mode keys, and the player surface.
- **One `@MainActor @Observable` AppStore.** Mutation path: view → store method → pure CFDomain function → CFStore transaction → UndoManager registration. GRDB `ValueObservation` feeds derived read models. No TCA.
- **CFDomain is pure**: value types + pure functions, zero dependencies, no I/O. Domain rules (segmentation, propagation, trim resolution, continuity, strategies) live here and are tested independently of UI and DB.
- **Views render state and call store methods only.** No business logic, no persistence, no AVFoundation in views.
- **Background work lives in named services** (Thumbnail / Waveform / LLM / Export / Transcription), `@concurrent` or actors; subprocesses via swift-subprocess behind locator seams.
- **Concurrency isolation policy (SE-0466)**: MainActor default isolation on the app target only; all five ClipFarmKit targets set `nonisolated` default isolation explicitly in `Package.swift` — packages do NOT inherit the Xcode default, and never "fix" a concurrency error by flipping a Kit target to MainActor-default. Keep SE-0461 settings symmetric across the app/package boundary.
- **All keyboard bindings live in the KeyMap registry** (serializable — user remapping at N19 is a settings UI, not a refactor). Never hardcode a shortcut in a view. Three layers: menu Commands / focused `onKeyPress` / modal NSEvent monitor.
- **Inject time and identity** (clock, ID allocation) so domain logic is deterministic and testable. ID allocators are monotonic max+1 over all existing keys; freed slots are never reused.

## Build workflow (how phases work)

- **`NATIVE_REWRITE_PLAN.md` is the master plan.** Unlike a just-in-time plan, every phase was written in detail up front — deliberately, so any future session can execute without rebuilding context. It is a living document: amend it, don't fork it.
- **`PHASES.md`** carries the current phase's execution detail (assumptions, in-flight notes, deviations); **`COMPLETED_PHASES.md`** receives the closeout entry.
- **One phase at a time.** Execute, then stop for Lillian's manual verification. Never auto-advance. *(Exception: in a `/run-phase` coordinator session, the **Autonomous batching** amendment below governs stopping points and defers — never skips — manual verification.)*
- **Closeout ritual** at the end of each phase:
  1. Write the closeout entry: what shipped, what was assumed where the spec was ambiguous, deviations from the plan, test counts.
  2. **Read the NEXT phase's entry in `NATIVE_REWRITE_PLAN.md` in full and record a "next-phase delta" note** — anything this phase's reality changes about the next phase's scope, assumptions, or sequencing — then amend the plan doc accordingly. This is how a fully-pre-written plan stays honest.
  3. Write the next phase's kickoff message into `../KICKOFF_MESSAGES.md` — self-contained, pasteable into a fresh session with no other context (follow that file's conventions).
  4. Update pointers (PHASES.md current phase) and commit per convention.
- **Two reviews per completed phase**: a self-assessment in-session and a separate Claude review session, both working from the `COMPLETED_PHASES.md` entry — write it detailed enough to be reviewed against the spec.
- **Schema/model changes get their own commit** before dependent feature work, so rollbacks are clean.
- **Backlog rule**: the PHASES.md backlog takes no entry without naming the phase that resolves it. If nothing owns it, fix it now instead of parking it.

### Autonomous batching (2026-07-05 amendment — active only in `/run-phase` coordinator sessions)

Lillian can delegate phase execution to a coordinator session via the `/run-phase` skill (`.claude/skills/run-phase/SKILL.md`). Deliberate process amendment, not drift. The rules:

- **Cast per phase: exactly three parties.** The coordinator (writes no feature code) + ONE implementer agent + ONE cold reviewer agent, strictly sequential. No fan-outs, no swarms — the manual workflow this replaces worked *because* it was only ever two chats. **Retrieval-helper exception (2026-07-06, Lillian's call, effective N4):** the implementer and the reviewer may each run at most ONE read-only helper agent at a time (`Explore` type, Sonnet) for retrieval only — locating code/usages (especially in the frozen Python reference), web/API documentation lookups, log sweeps. The helper never writes, never spawns agents of its own, and is **never used to summarize or interpret binding documents** (spec, plan, decisions, CLAUDE.md files, phase docs) — those are always read first-hand, in full, by the agent doing the work. Rationale: the helper keeps retrieval dumps out of the Fable context; it must never *compress* authoritative content into it — a lossy paraphrase of a load-bearing passage is exactly the spec-drift failure mode this project defends against. Beyond that one helper each: subagents never spawn subagents.
- **Two-reviews rule, unchanged in substance:** review 1 = implementer self-assessment in-session; review 2 = a cold reviewer running `REVIEW_PROMPT.md` **verbatim** with zero implementation context (the coordinator never summarizes the work to the reviewer — the isolation is the point). Findings are adjudicated implementer-vs-reviewer, the coordinator arbitrates against spec + decisions, and **every finding gets a written disposition** in the `COMPLETED_PHASES.md` entry (`PREBUILD_REVIEW_FINDINGS.md` style).
- **Manual verification is deferred, never skipped.** Auto-continued phases write their manual-verify checklist into the closeout entry marked `Manual verify: DEFERRED`; Lillian runs the accumulated queue at the next hard stop, and only then does an entry flip to Verified. **Debt cap: 3 phases** of deferred verification, then hard stop regardless of tier.
- **PROVISIONAL rule** (the autonomous variant of "never implement assumptions"): ambiguity that doesn't gate the phase's core → document 2–3 options in the phase entry, implement the most spec-defensible, mark it **PROVISIONAL**, log it in `QUESTIONS.md`. Ambiguity that gates the core → stop and ask.
- **Lillian-only calls — never made autonomously, by coordinator or subagent:**
  - changing or contradicting any LOCKED/RESOLVED decision;
  - adding any new third-party dependency (license vetting is hers — the D20/STTextView lesson);
  - inventing product behavior;
  - relaxing a §6 performance budget, or accepting a failed N2-class gate (the D11 pivot is hers);
  - deleting or overwriting anything she created (web implementation, her original recordings outside the footage inbox, sidecars next to those originals). **Footage inbox (D34, 2026-07-06):** ClipFarm's footage lives in **`~/ClipFarm/Footage/`** — outside any cloud-synced path, populated by Lillian herself. The inbox is a **managed working folder, not canonical storage**: the app (and agents exercising it) may write, reorganize, and delete files *within the inbox* — that's its job as an editor's media pool, and the unavailable-source greying invariant is the safety net. Footage anywhere else — including the retired `~/Desktop/AdAstra/…` dogfood path — is off-limits: don't read it as operative data, never write to it;
  - spec amendments beyond recording next-phase deltas.
- **Checkpoint tiers (Track 1).** The coordinator may stop *earlier* than this table, never later:

| Phase | Gate |
|---|---|
| N0 | START: Lillian on call for the Xcode File→New Project + Apple Development cert assist (implementer attempts pbxproj hand-authoring first). End: auto-continue, verify deferred. |
| N1 | Auto-continue. |
| N2 | **HARD STOP** at end: Lillian watches the playback gates and adjudicates them; a failed gate escalates immediately mid-phase (D11 pivot). |
| N3 | Auto-continue. TCC folder prompt may need her at the machine; the "clips no longer feel cut short" listening check is deferred. |
| N4 | **HARD STOP**: combined manual verify of N0 + N1 + N3 + N4. |
| N5–N6 | Auto-continue. |
| N7 | START: Ollama running + Anthropic key provisioned. **HARD STOP** at end: combined verify N5–N7 including the live tag run. |
| N8 | Auto-continue. |
| N9 | **HARD STOP**: grid feel + premades on the real project (N8 + N9). |
| N10–N11 | **HARD STOP** after N11: the Chipotle flow and trim mode are the product — Lillian drives both. |
| N12 | **HARD STOP**: frame-check exports in QuickTime, A/B preview vs file. |
| N13 | Run with Lillian by definition. |
| Track 2 | Tiering decided with Lillian when Track 1 closes. |

## Testing expectations

- **Domain invariants and business rules must land with tests** — pure-function tests in ClipFarmKit (Swift Testing), runnable via bare `swift test`. This is the primary loop; prefer it over xcodebuild whenever views aren't involved.
- **Every store mutation lands with a register→undo→redo test** — drive `UndoManager` directly against store methods; assert domain state and the DB round-trip in both directions.
- **The ported Python suite (~470 tests) is the parity baseline.** Adjudication rule: *the Python implementation is the reference, not the oracle.* When a ported test fails, investigate which side is wrong **against the spec** before changing the Swift code; record divergences in the phase entry.
- Golden-master tests may load the legacy `clipfarm.json` via the test-only fixture loader; that loader never ships in the app.
- **Tests run at the END of a step/phase — never run the full suite at the start of a session.** The previous phase's recorded closeout result is the baseline.
- UI is verified manually per the phase workflow (no XCUITest for now — decision D25).

## Product behavior guardrails

- **Do not invent product behavior.** If the spec/plan doesn't define it, propose 2–3 options with tradeoffs and ask. Never implement assumptions.
- **Surface uncertainty rather than silently picking a policy.**
- **Every action should be traceable to a spec behavior or a plan phase.** If you can't point at the justifying section, question whether it belongs.
- **If something feels complex, it's probably wrong.** The spec is opinionated but not complicated.

## Code hygiene

- No god state or dumping grounds. If a store method or view is getting long, you're mixing concerns — split it.
- Three similar lines beat the wrong abstraction; wait for the third use.
- Comments state constraints the code can't show — never narration of what the next line does.
- Port **semantics**, not spellings: Swift API Design Guidelines naming, not transliterated Python names.
- Keep helpers small, explicit, purpose-driven.

## Project structure

```
mac/
├── ClipFarm.xcodeproj        # thin shell — Xcode buildable folders (new files never touch pbxproj)
├── ClipFarm/                 # app target: App struct, SwiftUI views, menus/Commands,
│   │                         #   NSViewRepresentable wrappers, PlayerEngine glue
│   ├── App/                  # entry point, root window, nav, inspector pane slot
│   ├── Features/             # one folder per page: Library, Project, ScriptTOC,
│   │                         #   Attempts, Brief, Settings, TrimMode
│   └── Shared/               # shared views, KeyMap registry, adapters
└── ClipFarmKit/              # local SPM package — ~90% of all code
    ├── Package.swift
    ├── Sources/
    │   ├── CFDomain/         # pure logic, ZERO dependencies
    │   ├── CFStore/          # GRDB schema, migrations, snapshots, backup export
    │   ├── CFMedia/          # probe, composition, PlayerEngine, thumbnails, waveforms, keyframe maps
    │   ├── CFLLM/            # Ollama + Anthropic clients, tagging/naming orchestrators
    │   └── CFExport/         # export tiers, hybrid writer, ffmpeg (mkv remux)
    └── Tests/                # Swift Testing; one test target per source target
```

## Commands

```bash
# Domain/store/media/LLM tests — the fast loop, prefer this
cd mac/ClipFarmKit && swift test                 # optionally: --filter CFDomainTests

# Build the app
xcodebuild -scheme ClipFarm -configuration Debug build | xcbeautify -q

# Run: launch the built product binary directly for stdout/stderr logs
~/Library/Developer/Xcode/DerivedData/ClipFarm-*/Build/Products/Debug/ClipFarm.app/Contents/MacOS/ClipFarm

# App-target tests (none exist yet — D25 is manual UI verify; a passing run is
# vacuous until app test targets land, and adding them WILL require scheme
# edits despite the buildable-folder rule)
xcodebuild test -scheme ClipFarm -destination 'platform=macOS'

# Clean build
xcodebuild clean -scheme ClipFarm
```

(Verified at N0: the scheme is `ClipFarm`, shared in the repo (`xcodebuild` commands run from `mac/`); `xcbeautify` 3.2.1 installed via Homebrew. The bare `xcodebuild build` emits a benign "multiple matching destinations" note — arm64 is picked automatically. Two lockfiles pin GRDB — `mac/ClipFarmKit/Package.resolved` and `mac/ClipFarm.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved`; after any dependency re-resolve, re-commit **both** so the `swift test` loop and app builds stay on the same version.)
