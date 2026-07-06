# KICKOFF_MESSAGES — paste-ready session starters

**How this file works:** at every phase closeout, the finishing session writes the *next* phase's kickoff message here — self-contained enough for a fresh session with zero other context. Lillian pastes it into a new session verbatim. When a message has been used, move it to the **Used** section with the date. Newest pending message sits at the top of the **Queue**.

---

## Queue

### [USED 2026-07-05] Pre-build decision review — findings landed in `PREBUILD_REVIEW_FINDINGS.md` and were dispositioned the same day

> You are the **pre-build reviewer** for ClipFarm's native macOS rewrite — a docs-only adversarial review session. Do not write code and do not edit any files; your deliverable is a findings report that Lillian will carry back to the planning session.
>
> Context: ClipFarm is a personal video take-selection/assembly tool, currently a Python/FastAPI + React web app (built through phase 10a, ~470 tests, now a frozen reference). It is being rewritten as a native Swift/SwiftUI macOS app for macOS 26, with an eventual paid direct-distribution goal. Planning is complete and has been through two review rounds with Lillian.
>
> Read, in order: (1) `NATIVE_REWRITE_PLAN.md` — architecture + phases N0–N19; (2) `NATIVE_REWRITE_DECISIONS.md` — 31 decisions with status; (3) `clipfarm-spec.md` — product spec, especially the 2026-07-05 amendment banner at the top (product principles are non-negotiable and out of scope for critique); (4) `mac/CLAUDE.md` — build rules; (5) skim `COMPLETED_PHASES.md` for how the web build actually went (races, hotfixes, pain points), and consult the Python code wherever a plan claim depends on it.
>
> Surface issues in these categories, ranked by severity:
> - **Technical claims that may be wrong.** Verify the load-bearing ones against current primary sources (use web search): AVMutableComposition gapless multi-source playback and `replaceCurrentItem` swap behavior; boundary-time-observer trim looping; passthrough/edit-list export behavior at non-keyframe cuts; AVSampleCursor sync-sample enumeration for keyframe maps; smart-cut feasibility (SPS/PPS splice alignment); GRDB 7 + main-actor ValueObservation; the SwiftData-undo-instability claim justifying D6; Swift 6.2 Approachable Concurrency defaults; WhisperKit word-level timestamps; STTextView viability; swift-subprocess. Flag anything stale, overstated, or version-gated incorrectly (target: macOS 26 / Xcode 26).
> - **Internal contradictions** between plan ↔ decisions ↔ spec amendments ↔ mac/CLAUDE.md (phase numbering, invariants, references).
> - **Missing decisions** — anything treated as settled that was never decided, or a decision whose consequences aren't accounted for elsewhere.
> - **Phase-plan realism** — scope/sequencing risks, dependencies pointing the wrong way, whether N2's exit gates are the *right* gates (anything missing that would invalidate the composition approach only later, at N10–N12?), test-parity plan gaps.
> - **Commercial-track blind spots** (Track 2, N14–N19) — anything that would force a v1 architecture change if discovered late.
>
> Rules: LOCKED/RESOLVED decision *outcomes* are settled — do not relitigate preferences — but DO flag any decision with an unexamined consequence or a factual premise you can falsify. The Python implementation is reference, not oracle.
>
> Output: a severity-ranked findings list — for each: `[severity] · doc + section · what's wrong or risky · evidence (link if external) · proposed fix`. End with a short "clean bill" list of load-bearing claims you verified and found solid. Do not edit any files.

### NEXT — Autonomous coordinator run, starting at N0 (2026-07-05)

> Open a fresh session and type **`/run-phase`**. That is the entire kickoff — the skill (`.claude/skills/run-phase/SKILL.md`) loads the coordinator loop, feeds the N0 kickoff below **verbatim** to the implementer agent, and runs phases under the Autonomous batching amendment in `mac/CLAUDE.md` (checkpoint tiers, deferred manual verification, Lillian-only calls). Cast is capped at coordinator + one implementer + one cold reviewer (`REVIEW_PROMPT.md`); subagents never spawn subagents.
>
> Human setup for the first stretch: be on call for the N0 Xcode/cert assist (~5 min, only if pbxproj hand-authoring fails); expect a TCC folder prompt around N3 ingest; plan a watch session at the N2 hard stop. Open questions accumulate in `QUESTIONS.md` and surface at checkpoints.

### Phase N2: playback engine — the de-risking spike (written at N1 closeout — consumed by the coordinator, or paste manually into a fresh session)

