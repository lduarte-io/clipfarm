# ClipFarm Native — macOS Rewrite Technical Plan

*Drafted 2026-07-04 from (a) a full survey of the existing implementation (470 tests, phases 1–10a), (b) AVFoundation/video-engine research, and (c) Swift app-architecture research — all verified against 2025–2026 sources.*

*Updated 2026-07-05 after decision review round 1 with Lillian. Headline changes: **eventual paid distribution** is now a stated goal (Track 2); **no data migration** (fresh native library, re-ingest + re-tag); the web implementation is **reference-only** (not dogfooded); segmentation tail behavior is a **re-runnable setting**, not a migration; export/preview audio is **WYSIWYG by rule**; ingest resequenced earlier (N3). All Future-Ideas items that survived review are now fully-specified phases, not a backlog blob.*

*Round 2 (2026-07-05): D14 refined for the existing iPhone (long-GOP) back-catalog — keyframe ticks + snap-to-keyframe in trim mode, per-cut alignment analysis + an export mode picker in N12, and **smart-cut promoted to its own phase N16** (Track 2 renumbered **N14–N19**). D20 gained the `TranscriptViewAdapter` containment rule. Spec amendments landed in `clipfarm-spec.md`; per-project build rules live in `mac/CLAUDE.md`.*

*Round 3 (2026-07-05): the pre-build adversarial review (`PREBUILD_REVIEW_FINDINGS.md`) was dispositioned — 18 findings accepted and applied; finding 9 resolved by Lillian (**overlap allowed on adjust**, D33); finding 1 resolved by Lillian **flipping D20 to raw NSTextView** (STTextView is GPLv3/commercial, not MIT). Consequences here: N2 gates expanded (incl. a half-day export mini-spike), new D32 geometry policy, N16 rescoped to ffmpeg-mux-primary, per-target concurrency isolation policy, schema/test amendments throughout.*

**Companion document:** [`NATIVE_REWRITE_DECISIONS.md`](./NATIVE_REWRITE_DECISIONS.md) — every decision branch with options, tradeoffs, pick, and current status after review round 1. Every `D#` below points into it.

**How to use this plan:** it is written to be executable by a fresh session with no additional context — each phase names its goal, scope, port sources, tests, and manual-verify criteria. If construction is ever handed off mid-stream, the handoff artifact is this file + `NATIVE_REWRITE_DECISIONS.md` + the standard `PHASES.md`/`COMPLETED_PHASES.md` audit trail.

**Relationship to the spec:** `clipfarm-spec.md` remains canonical for *product behavior* — categories, propagation rules, provenance, views, the three-tier trim vision. This plan governs the *native implementation* and proposes deliberate spec amendments (§9) that land in the spec before construction. None are silent.

**Workflow:** unchanged — plan per phase, one phase at a time, stop for Lillian's manual verify, entries move to `COMPLETED_PHASES.md`, two reviews per phase. Native phases are numbered **N0–N19** (Track 1: N0–N13 = v1; Track 2: N14–N19 = commercial).

---

## 1. Why native — the pain-point → mechanism map

Every recorded pain point from the web build, and the native mechanism that addresses it. This is the justification and also the verification target — each row becomes something to demonstrate.

| Web pain (recorded) | Where recorded | Native answer |
|---|---|---|
| Two alternating `<video>` elements to fake gapless playback; ~100–300ms gap at every cross-source transition | spec "Cross-source preview latency"; `PreviewPane.tsx`; Phase 9/10a carries | `AVMutableComposition` + single `AVPlayer`: N clips from N files become **one virtual asset in one decode pipeline**. The gap does not exist. (D11) |
| Native `ended` event unusable (clips trim before file-end) → `timeupdate` polling vs `effective_end` with 50ms tolerance | `PreviewPane.tsx:19-21` | `addBoundaryTimeObserver` fires at exact `CMTime` boundaries. No polling, no tolerance fudge. |
| "Play button does nothing at clip end", active-attempt race, silent `play()` rejections | hotfix `bf23703` | This class of bug is browser-media-element state-machine friction; a single owned `PlayerEngine` with explicit state eliminates the category. |
| Hand-rolled HTTP Range streaming; `Cache-Control: no-store` disabling seek optimization | `routes/video.py`; PHASES backlog | No HTTP layer at all. The player reads files directly from disk. |
| Keyboard = document-level listeners with input-focus guards; no frame stepping; no ±1ms ergonomics | `PreviewPane.tsx`, `Library.tsx` | Three-layer native keyboard system (menu Commands / focused `onKeyPress` / modal `NSEvent` monitor), `AVPlayerItem.step(byCount:)`, zero-tolerance `CMTime` seeks. (D19) |
| Full-file JSON rewrite on every debounced save; spec already budgeted SQLite "sooner than eventually" | spec "End-state: move to a real database" | GRDB 7 / SQLite from day one — the rewrite *is* the planned migration moment. (D6) |
| Search is word-level substring only; phrase queries return zero hits | spec step 3 | SQLite FTS5: phrase + prefix search over all transcripts, essentially free. (D6) |
| Export never built; FFmpeg concat demuxer snaps to keyframes (not frame-accurate) | step 11 unbuilt; video research | Tiered native export: frame-accurate lossless passthrough for ProRes/all-intra, VideoToolbox re-encode otherwise; WYSIWYG audio rule. (D14, D31) |
| Clips routinely cut short (Whisper `word.end` lops the speech tail) | PHASES backlog, 2026-05-26 dogfood | Segmentation tail policy as a per-library **setting** with a re-apply-per-source action — tunable per speaker, not hardcoded. (D18) |
| Trim Mode (auto-replay loop, keyboard-only precision) deferred to "Future Ideas" — browser couldn't do it well | spec Future Ideas | First-class phase (N11). Boundary-observer loop + zero-tolerance re-seek makes instant-replay-per-nudge real. (D13) |
| Watchdog/conflict-modal/freeze machinery + save-lock/dirty-flag race discipline | `watcher.py`, multiple closed races across phases 6–10a | Dissolved entirely: SQLite transactions replace the whole category. Hand-editability becomes Backup/Export (D7). |

---

## 2. Target architecture

### 2.1 Repo & project layout (D4, D5)

Same repo, new top-level `mac/` directory. The web implementation stays in the tree as the **reference implementation** for porting and golden-master comparison (D27 — it is not dogfooded and never runs as a tool again); delete it whenever convenient post-port.

```
clipfarm/
├── clipfarm-spec.md              # canonical spec (amended per §9)
├── NATIVE_REWRITE_PLAN.md        # this file
├── NATIVE_REWRITE_DECISIONS.md   # decision log
├── clipfarm/ web/ tests/         # web implementation — frozen reference for the port
└── mac/
    ├── ClipFarm.xcodeproj        # thin app shell — Xcode 16+ "buildable folders",
    │                             # so new files never require pbxproj edits
    ├── ClipFarm/                 # app target: SwiftUI views, App struct, menus,
    │                             # NSViewRepresentable wrappers, PlayerEngine glue
    └── ClipFarmKit/              # local SPM package — ~90% of all code lives here
        ├── Package.swift
        ├── Sources/
        │   ├── CFDomain/         # pure logic, ZERO dependencies (fast `swift test`)
        │   ├── CFStore/          # GRDB schema, migrations, snapshots, backup export
        │   ├── CFMedia/          # AVFoundation: probe, composition, thumbnails, waveforms
        │   ├── CFLLM/            # Ollama + Anthropic clients, tagging/naming orchestrators
        │   └── CFExport/         # export tiering, WYSIWYG writer path, ffmpeg (mkv remux)
        └── Tests/                # Swift Testing; one test target per source target
```

