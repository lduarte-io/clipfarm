# ClipFarm Native — macOS Rewrite Technical Plan

*Drafted 2026-07-04 by Claude, from (a) a full survey of the existing implementation (470 tests, phases 1–10a), (b) AVFoundation/video-engine research, and (c) Swift app-architecture research — all verified against 2025–2026 sources.*

**Companion document:** [`NATIVE_REWRITE_DECISIONS.md`](./NATIVE_REWRITE_DECISIONS.md) — every decision branch with options, tradeoffs, and the pick made to keep this plan concrete. **Review that document first**; every `D#` reference below points into it. Overriding any pick is cheap right now and expensive after N2.

**Relationship to the spec:** `clipfarm-spec.md` remains canonical for *product behavior* — categories, propagation rules, provenance, views, the three-tier trim vision. This plan governs the *native implementation* and proposes a set of deliberate spec amendments (§9) where the storage/export/watcher mechanics change. Per CLAUDE.md rules, none of those amendments are silent — they're all listed and land in the spec before construction begins.

**Workflow:** the phase discipline is unchanged — plan per phase, one phase at a time, stop for Lillian's manual verify, entries move to `COMPLETED_PHASES.md`, two reviews per phase. Native phases are numbered **N0–N13** to avoid collision with the web build's 0–11.

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
| Export never built; FFmpeg concat demuxer snaps to keyframes (not frame-accurate) | step 11 unbuilt; video research | Tiered native export: frame-accurate lossless passthrough for ProRes/all-intra, VideoToolbox re-encode otherwise. (D14) |
| Clips routinely cut short (Whisper `word.end` lops the speech tail) | PHASES backlog, 2026-05-26 dogfood | Segmentation tail-extension fix implemented natively + one-shot widening pass at import. (D18) |
| Trim Mode (auto-replay loop, keyboard-only precision) deferred to "Future Ideas" — browser couldn't do it well | spec Future Ideas | First-class phase (N11). Boundary-observer loop + zero-tolerance re-seek makes instant-replay-per-nudge real. (D13) |

---

## 2. Target architecture

### 2.1 Repo & project layout (D4, D5)

Same repo, new top-level `mac/` directory. The web implementation stays in place untouched until cutover (N13), then gets archived.

```
clipfarm/
├── clipfarm-spec.md              # canonical spec (amended per §9)
├── NATIVE_REWRITE_PLAN.md        # this file
├── NATIVE_REWRITE_DECISIONS.md   # decision log
├── clipfarm/ web/ tests/         # web implementation — frozen, reference for port
└── mac/
    ├── ClipFarm.xcodeproj        # thin app shell — Xcode 16+ "buildable folders",
    │                             # so new files never require pbxproj edits
    ├── ClipFarm/                 # app target: SwiftUI views, App struct, menus,
    │                             # NSViewRepresentable wrappers, PlayerEngine glue
    └── ClipFarmKit/              # local SPM package — ~90% of all code lives here
        ├── Package.swift
        ├── Sources/
        │   ├── CFDomain/         # pure logic, ZERO dependencies (fast `swift test`)
        │   ├── CFStore/          # GRDB schema, migrations, snapshots, JSON import/export
        │   ├── CFMedia/          # AVFoundation: probe, composition, thumbnails, waveforms
        │   ├── CFLLM/            # Ollama + Anthropic clients, tagging/naming orchestrators
        │   └── CFExport/         # export tiering, ffmpeg subprocess (mkv remux)
        └── Tests/                # Swift Testing; one test target per source target
```

Why the split matters for construction: `swift test --filter CFDomainTests` runs the entire ported domain suite in seconds from the CLI with no xcodebuild, which is the tight loop Claude Code needs. The app target stays thin enough that `xcodebuild -scheme ClipFarm build` is only needed when views change.

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
CFExport   export planning + AVAssetExportSession/AVAssetWriter + swift-subprocess.
```

The existing invariants port directly: pure functions for domain rules; I/O in dedicated modules; a single entry point to persistent state; no globals (the store is created in the `App` struct and injected via SwiftUI `Environment`).

### 2.3 Persistence schema (D6, D7, D8, D9, D28)

GRDB 7, WAL mode, one database per library. Default library location `~/ClipFarm/` (visible, inspectable with `sqlite3`/DB Browser), overridable in Settings (D28).

```sql
meta(key TEXT PRIMARY KEY, value TEXT)            -- schema_version, created_at
sources(id TEXT PRIMARY KEY, filename, path, duration_sec REAL NULL,
        fps REAL NULL, transcript_path TEXT NULL, added_at, unavailable BOOL,
        is_hdr BOOL NULL, natural_width INT NULL, natural_height INT NULL)
