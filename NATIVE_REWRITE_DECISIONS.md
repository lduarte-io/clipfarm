# ClipFarm Native — Decision Log

*Companion to [`NATIVE_REWRITE_PLAN.md`](./NATIVE_REWRITE_PLAN.md). Every branch where a real choice existed, with the options, the tradeoffs, the pick I made so the plan could proceed, and what would flip it. Format per entry: **Pick** is what the plan assumes; **Confidence** is how contestable it is (high = I'd argue for it, medium = reasonable people differ, low = close call / your taste matters); **Flips if** names the evidence that changes the answer.*

Decisions you should look hardest at, because they're taste calls or hard to reverse later: **D2, D5, D7, D14, D18, D27, D28, D30.**

---

## A. Platform & scope

### D1 — UI framework
**Options:** (a) SwiftUI-first with AppKit escape hatches; (b) pure AppKit; (c) keep a web UI in a native shell (WKWebView/Tauri-style).
**Tradeoffs:** Pure AppKit gives maximum control but ~2–3× the UI code and none of SwiftUI's iteration speed; macOS 26 SwiftUI is genuinely good now (10k-item lists are fine; your scale ~6k is comfortable). A web-shell keeps the existing React code but keeps every playback/keyboard limitation you're rewriting to escape. The known SwiftUI weak spots (giant interactive text, modal key capture) have well-established AppKit drop-ins.
**Pick:** (a) SwiftUI shell + `NSViewRepresentable` at exactly three hot spots: transcript view (TextKit 2), modal key capture (NSEvent monitor), player surface.
**Confidence:** high. **Flips if:** nothing realistic; this is the 2026 consensus pattern for new Mac editor apps.

### D2 — Minimum macOS version
**Options:** (a) macOS 26 (Tahoe) only; (b) macOS 15; (c) macOS 14.
**Tradeoffs:** You run 26.4 and this is a one-user tool. Targeting 26 unlocks the newest SwiftUI fixes, Approachable Concurrency defaults, `SpeechTranscriber` evaluation later, and zero back-compat conditionals. The only cost is it won't run on an older second machine.
**Pick:** (a) macOS 26 minimum.
**Confidence:** high. **Flips if:** you want it on an older Mac (e.g., a laptop that can't take 26) — then macOS 15 costs little.

### D3 — Full native port vs hybrid (Swift UI + keep Python backend)
**Options:** (a) full Swift port including all domain logic; (b) Swift frontend talking to the existing FastAPI backend; (c) Swift app embedding Python.
**Tradeoffs:** (b) preserves 470 tested Python tests but keeps a server process, HTTP serialization, two runtimes, and blocks the biggest native wins (composition playback needs the media layer in-process; SQLite wants one owner). (c) is packaging pain forever. (a) costs the port — but the domain logic is pure functions with exhaustive tests, which is the *easiest possible* port, and the golden-master technique (§3 of the plan) de-risks drift.
**Pick:** (a) full native. The Python code's role shifts to "reference implementation + golden master" until cutover.
**Confidence:** high. **Flips if:** the port stalls badly mid-way — (b) is the retreat position, and the phase order (data layer first) keeps that retreat cheap through ~N5.

### D4 — Project scaffolding
**Options:** (a) plain `.xcodeproj` with Xcode 16+ buildable folders + local SPM package for the core; (b) Tuist; (c) XcodeGen; (d) pure SPM executable.
**Tradeoffs:** Buildable folders eliminated the historical pbxproj-merge/agent-editing pain (files in folders just build — no project-file edits). Tuist/XcodeGen add a generation step that solves team-scale problems you don't have. Pure SPM can't produce a proper signed `.app` bundle without fighting the build system. The local-package split means ~90% of code runs under fast `swift test` with no xcodebuild — the tight CLI loop matters because Claude Code is doing the building.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing realistic at solo scale.

### D5 — Repo location
**Options:** (a) same repo, `mac/` directory, web frozen in place until cutover; (b) fresh repo.
**Tradeoffs:** Same repo keeps the spec, phase logs, memory, and the Python reference implementation (needed for golden-master diffs) adjacent — one history from web v0 through native v1. A fresh repo is cleaner-feeling but severs the port from its reference and splits the audit trail. Repo size is fine (no media committed).
**Pick:** (a) same repo, `mac/`.
**Confidence:** medium — partly taste. **Flips if:** you want the Swift project to feel like a clean start, or you plan to open-source one side independently.

---

## B. Data & persistence

### D6 — Persistence engine
**Options:** (a) GRDB 7 (SQLite); (b) SwiftData; (c) Core Data; (d) keep JSON files via Codable.
**Tradeoffs:** SwiftData on macOS 26 has *documented crashes when undo interacts with autosave* — disqualifying for an undo-heavy editor — plus it benchmarks below both alternatives. Core Data works but is boilerplate with no upside over GRDB here. JSON at ~6k clips means full-file rewrite per save (the exact cost your spec already flagged) and hand-rolled many-to-many indexes. GRDB 7: Swift-6-ready, main-actor-friendly `ValueObservation` for SwiftUI reactivity, `DatabaseMigrator` (direct analog of your migrations directory), and **FTS5** — which upgrades transcript search from substring-only to phrase/prefix for free.
**Pick:** (a) GRDB 7, WAL mode. The rewrite is the spec's own budgeted SQLite migration, executed.
**Confidence:** high. **Flips if:** nothing realistic; SwiftData would need a year of stabilization to re-enter.

### D7 — Hand-editability & the watcher/conflict machinery
**Options:** (a) JSON export/import as the hand-edit path + external SQLite tools for inspection; watcher dissolved; (b) keep a live hand-editable JSON mirror with two-way sync; (c) SQLite only, no JSON affordance.
**Tradeoffs:** (b) recreates the hardest machinery in the web app (conflict detection, freeze, modal) to preserve a workflow — editing state by hand in a text editor — that mattered mostly because JSON *was* the database. (c) abandons a spec principle entirely. (a) keeps the guarantee honest: `File → Export Library as JSON` emits the exact clipfarm.json shape (git-diffable, greppable); edit it; re-import with a diff preview. Plus `sqlite3` / DB Browser / Datasette work live.
**Pick:** (a). This is a real spec amendment (plan §9.2) — the conflict-modal invariant dissolves.
**Confidence:** high on the mechanism, **medium on how much you'll miss casual live hand-edits** — you've actually used them (flipping `stale` flags during dogfood). Mitigation if it stings: a built-in "debug edit" panel for the common cases (flip a flag, tweak a value) is an evening's work later. **Flips if:** you tell me live hand-editing is sacred — then (b), budgeted honestly at ~a full phase of work.

### D8 — Undo model
**Options:** (a) NSUndoManager with before-value snapshots per mutation + DB file snapshots before destructive ops; (b) file snapshots only (web parity); (c) full command-pattern framework.
**Tradeoffs:** (b) means no Cmd+Z, which is unacceptable in a native editor. (c) is architecture for its own sake at solo scale. (a) is the documented value-type pattern, gets Edit-menu/Cmd+Z/redo for free, and keeps the snapshot ritual as disaster insurance (belt and suspenders — snapshots also survive app crashes, which UndoManager doesn't).
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing realistic.

### D9 — Data migration of the existing library
**Options:** (a) one-time importer clipfarm.json v1 → SQLite, IDs preserved verbatim, re-runnable (fresh library per run) until cutover; (b) start the native library empty and re-ingest from source folders.
**Tradeoffs:** (b) loses every tag row, attempt, and boundary correction accumulated during dogfood — months of judgment. (a) costs an importer that must exist anyway as the inverse of the JSON exporter (D7); round-trip testing makes each guarantee the other.
**Pick:** (a). Unknown-key log-and-drop tolerance moves into the importer.
**Confidence:** high. **Flips if:** nothing.

### D28 — Library location on disk
**Options:** (a) visible folder, default `~/ClipFarm/` (contains `clipfarm.db`, `.snapshots/`, `cache/`), overridable in Settings; (b) `~/Library/Application Support/ClipFarm/`; (c) library-as-document (open any folder).
**Tradeoffs:** (b) is the platform convention but hides the data from the person who inspects her data. (c) adds document-lifecycle complexity for a single-library tool. (a) matches how you actually work — poke it with `sqlite3`, back it up by copying a folder.
**Pick:** (a).
**Confidence:** medium — pure taste. **Flips if:** you prefer convention over visibility.

---

## C. Media engine

### D11 — Playback architecture
**Options:** (a) `AVMutableComposition` + single `AVPlayer`; (b) `AVQueuePlayer` with preloaded items; (c) custom `AVSampleBufferDisplayLayer` pipeline.
**Tradeoffs:** (b) has no cross-item preroll contract for local video — it rebuilds the web app's gap problem natively. (c) is truly gapless with total control but you own decoding, A/V sync, audio rendering, and seeking — weeks of work for what (a) gives free. (a) is the designed-for-this API: N ranges from N files become one virtual asset in one decode pipeline; mixed codecs/frame-rates fine; rebuild after edit is milliseconds of edit-list manipulation.
**Pick:** (a), with (c) as the documented escape hatch behind the N2 gate.
**Confidence:** high, pending the N2 spike gates (that's what the spike is for). **Flips if:** N2 measurements fail — then (c), and the plan absorbs the cost early rather than at N10.

### D12 — Time representation
**Options:** (a) Double seconds at rest, CMTime for all media arithmetic (timescale 600 / track-native); (b) CMTime everywhere including the schema; (c) Double everywhere.
**Tradeoffs:** (b) breaks JSON round-trip parity and makes the domain layer AVFoundation-dependent. (c) accumulates rounding drift across edits — the exact bug class CMTime exists to prevent. (a) keeps schema parity and converts exactly once at the media boundary.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D13 — Trim-mode loop mechanism
**Options:** (a) manual loop — boundary time observer at window end + zero-tolerance re-seek; (b) `AVPlayerLooper`.
**Tradeoffs:** (b) fixes the time range at init — every ±1ms nudge would tear down and recreate the looper (and it pauses the player during init). (a) re-arms one observer per nudge; zero-tolerance seeks within an already-buffered 2s local window are tens of milliseconds on Apple Silicon. Escape hatches if restart latency ever bothers you: alternate pre-seeked player items, then compressed-sample-buffer caching.
**Pick:** (a).
**Confidence:** high. **Flips if:** N2 loop-latency measurement disappoints (unlikely on local files).

### D14 — Export strategy
**Options:** (a) tiered native — passthrough when lossless-and-frame-accurate is actually true (ProRes/all-intra, or keyframe-aligned cuts), VideoToolbox re-encode otherwise; ffmpeg demoted to mkv-remux duty; (b) FFmpeg concat pipeline per the original spec; (c) smart-cut (re-encode only cut GOPs) via a sidecar tool.
**Tradeoffs:** The spec's assumption ("nothing special about DaVinci's cuts") hid a real subtlety: for long-GOP H.264, *frame-accurate + lossless + universally-compatible* — pick two. FFmpeg concat `-c copy` snaps inpoints to keyframes (not frame-accurate). Native passthrough is frame-accurate via edit lists but non-Apple players may show lead-in frames. Re-encode is frame-accurate and compatible but one generation of loss. **ProRes dissolves the trilemma** (all-intra → passthrough is perfect) — worth knowing when you choose recording settings. (c) is the theoretical best for H.264 but the implementations are experimental/sidecar-grade.
**Pick:** (a): ProRes/all-intra → passthrough; H.264 → re-encode by default with passthrough opt-in (caveat surfaced); smartcut documented as post-v1 if H.264-lossless becomes a hard need. Spec amendment (plan §9.4).
**Confidence:** high on the tiering, medium on the H.264 default (re-encode vs passthrough-with-caveat as default is judgment — I chose the safe one). **Flips if:** your exports only ever target Apple-ecosystem playback → passthrough could be the H.264 default too.

### D15 — `.mkv` support
**Options:** (a) remux to `.mp4` at ingest (`ffmpeg -c copy`, lossless, seconds); (b) dual playback paths (AVFoundation + ffmpeg-based player for mkv); (c) drop `.mkv` from the accepted set.
**Tradeoffs:** AVFoundation cannot open Matroska at all, and third-party format plugins aren't possible. (b) means two playback engines forever for a container you don't currently use. (c) regresses the spec's accepted-extension decision.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D16 — ffmpeg acquisition
**Options:** (a) resolve from PATH/Homebrew via swift-subprocess `.name("ffmpeg")` + Settings path override; (b) bundle a signed ffmpeg in the app.
**Tradeoffs:** (a) is zero packaging work and fine for a personal machine that already has Homebrew; licensing is moot for undistributed software. (b) matters only if the app ever ships to others (then: LGPL build, dynamic linking, VideoToolbox encoders keep you out of GPL).
**Pick:** (a).
**Confidence:** high. **Flips if:** you distribute the app.

### D17 — Metadata probing
**Options:** (a) AVFoundation async property loading (`load(.duration)` etc.); (b) keep ffprobe.
**Tradeoffs:** (a) removes a subprocess + JSON-parse for every probe and is fully reliable on .mov/.mp4/.m4v; `.mkv` gets probed post-remux. Duration policy (sidecar wins → probe → null) ports unchanged. `nominalFrameRate` is an average — frame math uses `minFrameDuration` (this is a *fix* relative to ffprobe's r_frame_rate on VFR sources).
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D18 — Segmentation tail extension (backlog item: "clips cutting off short")
**Options:** (a) implement `end_sec` → next-word-start in native ingest AND run the one-shot widening pass over legacy clips at import (default on, snapshot first, reviewable); (b) implement for new ingests only, leave legacy clips short; (c) port parity-exact, fix later.
**Tradeoffs:** This changes data, not just code: widening moves every clip's `end_sec` outward, which lengthens attempt playback tails (that's the *point* — it's the complaint from dogfood) and recomputes continuity caches. (b) leaves the library inconsistent (old clips short, new clips right). (c) ports a known-wrong behavior into greenfield code. The import moment is the natural, snapshot-protected time to run it — exactly what the backlog entry proposed.
**Pick:** (a), as an import option defaulting on.
**Confidence:** medium-high. **Flips if:** you'd rather A/B the feel first — then (b) with the widening pass as a button you press after trying it.

### D29 — HDR / mixed color policy
**Options:** (a) detect + flag HDR per source at ingest, warn on first mixed-HDR/SDR attempt, accept-HDR-output export policy; (b) normalize everything to SDR at ingest; (c) ignore until it bites.
**Tradeoffs:** Your current footage is homogeneous (camera SDR), so this is a landmine for the *first* iPhone clip that enters the library, not a today-problem. If any composition segment is HDR, export converts SDR segments up. (b) is destructive-ish and premature.
**Pick:** (a) — cheap flag now, policy decision deferred until mixed footage actually exists.
**Confidence:** high (because it defers the real call). **Flips if:** iPhone footage becomes a primary source — then decide (b)-at-ingest vs HDR-project-policy for real.

### D31 — Audio micro-fades at cut boundaries
**Options:** (a) ~10ms `AVAudioMix` volume ramps at every boundary in preview; on export only via the re-encode path; (b) no fades (web parity — pops are physics at hard cuts); (c) full crossfade feature (spec polish layer).
**Tradeoffs:** (a) is a few lines in the composition builder and removes clicky cuts from every preview session; passthrough export can't apply fades (no re-render), which creates a small preview≠export gap on the passthrough tier — surfaced in the export UI. (c) is the spec's polish-layer item, deferred.
**Pick:** (a), toggleable.
**Confidence:** medium. **Flips if:** the preview≠passthrough-export mismatch annoys more than the pops did.

---

## D. App architecture

### D10 — State architecture
**Options:** (a) `@MainActor @Observable` AppStore + pure CFDomain functions + GRDB ValueObservation; (b) TCA; (c) store-as-actor.
**Tradeoffs:** TCA's payoff (reducer-level determinism, team-scale consistency) doesn't price in at solo scale, and it fights UndoManager/AppKit interop. Store-as-actor makes every SwiftUI read `await` — the pattern GRDB 7's main-actor ValueObservation exists to avoid. (a) is the 2026 default for exactly this kind of app and mirrors your existing architecture (pure domain + thin shell) most directly.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing realistic.

### D19 — Keyboard architecture
**Options:** (a) three layers — menu Commands / focused `onKeyPress` / modal NSEvent local monitor — with a single KeyMap registry; (b) SwiftUI-only (`keyboardShortcut` + `onKeyPress`); (c) monitor-only.
**Tradeoffs:** (b) is focus-dependent — a stray click silently kills trim-mode nudges; bare `[ ] , .` capture through the focus system is exactly where SwiftUI is still flaky. (c) loses menu discoverability and system conflict handling for the 90% of shortcuts that aren't modal. (a) uses each mechanism where it's strong; the KeyMap registry keeps bindings in one place (and makes user-remappable keys a future evening, not a rewrite).
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D20 — Transcript view implementation
**Options:** (a) STTextView (maintained open-source TextKit 2 NSTextView replacement) wrapped in NSViewRepresentable; (b) raw NSTextView/TextKit 2 hand-rolled; (c) pure SwiftUI text.
**Tradeoffs:** (c) degrades hard on 30-min interactive transcripts (thousands of tappable word spans; SwiftUI text cost grows with string length). (b) is the zero-dependency version of the right answer but re-implements selection/highlight plumbing STTextView already has. (a) adds one dependency, purpose-built for this.
**Pick:** (a), with (b) as fallback if STTextView fights the word-level interaction model in N3.
**Confidence:** medium-high. **Flips if:** N3 finds STTextView's APIs awkward for word-hit-testing — the fallback is planned, not a crisis.

### D21 — Concurrency posture
**Options:** (a) Swift 6.2 Approachable Concurrency, MainActor default isolation, explicit background services, swift-subprocess; (b) Swift 5-style pre-strict concurrency.
**Tradeoffs:** (a) is the Xcode 26 new-target default and eliminates the annotation storm for a UI app; (b) is accumulating debt against the ecosystem's direction.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D22 — LLM client implementation
**Options:** (a) hand-rolled URLSession+Codable clients for both providers, behind the existing dispatcher contract; (b) community SDKs (SwiftAnthropic, ollama-swift).
**Tradeoffs:** There is no official Anthropic Swift SDK (2026); community wrappers are fine but you'd track a third-party dependency against a moving API for two non-streaming endpoints. ~200 lines each, fully under your control, matching the provider-never-leaks-past-the-dispatcher invariant.
**Pick:** (a). Also: adopt Anthropic **structured outputs** (`output_config` JSON schema) over the web version's forced-tool-use — simpler, guaranteed-parse; keep prompt caching; verify param shape against current docs at N7.
**Confidence:** high on (a); medium on structured-outputs-vs-tool-use (both work; tool-use is the proven-in-this-codebase path — trivial to keep instead).
**Flips if:** streaming or complex tool flows ever matter — revisit SDKs then.

### D23 — Secrets storage
**Options:** (a) Anthropic key in Keychain; (b) port the chmod-0o600 settings file.
**Tradeoffs:** Keychain is the native answer, encrypted at rest, no file to leak into backups. Costs a few lines of Security-framework glue. Settings file was the best available answer *for a web server*; it isn't anymore.
**Pick:** (a).
**Confidence:** high. **Flips if:** nothing.

### D24 — Signing / sandbox / distribution
**Options:** (a) non-sandboxed, automatic dev signing, no notarization; (b) sandboxed; (c) Developer ID + notarization.
**Tradeoffs:** Sandboxing a tool whose job is reading arbitrary video folders and spawning ffmpeg buys security-scoped-bookmark bookkeeping for zero benefit at one user. Locally-built apps never get the quarantine attribute, so Gatekeeper/notarization is moot. Note: TCC still prompts once per folder-category (Desktop, etc.) even unsandboxed — sign with a stable identity so grants persist across rebuilds; optionally grant Full Disk Access once to silence everything.
**Pick:** (a).
**Confidence:** high. **Flips if:** you distribute → (c).

### D25 — Testing framework & strategy
**Options:** (a) Swift Testing (`@Test`/`#expect`) in ClipFarmKit via `swift test`; route tests → store-method contract tests; UI verified manually per the phase workflow; (b) XCTest; (c) add automated UI tests (XCUITest).
**Tradeoffs:** Swift Testing is the modern default and runs fast under bare `swift test`. Your phase workflow already ends every phase with manual verification — XCUITest automation is high-maintenance for a solo tool whose UI is churning; the 470-test domain parity carries the correctness load.
**Pick:** (a).
**Confidence:** high. **Flips if:** a regression class emerges that only UI tests would catch.

### D30 — Preview surface
**Options:** (a) persistent right-side inspector pane in the main window (toggleable, resizable), floating/detachable window post-v1; (b) floating utility window (web-pane parity); (c) dedicated player page.
**Tradeoffs:** The spec asks for a persistent preview that follows your last click across all pages — an inspector pane is the native idiom for exactly that, keeps window management at zero, and plays nicest with the focus/keyboard system (trim mode needs predictable focus). Floating windows on macOS invite focus ambiguity, which is poison for a keyboard-modal app.
**Pick:** (a).
**Confidence:** medium — partly taste; you liked the floating pane's always-on-top-ness. **Flips if:** dogfood at N3 misses the floating behavior — detachable pane is an additive change.

---

## E. Process & sequencing

### D26 — Transcription integration timing
**Options:** (a) keep the external `transcribe.py` sidecar contract through v1; WhisperKit in-app post-v1; (b) WhisperKit in-app from the start; (c) Apple SpeechTranscriber (macOS 26).
**Tradeoffs:** (b) adds model management + a heavy dependency to the critical path for a pipeline that already works overnight-batch style. (c) is fast and zero-dependency but benchmarks at whisper-small accuracy — a *downgrade risk* vs your existing quality expectations, and unproven word-timing granularity on your recordings. WhisperKit's word timings map ~1:1 onto your existing sidecar schema, and large-v3-turbo would be a *quality upgrade* over faster-whisper `small` — worth doing, not worth blocking v1 on.
**Pick:** (a).
**Confidence:** high. **Flips if:** the transcribe.py round-trip becomes the workflow bottleneck during construction.

### D27 — Fate of the web implementation
**Options:** (a) freeze now (no 10b/11 web work), keep runnable for dogfood + golden-master reference, retire at N13; (b) finish web 10b/11 in parallel; (c) delete immediately.
**Tradeoffs:** (b) builds the hardest remaining features (trim UI, export) twice — pure waste given the rewrite decision. (c) loses the dogfood tool during construction and the golden-master reference. (a) keeps you editing (web) while the replacement grows, with defined handoff points (native becomes the better browser after N3, better assembler after N9/N10).
**Pick:** (a). The pending web Phase 10a manual verify matters only as far as you need 10a features for ongoing dogfood — no formal verify cycle needed.
**Confidence:** high. **Flips if:** the native build stalls long enough that you need export urgently — then web Phase 11 (export) alone might be worth building as a stopgap.

---

## Summary table

| # | Decision | Pick | Conf. |
|---|---|---|---|
| D1 | UI framework | SwiftUI + AppKit hot spots | high |
| D2 | Min macOS | 26 (Tahoe) only | high |
| D3 | Port scope | Full native; Python becomes reference | high |
| D4 | Scaffolding | xcodeproj (buildable folders) + local SPM core | high |
| D5 | Repo | Same repo, `mac/` | med |
| D6 | Persistence | GRDB 7 / SQLite / FTS5 | high |
| D7 | Hand-editability | JSON export/import; watcher dissolved | high/med |
| D8 | Undo | UndoManager + DB snapshots | high |
| D9 | Migration | Importer, IDs verbatim, round-trip tested | high |
| D10 | State | @Observable @MainActor store; no TCA | high |
| D11 | Playback | AVMutableComposition + one AVPlayer | high* |
| D12 | Time | Double at rest, CMTime in engine | high |
| D13 | Trim loop | Boundary observer, not AVPlayerLooper | high |
| D14 | Export | Tiered: passthrough / re-encode; ffmpeg → mkv only | high/med |
| D15 | .mkv | Remux to mp4 at ingest | high |
| D16 | ffmpeg | PATH/Homebrew + override; not bundled | high |
| D17 | Probing | AVAsset replaces ffprobe | high |
| D18 | Segmentation tail | Fix + widening pass at import (default on) | med-high |
| D19 | Keyboard | 3-layer + KeyMap registry | high |
| D20 | Transcript view | STTextView (TextKit 2) wrapper | med-high |
| D21 | Concurrency | Swift 6.2 approachable, MainActor default | high |
| D22 | LLM clients | Hand-rolled URLSession; structured outputs | high/med |
| D23 | Secrets | Keychain | high |
| D24 | Signing | Non-sandboxed, auto-signed, no notarization | high |
| D25 | Testing | Swift Testing; manual UI verify per workflow | high |
| D26 | Transcription | Sidecar contract v1; WhisperKit post-v1 | high |
| D27 | Web app | Freeze now, retire at N13 | high |
| D28 | Data location | Visible `~/ClipFarm/`, overridable | med |
| D29 | HDR policy | Flag at ingest, defer real policy | high |
| D30 | Preview surface | Inspector pane; detachable later | med |
| D31 | Audio micro-fades | On in preview, re-encode export only | med |

*\* D11 pending the N2 measured gates — that's the point of the spike.*