Why the split matters for construction: `swift test --filter CFDomainTests` runs the entire ported domain suite in seconds from the CLI with no xcodebuild — the tight loop Claude Code needs. The app target stays thin enough that `xcodebuild -scheme ClipFarm build` is only needed when views change.

**CLI loop** (goes in `mac/CLAUDE.md` at N0):
- Build: `xcodebuild -scheme ClipFarm -configuration Debug build | xcbeautify -q`
- Run: launch the built product binary directly for stdout logs
- Domain tests: `cd mac/ClipFarmKit && swift test` (optionally `--filter`)
- App-target tests: `xcodebuild test -scheme ClipFarm -destination 'platform=macOS'`
- Optional: XcodeBuildMCP for structured build/test tools.

### 2.2 Module layering

```
SwiftUI views ──▶ AppStore (@MainActor @Observable)          [app target]
                    │  reads: GRDB ValueObservation → derived view models
                    │  writes: store methods → UndoManager registration
                    ▼         → CFDomain pure function → CFStore transaction
CFDomain   pure functions + value types. No I/O, no AVFoundation, no GRDB.
CFStore    the ONLY seam to the database (ports the load_state/save_state invariant).
CFMedia    AssetCache, MetadataProbe, CompositionBuilder, PlayerEngine,
           ThumbnailService, WaveformService. AVFoundation lives here and only here.
CFLLM      provider-agnostic chatWithJSONSchema dispatcher + orchestrators.
CFExport   export planning + AVAssetWriter/AVAssetExportSession + swift-subprocess.
```

The existing invariants port directly: pure functions for domain rules; I/O in dedicated modules; a single entry point to persistent state; no globals (the store is created in the `App` struct and injected via SwiftUI `Environment`).

### 2.3 Persistence schema (D6, D7, D8, D28)

GRDB 7, WAL mode, one database per library. Default library location `~/ClipFarm/` (visible, inspectable with `sqlite3`/DB Browser), overridable in Settings (D28).

```sql
meta(key TEXT PRIMARY KEY, value TEXT)            -- schema_version, created_at
sources(id TEXT PRIMARY KEY, filename, path, duration_sec REAL NULL,
        fps REAL NULL, transcript_path TEXT NULL, added_at, unavailable BOOL,
        is_hdr BOOL NULL, natural_width INT NULL, natural_height INT NULL)
clips(id TEXT PRIMARY KEY,                        -- opaque; encodes source__start__end at creation
      source_id REFERENCES sources, start_sec REAL, end_sec REAL,
      transcript_text TEXT, derived_from_clip_id TEXT NULL,
      boundary_edited BOOL DEFAULT 0,             -- set by any hand boundary-correction;
                                                  -- re-apply-segmentation skips these (D18)
      tracks TEXT NULL,                           -- reserved JSON blob, NULL until N18
      created_at)
projects(id TEXT PRIMARY KEY, name, brief_md, script_lines TEXT /*JSON*/, created_at)
project_tags(id TEXT, project_id REFERENCES projects, kind TEXT /*section|line|tag*/,
             name, parent_id TEXT NULL, order_idx INT, PRIMARY KEY (project_id, id))
clip_project_tags(clip_id REFERENCES clips, project_id, project_tag_id NULL,
                  category TEXT, confidence REAL, source TEXT, stale BOOL, notes TEXT,
                  UNIQUE (clip_id, project_id, project_tag_id, category))  -- ports the validator
attempts(id TEXT PRIMARY KEY, project_id, name, parent_attempt_id TEXT NULL,
         source TEXT, premade_bucket TEXT NULL, continuity_score REAL NULL,
         needs_review BOOL, created_at)
attempt_clips(attempt_id REFERENCES attempts, position INT, clip_id TEXT,  -- NOT an FK:
              trim_start_offset REAL, trim_end_offset REAL,                -- tombstones are
              internal_pause_max_sec REAL NULL, notes TEXT,                -- dangling by design
              PRIMARY KEY (attempt_id, position))
voice_annotations(...)                            -- schema parity now, populated at N17
clips_fts                                         -- FTS5 EXTERNAL-CONTENT table over clips.transcript_text,
                                                  -- kept in sync by triggers (split/merge/delete/undo-safe)
settings(key TEXT PRIMARY KEY, value TEXT)        -- PER-LIBRARY settings (segmentation threshold, tail
                                                  -- policy, …) live IN the DB so they travel with the
                                                  -- library (finding 12); app-level prefs → UserDefaults;
                                                  -- API key → Keychain (D23)
```

Notes:
- **All IDs stay strings.** Clip IDs remain opaque-after-creation; `_hms`/`makeClipID` encoding ports exactly (`HH-MM-SS.mmm`, `int(round(t*1000))`, `__` separator, filename constraint unchanged). ID allocators stay monotonic max+1, never reusing freed slots.
- `attempt_clips.clip_id` is deliberately **not** a foreign key — the tombstone pattern (dangling refs on delete, `needs_review` banner) is spec behavior.
- Migrations via GRDB `DatabaseMigrator`, one registered closure per version — the direct analog of `clipfarm/migrations/`, versioned from day one.
- **Inspectability, not live hand-editing** (D7): the watcher/conflict machinery is dissolved. `File → Back Up Library…` exports the full state as JSON in the familiar clipfarm.json v1 shape (git-diffable, restorable); `sqlite3`/DB Browser/Datasette work for live *inspection*. No production JSON importer (D9) — a small **test-only fixture loader** reads the legacy `clipfarm.json` in the test target for golden-master comparisons.
- **Snapshots** (D8): before every destructive op, `VACUUM INTO '.snapshots/<ISO>-<hash>.db'` inside the library folder, pruned to last 50 — same ritual, atomic and WAL-safe. Belt to UndoManager's suspenders (§2.7); snapshots also survive crashes, which the in-memory undo stack doesn't. SQLite mechanics (finding 11): `VACUUM INTO` cannot run inside an open transaction — the snapshot executes in its own barrier access *immediately before* the mutating transaction begins, and partial snapshot files are cleaned up on failure. An *undo* of a destructive op is not itself snapshot-worthy — the pre-op snapshot already covers that state.
- **No data migration** (D9): the native library starts empty; sources are re-ingested from their folders and re-tagged (~30s on the Anthropic path). The lone in-flight web attempt is not worth an importer.
- **Uniqueness enforcement** (finding 10): SQLite unique indexes treat NULLs as distinct, and `project_tag_id` is NULL for every bucket-category row — so **domain validation is the enforcer** (named port test) and the index is made NULL-proof (unique over `COALESCE(project_tag_id, '')` or a generated column).
- The backup exporter emits the documented `script: {lines: []}` shape regardless of the `script_lines` column name.

### 2.4 Time policy (D12)

- **At rest**: `Double` seconds. Timestamps *originate* as floats (Whisper emits float seconds), and a Double carries 15–17 significant digits — sub-microsecond exactness at these magnitudes. Rational storage would add schema complexity without adding truth.
- **In CFMedia**: convert once at the boundary — `CMTime(seconds:, preferredTimescale: 600)` — and do *all* arithmetic in `CMTime`. The drift bug class only exists under iterative Double accumulation; the convert-once rule eliminates it.
- Frame-precise operations use the track's real timing (`minFrameDuration`, `naturalTimeScale`), never `nominalFrameRate` (it's an average — wrong on VFR iPhone footage). `fps == nil` sources keep the spec's 30fps-fallback-with-warning rule.

### 2.5 Playback engine (D11, D13)

`PlayerEngine` (CFMedia, `@Observable`), one instance app-wide, driving one persistent `AVPlayer` in the preview surface (D30 — right-side inspector pane, present on every page; detachable window is a Track 2 nicety).

