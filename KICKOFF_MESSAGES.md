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

### Phase N0: toolchain & skeleton (dispositions committed; consumed by the coordinator — or paste manually into a fresh session)

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

- **Pre-build decision review** — used 2026-07-05 (kept inline above, marked USED). Output: `PREBUILD_REVIEW_FINDINGS.md`; all 19 findings dispositioned the same day (D20 flipped, D32/D33 added, N2 gates expanded).
