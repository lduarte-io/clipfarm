# COMPLETED PHASES — ClipFarm build log

Phases move here from `PHASES.md` once Lillian has manually verified them. Each entry documents what was actually built, what assumptions were made (especially where the spec was ambiguous), and any deviations from the original plan. This file is the artifact that self-assessment and outside-session code review work from.

---

## Phase N2 — Playback engine (the de-risking spike) — native rewrite

**Manual verify: HARD STOP — awaiting Lillian's watch session** (N2 tier: she adjudicates every gate PASS/FAIL/REVISE-BUDGET; a FAIL is her D11-pivot call, never the implementer's). N1's deferred checklist also queues at this stop.

**Execution note:** N2 ran across two sessions on 2026-07-06 — interrupted mid-phase by the iCloud emergency repo move (incident note atop the PHASES.md N2 entry), resumed the same day in a fresh session. The interrupted run committed the plan entry (`6181f9d`) and the `LibrarySettings.smoothCutAudio` schema commit (`8e24684` — verified at resume against D31: default ON, WYSIWYG doc note, not undo-registered per the resolved N1 provisional 1); its in-flight CFMedia/harness code was preserved in `ceb8a87` (known not to compile) and was treated at resume as **unreviewed inherited code** — read line-by-line, corrected (bug list below), and finished. The resumed session was itself killed once by an API auth error and restored with context intact; in-flight results were re-verified from the durable gate logs before use. Resume work landed as `37e801e` (engine + harness + D34 port) plus the gate-refinement/closeout commits after it.

**Built (2026-07-06):** the `AVMutableComposition` + single-`AVPlayer` playback engine (plan §2.5) and the gate harness that measures it (plan §4/N2).

**CFMedia (replaces the N0 marker; AVFoundation never leaks past this module):**

