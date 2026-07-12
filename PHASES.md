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

## Phase N3 — Ingest (native rewrite) — **Built ✅ 2026-07-06 (manual verify DEFERRED to the N4 hard stop)**

> **Status: BUILT + cold review dispositioned.** 232 tests green (156 baseline + 76 new; 11 review findings — 9 fixed incl. 1 MAJOR, 2 accepted-as-noted with owners). **Golden masters PASS on real data** — Swift segmentation reproduces the web version's clips bit-for-bit on the three inbox sources (10/91/8 clips; IDs, ranges, texts), both as a pure-domain diff vs the legacy `clipfarm.json` and as an end-to-end `ingestFolder` over the real inbox. swift-subprocess landed pinned `1.0.0-beta.1` (pre-approved), both lockfiles committed. Closeout (incl. the deferred manual-verify checklist, 5 PROVISIONALs, environment findings for the N4 session, and the N4 delta): [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase N3. **The N4 kickoff is queued in `KICKOFF_MESSAGES.md`.** Deferred-verification debt: 1 phase. The plan entry below is the committed pre-implementation plan (audit trail).

**Goal (plan §4/N3):** point at a folder, get sources + clips — natively, with segmentation as a tunable setting (D18). Real data lives in the native app from here on (D9: no importer). **Tier: auto-continue** — manual verify DEFERRED to the N4 combined hard stop (including the "clips no longer feel cut short" listening check).

**Port map:** `ingest.py` (pairing, rejection semantics: `__`-in-stem hard-reject with rename offer; sidecar soft-fail → footage-only; re-ingest upgrade path; idempotency), `segmentation.py` (gap `>=` threshold — load-bearing comparison, tested by name), `transcripts.py → transcript_text_for_range` (half-open `[start, end)`, leading-space concat, strip), duration policy (sidecar wins → probe → null). `ffprobe.py`/`test_ffprobe.py` parsing tests are **obsolete by D17** (no ffprobe); the semantics that port are the fallback behaviors (probe failure → nil fps/duration, source still ingests) and they port into the ingest/probe-adapter tests.

### Scope

