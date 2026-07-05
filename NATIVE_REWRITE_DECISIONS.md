# ClipFarm Native — Decision Log

*Companion to [`NATIVE_REWRITE_PLAN.md`](./NATIVE_REWRITE_PLAN.md).*

*Updated 2026-07-05 after **decision review round 1** with Lillian. Every entry now carries a **Status**: `LOCKED` (Lillian confirmed or directed), `RESOLVED-R1` (settled during review round 1 discussion — flag if you disagree with the resolution as written), or `OPEN` (needs a future call, with the trigger named).*

*Round 2 (2026-07-05): D14 revised (`RESOLVED-R2` — keyframe visibility, snap-to-keyframe mode, smart-cut promoted to phase N16; Track 2 renumbered N14–N19) and D20 gained a containment/swap-cost addendum. Everything else confirmed by Lillian; spec amendments landed in `clipfarm-spec.md`; per-project rules in `mac/CLAUDE.md`.*

*Round 3 (2026-07-05): the pre-build adversarial review was dispositioned (`PREBUILD_REVIEW_FINDINGS.md`) — 18 findings accepted; **D20 FLIPPED by Lillian to raw NSTextView** (STTextView is GPLv3/commercial, not MIT as previously claimed); **D33 added** (overlap allowed on adjust — Lillian's call on finding 9); **D32 added** (mixed-geometry policy); D6/D16/D24/D29/D31 annotated.*

**New standing constraint from review round 1 — commercial trajectory.** ClipFarm will eventually be offered as a **paid, directly-distributed Mac app**. v1 (Track 1, N0–N13) is built for Lillian; Track 2 (N14–N19) hardens for customers. This constraint shaped D8, D11, D16, D24, D26 below, and the rule it produces is: *build the seams now (locator abstractions, serializable keymaps, sidecar-format transcription), defer the packaging (bundling, notarization, payments) to N19 — but never make a v1 choice that walls off distribution.*

**Second standing directive:** capture maximum detail now (plan + as much code as possible in the coming days, with handoff-quality docs for whatever remains). The plan doc is written as the handoff artifact.

---

## A. Platform & scope

### D1 — UI framework — **Status: LOCKED**
**Pick:** SwiftUI shell + `NSViewRepresentable` at exactly three hot spots: transcript view (TextKit 2), modal key capture (NSEvent monitor), player surface.
**Why:** macOS 26 SwiftUI handles this scale (10k-item lists fine; ~6k clips comfortable); the known weak spots (giant interactive text, modal key capture) have established AppKit drop-ins; pure AppKit is ~2–3× UI code for no gain; a web-shell keeps every limitation we're escaping.

### D2 — Minimum macOS version — **Status: LOCKED**
**Pick:** macOS 26 (Tahoe) minimum. No older-Mac support needed.
**Commercial note:** revisit the floor at N19 for the customer base — by any plausible ship date macOS 26 is old news, so this likely never changes.

### D3 — Port scope — **Status: LOCKED (never flipping)**
**Pick:** Full native Swift port, including all domain logic. No retained Python backend under any circumstances.
**Addendum from review (adjudication rule):** the Python implementation is the *reference*, not the *oracle*. When a ported test fails, investigate which side is wrong **against the spec** before assuming the Swift code is at fault — the web implementation has known warts, and a port-test failure may be surfacing one. Divergences get recorded in the phase entry.

### D4 — Project scaffolding — **Status: LOCKED**
**Pick:** Plain `.xcodeproj` with Xcode 16+ buildable folders (new files never touch pbxproj) + local SPM package `ClipFarmKit` holding ~90% of code (fast `swift test` CLI loop). No Tuist/XcodeGen (team-scale tools); no pure-SPM app (can't produce a proper signed bundle).

### D5 — Repo location — **Status: LOCKED**
**Pick:** Same repo, `mac/` directory. Web implementation stays as the porting/golden-master reference; delete it whenever we want post-port.

---

## B. Data & persistence

### D6 — Persistence engine — **Status: LOCKED**
**Pick:** GRDB 7 (SQLite), WAL mode, FTS5 for transcript search.
**Why:** SwiftData has undo-related crashes on macOS 26 — Apple-acknowledged (FB22539495; corroborating forum threads 822241 / 756757), thinner sourcing than "documented" originally implied but still disqualifying for an undo-heavy editor. Core Data is boilerplate with no upside over GRDB. JSON at ~6k clips is full-file rewrite per save — the cost the spec already flagged. GRDB: Swift-6-ready, main-actor `ValueObservation` for SwiftUI, `DatabaseMigrator` (analog of `clipfarm/migrations/`), FTS5 upgrades search from substring-only to phrase/prefix for free.

### D7 — Hand-editability — **Status: RESOLVED-R1**
**What this was about (the explanation Lillian asked for):** the original spec — written before any code — made "open `clipfarm.json` in a text editor and change anything, live" a core invariant: edit a clip's `start_sec`, flip a tag's `stale` flag, delete an attempt, by hand, while the app runs. The entire watcher/conflict-modal/freeze machinery (and several hard-won race fixes across web phases 6–10a) existed **solely to make that safe**. In practice it was used ~never — the one recorded instance was a reviewer suggesting a hand-flipped `stale` flag to test the amber-dot UI. For a paid app, customers hand-editing the database is a support nightmare, not a feature.
**Resolution:** live hand-editing is retired. Kept instead: (a) inspection via `sqlite3` / DB Browser / Datasette; (b) `File → Back Up Library…` — full-state JSON export in the familiar clipfarm.json shape, git-diffable — with a tolerant restore path; (c) in-app affordances for anything that used to justify a hand-edit (and a small debug-edit panel is an evening's work later if ever missed). A whole class of complexity (watcher, conflict modal, freeze, dirty-flag discipline) dies with this. Spec amendment §9.2.

### D8 — Undo model — **Status: RESOLVED-R1**
**Lillian's question:** given eventual paid distribution, should we invest in a full command-pattern framework (option C)?
**Answer:** no — `UndoManager` **is** a command-pattern implementation, provided by the OS, integrated with the Edit menu, per-window stacks, grouping and coalescing. It is what shipping commercial Mac apps use. A custom framework buys nothing a paying customer can see. The commercial-grade investment is **coverage and naming**: every mutation undoable, menu items read "Undo Split Clip" / "Undo Nudge In-Point", nudge bursts coalesce into one undo step. That's discipline, not architecture.
**Pick:** UndoManager with before-value snapshots per mutation + `VACUUM INTO` DB snapshots before destructive ops (snapshots also survive crashes, which the undo stack doesn't).

### D9 — Data migration — **Status: LOCKED (flipped in R1)**
**Was:** one-time importer, IDs preserved. **Now:** no production importer. Dogfood state is one halfway attempt — not worth the code. The native library starts empty: re-ingest source folders (minutes), re-tag (~30s Anthropic path), regenerate premades.
**Kept:** a ~100-line **test-only fixture loader** that reads legacy `clipfarm.json` in the test target, so golden-master tests can compare Swift vs Python domain output on identical state. Not a product feature.

### D28 — Library location — **Status: LOCKED**
**Pick:** Visible folder, default `~/ClipFarm/` (contains `clipfarm.db`, `.snapshots/`, `cache/`), overridable in Settings.

---

## C. Media engine

### D11 — Playback architecture — **Status: RESOLVED-R1 (pending N2 gates by design)**
**Lillian's question:** is the custom pipeline (C) really that much work? Lean toward the hard thing now for a truly great app.
**The honest cost of C** (`AVSampleBufferDisplayLayer` + `AVSampleBufferRenderSynchronizer` + `AVAssetReader`): you own demuxing (readers are forward-only — every seek rebuilds them), decode scheduling and buffer memory, audio rendering, A/V sync, rate control, bidirectional scrubbing, HDR tone mapping, color management, external-display behavior. Realistically **4–8 weeks to solid** plus a permanent maintenance surface — and it would not produce a better app for this job. Composition playback is not the easy compromise; it is the professionally-designed API for exactly this task. "Do the hard thing now" pays when the easy thing has a ceiling — the N2 spike *is* the ceiling test, run before any UI exists.
**Resolution:** (a) `AVMutableComposition` + single `AVPlayer`, with a hard commitment: **if any N2 gate fails, pivot to C at N2** — re-plan it as a real engine phase while it's cheap, not at N10. Also noted: `AVPlayerVideoOutput` adds custom per-frame GPU rendering (overlays, effects — future commercial polish) *on top of* AVPlayer timing, so "great app someday" doesn't require C either.

### D12 — Time representation — **Status: RESOLVED-R1**
**Lillian's question:** why does this matter / what breaks?
**Explanation:** option B would store rational time (integer value + timescale) in the DB. But timestamps *originate* as floating-point seconds (Whisper emits floats) — rational storage adds no truth to float-born data. A Double carries 15–17 significant digits: sub-microsecond exactness for any timestamp under a day. The real hazard is *iterative accumulation* (thousands of back-and-forth conversions during arithmetic); the rule "convert once at the media boundary, all arithmetic in `CMTime` inside the engine" eliminates it. (The old "JSON round-trip parity" argument is moot now that D9 dropped the importer — the remaining reasons are schema simplicity and human readability, at zero precision cost.)
**Pick:** Double seconds at rest; `CMTime` (timescale 600 / track-native) for all media arithmetic.

### D13 — Trim-mode loop mechanism — **Status: RESOLVED-R1**
**Lillian's question:** what would B (`AVPlayerLooper`) be good for at all?
**Answer:** looping a *fixed* clip forever — think menu-screen background video. Its time range is fixed at creation; changing the window means destroy-and-recreate, and it pauses the player while doing it. Trim mode changes the window on every keystroke, so B has literally zero benefit here.
**Pick:** manual loop — one boundary time observer at window end + zero-tolerance re-seek; nudges re-arm the observer. Escalation path if restart latency ever bothers: alternate pre-seeked player items, then compressed-sample-buffer caching.

### D14 — Export strategy — **Status: RESOLVED-R2 (revised in round 2 — the iPhone back-catalog is the driving case, not ProRes)**
**The explainer Lillian asked for (long-GOP H.264):** codecs like H.264/HEVC store most frames as *differences from neighboring frames*. A GOP ("group of pictures") starts with a keyframe — a complete image — followed by seconds of diff-frames meaningless without it ("long GOP" = keyframes seconds apart). Consequences: (1) you cannot start a *copied* (non-re-encoded) stream at an arbitrary frame — its first frames would reference data you cut off; (2) lossless cutting can therefore only start at keyframes (why FFmpeg `-c copy` snaps); (3) Apple's workaround (edit lists) copies the extra lead-in but marks it "don't display" — Apple players honor this, some third-party players show the extra frames; (4) re-encoding regenerates every frame, so cuts land anywhere, at one generation of quality cost. **ProRes stores every frame complete** (all-intra) → cut anywhere, losslessly, natively. That trilemma — frame-accurate / lossless / universally-compatible, pick two (for long-GOP) — is the entire reason for tiering.
**Round 2 (Lillian's follow-ups — lots of existing iPhone H.264 footage; ProRes can't help footage already shot):**
- **Preview is completely unaffected.** The GOP/keyframe constraint applies *only* to producing an exported file without re-encoding. In-app decode is frame-exact for every codec — the player decodes from the previous keyframe internally and displays exactly the requested frame. "Third-party players" means only whatever reads the *exported file*: VLC, Windows players, and — notably — upload ingest pipelines (YouTube/TikTok re-encode server-side; their demuxers are exactly the non-Apple readers that can mishandle edit lists).
- **Re-encode is not a scary default.** Every mainstream NLE re-encodes long-GOP timelines on export; at ~2× source bitrate via VideoToolbox it's visually transparent for talking-head content. And uploads get re-encoded by the platform regardless — generation loss really only matters for archival/local masters, which is what smart-cut is for.
- **Keyframes made visible (Lillian's idea, adopted):** trim mode shows the source's keyframe ticks (`KeyframeMapService` — AVSampleCursor sync-sample enumeration, cached per source), and the export dialog reports per-cut alignment ("11 of 14 cuts are keyframe-aligned"). **UX requirement (2026-07-05):** alignment info is only actionable when choosing Lossless/Smart, so the primary UI phrases it as outcomes ("these cuts can export with zero quality loss") with a hover-"?" plain-language explainer; raw keyframe jargon appears only in trim mode's ticks. Standard mode always cuts exactly on the chosen frame, on any footage.
- **Snap-to-keyframe mode (Lillian's "with-keyframe mode", adopted):** an optional nudge mode that lands cuts exactly on keyframes, making true-lossless passthrough available on any footage. Honesty note: iPhone keyframes are typically ~1s apart, so snapping can move a cut by up to ~half a second — a visible, deliberate tradeoff (the ticks show exactly what you're trading).
- **"Move the keyframe to the cut" (Lillian's idea — this IS smart-cut, promoted to its own phase N16):** keyframes baked into the recording can't be relocated, but one can be *created* at the cut by re-encoding only the short stretch from the cut point to the next keyframe, stream-copying everything else. Frame-accurate, ~lossless (a second or two re-encoded per cut), universally compatible. The hard part is bitstream-parameter alignment at splice points — research-grade, hence a dedicated phase with reference implementations named.
**Pick (revised):** an **export mode picker** — **Standard** (re-encode; WYSIWYG; universal; default for long-GOP) / **Lossless** (passthrough; offered clean when all cuts are keyframe-aligned or sources are all-intra, otherwise with the edit-list warning) / **Smart** (arrives with N16). The hybrid writer keeps audio WYSIWYG (D31) in every mode that copies video; mixed sources always re-encode with the D29 color target. The ProRes note stands for *future* recordings only.

### D15 — `.mkv` support — **Status: LOCKED**
**Pick:** remux to `.mp4` at ingest (`ffmpeg -c copy`, lossless, seconds). AVFoundation cannot open Matroska and plugins aren't possible; dual playback engines is forever-cost for a container not currently in use.

### D16 — ffmpeg acquisition — **Status: RESOLVED-R1**
**Lillian's input:** will distribute (paid) eventually — build A now and revisit, or flip now?
**Resolution:** keep **A now** (resolve from PATH/Homebrew via swift-subprocess, Settings path override) — but behind an **`FFmpegLocator` seam** so the N19 flip to a bundled, signed LGPL build (or eliminating the dependency by gating .mkv) is a packaging change, not architecture. Deferring is safe *because* the seam exists; that's the pattern for every distribution-affected choice. Licensing when bundling: LGPL build (no `--enable-gpl`), dynamic linking; VideoToolbox hardware encoders live in LGPL core, so x264 is never needed.
**Post-review note (finding 5):** N16 smart-cut muxes through ffmpeg as its primary path, so the dependency is effectively permanent — at N19, bundle the signed LGPL build; the "eliminate by gating .mkv" branch is dead.

### D17 — Metadata probing — **Status: LOCKED**
**Pick:** AVFoundation async property loading replaces ffprobe (`.mkv` probed post-remux). Duration policy (sidecar wins → probe → null) ports unchanged. Frame math uses `minFrameDuration`, never `nominalFrameRate` (average — wrong on VFR iPhone footage; this is a *fix* vs ffprobe's r_frame_rate).

### D18 — Segmentation tail behavior — **Status: RESOLVED-R1 (reframed)**
**Was:** implement the tail fix + a one-shot widening migration over imported legacy clips. **Both halves obsolete**: D9 killed the import (no legacy clips exist natively), and Lillian's point reframed the feature — tail behavior *varies by speaker and enunciation*, so it's a **setting, not a constant**.
**Resolution:** per-library **segmentation settings**: silence threshold (default 2.0s) and tail policy (extend-to-next-word-start default / fixed padding +N ms / word-end). Plus a **"Re-apply segmentation settings" action per source**: recomputes boundaries for auto-detected clips, **skips any clip with the new `boundary_edited` flag** (set by every hand boundary-correction, so re-apply never clobbers manual work), snapshot-protected, undoable. Change the setting, re-apply, listen, undo if worse — tunable per person.

### D29 — Mixed HDR/SDR footage — **Status: RESOLVED-R1 (was deferred; now decided — mixed footage already exists)**
**The choices, since Lillian has real iPhone HDR + camera SDR material:**
1. *Native everything*: preview tone-maps per display automatically; but if any clip in an export is HDR the whole export becomes HDR and SDR clips get converted up (subtle look shift).
2. *Normalize at ingest*: transcode every HDR source to an SDR working copy once — consistent forever, but costs storage/time and discards HDR headroom.
3. *Export-time policy*: preview plays everything native (tone-mapped, looks right); mixed-source exports go through the re-encode tier anyway (mixed sources can't passthrough into one output regardless), so the export dialog carries an explicit output color target — **default SDR** (sane for web/social talking-head delivery), HDR offered when sources allow.
**Pick:** (3). Zero ingest cost, nothing destroyed, decision lives where it takes effect. HDR flag captured per source at ingest so the dialog can be smart.
**Post-review note (finding 3):** none of this is automatic — a bare composition has no documented per-segment tone-mapping for mixed dynamic ranges, and export converts SDR segments *up* to HDR unless color properties are explicitly set. Explicit videoComposition color properties on both preview and export paths are a stated requirement; HDR↔SDR seams are an N2 gate.

### D31 — Audio micro-fades at cuts — **Status: RESOLVED-R1 (WYSIWYG rule)**
**Lillian's requirements:** preview must match the final video; wants it as a setting.
**Resolution:** one **"smooth cut audio" setting, default ON, honored identically by preview and export** — WYSIWYG is the rule, not a tier side-effect. Technical unlock: video and audio are independent tracks in the output writer, so the export can **copy video samples losslessly while re-encoding only the audio** with the same ~10ms fades applied (high-bitrate AAC — effectively transparent). Fades ON → hybrid-writer path (video still lossless where sources allow); fades OFF → pure passthrough available. Either way the file sounds exactly like the preview did. Cost: the hybrid writer path makes N12 moderately bigger than a one-line export preset — accepted for the truly-great-app bar.
**Post-review note (finding 4):** the hybrid path's frame accuracy is edit-list-based, so "preview == file" is **universal on Standard, Apple-player-verified on Lossless/hybrid** (ignoring demuxers desync A/V rather than just showing lead-in frames) — the mode-picker explainer states this. Hand-encoded AAC needs `TrimDurationAtStart` priming attachments or A/V drifts ~one frame.

### D32 — Mixed-geometry (rotation/size) policy — **Status: RESOLVED-R3 (added at the pre-build disposition)**
**Why it exists:** finding 2 falsified plan §2.5's original claim — `preferredTransform` is track-level, so portrait iPhone + landscape camera in one composition track cannot render correctly with it alone; per-segment transforms require an `AVMutableVideoComposition` (QA1744), and passthrough export ignores videoComposition transforms.
**Options:** (a) videoComposition always — own renderSize/frame policy everywhere, passthrough never available; (b) normalize rotation at ingest — destructive/expensive transcodes; (c) **conditional** — bare composition when geometry is uniform, videoComposition (per-segment transforms, project-canvas renderSize, portrait clips pillarboxed by default) when mixed.
**Pick:** (c) — the same shape as every other tiering decision: uniform sources keep the Lossless door open; mixed geometry (like mixed color) routes to the re-encode tier with correct rendering. Pillarbox default, fill-crop as a later option. Mixed-rotation render is an N2 gate; geometry joins keyframe alignment and codec/color uniformity in N12's Lossless-eligibility check.
**Confidence:** high. **Flips if:** the N2 gate shows videoComposition-attached playback misbehaving — then (a), with the tier consequences accepted.

### D33 — Overlap on boundary-adjust — **Status: LOCKED (Lillian, finding-9 disposition 2026-07-05)**
**The contradiction (verified in the Python code):** create-clip-from-range allows overlap on the same source (the Phase 10a 30s-take + 10s-highlight case), but adjust-boundaries rejected *any* resulting overlap — so deliberately overlapping clips could never be nudged; N11's trim mode would have hit this immediately.
**Pick:** **adjust allows overlap**, matching create — overlap is simply a legal state on a source; only merge rejects overlapping ranges (the operation is undefined for them). Deliberate divergence from the Python reference; ported with a named test for the previously-frozen case. Rejected alternatives: reject-only-*new*-overlaps (guardrail, but more complex and inconsistent with create), keep-as-is (a known landmine).

---

## D. App architecture

### D10 — State architecture — **Status: LOCKED**
**Pick:** `@MainActor @Observable` AppStore + pure CFDomain functions + GRDB ValueObservation. No TCA (its payoff is team-scale; it fights UndoManager/AppKit interop). No store-as-actor (makes every SwiftUI read `await`).

### D19 — Keyboard architecture — **Status: LOCKED**
**Pick:** three layers — menu Commands / focused `onKeyPress` / modal NSEvent local monitor — with a single **KeyMap registry, serializable from day one** (bindings are data, not code). **User-remappable keys is on the roadmap (N19)** per Lillian, and lands as a settings UI over the registry, not a refactor.

### D20 — Transcript view — **Status: FLIPPED-R3 (Lillian, pre-build disposition) — raw NSTextView/TextKit 2, no STTextView**
**Pick (flipped at R3):** raw NSTextView / TextKit 2 wrapped in NSViewRepresentable, hand-rolling the selection/highlight plumbing (a few extra days at N4). **Why the flip:** the pre-build review (finding 1) falsified the premise this decision rested on — STTextView is dual-licensed **GPLv3 / paid commercial**, not MIT; there is no vendoring escape hatch. v1 use would have been legal (GPL attaches at distribution), but with paid distribution certain, Lillian chose zero license entanglement and one less single-maintainer dependency now over resolving it at N19.
*(Original R1 pick, for the record: STTextView wrapped in NSViewRepresentable, NSTextView as fallback.)*
**Round-2 addendum (one-maintainer risk / swap cost — Lillian's question):** STTextView is contained behind a single **`TranscriptViewAdapter` seam we own** (operations: set attributed content, word hit-test at point, apply/clear highlight ranges, selection callbacks, scroll-to-word). Nothing outside that one wrapper file may import or reference STTextView. Swap cost is therefore re-implementing the adapter on raw NSTextView/TextKit 2 — a few days, mostly highlight plumbing — with zero changes anywhere else in the app. ~~Additional insurance: pinned version + MIT vendoring.~~ **(The MIT claim was wrong — see the flip above.)** The `TranscriptViewAdapter` seam survives the flip as module hygiene: the app talks to the adapter contract (set content / word hit-test / highlight ranges / selection events / scroll-to-word), never to the text view directly.
**The tradeoff, generalized (relevant to Lillian's separate writing app):** NSTextView is first-party with *decades* of editing behavior baked in — IME, spellcheck, find, text-level undo — but its TextKit 2 mode still has gaps where APIs silently fall back to TextKit 1, and its customization points are old-school. STTextView is a from-scratch NSTextView *replacement* on pure TextKit 2: modern API, no fallback paths, built-in highlight/selection plumbing — but third-party (essentially one maintainer; you inherit its conventions and bugs). Rule of thumb: **real text editor (the writing app) → NSTextView's editing maturity wins. Read-mostly view with custom word-level interaction (ClipFarm's transcript) → STTextView saves the plumbing.** Different apps, different right answers.

### D21 — Concurrency — **Status: LOCKED**
**Pick:** Swift 6.2 Approachable Concurrency, MainActor default isolation; explicit background services (Thumbnail/Waveform/LLM/Export/Transcription); swift-subprocess for external binaries.

### D22 — LLM clients — **Status: LOCKED**
**Pick:** hand-rolled URLSession+Codable clients behind the existing provider-agnostic dispatcher (no official Anthropic Swift SDK exists; community wrappers not worth tracking for two non-streaming endpoints). Anthropic path uses **structured outputs** (verify param shape against current docs at N7; forced-tool-use is the proven fallback); prompt caching kept.

### D23 — Secrets — **Status: LOCKED**
**Pick:** Anthropic key in Keychain. (The chmod-0o600 file was the best answer *for a web server*; it isn't anymore.)

### D24 — Signing / sandbox / distribution — **Status: RESOLVED-R1**
**Lillian's input:** wants to distribute; open on where it enters the process.
**Resolution:** it enters at **N19**, and nothing in v1 has to change to keep that door open. v1: non-sandboxed, automatic dev signing, no notarization (locally-built apps never get quarantined; TCC prompts once per folder-category — stable signing identity makes grants persist). N19: Developer ID + hardened runtime + notarization + **direct distribution** (Paddle/Lemon Squeezy-class merchant-of-record payments, Sparkle updates) — deliberately **not** Mac App Store, because MAS forces sandboxing, which is hostile to an app that reads arbitrary video folders and spawns helpers; most pro Mac media tools ship non-sandboxed outside MAS. Business-model note (N19): bring-your-own-key + Ollama default keeps commercial v1 simple; a bundled-token proxy is a later business decision. **N0 note (finding 19f):** TCC grant persistence requires a real Apple Development certificate — ad-hoc "Sign to Run Locally" re-signs each build and re-prompts.

### D25 — Testing — **Status: LOCKED (A for now)**
**Pick:** Swift Testing in ClipFarmKit via `swift test`; route tests → store-method contract tests; UI verified manually per the phase workflow. Revisit XCUITest only if a UI-only regression class emerges (plausible around N19 onboarding).

### D30 — Preview surface — **Status: LOCKED**
**Pick:** persistent right-side inspector pane; detachable/floating window as a later additive nicety.

---

## E. Process & sequencing

### D26 — Transcription integration — **Status: RESOLVED-R1 (reframed by distribution)**
**Lillian's question:** how does this relate to distribution goals?
**Answer:** it's the decision distribution flips hardest. A paid app **cannot ask customers to run a Python script** — in-app transcription is a *commercial requirement*, not a nice-to-have. Hence: **N14 = WhisperKit, first phase of Track 2** (large-v3-turbo — a quality *upgrade* over the current faster-whisper `small`; word timings map ~1:1 onto the sidecar schema). The `.whisper.json` sidecar stays the interchange format, so transcribe.py keeps working for Lillian's overnight-batch workflow and WhisperKit output is written as the same sidecars. Apple `SpeechTranscriber` (macOS 26): benchmark-only for now — fast, zero-dependency, but whisper-small-class accuracy.
**v1 (Track 1) unchanged:** sidecar contract, external transcription — it unblocks everything else and the phase-order cost of pulling N14 earlier is real. Flip trigger: if the transcribe.py round-trip becomes the bottleneck *during construction*, N14 can slot in right after N13 regardless of other Track 2 ordering (it already gates commercial viability, so it's first in Track 2 anyway).

### D27 — Fate of the web implementation — **Status: LOCKED (updated in R1)**
**Reality check from Lillian:** the web app is not being dogfooded — it's too janky (hence the rewrite). So it is **reference-only**: frozen in the tree for porting + golden-master comparison, never run as a tool, no Phase 10a verify ceremony, deletable whenever we feel like it post-port. There is no dogfood-handoff schedule — the native app becomes the only tool as soon as each capability lands.

---

## Summary table

| # | Decision | Pick | Status |
|---|---|---|---|
| D1 | UI framework | SwiftUI + AppKit hot spots | LOCKED |
| D2 | Min macOS | 26 only (revisit floor at N19) | LOCKED |
| D3 | Port scope | Full native; Python = reference, not oracle | LOCKED |
| D4 | Scaffolding | xcodeproj (buildable folders) + local SPM core | LOCKED |
| D5 | Repo | Same repo, `mac/`; delete web post-port someday | LOCKED |
| D6 | Persistence | GRDB 7 / SQLite / FTS5 | LOCKED |
| D7 | Hand-editability | Retired; inspect + Backup/Restore JSON | RESOLVED-R1 |
| D8 | Undo | UndoManager (it IS the command pattern) + DB snapshots | RESOLVED-R1 |
| D9 | Migration | None — fresh library, re-ingest + re-tag; test-only fixture loader | LOCKED |
| D10 | State | @Observable @MainActor store; no TCA | LOCKED |
| D11 | Playback | Composition + AVPlayer; pivot to custom pipeline AT N2 if gates fail | RESOLVED-R1 |
| D12 | Time | Double at rest, CMTime in engine (convert once) | RESOLVED-R1 |
| D13 | Trim loop | Boundary observer; AVPlayerLooper has zero benefit here | RESOLVED-R1 |
| D14 | Export | Mode picker (Standard/Lossless/Smart) + keyframe ticks & snap mode; smart-cut = N16 | RESOLVED-R2 |
| D15 | .mkv | Remux to mp4 at ingest | LOCKED |
| D16 | ffmpeg | PATH now behind FFmpegLocator seam; bundle at N19 | RESOLVED-R1 |
| D17 | Probing | AVAsset replaces ffprobe | LOCKED |
| D18 | Segmentation tail | Per-library setting + per-source re-apply, respects `boundary_edited` | RESOLVED-R1 |
| D19 | Keyboard | 3-layer + serializable KeyMap; remappable keys at N19 | LOCKED |
| D20 | Transcript view | FLIPPED: raw NSTextView/TextKit 2 behind the adapter (STTextView is GPL/commercial) | FLIPPED-R3 |
| D21 | Concurrency | Swift 6.2 approachable, MainActor default | LOCKED |
| D22 | LLM clients | Hand-rolled URLSession; structured outputs | LOCKED |
| D23 | Secrets | Keychain | LOCKED |
| D24 | Signing | Dev-signed v1 → Developer ID + notarization + direct sales at N19 | RESOLVED-R1 |
| D25 | Testing | Swift Testing; manual UI verify per workflow | LOCKED |
| D26 | Transcription | Sidecar v1; WhisperKit = N14, commercial requirement | RESOLVED-R1 |
| D27 | Web app | Reference-only, never dogfooded, delete whenever | LOCKED |
| D28 | Data location | Visible `~/ClipFarm/`, overridable | LOCKED |
| D29 | HDR policy | Preview native; export color target, default SDR | RESOLVED-R1 |
| D30 | Preview surface | Inspector pane; detachable later | LOCKED |
| D31 | Audio micro-fades | WYSIWYG setting (default on) for preview AND export; hybrid writer | RESOLVED-R1 |
| D32 | Mixed geometry | Conditional videoComposition; pillarbox default; gates the Lossless tier | RESOLVED-R3 |
| D33 | Overlap on adjust | Allowed (matches create); only merge rejects overlap | LOCKED |