- `MediaTime.swift` — the D12 seam: the one sanctioned Double→CMTime conversion (timescale 600); `seconds()` for display/persistence only; all media arithmetic past the boundary stays in CMTime.
- `MetadataProbe.swift` — D17 delivered: async property load of duration, `minFrameDuration` (real sample timing — the only sanctioned frame-math basis), `nominalFrameRate` (display only), `naturalSize`, `preferredTransform`, format descriptions (codec fourCC, color primaries/transfer), `isHDR` (HLG|PQ transfer), `orientedSize`, `hasAudio`/`audioTimeRange`; unreadable file → typed error.
- `AssetCache.swift` — actor; one `AVURLAsset` per URL, tracks + metadata preloaded (§2.5 rule 3); `LoadedAsset` `@unchecked Sendable` with the post-load read-only rationale documented; `invalidate(_:)` for N3+ repointing.
- `CompositionBuilder.swift` — §2.5 rules 1–8: one video + one audio track, back-to-back (rule 1); both tracks inserted from the SAME clamped range — min of video/audio track ranges — with `insertEmptyTimeRange` audio for footage-only sources so A/V alignment survives (rule 2; exercised by the real video-only inbox file); immutable snapshot output — `composition.copy()` + a `BuiltSegment` composition↔source map (rule 4); D32 conditional geometry — uniform → bare composition + track-level `preferredTransform` (Lossless door open), mixed → `AVMutableVideoComposition` with per-segment fit transforms (orientation-normalize → aspect-fit → center, pillarbox default; canvas = caller renderSize or first segment's oriented size) (rule 5); explicit 709 color properties ONLY when dynamic ranges mix — SDR default enforced, never assumed (rule 8 / D29); ~10 ms `AVAudioMix` down-then-up ramps at internal boundaries when `smoothCutAudio`, ramps shrink to half-segment so they can't overlap (rule 6 / D31); tombstones never reach the builder (rule 7). `CompositionPlanner` holds the pure planning rules (clamp / geometry uniformity / dynamic-range mix / fitTransform / fadeRamps), unit-tested without media.
- `PlayerEngine.swift` — `@MainActor @Observable final class` (method/class-level isolation — no Kit-target default flip), one persistent `AVPlayer` (`automaticallyWaitsToMinimizeStalling = false`): `load(ranges:smoothCutAudio:at:)` = build → fresh item → `itemConfigurator` hook (N4's video-output/KVO attach point; the harness's tap) → zero-tolerance pre-seek awaited → `replaceCurrentItem` → **observer re-arm** → resume-if-was-playing; zero-tolerance `seek`; `step(frames:)` (pauses first); D13 `loop(windowStartSec:windowEndSec:)` — boundary observer at window end, zero-tolerance re-seek, re-armed after every swap, 30 Hz periodic-observer overshoot recovery as belt-and-suspenders, `loopSeekInFlight` guard; `currentTimeSec` from the periodic observer (scrubber only); `isolated deinit` teardown.
- `PlayableRange` — CFMedia's range input, constructible from N1's `ResolvedRange` + a source-URL lookup (N1 delta 1); the harness bypasses the resolver with hand-specified ranges and nothing loads transcripts (N1 delta 2).

**CFMediaTestSupport (target; never ships):** `MediaFixtureRenderer` — deterministic AVAssetWriter fixtures with self-identifying content (frame index as 16 bit-blocks, orientation markers, gray body; H.264 / HEVC / HEVC-10-HLG / ProRes422; LPCM sine/burst audio; concurrent per-input feeding; validity-checked skip-if-exists) and `PixelProbe` (frame-index decode, mean-RGB, blackness, PNG dump).

**n2harness (SPM executable — PROVISIONAL 3; never ships):** one subcommand per gate — `fixtures`, `seams uniform|real|mixed|solo` (every variant A/Bs single-track vs alternating-two-video-tracks), `blink [--cycles N] [--fixture]`, `rotation`, `hdrseam`, `rebuild`, `frameacc`, `looptest [--loops N] [--escalation-only]`, `fades`, `exportspike a|b|c|all`, `demo [--real]` (the watch-session window: AVPlayerLayer + PlayerEngine; Space/R/L/Esc), `all`. Instrumentation (PROVISIONAL 2): `FrameTap` — `AVPlayerItemVideoOutput` polled at 2 kHz on a dedicated thread, recording per-delivered-frame (host time, item time, decoded frame index, blackness); offline `AVAssetReader` audio rendering honoring the audioMix; `MP4BoxParser` (moov→trak→edts→elst) for edit-list forensics; per-gate reports appended under `~/ClipFarm/outputs/reports/`. **D34:** `HarnessEnv` reads the footage inbox `~/ClipFarm/Footage/` (default; `--footage` override), probes whatever is present, lays out all ranges duration-aware, prints a material line per gate, and falls back to synthetic fixtures with an explicit flag when no qualifying real file exists — nothing is hardcoded to file names, and the retired `~/Desktop/AdAstra/…` references are gone. **Correction (2026-07-06):** that adaptivity claim was true of seams/blink/rebuild/rotation/fades/exportspike but NOT of `hdrseam`, which stayed hardcoded to the synthetic SDR+HLG fixtures (only its material note mentioned the inbox) — caught when Lillian's real HDR files arrived and the leg ignored them. `runHDRSeam` is now genuinely adaptive: a retained synthetic control leg plus one real leg per inbox HDR file (HEVC HLG first — the D29 iPhone profile — then ProRes HLG), alternating against the longest real SDR file, with the measured criterion stated per leg (real material scores ONLY max per-segment |preview − export| ≤ 6/255 — the WYSIWYG number; cross-seam shift is reference-only there since adjacent segments carry different content).

**Real material (probed at resume):** `iphone.MOV` — H.264 High, **natively portrait-encoded 1080×1920@30** (identity transform), BT.709 SDR, 17.57 s, AAC stereo + a second 4-ch (spatial) audio track + data tracks, keyframes ~0.97 s apart. `preresclip.mov` — H.264 Main (**not** ProRes despite the name), 3694×2176 @ 120 timebase (**true cadence 16.7–41.7 ms ⇒ VFR ~40–60 fps**), BT.709 SDR, 48.91 s, **no audio track**, keyframes 1–4 s apart. Neither is HDR/HEVC/ProRes → those legs ran on synthetic fixtures, flagged per report (PROVISIONAL 1).

**Inbox renames (2026-07-06, after all gate measurements; coordinator-applied, honest names):** `preresclip.mov` → **`h264-4k-vfr.mov`** (all gate rows above that name preresclip.mov were measured under the old name — same file, byte-identical). A third file was added hoping it was ProRes — it is not: **`h264-720p30.mov`** (H.264 Main 1280×720@30, BT.709 SDR, AAC mono — the classic dogfood profile; ffprobe-verified at rename). It postdates every gate run and is part of NO measurement above; the harness discovers inbox files by scan, not by name (verified post-rename: the demo assembly picks up all three), so future re-runs fold it in automatically — note it will also become the `fades` real-audio pick (first audio-bearing file in sort order). *(Superseded 2026-07-06 later the same day: Lillian delivered two verified-genuine HDR files — see the HDR addendum below.)*

**HDR material delivered (2026-07-06, after the adjudication; coordinator ffprobe-verified):** `proreshdr.MOV` — ProRes 422 HQ (`apch`), 3840×2160, yuv422p10le, BT.2020 + HLG, 30 fps, 31.8 s, ~2.9 GB (deliberately washes to near-white in its final ~1 s — exposure-lock stress material, not a defect; gate range layouts stay clear of it). `hdrnotprores.MOV` — HEVC Main 10 (`hvc1`), 1920×1080, yuv420p10le, BT.2020 + HLG, 30 fps, 10.8 s — the iPhone-consumer profile D29 targets.

**Bugs found in the inherited WIP and fixed at resume** (beyond the incident note's six compile errors — public inits for `BuiltSegment`/`CompositionBuildResult`; NSLock→`withLock` in an async context; `analyzeSplice` isolation + spurious awaits; the GateFades `sending` violation, resolved via the immutable-snapshot rationale):

1. **AVAssetWriter multi-input interleave deadlock** (`MediaFixtureRenderer`): video fed to completion before audio; multi-input writers throttle each input to the interleave pattern, so the leading input goes not-ready and its callback never re-fires → silent all-threads-parked hang (observed twice as a hung `swift test`, 24 min). Fixed with concurrent per-input feeder tasks (the documented pattern). Standing lesson recorded for N12/N14 writer work.
2. **Fixture-actor reentrancy double-render** (`TestFixtures`): the actor suspends across the render, so two parallel tests requesting the same spec both saw the file missing and wrote one path concurrently — one writer dies, its continuation leaks, the suite hangs. Fixed by memoizing the in-flight render `Task` per spec.
3. **Renderer hangs instead of throwing** on append failure (return values ignored; no once-guard on the continuation). Fixed: `@Sendable` once-guarded `finish()` + append checks in both feeders; skip-if-exists now validity-checks the existing file (loadable, duration within 0.5 s) so a killed run's partial file re-renders.
4. **GateBlink strategy B** re-evaluated `mutable.duration` between the two `removeTimeRange` calls (post-removal shrink can leave a tail on the second track) and hard-required an audio track (crash on video-only files). Fixed: whole-range captured once; audio optional.
5. **Rotation gate's expected canvas** hardcoded 1920×1080 (wrong even for the retired 720p dogfood files). Fixed: expected canvas = first segment's oriented size, computed from the probe.
6. **`fixtureFramesAreSelfIdentifying`** assumed AVAssetReader emission order == presentation order (false under B-frame reordering). Fixed: PTS-anchored expectations.

**Standing rule (Lillian, 2026-07-06, during the watch session):** all harness/user-facing outputs live in ONE visible place next to the footage inbox — **`~/ClipFarm/outputs/`** — never scattered in hidden system paths. `HarnessEnv.defaultWorkdir` moved accordingly and the existing artifacts (reports/, audio/, export/, frames/, fixtures/) were physically relocated; `~/Library/Caches/ClipFarm-N2Gates/` no longer exists. Applies to all future phases' user-facing artifacts.

**Measurement environment (incident-note rule):** repo, workdir (`~/ClipFarm/outputs`), and footage inbox all live outside iCloud-synced paths; `fileproviderd`/`cloudd` CPU was checked and logged **before every gate** — at or near **0.0% for every measurement run** (per-gate daemon lines in the driver logs; all numbers reproducible via the harness). Gates ran on the **release** build — debug Swift hot loops measured 20–50× slower (fixture rendering ~8 min debug → 58 s release) and release is the shipping configuration.

### Measured gate table (programmatic — PROVISIONAL 2) — **ADJUDICATED by Lillian 2026-07-06** (decisions below each group; only the HDR row remains open, deferred pending real material)

| # | Gate (plan §4/N2) | Leg / material | Numbers (release; daemons ~0%) | Automated verdict |
|---|---|---|---|---|
| 1 | Seam p95 inter-frame gap ≤ 1 frame duration, 20+ non-KF cuts | uniform: synthetic h264A+h264B+hevc1080 (1080p30, bare comp, 23 cross-file seams) | gaps p50 13.8 / p95 28.3 ms; ratio vs minFrameDuration p95 **0.85**; vs measured cadence p95 0.85 | **PASS** |
| | | real inbox: iphone↔preresclip alternating (23 seams, videoComposition) | p50 22.3 / p95 **101.8** / max 130.5 ms; cadence-ratio p95 2.48 | **FAIL** — stalls concentrate at entries into the heavy VFR file |
| | | mixed: preresclip + 4K-HEVC/ProRes/HLG/portrait fixtures (21 seams) | p50 21.7 / p95 **44.0** / max 173.6 ms; minFrameDuration-ratio p95 13.02 (VFR denominator artifact); cadence-ratio p95 2.04 | FAIL — worst seam is a heavy-VFR-file entry; 30fps-material seams stay ~1-frame |
| | | solo self-splice, iphone (11 same-file seams) | p95 29.0 ms; cadence-ratio p95 **0.87** | **PASS** — the dogfood-profile case is seamless |
| | | solo self-splice, preresclip | p95 **67.1 ms**; cadence-ratio p95 2.41 | FAIL — heavy-file decode re-init stalls even same-file |
| | | **mitigation A/B**: alternating two video tracks (plan-prescribed probe), all variants | does NOT help: real p95 101.9 vs 101.8 ms (identical class); uniform pins to exactly 1.00–1.02 frames (compositor-locked delivery, zero dropped frames) | **mitigation rejected — §2.5 rule 1 (single track pair) stands** |
| 2 | Swap-blink = 0 on winner, 100 edit cycles, A/B vs mutate-in-place | real preresclip (4K-class 120-timebase) | A rebuild+preseek+swap: **28 stalls >250 ms, 0 black frames** (gap p50 219 / p95 299 ms); B mutate-in-place: 88 blinks, stalls ≥2 s (max 8.8 s). Black detection REAL post-fix (finding 1); on real footage black is informational (dark content can false-positive) | winner **A**; **FAIL** on heavy real material |
| | | synthetic 1080p30 (`--fixture` leg) | A: **0 blinks, 0 black** — black folds into the criterion on fixture content (gap p50 79 / max 115 ms); B: 74 blinks | **PASS** — swap mechanics sound; the real-leg FAIL is decode spin-up, not the swap design |
| 3 | Mixed-rotation render (D32) | leg 1: real 4K landscape + portrait fixture (strict pixel checks); leg 2: real + real (iphone portrait) | canvas exact 3694×2176 (= first segment oriented); pillars 0/0; center gray 118 exact; real-real content present; **passthrough export SUCCEEDED but flattens to one naturalSize + identity transform (per-segment transforms not representable)** — file kept for N12's Lossless-eligibility rule | **PASS** (+ passthrough behavior recorded) |
| 4 | HDR↔SDR seam (D29): no visible shift ∧ preview == Standard export | synthetic SDR-709 + HLG-2020 alternating, managed vs bare control, preview + export | preview managed 117.3 / 105.0 per segment (Δ12.3/255); bare control 105.3 ≡ managed → the 118→105 offset is **fixture-encode-side**, not pipeline (PROVISIONAL 1 caveat); export HLG segments read **84.0** → \|preview−export\| **21/255** | **FAIL** as-thresholded; the load-bearing finding is the 21/255 preview-vs-export delta on HLG content |
| | **HDR re-run on REAL material (2026-07-06, post-adjudication — row still OPEN for Lillian):** real leg 1: h264-4k-vfr (SDR) ↔ hdrnotprores.MOV (HEVC Main 10 HLG) | SDR segments WYSIWYG-exact (Δ ≤ 0.3/255: 126.4→126.5, 138.8→139.1); HLG segments preview 122.1/133.1 vs export 99.2/107.1 → **max \|preview − export\| = 26.0/255**; D29 pin effect in preview = 45.2/255 (bare preview drifts to 143–178 — the pin is load-bearing) | criterion (real material): max per-segment \|preview−export\| ≤ 6/255 → **FAIL** — presented for adjudication, not adjudicated |
| | real leg 2: h264-4k-vfr (SDR) ↔ proreshdr.MOV (ProRes 422 HQ 4K HLG) | SDR segments exact; HLG segments preview 131.1/111.2 vs export 105.4/91.9 → **max Δ = 25.7/255**; pin effect 25.0/255 | **FAIL** — same systematic ~21–26/255 HLG-brighter-in-preview offset on synthetic, real-HEVC, AND real-ProRes ⇒ pipeline-systematic, not a fixture artifact; instrument caveat: "preview" = video-output BGRA readback, which may include display tone mapping — the screen-vs-file A/B is Lillian's eyeball |
| 5 | Rebuild < 10 ms @ 50 clips warm + edit→first-frame | real files ×100 rebuilds, uniform AND mixed(+videoComposition) | uniform p95 **1.33 ms**, mixed p95 **1.45 ms**; edit→`load()` returned p50 4.2 / max 7.1 ms; edit→first frame (paused) p50 155.9 / p95 159.7 ms | **PASS** (7× headroom); first-frame latency = the same heavy-decode spin-up class as gates 1-2/7 |
| 6 | Frame accuracy + `step(byCount:)` across seams | fixtures (self-identifying), non-KF + non-frame-aligned cuts | seam seeks frame-exact **4/4**; stepping monotone ±1 frame boundary both directions incl. partial edge frames (Δt 21.7 / −55 ms exactly as geometry predicts); automated score 8/9 — the 1 is a **boundary-instant fencepost**: at exactly the seam the platform displays the outgoing source's next frame (183) before the following step enters the incoming segment (310) | seeks **PASS**; stepping **FAIL (automated; fencepost — see trace)** — the raw report's verdict, carried verbatim per finding 6; the fencepost interpretation and any acceptance are Lillian's (also an N11 trim-mode UX note) |
| 7 | Trim-loop restart ≤ 50 ms (§6), long-GOP 4K HEVC non-KF window | gate leg: synthetic 4K HEVC, 50 loops | p50 83.4 / p95 **86.9 ms**; 0 missed fires | **FAIL** (budget 50 ms; observer mechanism itself flawless) |
| | | real preresclip, 30 loops | p50 173.7 / p95 191.7 ms | FAIL (informational) |
| | | 1080p30 H.264 common case, 30 loops | p50 40.4 / p95 **44.7** / max 48.1 ms | **PASS** — the budget holds on Lillian's actual footage profile |
| | | D13 escalation: prerolled standby AVPlayer, 20 laps | p50 **140.8** / p95 151.6 ms — *worse* than the simple path | escalation rejected as-measured; D13's deeper option (sample-buffer caching) deliberately unexplored at N2 |
| 8 | Micro-fades kill pops without softening onsets (D31) | offline-rendered fixture composition + envelope trace; real-audio WAVs from iphone.MOV | onset preserved (post-cut/steady RMS ratio 1.01); pop NOT nulled: max Δ 0.103 → 0.094; envelope shows **~50–65% gain at the cut on both sides** — the reader path applies volume ramps too coarsely to null the boundary | **FAIL** programmatic; audible A/B at the watch session; design note for N12: reader-path ramp granularity means sample-level fades (or a different mix path) may be needed for the WYSIWYG export |
| 9 | Export mini-spike (a)/(b)/(c) | (a) passthrough, two-file H.264, non-KF cuts | **SUCCEEDS** on macOS 26 (rdar://10421720 not hit); authors elst ×2 (lead-in retained, marked); Apple-side playback frame-exact (143/309 ✓); **libav honors the edit lists exactly** (175/175 frames, exact duration) | answered — Lossless tier viable; elst-honoring broader than feared (Chrome/QuickTime eyeball pending) |
| | | (b) sequential writer sessions | **NOT viable** — second `startSession` → `-11862 "Cannot append media data after ending session"` (one session per writer, API-hard) | answered — **N12 hybrid writer = per-segment writes + AVMutableMovie stitch** (the plan's named fallback), decided now |
| | | (c) elst A/B files | Standard re-encode control exact; real self-splices (iphone + preresclip) exact durations, elst present, libav consistent; files kept in `export/` | answered — eyeball at watch session |

**Row-1 addendum (informational, 2026-07-06 — the heavy-media adjudication below already stands):** `seams real` re-run over the grown 5-file inbox (SDR H.264 ×3 + HEVC HLG + ProRes 4K HQ; 24 cross-file seams, mixed geometry AND mixed dynamic range through the D29-pinned videoComposition): gaps p50 21.8 / p95 172.4 / max 238.3 ms; VFR-fair cadence-ratio p50 0.99 / p95 4.01. Entries into the 2.9 GB ProRes 4K HQ and 10-bit HEVC HLG files join the heavy class as expected — all-intra does not exempt 4K-10-bit material from first-frame spin-up. Alternating-tracks A/B again indistinguishable (p95 172.40 vs 172.37 ms) — the rule-1 mitigation rejection stands on the widest material mix tested.

**Lillian's adjudications (2026-07-06, relayed by the coordinator):**

1. **Heavy-media latency FAILs — gates 1 (real / mixed / solo-heavy legs), 2 (real leg), 7 (4K-HEVC gate + real legs), and gate 5's first-frame-latency note: ACCEPTED AS LIMITATION, with a designated future fix.** No D11 pivot; no §6 budget relaxation. The adopted mitigation is a **proxy workflow** (Lillian's own proposal): optionally re-encode a lighter editing copy per heavy source, edit/preview against the proxy, apply the identical cut list to the original at export. Owner: **N12** per the backlog rule (entry in PHASES.md backlog; PROVISIONAL — see QUESTIONS.md); drafted as **PROPOSED decision D35** in NATIVE_REWRITE_DECISIONS.md for her ratification.
2. **HDR↔SDR seam (gate 4): DEFERRED — not accepted, not failed.** Lillian will record a real iPhone HDR clip and drop it in the inbox; the re-run (`seams real` + `hdrseam`) decides. Stays on the watch-session checklist. *(Update, same day: real HDR material delivered and measured — see the two real-leg rows in the gate table. The numbers confirm the synthetic finding as pipeline-systematic (~21–26/255 preview-brighter-than-export on HLG segments; SDR segments exact). The row remains OPEN — hers to adjudicate on the real numbers + the PNG/screen-vs-file eyeball.)*
3. **Micro-fades (gate 8): ACCEPTED AS N12 DESIGN INPUT.** Preview keeps the current fades; the sample-level-fades finding routes to N12. The WAV listen stays on the checklist as confirmatory, not gating — her ears can still overturn.
4. **Stepping fencepost (gate 6): ACCEPTED AS-IS.** The boundary behavior is on record as accepted; N11 trim-mode design owns playhead-side-of-cut semantics. The seeks half was already PASS.

With these, every gate row is adjudicated except the deferred HDR row. **N2's architecture thesis stands: no D11 pivot.**

**Confound note (finding 2):** run-1/2 seam legs with a videoComposition attached ran with the compositor tick derived from `minFrameDuration` (≥120 Hz on the VFR file) — the exact cadence mistake this phase's own N3 delta records. All videoComposition-bearing legs were re-measured after the fix (run 5, nominal-rate tick): the FAIL/PASS pattern is unchanged (the bare-composition `solo preresclip` leg already showed the stalls are decode-side, not compositor-side), so the gate conclusions survive the confound; the table above carries the post-fix numbers.

**Cross-cutting read of the FAIL rows (the adjudication packet):** every latency FAIL traces to one root — first-frame decode spin-up on heavyweight material (4K-class, 120-timebase VFR, long-GOP H.264) after a discontinuity (seam entry, item swap, loop wrap). The same operations on 1080p30 material — the profile of every recording ClipFarm was conceived for — pass everything: seams p95 0.86 frame, blink 0/100, loop restart 44.7 ms. The two non-latency FAILs are (4) a preview-vs-export color delta on synthetic HLG (fixture caveat acknowledged; real-HDR re-run recommended) and (8) ramp granularity in the offline audio path (an N12 design input, not a preview defect claim). Neither FAIL class impeaches the composition architecture itself; whether any triggers the D11 pivot, a §6 budget revision, or an accepted limitation on ultra-heavy sources is **Lillian's call at the watch session**.

**Deviations from the committed plan entry:** (1) gate-methodology refinements after run 1, all measurement-side and recorded in the PHASES.md N2 entry (PROVISIONAL 2 addendum) — VFR-fair cadence denominator reported beside the literal one, single-vs-alternating-track A/B folded into every seams variant, transport-scored stepping, extra informational looptest/blink legs, fades envelope trace; **no threshold was relaxed**. (2) `seams solo` and `blink --fixture` are additions beyond the plan's leg list (isolation diagnostics). (3) The demo defaults to inbox + portrait/HLG fixtures (the plan's "camera + iPhone" wording predates D34; the inbox holds no HDR material yet).

### Cold review (2026-07-06) — findings & dispositions (review 2 of 2)

Reviewer ran `REVIEW_PROMPT.md` with zero implementation context; independently reproduced the (then-)149/149 suite, clean builds including `n2harness`, and spot-checked the closeout gate table against the durable reports. 18 findings: **17 ACCEPTED and fixed the same day** (`3cd561b`), **1 ACCEPTED-AS-NOTED** (exemption on record). Measurement-affecting fixes triggered a re-measurement of every touched gate leg (run 5, daemons 0.0%, release build); the table above carries post-fix numbers.

1. **[MAJOR] Blink black-frame counts vacuously zero** (`decode: false` hardcodes `isBlack = false`; the closeout cited "0 black frames" as measured evidence) → **ACCEPTED** — the packet-integrity catch. Fixed: `decode: true` on both strategies' taps; black folds into the blink criterion only on fixture material (real footage can be legitimately dark — informational there), semantics stated in the report note. Both blink legs re-measured; real-leg black=0 is now evidence, not an artifact.
2. **[MAJOR] videoComposition `frameDuration` from `minFrameDuration`** — the builder shipped the exact VFR cadence mistake the phase's own N3 delta warns about → **ACCEPTED** (the sharpest finding). Fixed: pure `CompositionPlanner.compositorFrameDuration` (highest `nominalFrameRate` wins, clamped 24–120) + 2 named tests; the harness's alternating-tracks builder uses it too. All videoComposition-bearing legs re-measured; confound note added to the adjudication packet (conclusions unchanged — the bare-composition solo leg had already isolated the stalls as decode-side).
3. **[MAJOR] `load()` stale-swap race** → **ACCEPTED**. Fixed: generation ticket — an older build/pre-seek that loses the race returns without touching the player; contract documented; deterministic race test (`staleLoadNeverBeatsANewerLoad`, stable ×3). Semantics: the load that STARTS last wins.
4. **[MINOR] Zero PlayerEngine tests** → **ACCEPTED**. Fixed: `EngineTests.swift` — built/duration exposure, play-state preservation across swap, the stale-load race, loop-window-survives-reload + re-arm (via an internal `isLoopArmed` hook), `clearLoop` disarm. 5 tests, fixture-based, no timing assertions.
5. **[MINOR] `smoothCutAudio` defaulted at both API layers** → **ACCEPTED**. Fixed: required parameter on `CompositionBuilder.build` and `PlayerEngine.load` with a D31 doc note at each; every call site updated (a test-file-private convenience wrapper remains for audio-neutral tests, commented as such).
6. **[MINOR] Gate-6 verdict column softened the automated verdict** → **ACCEPTED**. Fixed: the row now carries `FAIL (automated; fencepost — see trace)` verbatim; interpreting the fencepost is Lillian's call, as the tier requires.
7. **[MINOR] Inconsistent probe error surface** → **ACCEPTED**. Fixed: every load failure inside `MetadataProbe` maps to `MetadataProbeError.unreadable(url:detail:)` — one typed shape for N3's ingest, `detail` preserving the underlying error text.
8. **[MINOR] `PlayableRange(resolved:url:)` dropped `clipID`** → **ACCEPTED**. Fixed: optional `clipID` carried through to `BuiltSegment` (nil for hand-built ranges); test extended.
9. **[MINOR] Loop overshoot recovery could fight paused stepping** → **ACCEPTED**. Fixed: recovery gated on `isPlaying` (a paused overshoot can only be a deliberate step/seek); interaction documented on `loop()`. No re-measure needed: the loop gate recorded 0 missed fires, so the recovery path never participated in any measurement.
10. **[MINOR] Demo `L` loop window unclamped at composition end** → **ACCEPTED**. Fixed: window clamped inside the composition (the watch-session eyeball would otherwise silently no-op near the end).
11. **[NIT] `addMutableTrack` failure threw `.emptyRangeList`** → **ACCEPTED**. Fixed: `.trackCreationFailed`.
12. **[NIT] 1 ms seam classification tolerance** → **ACCEPTED**. Fixed: 1 µs (delivered display times land exactly on frame boundaries); rode the seams re-measurement.
13. **[NIT] Mixed variant keyed source AND phase to `i % 5`** (5 identical pairs cycled) → **ACCEPTED**. Fixed: phase decoupled (`i % 7 × 1.913 s`); re-measured.
14. **[NIT] Hardcoded `/opt/homebrew/bin/ffprobe` + pipe-read-after-exit** → **ACCEPTED (both halves addressed proportionately)**: `locateTool()` searches Homebrew-ARM/Intel/system paths with a graceful skip line; the small-output pipe pattern is documented as accepted for harness one-liners.
15. **[NIT] `--workdir`/`--footage` with missing value → obscure failure** → **ACCEPTED**. Fixed: usage errors.
16. **[NIT] PNG file I/O under the sample lock** → **ACCEPTED**. Fixed: write moved outside the lock.
17. **[NIT] Daemon check not reproducible from the harness** → **ACCEPTED**. Fixed: `HarnessEnv.daemonLine()` (ps sample) embedded in every report block — the measurement-environment guard now ships with the numbers.
18. **[NIT] Demo hardcoded keys vs the KeyMap-registry rule** → **ACCEPTED AS NOTED, no code change**: the KeyMap rule (mac/CLAUDE.md) governs app views; `n2harness` never ships and is not the app. Exemption deliberately on record here.

### Next-phase delta (N3 — plan §4/N3 read in full; plan entry amended)

1. **D17 is complete** — N3 consumes `MetadataProbe.probe(url:)`; per-source `is_hdr` + frame timing come free. VFR lesson: `minFrameDuration` is frame-precision math, NOT cadence — never surface `1/minFrameDuration` as "the fps" (a real inbox file reads 120 fps that way; true cadence ~40–60 fps). Display fps = `nominalFrameRate`, display only.
2. **iPhone files can be natively portrait-encoded** (coded 1080×1920, identity transform) and carry a second spatial-audio track + data tracks — ingest must not assume landscape-coded or single-audio-track sources.
3. **AVAssetWriter lessons banked for N12/N14:** multi-input feeding must be concurrent; sequential sample-writing sessions are API-impossible → the hybrid writer is per-segment writes + `AVMutableMovie` stitch (decided by spike (b)); reader-path audio-mix ramps are coarse (gate 8) — the WYSIWYG fade path may need sample-level DSP.
4. **The footage inbox is live** with two files; `preresclip.mov` (no sidecar, no audio) is a ready-made footage-only ingest case. The golden-master segmentation diff needs web-processed files — **Lillian moves them into the inbox herself** (D34); the leg defers with a flagged line if absent, N2-style.
5. **Write N3's waveform reader vDSP-vectorized and measure in release** — debug hot loops are 20–50× slower.
6. **swift-subprocess is N3's first new third-party dependency** — exact version + license need Lillian's approval BEFORE it lands (D20 lesson; flagged in the N3 kickoff).

### Watch-session checklist — REMAINING items only (rewritten 2026-07-06 after the black-window fix, the outputs move, and the gate adjudications; the gate table itself is adjudicated — nothing below re-opens it except the HDR row)

1. **Watch the fixed demo** (the invisible/black window is fixed — THREE root causes found across two rounds: (i) the AVPlayerLayer was added as a sublayer of a lazily-created backing layer and manually sized — now it IS the view's backing layer via `makeBackingLayer`, nothing left to silently no-op; (ii) async `main()`'s frame is itself a main-dispatch-queue job, so any nested `app.run()`/event pump starved the queue — `play()` dispatches, `@MainActor` tasks, and engine observers never ran; (iii) a CLI process's window ordered behind the frontmost Terminal under cooperative activation reads as "no window" — measured as `occlusionState` not-visible. Final architecture: **park-and-run** — all async setup completes, `app.run()` is scheduled as a run-loop-*source* callout (not a queue callout), and the async frame suspends forever on a continuation, which frees the main queue; `app.run()` then runs with its full presentation/activation machinery AND a serviceable main queue. Verified 3/3 headless: `onScreen(occlusion)=true`, `key=true`, `appActive=true`, layer ready, real-time playback, clean auto-exit):
   `cd mac/ClipFarmKit && swift run -c release n2harness demo`
   Expect: window front-and-center with video immediately (first segment is h264-4k-vfr pillarbox-fit); stdout prints `DEMO SELF-CHECK` + `DEMO WINDOW-CHECK` at 2 s/5 s — `layerReadyForDisplay=true` and `onScreen(occlusion)=true` are the health signals, so if anything ever looks wrong again the diagnosis is in the terminal. Keys: Space pause, **R** live-reload (swap-blink eyeball — ~80–110 ms gap expected on light material, ~220–325 ms freeze-frame on the heavy 4K-VFR file, per the accepted limitation), **L** 1.5 s loop at the playhead, Esc/Q quit. Then `demo --real` for the inbox-only assembly.
2. **Listen (confirmatory, not gating — adjudication 3):** `~/ClipFarm/outputs/audio/fixture-fades-{on,off}.wav` and `real-fades-{on,off}.wav`. ON should sound cleaner at the cuts; a residual tick is the known reader-path ramp-granularity finding already routed to N12 — flag only if ON sounds *worse* or intolerable.
3. **Eyeball the export-spike files** in QuickTime + VLC + Chrome: `~/ClipFarm/outputs/export/` — `spike-a-passthrough.mov` (edit-list passthrough: should play 5.83 s with clean cuts in all three), `spike-c-standard-reencode.mp4`, `spike-c-real-iphone.mov`, `spike-c-real-preresclip.mov` (measured under the file's old name), `rotation-passthrough.mov` (expected wrong-looking: passthrough cannot represent per-segment transforms — that's the recorded D32 evidence, not a bug). PNGs in `~/ClipFarm/outputs/frames/` (rotation pillarboxing, hdrseam segment grabs).
4. **HDR row — numbers are now MEASURED on your real files (the one OPEN gate row):** `hdrseam` ran on `hdrnotprores.MOV` and `proreshdr.MOV` against real SDR footage — the systematic finding: HLG segments read ~21–26/255 brighter in preview than in the Standard-tier SDR export, on every material type; SDR segments match exactly. What remains for your eyes: (a) PNGs at `~/ClipFarm/outputs/frames/hdrseam-real-*.png`, and (b) the decisive WYSIWYG A/B — play an HDR-containing assembly in the demo on screen next to the exported `~/ClipFarm/outputs/export/hdrseam-real-*-standard.mp4` in QuickTime: does the file match what the preview showed? Then adjudicate the row (accept-with-known-offset / route to N12 tone-map work / FAIL). Instrument caveat: the harness's "preview" is the video-output readback, which can include display tone mapping — your screen comparison is the ground truth it cannot capture.
5. **N1's deferred checklist** (its closeout, items 1–3) also clears at this stop.

**Test counts:** **156 green** (`swift test`): 117 carried baseline (118 − the deleted CFMedia smoke marker) + 20 CompositionPlanner/MediaTime pure tests (incl. 2 compositor-cadence tests from finding 2) + 14 probe/builder integration tests + 5 PlayerEngine contract tests (finding 4; incl. the stale-load race test, finding 3) — tiny AVAssetWriter fixtures rendered at test time in temp, no footage dependency. `CFExportModule` marker + smoke test stay until N12 (the export spike is harness code by design — N1 delta 5).

**Files:** NEW `mac/ClipFarmKit/Sources/CFMedia/{MediaTime,AssetCache,MetadataProbe,CompositionBuilder,PlayerEngine}.swift`, `Sources/CFMediaTestSupport/{MediaFixtureRenderer,PixelProbe}.swift`, `Sources/n2harness/{Main,HarnessEnv,FrameTap,MP4BoxParser,GateSeams,GateBlink,GateRotationHDR,GateRebuildLoopStep,GateFades,GateExportSpike,Demo}.swift`, `Tests/CFMediaTests/{MediaFixtures,PlannerTests,MediaIntegrationTests,EngineTests}.swift`. MODIFIED `Package.swift` (CFMediaTestSupport + n2harness targets), `Sources/CFStore/LibrarySettings.swift` (+`smoothCutAudio`, own commit `8e24684`). DELETED the `CFMedia.swift` N0 marker + its smoke test.

---

## Phase N1 — Domain models + persistence core (native rewrite)

**Manual verify: VERIFIED ✅ (Lillian, 2026-07-06 at the N2 hard stop)** — item 1 via the then-current 157-test suite (`swift test` green from `mac/ClipFarmKit`); item 2 via Xcode Run at the post-move path (`~/dev/clipfarm/mac/`): app launches, six-item sidebar + Library + inspector/preview pane confirmed; item 3 stands on the in-session scratch-library evidence below. Original deferred checklist retained for the record:

1. `cd mac/ClipFarmKit && swift test` → 118 tests green (~30s cold build, <1s test run).
2. `cd mac && xcodebuild -scheme ClipFarm -configuration Debug build | xcbeautify -q` → clean (the app no longer carries the N0 linkage probe).
3. Schema check — pre-verified in-session against a scratch library (`sqlite3 <lib>/clipfarm.db .schema` matches plan §2.3 exactly: all ten tables + `clips_fts` + three sync triggers + the `COALESCE` unique index; `.snapshots/` pruned to exactly 50 after 56 snapshot calls; the pre-destructive snapshot held the pre-change rows while the main DB held the post-change state). To re-verify by hand, any CFStore test's temp library works, or ask a session to re-run the scratch-evidence drill.

**Built (2026-07-06):** the data layer, per plan §4/N1 — `models.py`/`store.py`/`resolver.py`/`continuity.py` ported; nothing sits on it yet. Plan entry committed pre-implementation (`911c939`, per the N0 finding-1 process rule); work landed as three commits (schema+models → resolver+continuity → services+settings scaffolding).

**CFDomain (pure, zero deps — not even Foundation):**

- `Entities.swift` + `EntitiesCodable.swift` — field-for-field port of every entity: `Source` (+ native `isHDR`/`naturalWidth`/`naturalHeight`), `Clip` (+ new `boundaryEdited`; `tracks` nil until N18), `TracksOverride`/`AudioOverride`/`VideoOverride`/`Overlay`, `ProjectTag`, `Script` (amendment-#10 naming), `Project`, `ClipProjectTag`, `AttemptClip`, `Attempt` (+ `needsReview`), `VoiceAnnotation`; enums `ClipCategory`, `TagKind` (incl. `.tag`), `TagSource`, `PremadeBucket`, `AttemptSource`. Timestamps stay ISO strings (backup-format parity). Codable with the documented snake_case keys, decode-with-defaults (missing optional/defaulted keys tolerated — the substrate for the N3/N9 fixture loader and the N13 tolerant restore), encode writes explicit `null`s.
- `ClipFarmState.swift` — the whole-library value container mirroring the documented JSON shape + **uniqueness as domain rule**: `validateClipProjectTagUniqueness` throws on duplicate `(clip_id, project_id, project_tag_id, category)`; nil tag ID is a value, not a bypass (finding 10 — domain validation is the enforcer).
- `Whisper.swift` — sidecar models (`schema_version` pinned; full ingest validation semantics arrive at N3) + `allWords` flattening; leading-space word convention preserved and tested.
- `Identifiers.swift` — `ClipID.hms` (`HH-MM-SS.mmm`, **half-even rounding** matching Python 3's `int(round())` so IDs golden-master-match at N3 — tested at exact .5ms boundaries), `ClipID.make`, stem validation + sanitized-rename helper, `nextNumericID` (max+1 over all numeric keys, gaps never refilled, non-numeric ignored — Python `isdigit()` parity).
- `Resolver.swift` — `resolveAttempt` port, contract intact: order preserved; dangling ref → one `.tombstone`; source-bounds clamp with warnings; zero/negative effective duration throws; `internalPauseMaxSec` splits on **strictly-greater** gaps with the gap **dropped entirely**; straddle-excluding word filter ported as-is (N15 owns the fix); missing source/transcript → single-range fallback + warning. **Port adaptations (purity):** transcripts via injected `transcriptProvider: (Source) -> WhisperTranscript?`; diagnostics via `onWarning: (ResolverWarning) -> Void` (typed cases replace log-string assertions); `KeyError`/`ValueError` → `ResolverError`.
- `Continuity.swift` — `continuityScore` (runs = same source AND forward progression; max-run/total; empty/all-orphan/zero-runtime throw typed errors) + `refreshContinuityScore(of: inout Attempt, in:)` (degenerate → nil; the `refresh_attempt_continuity` port).

**CFStore (the only GRDB seam):**

- `LibrarySchema.swift` — `DatabaseMigrator`, `"v1"` registered from day one; tables exactly per plan §2.3. FTS5 **external-content** `clips_fts` + insert/delete/`UPDATE OF transcript_text` triggers (undo-safe — undo replays ordinary statements). **NULL-proof unique index** `(clip_id, project_id, COALESCE(project_tag_id,''), category)`. FK edges follow the plan's explicit markings: `clips.source_id`, `clip_project_tags.clip_id`, `project_tags.project_id`, `attempt_clips.attempt_id` (with `ON DELETE CASCADE` — an attempt's clip list is its own composition) are FKs; `attempt_clips.clip_id` and `attempts.parent_attempt_id` are **deliberately not** (tombstones + dangling fork parents); `attempts.project_id` / `clip_project_tags.project_id` are plain columns (unmarked in §2.3; N6's delete-project-hard-deletes-attempts stays explicit op code). `meta.schema_version` is informational (the migrator is the enforcer; the backup JSON will carry it).
- `LibraryStore.swift` — `open(at:undoManager:now:)`: create folder → `DatabasePool` (WAL) → **refuse a superseded library** (`hasBeenSuperseded` → `librarySupersededByNewerApp`) → migrate → stamp `meta.created_at` once (injected clock) → **source-integrity check** (missing files flip `unavailable`, reappearing files flip back; never crash the load). `close()`. Injected `UndoManager?` + injected `now` (inject-time-and-identity rule).
- `Records.swift` — per-table Codable record adapters (CFDomain stays GRDB-free); `clips.tracks` and `projects.script_lines` are JSON text columns mapped inside CFStore only; `script == nil` (NULL) vs `Script(lines: [])` (`'[]'`) round-trip distinctly (tested).
- `Snapshots.swift` — `<ISO>-<ms>-<token4>__<reason>.db` under `.snapshots/`, prune to 50, partial-file cleanup on a failed `VACUUM INTO`. **`performDestructive(reason:_:)` runs snapshot + mutating transaction inside ONE `writeWithoutTransaction` barrier** (finding 11: VACUUM can't run inside a transaction; one barrier access means no writer can interleave). Failed mutations roll back while the pre-op snapshot remains. Undo of a destructive op takes no snapshot (tested).
- `StoreMutations.swift` — N1 mutation surface: `addSource` (allocates via `nextNumericID`), `addClips` (bulk, all-or-nothing, duplicate-ID rejection), `addClipProjectTag` (domain-validates against stored rows; DB index as backstop), `importState` (whole-library replace in one transaction — the fixture/restore primitive; validates first, clears the undo stack by design, not undo-registered). Undo plumbing: a symmetric `registerUndo(actionName:inverse:reapply:)` helper — each inverse re-registers its counterpart, giving unbounded undo/redo chains; every mutation names its action ("Add Source" / "Add Clips" / "Tag Clip").
- `StoreReads.swift` — `fetchState()` (whole-library value snapshot: backup/fixture/round-trip primitive; per-view reads arrive with ValueObservation at N4+), `source(id:)`, `clip(id:)`.
- `LibrarySettings.swift` — typed per-library settings over the `settings` table (D18 scaffolding): `silenceThresholdSec` (2.0), `tailPolicy` (extend-to-next-word-start / fixed-padding / word-end), `tailPaddingSec`. Missing keys → defaults; unknown keys ignored; unparseable values fall back.
- `LibraryManager.swift` — the **close→swap→reopen path**: undo stack cleared FIRST on every transition (inverses capture the outgoing store), then close, then open; `storeDidChange` fires with the new store (nil on close) — the ValueObservation-restart hook N4+ subscribes to; snapshot-restore and backup-restore (N13) reuse this path.

**CFLLM (settings scaffolding, D22/D23):**

- `TaggingPreferences.swift` — app-level prefs in injected `UserDefaults`: provider (ollama default), `llama3.1:8b`, `claude-sonnet-4-6`, model-options constant; garbage stored values fall back.
- `SecretStore.swift` — protocol + `KeychainSecretStore` (generic password, service `org.duartes.clipfarm`) + `InMemorySecretStore`. The API key's only home is the Keychain — tested that nothing key-shaped reaches UserDefaults. **Live-Keychain path verified at N7** with the Settings page (tests use the in-memory double to keep `swift test` off the login keychain).

**N0 scaffolding cleanup:** `CFDomainModule`/`CFStoreModule`/`CFLLMModule` markers + their smoke tests deleted; the `precondition` linkage probe removed from `ClipFarmApp.swift` (real Kit code proves linkage). CFMedia/CFExport markers remain until N2. New **`CFTestSupport`** target (fixture builders — `Fixtures.state/stateWithClips/transcript/fullState` mirror the reference suites' helpers) under the same `kitSwiftSettings`; not part of the library product, never ships.

**Platform discovery (corrects the N0 delta and the N1 kickoff's premise):** the macOS 26 SDK marks `NSUndoManager` **`NS_SWIFT_UI_ACTOR` (@MainActor)** — the class and the `registerUndo` handler closures. "UndoManager is Foundation, drive it directly from Kit tests" stands, but the calls are MainActor-isolated. Resolution: **method-level `@MainActor`** on the undo-registering mutations (`addSource`/`addClips`/`addClipProjectTag`/`importState`), on `LibraryManager`, and on the undo-driving tests — explicitly NOT a Kit-target isolation-default flip (forbidden). Reads, snapshots, `performDestructive`, and settings stay nonisolated. Architecturally consistent with §2.7 (user mutations arrive from the MainActor AppStore); if a background writer (N7 tagging commit) ever needs a non-undo bulk path, it can be added nonisolated then.

**Naming deviation:** the reference's `Category` type is **`ClipCategory`** in Swift — `Category` collides with an SDK type in any Foundation-importing file ("port semantics, not spellings").

**Provisional calls (3) — all RESOLVED (Lillian, 2026-07-06, live ahead of the checkpoint; `QUESTIONS.md` → Answered):**

1. **Settings writes are not undo-registered.** mac/CLAUDE.md's blanket "every store mutation lands with a register→undo→redo test" vs the platform convention that config changes never sit on the document undo stack. Options: (a) undo-register settings too; (b) scope undo to library-content mutations; (c) defer settings to N3. Implemented **(b)** — D18's re-apply action (the thing that changes *data*) is snapshot-protected + undoable at N3 regardless. **Answered: keep as implemented** — content-only undo is final.
2. **Snapshot filename token is a random 4-hex collision token, not a content hash.** The reference hashed file bytes; with `VACUUM INTO` the content isn't knowable pre-copy without reading the whole DB (+WAL). The spec's stated purpose is same-millisecond collision avoidance, which the token satisfies (tested under a frozen clock). Options: hash live file bytes / random token / meta counter. Implemented **token**. **Answered: keep as implemented.**
3. **`tailPaddingSec` default.** D18 names "fixed padding +N ms" without a default N. Options: 0.0 (inert until N3's UI exposes it) / ~0.25s / no default. Implemented 0.0. **Answered: CHANGED — Lillian picked 0.25s**; reworked in `LibrarySettings.swift` + tests at the adjudication pass. N3's segmentation UI can still revisit once results are audible.

**Recorded divergences from the Python reference (adjudicated against spec/plan):**

- Watcher/conflict-freeze/`WritesFrozenError` tests: not ported (D7 — machinery dissolved).
- "Snapshot with no state file → None": doesn't port — an open library always has a database file; a snapshot always lands.
- Atomic-write/tmp-file/save-lock tests: superseded by SQLite transactions + GRDB's serialized writer.
- `test_settings` reshaped across three lanes: segmentation → DB settings table (CFStoreTests); provider/model → UserDefaults (CFLLMTests); API key → Keychain contract (CFLLMTests). The chmod-0o600 and plaintext-at-rest tests died with the settings file — the key not being in a file is the point.
- Uniqueness-test fixtures now create the clips they tag (`clip_project_tags.clip_id` IS an FK per §2.3; the reference fixtures dangled).
- Resolver: `KeyError`/`ValueError` → typed errors; caplog assertions → `onWarning` captures; transcripts injected rather than disk-loaded inside the resolver.
- `unavailable` semantics: the integrity check also flips `true → false` when a file reappears (reference behavior, kept: it recomputed the flag both ways).

**Deviations from the committed plan entry:** one — the commit split. `StoreMutations`/`StoreReads` rode in the schema commit rather than the services commit, because SPM refuses a test target with zero sources: deleting the N0 smoke test in the schema commit required at least one real CFStoreTests file, and every store test needs `importState`/`fetchState`. The schema commit therefore contains the persistence core + its ported tests; the services commit carries snapshots/settings/manager/CFLLM. Recorded here per the audit-trail rule.

**Swift Testing gotcha (for future phases):** a closure whose only `try`s live inside `#expect` macros is inferred non-throwing (macro bodies are invisible to throws inference) — annotate such closures `{ (x) throws in … }`. Two tests carry the comment.

**Cold review (2026-07-06) — findings & dispositions** (review 2 of 2; reviewer ran with zero implementation context; all ten findings adjudicated implementer-vs-reviewer and ACCEPTED — the reviewer was right on every count; fixed the same day):

1. **[MINOR] Failed-VACUUM cleanup could delete a pre-existing valid snapshot; the test pinned the hazard as the contract** → **ACCEPTED.** Correct and the sharpest finding: a same-ms + same-token filename collision (~1/65536 per same-ms pair) would have made the failure path destroy the *older good snapshot* — the crash-surviving belt. Fixed: `writeSnapshot` records `fileExists` before the VACUUM and removes the file on failure only if it did NOT pre-exist; the test is reworked (`failedSnapshotNeverDestroysAPreexistingFile`) to assert the pre-existing file survives byte-identical while the error still propagates.
2. **[MINOR] FTS5 external-content index is keyed to `clips`' implicit rowid; `VACUUM INTO` is not documented to preserve implicit rowids → latent restore-time desync** → **ACCEPTED.** Nothing broken today (reviewer verified empirically on SQLite 3.51), but the guarantee is absent from the docs. Fixed as proposed: a RESTORE-TIME CAVEAT comment now sits on the trigger block in `LibrarySchema.swift` instructing the restore implementer (Settings→Restore / N13) to run `INSERT INTO clips_fts(clips_fts) VALUES('rebuild')` after opening a restored database. N13 owns the runtime fix.
3. **[MINOR] ID allocation and uniqueness validation ran outside the write transaction (TOCTOU)** → **ACCEPTED.** Safe today (@MainActor + synchronous), but the closeout itself contemplates a future nonisolated bulk writer at N7 — check-then-act must not straddle accesses. Fixed: `addSource` allocates inside its `dbPool.write`; `addClipProjectTag` fetches + validates + inserts in one transaction. Zero behavior change; existing tests pass unchanged.
4. **[MINOR] `LibraryManager.open` failure path violated the `storeDidChange` contract** → **ACCEPTED.** The outgoing store is already closed by then — exactly when observers need teardown. Fixed: the catch path fires `storeDidChange?(nil)` before rethrowing; failure semantics documented; new regression test `failedOpenFiresStoreDidChangeNilAndClearsState` (superseded-library folder → open throws → events `[true, false]`, store nil, undo cleared).
5. **[MINOR] `LibraryStore.open` leaked the `DatabasePool` when migrate/stamp/integrity threw** → **ACCEPTED.** Only the superseded branch closed the pool. Fixed: all post-pool steps wrapped in one `do/catch { try? dbPool.close(); throw }` (the superseded branch folded into it).
6. **[MINOR] `Project.name` min_length=1 validation dropped without being recorded** → **ACCEPTED.** Real reference divergence the divergence list missed. Fixed the stronger way: `StateValidationError.emptyProjectName` added to `ClipFarmState.validate()` (the import/load seam; struct construction stays unchecked — N6's project ops are the other enforcement seam, documented in code), + domain test `emptyProjectNameFailsValidation`.
7. **[NIT] Model-options list mixed ID forms (dated Haiku ID vs aliases)** → **ACCEPTED.** `claude-haiku-4-5-20251001` → the canonical alias `claude-haiku-4-5` in `TaggingPreferences.anthropicModelOptions` + tests (reviewer verified all three against the current Anthropic catalog). Recorded as a deliberate deviation from the reference's dated ID.
8. **[NIT] `importState` doc said "entire library content" but leaves `settings` untouched** → **ACCEPTED.** Doc now states the `settings`/`meta` tables are deliberately untouched (not part of the documented JSON shape) and names N13 as owner of the do-settings-travel-with-a-backup decision.
9. **[NIT] `performDestructive` carries only half of the "snapshot AND undo — both, always" invariant with no reminder** → **ACCEPTED.** Doc sentence added: every call site (N5 onward) must pair it with `registerUndo(actionName:inverse:reapply:)`.
10. **[NIT] `Overlay.type` silently widened from `Literal["blackout"]` to free `String`** → **ACCEPTED.** Constraint comment added at the field: make it an enum when N18 activates `tracks`.

**Test counts:** **118 green** (`swift test`, <1s run; 116 at first closeout + 2 from the cold-review adjudication): CFDomainTests 52 (resolver 14, continuity 9, refresh 5, identifiers 11, model defaults + Codable + validation rules 13), CFStoreTests 58 (open/schema 7, round-trip 8, uniqueness 7, integrity 3, snapshots 9, migrations 4, settings 5, FTS sync 3, undo 7, manager 5), CFLLMTests 6, smoke (CFMedia/CFExport markers) 2. Baseline for N2. `xcodebuild` app build clean.

**Next-phase delta (N2, per the closeout ritual — plan §4/N2 read in full):**

1. **Resolver handoff shape:** N2's `PlayerEngine` consumes `[ResolvedItem]` (`.range(ResolvedRange)` with `clipID`/`sourceID`/`effectiveStartSec`/`effectiveEndSec`, `.tombstone` skipped by the builder per §2.5 rule 7). The N2 debug harness can bypass the resolver entirely (hand-specified file/start/end ranges — no ingest, as planned) but the CFMedia range type should be constructible from `ResolvedRange` + a source-path lookup.
2. **`transcriptProvider` is the engine's job to supply** when internal-pause expansion is wanted; N2's harness passes `{ _ in nil }` — real sidecar loading arrives with N3/N4 (a CFStore/CFMedia-side loader, cached, like the reference's `transcripts.py`).
3. **"Smooth cut audio" setting:** micro-fades are in N2 scope; the per-library `LibrarySettings` accessor for it doesn't exist yet — add `smoothCutAudio: Bool = true` to `LibrarySettings` (one accessor + tests) when the engine wires it. Plan §4/N2 amended with a parenthetical.
4. **UndoManager is @MainActor in the macOS 26 SDK** (see platform discovery above) — N2 doesn't register undo, but any phase that does inherits the method-level-@MainActor pattern, and undo-driving tests are `@MainActor @Test`.
5. **Marker cleanup continues:** delete `CFMediaModule` (+ smoke test) as N2's real CFMedia code lands; `CFExportModule` goes when the export mini-spike writes real CFExport code (or at N12, whichever first).
6. **Footage stays read-only:** N2's gates run against real files in the dogfood folder — reads only, ever; no shell writes there (standing rule).
7. `swift test` baseline is 118 (post-adjudication); N2 adds CFMedia tests where logic is testable without hardware timing (gate measurements are the harness's job, not unit tests).

**Files:**

```
NEW:
  mac/ClipFarmKit/Sources/CFDomain/{Entities,EntitiesCodable,ClipFarmState,
                                    Whisper,Identifiers,Resolver,Continuity}.swift
  mac/ClipFarmKit/Sources/CFStore/{LibrarySchema,Records,LibraryStore,StoreReads,
                                   StoreMutations,Snapshots,LibrarySettings,
                                   LibraryManager}.swift
  mac/ClipFarmKit/Sources/CFLLM/{TaggingPreferences,SecretStore}.swift
  mac/ClipFarmKit/Sources/CFTestSupport/Fixtures.swift
  mac/ClipFarmKit/Tests/CFDomainTests/{ResolverTests,ContinuityTests,
      ContinuityRefreshTests,IdentifierTests,ModelDefaultsTests}.swift
  mac/ClipFarmKit/Tests/CFStoreTests/{StoreTestSupport,StoreOpenTests,
      StoreRoundTripTests,UniquenessTests,SourceIntegrityTests,SnapshotTests,
      MigrationTests,LibrarySettingsTests,FTSSyncTests,UndoTests,
      LibraryManagerTests}.swift
  mac/ClipFarmKit/Tests/CFLLMTests/TaggingPreferencesTests.swift

DELETED (N0 scaffolding):
  mac/ClipFarmKit/Sources/{CFDomain/CFDomain,CFStore/CFStore,CFLLM/CFLLM}.swift
  mac/ClipFarmKit/Tests/{CFDomainTests/CFDomainSmokeTests,
      CFStoreTests/CFStoreSmokeTests,CFLLMTests/CFLLMSmokeTests}.swift

MODIFIED:
  mac/ClipFarmKit/Package.swift        — CFTestSupport target; CFStoreTests + GRDB
  mac/ClipFarm/App/ClipFarmApp.swift   — linkage probe removed
  PHASES.md                            — N1 plan entry (committed pre-code) → pointer
  NATIVE_REWRITE_PLAN.md               — N2 scope parenthetical (delta #3)
  COMPLETED_PHASES.md                  — this entry
  KICKOFF_MESSAGES.md                  — N1 marked used; N2 kickoff queued
  QUESTIONS.md                         — 3 PROVISIONAL items

ADJUDICATION FOLLOW-UP (2026-07-06, same-day commit):
  mac/ClipFarmKit/Sources/CFStore/Snapshots.swift        — findings 1, 9
  mac/ClipFarmKit/Sources/CFStore/LibrarySchema.swift    — finding 2
  mac/ClipFarmKit/Sources/CFStore/StoreMutations.swift   — findings 3, 8
  mac/ClipFarmKit/Sources/CFStore/LibraryManager.swift   — finding 4
  mac/ClipFarmKit/Sources/CFStore/LibraryStore.swift     — finding 5
  mac/ClipFarmKit/Sources/CFDomain/ClipFarmState.swift   — finding 6
  mac/ClipFarmKit/Sources/CFDomain/Entities.swift        — finding 10
  mac/ClipFarmKit/Sources/CFLLM/TaggingPreferences.swift — finding 7
  mac/ClipFarmKit/Sources/CFStore/LibrarySettings.swift  — tailPaddingSec 0.25 (Lillian)
  Tests: SnapshotTests (reworked), LibraryManagerTests (+1),
         ModelDefaultsTests (+1), LibrarySettingsTests, TaggingPreferencesTests
  .gitignore + .obsidian/ untracked    — authorized by Lillian (files stay on disk)
  QUESTIONS.md                         — all 4 open items → Answered
  COMPLETED_PHASES.md                  — dispositions block; provisionals flipped
```

---

## Phase N0 — Toolchain & skeleton (native rewrite)

**Manual verify: VERIFIED ✅ (Lillian, 2026-07-06 at the N2 hard stop)** — item 1's Run half completed: app Run from Xcode at the post-move path (`~/dev/clipfarm/mac/`) launches with the six-item sidebar + inspector/preview pane; items 2–3 via the current suite/build (157 tests green, clean builds through the N2 commits); item 4 stands on the N0 programmatic pre-verification. Original checklist retained for the record:

*Partial verify 2026-07-06 (Lillian, live during the N1 run): opened `ClipFarm.xcodeproj` in Xcode successfully — the hand-authored pbxproj parses and loads in the GUI, settling the pbxproj-validity risk. Item 1's Run-the-app half and items 2–4 remain deferred.*

1. `open mac/ClipFarm.xcodeproj` → Run: app launches, shows the six-item sidebar (Library / Project / Script / Attempts / Brief / Settings) and the right-side inspector pane with a "Preview" placeholder + toolbar toggle.
2. `cd mac/ClipFarmKit && swift test` → 6 tests green.
3. `cd mac && xcodebuild -scheme ClipFarm -configuration Debug build | xcbeautify -q` → clean.
4. Drop any stray `.swift` file under `mac/ClipFarm/`, rebuild — it compiles with zero pbxproj edits (pre-verified programmatically; see below).

**Built (2026-07-05):** the native stack end-to-end, no features — plan `NATIVE_REWRITE_PLAN.md` §4/N0.

- **`mac/ClipFarmKit`** — local SPM package, swift-tools 6.2, platform macOS 26. Five source targets (CFDomain / CFStore / CFMedia / CFLLM / CFExport, each a one-file module marker documenting its future contents) + five Swift Testing test targets. **Isolation policy per mac/CLAUDE.md (SE-0466):** every target (source *and* test) gets `kitSwiftSettings = [.defaultIsolation(nil), .enableUpcomingFeature("NonisolatedNonsendingByDefault"), .enableUpcomingFeature("InferIsolatedConformances")]` — explicit nonisolated default; the two upcoming features are the Approachable Concurrency deltas not already implied by Swift 6 language mode, keeping SE-0461 symmetric with the app target. GRDB dependency `from: "7.0.0"`, **resolved and committed at 7.11.1** (`Package.resolved` is the pin).
- **`mac/ClipFarm/`** (app sources, buildable folder) — `App/` (ClipFarmApp with a `precondition(CFDomainModule.name == "CFDomain")` proving Kit linkage; RootView = NavigationSplitView with the six nav items; InspectorPane placeholder per D30 with a toolbar toggle; NavigationItem enum), `Features/<Page>/<Page>View.swift` × 6 (ContentUnavailableView placeholders only), minimal `Assets.xcassets` (AppIcon/AccentColor stubs). No `Shared/` yet — it appears with its first real file.
- **`mac/ClipFarm.xcodeproj`** — **hand-authored pbxproj succeeded on the first build**; the File→New-Project fallback was not needed. objectVersion 77; `PBXFileSystemSynchronizedRootGroup` for `ClipFarm/` wired via `fileSystemSynchronizedGroups` (buildable folder — empty Sources/Resources phases, files discovered from disk); `XCLocalSwiftPackageReference` → `ClipFarmKit` + one `XCSwiftPackageProductDependency` in Frameworks. Shared scheme `ClipFarm` committed (`xcshareddata/xcschemes/`) so headless `xcodebuild -scheme ClipFarm` works in a fresh clone.
- **Target settings:** `PRODUCT_BUNDLE_IDENTIFIER=org.duartes.clipfarm`, `MACOSX_DEPLOYMENT_TARGET=26.0`, `CODE_SIGN_STYLE=Automatic` + `DEVELOPMENT_TEAM=384925MZJ6` (bound to the real cert `Apple Development: lil@duartes.org (3Z7KXSJP8G)` — the N0 START cert-assist contingency was not needed; TCC grants will persist per finding 19f/D24), **non-sandboxed** (no entitlements file; verified post-build: the only entitlement is debug `get-task-allow`), `ENABLE_HARDENED_RUNTIME=YES`, `GENERATE_INFOPLIST_FILE=YES`, `SWIFT_VERSION=6.0`, `SWIFT_APPROACHABLE_CONCURRENCY=YES`, `SWIFT_DEFAULT_ACTOR_ISOLATION=MainActor` (app target only, per policy).
- **`.gitignore`** — added `mac/ClipFarmKit/.build/`, `xcuserdata/`, `*.xcuserstate`, `DerivedData/`. Both `Package.resolved` files (Kit + xcodeproj workspace) are committed deliberately.
- **`mac/CLAUDE.md`** — commands section updated: scheme name verified as `ClipFarm`, xcbeautify 3.2.1 installed (Homebrew), benign "multiple matching destinations" note documented.

**Verification performed in-session (all green):**

- `swift test` from `mac/ClipFarmKit`: **6/6 tests pass** (~9s cold build) — one module-marker test per target + `grdbOpensAnInMemoryDatabase` (in-memory `DatabaseQueue`, `SELECT 1`) proving the GRDB link.
- `xcodebuild -scheme ClipFarm -configuration Debug build`: **BUILD SUCCEEDED**, zero compile warnings; signs with the real Apple Development identity; re-verified through `| xcbeautify -q`.
- `codesign -dv --entitlements -`: Identifier `org.duartes.clipfarm`, TeamIdentifier `384925MZJ6`, hardened-runtime flag set, no sandbox entitlement.
- **Buildable-folder proof:** a throwaway `Shared/BuildProbe.swift` was created, compiled into the target by the very next `xcodebuild` run with **zero pbxproj edits** (`SwiftCompile … BuildProbe.swift` in the log), then deleted.
- **Launch smoke:** built binary launched headless, process stayed alive >3s with empty stderr, then killed (SIGTERM).

**Environment recorded:** Xcode 26.3 (17C519), Swift 6.2.4, macOS 26.4.1, GRDB 7.11.1, xcbeautify 3.2.1.

**Provisional calls — all four RESOLVED (Lillian, 2026-07-05: keep as implemented; `QUESTIONS.md` → Answered).** Options recorded here with their alternatives, since the pre-code PHASES.md plan entry was collapsed to a pointer at closeout and never committed (cold-review finding 1 below):

1. **GRDB pin mechanics** — options: `exact:` a specific 7.x (blocks GRDB patch releases; requires guessing the latest version) / `from: "7.0.0"` with the exact version pinned by the committed `Package.resolved` (standard SPM practice) / vendored checkout (heaviest, nothing needs it). Implemented and kept: `from:` + Package.resolved at 7.11.1.
2. **Package product shape** — one umbrella `ClipFarmKit` library product exporting all five targets (one pbxproj product dependency; app still imports modules individually) vs five separate products. Implemented and kept: umbrella.
3. **Intra-Kit dependency graph** — guess the full graph now (e.g. CFExport→CFMedia) vs minimal (every non-domain target → CFDomain only; CFStore also → GRDB), edges added when a phase needs them. Implemented and kept: minimal.
4. **App-target Swift language mode** — `SWIFT_VERSION=5.0` (Apple's migration recommendation, wrong for greenfield) vs `6.0` (strict concurrency; matches Kit targets, which default to mode 6 under swift-tools 6.2). Implemented and kept: 6.0.

**Deviations from plan:** none of substance. xcbeautify was missing from the machine and installed via Homebrew — treated as executing the documented CLI loop (dev tooling named in mac/CLAUDE.md), not as a new third-party dependency; flagged here for transparency. `swift test` output shows the toolchain's testing library targeting `arm64e-apple-macos14.0` — that is the prebuilt Swift Testing runtime's own deployment floor, not the package's (platform macOS 26 is enforced at compile time).

**Test counts:** 6 smoke tests (baseline for N1; no ported tests yet by design).

**Next-phase delta (N1, per the closeout ritual):** read in full; **no amendment to the N1 plan entry required.** Reality anchors for N1: GRDB is 7.11.1; `kitSwiftSettings` in `Package.swift` already applies the isolation policy to any new code in existing targets (N1 adds files, not targets); the five module-marker enums (`CFDomainModule` etc.) are N0 scaffolding to be deleted as real code lands — together with the `precondition(...)` linkage probe in `ClipFarmApp.init` (finding 6); smoke tests sit in `Tests/<Target>Tests/` ready to grow; the `swift test` loop is fast (~9s cold, sub-second incremental) so the ~90 N1 tests ride it comfortably. One sequencing note *(wording corrected per cold-review finding 2)*: `UndoManager` is a **Foundation** class — Kit tests drive it directly against store methods, exactly as mac/CLAUDE.md's register→undo→redo rule requires; only the *window's instance* (`NSWindow.undoManager` / `@Environment(\.undoManager)`) is vended app-side. Design consequence: CFStore's close→swap→reopen path takes an injected `UndoManager` (or exposes a clear-stack hook) rather than owning a window's; exact shape resolved at N1 planning time.

**Cold review (2026-07-06) — findings & dispositions** (review 2 of 2; reviewer ran with zero implementation context; adjudicated implementer-vs-reviewer per the autonomous-batching rules; all seven findings verified against the tree before disposition):

1. **[MINOR] Closeout referenced a PHASES.md N0 plan entry that was never committed** → **ACCEPTED.** Correct: the plan entry was written pre-code per workflow rule 1 but collapsed to a pointer at closeout inside a single commit, so the plan-first artifact never reached git (`git show 9b8add9 -- PHASES.md` = pointer only). Fixed: the provisional-calls section above now carries the four options verbatim and points at `QUESTIONS.md`. **Process rule for future auto-continued phases: commit the PHASES.md plan entry (or preserve it verbatim in the closeout) so the audit trail the two-review process depends on is in git.**
2. **[MINOR] "UndoManager is an AppKit-side object" premise in the N1 delta + kickoff** → **ACCEPTED.** `UndoManager` is Foundation; that is precisely why mac/CLAUDE.md can require driving it directly in Kit tests. Wording corrected in both places (delta above; `KICKOFF_MESSAGES.md` N1 kickoff); the seam advice survives as a design choice — inject the manager, never own a window's.
3. **[NIT] Missing "Run" line in mac/CLAUDE.md Commands vs plan §2.1** → **ACCEPTED.** Direct-binary run line added to the Commands block.
4. **[NIT] `xcodebuild test` passes vacuously (empty `<Testables>`, no app test targets)** → **ACCEPTED.** Parenthetical added to Commands: no app test targets exist yet (D25); when they land they WILL require scheme edits despite the buildable-folder rule.
5. **[NIT] Dual `Package.resolved` drift hazard under `from:`** → **ACCEPTED.** Rule added to the Commands note (and the `QUESTIONS.md` answered entry): after any dependency re-resolve, re-commit **both** lockfiles so `swift test` and app builds stay on the same GRDB.
6. **[NIT] `precondition(...)` linkage probe ships in Release** → **ACCEPTED, deletion scheduled at N1** (no code change now — linkage proof is still doing its N0 job until real Kit imports exist). The N1 kickoff now explicitly instructs deleting the probe together with the module-marker enums.
7. **[NIT] `NavigationItem.rawValue` doubles as user-facing label** → **ACCEPTED, fixed now** rather than parked (backlog rule: nothing owned it). Raw values are now lowercase identifiers; labels come from a dedicated `label` property; rebuilt clean via the documented loop.

**Files:**

```
NEW:
  mac/ClipFarm.xcodeproj/project.pbxproj
  mac/ClipFarm.xcodeproj/project.xcworkspace/contents.xcworkspacedata
  mac/ClipFarm.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved
  mac/ClipFarm.xcodeproj/xcshareddata/xcschemes/ClipFarm.xcscheme
  mac/ClipFarm/App/{ClipFarmApp,RootView,InspectorPane,NavigationItem}.swift
  mac/ClipFarm/Features/{Library/LibraryView,Project/ProjectView,ScriptTOC/ScriptTOCView,
                         Attempts/AttemptsView,Brief/BriefView,Settings/SettingsView}.swift
  mac/ClipFarm/Assets.xcassets/{Contents.json,AppIcon.appiconset/,AccentColor.colorset/}
  mac/ClipFarmKit/{Package.swift,Package.resolved}
  mac/ClipFarmKit/Sources/{CFDomain,CFStore,CFMedia,CFLLM,CFExport}/<Module>.swift
  mac/ClipFarmKit/Tests/{CFDomain,CFStore,CFMedia,CFLLM,CFExport}Tests/<Module>SmokeTests.swift

MODIFIED:
  .gitignore                 — Xcode/SPM artifacts
  mac/CLAUDE.md              — scheme + xcbeautify verified note
  PHASES.md                  — N0 plan entry → closeout pointer
  KICKOFF_MESSAGES.md        — N0 marked used; N1 kickoff queued
  QUESTIONS.md               — 4 PROVISIONAL items

ADJUDICATION FOLLOW-UP (2026-07-06, second commit):
  mac/ClipFarm/App/NavigationItem.swift — label/identifier split (finding 7)
  mac/ClipFarm/App/RootView.swift       — uses item.label (finding 7)
  mac/CLAUDE.md                         — Run line, vacuous-test note, dual-lockfile rule (findings 3/4/5)
  KICKOFF_MESSAGES.md                   — UndoManager correction + scaffolding-cleanup step (findings 2/6)
  COMPLETED_PHASES.md                   — this disposition block; provisionals flipped to resolved (finding 1, Item 2)
  QUESTIONS.md                          — 4 items moved Open → Answered (Lillian: keep as implemented)
```

---

## Phase 9.5 — Tagging provider toggle (Ollama / Anthropic API)

**Verified by Lillian:** ⏳ pending (live verify in progress as of 2026-05-26).

**Built (2026-05-26):** the Phase 6.1 deferred Sonnet-toggle decision finally lands. Switch between local Ollama (default, free, ~5 min per chrysalis-size run) and Anthropic API (opt-in, paid, ~30s with Sonnet 4.6) from `/settings`. Single dispatcher; orchestrators stay provider-agnostic.

- **`clipfarm/settings.py`** — `TaggingSettings` (provider / ollama_model / anthropic_model / anthropic_api_key) inside a versioned `Settings` container. Storage: `.clipfarm/settings.json` (gitignored, deliberately separate from `clipfarm.json` since the API key must not go in the hand-editable project state). Atomic writes via tmp+rename. Corrupt-file load returns defaults with a warning so tagging can't break on bad config. `CLIPFARM_SETTINGS_PATH` env override for tests.
- **`clipfarm/llm_anthropic.py`** — `chat_with_json_schema_anthropic` matches the existing `chat_with_json_schema` contract (returns parsed dict or `None`; never raises). Structured output via tool use: a single forced `submit_tags` tool whose `input_schema` is the caller's existing JSON schema. **Prompt caching** on the system message (`cache_control: {"type": "ephemeral"}`) so the shared brief context across N batches in a tagging run hits the 5-min cache. Lazy SDK import — users on Ollama don't pay the cost. Defensive extraction handles both typed-SDK and dict response shapes.
- **`clipfarm/routes/settings.py`** — GET (key never returned; replaced with `anthropic_api_key_set: bool`), PATCH (update provider / model), POST `/anthropic-key` (Set + test affordance — tiny ~3-token call against the chosen model, persists only on success), DELETE (clear key).
- **`clipfarm/routes/tagging.py` + `routes/premade.py`** — both reload `Settings` at request time, dispatch to the chosen client. Anthropic path skips the Ollama precondition ping. 400 if provider=anthropic with no key.
- **Frontend `web/src/pages/Settings.tsx`** — provider radio, model dropdown with custom-value preservation, password-masked key input, Set+test / Set-without-test / Clear-key buttons, live "key is set" indicator.

**Tests added (26 new — 419 → 445 total passing):**

- `tests/test_settings.py` (6): round-trip, defaults on missing file, corrupt-file fallback, atomic write no leftover, parent-dir creation, on-disk plaintext contract.
- `tests/test_llm_anthropic.py` (8): happy path verifies system extraction + cache_control + tool_choice; failure paths (empty key, missing SDK, create raises, no tool_use block) return None; `ping_anthropic` polarity tests.
- `tests/test_routes_settings.py` (12): GET/PATCH/POST/DELETE coverage; raw key never in GET body; ping_anthropic mocked to avoid real network; 400 when provider=anthropic + no key.

**Reviewer's assessment summary (2026-05-26):**

> Implementation quality is at the same standard as Phase 6 / Phase 8. Tool-use translation correct, prompt caching wired right, storage separation architecturally sound, key never returned by GET, schema-versioned settings, atomic writes. 26 new tests covering the right failure modes. Process broke down in one specific way (spec wasn't updated for "no external services") — followed up with spec + CLAUDE.md edits in this same session so the spec-is-canonical invariant is restored.

**Spec / CLAUDE.md follow-ups landed alongside this entry:**

- `clipfarm-spec.md` → Stack Locked LLM bullet rewritten as "**Pluggable provider** — Ollama (default) or Anthropic API (opt-in)."
- `CLAUDE.md` → "No external services" line rewritten as "**Network footprint**: localhost-only by default; Anthropic API is the only opt-in network call when the user configures a key." Spec-is-canonical invariant restored.

**Polish landed in the same session (post-reviewer, four items):**

1. **`os.chmod(path, 0o600)` after the settings atomic-write.** Defensive against multi-user / Time Machine readability. POSIX-only; non-fatal on chmod failure (warning log). New test asserts the mode after save.
2. **`ping_anthropic` returns `(ok, error_message)`** instead of bare `bool`. Settings route includes the specific cause in the 400 detail so the UI shows "401 Authentication failed" or "model 'X' not found" instead of generic "test failed." `_extract_error_message` pulls SDK's `.message` attribute preferentially.
3. **"Set without test" tooltip** clarifies the tradeoff: errors surface only at first real call; typos won't be caught upfront.
4. **Progress panel surfaces active provider + model.** Both `tag_progress` and `premade_progress` now carry `{provider, model}`, rendered as a chip in `TagProgressPanel` / `PremadeProgressPanel` ("anthropic · claude-sonnet-4-6"). Answers "wait, is this the 5-min Ollama path or the 30s Sonnet path?" mid-run.

Test count after polish: 445 → 447.

**Still deferred (matches the Phase 9 stated plan):**

- Phase 9's cross-source preload fix carries forward to Phase 10 kickoff — separate concern; intentional.

**Files touched:**

```
NEW:
  clipfarm/settings.py
  clipfarm/llm_anthropic.py
  clipfarm/routes/settings.py
  tests/test_settings.py
  tests/test_llm_anthropic.py
  tests/test_routes_settings.py

MODIFIED:
  clipfarm/app.py            — include settings router
  clipfarm/routes/tagging.py — provider dispatch + Anthropic path
  clipfarm/routes/premade.py — same dispatch
  web/src/pages/Settings.tsx — full UI replaces placeholder
  pyproject.toml + uv.lock   — `anthropic` SDK dependency
  clipfarm-spec.md           — pluggable-provider language
  CLAUDE.md                  — network-footprint language
  COMPLETED_PHASES.md        — this entry
```

---

## Phase 9 — Live preview

**Verified by Lillian:** ✅ 2026-05-26 (reviewer: "ship it, with one real correctness/efficiency flag for Phase 10 kickoff").

**Built (2026-05-26):**

First time the assembled work plays back. Click any clip anywhere → preview pane appears bottom-right and plays the clip's range. Click an attempt → pane plays through every clip in sequence. Pane survives navigation across pages so playback isn't interrupted.

- **`clipfarm/resolver.py`** — pure `Attempt → list[ResolvedRange | TombstoneRange]`. Five-rule contract locked in module docstring (item order = AttemptClip order; tombstone = exactly one item; live clip = ≥1 ranges; trim offsets double-clamped — base by Phase 4, source-bounds here with warning logs; missing-transcript falls back to single un-expanded range with warning). `internal_pause_max_sec` semantic: **gap dropped entirely between sub-ranges**, not collapsed-to-max (plan-review #1, spec wording updated to match). **Shared with Phase 11 export** per the docstring so the trim + gap-drop + clamp rules live in one place.
- **`clipfarm/routes/resolver.py`** — `GET /api/attempts/{id}/resolved`. Adds `source_url` + `source_filename` server-side so the frontend doesn't have to join against `/api/state`. Pure read.
- **`clipfarm/routes/video.py`** — `GET /api/sources/{id}/video` with HTTP Range support. Locked range forms: `bytes=N-M` + `bytes=N-`; suffix `bytes=-N` and multi-range rejected with **416** (plan-review #3). 200/206/404/410/416. Content-Type derived from extension. 64KB chunked streaming. `Cache-Control: no-store` (dogfood-correct since source files can be replaced; the cache would mask that — worth revisiting in v1 because it disables browser seek optimization).
- **`web/src/playback/context.tsx`** — `PlaybackProvider` + `usePlayback()` hook. Queue state lives outside `<Routes>` so the `<video>` element survives nav. `playClip` / `playAttempt` / `pause` / `resume` / `dismiss` / `advance` / `seekToIndex`.
- **`web/src/playback/PreviewPane.tsx`** — floating bottom-right, default 480×270, drag-resizable from top-left corner (only growable corner since anchored BR). Min 320×180, max 80% of viewport. Size persisted to `localStorage["clipfarm.preview_pane_size"]`. Two alternating `<video>` elements (A/B) swap on `timeupdate`-vs-`effective_end` (50ms tolerance — native `ended` won't fire when we trim before file-end). `PRELOAD_AHEAD_SEC = 0.5` named constant with tuning comment (plan-review #4). Cross-source: hold-last-frame + `↻ Loading next clip…` overlay until new element fires `canplay`. Tombstone item: 2-second placeholder card then auto-advance. **Native `<video>` controls={false}** (plan-review #6) so the native scrubber can't seek out of the resolved range. Minimize-to-pill + dismiss controls.
- **SidePanel extraction (Phase 9 kickoff carry per Phase 8 advance note)** — `web/src/components/SidePanel.tsx`, 58 lines, shell only (plan-review #5): chrome + close-X + scrollable body. Page-specific bodies stay inline. Project / ScriptTOC / Attempts swap in.
- **Per-page playback wiring** — Project + ScriptTOC clicking a TakeCard calls `playClip`; Attempts clicking an AttemptCard calls `playAttempt`. App.tsx wraps `<Routes>` in `<PlaybackProvider>` + renders `<PreviewPane />` outside Routes.

**Tests added (28 new — 418 total passing, up from 390):**

- `tests/test_resolver.py` (14): single-clip no-trim, trim_start/trim_end, **negative effective_start clamped to 0**, **effective_end past source duration clamped**, **unknown source duration treated as infinity**, zero-duration after clamp raises, **dangling clip emits tombstone**, internal_pause no-gaps single range, internal_pause one gap >max splits in two, internal_pause gap exactly-at-max no split (strict `>` boundary locked), missing transcript with internal_pause set falls back to single range, multi-clip order preserved, unknown attempt raises KeyError.
- `tests/test_routes_resolver.py` (4): happy path with `source_url` + `source_filename` derivation, 404 unknown, tombstone in response, read-only (no snapshot side effect).
- `tests/test_routes_video.py` (10): 200 full + `Accept-Ranges`, 206 closed `bytes=N-M` with correct Content-Range and body, **206 open-ended `bytes=N-`** (the form browsers actually use), **416 suffix `bytes=-N` rejected** (plan-review #3 in code comment), **416 multi-range rejected**, 416 past EOF, 404 unknown, 410 unavailable, content-type for `.mp4` + `.mkv`.

**Decisions resolved during execution:**

- **Drag-resize handle on top-left corner only.** Pane is anchored bottom-right; the top-left is the only corner that growing makes sense from. Single tuning knob; sizes persisted to localStorage.
- **`v.currentSrc.split("/api/")[1]` source comparison** is fragile (works because `currentSrc` is empty on first load → `undefined !== ...` evaluates True → cross-source path fires, which is accidentally correct). Reviewer flagged for the Phase 10 cross-source preload fix; compare `source_id` directly then.
- **Word filter for `internal_pause_max_sec` expansion uses strict inclusion** (`w.start >= effective_start AND w.end <= effective_end`). Words that straddle the trim boundary don't participate in gap detection. Acceptable edge case for v0; flag for polish layer.

**Real-data smoke on btc.0.4 (2.77 GB file):**

```
HEAD /api/sources/4/video         → 405 (browsers GET with Range, never HEAD for <video>)
GET Range: bytes=0-1023           → 206  Content-Range: bytes 0-1023/2769128250  Content-Length: 1024
GET Range: bytes=1000000-         → 206  (open-ended form)
GET Range: bytes=-100             → 416  Content-Range: bytes */2769128250
```

**Reviewer assessment summary (2026-05-26, separate session):**

> Top-line: ship it, with one real correctness/efficiency flag for Phase 10 kickoff. All three required plan-review items landed cleanly with named tests + code comments. All four advisory items addressed. Test count tracks: 14 resolver + 9+ video + 4-5 resolver-route = ~28 new. The architecture matches the plan exactly — backend resolver shared with future Phase 11 export, Range-aware video streaming, two-`<video>` swap with timeupdate detection, SidePanel extracted as a thin shell. Real-data smoke on btc.0.4 single-source means cross-source UX isn't visually verified (documented as a blind spot).

**One real bug carry to Phase 10 kickoff (reviewer flag):**

- **Cross-source preload is wasted.** In `PreviewPane.tsx:181-201`, the time-update handler calls `setActiveIdx` only for same-source swaps; cross-source falls through to `advance()` only, which causes the active-ref effect to re-fetch the source on the active element while the hidden element's preloaded file gets thrown away. Net: cross-source pays full file-load latency every time. Hold-last-frame UX still works (because `v.load()` doesn't blank the display until `canplay`) but it's worse than designed. Fix is ~5 lines — always `setActiveIdx` on range-end + advance; the previously-active element stays in DOM holding its last frame. **First multi-source assembly will feel slower than necessary; btc.0.4 is single-source so this didn't surface.**

**Three smaller observations (non-blocking, polish layer):**

1. **`v.currentSrc.split("/api/")[1]`** source-URL comparison is fragile. Compare `source_id` directly during the Phase 10 cross-source preload fix.
2. **Word filter** `w.start >= effective_start AND w.end <= effective_end` excludes words straddling the trim boundary from gap detection. Edge case; v0 dogfood unlikely to hit it.
3. **`Cache-Control: no-store`** disables browser-side seek optimization. Correct for dogfood (files can be replaced); revisit for v1.

**Files touched in Phase 9:**

```
NEW:
  clipfarm/resolver.py
  clipfarm/routes/resolver.py
  clipfarm/routes/video.py
  tests/test_resolver.py
  tests/test_routes_resolver.py
  tests/test_routes_video.py
  web/src/components/SidePanel.tsx
  web/src/playback/context.tsx
  web/src/playback/PreviewPane.tsx

MODIFIED:
  clipfarm/app.py               — include resolver + video routers
  web/src/App.tsx               — wrap in PlaybackProvider + render PreviewPane outside Routes
  web/src/pages/Project.tsx     — CardSidePanel uses extracted SidePanel; playClip on card click
  web/src/pages/ScriptTOC.tsx   — same swap + playClip
  web/src/pages/Attempts.tsx    — AttemptSidePanel uses extracted SidePanel; playAttempt on click
  web/dist/...                  — rebuilt
```

---

## Phase 8.1 — Long-run progress UI

**Verified by Lillian:** ✅ 2026-05-26 (reviewer: "small, focused, well-engineered").

**Built (2026-05-26):**

Addresses Phase 6 open follow-up #1 — the "is this thing still alive?" problem Lillian hit during a 2026-05-26 tag run.

- **Backend single-slot pattern.** `app.state.tag_progress` + `app.state.premade_progress` initialized to `None` in lifespan. None = idle; populated dict = run-in-progress. Single slot per op type is correct for single-user v0 (the save lock already enforces one run at a time).
- **Orchestrator progress callback.** `tag_project` and `generate_premade_attempts` gain optional `progress: Callable[[dict], None] | None` parameter. Emits at known phase transitions:
  - Tagging: `preflight` → `batching` (per batch, with `current_batch` / `total_batches`) → `committing`.
  - Premade: `preflight` → `running_strategies` (per strategy, with `strategy_name`) → `naming` → `persisting`.
  - Callback exceptions are logged + swallowed via `_safe_progress` helpers — *"progress is observability, not correctness."*
- **Routes initialize + finalize the slot inside `try/finally`** so a crashing orchestrator can't leave pollers staring at a stale "running" state. The `write_progress` closure also tolerates the slot being wiped mid-call (defense-in-depth on the race window between orchestrator-emit and route-finalize).
- **New endpoints**: `GET /api/tag/progress` + `GET /api/premade/progress`. Cheap, no lock acquisition. Return `{running: false}` when idle or `{running: true, ...info}` when active.
- **Frontend**: new `web/src/components/RunProgress.tsx` — shared `useRunProgress(endpoint, active)` hook (polls every 2s while active; stops on flip-to-false) + `<TagProgressPanel>` + `<PremadeProgressPanel>`. Three uses (Brief, Attempts, Project) = real abstraction trigger.
- **UX details locked**: phase labels live in the frontend (machine keys in backend), ETA formula = `elapsed * (total - current) / current` and only shows for ≥10s estimates, color-coded progress bars (sky for tagging, violet for premade) so the two run types are visually distinct.

**Tests added (9 new — 390 total passing, up from 381):**

- `tests/test_routes_progress.py`: idle-shape for both endpoints; running-state visible to a concurrent reader (ThreadPoolExecutor race coverage matching Phase 6.1's pattern); slot cleared on orchestrator exception; per-batch + per-strategy callback emit sequence; swallowed-callback-exception path.
- Updated existing route test stubs in `tests/test_routes_tagging.py` + `tests/test_routes_premade.py` to accept the new `progress=None` kwarg.

**Files touched in Phase 8.1:**

```
NEW:
  clipfarm/routes/progress endpoints (in routes/tagging.py + routes/premade.py)
  tests/test_routes_progress.py
  web/src/components/RunProgress.tsx

MODIFIED:
  clipfarm/app.py          — app.state.tag_progress + premade_progress init
  clipfarm/tagging.py      — ProgressCallback + _safe_progress helper + emit points
  clipfarm/premade.py      — same pattern
  clipfarm/routes/tagging.py — try/finally slot management + GET /api/tag/progress
  clipfarm/routes/premade.py — same pattern for premade
  web/src/pages/Brief.tsx + Attempts.tsx + Project.tsx — wire useRunProgress
  tests/test_routes_tagging.py + test_routes_premade.py — accept progress=None kwarg
```

---

## Phase 8 — Premade attempts generation

**Verified by Lillian:** ✅ 2026-05-26 (reviewer approval: "ship it"; all 7 plan-review items landed; 3 advisory observations noted below for follow-up).

**Built (2026-05-25 → 2026-05-26):**

First write-side phase after Phase 6. Turns `clip_project_tags` rows into the named candidate attempts the spec calls out. **The moment a project goes from "a labeled library" to "candidate videos you can pick from."**

- **`clipfarm/strategies.py`** — eight pure-function strategies with a shared `_detect_takes` helper.
  - **Best plausible (5 strategies, `premade_bucket="best"`):** `best_per_line_in_script_order`, `longest_contiguous_take`, `near_one_take` (returns up to 3 SEPARATE attempts per the plan-review fix — each carries continuity ≈ 1.0), `shortest_complete`, `energy_shift` (words-per-second from Whisper timestamps; marked v0 heuristic in code).
  - **Diagnostic (3 strategies, `premade_bucket="diagnostic"`):** `started_with_line`, `skipped_line`, `ad_libbed`. Each capped at 3 results.
  - `_detect_takes` parameterized via `tolerated_inside` set: default `{fragment}` for clean-take strategies; `ad_libbed` uses `{fragment, standalone-idea, related-but-different}` so ad-libs land INSIDE takes rather than breaking them. Found mid-implementation when the first `ad_libbed` test failed — original design treated ad-libs as run-breakers, which meant `ad_libbed` could never find takes-with-ad-libs.
  - **Best-plausible ceiling: 7 attempts** (4 single-result strategies + up to 3 from `near_one_take`). Total ceiling: 16 (7 best + 9 diagnostic).
- **`clipfarm/continuity.py`** — `compute_continuity_score(state, attempt_clips)` walks the clip list, groups consecutive same-source forward-progressing clips into "runs," returns `max_run_runtime / total_runtime`. Explicit `ValueError` on empty/all-orphan/zero-runtime — defense-in-depth so a stale attempt can't crash the UI's score-bar rendering. Honors `trim_*_offset` even though Phase 8 doesn't populate them (Phase 10's edits don't need to rewrite the formula).
- **`clipfarm/attempt_naming.py`** — single batched LLM call for N attempts with per-attempt canned fallback. If the LLM fails entirely → all canned. If it returns SOME valid names but is missing or malformed for others → those individual rows fall back to canned. Overall `name_source` flag returned: `"llm"` / `"canned"` / `"mixed"`.
- **`clipfarm/premade.py`** — orchestrator. Pre-check (project exists, has on-script tags), runs every strategy, dedups across them by clip-list equality (first-by-strategy-order wins), computes continuity, batches into one LLM-naming call, optionally replaces `source="ai-premade"` attempts (hand-built + forks NEVER touched), allocates `_next_attempt_id` (monotonic-string-int matching `_next_source_id` / `_next_project_id`). Returns a `PremadeResult` with `generated_count`, `replaced_count`, `new_attempt_ids`, `naming_source`, `reason`, `mutated`.
- **`clipfarm/routes/premade.py`** — `POST /api/projects/{id}/premade-attempts`. Response shape includes `attempts: dict[id, Attempt]` so the frontend renders without a follow-up state fetch. **No 502 from this route** (canned-fallback removes the Ollama-required precondition; skipping the ping avoids tying up the save lock on a network roundtrip when we'd succeed anyway). All Phase 6.1 invariants carried forward: `dirty=True` before run, `asyncio.to_thread` wrap, `mutated`-gated commit, snapshot reason `"premade-attempts"`.
- **Frontend — `web/src/pages/Attempts.tsx` (new)**: two sections (Best plausible + Diagnostic), per-attempt card with name + source badge + continuity bar (green ≥ 0.8 / amber 0.4–0.8 / red < 0.4) + clip count + runtime, side panel with ordered clip list, regenerate confirmation modal with variable-count copy.
- **Frontend — `web/src/pages/Project.tsx` (touched)**: best-plausible-only compact summary panel above the Take Grid (diagnostic stays on `/attempts` per the exploration-vs-assembly split). "Generate premade attempts" CTA when none exist. Navigates to `/attempts` on success.
- **`web/src/App.tsx`** — `/attempts` route + new "Attempts" nav item between "Script" and "Brief".

**Tests added (59 new — 381 total passing, up from 322):**

- `tests/test_continuity.py` (9): formula edge cases — single-clip = 1.0, two-consecutive-same-source = 1.0, two-different-sources = max/total = 0.5, backward jump breaks run, three-clip composite, empty raises, zero-runtime raises, orphan handling, trim-offset shrinks runtime.
- `tests/test_strategies.py` (21): per-strategy isolation tests (3 each for the major strategies, 2 for the simpler ones) + cross-cutting "every strategy returns [] on empty state."
- `tests/test_attempt_naming.py` (9): empty input, llm-None path, happy LLM call (assert batched-not-sequential), partial success mixing LLM + canned per row, llm-returns-None falls back, malformed response falls back, too-few-names pads with canned, overly-long name truncates with ellipsis, llm-client raises falls back.
- `tests/test_premade.py` (11): happy path with bucket separation, canned vs LLM naming sources, replace-existing keeps hand-built/forks, replace_existing=False appends, unknown project + no-on-script raises, dedup, _next_attempt_id allocator (no collision across 2 runs, skips existing higher IDs), zero-result returns reason not exception.
- `tests/test_routes_premade.py` (9): happy paths (canned + LLM), 404 unknown, 400 no-on-script, 409 frozen, dirty=True precondition, mutated=False skips commit, mutated=True snapshots once, event-loop-responsive via ThreadPoolExecutor.

**Decisions locked + made during execution:**

- **`_detect_takes` tolerance set is parameterized**, not hardcoded. Found while implementing `ad_libbed` — the first test failure surfaced that ad-libs needed to land INSIDE takes, not act as run-breakers. The parameterization is cleaner than a separate detector function.
- **No 502 from the premade route.** Documented in the route docstring as a deliberate departure from Phase 6's pattern. Canned-fallback names mean Ollama being unreachable doesn't fail the run; the response's `naming_source` field lets the frontend surface "we used canned names because Ollama was down."
- **`_next_attempt_id` is monotonic over the FULL set of existing attempt IDs**, not just IDs we're about to keep. Comment: "so we don't reuse a freed slot mid-run and confuse anyone reading the snapshot trail." Important because regeneration deletes ai-premade and then adds new ones; reusing freed slots would make snapshot diffs confusing.
- **Best-plausible summary panel on Project page** stays best-only — diagnostic only appears on `/attempts`. Reviewer plan-review item #2 endorsed this as "clean primary surface."
- **`near_one_take` dedup**: when its top-1 result matches `longest_contiguous_take`'s clip list (single qualifying take case), dedup eliminates the duplicate. First-by-strategy-order wins; `longest_contiguous_take` runs first in `ALL_STRATEGIES`. Smoke run on btc.0.4 hit this case.

**Real-data smoke on btc.0.4** (synthetic 3-line project, 91 clips):

```
generated: 5
naming_source: canned (no LLM in smoke; live UI uses LLM)
mutated: True

#1  [best      ]  continuity=1.00   3 clips  -- best take of each line, in script order
#2  [best      ]  continuity=1.00  13 clips  -- the longest contiguous take
#3  [best      ]  continuity=1.00   3 clips  -- the shortest complete take of the full script
#4  [best      ]  continuity=1.00   3 clips  -- the take where the energy picked up
#5  [diagnostic]  continuity=1.00  30 clips  -- the take where you ad-libbed bonus material
```

5 of 8 strategies populated. `near_one_take` correctly dedup'd against `longest_contiguous_take` (same clip list). `started_with_line` / `skipped_line` produced 0 because the synthetic distribution has only one take. **Continuity = 1.0 across the board** because btc.0.4 is single-source — the cross-source/backward-jump score-tone visuals will only exercise on the first multi-source dogfood.

**Reviewer assessment summary (2026-05-26, separate session):**

> Top-line: ship it. All seven plan-review items landed correctly, Phase 6 invariants all carried forward, real-data smoke on btc.0.4 produced 5 attempts with sensible continuity behavior. Big phase (~3725 insertions across 14 files, 59 new tests, 381 total) but well-executed. The architecture follows the Phase 6/7 pattern cleanly — pure strategies + thin orchestrator + thin route. The diff is clean, the patterns are all proven, the plan-review items all landed.

**Three advisory items from the review (carried into Phase 9 / 8.1 follow-ups):**

1. **Continuity formula isn't visually stressed by single-source btc.0.4** — amber and red score-bar colors won't appear until a multi-source attempt exists. Same blind-spot as the cross-source preview latency from Phase 2. First multi-source dogfood is the real UX test.
2. **Visually confirm the "names are spec defaults" hint** surfaces when `naming_source="canned"` — stop Ollama, regenerate, look for the small hint in the run-summary banner. Visual surface only; the backend produces the signal correctly.
3. **`near_one_take` #1, #2, #3 collide on canned names** — all 3 canned-fallback names would be identical if Ollama is down AND there are 3 near-one-take results. LLM-path names differentiate via `name_hint` differences. Edge case (requires both 3 near-one-takes AND Ollama unreachable simultaneously); revisit if it manifests in real dogfood.

**Files touched in Phase 8:**

```
NEW:
  clipfarm/strategies.py
  clipfarm/continuity.py
  clipfarm/attempt_naming.py
  clipfarm/premade.py
  clipfarm/routes/premade.py
  tests/test_strategies.py
  tests/test_continuity.py
  tests/test_attempt_naming.py
  tests/test_premade.py
  tests/test_routes_premade.py
  web/src/pages/Attempts.tsx

MODIFIED:
  clipfarm/app.py           — include premade router
  web/src/App.tsx           — /attempts route + nav item
  web/src/pages/Project.tsx — best-plausible summary panel + Generate CTA
  web/dist/...              — rebuilt
```

---

## Phase 7b — Script TOC view

**Verified by Lillian:** ✅ 2026-05-25 (reviewer approval: "ship it"; no required changes, no advisory items).

**Built (2026-05-25):**

Frontend-only re-layout of Phase 7's data. Same `/api/projects/{id}/take-grid` endpoint powers both pages — Take Grid (Phase 7) for cross-line scanning across deliveries; Script TOC (this phase) for working one line top-to-bottom. Each script line becomes a collapsible `<details>` with its takes stacked vertically inside; same buckets-at-the-bottom shape; same side panel + Open-in-Library deep-link.

- **`web/src/pages/ScriptTOC.tsx`** — new page.
  - Vertical outline: line number (`01.`, `02.`, …) + name + tag-id + take-count badge. Empty rows de-emphasized (italic, neutral-500) but still visible so structural gaps surface.
  - Compact card per take (~280-char snippet visible without scrolling). Same category-badge palette as Phase 7.
  - All lines collapsed by default. State is component-local — reload = fresh-everything-collapsed.
  - Buckets section at the bottom, same 4-bucket shape as Phase 7.
  - Side panel sticky on the right; selecting a take shows full transcript + Open-in-Library `?source=&word=` deep-link.
- **`web/src/App.tsx`** — `/script` route + new "Script" nav item between "Project" and "Brief".

**Decisions locked with this phase:**

- **No "Pick this take" assembly button in v0.** Visible-but-disabled UI would be a nag without an attempt target; lands in Phase 8 when attempts exist.
- **Card + SidePanel deliberately duplicated from Project.tsx, not extracted.** Two implementations isn't an abstraction trigger (project rule: wait for the third). Phase 9's live-preview SidePanel rewrite is the natural extraction point.
- **Line numbering shown.** Anchors the user's mental model of script order.

**Tests:** zero new — `build_take_grid` is already exhaustively covered by Phase 7's 14 orchestrator tests + 7 route tests. Phase 7b is a re-layout of the same data with no new backend surface. **322 passing + 1 skipped** (the existing ffprobe skip), unchanged from Phase 7's settling-in commits.

**The three brief-parser polish commits (between Phase 7 and Phase 7b) are part of the same "Phase 7 settling in" story** — real friction from real dogfood paste, fixed in three rounds:

- **`d9b540a`** — natural-paragraph script blocks: column-0 dashes + blank lines between items + multi-line continuations now accepted via a loose-list rewrite fallback. Triggered by the `break-the-chrysalis` brief paste.
- **`ff2e133`** — leading preamble before `---` fence tolerated. Triggered by "New project" UI text getting captured above the frontmatter.
- **`8270041`** — `textwrap.dedent` pre-pass so uniformly-indented pastes (from markdown code blocks) work. Triggered by the corrected brief still failing because of a 2-space prefix on every line.

Each commit names its specific dogfood trigger so the audit trail is self-contained. The 12-test delta (310 → 322) is entirely from these polish commits — Phase 7b proper added zero new tests.

**Reviewer assessment summary (2026-05-25, separate session):**

> Top-line: ship it. Smallest phase yet, exactly as predicted. Frontend-only (ScriptTOC.tsx + nav route), zero backend changes, zero new tests because `build_take_grid` is the read surface from Phase 7. 322 passing + 1 skipped. The implementation matches the plan precisely — no surprises, no scope creep into 7b itself, no missing pieces. The "reuses Phase 7's endpoint" promise was kept. No required changes, no advisory items even.

**Files touched in Phase 7b:**

```
NEW:
  web/src/pages/ScriptTOC.tsx

MODIFIED:
  web/src/App.tsx              — /script route + nav item
  PHASES.md                    — Phase 7b marked verified
  COMPLETED_PHASES.md          — this entry
  web/dist/...                 — rebuilt
```

---

## Phase 7 — Take grid view (read-side of the project layer)

**Verified by Lillian:** ✅ 2026-05-25 (reviewer approval: "ship it"; three advisory items deferred to Phase 8 kickoff or never).

**Built (2026-05-25):**

The read-side counterpart to Phase 6. After tagging writes `clip_project_tags`, this phase makes those rows legible: each script line becomes a row of "take cards" with the tagged clips ranked best-match-first; four collapsible buckets carry the off-line categories. **This is the moment editing `btc.0.4` actually gets fast** — scan multiple deliveries of one line side-by-side.

- **Phase 7 kickoff cleanups (the Phase 6.1 carries):** all five items from the Phase 6 review fixed in this phase, with explicit tests for each.
  - **Bug #1 — dirty-flag race closed.** `app.state.dirty = True` now flips BEFORE `tag_project` runs (inside the `save_lock`-held block). An external `clipfarm.json` edit during the LLM run now routes to `on_conflict` (freeze + 409) instead of silent reload. Module docstring on `routes/tagging.py` documents the race in plain language so future-Claude doesn't accidentally revert it. **Two tests** cover this: a precondition spy (`dirty=True` at orchestrator entry) and a full race-coverage simulation (mid-batch watcher fires → `writes_frozen` flips → commit raises → HTTP 409).
  - **Bug #2 — event-loop responsiveness.** `result = await asyncio.to_thread(tag_project, ...)`. Save lock is still held across the await; mutation-under-lock invariant holds. **Test uses `concurrent.futures.ThreadPoolExecutor`** (not `httpx.AsyncClient`, per the reviewer's note that `ASGITransport` skips FastAPI lifespan and breaks tests that read `app.state` — Phase 2's review captured this). Asserts `GET /api/state` returns within 1s during a 2s mock LLM batch.
  - **Cosmetic — `mutated: bool` on `TaggingResult`.** Replaces the looser `clips_tagged > 0 or batches > 0` commit gate. Set True exactly when a row is appended OR stale rows are dropped pre-LLM. Two tests cover the truth table (rows appended → True; only stale dropped → True; neither → False; dry-run never sets it).
  - **Cosmetic — `section_tag_id` removed from `_validate_row` return.** Field was reserved-but-null per Phase 5's flat-lines simplification; extracting it into the validated row was a no-op vector for future bugs. Now: extracted from the LLM output (still in the JSON schema for completeness), then dropped before persistence.
  - **Cosmetic — stale-drop tradeoff documented in code.** Comment in `tagging.py` near the pre-LLM stale-row drop explains the "stale rows gone if every batch fails" tradeoff (deliberate, accepted; user retries `Tag clips` which tags-from-scratch the affected clips).

- **`clipfarm/take_grid.py`** — pure orchestrator.
  - `build_take_grid(state, project_id) -> TakeGridView`. Pydantic models for the response shape (`TakeCard`, `LineRow`, `BucketView`, `TakeGridSummary`).
  - **Sort order locked**: line cards by `confidence DESC, start_sec ASC`; bucket cards by `start_sec ASC` only (buckets aren't ranked against a specific match target).
  - **Card payload**: `clip_id`, `source_id`, `filename` (resolved server-side from `state.sources`), `start_sec`, `end_sec`, `transcript_text`, `category`, `confidence`, `project_tag_id`, `stale`, `first_word_index` (index into the source's flattened Whisper word list — computed via the existing `transcripts.load_transcript_for_source` cache so it's nearly free on warm cache, one read per source on cold cache).
  - **Empty lines preserved as rows**: every `ProjectTag(kind="line")` appears in `lines[]` even with zero matched cards. The user sees "line 4 has zero matches" as a visible gap, not silent absence.
  - **Summary counters** with explicit semantics in code comments: `untagged_clips` (clips with NO rows for this project), `stale_clips` (clips with at least one stale row), `total_tagged` (clips with at least one row). Disjoint is **not** required — a clip with one stale + one fresh row counts toward both `stale_clips` AND `total_tagged`.
  - **Defensive: `clip is None` skip + `on-script` with `project_tag_id=None` drop** — both unreachable in well-formed state today but cheap defense against future bugs in the propagation/delete paths.
  - Pure: reads state + the Whisper sidecar cache, returns a fresh view. No mutations.

- **`clipfarm/routes/take_grid.py`** — `GET /api/projects/{project_id}/take-grid`.
  - 404 unknown project. No mutations, no snapshot, no save-lock acquisition.
  - Read-only invariant tested explicitly: snapshot directory size unchanged across 3 calls; `app.state.dirty` not flipped.

- **Frontend (`web/src/pages/Project.tsx`)** — first real implementation, replaces the placeholder.
  - Project picker (dropdown if multiple, label if one). Summary chips: `X tagged`, `Y untagged` (amber if >0), `Z stale` (amber if >0). "Tag clips →" link back to the Brief page.
  - **Line rows**: each script line is a row with a horizontally-scrolling strip of cards underneath. Card: 220px wide, color-coded category badge, confidence %, filename (mono, truncated), timestamp range, 3-line transcript snippet. Selected card gets a ring outline.
  - **Stale cards** show an amber dot indicator in the top-right corner of the card with a tooltip explaining "brief changed after this tag was written; re-tag to refresh."
  - **Four collapsible buckets** below the lines: related-but-different + standalone-idea open by default (high-signal), off-topic + fragment collapsed (low-signal noise).
  - **Side panel on the right (sticky)**: clicking a card slides in the full transcript, source filename + timestamp range, category + confidence chip, and an "Open in Library" button that navigates to `/library?source=<id>&word=<first_word_index>`. Phase 9's live-preview pane will slot into this same panel.
  - **Library route updated** to read `?source=&word=` query params on mount and trigger the existing `focusRequest` mechanism. Params are cleared after consumption so later in-page nav isn't undone by a stale URL.
  - **Empty states**: no projects → link to Brief; project with no tags → prompt to Tag clips.

- **Tests added (31 new — 310 total passing, up from 279):**
  - `tests/test_take_grid.py` (14): empty project, unknown-project KeyError, on-script grouping, line-card sort (confidence DESC then start ASC), bucket-card sort (start ASC only, confidence ignored), all four buckets populate, `order_idx` row ordering, summary math (3-way disjoint not required), stale surfacing-not-filtering, multi-project isolation, filename resolution, `first_word_index=None` when source has no transcript, orphan tag-row defensive skip.
  - `tests/test_routes_take_grid.py` (7): happy path with real sidecars, 404 unknown project, no-snapshot-side-effect (3 calls, snapshot delta = 0), no-dirty-flag-side-effect, filename + `first_word_index=0` on first clip, `first_word_index=2` on second clip (word-list offset math), summary numbers match the live state.
  - `tests/test_routes_tagging.py` enhancements (6): bug #1 precondition + bug #1 race coverage, bug #2 thread-based responsiveness, `mutated=False` skips commit, `mutated=True` triggers commit.
  - `tests/test_tagging.py` enhancements (5): mutated truth table (rows-appended, only-stale-dropped, no-changes, dry-run-never-sets) + `section_tag_id` not persisted on the written row.

**Decisions locked with this phase (carried from PHASES.md plan + the reviewer's plan-review folds):**

- **Related-but-different lives in a top-level bucket, not under each script line.** The LLM doesn't surface a line-association signal for these (`line_tag_id=null`), so there's no per-line data to attach. Open by default.
- **Confidence DESC then start ASC inside line rows; start ASC only inside buckets.** Best match first when a line target exists; recording order when it doesn't.
- **Side panel for card detail in v0** (not modal, not inline expand). Phase 9's preview pane reuses the same slot — no UI rebuild.
- **Stale: surface, don't filter.** Amber dot indicator on the card, full disambiguation via the existing Brief-page "Tag clips" button.
- **Filename resolution server-side per card.** Saves an N+1 frontend lookup.
- **`first_word_index` computed server-side per card.** Per the reviewer's plan-review fold — frontend gets a ready-to-use `/library?source=&word=` link with zero transcript walking on the client.
- **Untagged counted at the source-clip level**: a clip with NO rows for this project counts once. Stale-only counts toward `stale_clips`. Non-disjoint is intentional and documented in code.

**Real-data smoke test (synthetic project on the live `clipfarm.json`, btc.0.4's 91 clips):**

- 3-line script + simulated Phase-6-style tag distribution (4 + 5 + 4 on-script, 10 related, 7 standalone, 30 off-topic, 17 fragment).
- `build_take_grid` returned: 3 line rows (4/5/4 cards), 4 buckets (10/7/30/17), summary `tagged=77, untagged=80, stale=0`.
- First card sanity-check: `filename="btc.0.4.mov"`, `first_word_index=0`, `start_sec=4.37` (matches the btc.0.4 transcript opening "She makes me smile all the time…"). Transcript text loaded from the real Whisper sidecar via the cache, not synthesized.
- Live `GET /api/projects/missing/take-grid` against the running server returned `{"detail":"unknown project_id: missing"}` with status 404, confirming the route is wired through `app.include_router`.

**Reviewer assessment summary (2026-05-25, separate session):**

> Top-line: ship it. Both Phase 6.1 bugs fixed correctly with the right tests, the take-grid orchestrator is clean and well-documented, sort orders + counter semantics match the plan, frontend surfaces match the visual spec. 310 tests passing (~32 new). The diff is exactly the size and shape Phase 7 should be. No required pre-commit changes.

Three advisory items captured for later (none blocking):

1. **`untagged_clips` UI semantics** — counter includes clips from sources unrelated to the project's current focus. For a single-source project (Lillian editing btc.0.4 only), `untagged=80` includes ~66 clips from other 05.19.26 sources. The number is correct per the multi-project spec ("re-mine the library when a new brief lands") but the chip could read confusingly. Phase 8+ polish: tooltip ("across your full library") or a "scope to source(s)" filter.
2. **Stale dot visual confirmation** — smoke test had `stale=0`, so the amber dot UX hasn't been visually exercised. Worth a hand-edit during the next dogfood pass: flip `clip_project_tags[0].stale = true` in `clipfarm.json`, reload, confirm the dot renders on the right card.
3. **COMPLETED_PHASES.md cross-reference** — added a one-line pointer in the Phase 6 entry to the Phase 7 carry fixes (applied this commit).

**Files touched in Phase 7:**

```
NEW:
  clipfarm/take_grid.py
  clipfarm/routes/take_grid.py
  tests/test_take_grid.py
  tests/test_routes_take_grid.py

MODIFIED:
  clipfarm/routes/tagging.py   — Phase 6.1 bug #1 (dirty before run) + bug #2 (asyncio.to_thread) + mutated-gated commit + docstring
  clipfarm/tagging.py          — mutated field on TaggingResult + section_tag_id drop + stale-tradeoff comment + result.mutated set at the two mutation points
  clipfarm/app.py              — include take_grid router
  web/src/pages/Project.tsx    — full Take Grid implementation (replaces placeholder)
  web/src/pages/Library.tsx    — useSearchParams hook for ?source=&word= deep-link
  web/dist/...                 — rebuilt
  tests/test_routes_tagging.py — 6.1 bug regression tests
  tests/test_tagging.py        — mutated truth table + section_tag_id-not-persisted
  PHASES.md                    — Phase 6 marked verified with 6.1 carry note; Phase 7 plan landed (with three reviewer plan-review folds applied)
  COMPLETED_PHASES.md          — Phase 6 6.1-carries cross-reference + this Phase 7 entry
```

---

## Phase 6 — Ollama tagging (batched)

**Verified by Lillian:** ✅ 2026-05-25 (with two real bugs flagged for Phase 7 kickoff — see below)

**Bugs flagged by the reviewer for Phase 7 kickoff (carries):**

1. **Race between mutation and `dirty=True` flag.** The orchestrator drops stale rows at the start of `tag_project` (before any LLM call), but `app.state.dirty = True` is set AFTER `tag_project` returns. During the 5.5-min LLM run, the watcher's `has_unsaved_changes()` reads `dirty=False`; an external `clipfarm.json` edit routes to `on_external_change` (silent reload) instead of `on_conflict` (freeze). Reload reassigns `app.state.clipfarm`; the orchestrator's local `state` variable points at the abandoned old object and keeps mutating it. At commit time, `commit_state_with_snapshot_locked` writes the NEW (reloaded) state, not the mutated one. Result: response says "tagged 77 clips" but disk has zero new tags. Fix: set `app.state.dirty = True` BEFORE the orchestrator call (3-line change). Not reachable under v0 single-user single-tab usage but real risk if the user hand-edits during a run.
2. **Synchronous `tag_project` blocks the event loop for 5.5 min.** `chat_with_json_schema` uses sync `httpx.post`; the route handler `await`s the orchestrator but the orchestrator never yields. Whole server becomes unresponsive — `GET /api/state` hangs from a second tab. Fix: `result = await asyncio.to_thread(tag_project, ...)`. The `asyncio.Lock` is still held by the route, so mutation-under-lock holds. Two-line change.
3. **Commit-when-nothing-mutated (cosmetic).** `result.clips_tagged > 0 or result.batches > 0` triggers commit even when all batches fail validation and no stale rows existed to drop. State is unchanged but we still snapshot + write. Tighten the condition to a `mutated: bool` field on `TaggingResult`.
4. **Dead `section_tag_id` extraction (cosmetic).** `_validate_row` extracts `section_tag_id` but the field isn't on `ClipProjectTag`. Remove from the return dict.
5. **Pre-LLM stale-drop tradeoff undocumented (cosmetic).** If every batch fails after stale rows have been dropped, the previously-tagged-but-stale data is gone (user recovers via snapshot revert). Add a code comment.

All five ride along on the Phase 7 kickoff cleanup pass.

**Phase 6.1 carries — landed in Phase 7 kickoff (2026-05-25):** all five items above (bug #1 dirty-flag ordering, bug #2 `asyncio.to_thread` wrap, cosmetic mutated flag, cosmetic `section_tag_id` drop, cosmetic comment) were fixed alongside the Phase 7 read-side build. See the Phase 7 entry below for the specific commits + tests. Phase 6 standalone is now considered fully closed.

**Built (2026-05-25):**

The first phase where ClipFarm actually uses Llama 3.1 8B. A brief + ingested footage + one button → tagged library. Phase 7's take grid reads these rows to build the per-line columns.

- **Phase 6 kickoff cleanups (the Phase 4 architectural carries + the Phase 5 review residue):**
  - **`serialize_state` moved inside the save lock** in both `save_state` and `save_state_with_snapshot`. Closes the Phase 4 race where two concurrent route handlers could each capture a serialized form before the other wrote, then race on which version landed on disk.
  - **`commit_state_to_disk_locked(app)` and `commit_state_with_snapshot_locked(app, reason)` added** — caller-already-holds-lock variants. **All six mutating routes migrated to the new one-critical-section-per-op pattern**: Phase 2 ingest, Phase 4 clips (split / merge / adjust / create / delete), Phase 5 projects (create / update / delete). Each now does `async with save_lock: { mutate; <locked commit> }`. The lock-held-during-orchestrator tests carry over; new tests assert the commit also runs inside the same lock scope.
  - **`ClipProjectTag` uniqueness validator activated** (the Phase 1 stub). Duplicate `(clip_id, project_id, project_tag_id, category)` is rejected at construction time + on load. Same `project_tag_id` with a different `category` is allowed (a clip can be `on-script` AND `standalone-idea` for the same line tag if the LLM categorized it both ways). 7 new tests in `tests/test_uniqueness_validator.py`.
  - **`_project_detail` section sort O(N²) → O(N)** via a precomputed `name → order_idx` lookup.
  - **`_full_brief_md` reconstruction branch documented** with a one-line comment ("reachable only via direct programmatic API").

- **`clipfarm/llm.py`** — thin Ollama HTTP client.
  - `chat_with_json_schema(messages, schema, ...)` posts to `/api/chat` with the JSON-schema `format` field, returns parsed dict or `None` on any failure (HTTP error, malformed JSON, content not JSON-parseable, connection refused, timeout). Never raises — the orchestrator handles `None` as "this batch failed."
  - `ping_ollama(timeout=3.0)` does a cheap `GET /api/tags` reachability check. The tagging route uses this as the precondition before kicking off a long run, so the user gets a 502 immediately on a down LLM instead of waiting through 15 retries.
  - `OLLAMA_HOST` env var override. Temperature locked at 0.2 for tagging — consistent categorization, not creative writing. Streaming off (one JSON response, simpler parse path).

- **`clipfarm/tagging.py`** — pure orchestrator.
  - `tag_project(state, project_id, *, llm_client, batch_size, dry_run) -> TaggingResult`. Pre-flight: KeyError on unknown project, ValueError on empty brief (no script, no sections, no tags — the route translates to 400).
  - Builds the candidate list: every clip without a row for this project, OR every clip with at least one `stale=True` row. Stale rows for candidates are pre-dropped so the fresh write replaces them cleanly.
  - System prompt built per project: name, "what's good" body, script-line IDs, section IDs, ad-hoc tag IDs, plus the category enum. User prompt per batch: a JSON-ish "Tag these clips" block. Output: `{results: [{clip_id, line_tag_id, section_tag_id, category, confidence}, ...]}` with the JSON schema enforced in the request `format`.
  - **All seven LLM-output validation rules from the plan landed**: unknown `clip_id` (drop), unknown `line_tag_id` (drop), invalid `category` (drop), missing required field (drop), out-of-range `confidence` (clamp to [0, 1] + log, keep row), batch-size mismatch (clip-ID reconstruction — partial wins are real), non-dict row (drop). All logged with the row's `clip_id` named.
  - **Retry policy**: once per batch on `None` from the LLM client OR empty post-validation row set. On second failure, batch lands in `untagged_batches` with the reason + a 300-char raw excerpt. Run continues to the next batch.
  - **Cross-project isolation tested**: tagging project A doesn't touch project B's rows. This is the spec's "multi-project tagging is the engine" principle becoming a verified invariant.

- **`clipfarm/routes/tagging.py`** — `POST /api/projects/{project_id}/tag`.
  - Query params: `batch_size` (default 10, range 1-30), `dry_run` (default false).
  - Pre-flight error mapping: 404 unknown, 400 empty brief, 409 frozen, 502 Ollama unreachable (`ping_ollama` runs before the lock acquire so a down LLM doesn't tie up the save lock).
  - Holds `app.state.save_lock` across the entire batched run + the locked commit. One snapshot per `/tag` call, reason `tag-clips`.
  - **Documented v0 deliberate choices** in the module docstring + route comments: lock-held-for-the-duration blocks every other mutating route during a tag run; freeze-during-tagging loses the in-memory results (correct behavior, user retries after resolving); synchronous architecture fits ~20s expected runtime, polling/SSE land later if a project ever needs minutes.

- **Frontend (`web/src/pages/Brief.tsx`):** "Tag N clips" button next to Save/Delete on the Brief page.
  - Counts untagged + stale clips for the selected project, shows the number in the button label.
  - Disabled when the brief is dirty (save first), when there are 0 clips to tag, or while a tag run is in progress.
  - On success: emerald result banner showing `Tagged N clips in M batches · 30s · K already tagged · L rows dropped`, with collapsible details if any batches failed.
  - On 502: clear "Ollama is unreachable. Is `brew services start ollama` running?" error message.
  - On 409 / 400 / 404: surface the server's detail verbatim.

- **Tests added (48 new — 280 total passing, up from 232):**
  - `tests/test_uniqueness_validator.py` (7): activation tests.
  - `tests/test_llm.py` (12): canned-httpx tests for every failure mode (HTTP 500, connection error, timeout, malformed wrapper JSON, missing message.content, content-not-JSON, `OLLAMA_HOST` override) + happy path + the `ping_ollama` polarity tests.
  - `tests/test_tagging.py` (19): happy path, batch splitting, idempotency, stale-flagged re-tag, no-op when all tagged, **cross-project isolation**, every validation rule (unknown line_tag_id / invalid category / missing field / clamp confidence / hallucinated clip_id / batch-size mismatch keeps partial), retry-on-empty-then-clean, retry-failure-buckets-batch-continues, llm-returns-None buckets-after-retry, empty-brief raises, unknown-project raises, dry_run-writes-no-rows, batch-size-out-of-range raises.
  - `tests/test_routes_tagging.py` (10): happy path through TestClient with a mocked LLM client + ping; 404 unknown project; 400 empty brief; 409 freeze; 502 Ollama unreachable; lock-held-during-orchestrator; **commit-inside-same-lock-scope assertion (the new Phase 6 invariant)**; idempotency over the route; dry_run writes nothing + no snapshot; snapshot-once-per-call.

**Manual verification run (all green) — real Ollama call against btc.0.4:**

- Ingested just `btc.0.4.mov` + sidecar into a clean state: 1 source, 91 clips.
- Created the project from a real brief (3 script lines, 3 sections, 2 ad-hoc tags).
- `POST /api/projects/1/tag?batch_size=10` against real Ollama running locally — `llama3.1:8b` (4.7GB, Q4_K_M):
  - 10 batches dispatched.
  - **77 clips tagged on the first pass** (85% coverage). 14 rows dropped to validation (LLM hallucinated tag IDs or returned invalid categories).
  - 0 untagged batches — every batch had at least one valid row.
  - Total runtime: **5.5 minutes** for the full 91 clips. **Significantly slower than the spec's ~20-second estimate** — flag for the open-follow-ups list below. The model is slow on this machine; per-batch latency ~30s.
- **Second call (idempotent retry)**: 77 clips skipped (already tagged + non-stale), 14 clips re-attempted (the previously-dropped ones, which look untagged at row-level), 10 more tagged + 4 dropped. Total coverage 87/91 = 95.6%.
- **Category distribution on first-pass tags:**
  - `off-topic`: 30 (Lillian's birthday / cat / chatter)
  - `fragment`: 17 (false starts, single-word noises)
  - `on-script`: 13 (matched a script line)
  - `related-but-different`: 10 (Bitcoin-adjacent insights not in the script)
  - `standalone-idea`: 7 (could be its own short)
- **Five-clip sample (sanity-check the qualitative output):**
  - `"turned 23 last week"` → **off-topic** ✓ (her birthday, not Bitcoin)
  - `"can't multiply your way out of zero going from zero to one..."` → **related-but-different** ✓ (Bitcoin-adjacent insight)
  - `"It's okay to appreciate where you have been..."` → **on-script** ✓
  - `"I'm calling it break the chrysalis..."` → **on-script** ✓
  - `"Okay, the log 15 minutes I'm"` → **fragment** ✓ (false start)

The categorization is qualitatively correct on this sample. Lillian should spot-check ~20 more on her own dogfood pass before judging quality.

**Assumptions made + deviations from the plan:**

- **Llama 3.1 8B is much slower than the spec estimated.** Plan: ~20s for 150 clips. Actual: 5.5 minutes for 91 clips (≈3.6s per clip). The model is doing 10 clips per batch × 30s/batch ≈ 30 inference steps. Three possible causes:
  - The machine's M-chip is running the 4-bit quant on CPU (Ollama's default), not Metal.
  - Prompt is longer than estimated — each batch carries the full brief + 10 clip transcripts.
  - Per-token generation is slower at the prompt's depth than at shallow benchmarks.
  - **Action**: leave as-is for v0 — the run completes, output is correct. Worth checking `ollama ps` to confirm Metal is in use; if not, that's a 5-10× speedup left on the table. Flag for the polish layer.
- **Confidence is a useless signal in practice (so far).** The 5-clip sample shows the model returning `1.00` for "on-script" matches and `0.00` for everything off-topic — basically binary. The spec's "surface visually in the take grid" UX still works, but the slider expectation (mid-confidence cases worth manual review) won't materialize until the LLM matures. v0 ships it; Phase 7's UI can decide how loud to make it.
- **`section_tag_id` left null even when the LLM tried to fill it.** Several rows came back with `section_tag_id` set, but Phase 5's flat-lines simplification means we ignore it. The data stays null on disk (the orchestrator drops `row.get("section_tag_id")` into the model without re-validation). Phase 7+ activates the field once the brief format gains section→line hierarchy.

**Open follow-ups for the reviewer to evaluate:**

1. **Per-clip-batch progress UI** — at 5.5 min for 91 clips, the synchronous spinner is genuinely uncomfortable. The plan called this out as "Phase 9+ swap to background tasks if needed." Real-world runtime suggests Lillian will hit this discomfort on dogfood. Worth considering whether Phase 6.1 adds an SSE progress stream (cheap; the orchestrator already runs in a loop where we can yield events).
2. **Ollama Metal acceleration check.** `ollama ps` should report Metal usage; if it's CPU-only, that's the easy speed win. Worth a sanity check before Phase 7.
3. **`brew services start ollama` vs `ollama serve` quirks.** Service mode worked but the runtime suggests it may not be using all available resources. Document the recommended start-up incantation in CLAUDE.md / README once we have the right answer.

**Files touched in Phase 6:**

```
NEW:
  clipfarm/llm.py
  clipfarm/tagging.py
  clipfarm/routes/tagging.py
  tests/test_llm.py
  tests/test_tagging.py
  tests/test_routes_tagging.py
  tests/test_uniqueness_validator.py

MODIFIED:
  clipfarm/models.py        — uniqueness validator activated (Phase 1 stub)
  clipfarm/store.py         — serialize_state inside lock; new save_state_locked + save_state_with_snapshot_locked
  clipfarm/routes/deps.py   — new commit_state_to_disk_locked + commit_state_with_snapshot_locked
  clipfarm/routes/ingest.py — migrated to single-critical-section pattern
  clipfarm/routes/clips.py  — five routes migrated
  clipfarm/routes/projects.py — three mutating routes migrated + O(N²) section sort fix
  clipfarm/projects.py      — _full_brief_md reconstruction-branch comment
  clipfarm/app.py           — include tagging router
  pyproject.toml + uv.lock  — httpx now a main dep (was dev-only)
  web/src/pages/Brief.tsx   — Tag N clips button + result/error display
  web/dist/...              — rebuilt
  PHASES.md                 — Phase 5 marked verified, Phase 6 plan landed (with seven plan-review fixes folded in)
  COMPLETED_PHASES.md       — Phase 5 verified stamp + this Phase 6 entry
```

---

## Phase 5 — Brief editor + project creation

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

The write side of the project layer. Markdown briefs become typed `Project` records with `Script`, `ProjectTag` (sections + lines + ad-hoc tags) structures ready for Phase 6 to write `clip_project_tags` against. Phase 5 doesn't tag anything itself — it builds the structure tagging will populate.

- **Phase 5 kickoff cleanup:**
  - **`_log_unknown_keys` refactor.** Replaced the `_looks_like_dict_of_model` heuristic with deterministic `typing.get_origin` + `typing.get_args` annotation inspection. The new `_walk_annotation` dispatches: direct `BaseModel` → recurse; `Optional[X]` → unwrap None + re-dispatch; `list[X]` / `tuple[X]` → walk elements; `dict[K, X]` → walk values; anything else → stop. No more guessing. Four new test cases (`dict[str, ProjectTag]`, `dict[str, Project]`, `Optional[Script]`, `list[ClipProjectTag]`) prove the dotted paths come out right.

- **Model changes:**
  - New `Script(StrictModel)` with `lines: list[str]`. The advance note from Phase 1.
  - Replaced `Project.script_json: dict` with `Project.script: Optional[Script] = None`.
  - Expanded `TagKind` to `Literal["section", "line", "tag"]`. The third kind distinguishes ad-hoc tags from script lines (both used to be `kind="line"` with `parent_id=None` and were indistinguishable, which broke the name-keyed merge).
  - `Project.name` now `min_length=1` (empty strings rejected at the Pydantic boundary).

- **`clipfarm/brief.py`** — pure YAML-frontmatter + markdown-body parser.
  - `parse_brief(text) -> ParsedBrief` (the latter a Pydantic model with `name`, `script`, `sections`, `tags`, `body_md`).
  - `BriefParseError` carries `line` / `column` when PyYAML exposes them. Routes pass these through to the user verbatim in the 400 detail.
  - **Locked policies (matching the plan):** `name` required + non-empty + must be a string; body-only briefs rejected ("not yet a project"); duplicate script lines tolerated (`order_idx` differentiates them); YAML escaping documented in the editor's inline help.

- **`clipfarm/projects.py`** — pure orchestration with the **name-keyed merge**:
  - `create_project(state, parsed, brief_md_source=...) -> project_id` — allocates monotonic string-int IDs, builds Project + ProjectTag entries.
  - `update_project(state, project_id, parsed, brief_md_source=...) -> stale_count` — uses the merge so reorders preserve ProjectTag IDs (clip refs survive), renames create new IDs (old ones become dangling tombstones with `stale: true` on every `clip_project_tags` row). Flips every `clip_project_tags` row for this project to `stale: true` regardless (spec's explicit-retag rule).
  - `delete_project(state, project_id) -> (dropped_tag_rows, deleted_attempts)` — drops the project + its `ProjectTag` dict + every `clip_project_tags` row + hard-deletes its attempts. Forward-compatible response shape even when both counts are 0 today.
  - `list_projects(state)` returns summaries (project_id, name, created_at, line_count, section_count, tag_count).
  - **Identity rules:** sections = `name`; lines = `(parent_section_name, line_text, occurrence_index_in_section)`; ad-hoc tags = `name` (kind="tag"). Each rule has a dedicated test.

- **`clipfarm/routes/projects.py`** — 5 mutating routes + 1 read-only parse-preview:
  - `POST /api/projects/parse` body `{brief_md}` → `{name, lines_count, sections, tags}`. **Read-only, no lock, no snapshot.** Used by the frontend's debounced live preview.
  - `POST /api/projects` body `{brief_md}` → `{project_id, snapshot}`. 400 on parse error (with line/column when available). Snapshot reason `"create-project"`.
  - `GET /api/projects` → list of summaries.
  - `GET /api/projects/{id}` → full detail (name, brief_md, script_lines, sections, tags). 404 unknown.
  - `PATCH /api/projects/{id}` body `{brief_md}` → `{project_id, stale_tag_rows, snapshot}`. Snapshot reason `"edit-brief"`.
  - `DELETE /api/projects/{id}` → `{project_id, dropped_tag_rows, deleted_attempts, snapshot}`. Snapshot reason `"delete-project"`.
  - Every mutating route holds `app.state.save_lock` across the orchestrator (Phase 2.1 pattern), routes through `commit_state_with_snapshot` (Phase 4 invariant).

- **Frontend (`web/src/pages/Brief.tsx`)** — first real impl:
  - Left rail: project list + "New project" button.
  - Main panel: textarea with the brief markdown, Save / Delete buttons, live parse preview (debounced 200ms, calls `/api/projects/parse`), inline `Brief format help` `<details>` block with the YAML escape tip + an example brief.
  - Save button calls `POST` (new) or `PATCH` (existing); 400 errors surface inline with the parser's line/column.
  - Delete button opens an always-confirm dialog ("you can restore from `.clipfarm/snapshots/`").
  - Unsaved-changes indicator (`• unsaved`) when `draftBrief !== originalBrief`.

- **Tests added (50 new — 232 total passing, up from 182):**
  - `tests/test_brief.py` (16): full brief, minimal brief, body-only rejected, missing name raises, empty/whitespace/non-string name raises, malformed YAML raises with position, non-list script/sections/tags rejected, non-string entries rejected, duplicate lines preserved, name strips whitespace, body preserves markdown, quoted special chars (the YAML escape test).
  - `tests/test_projects.py` (14): monotonic IDs, section/line/tag entries, brief_md source storage, duplicate lines get separate tags, **reorder preserves IDs** (the name-keyed merge test), **rename creates new tag ID** (with old becoming dangling tombstone), `update_project` flips every `clip_project_tags` row to stale, dangling-tombstone behavior on rename, unknown project_id raises, delete removes project + tag rows + attempts (synthetic data covers all three propagation paths), list_projects shape.
  - `tests/test_routes_projects.py` (16): each route's happy path through `_count_snapshots_after_op` (where applicable — parse preview is asserted NOT to snapshot), 400s on malformed brief, 404s on unknown ID, 409 when frozen, lock-held assertion, snapshot-count-equals-op-count (after seed: 3 mutating ops → 3 snapshots).
  - `tests/test_load_unknown_keys.py` (4 new): unknown key inside `dict[str, ProjectTag]` value → `projects.1.tags.1._secret` path; unknown key inside `dict[str, Project]` value → `projects.1._secret`; unknown key inside `Optional[Script]` → `projects.1.script._secret`; unknown key inside `list[ClipProjectTag]` → `clip_project_tags.[0]._secret`. The refactored walker handles each shape deterministically.

**Manual verification run (all green):**

- `POST /api/projects/parse` with the full brief → 200 with `{name: "btc explainer v0.4", lines_count: 2, sections: ["the hook"], tags: ["hook"]}`. Confirms the read-only preview works.
- `POST /api/projects` (first call, fresh state) → 200 with `{project_id: "1", snapshot: null}`. **Null snapshot is correct** — the Phase 1 invariant is "can't snapshot what doesn't exist on disk yet"; the create itself writes the state file, which subsequent mutations then snapshot.
- `GET /api/projects` → list shape correct: `line_count: 2, section_count: 1, tag_count: 4` (2 lines + 1 section + 1 ad-hoc tag).
- `GET /api/projects/1` → full detail returns the original markdown verbatim in `brief_md`, with parsed `script_lines`, `sections`, `tags` arrays.
- `PATCH /api/projects/1` with a brief that renamed `the hook` → `the opening` AND added a third line "close" → 200 with `snapshot: "...__edit-brief.json"`. After PATCH, `GET /api/projects/1` shows the renamed section and added line.
- `POST /api/projects` with no frontmatter → 400.
- `POST /api/projects` with frontmatter missing `name` → 400 with structured detail: `{"error": "'name' is required in the frontmatter — projects without names aren't valid"}`.
- `DELETE /api/projects/1` → 200 with `snapshot: "...__delete-project.json"`. Final snapshot inventory: `1 edit-brief.json | 1 delete-project.json` — exactly 2 snapshots for 2 mutating ops on existing-file state (the first create wrote 0 because the state file didn't exist beforehand).

**Assumptions made + deviations from the plan:**

- **Third TagKind ("tag") added during execution.** The plan only had "section" and "line"; mid-execution I hit the merge ambiguity where ad-hoc tags and script lines both came out as `kind="line"` with `parent_id=None`, making them indistinguishable in the existing-tag-lookup step. Added a third kind "tag" specifically for ad-hoc project labels. Updated `TagKind = Literal["section", "line", "tag"]` and the merge. Backward-compat-safe (no existing state has Project entries yet — Phase 5 is the first writer).
- **Script lines stay top-level (parent_id=None) for v0.** The brief doesn't currently let users group lines under sections — that's a brief-format extension for later. Sections are just labels in the current brief. Means line identity is `("", line_text, occurrence_index)` for v0. When the brief format grows a sections→lines structure, line identity becomes `(parent_section_name, line_text, occurrence_index)` and the merge code already handles it.
- **Live parse preview hits the server.** The plan called this out as the right choice (one parser, no js-yaml drift); confirmed during execution that hitting `/api/projects/parse` every 200ms on the keystroke debounce is cheap (PyYAML parses 4KB of frontmatter in <1ms).
- **`Project.brief_md` reconstruction fallback.** The orchestrator's `_full_brief_md` function reconstructs a canonical brief from `ParsedBrief` when `brief_md_source` is not passed. v0 route handlers always pass the source. The fallback exists for future programmatic Project creation (tests, migrations) where there's no original markdown to preserve.
- **First-op-on-fresh-state snapshot=null is correct.** Documented in two test fixtures (create-happy and snapshot-count-equals-op-count both seed the state file with a throwaway create before measuring). The Phase 1 `snapshot_before_destructive` returns None when the file doesn't exist yet — that's the documented behavior, not a bug to fix.

**Open follow-ups for the reviewer to evaluate:**

1. **Brief-format extension: sections → lines.** v0 lines are flat (parent_id=None); the brief has separate `script:` and `sections:` arrays with no link between them. A future brief format could group lines under sections (e.g. `script: [{section: "the hook", lines: [...]}, ...]`). The merge code already handles `parent_id` in the identity rule; only the brief schema and parser need to grow. Defer until Phase 7's UI starts needing the hierarchy.
2. **Markdown rendering in the preview.** v0 shows the brief as a textarea; no rendered preview. A side-by-side rendered view would be polish — easy to add with `react-markdown` if it ever matters.
3. **Project name uniqueness.** No constraint today — two projects can share a name (different IDs). The UI surfaces both. Worth a "unique-name warning" in the brief editor if Phase 7+ surfaces the project picker prominently.

**Files touched in Phase 5:**

```
NEW:
  clipfarm/brief.py
  clipfarm/projects.py
  clipfarm/routes/projects.py
  tests/test_brief.py
  tests/test_projects.py
  tests/test_routes_projects.py

MODIFIED:
  clipfarm/store.py         — _log_unknown_keys refactor with typing.get_origin/get_args
  clipfarm/models.py        — new Script model, Project.script: Optional[Script], TagKind expanded to "tag"
  clipfarm/app.py           — include projects router
  pyproject.toml            — pyyaml dependency
  uv.lock                   — pyyaml lockfile entry
  tests/test_load_unknown_keys.py — 4 new dotted-path tests for the new shapes
  web/src/pages/Brief.tsx   — first real implementation (was placeholder)
  web/dist/...              — rebuilt
  PHASES.md                 — Phase 4 marked verified, Phase 5 plan landed (with seven plan-review fixes folded in)
  COMPLETED_PHASES.md       — Phase 4 verified stamp + this Phase 5 entry
```

---

## Phase 4 — Boundary correction

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

The manual escape hatch for AI segmentation mistakes — five operations
on base clips with full tag + attempt propagation rules tested against
synthetic state (Phase 6 and Phase 8 plug in clean later).

- **Backend (new for Phase 4):**
  - `clipfarm/propagation.py` — pure tag + attempt-ref + trim-clamp rules. `clone_tags_to_pair` (split → clone with `stale=True` on both halves), `union_merge_tags` (merge → dedupe on `(project_id, project_tag_id, category)`), `drop_tags_for_clip` (delete → final removal), `reassign_attempt_refs` (split/merge → swap clip_id with optional `needs_review` flip), `mark_attempts_needs_review_for_clip` (delete → flag but leave the AttemptClip's `clip_id` pointing at the deleted ID — deliberate tombstone for the Phase 7+ "removed — pick a replacement" placeholder), `clamp_attempt_trims_for_clip` (the four-case rule activated). Module is pure: no I/O, no snapshot.
  - `clipfarm/boundary.py` — orchestrators. Each clip-producing op takes `transcript: Optional[WhisperTranscript]` as an explicit parameter — the route layer loads via `transcripts.load_transcript_for_source` once and passes it in. Keeps `boundary.py` I/O-free, matches the Phase 2/3 seam. `split_clip(state, clip_id, split_at_sec, transcript) → (c1, c2)`; `merge_clips(state, clip_ids, transcript) → new_id`; `adjust_clip_boundaries(state, clip_id, start, end, transcript)`; `create_clip_from_range(state, source_id, start, end, transcript) → new_id`; `delete_clip(state, clip_id) → (dropped_tags, affected_attempts)`.
  - `clipfarm/routes/clips.py` — five routes: `POST /api/clips/{id}/split`, `POST /api/clips/merge`, `PATCH /api/clips/{id}/boundaries`, `POST /api/sources/{id}/clips`, `DELETE /api/clips/{id}`. Each holds `app.state.save_lock` around the orchestrator call (mutation-under-lock pattern from Phase 2.1) and routes through `commit_state_with_snapshot(app, reason=<kebab-case>)`. Snapshot reasons: `split-clip`, `merge-clips`, `adjust-boundaries`, `create-clip`, `delete-clip` — all searchable + consistent. Domain errors map cleanly: `KeyError` → 404, `ValueError` → 400, `WritesFrozenError` → 409.
  - `transcript_text_for_range` extracted from `clipfarm/ingest.py` into `clipfarm/transcripts.py` (now public, shared between ingest and boundary). Ingest output is byte-identical before/after the extraction.

- **Trim-clamp four-case rule activated** (Phase 1 advance note). Negative offsets aren't touched (they extend past the base into source raw range, not constrained by base bounds). Positive offsets get clamped on **inward** base moves only:
  - Case 1: start moved inward → `trim_start = max(0, (old_start + old_trim_s) - new_start)`. Preserves the absolute effective start; collapses to 0 if the new base overshoots.
  - Case 2: end moved inward → symmetric.
  - Case 3: outward moves leave positive offsets alone.
  - Case 4 (pathological): if clamping produces `effective_start ≥ effective_end`, both offsets collapse to 0, a `WARNING` log line names `(attempt_id, clip_id)`, and the boundary adjustment succeeds. The boundary edit is the user's explicit ask; we don't fail it on a downstream trim conflict. `Attempt.needs_review` (already set by the calling op) gives Phase 10's UI a hook to surface "trim was reset on this attempt."

- **Frontend (`web/src/pages/Library.tsx`):** big extension of the Phase 3 view.
  - **Multi-clip selection** via cmd/ctrl-click (toggle in/out); plain click resets to single selection.
  - **Action bar** above the transcript that materializes based on selection:
    - 0 clips → keyboard hint banner.
    - 1 clip → `Split @midpoint`, `[` / `]` / `,` / `.` boundary nudge buttons + a destructive `Delete` button. Same actions wired to keyboard.
    - ≥ 2 clips on the same source → `Merge N clips` button.
  - **Keyboard shortcuts**: `m` → merge, `[` `]` `,` `.` → nudge boundaries (each move jumps to the nearest word boundary in the transcript), `Cmd/Ctrl+Backspace` → delete with always-confirm dialog. Disabled while focus is inside a text input.
  - **Create-clip dialog** (numeric start/end inputs in seconds) — works on transcript-having and footage-only sources; the footage-only source view shows a "Create clip by range" button up front instead of a transcript.
  - **Delete confirm dialog** ("you can restore from `.clipfarm/snapshots/`") — always confirms in v0; the "disable confirmation" setting is deferred until Settings has real persistence (TODO comment points at the future hook).
  - **Toasts** for success + failure on every mutation. Auto-dismiss at 1.8s for `ok` and 4.5s for `err`.
  - **Auto-refresh** on every mutation: `/api/state` refetched + transcript view re-fetched (cache hit, so cheap) via a `refreshNonce`.
  - **Search panel** got a minimum query length of 2 chars on the debounce — small UX polish flagged in the Phase 3 review.

- **Tests added (71 new — 182 total passing, up from 111):**
  - `tests/test_propagation.py` (18): all six pure helpers tested with synthetic tags + attempts. Clamp's four cases each get their own test, including the pathological collapse with a `caplog` assertion. Negative offsets explicitly tested as untouched. Multiple attempts referencing the same clip both get adjusted.
  - `tests/test_boundary.py` (35): each orchestrator's happy path + every validation error + the propagation side effects (tags cloned with `stale`, tags union-deduped on merge, attempt refs reassigned with `needs_review` on split, refs reassigned without `needs_review` on merge, refs left dangling on delete with `needs_review` set). Footage-only behavior (`transcript=None` → `transcript_text=""`) tested for `split_clip` and `create_clip_from_range`. The half-open touching-endpoint case proven not to be an overlap.
  - `tests/test_routes_clips.py` (18): one happy-path + at least one error case per route + the structural **`_count_snapshots_after_op` helper** wrapping every happy-path test (asserts exactly one new snapshot file appeared, name carrying the expected reason segment). Plus the **snapshot-count-equals-op-count invariant**: run one of each op type, assert snapshots increased by exactly 5. Plus the **save-lock-held-during-orchestrator** assertion carried from Phase 2.1.

**Manual verification run (all green):**

Ingest `05.19.26/` (18 sources, 157 clips), then exercise each op against `btc.0.4`:

- **Split** `btc.0.4__00-00-24.890__00-00-50.610` at the midpoint 37.75s → 2 new clips with the boundaries summing to the original. Snapshot file: `…__split-clip.json`.
- **Merge** the two halves back together → 1 new clip with the original range restored. `transcript_text` preview matches expected content. Snapshot: `…__merge-clips.json`.
- **Adjust** the merged clip's end inward by 1.0s (to 49.61) → clip ID unchanged, boundaries updated. Snapshot: `…__adjust-boundaries.json`.
- **Create** a new clip at `[55, 60)` (known unused range) → `btc.0.4__00-00-55.000__00-01-00.000` appears in state. Snapshot: `…__create-clip.json`.
- **Delete** the just-created clip → gone. Response carries `dropped_tag_rows=0` and `affected_attempts=0` (forward-compatible shape even at zero). Snapshot: `…__delete-clip.json`.
- **Snapshot inventory**: exactly 5 files total, one of each reason (`adjust-boundaries`, `create-clip`, `delete-clip`, `merge-clips`, `split-clip`). The op-count == snapshot-count invariant holds.
- **Failure modes**: split with an out-of-range timestamp on a deleted clip → 404 (the clip is gone, found first). Merge across two sources → 400. All as designed.

**Assumptions made + deviations from the plan:**

- **Boundary nudge snaps to word boundaries on the server side.** The frontend collects every word's `start` / `end` timestamps from the loaded transcript and finds the next word edge in the direction of the press. The server is told the new exact boundaries, doesn't infer. Footage-only sources don't have word edges to snap to, so nudge keys are inert on them — the `Create clip by range` dialog is the only path.
- **Merge selection only enables the `Merge N` button when all selected clips live on the same source.** The check is client-side (visual); the server still rejects cross-source merges with 400 even if the client missed it. Defense in depth.
- **Split @midpoint** (not user-chosen). v0 ships the simplest split UX: pick the clip, split at its midpoint. A finer split-anywhere UI (click between two specific words) is a Phase 4.1 polish item — flagged for follow-up. The server already supports any in-range `split_at_sec`, so the upgrade is frontend-only.
- **Transcript LRU cache invalidation on mutate.** After every boundary op the route doesn't touch the on-disk sidecar (only `clipfarm.json` changes), but `Clip.transcript_text` is recomputed from the cached transcript. The cache still returns the right `WhisperTranscript` (mtime hasn't changed); the recomputation runs against it. No stale-text risk.
- **Pathological-clamp warning is logged, not surfaced in API response.** A future hook (Phase 10 UI) can read the log or check `Attempt.needs_review` to know a trim was reset. v0 doesn't propagate the warning back through the boundary route response — keeps the response shape simple. Flagged as a possible polish item.
- **`merge_clips` with overlapping clips raises** rather than silently picking one side. Stricter than the spec's literal text but safer — overlapping clips on the same source shouldn't exist in the first place (boundary validation prevents it on adjust/create), so encountering them at merge time is a data-state bug worth surfacing.

**Open follow-ups for the reviewer to evaluate:**

1. **Split UX**: v0 ships `Split @midpoint` only. A finer "click between two words to split there" UI (popover anchored at the gap, computes the gap midpoint as `split_at_sec`) is a polish-layer add. Cheap, isolated to `TranscriptView`. Not blocking — but worth doing before extensive boundary-correction dogfooding.
2. **Pathological-clamp surfacing in the API response.** Right now the warning lives only in logs. If Phase 10's UI wants to show "trim was reset on N attempts," the route could return a count of `pathological_clamps` from the adjust path. Forward-compatible response shape; defer until Phase 10 actually needs it.
3. **Drag-select word range → create clip.** Cleaner than the numeric dialog for transcript-having sources. Native browser selection over the word spans is possible but capture-fiddly; v0 punted to the numeric dialog. Worth doing alongside Split UX polish.
4. **`merge_clips` with no_overlap_or_gap_tolerance.** The spec's example talks about merging clips that were incorrectly split (sub-2-sec gap). v0 accepts any gap. Worth pinning a spec note that ≥ 2 sec gaps are intentional and merge folds them in deliberately.

**Files touched in Phase 4:**

```
NEW:
  clipfarm/boundary.py
  clipfarm/propagation.py
  clipfarm/routes/clips.py
  tests/test_boundary.py
  tests/test_propagation.py
  tests/test_routes_clips.py

MODIFIED:
  clipfarm/app.py           — include clips router
  clipfarm/ingest.py        — use public transcript_text_for_range from transcripts.py
  clipfarm/transcripts.py   — export transcript_text_for_range as a shared helper
  web/src/pages/Library.tsx — multi-clip selection + action bar + dialogs + toasts + keyboard shortcuts
  web/dist/...              — rebuilt
  PHASES.md                 — Phase 3 marked verified, Phase 4 plan landed (with seven plan-review fixes folded in)
  COMPLETED_PHASES.md       — Phase 3/3.1 verified stamps + this Phase 4 entry
```

---

## Phase 3.1 — P3 review punch-list

**Verified by Lillian:** ✅ 2026-05-25 (folded in alongside Phase 3).

**Three small fixes from the reviewer's Phase 3 assessment:**

1. **`typing.Optional` import cleanup in `tests/test_transcripts.py`.** Mid-file `from typing import Optional` with a stale `noqa` comment claiming to dodge a forward-ref headache that never existed (`from __future__ import annotations` already deferred evaluation). Moved to the top with the rest of the imports; dropped the comment.
2. **Half-open `[start, end)` convention documented in `_clip_id_for_timestamp`** (`clipfarm/routes/search.py`). Added a docstring callout that a timestamp `t == c.end_sec` belongs to the NEXT clip, not this one. Cheap defense against Phase 4's extend/shrink drifting to inclusive ranges and breaking the "one timestamp belongs to one clip" invariant.
3. **`/api/sources/{id}/transcript` payload trim — `probability` dropped from each word.** New `WhisperWordLite` / `WhisperSegmentLite` types in `routes/transcripts.py`; the route projects the full Whisper segments into the lite shape before returning. The frontend uses zero bytes of `probability`, and stripping it removes ~50% of the per-word JSON cost. On btc.0.4 (4735 words) that's a meaningful trim; on a future 2-hour recording it's load-bearing. New test (`test_transcript_response_drops_probability`) asserts the field is absent on every word in the response.

**112 tests passing** (Phase 3's 110 + the new payload-trim test + the existing tests still green).

**Files touched in 3.1:**

```
tests/test_transcripts.py          — moved Optional import to the top
clipfarm/routes/search.py          — half-open interval comment
clipfarm/routes/transcripts.py     — WhisperWordLite + WhisperSegmentLite + projection
tests/test_routes_transcripts.py   — new test for probability stripping
```

---

## Phase 3 — Library page (raw transcript browser)

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

- **Backend (new for Phase 3):**
  - `clipfarm/transcripts.py` — `load_transcript_for_source(source)` reads + validates the sidecar via `WhisperTranscript`, returns `None` on failure (never raises). In-process LRU cache keyed by `(transcript_path, mtime_ns)`, capacity 32. Re-running `transcribe.py` automatically invalidates the entry because the mtime changes — no server restart needed. Thread-safe via `threading.RLock`.
  - `clipfarm/search.py` — pure `search_transcript(transcript, query, context_words=5) -> list[SearchHit]`. Word-level case-insensitive substring (the locked v0 behavior); strips the faster_whisper leading-space convention before comparing. Multi-word phrases don't match (Future Idea). Empty/whitespace query raises `ValueError`. `SearchHit` carries `word_index`, `timestamp_sec`, `context_before`, `match`, `context_after`.
  - `clipfarm/routes/transcripts.py` — `GET /api/sources/{source_id}/transcript`. Returns `{source_id, filename, duration_sec, segments, clips}` where `clips` is the source's auto-detected ranges sorted by `start_sec`. **404** for unknown source. **422** for transcript-less (footage-only) source — the frontend uses this to render a clean "no transcript" message. **500** if `state.transcript_path` is set but the file is unreadable (state-vs-disk drift).
  - `clipfarm/routes/search.py` — `GET /api/search?q=&source_id=&limit=`. Walks every source (or one if filtered), pulls transcripts via the cache, returns `{query, total, truncated, hits[]}` with each hit stamped with `source_id`, `filename`, and `clip_id` (the clip the matched word falls inside, if any). Empty/whitespace `q` → **400**. Unknown `source_id` → **404**. `limit` default 200, max 1000.

- **Frontend (`web/src/pages/Library.tsx`):** rebuilt as a two-column layout.
  - **Top bar:** debounced (200ms) search input + hit count + truncation indicator. Results inline below the bar — each hit shows filename + timestamp + context with the matched word highlighted in amber. Clicking a hit jumps to the source, scrolls to the word, and flash-rings the matched word + its clip.
  - **Left rail:** collapsible "Ingest" panel (folder text input + button + result summary, with `<details>` expansions for `sources_skipped` / `rejected` / `warnings` — matches the carry-over UX request from the Phase 2 review). Source list below: filename, duration, clip count, footage-only indicator, unavailable greyed-out treatment.
  - **Main panel:** the selected source's raw transcript as a flowing text stream. Words are span-wrapped with `data-word-index`, `data-start`, `data-end`, and `data-clip-id` attributes (Phase 4 will use the first; Phase 9 will use the timing). Clip ranges get alternating tints so adjacent boundaries read as distinct; the selected clip gets a stronger amber ring. Empty states for "no source picked" and "footage-only" (422) are explicit.
  - Faster_whisper leading-space convention is preserved via `white-space: pre-wrap` — concatenation looks like prose, not "wordwordword".

- **Tests added (110 passing total — was 77 in Phase 2.1):**
  - `tests/test_transcripts.py` (7): parsed shape returns; `None` for missing path / missing file / malformed JSON; cache hit doesn't re-read disk (verified by patching `read_text` to raise); cache invalidates on mtime change; cache cap evicts oldest.
  - `tests/test_search.py` (13): empty transcript; substring match within word; case-insensitive; no-match; multi-word phrase does NOT match (locks v0 behavior); multiple hits in order; context bounds clamp at start + end; custom `context_words`; match spans segment boundary; default 5 locked; empty/whitespace query raises; negative `context_words` raises.
  - `tests/test_routes_transcripts.py` (5): happy path returns expected shape; 404 for unknown source; 422 for footage-only; 500 if sidecar disappears mid-session; clips sorted by `start_sec` regardless of insertion order.
  - `tests/test_routes_search.py` (8): finds matches across sources; case-insensitive; 400 on empty `q` (422 on missing param from FastAPI); no-match returns clean empty list; `source_id` filter narrows results; unknown `source_id` → 404; `limit` truncates and flips `truncated: true`; hit carries `clip_id` when timestamp falls inside a detected clip.

**Manual verification run (all green):**

Real ingest against `05.19.26/` (18 sources, 157 clips). Then:

- `GET /api/sources/4/transcript` (btc.0.4) → returns `duration_sec=2059.84`, `segments=379`, `clips=91`, `total words=4735`. First segment words confirm the faster_whisper leading-space convention is preserved end-to-end (`[' She', ' makes', ' me', ' smile', ...]`).
- `GET /api/sources/9999/transcript` → **404**.
- `GET /api/search?q=smile` → **2 hits**, both inside btc.0.4, each carrying the correct `clip_id` and a real context window:
  - `5.1s → ...| She makes me|smile|all the time a flop...` (inside `btc.0.4__00-00-04.370__00-00-09.600`)
  - `1879.9s → ...|back at this video and|smile|My little dog is in...` (inside `btc.0.4__00-31-10.500__00-31-27.200`)
- `GET /api/search?q=the` → 337 hits across the library; `?source_id=4` narrows to 223 from btc.0.4 alone. `?limit=3` truncates with `truncated: true`.
- Empty + whitespace queries → **400**.
- Synthesized footage-only `.mov` → `GET /transcript` returns **422**.
- Repeated `GET /api/search?q=smile` runs at ~10ms wall-clock (cache hits on every sidecar after first scan); single transcript fetch at 13ms warm.

**Assumptions made + deviations from the plan:**

- **Substring search is genuinely word-level.** A search for `"i"` matches every word containing the letter "i" (2433 hits on the dogfood folder). That's correct under the locked v0 spec but the volume is real — the `limit` cap (default 200, max 1000) keeps the response bounded. A future "whole-word" toggle is a polish-layer addition, not a v0 requirement.
- **Cache invalidation is mtime-based, not content-based.** If a sidecar is overwritten with byte-identical content (rare), the cache will still serve the old parsed object — which is fine because the parsed object IS byte-identical. The "stale paths" eviction in `_TranscriptCache.put` drops the previous-mtime entry when a new mtime appears, so we never keep two versions of the same path.
- **`load_transcript_for_source` returns `None` on every failure.** That's defensive — sidecar problems shouldn't take down the search route. The route then turns transcript-less or load-failed into a 422 or 500 as appropriate. `None` is also what `transcript_path is None` returns, so the call site doesn't need to distinguish.
- **`StampedHit.clip_id` is `Optional[str]`.** A search match can land between two detected clips (in the silence gap that defined the boundary). The frontend handles `null` by showing the timestamp without a clip ID. v0 segmentation uses 2-sec gaps, so the gap-only words are short and rare, but the model has to allow it.
- **Frontend layout assumes ≥1024px width.** The two-column grid is fixed-width (280px sidebar). Phase 3 is a desktop-only UI; mobile / narrow viewport tuning is polish-layer territory. Lillian works on a Mac in a full-width browser, so no v0 problem.

**Open follow-ups for the reviewer to evaluate:**

1. **Multi-word phrase search.** Spec calls out semantic search as Future Ideas but doesn't explicitly bin "phrase substring" (e.g. `"self custody"`). v0 behavior is locked: phrase queries return 0 hits even when the words appear adjacent. Worth a one-line decision in the spec under "Library page" if Lillian wants this to stay clear of confusion.
2. **Per-source ingest history.** The Phase 2 reviewer flagged "rejection-noise on re-ingest" as a Phase 3-or-later concern. Phase 3 still scopes rejection lists to the current ingest response — they vanish on next reload. Persistent ingest history would land alongside an "Ingest activity" log view, which is polish-layer. Defer.
3. **`/api/search` with `q` of one character** is allowed by the current validation (after strip). `?q=a` returns 2.5k+ hits in the dogfood folder. The `limit` cap protects performance, but the UX might want a minimum length of 2 or 3 in the frontend's debounce. Flagged for polish.

**Files touched in Phase 3:**

```
NEW:
  clipfarm/transcripts.py
  clipfarm/search.py
  clipfarm/routes/transcripts.py
  clipfarm/routes/search.py
  tests/test_transcripts.py
  tests/test_search.py
  tests/test_routes_transcripts.py
  tests/test_routes_search.py

MODIFIED:
  clipfarm/app.py        — include transcripts + search routers
  web/src/pages/Library.tsx — rebuilt as two-column with search bar, transcript view, and ingest panel
  web/dist/...           — rebuilt
  PHASES.md              — Phase 2 marked verified, Phase 3 plan written
  COMPLETED_PHASES.md    — Phase 2/2.1 verified stamps + this Phase 3 entry
```

---

## Phase 2.1 — Dead-code purge + mutation-under-lock seam

**Verified by Lillian:** ✅ 2026-05-25 (folded in alongside Phase 2).

**Two fixes from the reviewer's Phase 2 assessment:**

1. **Deleted dead `_transcript_sidecar` in `ingest.py`.** The function was wrong (would have built `btc.0.4.mov.whisper.json` instead of `btc.0.4.whisper.json`) and had a comment underneath saying so. The real impl (`_sidecar_path_for`) lived right below. Removing the misdirection before someone reads it during Phase 3.
2. **`async with app.state.save_lock:` around the `ingest_folder` call in `routes/ingest.py`.** Under today's purely-sync orchestrator no race is reachable (asyncio doesn't preempt sync code), but the lock makes "mutation requires the lock" an explicit invariant ahead of Phase 4's destructive routes. The day ingest goes async (network probe, ML enrichment, anything with an `await`), `_next_source_id`'s `max(existing) + 1` allocator would otherwise be racy across concurrent route handlers. Comment in the route spells out why both critical sections (mutation here + write in `commit_state_to_disk`) are safe as two separate locks.

**Tests added (2 new — 77 total passing):**

- `test_ingest_holds_save_lock_during_orchestrator_call` — patches `ingest_folder` with a fake that records `app.state.save_lock.locked()` at call time. Asserts `[True]`. Would catch the seam closing back up if a future refactor moves the lock or removes it.
- `test_concurrent_ingest_produces_consistent_state` — three concurrent `/api/ingest` POSTs via `ThreadPoolExecutor` against the same folder. Final state must have each source exactly once with a unique ID, and the clip count must match a single-ingest run (no double-segmentation). Today this passes trivially because the orchestrator is sync; tomorrow when ingest goes async it's the regression guard that catches the ID race.

**Files touched in 2.1:**

```
clipfarm/ingest.py        — removed dead _transcript_sidecar
clipfarm/routes/ingest.py — wrapped ingest_folder call in `async with app.state.save_lock:`
tests/test_routes_ingest.py — two new tests
```

The reviewer's other notes are tracked separately: the spec edits (#3 — duration policy, video extensions, source-ID format, sidecar-problems-don't-kill-source) are queued for the reviewer to land directly in `clipfarm-spec.md`; the polish items (`sources_skipped` UI expansion, rejection-noise on re-ingest, recursive folder walk) are flagged for Phase 3 kickoff.

---

## Phase 2 — Ingest pipeline

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

- **Phase 2 kickoff cleanups** (punch-list residue from the Phase 1 review):
  - New `clipfarm/routes/deps.py` — `get_state`, `commit_state_to_disk`, `commit_state_with_snapshot` all live here. `app.py` and every route imports from `deps.py`; the duplicate `_get_state` in `routes/state.py` is gone.
  - `WATCHDOG_DEBOUNCE_MS = 200` dead constant removed from `store.py` (real interval is `_WATCH_POLL_INTERVAL_SEC = 0.5` in `watcher.py`).
  - `POST /api/test/touch` gated behind `CLIPFARM_TEST_ROUTES=1`. Default OpenAPI surface no longer carries it.
  - One-line comment in `run_source_integrity_check` documenting the `validate_assignment=False` assumption.
  - New `tests/test_models_round_trip.py` (6 tests): defaults for `Clip.tracks`, `Attempt.continuity_score`, `Attempt.premade_bucket`, `Attempt.needs_review`, `AttemptClip.internal_pause_max_sec` serialize as `null` (never `{}`, never missing). Parametrized round-trip through disk confirms exact value preservation.

- **Backend (new for Phase 2):**
  - `clipfarm/ffprobe.py` — `probe_video(path) -> {fps, duration_sec}`. Subprocess wrapper around `ffprobe -show_entries stream=r_frame_rate,duration:format=duration -of json`. Parses fractional fps (`30000/1001` → `29.97`). All failure modes (binary missing, exit nonzero, malformed JSON, `OSError`) return `(None, None)` and log a warning. Never raises.
  - `clipfarm/segmentation.py` — pure `segment_words_by_silence(words, gap_threshold_sec=2.0) -> list[(start, end)]`. No I/O. Tested with the threshold-boundary edge cases (sub-, equal-, super-threshold), empty input, single-word input, custom thresholds, and the spec's locked default of 2.0s.
  - `clipfarm/ingest.py` — orchestrator. Walks the folder, pairs `.mov` + `<stem>.whisper.json`, validates the sidecar via `WhisperTranscript`, rejects `__`-named files with a sanitized-rename suggestion, probes fps/duration, segments words into clips, mutates `state` in place. Returns `IngestResult` summary. **Re-ingest semantics:** new source → add+segment; existing source with transcript newly available → upgrade+segment; otherwise → skip. Sources whose files disappeared aren't auto-removed (integrity check handles them). `duration` policy: prefer sidecar value when present, fall back to ffprobe.
  - `clipfarm/routes/ingest.py` — `POST /api/ingest` with `{folder: <absolute path>}`. 400 on relative or missing path, 409 when `writes_frozen`, 200 with `IngestResult` JSON on success. Persists via `commit_state_to_disk(app)` only when something actually changed.

- **Frontend (`web/src/pages/Library.tsx`):** absolute-path text input + Ingest button. Result summary shows added/updated/skipped/rejected/clip counts, with collapsible rejection + warning details. Source list below: filename (mono), duration, fps, clip count, transcript status (`ok` / `footage-only`), `unavailable` indicator on missing files. Path-picker UX limitation captured: HTML's `<input type="file" webkitdirectory>` can't surface absolute filesystem paths from the browser sandbox, so a text input is the v0 affordance. An Electron-style native picker can land later.

- **Tests added (75 passing total — was 33 in Phase 1):**
  - `tests/test_ffprobe.py` (9): canned-subprocess tests for clean run, fractional fps, `0/0` fps, missing duration, exit nonzero, binary missing, malformed JSON, `OSError`. Plus a real-file smoke test against `btc.0.4.mov` (skipped if not present).
  - `tests/test_segmentation.py` (11): empty/single/contiguous inputs, above-/exact-/below-threshold gaps, multi-segment, custom thresholds, negative threshold raises, zero threshold splits every word, default-threshold-locked assertion.
  - `tests/test_whisper_validation.py` (6): real `btc.0.4.whisper.json` validates and carries leading-space word convention; minimal valid payload; missing `segments` defaults to `[]`; missing `start` raises; missing `word` field raises; unknown top-level key dropped silently at model boundary.
  - `tests/test_ingest.py` (11): happy path (2 pairs → 4 clips); transcript-less → footage-only; `__` rejection with sanitized rename; `schema_version=2` rejected, batch continues; malformed JSON sidecar rejected, source still added as footage-only; re-ingest idempotent; transcript-appearing-later upgrades source; filenames with spaces and special chars round-trip (`cuddlingchai content.mov`, `is my face crooked??.mov`, `more test videos <3.mov`); `__` in directory path is fine (only filename stem is constrained); dotted stem (`btc.0.4`) handled correctly; not-a-directory raises.
  - `tests/test_routes_ingest.py` (5): happy path through `TestClient` (lifespan runs), relative path → 400, missing folder → 400, freeze → 409, re-ingest through route is idempotent.
  - `tests/test_models_round_trip.py` (6): the kickoff-cleanup test mentioned above.

**Manual verification run (all green):**

Live ingest against the real dogfood folder (`05.19.26/`):

- `POST /api/ingest {"folder": "...05.19.26"}` → **200, 18 sources_added, 157 clips_detected, 0 rejected, ~1.3s total**.
- `btc.0.4.mov`: source_id `"4"`, **fps 30.0, duration 2059.84s (~34 min), 91 clips detected**. First clip starts at 4.37s (`"She makes me smile all the time..."`). This is the empirical clip-count baseline for `btc.0.4` — regressions on the segmentation should be visible against `91`.
- Special-char filenames (`is my face crooked??.mov`, `more test videos <3.mov`) ingested cleanly and round-trip through `clipfarm.json` without escaping issues.
- **Re-ingest idempotency:** second `POST /api/ingest` on the same folder returns `sources_added=[]`, all 18 in `sources_skipped`, `clips_detected=0`.
- **`__` rejection:** synthetic `/tmp/clipfarm_p2_synth/bad__file.mov` rejected with `sanitized_rename: "bad_file.mov"`; the rest of the batch (`good.mov`, `from_future.mov`) still ingested.
- **`schema_version=2` rejection:** synthetic `from_future.whisper.json` rejected with a clear message pointing at `transcribe.py`. The corresponding `from_future.mov` was still added as a footage-only source rather than disappearing.

**Benchmark:** `load_state()` over the full ingested state (18 sources, 157 clips, 88,413-byte `clipfarm.json`): **2.63 ms average over 10 runs** (warm). For the spec's scale concern (a single 30-min recording produces ~350 clips; the 05.19.26 folder hits ~6k clips at full transcription), linear extrapolation puts load time at ~100ms for 6k clips — comfortably within "snappy on startup" budget. **SQLite migration not urgent at the dogfood scale.** Worth re-measuring after one full week of real use.

**Assumptions made + deviations from the original plan:**

- **`duration` policy decided.** Sidecar's `duration` (from `transcribe.py`) wins when present; falls back to `ffprobe` duration; otherwise `None`. The deferred question from the Phase 1 review is now answered explicitly in code + spec-aligned. btc.0.4 stored as `2059.84s` — the sidecar value, not ffprobe's.
- **Sidecar problems don't kill the source.** A malformed or wrong-schema-version sidecar adds the source to `rejected` but ALSO registers the `.mov` as a footage-only source. Rationale: the user almost always wants to keep the source entry and re-run `transcribe.py` later. Losing the source on a sidecar problem would be surprising.
- **Acceptable video extensions widened to `{.mov, .mp4, .m4v, .mkv}`.** The spec calls out `.mov` for the dogfood folder but doesn't constrain the set. Phase 2 tolerates the common siblings — only `.mov` exists in `05.19.26/`, so no behavior change in practice, but the next folder Lillian drops might not be `.mov`-pure.
- **Source ID format locked.** Monotonic string integers (`"1"`, `"2"`, ...). Adequate for the scale; if we ever need UUIDs, that's a migration.
- **Clip-ID encoded form uses `HH-MM-SS.mmm` (hyphens, not colons).** Dashes are filesystem-safe; colons make some tools unhappy. The ID is opaque after creation either way, but the encoded form needs to round-trip through JSON keys + URL slugs eventually.
- **Frontend's path input is a text field, not a folder picker.** Browser sandbox can't supply absolute paths from `<input type=file>`. Documented as a known v0 constraint; an Electron wrapper or a native picker addon can land later. Not blocking for dogfood.
- **`test_routes_ingest.py` uses `TestClient` not `httpx.AsyncClient`.** Tried `ASGITransport` first — lifespan doesn't run with it, so `app.state.writes_frozen` was undefined. `TestClient` runs lifespan correctly. The async-ness of routes is still tested via `pytest-asyncio` for the lower-level store/save pieces.

**Open follow-ups for the reviewer to evaluate:**

1. **`_log_unknown_keys` dict-of-model heuristic** (deferred from Phase 1 review #5) is still in place. Phase 2 didn't add nested-shape models that would stress it (WhisperTranscript is consumed at the model boundary in ingest, not loaded through `load_state`). Worth doing before Phase 5 ships the `Script` model.
2. **Per-clip transcript text quality.** v0 strips leading/trailing whitespace on the assembled clip text. Faster_whisper's leading-space convention means we lose the leading word's space — that's intentional for display but worth flagging if Phase 3's transcript browser needs the raw form. The full transcript stays in `<stem>.whisper.json` and the by-source view (Phase 3) reads from there directly, so this is cosmetic on the clip card, not lossy.
3. **No clip-overlap invariant tested.** Adjacent segmentation ranges don't overlap by construction (each word belongs to exactly one range), but there's no explicit test asserting that across the full 05.19.26 ingest. Worth adding in Phase 3 or as part of Phase 4's boundary-correction tests.

**Files touched in Phase 2:**

```
NEW:
  clipfarm/ffprobe.py
  clipfarm/segmentation.py
  clipfarm/ingest.py
  clipfarm/routes/ingest.py
  clipfarm/routes/deps.py
  tests/test_ffprobe.py
  tests/test_segmentation.py
  tests/test_whisper_validation.py
  tests/test_ingest.py
  tests/test_routes_ingest.py
  tests/test_models_round_trip.py

MODIFIED (kickoff cleanups):
  clipfarm/app.py       — imports from deps.py; removed inline get_state and commit helpers; route inclusion adds ingest
  clipfarm/store.py     — removed dead WATCHDOG_DEBOUNCE_MS; documented integrity-check assumption
  clipfarm/routes/state.py  — uses deps.get_state; test/touch gated by CLIPFARM_TEST_ROUTES env var
  web/src/pages/Library.tsx  — first real implementation (was placeholder)
  web/dist/...          — rebuilt
```

---

## Phase 1.1 — Race fix + atomic snapshot-then-save

**Verified by Lillian:** ✅ 2026-05-25 (folded in alongside Phase 1).

**Two fixes from the reviewer's pass on Phase 1:**

1. **Hash-install race in `commit_state_to_disk` closed.** Original flow released the lock between writing the file and installing the new hash on the watcher; if the 0.5s poll fell in that window, the watcher saw an "external" change and would freeze writes. Fix: `save_state()` now takes an optional `post_write` callback that runs **inside the lock** with the serialized form's hash. `commit_state_to_disk` passes `watcher.update_last_known_hash` as `post_write`, so the hash install and the write are one critical section.
2. **`save_state_with_snapshot()` added.** Spec invariant says snapshot-then-save is a single locked critical section. Original `snapshot_before_destructive()` was sync and called outside the lock. New helper acquires the lock once, snapshots the pre-change on-disk file, atomic-writes the new state, installs the hash via `post_write` — all inside one `async with lock:` block. Routes that mutate base clips (Phase 4's first user) call this via `commit_state_with_snapshot(app, reason)` on `app.py`.

**Tests added (`tests/test_store.py`, 6 new — 27 total passing):**

- `test_post_write_called_inside_lock_with_correct_hash` — asserts the callback receives `hash_serialized(serialized)` and that `lock.locked()` returns True while the callback runs.
- `test_post_write_not_called_when_frozen` — if `WritesFrozenError` raises, the callback never fires (the watcher must not learn about a write that didn't happen).
- `test_save_with_snapshot_writes_old_state_to_snapshot_then_new_to_main` — establishes a baseline, applies a destructive save, asserts the snapshot file has the OLD content and the main file has the NEW content.
- `test_save_with_snapshot_no_baseline_returns_none_snapshot` — fresh file → snapshot returns None and the new state still lands.
- `test_save_with_snapshot_post_write_inside_lock` — same lock-held + correct-hash assertions for the snapshot variant.
- `test_save_with_snapshot_raises_when_frozen` — freeze blocks both the snapshot AND the write; neither side-effect occurs.

**Live re-verification:** 40 concurrent `POST /api/test/touch` against the new code → all 200s, file valid JSON, **zero "external write" events in the watcher log** (vs. the original code where the race window could trip the freeze). The reviewer's deferred punch-list items (`WATCHDOG_DEBOUNCE_MS` constant, duplicate `_get_state`, dict-of-model heuristic, `/api/test/touch` env-gate, integrity-check mutation comment, round-trip test for new optional fields, `WhisperTranscript.duration` policy) are out of scope for this cleanup — flagged for either a focused follow-up or Phase 2 kickoff.

**Files touched in 1.1:**

```
clipfarm/store.py     — added `post_write` param to save_state; new save_state_with_snapshot()
clipfarm/app.py       — commit_state_to_disk uses post_write; new commit_state_with_snapshot()
tests/test_store.py   — six new tests
```

---

## Phase 1 — FastAPI backend + frontend skeleton + JSON schema + safety scaffolding

**Verified by Lillian:** ✅ 2026-05-25

**Built (2026-05-25):**

- **Backend package `clipfarm/`** wired up against the revised spec + plan:
  - `models.py` — every entity from the data model, all with `extra="ignore"`. New product fields are declared: `Attempt.continuity_score`, `Attempt.premade_bucket`, `Attempt.needs_review`, `AttemptClip.internal_pause_max_sec`. `WhisperTranscript` (+ `WhisperWord`, `WhisperSegment`) declared for Phase 2 ingest. `ClipFarmState` carries a stubbed `model_validator(mode="after")` for `ClipProjectTag` uniqueness — early-returns at v0, one-line activation in Phase 6.
  - `store.py` — single entry point for `clipfarm.json`. `load_state()` reads → migrates → log+drops unknown keys → validates → integrity-checks. Two save APIs: `save_state(state, path, lock, *, writes_frozen=False)` (async, takes the lock, raises `WritesFrozenError` when frozen) for routes, and `save_state_sync()` for tests/startup. `snapshot_before_destructive()` writes to `.clipfarm/snapshots/<ISO>-<ms>-<hash4>__<reason>.json` and prunes to 50.
  - `watcher.py` — `PollingObserver` (not `Observer`) with a 0.5s poll interval. Self-write filtered by comparing the file's hash to the in-memory `last_known_hash`. Conflict path is exposed via the `WatcherCallbacks.on_conflict` callback — `app.py`'s impl flips `app.state.writes_frozen` and pushes onto a `queue.Queue`.
  - `app.py` — lifespan installs `app.state.{clipfarm, save_lock, writes_frozen, dirty, conflict_events, watcher}`. `commit_state_to_disk(app)` is the single seam routes use to persist; it respects the freeze flag. `get_state(request)` is the DI provider (also re-exported as a local `_get_state` proxy inside `routes/state.py` to avoid the import cycle with `app.py`).
  - `routes/state.py` — `GET /api/state`, `GET /api/health`, `GET /api/conflicts/pending` (counter + frozen flag for the Phase 2 modal to surface), `POST /api/test/touch` (used by the concurrent-save verification — bumps an off-schema counter on `app.state._touch_counter` and saves).
  - `migrations/__init__.py` — `CURRENT_VERSION = 1`, empty `_MIGRATIONS` list, `run_migrations()` runner. `v1_to_v2.py` placeholder.

- **Frontend `web/`** — Vite + React + Tailwind scaffold built to `web/dist/`. Four routed pages (Library / Project / Brief / Settings) with placeholder content. `vite.config.ts` proxies `/api/*` to `:8765` for dev mode. FastAPI mounts `web/dist/assets/` and serves `index.html` via the catch-all so React Router handles refreshes.

- **Tests (21 passing):**
  - `test_store.py` (10): atomic-save round-trip, atomic-write leaves no `.tmp`, empty-state on missing file, snapshot writes pre-state bytes, snapshot no-op on missing file, pruning keeps last `SNAPSHOT_LIMIT`, label sanitization, same-millisecond distinct filenames, **concurrent saves serialize under `asyncio.Lock`**, frozen save raises `WritesFrozenError`.
  - `test_load_unknown_keys.py` (2): top-level + nested unknown keys load successfully, warning emitted naming each, round-tripped state contains no unknowns.
  - `test_migrations.py` (4): no-op at current version, `needs_migration` helper, refuses downgrade, chained migrations apply in order.
  - `test_source_integrity.py` (3): missing source flips `unavailable=True`, restored source flips back, end-to-end through `load_state`.
  - `test_conflict_freeze.py` (2): `writes_frozen=True` blocks save, post-resolution unfrozen save writes.

- **Repo plumbing:** `.gitignore` covers `.DS_Store`, `clipfarm.json`, `.clipfarm/`, `web/node_modules/`, `web/dist/`, `__pycache__/`, `.venv/`. `.DS_Store` removed from tracking via `git rm --cached`. `README.md` covers prerequisites + dev commands.

**Manual verification run (all green):**

- `uv run uvicorn clipfarm.app:app --port 8765` boots cleanly.
- `GET /` returns the React shell (asset 200).
- `GET /api/state` returns the empty default shape when `clipfarm.json` is absent.
- External-edit reload check: three sequential edits to the on-disk JSON were each picked up within ~1.5s; the second edit added an unknown top-level key and the third added a nested unknown — both got `WARNING` log lines from `clipfarm.store` naming the exact dotted path of each dropped key (`_lillian_note`, `projects.3._secret_field`).
- Concurrent-save check: 20 parallel `POST /api/test/touch` calls all returned 200 with counters 1→20; the final on-disk file is valid JSON.
- `pytest`: 21 passed.

**Assumptions made + deviations from the original plan:**

- **`PollingObserver` over the default `Observer` on macOS.** The native FSEvents-backed observer is unreliable for rapid back-to-back single-file changes — verification on this machine showed the second edit never firing. PollingObserver with a 0.5s interval gives a deterministic per-poll diff at trivial cost (single `stat()` per cycle). Locked the choice in the watcher and called it out in a comment so it doesn't drift back. **Recommend the reviewer flag whether this should be promoted into the spec's "Decisions locked" section.**
- **`threading.RLock` (not `Lock`) inside the watcher.** Found during verification: `_maybe_fire_change` holds the lock then invokes `on_external_change`, which calls back into `update_last_known_hash`, which tries to re-acquire the same lock. With `threading.Lock` that's a permanent deadlock — the first event succeeded but the watchdog thread hung indefinitely afterward, so no subsequent edit was ever detected. RLock is reentrant; the only externally observable difference is that the thread doesn't hang. Comment in the constructor explains the why.
- **`StrictModel` keeps its name despite switching to `extra="ignore"`.** The name now slightly misleads (the model is no longer "strict" in the Pydantic sense). Left for now because every model in the file inherits from it and the rename is a churn-only edit — propose renaming in a focused PR if the reviewer cares.
- **`AsyncIO.Lock` not extended to the snapshot helper directly.** Spec says snapshot-then-save are one critical section. `save_state()` acquires the lock, but `snapshot_before_destructive()` is synchronous and currently called *outside* the lock by future destructive routes. Phase 4 (boundary correction) is the first place that needs that coupling — will tighten the API then (likely a `save_with_snapshot()` helper that acquires the lock once and does both inside). Phase 1 doesn't expose any destructive routes yet, so this seam doesn't matter at v0; flagged so the reviewer doesn't miss it.
- **`POST /api/test/touch` is shipped as a real route, not feature-flagged.** It mutates an off-schema counter (`app.state._touch_counter`) and persists via `commit_state_to_disk()`, so it doesn't dirty the JSON schema. It's tagged `[test]` in the OpenAPI doc. Will remove once the Phase 1 concurrent-save verification is no longer needed — flagged in a comment on the handler.
- **`asyncio_mode = "auto"`** in `pyproject.toml` so async tests don't need explicit `@pytest.mark.asyncio` decorations everywhere. Was an open call in the plan; locked here.
- **Snapshot pruning test had to mutate the file between snapshots.** Without changing the file content, every snapshot in a tight loop has the same `(ms, hash4)` tuple and collapses to one filename. Added a per-iteration whitespace tweak so the hash varies. Documented in the test. Not a behavior bug — just a test-construction note.
- **Test for `Source` round-trip explicitly sets `unavailable=True`.** The integrity check correctly flips `unavailable` to `True` on load for fake paths; the round-trip equality only holds if the in-memory side already reflects that. Test fixture sets it up front; the comment explains why.

**Open follow-ups for the reviewer to evaluate:**

1. Add the `PollingObserver` decision to the spec's "Decisions locked" if accepted.
2. Decide whether `StrictModel` rename is worth doing in a focused pass before Phase 2 lands more models.
3. Confirm the `commit_state_to_disk(app)` + `app.state` shape is the API the implementer should be using for the next phase's mutating routes — alternative would be a tighter `state_service` wrapper, but Phase 4 is the right point to introduce that if it's wanted.

**Files touched:**

```
pyproject.toml, .python-version, .gitignore, README.md
clipfarm/__init__.py
clipfarm/models.py
clipfarm/store.py
clipfarm/watcher.py
clipfarm/app.py
clipfarm/routes/__init__.py
clipfarm/routes/state.py
clipfarm/migrations/__init__.py
clipfarm/migrations/v1_to_v2.py
tests/__init__.py
tests/test_store.py
tests/test_migrations.py
tests/test_source_integrity.py
tests/test_load_unknown_keys.py
tests/test_conflict_freeze.py
web/package.json, web/index.html, web/vite.config.ts, web/tsconfig.json
web/tailwind.config.js, web/postcss.config.js
web/src/main.tsx, web/src/App.tsx, web/src/index.css
web/src/pages/{Library,Project,Brief,Settings}.tsx
```

---

## Phase 0 — Environment setup

**Verified by Lillian:** ✅ 2026-05-25 (low-stakes; commands were `brew install ollama ffmpeg uv && brew services start ollama && ollama pull llama3.1:8b`).

**Done (2026-05-25):**

- `brew install ollama ffmpeg uv` — installed all three. `ffprobe` ships in the same FFmpeg bundle (needed for Phase 2 fps probing).
- `brew services start ollama` — the ollama daemon is running on `localhost:11434`.
- `ollama pull llama3.1:8b` — model downloaded (~4.7GB, Q4_K_M quant). `curl localhost:11434/api/tags` returns the model in the list. Not exercised yet beyond presence — first real LLM call lands in Phase 6.
- `uv` is on PATH and used by `uv sync` to manage the Python environment.

**Note:** the spec said Python 3.11, the machine has 3.12 via pyenv. Spec + CLAUDE.md were updated to Python 3.12 in the same session before any code was written. No reason to install a second Python.

**Assumptions:** Lillian's existing `transcribe.py` continues to produce the sidecar shape pinned in spec → "Whisper transcript schema." Verified visually by sampling one of the `05.19.26/*.whisper.json` files; the full model-level validation happens in Phase 2.