1. **Schema v2 (own commit, schema rule):** `sources.original_path TEXT NULL` — provenance for `.mkv` sources remuxed at ingest (plan §4/N3: "the original `.mkv` path is kept as provenance — default reviewable in this phase's plan"). `Source.originalPath` entity field + record + `original_path` Codable key. v1 migration's meta stamp changes to literal `'1'` (semantics unchanged for fresh DBs; no live library exists — `~/ClipFarm/clipfarm.db` has never been created); v2 stamps `'2'`.
2. **swift-subprocess dependency (pre-approved by Lillian 2026-07-06, QUESTIONS.md → Answered):** pinned `.exact("1.0.0-beta.1")`, Apache-2.0, dependency of **CFExport** only (the module map assigns ffmpeg/mkv-remux to CFExport). Both `Package.resolved` lockfiles re-committed. `FFmpegLocator` (Settings-path override hook now unused → PATH/Homebrew-ARM/Homebrew-Intel/system search, D16) + `MKVRemuxer` (`ffmpeg -map 0:v? -map 0:a? -c copy`, **streaming stderr** per D16, write to temp sibling then atomic-rename to `<stem>.mp4`, skip-if-exists).
3. **CFDomain `Segmentation.swift` (pure):** `segmentWordsBySilence(words:gapThresholdSec:)` port + `applyTailPolicy(to:words:policy:paddingSec:sourceDurationSec:)` (D18 three modes) + `WhisperTranscript.transcriptText(from:to:)` port. `ProbedSourceInfo` value type lives in CFDomain so CFMedia (producer) and CFStore (consumer) share it without a new module edge.
4. **CFStore ingest (`Ingest.swift`):** `ingestFolder(at:probe:remux:)` — two passes: pass 1 (async, no writes) scans/remuxes/loads sidecars/probes into a plan; pass 2 (sync, `@MainActor`) applies all store writes inside **one undo group** (“Ingest Folder”). Injected `probe: (URL) async -> ProbedSourceInfo?` and `remux: (URL) async throws -> URL` closures keep CFStore free of AVFoundation/subprocess (and mirror how the Python tests stub `probe_video`). New store mutation `updateSource(id:_:)` (undoable, "Update Source") carries the transcript-upgrade path. Segmentation reads `LibrarySettings` (threshold + tail policy). FTS5 rows come free from the N1 triggers (asserted by test). Sources store `fps = nominalFrameRate` (display-only, N2 delta), `is_hdr`, `natural_width/height` from the probe.
5. **CFStore re-apply (`ReapplySegmentation.swift`):** `reapplySegmentation(forSourceID:)` — recompute ranges with current settings; **skip clips with `boundary_edited`**; ID-preserving diff (see PROVISIONAL 3); explicit `clip_project_tags` cleanup for removed clips + `needs_review` flip on attempts referencing them (the spec's delete-propagation rule, applied forward); snapshot-protected (`performDestructive`, reason `re-apply-segmentation`) + undoable ("Re-apply Segmentation"); true no-op takes **no** snapshot.
6. **CFMedia:** `SourceMetadata.probedSourceInfo` adapter (duration seconds, `fps = nominalFrameRate`, `isHDR`, naturalSize) + **`WaveformService`** (actor): AVAssetReader over the FIRST audio track only (N2 delta: spatial second tracks exist), LPCM Float32, vDSP (`vDSP_maxmgv`) peak per bucket at 100 buckets/sec (plan's 50–100 range), binary cache file (`CFWV` magic + version + rate + count + Float32 LE) under `<library>/cache/waveforms/<sourceID>.waveform`, skip-if-exists, no-audio → empty (0-bucket) file so N11 can distinguish "no audio" from "not ready". Run async post-ingest by the app; ingest never blocks on it.
7. **App target:** minimal `@MainActor @Observable AppStore` (opens the default library `~/ClipFarm/` with the window's `UndoManager`, creates the **footage inbox `~/ClipFarm/Footage/`** on first run per D34, wires probe/remux closures from CFMedia/CFExport, kicks off waveforms post-ingest); Library page ingest UI — "Ingest Folder…" via `NSOpenPanel` **defaulting to the inbox** + drag-a-folder-onto-the-Library-view (`dropDestination`), source list (filename, duration, fps, clip count, footage-only note, unavailable greyed) with per-source "Re-apply Segmentation", ingest summary incl. rejections/warnings; Settings page gains the D18 segmentation section (threshold, tail policy, padding) writing through `updateLibrarySettings`.

### PROVISIONAL calls (logged in QUESTIONS.md)

1. **`.mkv` provenance representation.** Options: (a) nullable `sources.original_path` column (schema §2.3 amendment, plain and queryable); (b) piggyback into a JSON/notes blob (schema-opaque); (c) don't persist (violates provenance-forever). Implemented **(a)** — the plan text explicitly promises the original path is "kept as provenance" and makes the mechanism this phase's reviewable default.
2. **Fixed-padding tail clamping (D18 `.fixedPadding`).** Options: (a) clamp padded end to the next clip's first-word start AND to source duration when known; (b) unclamped (`end + padding` verbatim — can swallow the next take's onset); (c) clamp to duration only. Implemented **(a)** — padding that eats the next clip's first word would create overlapping auto-detected clips and audibly clip the next take's onset; the resolver's source-bounds clamp already argues ends should stay in-file.
3. **Re-apply semantics.** Options: (a) wholesale replace (delete every non-`boundary_edited` clip, insert all recomputed ranges — simple, but needlessly destroys tag rows for clips whose range didn't change); (b) **ID-preserving diff**: recomputed ranges whose encoded ID already exists keep the existing row untouched (tags + `created_at` survive); auto-detected clips whose ID is not in the new set are deleted; new-set ranges not present are inserted. Implemented **(b)** — same end state for boundaries, strictly less destruction, aligned with "clip IDs are opaque and stable".
4. **Ingest undo granularity.** Options: (a) one grouped "Ingest Folder" undo step; (b) per-source undo steps (N sources = N Cmd+Z presses); (c) not undoable (ingest is additive, web had no undo). Implemented **(a)** — one user gesture, one undo step, matching UndoManager grouping conventions.
5. **Failed `.mkv` remux disposition.** Options: (a) reject the file (`remux-failed`, with ffmpeg-missing detail pointing at install/Settings) — nothing playable exists to register; (b) register a footage-only source pointing at the `.mkv` (AVFoundation can never open it — a permanently dead row); (c) abort the whole ingest. Implemented **(a)** — mirrors the `__`-stem hard-reject shape: "the source itself is the problem"; re-ingest after fixing ffmpeg picks it up.

### Tests (target ~60 new; run at END per workflow)

- CFDomain: the 11 ported segmentation tests (incl. exact-threshold `>=` split, by name) + tail-policy suite (extend-to-next-word-start mid/last/duration-nil/duration-shorter-than-last-word guard; fixed-padding clamp cases; word-end passthrough) + `transcriptText(from:to:)` half-open + leading-space semantics + **golden-master fixture**: a seeded word list generated by a checked-in Python script (`scripts/gen_segmentation_golden.py`) run against the *reference* `segment_words_by_silence`/`_make_clip_id`/`transcript_text_for_range`, expected output committed as a test resource; Swift must match ranges, IDs, and texts exactly (tail policy = word-end for comparability).
- CFStore: the 11 ported ingest tests (stubbed probe/remux) + mkv path (remux invoked, `path`=mp4, `original_path`=mkv, mkv+mp4 sibling dedup, remux-failure reject) + settings-driven threshold/tail + FTS row present after ingest + register→undo→redo for ingest group and `updateSource` + re-apply suite (boundary_edited skip, ID-preserving keep, snapshot-once, no-op-no-snapshot, undo/redo exact restore incl. tag rows + needs_review, missing-transcript error) + v1→v2 migration (upTo:"v1" then full) + `originalPath` round-trip.
- CFMedia: probe-adapter mapping (incl. HLG fixture → `isHDR`) + waveform (bucket count/loud-vs-silent placement on a burst fixture, encode/read round-trip, skip-if-exists, no-audio → empty).
- CFExport: locator + real-ffmpeg remux integration (graceful skip when ffmpeg absent).
- **Golden-master real-folder leg (plan §4/N3): RUNS** — Lillian moved the web-processed pairs into the inbox 2026-07-06 (`btc.0.2` / `btc.0.4` / `freestylingbtc0`, video + sidecar each; sidecar durations match the web state exactly). Pre-flight verified with the reference implementation: fresh Python segmentation on those exact sidecars reproduces the legacy `clipfarm.json` clips bit-for-bit (10 / 91 / 8 clips, IDs + texts equal, zero hand-edits) — so the committed legacy `clipfarm.json` is a valid expected-output oracle. Two tests, both gated `.enabled(if:)` on inbox presence so other machines skip visibly: (1) CFDomain diff — Swift segmentation (threshold 2.0, word-end tail) on the inbox sidecars vs the legacy clips per source: same IDs, same ranges, same `transcript_text`; (2) CFStore end-to-end — `ingestFolder` over the real inbox (stubbed probe, temp library, inbox strictly read-only) → per-source clip counts match the web version, remaining videos land footage-only.

### Deferred / out of scope

N4 owns: transcript browser + `transcripts.py` mtime cache, FTS search UI, sidebar badges, click-to-play. N5 owns: boundary ops (which set `boundary_edited`). Proxy generation stays N12's call (D35). Thumbnails N8; keyframe maps N11. The `CFExportModule` marker + smoke test stay (kickoff instruction) even though CFExport gains its first real code (locator/remuxer); production export tiers remain N12.

### Manual verify — DEFERRED to the N4 hard stop (checklist for the closeout)

1. Launch the app → Library → "Ingest Folder…" (defaults to `~/ClipFarm/Footage/`) → ingest completes; 8 sources listed — `btc.0.2` (10 clips), `btc.0.4` (91), `freestylingbtc0` (8), the five N2 files footage-only (0 clips, no sidecars). Clip counts equal the web version's for the three transcribed sources.
2. TCC: no prompt expected for `~/ClipFarm/` (not a protected folder category); note anything that does appear.
3. Flip tail policy in Settings, "Re-apply Segmentation" on one transcribed source, hear the difference in N4's click-to-play ("clips no longer feel cut short" check), Cmd+Z restores.
4. `.snapshots/` gains a `re-apply-segmentation` snapshot; `sqlite3 ~/ClipFarm/clipfarm.db 'PRAGMA table_info(sources)'` shows `original_path`.
5. Golden-master leg: covered in-phase by the two automated tests (see Tests) — re-eyeball optional.

---

## Phase N2 — Playback engine (the de-risking spike) — **Verified ✅ 2026-07-06 (HARD STOP completed — watch session run, every gate adjudicated)**

> **Status: CLOSED.** Implementation, gate measurements, cold review (18 findings dispositioned), Lillian's full gate adjudication, AND her watch session complete — 157 tests green. **No D11 pivot; the composition-architecture thesis stands.** Heavy-media latency accepted as limitation with the D35 proxy plan (LOCKED; evidence addendum in the backlog: proxies must be 1080p-class — 4K all-intra stalls too); HDR row **PASS by eyeball** on real material (instrument-attributed delta; N12 re-verifies calibrated); fades + stepping dispositions per the adjudication block. Watch-session findings (black demo window — two root causes; R-after-end; inescapable loop) all fixed same-day. Full record: [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase N2. **The N3 kickoff in `KICKOFF_MESSAGES.md` is clear to consume on Lillian's go** (swift-subprocess pre-approved pinned `1.0.0-beta.1`, QUESTIONS.md → Answered).

> **⚠️ INCIDENT 2026-07-06 — N2 interrupted mid-phase by an emergency repo move (iCloud).** Lillian's Desktop is iCloud-synced; the sync daemons (`fileproviderd`/`cloudd`) were saturated (primarily by another project's `.build` tree) and were strangling all file I/O on Desktop paths. Because N2's exit gates are *measured performance numbers*, anything measured under that I/O storm would be invalid. Actions taken by the coordinator, in order: (1) N2 implementer agent stopped mid-build (it had completed the plan-entry commit `6181f9d` and the `smoothCutAudio` schema commit `8e24684`; the uncommitted CFMedia sources / test support / n2harness in this WIP commit are its in-flight state — **known not to compile** (n2harness access-level errors + an async-context NSLock misuse; normal stopped-mid-file state, confirmed post-move); (2) this note + WIP commit; (3) repo moved `~/Desktop/clipfarm` → `~/dev/clipfarm` with a Desktop symlink left behind — **move completed and verified 2026-07-06**: git clean at the new home, and the full 118-test N1 baseline rebuilt from scratch and passed at `f6a9180` in an isolated worktree (`mac/ClipFarmKit/.build` was deleted pre-move as regenerable; it regenerates fine). **For the resuming session:** re-run N2 with the same kickoff from `KICKOFF_MESSAGES.md`, treating the WIP code as a starting point to review/finish — not as reviewed work. Gate measurements MUST be taken while iCloud sync is idle (check `fileproviderd`/`cloudd` CPU in Activity Monitor first). **Footage-path resolution (2026-07-06, Lillian — D34 / spec amendment 14):** real-file testing now reads the dedicated footage inbox `~/ClipFarm/Footage/` (outside iCloud; a managed working folder — writes/deletes inside it are fine); Lillian populates it herself before the watch session; the old `~/Desktop/AdAstra/…` path is retired from operative use. Once files are in the inbox, gate reads no longer traverse the iCloud I/O path at all.

**Goal (plan §4/N2):** prove the rewrite's thesis — `AVMutableComposition` + a single `AVPlayer` — before any UI sits on it, and fail fast if a research assumption is wrong. **HARD STOP tier:** Lillian watches the gates and adjudicates at the end; any FAILED gate escalates immediately mid-phase (the D11 pivot is her call, never the implementer's).

### Scope

1. **CFStore (own commit, schema/model rule):** `LibrarySettings.smoothCutAudio: Bool = true` accessor over N1's settings table (+ tests). D31: this one setting governs preview AND export micro-fades.
2. **CFMedia** (replaces the N0 `CFMediaModule` marker + smoke test):
   - `MediaTime.swift` — the D12 seam: `Double` seconds → `CMTime(seconds:, preferredTimescale: 600)` conversion happens here, exactly once; all arithmetic beyond it in `CMTime`.
   - `AssetCache.swift` — one `AVURLAsset` per source URL with properties async-preloaded (tracks, duration, per-track format/timing); warm-cache rebuilds are pure edit-list manipulation.
   - `MetadataProbe.swift` — async property load: duration, `minFrameDuration` (never `nominalFrameRate` — recorded for display only), format descriptions, `naturalSize`, `preferredTransform`, HDR detection (transfer function HLG/PQ from format descriptions).
   - `CompositionBuilder.swift` — §2.5 rules 1–8: one video + one audio composition track; both tracks inserted from the same clamped range (min of video/audio track durations); geometry-uniformity detection → bare composition when uniform, `AVMutableVideoComposition` with per-segment transforms (project-canvas renderSize, pillarbox default) when mixed (D32); explicit videoComposition color properties when dynamic ranges mix, SDR default enforced (D29); ~10ms `AVAudioMix` volume ramps at internal cut boundaries when `smoothCutAudio` is on (D31); tombstones skipped (rule 7 — input is post-resolver ranges); output is an immutable snapshot (`AVComposition` copy) + a composition-time↔source segment map.
   - `PlayerEngine.swift` — `@MainActor @Observable` final class (method/class-level isolation, NOT a target-default flip), one persistent `AVPlayer`: `load(ranges:)` (build → new item → **pre-seek await with zero tolerance → `replaceCurrentItem`** → re-arm observers), `play`/`pause`, `seek(to:)` zero-tolerance, `step(frames:)`, `loop(window:)` (boundary observer at window end → zero-tolerance re-seek to start; **re-armed after every swap**; periodic-observer belt-and-suspenders force-reseek on overshoot), `currentTime` via periodic observer.
   - CFMedia range input: `PlayableRange { url, startSec, endSec }`, constructible from N1's `ResolvedRange` + a source-URL lookup (N1 delta #1). The N2 harness bypasses the resolver with hand-specified ranges; nothing here loads transcripts (`transcriptProvider` stays the caller's job — N1 delta #2).
3. **Unit tests (`swift test`, self-contained):** logic testable without hardware timing — MediaTime conversion policy, geometry/color uniformity detection, fade-ramp window computation, plus integration tests against **tiny AVAssetWriter-generated fixtures** (built by a CFMediaTests-local factory at test run, temp dir; no ffmpeg, no network, no footage-folder dependency): probe fields, track counts, duration sums, segment maps, clamped-range rule, conditional videoComposition/audioMix attachment.
4. **Debug harness — `n2harness`** executable target in ClipFarmKit (never ships; not part of the library product, like CFTestSupport): hand-rolled subcommands, one per gate (no new dependencies — argument parsing is by hand). Runs against real files from the footage inbox `~/ClipFarm/Footage/` (D34 — managed working folder; Lillian populates it) + synthesized fixtures (see PROVISIONAL 1). Subcommands: `fixtures` (generate synthetic media into a workdir), `seams`, `blink`, `rotation`, `hdrseam`, `rebuild`, `frameacc`, `looptest`, `fades`, `exportspike`, `demo` (windowed multi-source assembly playback for Lillian's watch session).
5. **Export mini-spike (half-day, finding 4):** implemented as harness spike code, NOT production CFExport (the `CFExportModule` marker stays until N12 per N1 delta #5 — the spike's job is answers, not architecture). (a) passthrough export of a two-file H.264 composition with non-keyframe cuts — succeed at all? edit lists or snap? (b) sequential AVAssetWriter sample-writing sessions — are lead-in frames edited out for segments 2..N or only the first? (c) elst A/B: ffprobe/libav behavior recorded (VLC proxy) + the three files left regenerable for Lillian's QuickTime/VLC/Chrome eyeball at the watch session.

### Exit gates (measured, recorded in the closeout gate table)

Per plan §4/N2 + §6 budgets: seam p95 inter-frame gap ≤ 1 frame duration at 20+ non-keyframe cuts across ≥3 files incl. HDR; swap-blink = 0 on the winning strategy over 100 edit cycles (A/B rebuild-swap vs mutate-in-place); mixed-rotation renders correctly via videoComposition (+ record passthrough behavior); HDR↔SDR seam: no visible shift and preview == Standard-tier export; rebuild < 10ms @ 50 clips warm + edit→first-frame latency recorded; frame-accurate boundaries + `step(byCount:)` works across seams; trim-loop restart ≤ 50ms on long-GOP 4K HEVC non-keyframe window; micro-fades kill pops without softening onsets (measured discontinuity + onset envelope, plus audible A/B artifacts); export mini-spike questions (a)/(b)/(c) answered.

### PROVISIONAL calls (logged in QUESTIONS.md)

1. **Synthetic fixtures for codec variety.** The gates demand ProRes, HEVC, 4K, iPhone-HDR (HLG/BT.2020) and portrait-rotation material. Options: (a) synthesize deterministic fixtures via AVAssetWriter/VideoToolbox in the harness — per-frame-identifiable content (frame index encoded in pixel blocks) makes frame accuracy/seam drops *precisely* measurable; footage untouched; (b) ask Lillian for real files of every profile now (blocks the phase on her availability); (c) run only the legs real material covers (fails the gate list as written). Implemented **(a)**, with real inbox files used everywhere they qualify. *(Resume update 2026-07-06: Lillian populated `~/ClipFarm/Footage/` with `iphone.MOV` — H.264 High, native portrait 1080×1920@30, BT.709 SDR, 17.6s, AAC audio, keyframes ~every 0.97s — and `preresclip.mov` — H.264 Main (not ProRes despite the name), 3694×2176@120, BT.709 SDR, 48.9s, video-only, keyframes 1–4s apart. Real legs now cover mixed-rotation (real portrait!), seams, blink, rebuild, loop-restart corroboration, real-audio fades, and real passthrough exports; the HDR, ProRes, and 4K-HEVC-worst-case legs remain synthetic — no such real material exists in the inbox. Every gate report records which material produced each number.)* Recommend re-running `seams real`/`hdrseam` if a genuine iPhone HDR clip lands in the inbox at the watch session (one command each).
2. **Gate instrumentation methodology.** "Under screen capture" + "visible blinks" + "audibly" imply human-at-machine measurement the agent doesn't have (screen-recording TCC, speakers). Options: (a) programmatic instrumentation — `AVPlayerItemVideoOutput` frame delivery (host-time gaps + PTS continuity + decoded frame-index checks + black/stale-frame detection) for seams/blink/frame-accuracy/loop-restart, offline `AVAssetReader` audio rendering for fade discontinuity/onset math — plus windowed `demo`/audible artifacts for Lillian to confirm perceptually at the watch session; (b) defer all measurement to Lillian (turns a de-risking spike into a manual QA session). Implemented **(a)**: decode-level instrumentation is strictly more precise than eyeballing; the perceptual confirmations ride the HARD-STOP watch session that this tier already mandates. Numbers below marked "programmatic" where the eyeball confirmation is still pending. *(Resume methodology refinements after gate run 1 — all measurement-side, no thresholds relaxed: (i) the seam gate reports its ratio against BOTH `minFrameDuration` (the gate list's literal wording — unfair-by-construction on VFR files, whose min-ever sample duration sits far below their real cadence; `preresclip.mov` reads "120 fps" but delivers 16.7–41.7ms frames) and the incoming segment's measured median delivery gap (decode-level, VFR-immune); (ii) every seams variant runs a single-track vs alternating-two-video-tracks A/B — the plan's own prescribed seam mitigation, measured before anything custom; (iii) `step(byCount:)` is scored display==transport — run 1's "failures" were harness bookkeeping that assumed in-points sit on frame boundaries, which non-keyframe-cut test material deliberately violates; (iv) looptest adds a 1080p30 common-case leg and a D13-escalation leg (pre-rolled standby AVPlayer) as informational numbers beside the 4K-HEVC gate leg; (v) blink adds a `--fixture` 1080p30 leg separating swap mechanics from heavyweight-decode spin-up; (vi) fades adds a 2ms-bin RMS envelope trace at the seam diagnosing how the offline reader applies volume ramps.)*
3. **Harness placement.** Options: SPM executable target in ClipFarmKit (CLI-runnable via `swift run`, shares CFMedia directly, never ships) / separate throwaway package (duplicate build config, drifts) / app-target debug screen (needs xcodebuild + GUI for every measurement). Implemented the **SPM executable target**.

### Deferred / explicitly out of scope

- Thumbnails, waveforms, keyframe maps (N3/N8/N11 per module doc). FTS/store integration (none — CFMedia doesn't touch the DB). App-side player surface / inspector wiring (N4+). `AVPlayerVideoOutput` per-frame access (future). Production export tiers (N12 — the spike only answers its three questions).

### Verify (Lillian, HARD STOP)

Watch a multi-source (camera + iPhone-style) assembly play gapless (`swift run n2harness demo`); review the measured gate table in `COMPLETED_PHASES.md`; listen to the fades A/B artifacts; eyeball the export-spike files in QuickTime/VLC/Chrome; adjudicate every gate PASS/FAIL (a FAIL triggers the D11 pivot decision — hers).

---

## Backlog

- **Proxy workflow for heavy sources (owner: N12 — PROVISIONAL, see QUESTIONS.md).** Lillian's adjudication of N2's heavy-media latency FAILs (2026-07-06): accepted as limitation with this as the designated fix — optionally re-encode a lighter editing copy per heavy source (4K-class / high-rate VFR / long-GOP), preview and edit against the proxy, apply the identical resolved cut list to the original at export. N12 owns it because the resolver already feeds preview and export from the same range list — proxies swap the preview *material*, never the cut math; WYSIWYG stays intact by construction (cuts are times, not pixels). Placement is PROVISIONAL because proxy *generation* could alternatively live at N3 ingest (per-source background job, like waveforms) — N12 decides generation timing when it lands. Drafted as PROPOSED decision D35 for Lillian's ratification → **ratified LOCKED 2026-07-06** (ownership stays N12; generation timing still N12's call). **Evidence addendum (run 6, 2026-07-06):** 4K 10-bit ProRes 422 HQ entries stall in the same class as long-GOP 4K H.264 (~170–240 ms first-frame) — all-intra does NOT exempt a file from 4K decode spin-up. Consequence for N12: proxies must be **lower-resolution (1080p-class)**, not merely a friendlier codec; and the alternating-tracks mitigation rejection now stands on the widest file mix tested (5-file inbox incl. ProRes-HQ + 10-bit HEVC). **(2026-07-11: consolidated into the plan's N12 expansion — generation timing + proxy rules decided at the product session, D35 annotated in the decision log; QUESTIONS.md item Answered.)**
- **Fades A/B listen on a longer real assembly (owner: N12).** Lillian ran the N2 confirmatory WAV listen (2026-07-06): fades-ON vs OFF indistinguishable to her on the short pairs — confirmatory PASS (ON not worse), adjudication 3 stands. Her request: once ClipFarm can assemble a longer real edit (minutes, many cuts), run a proper A/B listen with fades on/off before the WYSIWYG fade path finalizes. Rides N12's sample-level-fade design + export verification; no new material to be generated before then. **(2026-07-11: folded into the plan's N12 expansion, item 2.)**

---

## Phase N1 — Verified ✅ 2026-07-06 (deferred manual verify cleared by Lillian at the N2 hard stop, same day)

Domain models + persistence core for the native rewrite. Plan entry committed pre-implementation in `911c939` (per the N0 finding-1 process rule); closeout in [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) → Phase N1 — 118 tests green after the cold-review adjudication (10 findings, all accepted, fixed 2026-07-06), three PROVISIONAL calls **all resolved by Lillian 2026-07-06** (items 1–2 keep-as-implemented; tailPaddingSec reworked to 0.25s; `QUESTIONS.md` → Answered), one platform discovery (UndoManager is `NS_SWIFT_UI_ACTOR` in the macOS 26 SDK → method-level `@MainActor` on undo-registering mutations), N2 delta recorded. **Next phase: N2** (kickoff queued in `KICKOFF_MESSAGES.md`).

---

## Phase N0 — Verified ✅ 2026-07-06 (built 2026-07-05; deferred manual verify cleared by Lillian at the N2 hard stop)

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
