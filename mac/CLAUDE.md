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
- **`tracks` stays `NULL` until phase N18.**
- **Schema is versioned via `DatabaseMigrator` from day one**; schema changes get their own commit before dependent feature work.
- **No global singletons.** The store is created in the `App` struct and injected via `Environment`; services are injected, not reached for.

## Architecture rules (locked — see decisions D1/D10/D19/D20/D21)

- **SwiftUI shell + AppKit at exactly three hot spots**: the TextKit 2 transcript view (STTextView **contained behind the `TranscriptViewAdapter` seam** — nothing outside the wrapper file references it), the NSEvent local monitor for modal trim-mode keys, and the player surface.
- **One `@MainActor @Observable` AppStore.** Mutation path: view → store method → pure CFDomain function → CFStore transaction → UndoManager registration. GRDB `ValueObservation` feeds derived read models. No TCA.
- **CFDomain is pure**: value types + pure functions, zero dependencies, no I/O. Domain rules (segmentation, propagation, trim resolution, continuity, strategies) live here and are tested independently of UI and DB.
- **Views render state and call store methods only.** No business logic, no persistence, no AVFoundation in views.
- **Background work lives in named services** (Thumbnail / Waveform / LLM / Export / Transcription), `@concurrent` or actors; subprocesses via swift-subprocess behind locator seams.
- **All keyboard bindings live in the KeyMap registry** (serializable — user remapping at N19 is a settings UI, not a refactor). Never hardcode a shortcut in a view. Three layers: menu Commands / focused `onKeyPress` / modal NSEvent monitor.
- **Inject time and identity** (clock, ID allocation) so domain logic is deterministic and testable. ID allocators are monotonic max+1 over all existing keys; freed slots are never reused.

## Build workflow (how phases work)

- **`NATIVE_REWRITE_PLAN.md` is the master plan.** Unlike a just-in-time plan, every phase was written in detail up front — deliberately, so any future session can execute without rebuilding context. It is a living document: amend it, don't fork it.
- **`PHASES.md`** carries the current phase's execution detail (assumptions, in-flight notes, deviations); **`COMPLETED_PHASES.md`** receives the closeout entry.
- **One phase at a time.** Execute, then stop for Lillian's manual verification. Never auto-advance.
- **Closeout ritual** at the end of each phase:
  1. Write the closeout entry: what shipped, what was assumed where the spec was ambiguous, deviations from the plan, test counts.
  2. **Read the NEXT phase's entry in `NATIVE_REWRITE_PLAN.md` in full and record a "next-phase delta" note** — anything this phase's reality changes about the next phase's scope, assumptions, or sequencing — then amend the plan doc accordingly. This is how a fully-pre-written plan stays honest.
  3. Write the next phase's kickoff message into `../KICKOFF_MESSAGES.md` — self-contained, pasteable into a fresh session with no other context (follow that file's conventions).
  4. Update pointers (PHASES.md current phase) and commit per convention.
- **Two reviews per completed phase**: a self-assessment in-session and a separate Claude review session, both working from the `COMPLETED_PHASES.md` entry — write it detailed enough to be reviewed against the spec.
- **Schema/model changes get their own commit** before dependent feature work, so rollbacks are clean.
- **Backlog rule**: the PHASES.md backlog takes no entry without naming the phase that resolves it. If nothing owns it, fix it now instead of parking it.

## Testing expectations

- **Domain invariants and business rules must land with tests** — pure-function tests in ClipFarmKit (Swift Testing), runnable via bare `swift test`. This is the primary loop; prefer it over xcodebuild whenever views aren't involved.
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

# App-target tests
xcodebuild test -scheme ClipFarm -destination 'platform=macOS'

# Clean build
xcodebuild clean -scheme ClipFarm
```

(Verify the scheme name at N0 and correct here if it differs.)