> You are the **implementer** starting phase **N2 (playback engine — the de-risking spike)** of ClipFarm's native macOS rewrite. This phase exists to prove the rewrite's thesis (AVMutableComposition + single AVPlayer) before any UI sits on it — and to fail fast if a research assumption is wrong. **N2 is a HARD STOP phase: Lillian watches the gates and adjudicates them at the end; a FAILED gate escalates immediately mid-phase — the D11 pivot to the sample-buffer pipeline is her call, never yours.**
>
> State after N1: the data layer exists and is tested — CFDomain has all entities (`ClipCategory`, `Clip.boundaryEdited`, etc.), clip-ID encoding, `resolveAttempt` (emits `[ResolvedItem]`: `.range(ResolvedRange)` with `clipID/sourceID/effectiveStartSec/effectiveEndSec`, `.tombstone` — the builder skips tombstones per plan §2.5 rule 7), and `continuityScore`; CFStore has the GRDB v1 schema (FTS5 + triggers), snapshots (`VACUUM INTO`, one-barrier destructive ritual), per-library `LibrarySettings`, and the `LibraryManager` close→swap→reopen path; CFLLM has prefs + Keychain scaffolding. `swift test` baseline: **116 green** from `mac/ClipFarmKit`. CFMedia and CFExport still hold their N0 module markers — delete each (plus its smoke test) as real code lands in it.
>
> Read first: `mac/CLAUDE.md` (binding rules), `NATIVE_REWRITE_PLAN.md` §2.4 (time policy) / §2.5 (playback engine rules — each is load-bearing) / §6 (performance budgets) and the **N2 phase entry** (the full expanded gate list — measured, not eyeballed), `NATIVE_REWRITE_DECISIONS.md` (D11, D12, D13, D29, D31, D32 at minimum), and `COMPLETED_PHASES.md` → Phase N1 (what exists + the next-phase delta notes).
>
> Execute N2 per the plan:
> - **CFMedia**: `AssetCache` (AVURLAsset per source, properties pre-loaded), `MetadataProbe` (async load of duration/frame timing/format/naturalSize/preferredTransform + HDR detection; frame math from `minFrameDuration`, never `nominalFrameRate`), `CompositionBuilder` (§2.5 rules 1–8: one video + one audio track; both tracks inserted from the same clamped range; immutable-snapshot rebuilds + `replaceCurrentItem` swap with pre-seek-await; D32 conditional videoComposition for mixed geometry; D29 explicit color properties when dynamic ranges mix; ~10ms audio micro-fades — add the `smoothCutAudio: Bool = true` accessor to N1's `LibrarySettings` when wiring this, one accessor + tests), `PlayerEngine` (`load(ranges:)`, play/pause, zero-tolerance `seek`, `step(frames:)`, boundary-observer `loop(window:)` with re-arm-after-every-swap discipline + periodic-observer belt-and-suspenders, `currentTime` via periodic observer).
> - **Time policy (D12)**: `Double` seconds cross the CFMedia boundary exactly once → `CMTime`; all media arithmetic in `CMTime`.
> - **Debug harness**: hand-specified `(file, start, end)` ranges over real files from the dogfood folder (`~/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/Video/Scripts/mp4files/05.19.26/`) — no ingest, no resolver needed (pass a nil `transcriptProvider` if any path touches it). **Footage folders are strictly read-only — never a shell write, move, or delete there.**
> - **Exit gates (all in the plan's N2 entry — run them, record numbers):** seam-drop instrumentation (p95 inter-frame gap at 20+ non-keyframe cuts across ≥3 files incl. iPhone HDR); swap-blink count over 100 edit cycles A/B'd vs mutate-in-place; mixed-rotation render probe (D32); HDR↔SDR seam probe (D29, preview AND a Standard-tier export); rebuild <10ms for 50 clips + end-to-end edit→first-frame latency; frame accuracy + `step(byCount:)`; worst-case trim-loop restart ≤50ms on long-GOP 4K HEVC; micro-fades kill pops without softening onsets; **the half-day export mini-spike** (passthrough-at-non-keyframe-cuts behavior, hybrid sequential-writer lead-in test, quick elst A/B in QuickTime/VLC/Chrome) — its answers choose N12's architecture.
> - TCC note: first read of the footage folder may prompt — Lillian may need to be at the machine.
>
> Workflow (binding): write the N2 plan entry into `PHASES.md` *before* code **and commit it before implementation begins**; one phase only; N2 tier = **HARD STOP at end** — report gate numbers to Lillian and stop; do NOT auto-continue to N3. Schema/model changes (the `LibrarySettings` accessor) get their own commit before dependent work. At closeout: `COMPLETED_PHASES.md` entry with the measured gate table (detailed enough for the cold reviewer AND for Lillian's watch session), read the N3 plan entry in full and record a next-phase delta, write the N3 kickoff into `KICKOFF_MESSAGES.md`, update pointers, commit per convention.
>
> **Autonomous mode.** Non-gating ambiguity → document 2–3 options in the phase entry, implement the most spec-defensible, mark it **PROVISIONAL**, log it in `QUESTIONS.md`. Core-gating ambiguity (including any gate that fails or can't be measured) → stop and report. Never: add a third-party dependency, contradict a LOCKED/RESOLVED decision, relax a §6 budget or accept a failed gate (Lillian-only), invent product behavior, or write into footage folders.

### [USED 2026-07-06] Phase N1: domain models + persistence core (executed 2026-07-06 — closeout in `COMPLETED_PHASES.md` → Phase N1; manual verify deferred)

> You are the **implementer** starting phase **N1 (domain models + persistence core)** of ClipFarm's native macOS rewrite. N0 is complete: `mac/ClipFarm.xcodeproj` + `ClipFarmKit` (CFDomain / CFStore / CFMedia / CFLLM / CFExport, five Swift Testing smoke targets) build green; `swift test` runs 6 smoke tests from `mac/ClipFarmKit`; GRDB is pinned at **7.11.1** via the committed `Package.resolved`; the isolation policy is already encoded as `kitSwiftSettings` in `Package.swift` (nonisolated default + SE-0461/SE-0470 upcoming features) — new N1 code inherits it, and Kit targets are never flipped to MainActor-default.
>
> Read first: `mac/CLAUDE.md` (binding rules — invariants, isolation policy, testing expectations, closeout ritual), `NATIVE_REWRITE_PLAN.md` §2.3 (persistence schema) / §2.4 (time policy) / §2.7 (state, undo, concurrency) and the **N1 phase entry**, `NATIVE_REWRITE_DECISIONS.md` (D6, D7, D8, D9, D12, D23, D28 at minimum), and `COMPLETED_PHASES.md` → Phase N0 (what exists, PROVISIONAL calls, next-phase delta). `clipfarm-spec.md` is canonical for product behavior — data-model invariants, clip-ID encoding, snapshot ritual.
>
> Execute N1 per the plan — the data layer exists, tested, before anything sits on it:
> - **Port map:** `clipfarm/models.py` → CFDomain structs, field-for-field (including `Source.unavailable`, `Attempt.needs_review`, `TagKind.tag`, plus new `Clip.boundary_edited`; adopt the `script` naming). `clipfarm/store.py` → CFStore: GRDB schema exactly per plan §2.3 (FTS5 external-content table + sync triggers; `attempt_clips.clip_id` deliberately NOT an FK — tombstones dangle by design), `DatabaseMigrator` with v1 registered from day one, snapshot service (`VACUUM INTO`, prune to 50 — snapshot runs in its own barrier access *immediately before* the mutating transaction, partial-file cleanup on failure), uniqueness via a NULL-proof unique index (`COALESCE(project_tag_id, '')` or generated column) **plus** domain validation as the enforcer (finding 10), source-integrity check on open. `resolver.py` + `continuity.py` → CFDomain pure functions (N2 consumes them).
> - **ID rules:** all IDs strings; clip IDs encode `source__start__end` at creation (`HH-MM-SS.mmm`, `int(round(t*1000))`, `__` separator, filename constraint) and are opaque afterward; allocators are monotonic max+1 over all existing keys, never reusing freed slots. Time at rest is `Double` seconds (D12).
> - **Settings scaffolding:** per-library settings table in the DB (they travel with the library); app-level prefs → `UserDefaults`; API key → Keychain (D23).
> - **Library close→swap→reopen path** (clears the UndoManager stack, restarts ValueObservations) — snapshot-restore, backup-restore, and library switching reuse it later. N0 delta note (corrected by the N0 cold review): `UndoManager` is a **Foundation** class — Kit code and Kit tests can hold and drive one directly; only the *window's instance* (`NSWindow.undoManager` / `@Environment(\.undoManager)`) is vended app-side. Design consequence: CFStore takes an **injected** `UndoManager` (or exposes a clear-stack hook) rather than owning a window's — never reach for UI from the Kit.
> - **Tests (~90):** models round-trip, uniqueness, store/snapshot/migrations, source integrity, settings, resolver (14), continuity (9 + 5 refresh), fixture builders for everything downstream. Ported Python tests follow the adjudication rule — the Python implementation is the *reference, not the oracle*; investigate against the spec before changing Swift, record divergences in the phase entry. Every store mutation lands with a register→undo→redo test — drive `UndoManager` directly against store methods (it is Foundation; no UI involved).
> - **N0 scaffolding cleanup:** the five module-marker enums (`CFDomainModule` etc.) are placeholders — replace/delete them as real code lands, and when `CFDomainModule` goes, also delete the `precondition(CFDomainModule.name == "CFDomain")` linkage probe in `mac/ClipFarm/App/ClipFarmApp.swift` (real Kit imports prove linkage from N1 on; `precondition` ships in Release builds).
>
> Workflow (binding): write the N1 plan entry into `PHASES.md` *before* code **and commit it before implementation begins** (N0 cold-review finding 1: the plan-first artifact must reach git — don't collapse it to a pointer inside the same commit as the work); one phase only; N1 tier = auto-continue at end, manual verify **DEFERRED** (checklist for the closeout entry: create a scratch library; snapshot fires before a destructive op and prunes correctly; `sqlite3` on the library DB shows the §2.3 schema). Schema/model changes get their own commit before dependent feature work. At closeout: `COMPLETED_PHASES.md` entry (detailed enough for the cold reviewer), read the N2 plan entry in full and record a next-phase delta, write the N2 kickoff message into `KICKOFF_MESSAGES.md`, update pointers, commit per convention.
>
> **Autonomous mode.** Non-gating ambiguity → document 2–3 options in the phase entry, implement the most spec-defensible, mark it **PROVISIONAL**, log it in `QUESTIONS.md`. Core-gating ambiguity → stop and ask. Never: add a third-party dependency (GRDB is the only sanctioned one), contradict a LOCKED/RESOLVED decision, invent product behavior, or touch footage folders (strictly read-only).

### [USED 2026-07-05] Phase N0: toolchain & skeleton (executed same day — closeout in `COMPLETED_PHASES.md` → Phase N0; manual verify deferred)

> You are the **implementer** starting phase **N0 (toolchain & skeleton)** of ClipFarm's native macOS rewrite.
>
> Read first: `mac/CLAUDE.md` (binding rules — including the closeout ritual), `NATIVE_REWRITE_PLAN.md` §2 (target architecture) and the N0 phase entry, and the `NATIVE_REWRITE_DECISIONS.md` summary table. `clipfarm-spec.md` is canonical for product behavior and carries the 2026-07-05 amendment banner.
>
> Execute N0 per the plan:
> - `mac/` layout: `ClipFarm.xcodeproj` (thin app shell) + `ClipFarmKit` local SPM package with targets CFDomain / CFStore / CFMedia / CFLLM / CFExport and a Swift Testing test target each (smoke tests only — no features).
> - Project settings: bundle id `org.duartes.clipfarm`, minimum macOS 26, automatic signing **with a real Apple Development certificate** (ad-hoc "Sign to Run Locally" re-signs each build and re-triggers TCC prompts), **non-sandboxed**, Swift 6.2 Approachable Concurrency. **Isolation policy (SE-0466):** MainActor default isolation on the app target only; all five ClipFarmKit targets set `nonisolated` default isolation explicitly in `Package.swift` — packages do not inherit the Xcode default. Use Xcode buildable folders (synchronized groups) so new source files never require pbxproj edits.
> - GRDB 7 as a pinned ClipFarmKit dependency.
> - App target: empty main window with the nav skeleton (Library / Project / Script / Attempts / Brief / Settings) and the inspector-pane slot.
> - Verify the CLI loop documented in `mac/CLAUDE.md` (`swift test` from ClipFarmKit; `xcodebuild build` piped through `xcbeautify -q`) and correct those commands if scheme names differ.
> - Practical note: creating the `.xcodeproj` without the Xcode GUI means hand-authoring a minimal pbxproj with `fileSystemSynchronizedGroups`. Attempt it; if it turns into a slog, stop and ask Lillian to run File → New Project in Xcode with the settings above (2 minutes), then take over from there. Do not burn hours on project-file archaeology.
>
> Workflow (binding): record an N0 entry in `PHASES.md`; one phase only; stop for Lillian's manual verify (app launches; `swift test` green from CLI; `xcodebuild build` clean; adding a stray `.swift` file builds with no pbxproj edit). At closeout: write the `COMPLETED_PHASES.md` entry, read phase N1 in the plan and record a next-phase delta, write the N1 kickoff message into `KICKOFF_MESSAGES.md`, and commit per convention.

---

## Used

- **Phase N1 kickoff** — used 2026-07-06 (kept inline above, marked USED). Output: domain models + persistence core, 116 tests green; closeout in `COMPLETED_PHASES.md` → Phase N1; manual verify deferred per tier.
- **Phase N0 kickoff** — used 2026-07-05 (kept inline above, marked USED). Output: `mac/` skeleton built and verified; closeout in `COMPLETED_PHASES.md` → Phase N0; manual verify deferred per tier.
- **Pre-build decision review** — used 2026-07-05 (kept inline above, marked USED). Output: `PREBUILD_REVIEW_FINDINGS.md`; all 19 findings dispositioned the same day (D20 flipped, D32/D33 added, N2 gates expanded).