Composition rules (each is load-bearing; from research):
1. **One `AVMutableCompositionTrack` for video, one for audio.** All ranges inserted back-to-back into the same two tracks (per-clip tracks cause decoder churn and inter-track flicker).
2. **Insert both tracks from the same asset over the same clamped range** (min of audio/video track durations) — mismatched insertions are the classic source of composition drift and tail pops.
3. **Cache `AVURLAsset` per source** with properties pre-loaded. Rebuilding a 50-clip composition is then single-digit milliseconds of edit-list manipulation.
4. **Compositions are immutable snapshots**: every edit builds a fresh composition and swaps via `player.replaceCurrentItem(with:)` — swap while paused where possible, pre-seek the new item to the mapped time with zero tolerance and **await seek completion** before attaching. The black-frame blink on swap is a known, Apple-unanswered report in exactly this configuration, so N2 gates on a *measured* blink count, and **mutating the live composition in place is the designed fallback** (deliberately relaxing this rule) if rebuild-and-swap can't reach zero.
5. **Geometry policy (D32)**: `preferredTransform` is track-level — it cannot render mixed orientations in one track (QA1744). `CompositionBuilder` detects geometry uniformity: uniform → bare composition (Lossless-tier eligible); mixed (portrait iPhone + landscape camera) → attach an `AVMutableVideoComposition` with per-segment `setTransform(_:at:)` instructions (renderSize = project canvas; portrait clips pillarboxed by default) — which also disqualifies passthrough export, since passthrough ignores videoComposition transforms. `automaticallyWaitsToMinimizeStalling = false` for local files.
6. **~10ms `AVAudioMix` volume ramps at cut boundaries** when the "smooth cut audio" setting is on (default on). WYSIWYG rule (D31): the same setting governs export — preview and file always match.
7. Tombstones: skipped by the composition builder, surfaced as an overlay chip in the UI ("▢ removed clip at position N") rather than a playback placeholder.
8. **Color policy (D29, tightened post-review)**: a bare composition has **no documented per-segment tone-mapping guarantee** for mixed HDR/SDR — when dynamic ranges mix, the builder sets explicit videoComposition color properties on BOTH preview and export paths (they ride the same object as the geometry instructions). Left alone, export converts SDR segments *up* to HDR — the opposite of the default-SDR target — so the SDR default is enforced, never assumed. HDR↔SDR seam behavior is an N2 gate.

Engine API surface: `load(ranges:[ResolvedRange])`, `play/pause`, `seek(to:)` (zero-tolerance), `step(frames:)`, `loop(window:)` (trim mode: `addBoundaryTimeObserver` at window end → re-seek to window start; nudges re-arm the observer — **not** `AVPlayerLooper`, which requires teardown per range change, D13), `currentTime` published via periodic observer for the scrubber only.

The resolver (`resolve_attempt` port) stays a pure CFDomain function producing `[ResolvedRange]`; the engine consumes it. Preview and export consume the *same* resolved ranges — the Phase 9 "shared with export" contract ports intact.

**Escape-hatch commitment (D11):** if any N2 gate fails, we pivot to the `AVSampleBufferDisplayLayer`/`AVSampleBufferRenderSynchronizer` custom pipeline **at N2**, while it's cheap — not later with UI stacked on top. For future custom rendering needs (overlays, effects), `AVPlayerVideoOutput` adds per-frame access *on top of* AVPlayer timing without forfeiting the composition architecture.

### 2.6 Keyboard system (D19)

Three layers, one `KeyMap` registry as the single source of truth (drives menus, handlers, and a `?` cheat-sheet overlay). **The registry is serializable from day one** — bindings are data, not code — so user-remappable keys (N19) is a settings UI, not a refactor.

1. **Menu Commands** (SwiftUI `Commands` + `.keyboardShortcut` + `focusedSceneValue`): everything discoverable — Save/Export/Undo/Redo, mode toggles, "Clip → Nudge In-Point Left" etc. Menu items dispatch to the focused editing context.
2. **Focused-view keys** (`.focusable()` + `.onKeyPress`): list/grid navigation, Enter/Escape, arrows, space-to-play within a focused pane.
3. **Modal monitor** (`NSEvent.addLocalMonitorForEvents(matching: .keyDown)`): installed on trim-mode entry, removed on exit. Owns bare `[ ] , .` + Shift/Alt/Cmd+Alt increment modifiers regardless of focus. Rules: never swallow when a text view is first responder; always pass through Cmd+Q/W/Z; return `nil` to consume.

Layer 3 exists because `.onKeyPress` is focus-dependent — a single stray click must not kill `]` nudges mid-trim. This design is what makes the spec's Trim Mode ("no mouse, no scrub wheel") actually buildable.

### 2.7 State, undo, concurrency (D10, D21)