clips(id TEXT PRIMARY KEY,                        -- opaque; encodes source__start__end at creation
      source_id REFERENCES sources, start_sec REAL, end_sec REAL,
      transcript_text TEXT, derived_from_clip_id TEXT NULL,
      tracks TEXT NULL,                           -- reserved JSON blob, v0 always NULL
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
voice_annotations(...)                            -- schema parity, unused in v1
clips_fts                                         -- FTS5 virtual table over clips.transcript_text
```

Notes:
- **All IDs stay strings, verbatim from the JSON** (D9). Clip IDs remain opaque-after-creation; `_hms`/`makeClipID` encoding ports exactly (`HH-MM-SS.mmm`, `int(round(t*1000))`, `__` separator, filename constraint unchanged).
- `attempt_clips.clip_id` is deliberately **not** a foreign key — the tombstone pattern (dangling refs on delete, `needs_review` banner) is spec behavior.
- Migrations via GRDB `DatabaseMigrator`, one registered closure per version — the direct analog of `clipfarm/migrations/`, versioned from day one.
- **Hand-editability** (D7): `File → Export Library as JSON` produces the exact `clipfarm.json` v1 shape (git-diffable, greppable); `File → Import` reads it back. Plus `sqlite3` / DB Browser / Datasette for live inspection. The watchdog/conflict-modal machinery **dissolves** — there is no hand-editable live file to conflict with (spec amendment §9).
- **Snapshots** (D8): before every destructive op, `VACUUM INTO '.snapshots/<ISO>-<hash>.db'` inside the library folder, pruned to last 50 — same ritual, atomic and WAL-safe. This is the belt; in-memory UndoManager (§2.7) is the suspenders.

### 2.4 Time policy (D12)

- **At rest**: `Double` seconds, exactly as today. Schema and JSON round-trip parity.
- **In CFMedia**: convert once at the boundary — `CMTime(seconds:, preferredTimescale: 600)` — and do *all* arithmetic in `CMTime`. Never accumulate edits through `Double` round-trips.
- Frame-precise operations use the track's real timing (`minFrameDuration`, `naturalTimeScale`), never `nominalFrameRate` (it's an average — wrong on VFR sources). `fps == nil` sources keep the spec's 30fps-fallback-with-warning rule.

### 2.5 Playback engine (D11, D13)

`PlayerEngine` (CFMedia, `@Observable`), one instance app-wide, driving one persistent `AVPlayer` in the preview surface (D30 — right-side inspector pane, present on every page like the spec's persistent preview pane).

Composition rules (each is load-bearing; from research):
1. **One `AVMutableCompositionTrack` for video, one for audio.** All ranges inserted back-to-back into the same two tracks (per-clip tracks cause decoder churn and inter-track flicker).
2. **Insert both tracks from the same asset over the same clamped range** (min of audio/video track durations) — mismatched insertions are the classic source of composition drift and tail pops.
3. **Cache `AVURLAsset` per source** with properties pre-loaded. Rebuilding a 50-clip composition is then single-digit milliseconds of edit-list manipulation.
4. **Compositions are immutable snapshots**: every edit builds a fresh composition and swaps via `player.replaceCurrentItem(with:)` — swap while paused where possible, pre-seek the new item to the mapped time with zero tolerance before attaching (kills the black-frame blink).
5. Apply `preferredTransform` (rotated phone footage); `automaticallyWaitsToMinimizeStalling = false` for local files.
6. Optional ~10ms `AVAudioMix` volume ramps at cut boundaries to kill audio pops (D31 — on for preview, re-encode-export only).
7. Tombstones: skipped by the composition builder, surfaced as an overlay chip in the UI ("▢ removed clip at position N") rather than a playback placeholder.

Engine API surface: `load(ranges:[ResolvedRange])`, `play/pause`, `seek(to:)` (zero-tolerance), `step(frames:)`, `loop(window:)` (trim mode: `addBoundaryTimeObserver` at window end → re-seek to window start; nudges re-arm the observer — **not** `AVPlayerLooper`, which requires teardown per range change, D13), `currentTime` published via periodic observer for the scrubber only.

The resolver (`resolve_attempt` port) stays a pure CFDomain function producing `[ResolvedRange]`; the engine consumes it. Preview and export consume the *same* resolved ranges — the Phase 9 "shared with export" contract ports intact.

### 2.6 Keyboard system (D19)

Three layers, one `KeyMap` registry as the single source of truth (drives menus, handlers, and a `?` cheat-sheet overlay):

1. **Menu Commands** (SwiftUI `Commands` + `.keyboardShortcut` + `focusedSceneValue`): everything discoverable — Save/Export/Undo/Redo, mode toggles, "Clip → Nudge In-Point Left" etc. Menu items dispatch to the focused editing context.
2. **Focused-view keys** (`.focusable()` + `.onKeyPress`): list/grid navigation, Enter/Escape, arrows, space-to-play within a focused pane.
3. **Modal monitor** (`NSEvent.addLocalMonitorForEvents(matching: .keyDown)`): installed on trim-mode entry, removed on exit. Owns bare `[ ] , .` + Shift/Alt/Cmd+Alt increment modifiers regardless of focus. Rules: never swallow when a text view is first responder; always pass through Cmd+Q/W/Z; return `nil` to consume.

Layer 3 exists because `.onKeyPress` is focus-dependent — a single stray click must not kill `]` nudges mid-trim. This design is what makes the spec's Trim Mode ("no mouse, no scrub wheel") actually buildable.

### 2.7 State, undo, concurrency (D10, D21)

- **AppStore**: `@MainActor @Observable final class`, owning value-type domain state and mediating all mutations. GRDB `ValueObservation` (main-actor-friendly in GRDB 7) feeds derived read models. No TCA (D10).
- **Undo** (D8): every store mutation captures the touched value-subtree *before* mutating and registers inverse application with the window's `UndoManager` — Cmd+Z / Edit-menu / redo for free. This is a genuine upgrade over the web version, which had no undo at all (only snapshot-file revert). Destructive ops additionally take a DB snapshot (§2.3).
- **Concurrency**: Swift 6.2 Approachable Concurrency, MainActor default isolation (Xcode 26 new-target default). Background work is explicit: `ThumbnailService`, `WaveformService`, `LLMClient`, `ExportService` as `@concurrent`/actor services. Subprocesses (ffmpeg remux, optional transcribe.py invocation later) via **swift-subprocess** with streamed output.
- **Progress**: the web's polling endpoints (`/api/tag/progress`) dissolve — orchestrators expose `AsyncStream<Progress>` consumed directly by the UI. Same phase-key granularity (preflight → batching N/M → committing) so the UX ports.

### 2.8 LLM provider layer (D22, D23)

- Hand-rolled `URLSession` + `Codable` clients (~200 lines each), no third-party SDK. The provider-agnostic `chatWithJSONSchema(messages:schema:)` dispatcher contract ports verbatim.
- **Ollama**: `POST /api/chat` with the JSON schema in `format`, `stream: false`, temp 0.2 — unchanged.
- **Anthropic**: switch from forced-tool-use to the newer **structured outputs** (`output_config: {format: {type: "json_schema", ...}}`) — simpler, guaranteed-parse; keep **prompt caching** (`cache_control: ephemeral`) on the shared brief block for the batched-tagging cost win. Verify the exact param shape against current API docs at implementation time (N7).
- **API key moves to Keychain** (D23) — the native answer to the web version's chmod-0o600 settings file. Provider/model settings to `UserDefaults`; existing `.clipfarm/settings.json` values imported at N1.

---

## 3. Test parity strategy (D25)

The 470-test Python suite is the parity baseline. Route tests lose their HTTP layer (there are no routes anymore) and become **store-method tests** asserting the same contracts: validation errors, snapshot-taken-once, mutation gating, tombstone rules.

| Python test file (count) | Swift target | Phase | Notes |
|---|---|---|---|
| test_segmentation (11) | CFDomainTests | N5 | + new tail-extension tests (D18) |
| test_boundary (33), test_propagation (18) | CFDomainTests | N4 | port edge semantics exactly (below) |
| test_resolver (14), test_continuity (9), test_continuity_refresh (5) | CFDomainTests | N1/N2 | |
| test_strategies (21), test_premade (11), test_attempt_naming (9) | CFDomainTests / CFLLMTests | N9 | + golden-master vs Python output |
| test_brief (28), test_projects (14) | CFDomainTests | N6 | all 3 dogfood tolerance hacks port |
| test_tagging (25), test_llm (12), test_llm_anthropic (10) | CFLLMTests | N7 | validation rules identical |
| test_take_grid (14), test_search (13) | CFDomainTests / CFStoreTests | N8/N3 | search semantics upgrade to FTS5 (new tests) |
| test_store (16), test_migrations (4), test_models_round_trip (4), test_uniqueness_validator (7), test_load_unknown_keys (6), test_source_integrity (3), test_settings (7) | CFStoreTests | N1 | unknown-key tolerance moves to the JSON importer |
| test_ingest (11), test_ffprobe (9), test_whisper_validation (6), test_transcripts (7) | CFStoreTests / CFMediaTests | N5/N3 | ffprobe tests become MetadataProbe tests |
| test_routes_* (~141) | CFStoreTests (store-method contracts) | per phase | HTTP/status-code assertions map to thrown-error assertions |
| test_conflict_freeze (2) | — dropped | — | watcher dissolves (D7); replaced by import-conflict tests |

**Load-bearing semantics that must port bit-exactly** (each gets a named test):
- Silence segmentation: new clip when gap `>=` threshold (2.0s default); tail-extension per D18.
- Internal-pause expansion: split when gap `>` max (strictly greater); gap dropped entirely; word filter `w.start >= start && w.end <= end` (straddle exclusion — port as-is, fix flagged post-v1).
- Overlap tests are **half-open `[s,e)`** — touching endpoints don't collide; create-from-range allows overlap, merge rejects it, adjust-boundaries rejects it.
- Tag dedup on merge: key `(project_id, project_tag_id, category)`, first-encountered wins.
- Trim clamping: the four propagation cases including the pathological zero-both-offsets-and-warn case; negative offsets never clamped against base.
- Tagging validation: hallucinated-ID drop, confidence clamp to [0,1], **on-script-with-null-line demoted to related-but-different** (not dropped), one retry per batch, partial-batch wins.
- Continuity: runs = same source AND forward progression; score = max-run-runtime / total; ValueError-equivalents on empty/all-orphan/zero-runtime.
- ID allocation: monotonic max+1 over all existing keys, never reuse freed slots.

**Golden-master checks** (N9, N13): run the Python strategies/resolver against the imported state and diff against Swift output on the same data. Cheap insurance against subtle port drift, possible because both implementations coexist in one repo until cutover.

---

## 4. Phase plan

Each phase ends with Lillian's manual verify, per the standing workflow. "Port map" names the Python source of truth.

---

### N0 — Toolchain & skeleton

**Goal:** the stack works end-to-end before any feature work (native analog of web Phase 1).

**Scope:** `mac/` layout per §2.1; `ClipFarm.xcodeproj` (buildable folders, automatic signing, bundle id `org.duartes.clipfarm`, **non-sandboxed**, min target macOS 26 — D2, D24); `ClipFarmKit` package with the five targets + Swift Testing smoke tests; GRDB 7 dependency; `mac/CLAUDE.md` documenting the CLI build/test/run loop; empty main window with the nav skeleton (Library / Project / Script / Attempts / Brief / Settings) and the inspector pane slot.

**Verify:** app launches; `swift test` green from CLI; `xcodebuild build` clean; adding a new Swift file requires no pbxproj edit.

---

### N1 — Domain models, persistence, import/export

**Goal:** the existing library — btc.0.4, chrysalis, all tags and attempts — lives in the native app before any UI exists. Real data from day one de-risks everything after.

**Port map:** `models.py` → CFDomain structs (field-for-field, including `Source.unavailable`, `Attempt.needs_review`, `TagKind.tag`; resolve the `script` vs `script_json` naming — adopt `script`, amend spec §9). `store.py` → CFStore (schema §2.3, DatabaseMigrator, snapshot service, uniqueness via unique index + validation). `resolver.py`, `continuity.py` → CFDomain (pure; needed by N2). Importer: `clipfarm.json` v1 → DB with unknown-key log-and-drop tolerance; `settings.json` → UserDefaults + Keychain. Exporter: DB → identical JSON shape.

**Tests (~110):** models round-trip, uniqueness, store/snapshot/migrations, unknown keys, source integrity, resolver (14), continuity (14), **import→export semantic round-trip on the real `clipfarm.json`**.

**Verify:** import the live library; exporter output diffs semantically clean against the input; source-integrity check marks a temporarily-renamed `.mov` unavailable.

---

### N2 — Playback engine (the de-risking spike)

**Goal:** prove the thesis of the rewrite before building UI on it. This phase exists to fail fast if any research assumption is wrong.

**Scope:** CFMedia — `AssetCache`, `MetadataProbe` (async `load(.duration/.nominalFrameRate/.formatDescriptions/.naturalSize/.preferredTransform)`), `CompositionBuilder` (rules §2.5), `PlayerEngine` (full API §2.5 incl. loop mode). Debug-only UI: list imported attempts, click to play through the inspector pane.

**Exit criteria (hard gates — measured, not eyeballed):**
- A synthetic attempt spanning **two different source files** plays through the boundary with no visible/audible gap — the thing the web app could never do. Also verify: this is the *first time ever* the cross-source path gets truly exercised (the web version's fix was never visually verified — recorded blind spot).
- Composition rebuild for a 50-clip attempt < 10ms (asset cache warm); item swap while paused shows no black-frame blink.
- Cut boundaries land frame-accurately (spot-check against source timecodes); `step(byCount:)` works across a composition.
- Trim-loop mode: nudge → re-loop restart feels instant (< ~50ms perceived).

**Fallback if gates fail:** `AVSampleBufferDisplayLayer`/`AVSampleBufferRenderSynchronizer` custom pipeline is the documented escape hatch (weeks of work — that cost is why the gate is here at N2, not N10).

**Verify:** Lillian watches a multi-source assembly play gapless.

---

### N3 — Library (transcript browser + search)

**Goal:** the manual escape hatch, native. Browse any recording without watching it linearly.

**Port map:** `transcripts.py` (mtime-keyed cache), `search.py` semantics superseded by FTS5, whisper sidecar validation.

**Scope:** source sidebar (unavailable greyed out; footage-only badge); transcript view as **TextKit 2 / STTextView wrapper** (D20) — word-level ranges, inline clip-boundary highlighting, click-word, drag-select; FTS5 index populated at import/ingest; search UI with phrase + prefix support (upgrade over web, new tests); click clip → inspector plays it via PlayerEngine; deep-link plumbing (select source + scroll to word) for later grid → library navigation.

**Verify:** browse btc.0.4 smoothly (30-min transcript, no typing/scroll lag); phrase-search `"self custody"` returns hits (web returned zero); click-to-play is instant.

---

### N4 — Boundary correction + system undo

**Goal:** split / merge / adjust / create / delete with real undo — the escape hatch when segmentation is wrong.

**Port map:** `boundary.py` (33 tests), `propagation.py` (18), clip-ID encoding, `clamp_attempt_trims_for_clip` four-case clamp, mm:ss↔seconds input parsing from `Library.tsx`.

**Scope:** all five ops as store methods wired to UndoManager (every op undoable; DB snapshot before each, reasons named as today); UI in the Library transcript view — click-between-words split, multi-select merge (same-source, non-overlap), boundary drag/nudge (`[ ] , .` at 100ms, Shift 10ms — base-level), create-from-selection (+ numeric range entry for footage-only sources, overlap allowed per the Phase 10a revision), delete with confirm. Propagation exactly per spec: clone-tags-stale-true on split, union-merge on merge, `needs_review` flags, tombstones.

**Verify:** split a clip mid-take on real data, Cmd+Z restores it perfectly; merge two takes; verify a snapshot file appeared; tags/attempts propagate per spec.

---

### N5 — Ingest

**Goal:** point at a folder, get sources + clips — natively, with the segmentation fix.

**Port map:** `ingest.py` (pairing, rejection semantics: `__`-in-stem hard-reject with rename offer; sidecar soft-fail → footage-only; re-ingest upgrade path), `segmentation.py`, duration policy (sidecar wins → probe → null).

**Scope:** `NSOpenPanel` folder picker + drag-a-folder-onto-window (native fixes the web's absolute-path-text-input wart); `MetadataProbe` replaces ffprobe (D17); **`.mkv` remuxed to `.mp4` at ingest** via `ffmpeg -c copy` through swift-subprocess, ffmpeg resolved from PATH with Settings override (D15, D16 — AVFoundation cannot open Matroska); HDR flag captured per source, mixed-HDR/SDR warning surfaced (D29); **segmentation with tail extension** — `end_sec` = next word's start, last clip → source duration (D18); one-shot widening pass offered over imported legacy clips (default on, snapshot first); per-source **waveform generation** at ingest (AVAssetReader + Accelerate, ~50–100 buckets/sec, binary sidecar in the library cache — every clip's waveform is then a free slice); FTS5 rows.

**Verify:** re-ingest `05.19.26/` into a scratch library — source/clip counts match the web version modulo the deliberate tail extension; clips no longer feel cut short on playback; an `.mkv` (synthesize one) ingests via remux.

---

### N6 — Projects + brief editor

**Port map:** `brief.py` (28 tests — including all three dogfood tolerance hacks: dedent, preamble-before-frontmatter, loose-list rewrite), `projects.py` name-keyed tag merge (section=name; line=(section, text, occurrence); adhoc=name; surviving identities keep IDs), update-stales-all-rows, delete-hard-deletes-attempts.

**Scope:** brief editor page (plain text editor is fine here), debounced live parse preview, create/update/delete with staling.

**Verify:** paste the chrysalis brief verbatim — parses; edit it — tags stale correctly, IDs preserved for surviving lines.

---

### N7 — LLM tagging

**Port map:** `tagging.py` (25 — every validation rule), `llm.py`, `llm_anthropic.py`, `settings.py`.

**Scope:** CFLLM clients per §2.8; Settings page (provider radio, model dropdowns, key set/test/clear → Keychain, ping with specific error surfacing); tagging orchestrator with identical validation/retry/stale-drop semantics; `AsyncStream` progress panel (provider·model chip, per-batch N/M, ETA). The save-lock/dirty-flag race machinery from the web version dissolves — mutations are DB transactions; a long tag run writes rows transactionally at commit and never blocks reads (WAL).

**Verify:** live tag run on a real project — Anthropic path ~30s, Ollama path works, progress panel live; kill the app mid-run → no partial rows.

---

### N8 — Take grid + Script TOC

**Port map:** `take_grid.py` (sort orders: confidence DESC + start ASC in lines; start ASC in buckets; empty lines visible; summary counter semantics), deep-link behavior.

**Scope:** `ThumbnailService` (AVAssetImageGenerator — wide tolerance for grid thumbs so it grabs cheap keyframes, `maximumSize` ~2× display, disk+NSCache keyed `(source, roundedTime, size)`); Take Grid page (LazyVGrid, per-line rows, thumbnail cards with provenance + category badge + confidence + stale dot, four buckets); Script TOC page (outline, collapsible, takes inline); side-panel detail with Open-in-Library deep-link; active-attempt "adding to" concept + `+` on cards (localStorage → UserDefaults).

**Verify:** scan 10 deliveries of one line side-by-side with thumbnails; grid scrolls smoothly at full library scale (6k clips).

---

### N9 — Attempts + premade generation

**Port map:** `strategies.py` (all 8 + `_detect_takes` with parameterized tolerance sets + caps + coverage constants), `premade.py` (dedup-first-wins, replace-only-ai-premade, ID allocation), `attempt_naming.py` (single batched call, per-name canned fallback, llm/canned/mixed), `attempts_ops.py` (create/fork/rename/replace-clips with the four tombstone rules/delete-with-dangling-parent).

**Scope:** Attempts page — two buckets, continuity bar (green/amber/red thresholds as today), fork/rename/delete/set-active, drag-to-reorder (SwiftUI drag or custom, optimistic + undoable), regenerate with confirm; **golden-master test**: Swift strategies vs Python strategies on the imported library must produce identical clip lists.

**Verify:** regenerate premades on the real project — output matches the web version's attempts; fork + reorder + undo works; watch a fork play through.

---

### N10 — Attempt editing (the never-built web-10b scope)

**Goal:** everything Phase 10b was going to be, but native — greenfield, no port reference for the UI.

**Scope:** replace-this-clip picker (siblings of same line tag, confidence DESC, one-click swap); tombstone replacement flow (pick replacement → swap in → clear `needs_review`); **"tighten internal pauses" toggle** per attempt-clip (`internal_pause_max_sec` = 0.5s default; resolver + engine already honor it since N1/N2 — this is just the affordance); duplicate-clip-in-attempt allowed; live composition rebuild on every edit (edit → new composition → seamless swap); `needs_review` banner with jump-to-slot.

**Verify:** the Chipotle-line flow end-to-end — pick takes top to bottom in Script TOC, tighten pauses on two clips, replace one clip from siblings, watch the result instantly.

---

### N11 — Trim mode (the native headline)

**Goal:** the spec's Future-Ideas Trim Mode, promoted to v1 — this is the single biggest "why native" payoff (D13, D19).

**Scope:**
- Enter on any attempt-clip (from N10 UI) or any base clip (Library): modal state, NSEvent monitor installed, UI chrome dims to an edit-point HUD with the waveform strip (slice of the N5 per-source waveform) centered on the boundary.
- **Auto-replay loop**: 1–2s window centered on the edit point, boundary-observer loop (§2.5); every nudge instantly re-arms and replays. Spacebar pauses/resumes the loop.
- **Nudge keys**: `[` `]` in-point, `,` `.` out-point; increments 100ms / Shift 10ms / Alt 1ms / **Cmd+Alt ±1 frame** (track `minFrameDuration`; fps-null sources → 30fps + one-time warning per spec).
- **Direct numeric entry**: `-50ms`, `+0.5s`, `+2f`.
- **Permissiveness buttons**: more-generous/tighter per side, configurable step (% or absolute).
- Attempt context → writes `trim_*_offset` (per-attempt, base immutable); Library context → boundary correction with full propagation. Same engine, two write targets — the spec's boundary-correction vs per-attempt-trim distinction made physical.
- All nudges undoable (coalesced sensibly per UndoManager grouping).

**Verify:** the spec's pitch, literally: enter trim mode, clip loops in your ear, two taps of `]`, perfect, next clip. Whole-attempt cleanup in minutes without touching the mouse.

---

### N12 — Export

**Goal:** full-quality MP4 out (web step 11, never built) (D14).

**Scope:** CFExport — resolve attempt (same resolver as preview) → analyze source codecs → **tier 1: passthrough** (`AVAssetExportPresetPassthrough`) when sources are ProRes/all-intra or every cut lands on a sync sample — lossless *and* frame-accurate; **tier 2: re-encode** via `AVAssetExportPresetHighestQuality` or AVAssetWriter + VideoToolbox at high bitrate (one-generation loss, unavoidable for H.264 arbitrary cuts — surfaced honestly in the UI); modern async API (`export(to:as:)`, `states(updateInterval:)` progress); audio micro-fades on the re-encode path (D31); post-export verification pass (duration sum matches resolved ranges; boundary spot-check); H.264-passthrough edit-list caveat surfaced (frame-accurate in Apple players, lead-in frames may show in non-Apple demuxers) with re-encode offered as the safe default for H.264. FCPXML stays deferred (post-v1). Optional `smartcut` sidecar integration documented but deferred (D14).

**Verify:** export the best chrysalis attempt; frame-check three cut points in QuickTime; compare a ProRes passthrough export bit-for-bit at a cut region.

---

### N13 — Cutover + parity audit

**Scope:** final re-import of the latest `clipfarm.json`; full parity checklist (one row per §1 pain point + one per spec "What the app must let me do" bullet); golden-master reruns; archive the web implementation (`legacy-web/` or a git tag; delete from working tree); land the spec amendments (§9) in `clipfarm-spec.md` + rewrite CLAUDE.md's invariants/stack/structure sections for the native app; carry `PHASES.md`/`COMPLETED_PHASES.md` forward as the single build log.

**Verify:** one full real editing session — ingest a new recording, brief, tag, assemble, trim, export — with the web server never started.

---

### Post-v1 backlog (explicitly out of v1 scope)

In rough priority order: **WhisperKit in-app transcription** (D26 — keep the `.whisper.json` sidecar contract as the interchange format; large-v3-turbo is a quality upgrade over the current faster-whisper `small`; auto-transcribe untranscribed sources); voice annotations (trigger-phrase design per spec); three-tier aggressiveness polish layer (global slider + section microadjust — the trim-mode engine from N11 is its foundation); word-straddle filter fix for internal-pause expansion; per-clip media composition (`tracks` activation); cross-project surfacing UI; FCPXML export; smartcut sidecar for lossless H.264; `energy_shift` upgrade from words-per-second proxy to real audio analysis (waveforms from N5 make this cheap); Apple `SpeechTranscriber` evaluation (macOS 26 — fast but whisper-small-class accuracy; validate word timings on real recordings before trusting).

---

## 5. Performance budgets

Measured at N2/N3/N8 gates, re-checked at N13:

| Operation | Budget |
|---|---|
| Composition rebuild, 50 clips (warm asset cache) | < 10 ms |
| Zero-tolerance seek, local file | < 50 ms |
| Cross-source playback transition | imperceptible (0 added latency) |
| Trim-mode nudge → loop restart | < 50 ms perceived |
| FTS5 phrase search over full library | < 50 ms |
| Import of existing clipfarm.json (~6k-clip scale) | < 5 s |
| Take-grid scroll at full library scale | 60 fps, thumbnails lazy |
| Cold launch to interactive library | < 2 s |

## 6. Risks & mitigations

1. **Composition playback assumption fails** (glitchy swap, boundary inaccuracy) → that's why N2 is a gated spike right after data; escape hatch is the sample-buffer pipeline, decision point documented before UI is built on it.
2. **SwiftUI perf at library scale** (6k cards, 30-min transcripts) → transcript view is TextKit 2 from day one (not retrofitted); grid budgeted at N8 with NSCollectionView wrap as fallback.
3. **Port drift in subtle domain rules** → bit-exact semantics list (§3) + golden-master diffs against the living Python implementation while both coexist.
4. **H.264 passthrough compatibility** (edit-list handling outside Apple players) → tier logic defaults H.264 to re-encode; passthrough is opt-in with the caveat surfaced.
5. **`.mkv` support regression** → remux-at-ingest keeps the extension set intact with one playback engine.
6. **Xcode/CLI friction for agent-driven construction** → buildable folders + SPM core means 90% of iteration is `swift test`; app-target churn is thin.
7. **Losing hand-editability** → JSON export/import round-trip is a tested, first-class feature from N1, not an afterthought.

## 7. What we explicitly do NOT port

The HTTP/API layer (routes, Range streaming, status-code mapping); the two-`<video>` preview and all its workarounds; the watchdog/conflict-freeze machinery (D7); polling progress endpoints; the async save-lock/dirty-flag discipline (replaced by DB transactions); React/Vite/Tailwind entirely; the absolute-path ingest text field.

## 8. Dogfood continuity during construction

The web app stays runnable and is the working tool until native reaches usable parity. Recommended handoff points: after **N3** the native app becomes the better *browsing* tool (search upgrade); after **N9/N10** the better *assembly* tool; **N13** retires the web version. New recordings made during construction get ingested into the web version's `clipfarm.json` and re-imported — the importer is idempotent-by-re-import (fresh library each time until cutover). The pending web Phase 10a verify is moot except as needed to keep dogfooding (D27).

## 9. Proposed spec amendments (land in `clipfarm-spec.md` before N0)

Per the CLAUDE.md rule — divergence is proposed explicitly, never silent:

1. **Storage**: `clipfarm.json` → SQLite (GRDB) as source of truth; the spec's own "End-state: move to a real database" section is hereby executed. Hand-editability is preserved via the JSON export/import contract (exact v1 shape).
2. **Watcher/conflict policy**: dissolved with the hand-editable live file. External-edit path becomes: export JSON → edit → import (with a diff preview on import replacing the conflict modal's role).
3. **Snapshots**: file-copy of JSON → `VACUUM INTO` DB snapshot, same before-every-destructive-op ritual, same keep-50; **plus** first-class in-memory undo (UndoManager), which the spec never had.
4. **Export mechanism**: FFmpeg concat demuxer → native tiered export (passthrough / VideoToolbox re-encode). FFmpeg's remaining role: `.mkv` remux at ingest. Rationale: concat demuxer inpoints snap to keyframes (not frame-accurate); native passthrough on ProRes is lossless *and* frame-accurate.
5. **`.mkv` handling**: accepted at ingest, remuxed to `.mp4` (lossless stream copy) because AVFoundation cannot open Matroska.
6. **Segmentation**: clip `end_sec` extends to the next word's start (backlog item promoted to spec text); last clip extends to source duration.
7. **Search**: word-substring v0 limitation replaced by FTS5 phrase/prefix search.
8. **Field naming**: `script_json` in the data-model example → `script` (matches implementation since Phase 5).
9. **Stack section**: Python/FastAPI/React → Swift/SwiftUI/GRDB/AVFoundation; localhost-only network footprint **unchanged** (Ollama + opt-in Anthropic remain the only network calls).
10. **Trim Mode**: promoted from Future Ideas to v1 (N11) — the browser constraint that deferred it no longer exists.

Unchanged and reaffirmed: library-not-timeline; provenance forever; AI-suggests-you-pick; multi-project tagging as the engine; every data-model invariant not named above (opaque clip IDs, `__` constraint, base-clip immutability from attempts, propagation rules, `tracks: null`, derived continuity, schema versioning from day one).