- **AppStore**: `@MainActor @Observable final class`, owning value-type domain state and mediating all mutations. GRDB `ValueObservation` (main-actor-friendly in GRDB 7) feeds derived read models. No TCA (D10).
- **Undo** (D8): every store mutation captures the touched value-subtree *before* mutating and registers inverse application with the window's `UndoManager` — Cmd+Z / Edit-menu / redo for free. UndoManager *is* the platform's command pattern; the commercial-grade investment is coverage and naming ("Undo Split Clip", "Undo Nudge In-Point"), with sensible grouping/coalescing for nudge bursts. Destructive ops additionally take a DB snapshot (§2.3).
- **Concurrency**: Swift 6.2 Approachable Concurrency. **Per-target isolation policy (SE-0466 — SPM packages do NOT inherit the Xcode default):** the app target uses MainActor default isolation; all five ClipFarmKit targets explicitly set `nonisolated` default isolation in `Package.swift` (Approachable Concurrency upcoming-feature flags opted in per-target) so domain functions and services can never silently serialize onto the main thread — and never "fix" a concurrency error by flipping a Kit target to MainActor-default. Keep SE-0461 (`NonisolatedNonsendingByDefault`) symmetric across the app/package boundary. Background work is explicit: `ThumbnailService`, `WaveformService`, `LLMClient`, `ExportService`, later `TranscriptionService` as `@concurrent`/actor services. Subprocesses (ffmpeg remux) via **swift-subprocess** behind an `FFmpegLocator` seam (D16 — PATH/Homebrew now, bundled+signed at N19; **pin the exact pre-1.0 version** — 1.0.0-beta landed 2026-07 with breaking churn — and use the streaming API for ffmpeg's chatty stderr, since collected-output modes throw past the byte limit).
- **Progress**: orchestrators expose `AsyncStream<Progress>` consumed directly by the UI (replaces the web's polling endpoints). Same phase-key granularity (preflight → batching N/M → committing) so the UX ports.

### 2.8 LLM provider layer (D22, D23)

- Hand-rolled `URLSession` + `Codable` clients (~200 lines each), no third-party SDK. The provider-agnostic `chatWithJSONSchema(messages:schema:)` dispatcher contract ports verbatim.
- **Ollama**: `POST /api/chat` with the JSON schema in `format`, `stream: false`, temp 0.2 — unchanged.
- **Anthropic**: switch from forced-tool-use to **structured outputs** (`output_config` JSON schema) — simpler, guaranteed-parse; keep **prompt caching** (`cache_control: ephemeral`) on the shared brief block for the batched-tagging cost win. Verify the exact param shape against current API docs at implementation time (N7); forced-tool-use is the proven fallback.
- **API key in Keychain** (D23); provider/model settings in `UserDefaults`.

---

## 3. Test parity strategy (D25)

The 470-test Python suite is the parity baseline. Route tests lose their HTTP layer (there are no routes anymore) and become **store-method tests** asserting the same contracts: validation errors, snapshot-taken-once, mutation gating, tombstone rules.

**Adjudication rule (locked with D3):** the Python implementation is the *reference*, not the *oracle*. When a ported test fails against the Swift implementation, investigate **which side is wrong against the spec** before "fixing" the Swift code — the web implementation has known warts, and a failing port test is as likely to have surfaced one as to have caught a port bug. Divergences found this way get recorded in the phase entry (and, if behavioral, proposed as spec clarifications).

| Python test file (count) | Swift target | Phase | Notes |
|---|---|---|---|
| test_segmentation (11) | CFDomainTests | N3 | + tail-policy tests (D18: all three modes + re-apply skip rules) |
| test_boundary (33), test_propagation (18) | CFDomainTests | N5 | port edge semantics exactly (below); + `boundary_edited` flag tests |
| test_resolver (14), test_continuity (9), test_continuity_refresh (5) | CFDomainTests | N1/N2 | |
| test_strategies (21), test_premade (11), test_attempt_naming (9) | CFDomainTests / CFLLMTests | N9 | + golden-master vs Python output via test-only fixture loader |
| test_brief (28), test_projects (14) | CFDomainTests | N6 | all 3 dogfood tolerance hacks port |
| test_tagging (25), test_llm (12), test_llm_anthropic (10) | CFLLMTests | N7 | validation rules identical |
| test_take_grid (14), test_search (13) | CFDomainTests / CFStoreTests | N8/N4 | search semantics upgrade to FTS5 (new tests) |
| test_store (16), test_migrations (4), test_models_round_trip (4), test_uniqueness_validator (7), test_source_integrity (3), test_settings (7) | CFStoreTests | N1 | |
| test_load_unknown_keys (6) | — narrowed | N13 | tolerance semantics apply to the Backup *restore* path only |
| test_ingest (11), test_ffprobe (9), test_whisper_validation (6), test_transcripts (7) | CFStoreTests / CFMediaTests | N3/N4 | ffprobe tests become MetadataProbe tests |
| test_routes_* (~141) | CFStoreTests (store-method contracts) | per phase | HTTP/status-code assertions map to thrown-error assertions |
| test_conflict_freeze (2) | — dropped | — | watcher dissolved (D7) |

**Load-bearing semantics that must port bit-exactly** (each gets a named test; subject to the adjudication rule above):
- Silence segmentation: new clip when gap `>=` threshold (2.0s default, now a setting); tail policy per D18.
- Internal-pause expansion: split when gap `>` max (strictly greater); gap dropped entirely; word filter `w.start >= start && w.end <= end` (straddle exclusion — port as-is, fix scheduled N15).
- Overlap tests are **half-open `[s,e)`** — touching endpoints don't collide. **Overlap policy (finding 9, resolved by Lillian 2026-07-05, D33): create-from-range AND adjust-boundaries allow overlap; only merge rejects it** (undefined for overlapping ranges). Deliberate divergence from the Python reference, which rejected all overlap on adjust and thereby froze deliberately-overlapping clips — port the *new* rule, with a named test for the previously-frozen case.
- Tag dedup on merge: key `(project_id, project_tag_id, category)`, first-encountered wins.
- Trim clamping: the four propagation cases including the pathological zero-both-offsets-and-warn case; negative offsets never clamped against base.
- Tagging validation: hallucinated-ID drop, confidence clamp to [0,1], **on-script-with-null-line demoted to related-but-different** (not dropped), one retry per batch, partial-batch wins.
- Continuity: runs = same source AND forward progression; score = max-run-runtime / total; error-equivalents on empty/all-orphan/zero-runtime.
- ID allocation: monotonic max+1 over all existing keys, never reuse freed slots.

**Golden-master checks** (N3, N9): a test-only fixture loader reads the legacy `clipfarm.json` (and/or a freshly Python-ingested state) so Swift strategies/resolver/segmentation can be diffed against Python output on identical inputs while both implementations coexist. Cheap insurance against subtle port drift.

**Undo coverage** (finding 15): every store mutation lands with a register→undo→redo test — drive `UndoManager` directly against store methods, asserting both domain state and the DB round-trip after each direction.

---

## 4. Track 1 — v1 phase plan (N0–N13)

Each phase ends with Lillian's manual verify, per the standing workflow. "Port map" names the Python source of truth.

---

### N0 — Toolchain & skeleton

**Goal:** the stack works end-to-end before any feature work (native analog of web Phase 1).

**Scope:** `mac/` layout per §2.1; `ClipFarm.xcodeproj` (buildable folders, automatic signing **with a real Apple Development certificate** — ad-hoc "Sign to Run Locally" re-signs each build and re-triggers TCC folder prompts — bundle id `org.duartes.clipfarm`, **non-sandboxed**, min target macOS 26 — D2, D24); `ClipFarmKit` package with the five targets + Swift Testing smoke tests; GRDB 7 dependency; `mac/CLAUDE.md` documenting the CLI build/test/run loop; empty main window with the nav skeleton (Library / Project / Script / Attempts / Brief / Settings) and the inspector pane slot.

**Verify:** app launches; `swift test` green from CLI; `xcodebuild build` clean; adding a new Swift file requires no pbxproj edit.

---

### N1 — Domain models + persistence core

**Goal:** the data layer exists, tested, before anything sits on it.

**Port map:** `models.py` → CFDomain structs (field-for-field, including `Source.unavailable`, `Attempt.needs_review`, `TagKind.tag`, plus new `Clip.boundary_edited`; adopt `script` naming, amend spec §9). `store.py` → CFStore (schema §2.3, DatabaseMigrator, snapshot service via `VACUUM INTO` + prune-to-50, uniqueness via unique index + validation, source-integrity check on open). `resolver.py`, `continuity.py` → CFDomain (pure; needed by N2). Settings scaffolding (per-library settings table in the DB; app-level prefs in UserDefaults; Keychain for the key). Snapshot sequencing per SQLite mechanics (§2.3): snapshot in its own barrier access *before* the mutating transaction, partial-file cleanup on failure. Also build the library **close→swap→reopen** path here (clears the UndoManager stack, restarts ValueObservations) — snapshot-restore, backup-restore, and library switching all reuse it later.

**Tests (~90):** models round-trip, uniqueness, store/snapshot/migrations, source integrity, resolver (14), continuity (14), fixture builders for everything downstream.

**Verify:** create a scratch library; snapshot fires before a destructive op and prunes correctly; `sqlite3 ~/ClipFarm/clipfarm.db .schema` shows the expected shape.

---

### N2 — Playback engine (the de-risking spike)

**Goal:** prove the thesis of the rewrite before building UI on it. This phase exists to fail fast if any research assumption is wrong.

**Scope:** CFMedia — `AssetCache`, `MetadataProbe` (async `load(.duration/.nominalFrameRate/.formatDescriptions/.naturalSize/.preferredTransform)` + HDR detection), `CompositionBuilder` (rules §2.5 incl. audio micro-fades — the "smooth cut audio" per-library setting gets its `LibrarySettings` accessor here, on N1's settings table), `PlayerEngine` (full API §2.5 incl. loop mode). Debug-only harness: hand-specified `(file, start, end)` ranges over real files from the dogfood folder — no ingest needed yet. *(N1 delta: the engine consumes N1's `[ResolvedItem]`; the harness may bypass the resolver with raw ranges and passes a nil `transcriptProvider` — sidecar loading arrives N3/N4.)*

**Exit criteria (hard gates — measured, not eyeballed; expanded per the pre-build review):**
- **Seam-drop instrumentation**: 20+ deliberately non-keyframe-aligned cuts across ≥3 files (ProRes + H.264 + HEVC, incl. 4K and one iPhone HDR clip); capture frame-delivery timestamps; gate p95 inter-frame gap at seams ≤ 1 frame duration. If seams drop, A/B Apple's documented mitigation — two alternating video tracks in one composition — before anything custom.
- **Swap-blink count**: 100 edit→rebuild→pre-seek→swap cycles under screen capture, A/B'd against mutate-in-place; gate = zero visible blinks on whichever strategy wins (the winner becomes the PlayerEngine contract).
- **Mixed-rotation render** (D32): portrait iPhone + landscape camera in one composition renders correctly via videoComposition; record what passthrough does with it.
- **HDR↔SDR seam probe** (D29): alternating HLG/SDR segments, with and without videoComposition color properties, pixel-probed in preview *and* a Standard-tier export; gate = no visible shift and preview == export.
- **Rebuild + end-to-end edit latency**: composition rebuild < 10ms for 50 clips (warm asset cache), and the number that actually matters — edit → new item `readyToPlay` → first frame — measured on the same 50-clip composition.
- **Frame accuracy**: cut boundaries land frame-accurately (spot-check against source timecodes); `step(byCount:)` works across a composition.
- **Worst-case trim-loop restart**: boundary-fire → first rendered frame at window start on long-GOP 4K HEVC with a non-keyframe-aligned window; meet 50ms or formally revise the budget. The PlayerEngine contract states the re-arm discipline (boundary observers die on every `replaceCurrentItem` — re-register after every edit) plus a periodic-observer end-of-window verification as belt-and-suspenders against missed fires.
- **Micro-fades**: audibly kill cut pops without softening speech onsets.
- **Export mini-spike (half a day, finding 4)**: (a) passthrough export of a two-file H.264 composition with non-keyframe cuts — does it succeed at all (rdar://10421720), and does it author edit lists or snap to sync samples? (b) hybrid-writer sequential sessions — are lead-in frames edited out for segments 2..N, or only the first? (fallback if not: per-segment writes stitched with `AVMutableMovie`); (c) quick elst A/B of one output in QuickTime / VLC / Chrome / a Resolve import. Answers to (a) and (b) choose N12's architecture before any UI depends on it.

**If gates fail:** playback gates → pivot to the sample-buffer pipeline **now** (D11 commitment) — re-plan N2 as a 4–8-week engine phase and shift everything right, rather than discovering it at N10. Export-spike surprises → re-architect N12 (writer-per-segment + `AVMutableMovie`) while it's still on paper.

**Verify:** Lillian watches a multi-source (camera + iPhone) assembly play gapless.

---

### N3 — Ingest

**Goal:** point at a folder, get sources + clips — natively, with segmentation as a tunable setting. Real data lives in the native app from here on (D9: no importer; this is how the library gets populated).

**Port map:** `ingest.py` (pairing, rejection semantics: `__`-in-stem hard-reject with rename offer; sidecar soft-fail → footage-only; re-ingest upgrade path), `segmentation.py`, duration policy (sidecar wins → probe → null).

**Scope:** default ingest location is the **footage inbox `~/ClipFarm/Footage/`** (D34 / spec amendment 14 — created on first run, outside any cloud-sync path; the picker defaults there; the inbox is a managed working folder, so sources may later be deleted from it — the unavailable-source greying is the safety net); `NSOpenPanel` folder picker + drag-a-folder-onto-window; `MetadataProbe` replaces ffprobe (D17) — *delivered at N2: consume `MetadataProbe.probe(url:)`, don't rebuild; N2 deltas: `minFrameDuration` is frame-math-only, never surfaced as "the fps" (VFR files read absurdly high that way — display fps is `nominalFrameRate`), and sources may be natively-portrait-encoded with extra audio/data tracks* — HDR flagged per source (D29, also free from the N2 probe); **`.mkv` remuxed to `.mp4` at ingest** via `ffmpeg -c copy` through swift-subprocess + `FFmpegLocator` (D15, D16 — AVFoundation cannot open Matroska; the remuxed `.mp4` lands as a sibling of the original with the same stem, skip-if-exists; `sources.path` records the `.mp4` and the original `.mkv` path is kept as provenance — default reviewable in this phase's plan); **segmentation settings** (D18): silence threshold (default 2.0s) + tail policy (extend-to-next-word-start default / fixed padding / word-end), both per-library, plus a **"Re-apply segmentation settings" action per source** — recomputes boundaries for auto-detected clips, *skips any clip with `boundary_edited` set*, snapshot-protected, undoable; per-source **waveform generation** (AVAssetReader + Accelerate, ~50–100 buckets/sec, binary cache file — every clip's waveform is then a free slice), run **asynchronously post-ingest** by WaveformService so a full-folder ingest never blocks on audio decode (N11 degrades gracefully when a strip isn't ready yet); FTS5 rows written at ingest.

**Tests:** ingest (11), segmentation (11 + tail-policy modes + re-apply skip rules), probe tests, golden-master segmentation diff vs Python on the same folder (tail policy = word-end for comparability).

**Verify:** ingest the footage inbox (`~/ClipFarm/Footage/` — Lillian drops files in herself; the golden-master count comparison needs the inbox to contain files the web version also processed) — counts match the web version modulo tail policy; clips no longer feel cut short; flip tail policy, re-apply on one source, hear the difference, undo it.

---

### N4 — Library (transcript browser + search)

**Goal:** the manual escape hatch, native. Browse any recording without watching it linearly.

**Port map:** `transcripts.py` (mtime-keyed cache), whisper sidecar validation; `search.py` semantics superseded by FTS5.

**Scope:** source sidebar (unavailable greyed out; footage-only badge); transcript view as a **raw NSTextView / TextKit 2 wrapper** (D20 — flipped at the pre-build disposition: STTextView is GPLv3/commercial and Lillian chose zero license entanglement) — word-level ranges, inline clip-boundary highlighting, click-word, drag-select — still contained behind the **`TranscriptViewAdapter` seam** (set content / word hit-test / highlight ranges / selection events / scroll-to-word) as module hygiene; budget the few extra days of selection/highlight plumbing STTextView would have provided; FTS5 search UI with phrase + prefix support (upgrade over web, new tests); click clip → inspector pane plays it via PlayerEngine; deep-link plumbing (select source + scroll to word) for later grid → library navigation.

**Verify:** browse btc.0.4 smoothly (30-min transcript, no typing/scroll lag); phrase-search `"self custody"` returns hits (web returned zero); click-to-play is instant.

---

### N5 — Boundary correction + system undo

**Goal:** split / merge / adjust / create / delete with real undo — the escape hatch when segmentation is wrong.

**Port map:** `boundary.py` (33 tests), `propagation.py` (18), clip-ID encoding, `clamp_attempt_trims_for_clip` four-case clamp, mm:ss↔seconds input parsing from `Library.tsx`.

**Scope:** all five ops as store methods wired to UndoManager (every op undoable, named — "Undo Split Clip"; DB snapshot before each, reasons named as today); every op sets `boundary_edited` on affected clips (D18); UI in the Library transcript view — click-between-words split, multi-select merge (same-source, non-overlap), boundary drag/nudge (`[ ] , .` at 100ms, Shift 10ms — base-level), create-from-selection (+ numeric range entry for footage-only sources, overlap allowed per the Phase 10a revision), delete with confirm. Propagation exactly per spec: clone-tags-stale-true on split, union-merge on merge, `needs_review` flags, tombstones. **Overlap policy (D33, Lillian 2026-07-05): adjust allows overlap** — matching create; only merge rejects overlapping ranges. FTS5 stays in sync through every op (external-content triggers) — test: search reflects a split/merge/delete.

**Verify:** split a clip mid-take on real data, Cmd+Z restores it perfectly; merge two takes; verify a snapshot file appeared; re-apply-segmentation skips the hand-corrected clip.

---

### N6 — Projects + brief editor

**Port map:** `brief.py` (28 tests — including all three dogfood tolerance hacks: dedent, preamble-before-frontmatter, loose-list rewrite), `projects.py` name-keyed tag merge (section=name; line=(section, text, occurrence); adhoc=name; surviving identities keep IDs), update-stales-all-rows, delete-hard-deletes-attempts.

**Scope:** brief editor page (plain text editor is fine here), debounced live parse preview, create/update/delete with staling.

**Verify:** paste the chrysalis brief verbatim — parses; edit it — tags stale correctly, IDs preserved for surviving lines.

---

### N7 — LLM tagging

**Port map:** `tagging.py` (25 — every validation rule), `llm.py`, `llm_anthropic.py`, `settings.py`.

**Scope:** CFLLM clients per §2.8; Settings page (provider radio, model dropdowns, key set/test/clear → Keychain, ping with specific error surfacing); tagging orchestrator with identical validation/retry/stale-drop semantics; `AsyncStream` progress panel (provider·model chip, per-batch N/M, ETA). The save-lock/dirty-flag race machinery from the web version dissolves — a long tag run writes rows transactionally at commit and never blocks reads (WAL).

**Verify:** live tag run on a real project — Anthropic path ~30s, Ollama path works, progress panel live; kill the app mid-run → no partial rows.

---

### N8 — Take grid + Script TOC

**Port map:** `take_grid.py` (sort orders: confidence DESC + start ASC in lines; start ASC in buckets; empty lines visible; summary counter semantics), deep-link behavior.

**Scope:** `ThumbnailService` (AVAssetImageGenerator — wide tolerance for grid thumbs so it grabs cheap keyframes, `maximumSize` ~2× display, disk+NSCache keyed `(source, roundedTime, size)`); Take Grid page (LazyVGrid, per-line rows, thumbnail cards with provenance + category badge + confidence + stale dot, four buckets); Script TOC page (outline, collapsible, takes inline); side-panel detail with Open-in-Library deep-link; active-attempt "adding to" concept + `+` on cards.

**Verify:** scan 10 deliveries of one line side-by-side with thumbnails; grid scrolls smoothly at full library scale (6k clips).

---

### N9 — Attempts + premade generation

**Port map:** `strategies.py` (all 8 + `_detect_takes` with parameterized tolerance sets + caps + coverage constants), `premade.py` (dedup-first-wins, replace-only-ai-premade, ID allocation), `attempt_naming.py` (single batched call, per-name canned fallback, llm/canned/mixed), `attempts_ops.py` (create/fork/rename/replace-clips with the four tombstone rules/delete-with-dangling-parent).

**Scope:** Attempts page — two buckets, continuity bar (green/amber/red thresholds as today), fork/rename/delete/set-active, drag-to-reorder (optimistic + undoable), regenerate with confirm; **golden-master test**: Swift strategies vs Python strategies on identical fixture state must produce identical clip lists (adjudicate divergences per §3).

**Verify:** generate premades on the real project; fork + reorder + undo works; watch a fork play through gapless.

---

### N10 — Attempt editing (the never-built web-10b scope)

**Goal:** everything web Phase 10b was going to be, but native — greenfield, no port reference for the UI.

**Scope:** replace-this-clip picker (siblings of same line tag, confidence DESC, one-click swap); tombstone replacement flow (pick replacement → swap in → clear `needs_review`); **"tighten internal pauses" toggle** per attempt-clip (`internal_pause_max_sec` = 0.5s default; resolver + engine honor it since N1/N2 — this is just the affordance); duplicate-clip-in-attempt allowed; live composition rebuild on every edit (edit → new composition → seamless swap); `needs_review` banner with jump-to-slot.

**Verify:** the Chipotle-line flow end-to-end — pick takes top to bottom in Script TOC, tighten pauses on two clips, replace one clip from siblings, watch the result instantly.

---

### N11 — Trim mode (the native headline)

**Goal:** the spec's Future-Ideas Trim Mode, promoted to v1 — the single biggest "why native" payoff (D13, D19).

**Scope:**
- Enter on any attempt-clip (from N10 UI) or any base clip (Library): modal state, NSEvent monitor installed, UI chrome dims to an edit-point HUD with the waveform strip (slice of the N3 per-source waveform) centered on the boundary.
- **Auto-replay loop**: 1–2s window centered on the edit point, boundary-observer loop (§2.5); every nudge instantly re-arms and replays. Spacebar pauses/resumes the loop.
- **Nudge keys**: `[` `]` in-point, `,` `.` out-point; increments 100ms / Shift 10ms / Alt 1ms / **Cmd+Alt ±1 frame** (track `minFrameDuration`; fps-null sources → 30fps + one-time warning per spec).
- **Keyframe awareness (D14)**: the HUD strip shows the source's keyframe (sync-sample) ticks, and an optional **snap-to-keyframe** nudge mode lands cuts exactly on keyframes for users who want the lossless-export guarantee (ticks make the placement tradeoff visible — iPhone keyframes are typically ~1s apart). Positions come from `KeyframeMapService` (AVSampleCursor sync-sample enumeration, cached per source).
- **Direct numeric entry**: `-50ms`, `+0.5s`, `+2f`.
- **Permissiveness buttons**: more-generous/tighter per side, configurable step (% or absolute).
- Attempt context → writes `trim_*_offset` (per-attempt, base immutable); Library context → boundary correction with full propagation (+ `boundary_edited`). Same engine, two write targets — the spec's boundary-correction vs per-attempt-trim distinction made physical.
- All nudges undoable, coalesced per burst (one undo step per "settled" adjustment).

**Verify:** the spec's pitch, literally: enter trim mode, clip loops in your ear, two taps of `]`, perfect, next clip. Whole-attempt cleanup in minutes without touching the mouse.

---

### N12 — Export

**Goal:** full-quality MP4 out (web step 11, never built), with the WYSIWYG rule (D14, D29, D31).

**Scope:** CFExport —
- Resolve attempt (same resolver as preview) → analyze source codecs/color → pick tier:
  - **Tier 1 — passthrough** (lossless + frame-accurate): eligibility = all sources ProRes/all-intra or every cut keyframe-aligned, **AND codec/parameter-uniform** (mixed iPhone HEVC + camera H.264 can never share one passthrough output track), **AND geometry-uniform (D32) AND color-uniform (D29)**; available only when "smooth cut audio" is off *or* via the hybrid path below. H.264 passthrough carries the edit-list caveat (frame-accurate in Apple players; ignoring demuxers don't just show lead-in frames — they desync A/V and misreport duration) — surfaced in the dialog, re-encode offered as the safe default for H.264. MP4-container passthrough requires a non-nil `sourceFormatHint` (`.mov` is the permissive container).
  - **Tier 1.5 — hybrid writer (the WYSIWYG path, D31)**: `AVAssetReader`/`AVAssetWriter` copying video samples untouched while decoding→micro-fading→re-encoding audio (high-bitrate AAC, effectively transparent; hand-encoded AAC carries `TrimDurationAtStart` priming attachments or A/V drifts ~one frame). Video losslessness preserved. Frame accuracy here is edit-list-based, so it inherits Tier 1's demuxer caveat: **"preview == file" is universal on Standard, Apple-player-verified on Lossless/hybrid** — the "?" explainer says so. If the N2 spike shows sequential writer sessions don't edit out lead-ins for segments 2..N, the fallback architecture is per-segment writes stitched with `AVMutableMovie`.
  - **Tier 2 — re-encode**: `AVAssetWriter` + VideoToolbox at high bitrate. Always used for mixed-codec or mixed-color-space source sets. **Color policy (D29)**: export dialog offers output target — default **SDR** for mixed material (sane for web/social talking-head content), HDR available when sources allow.
- Modern async API (`export(to:as:)` / writer equivalents, `states(updateInterval:)`-style progress); post-export verification pass (duration sum matches resolved ranges; boundary spot-check); provenance metadata stamped into the output.
- **Export mode picker + per-cut analysis (D14)**: the dialog reports keyframe alignment per cut ("11 of 14 cuts are keyframe-aligned", from `KeyframeMapService`) and offers **Standard** (re-encode — WYSIWYG, universal; default for long-GOP sources) vs **Lossless** (passthrough — offered clean when all cuts are keyframe-aligned or sources are all-intra; otherwise carries the edit-list warning). **Smart** mode slots in with N16. Every mode and the per-cut report carries a hover-"?" plain-language explainer (spec UX requirement — outcomes, not codec jargon; "keyframe" terminology stays in trim mode's power-user ticks; Standard always cuts exactly on the chosen frame).
- FCPXML lives in the N19 grab-bag; smart-cut is its own phase (N16).

**Verify:** export the best real attempt; frame-check three cut points in QuickTime; A/B preview vs exported file at two cut points with fades on — identical; a ProRes passthrough export is bit-identical in a copied region; a mixed camera+iPhone export lands SDR by default and looks right.

---

### N13 — v1 close-out

**Scope:** full parity audit (one row per §1 pain point + one per spec "What the app must let me do" bullet); golden-master reruns; **`File → Back Up Library…`** (JSON export in the clipfarm.json v1 shape) + restore path with log-and-skip tolerance for unknown keys — **restore = replace the whole library** (confirm dialog + automatic pre-restore snapshot; never a merge); **Settings → Restore snapshot** (the spec's promise — reuses the N1 close→swap→reopen path, clears the undo stack, restarts observations); land the spec amendments (§9) in `clipfarm-spec.md` + rewrite CLAUDE.md's invariants/stack/structure sections for the native app; archive or delete the web implementation at Lillian's discretion (its reference job ends when golden-masters pass).

**Verify:** one full real editing session — ingest a new recording, brief, tag, assemble, trim, export — native only, keyboard-heavy, and it feels *good*.

---

## 5. Track 2 — commercial track (N14–N19)

Locked as real phases per review round 1: the goal is a **paid, directly-distributed app** eventually. v1 (Track 1) is built for Lillian; Track 2 hardens it for customers. Each phase below is specified to be executable by a future session. Sequencing within Track 2 is adjustable; N14 gates commercial viability (customers can't run transcribe.py) and should go first.

### N14 — In-app transcription (WhisperKit)

**Scope:** `TranscriptionService` on WhisperKit (SPM, macOS 14+): word-level timestamps (`WordTiming {word, start, end, probability}` maps ~1:1 onto the existing sidecar schema — WhisperKit output is *written as* `.whisper.json` sidecars, keeping one interchange format and full transcribe.py compatibility); model management UI (download large-v3-turbo on first use, storage location, progress); ingest integration — untranscribed video → "Transcribe now" per source or auto-transcribe-on-ingest setting; background queue with progress; re-transcribe affordance. Quality note: large-v3-turbo is an *upgrade* over the current faster-whisper `small`. Apple `SpeechTranscriber` (macOS 26) evaluated as a "fast draft" option only if its word-timing quality proves out on real recordings — benchmark, don't assume. **Acceptance gate (finding 16):** golden-master WhisperKit word timings against existing `transcribe.py` sidecars on the dogfood folder — timing *quality*, not just WER (turbo's pruned decoder has fewer alignment heads); verify the leading-space word convention matches faster-whisper's (or normalize in the sidecar writer); if SpeechTranscriber is ever wired, its run-level AttributedString timings need a per-word flattening adapter.

**Verify:** drop a bare .mov with no sidecar → transcribed in-app → segmented → browsable, no terminal involved.

### N15 — Polish layer: three-tier aggressiveness (the spec's headline differentiator)

**Scope:** builds directly on N11's engine + N3's waveforms.
- **Tier 1 — global aggressiveness**: one per-attempt slider generalizing `internal_pause_max_sec` across all clips (plus breath/filler sensitivity later); resolver recomputes sub-ranges live, preview updates on release.
- **Tier 2 — section microadjust**: per-clip (or per-sub-range-span) "a little more generous / a little tighter" buttons that bias the local threshold relative to global; recompute + instant replay of the affected span. This is the 30-minutes-to-30-seconds feature — the two buttons, not a scrub wheel.
- **Tier 3 — frame nudge**: already exists (N11); integrated into the same HUD.
- Also lands here: the word-straddle boundary filter fix (words spanning a trim boundary participate in gap detection correctly) — with tests replacing the ported-as-is behavior.

**Verify:** the spec's one-liner — one slider one-shots the video, two taps fix the two tight spots, two frames of nudge on one cut, whole-video cleanup in under a minute.

### N16 — Smart-cut export engine

**Goal:** Lillian's "move the keyframe to where the cut is" — frame-accurate, ~lossless, universally-compatible export for long-GOP H.264/HEVC. The answer for the existing iPhone back-catalog (D14).

**Scope:** for every cut that isn't keyframe-aligned, decode and re-encode **only** the span from the cut point to the next keyframe (creating a new keyframe exactly at the cut); stream-copy every other sample. **Primary mux path: ffmpeg, behind the existing `FFmpegLocator`** (finding 5) — every credible reference implementation muxes through ffmpeg/libav; there is no documented AVAssetWriter support for appending heterogeneous format descriptions to one input, and multi-stsd MP4s demux poorly in the wild. Encode boundary GOPs with VideoToolbox to matched parameters; hand ffmpeg the splice. A pure-AVAssetWriter matched-parameter splice is the **stretch goal**, not the plan. (Consequence for D16/N19: the ffmpeg dependency is effectively permanent — bundle the signed LGPL build.) Reference implementations to study: `skeskinen/smartcut` (Python/PyAV — the cleanest; unmaintained since 2026-02), LosslessCut's experimental smart-cut, `avcut`. Ships as the **Smart** option in the N12 export mode picker. Acceptance gate: a player-compatibility matrix (QuickTime, VLC, Chrome, YouTube ingest) — if splice robustness can't be proven beyond the Apple ecosystem, the mode ships labeled "Apple-verified" and Standard remains the universal default.

**Verify:** export an iPhone H.264 attempt in Smart mode — cuts frame-exact; total re-encoded span ≤ a few seconds per cut with the stream bit-identical elsewhere; plays clean in QuickTime, VLC, and Chrome.

### N17 — Voice annotations

**Scope:** per the spec's design (trigger-phrase required): configurable wake word (default `"clipfarm"`); detector scans transcript words at ingest/transcription for wake-word onset → captures to next sentence boundary → `voice_annotations` rows with `resolved_clip_id` = immediately-preceding clip; review UI (annotation inbox: accept → creates tag/note on the clip, dismiss, edit target); annotations surfaced on clip cards + filterable. Explicitly per spec: if trigger-phrase ergonomics feel awkward on a real shoot, this ships dark (feature-flagged) rather than half-good.

**Verify:** record a test clip saying "clipfarm: good line, save that for section C" → annotation appears in inbox attached to the right clip.

### N18 — Per-clip media composition (`tracks` activation)

**Scope:** the reserved `tracks` schema hook goes live, additively (no migration needed by design):
- **Audio override**: sync external audio (separate-mic capture) to a clip — offset estimation by waveform cross-correlation (Accelerate FFT over the N3 waveforms) with manual nudge fallback; `CompositionBuilder` swaps the audio segment; export honors it.
- **Video swap (keep audio)**: replace a clip's video with a range from another source (B-roll/cutaway) — `tracks.video_override`, builder inserts video from the override source + audio from the original.
- **Blackout/placeholder overlays**: `(start, end, color)` ranges rendered via `AVMutableVideoComposition` (or `AVPlayerVideoOutput` overlay) in preview and burned in re-encode export — "the hole where B-roll goes."
Deliberately basic per spec: assembly aids, not creative effects — Resolve keeps the polish layer.

**Verify:** replace one clip's camera-mic audio with a synced external recording; black out a 3s span; both survive preview and export.

### N19 — Commercial hardening & distribution

**Scope** (the "ship it to strangers" phase; several items are packaging, not architecture, *because* the seams were built earlier):
- **Signing/distribution (D24)**: Developer ID certificate, hardened runtime, notarization; **direct distribution** (not Mac App Store — sandboxing is hostile to this app's file model); Sparkle for updates.
- **Bundled dependencies (D16)**: signed LGPL ffmpeg build inside the bundle, swapped in behind `FFmpegLocator` — the dependency is effectively permanent now that N16 smart-cut muxes through it (the old "eliminate by gating .mkv" branch is dead); WhisperKit model download UX hardened (checksums, resume, offline messaging).
- **Payments/licensing**: evaluate Paddle vs Lemon Squeezy (both handle tax/VAT as merchant of record — the right shape for a solo dev); license-key validation kept simple and offline-tolerant. Pricing/API-token economics (bundled Anthropic proxy vs bring-your-own-key) is a business decision — **bring-your-own-key + Ollama default keeps v-commercial-1 simple and is the pick until real users say otherwise**.
- **First-run experience**: onboarding (pick library folder, TCC folder-access prompt with explanation, optional model download, sample project); empty states audited.
- **User-remappable keys (D19 payoff)**: settings UI over the serializable KeyMap registry; conflict detection; reset-to-defaults.
- **Reliability**: opt-in crash reporting; `sqlite3` integrity check on open; backup-on-update.
- **Grab-bag** (schedule within N19 or as N19.x as energy allows): cross-project clip surfacing UI (badge + "also in…" popover — data model already supports it); FCPXML export; `energy_shift` upgrade from words-per-second proxy to real audio analysis (waveforms make this cheap); min-macOS floor revisit for the customer base.

**Verify:** a clean Mac (or fresh user account) with nothing installed: download DMG → open → onboard → ingest → transcribe → assemble → export, no terminal, no Homebrew, no prompts beyond the expected TCC one.

---

## 6. Performance budgets

Measured at N2/N4/N8 gates, re-checked at N13:

| Operation | Budget |
|---|---|
| Composition rebuild, 50 clips (warm asset cache) | < 10 ms |
| Zero-tolerance seek, local file | < 50 ms |
| Cross-source playback transition | imperceptible (0 added latency) |
| Trim-mode nudge → loop restart | < 50 ms perceived |
| FTS5 phrase search over full library | < 50 ms |
| Take-grid scroll at full library scale (~6k clips) | 60 fps, thumbnails lazy |
| Cold launch to interactive library | < 2 s |
| In-app transcription (N14, large-v3-turbo, Apple Silicon) | ≥ 10× realtime |

## 7. Risks & mitigations

1. **Composition playback assumption fails** → N2 is a gated spike right after the data layer; escape hatch (sample-buffer pipeline) triggers *at N2* by commitment, not later (D11). Gate list expanded per `PREBUILD_REVIEW_FINDINGS.md` (geometry, HDR seams, swap-blink, export mini-spike) so media surprises surface at N2, not N10–N12.
2. **SwiftUI perf at library scale** (6k cards, 30-min transcripts) → transcript view is TextKit 2 from day one (not retrofitted); grid budgeted at N8 with NSCollectionView wrap as fallback.
3. **Port drift in subtle domain rules** → bit-exact semantics list (§3) + golden-master diffs + the adjudication rule (Python is reference, not oracle — spec decides).
4. **H.264 passthrough compatibility** (edit-list handling outside Apple players) → tier logic defaults H.264 to re-encode; passthrough is opt-in with the caveat surfaced; keyframe ticks + snap-to-keyframe make true-lossless available case-by-case, and N16 smart-cut is the systemic fix.
5. **Mixed HDR/SDR footage** (already real — iPhone + camera) → flagged at ingest, native tone-mapped preview, explicit export color target defaulting SDR (D29).
6. **Xcode/CLI friction for agent-driven construction** → buildable folders + SPM core means 90% of iteration is `swift test`; app-target churn is thin.
7. **Session/context limits during construction** → this plan + the decisions doc are the standing handoff artifact; each phase is self-contained; `PHASES.md` discipline captures in-flight state at every stop.

## 8. What we explicitly do NOT port

The HTTP/API layer (routes, Range streaming, status-code mapping); the two-`<video>` preview and all its workarounds; the watchdog/conflict-freeze machinery and the save-lock/dirty-flag discipline (replaced wholesale by DB transactions); polling progress endpoints; React/Vite/Tailwind entirely; the absolute-path ingest text field; the production JSON importer (test-only fixture loader instead).

## 9. Proposed spec amendments (land in `clipfarm-spec.md` before N0)

Per the CLAUDE.md rule — divergence is proposed explicitly, never silent:

1. **Storage**: `clipfarm.json` → SQLite (GRDB) as source of truth; the spec's own "End-state: move to a real database" section is hereby executed.
2. **Hand-editability → inspectability + backup**: live hand-editing of state is retired (it was ~never used in practice, and is wrong for a distributed app). Replaced by: external SQLite inspection tools, `File → Back Up Library…` JSON export in the familiar shape, and a tolerant restore path. The watcher/conflict-modal invariant dissolves with it.
3. **Snapshots**: file-copy of JSON → `VACUUM INTO` DB snapshot, same before-every-destructive-op ritual, same keep-50; **plus** first-class in-memory undo (UndoManager), which the spec never had.
4. **Export mechanism**: FFmpeg concat demuxer → native tiered export behind a mode picker (Standard re-encode / Lossless passthrough / Smart), with a WYSIWYG audio rule, an explicit output color target, **per-cut keyframe-alignment visibility + an optional snap-to-keyframe trim mode**, and a smart-cut engine (re-encode only the cut GOPs — N16) for lossless-grade long-GOP export. FFmpeg's remaining role: `.mkv` remux at ingest (bundled at N19).
5. **`.mkv` handling**: accepted at ingest, remuxed to `.mp4` (lossless stream copy) because AVFoundation cannot open Matroska.
6. **Segmentation**: silence threshold and tail behavior become per-library **settings** (defaults: 2.0s, extend-to-next-word-start), with a per-source re-apply action that respects hand-corrected clips (`boundary_edited`). Replaces both the hardcoded 2s rule and the backlog's one-shot-migration framing.
7. **Search**: word-substring v0 limitation replaced by FTS5 phrase/prefix search.
8. **Field naming**: `script_json` in the data-model example → `script` (matches implementation since Phase 5).
9. **Stack section**: Python/FastAPI/React → Swift/SwiftUI/GRDB/AVFoundation; localhost-only network footprint **unchanged** (Ollama + opt-in Anthropic remain the only network calls in Track 1).
10. **Trim Mode**: promoted from Future Ideas to v1 (N11).
11. **Trajectory**: "built for you, not for everyone" is amended to "**built for Lillian first**" — the product principles (library-not-timeline, provenance forever, AI-suggests-you-pick, multi-project tagging) remain non-negotiable, and a commercial Track 2 (N14–N19) hardens the same app for paid direct distribution rather than forking it.
12. **Future Ideas pruning**: Trim Mode (→N11), three-tier aggressiveness (→N15), smart-cut export (→N16), voice annotations (→N17), per-clip media composition (→N18), cross-project surfacing UI / FCPXML / audio-energy analysis (→N19), database migration (→executed), auto-transcription (→N14) all move from Future Ideas into numbered phases. Future Ideas retains only the genuinely speculative (B-roll suggestion bucket, per-section auto-aggressiveness profiles, voice-annotation training, multi-machine sync).
