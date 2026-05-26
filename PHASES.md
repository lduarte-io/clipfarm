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

## Phase 9 — Built ⏳ 2026-05-26 (awaiting manual verify)

**Goal.** First time the assembled work actually plays back. Click any clip anywhere → it plays from the source file at the clip's range. Click an attempt → it plays through every clip in sequence. A persistent floating preview pane follows you across pages so playback survives navigation. **This is the phase where "the candidate videos you can pick from" becomes "the candidate videos you can watch."**

Spec quote that anchors this phase:
> "Live see" an attempt — play the assembled sequence instantly, no export step. Just plays the underlying clips back-to-back from the source files.

### Decisions resolved with Lillian (2026-05-26 before code work)

1. **Floating bottom-right pane WITH drag-resize in v0**, default 480×270. Minimize-to-pill button when not needed. Size persisted to `localStorage` (key `clipfarm.preview_pane_size`) so it sticks across reloads. Resize handle on the top-left corner of the pane (since the pane is anchored bottom-right, top-left is the only growable corner). Min 320×180, max 80% of viewport.
2. **Auto-play immediately** on click. Dismissing the pane stops playback.
3. **SidePanel shell extracted as Phase 9 kickoff carry** (~50-line refactor). Three pages swap to the shared shell.

### Verification (manual + automated)

- `uv run pytest` passes (target ~415 tests; 390 current + ~25 new for resolver, video route, attempt-resolved route, tombstone handling).
- `curl -I localhost:8765/api/sources/<id>/video` returns 200 + `Accept-Ranges: bytes` header. `curl -H 'Range: bytes=0-1023' ...` returns 206 with `Content-Range: bytes 0-1023/<total>`.
- `curl localhost:8765/api/attempts/<id>/resolved` returns an ordered list of items, each either a `range` (with `source_id`, `source_url`, `effective_start_sec`, `effective_end_sec`, `clip_id`) or a `tombstone` (with `clip_id`, `reason`). Sub-range expansion fires when an `AttemptClip` has `internal_pause_max_sec` set.
- **Real-data smoke on btc.0.4:** click a take card on `/project` → preview pane appears bottom-right and plays the 5–10s range. Click "longest contiguous take" on `/attempts` → preview plays through all clips in sequence with no visible gap (single-source, so the alternating-`<video>` swap stays smooth).
- **Cross-source caveat documented but NOT visually tested in this phase** — btc.0.4 is single-source. First multi-source attempt is when the ~100–300ms file-load latency at source boundaries becomes the real stress test. Recorded for follow-up; not blocking Phase 9 verification.

### Scope

**Backend — resolver + video streaming:**

- **`clipfarm/resolver.py`** — pure orchestration over `Attempt` → ordered playback items.
  - **Discriminated union type:**
    ```python
    class ResolvedRange(StrictModel):
        type: Literal["range"] = "range"
        clip_id: str  # informational — frontend correlates to attempt clip list
        source_id: str
        effective_start_sec: float
        effective_end_sec: float

    class TombstoneRange(StrictModel):
        type: Literal["tombstone"] = "tombstone"
        clip_id: str       # the deleted clip's ID, preserved on the AttemptClip
        reason: str        # "clip deleted in boundary correction"
    ```
  - `resolve_attempt(state, attempt_id) -> list[ResolvedRange | TombstoneRange]`. For each `AttemptClip`:
    - **Dangling clip (Phase 4 invariant)**: if `state.clips[ac.clip_id]` is missing, emit a single `TombstoneRange` and continue. This is the first reader of the "removed — pick a replacement" placeholder behavior; Phase 4's `delete_clip` set `Attempt.needs_review=True` but kept the dangling `AttemptClip` so the assembly slot stays visible.
    - **Otherwise compute trim with source-bounds clamping (Phase 9 plan-review #2):**
      - Raw: `start = base_clip.start_sec + trim_start_offset`, `end = base_clip.end_sec - trim_end_offset`
      - **Clamp against source bounds**:
        - `effective_start = max(0.0, start)` — negative trim past source 0 clamped.
        - `effective_end = min(source.duration_sec if source.duration_sec is not None else math.inf, end)` — trim past source duration clamped.
      - Log a warning when either clamp fires (so Phase 10's trim-UI dev sees they pushed past bounds).
      - Phase 4's `clamp_attempt_trims_for_clip` handles base-bounds clamping; this resolver-side clamp is the source-bounds backstop Phase 4 explicitly declined to handle.
    - **If `internal_pause_max_sec is not None`**: walk the source's Whisper transcript words inside `[effective_start, effective_end)`. Find inter-word gaps `> internal_pause_max_sec`. **Split the range at each such gap, dropping the gap entirely** (Phase 9 plan-review #1 — spec wording updated to match: gaps are dropped between sub-ranges, not collapsed-to-max). Emit one `ResolvedRange` per surviving sub-span.
    - If `internal_pause_max_sec is None`: emit one `ResolvedRange` per `AttemptClip` (the default in v0; Phase 10 ships the toggle UI).
  - **Resolver contract (locked here so Phase 10 + Phase 11 can rely on it):**
    1. Item order in the returned list matches the order of `AttemptClip` entries in the attempt.
    2. A tombstone produces exactly one item with `type: "tombstone"`.
    3. A live clip produces ≥1 `ResolvedRange` (multiple only when `internal_pause_max_sec` splits gaps).
    4. **Trim offsets are clamped twice**: base-bounds by Phase 4's `clamp_attempt_trims_for_clip` (on boundary correction); source-bounds by this resolver (every read). Zero/negative effective duration after both clamps raises `ValueError` — orchestrator shouldn't produce it; defense in depth.
    5. Missing transcript on a source with `internal_pause_max_sec` set: **fall back to no-expansion** (single `ResolvedRange` with the full trimmed span). Log a warning. Phase 11 export uses the same fallback.
  - Raises `KeyError` on unknown attempt; route translates to 404.
  - **Shared with Phase 11 export** — module docstring calls this out explicitly so the export step doesn't reimplement the gap-drop or clamp rules.

- **`clipfarm/routes/resolver.py`** — `GET /api/attempts/{attempt_id}/resolved`. Returns the resolved-items list + per-range `source_filename` and `source_url` (so the frontend can label what's playing without a separate fetch). Pure read; no lock, no snapshot.

- **`clipfarm/routes/video.py`** — `GET /api/sources/{source_id}/video`. Streams the source file with HTTP Range support so the `<video>` element can seek without re-downloading.
  - **Range header forms supported (Phase 9 plan-review #3):**
    - `bytes=N-M` (closed range) — returns the byte slice `[N, M]` inclusive.
    - `bytes=N-` (open-ended, "from N to EOF") — returns `[N, total-1]`. **Browsers actually use this for streaming**, not always closed ranges.
    - `bytes=-N` (suffix range, "last N bytes") — **rejected with 416 Range Not Satisfiable.** Uncommon; browsers don't use it for `<video>`; supporting it adds parsing surface for no benefit. Documented behavior, not an oversight.
    - Multi-range (`bytes=0-99,200-299`) — rejected with 416. `<video>` never asks for these.
  - All success responses include `Content-Range: bytes start-end/total` + `Accept-Ranges: bytes` + `Content-Length: end-start+1`.
  - Default chunk size 64KB for the streaming iterator.
  - Returns 200 full-response when no Range header is present (browsers do this on initial fetch to learn duration).
  - 404 unknown source. **410 Gone if `source.unavailable=True`** (file moved/deleted since ingest).
  - **416 Range Not Satisfiable** if the requested range is past EOF, uses an unsupported form, or has `N > total`.
  - `Content-Type` derived from extension: `.mov` / `.mp4` / `.m4v` → `video/mp4`; `.mkv` → `video/x-matroska`. The four spec-supported extensions.

**Frontend — playback context + preview pane:**

- **`web/src/playback/` as a top-level subsystem directory** (Phase 9 plan-review #7), parallel to `pages/` and `components/`. Holds `context.tsx` + `PreviewPane.tsx` + (eventually) `useVideoElement.ts` etc. Playback is a stateful subsystem with its own context, not a single UI component — top-level placement makes the boundary obvious. If Phase 9 stays the only subsystem-shaped folder, fine; if Phase 10 / 11 grow more (e.g. `web/src/export/`), the pattern scales.
- **`web/src/playback/context.tsx`** — React context exposing:
  - `queue: ResolvedItem[]` — currently-loaded playback queue (ranges + tombstones).
  - `currentIndex: number` — index of the item currently playing.
  - `playing: boolean`, `dismissed: boolean`.
  - `playClip({source_id, start_sec, end_sec, clip_id, filename})` — load a single-range queue and start.
  - `playAttempt(attempt_id)` — fetch `/api/attempts/{id}/resolved`, load full queue, start.
  - `pause()`, `resume()`, `dismiss()`, `seekToIndex(i)` — `seekToIndex` skips tombstone items automatically.

- **`web/src/playback/PreviewPane.tsx`** — floating pane anchored bottom-right of viewport.
  - **Default size 480×270**, drag-resizable from the top-left corner (only growable corner since the pane is anchored bottom-right). Min 320×180, max 80% of viewport width/height. Size persisted to `localStorage["clipfarm.preview_pane_size"]` as `{width, height}` so it survives reloads. Restored on mount.
  - **Two alternating `<video>` elements** (visible / hidden, swapped on range-end). The currently-visible one plays the active range; the hidden one preloads the next range's source file at `effective_start`.
  - **Native `<video>` controls hidden** (`controls={false}`). Phase 9 plan-review #6: native scrubber would let the user seek out of the resolved range and break the queue model. Custom controls only; OS-level volume keys still work. Fullscreen via a custom button if added later.
  - **End-of-range detection via `timeupdate`** comparing `currentTime` against `range.effective_end` (the file's natural `ended` event won't fire when we trim before file-end). Tolerance ~50ms to avoid overshoot.
  - **Preload-ahead constant**: `const PRELOAD_AHEAD_SEC = 0.5` named at the top of the file with a tuning comment (Phase 9 plan-review #4): *"How far ahead of the swap moment we tell the hidden element to start loading the next range's source + seek to its effective_start. Too short = swap stalls on cross-source. Too long = wasted preloads when user pauses or dismisses. 0.5s feels right for same-source SSD reads + ~100–300ms cross-source latency."* Single tuning knob.
  - **Swap behavior on range-end (locked for the cross-source case):**
    - **Same-source next**: the hidden element is already preloaded (`preload="auto"` + `currentTime = next.effective_start` set `PRELOAD_AHEAD_SEC` before swap); swap is instant, no visible gap.
    - **Cross-source next** (different `source_id`): we set the hidden element's `src` as soon as we detect the source change in `currentIndex+1`, but the browser still needs to load file headers. UX during the gap: **the just-finished frame is HELD (current video element stays in DOM, paused on its last frame), and a small overlay shows `↻ Loading next clip…` until the new element fires `canplay`.** No black flash. Worst case: ~100–300ms with the "loading" overlay visible. Better than a black frame.
  - **Tombstone handling**: when `currentIndex` lands on a tombstone, the pane shows a "▢ Removed clip — pick a replacement" placeholder card in its body, holds for 2 seconds, then auto-advances to the next item. Replacement UI lives on the Attempts page (Phase 10).
  - **Controls**: play/pause toggle, current-range label (`"3 of 7 · btc.0.4.mov · 1:23–1:31"`), minimize-to-pill button, dismiss (X) button.
  - Cross-source caveat surfaced as a code comment in the swap logic.

- **`web/src/App.tsx`** — wrap routes in `<PlaybackProvider>`; render `<PreviewPane />` inside the provider (outside `<Routes>` so it survives nav).

- **Per-page integration:**
  - `web/src/pages/Project.tsx` — clicking a `TakeCard` calls `playClip(...)` (in addition to opening the side panel). The side panel gets an explicit "▶ Play" button as well (for the case where playback got dismissed).
  - `web/src/pages/ScriptTOC.tsx` — same.
  - `web/src/pages/Attempts.tsx` — clicking an attempt card loads its `playAttempt(id)` queue; the side panel gets the same "▶ Play attempt" button. Tombstone items in the attempt's clip list render the "▢ Removed clip" placeholder.
  - `web/src/pages/Library.tsx` — bonus affordance: a "▶ Play this clip" button on the search-hit row (single-clip range).

- **`web/src/components/SidePanel.tsx`** — extracted **shell only** (Phase 9 plan-review #5). Sticky right-side panel chrome: container + header row (title slot + close-X button) + scrollable body slot. Takes children for page-specific bodies. Each page's specific content (TakeCard transcript + Open-in-Library button; Attempts clip list with continuity bar; etc.) stays inline in the calling page — we're consolidating the wrapper, not the content. ~50 lines, not the ~150 it would be for full pattern unification.

**Tests (~28 new):**

- `tests/test_resolver.py` (~14): trim offset application (start + end), **source-bounds clamping fires on negative effective_start + on effective_end past source duration**, `internal_pause_max_sec` gap drop (no gaps → single range; one gap > max → 2 ranges; gap exactly = max → no split — boundary inclusive), zero-duration sub-range after clamp raises ValueError, missing transcript on source with internal_pause_max_sec set → fallback to single range with warning, multi-clip attempt → items in order, **tombstone emitted for dangling clip**, unknown attempt raises KeyError.
- `tests/test_routes_resolver.py` (~4): happy path, 404 unknown attempt, response shape includes `source_url` per range, tombstone item present in response.
- `tests/test_routes_video.py` (~10): 200 full response + `Accept-Ranges: bytes` header, 206 partial response with `bytes=N-M` correct `Content-Range` and body bytes, **206 partial response with `bytes=N-` (open-ended) form**, **416 for suffix-range `bytes=-N`**, **416 for multi-range `bytes=0-99,200-299`**, range past EOF → 416, 404 unknown source, 410 unavailable source, content-type derivation per extension (`.mov`/`.mp4`/`.m4v`/`.mkv`), no Range header → 200.

### Decisions locked with this plan

- **Resolver lives in the backend, not the frontend.** Reused by Phase 11 export (which is Python-side). Avoids reimplementing the trim + gap-collapse + tombstone rules in two places.
- **Discriminated union `ResolvedRange | TombstoneRange`** for the resolver's output. Frontend handles both shapes. Tombstones aren't filtered out — they're emitted in-order so the attempt's clip-list structure is preserved (slot-by-slot).
- **`internal_pause_max_sec` semantic: gaps dropped entirely** between sub-ranges (plan-review #1). Spec wording updated to match. Equivalent listening experience for the dogfood use case; "preserve a beat of silence" stays a v1 toggle on top if it ever matters.
- **Trim offsets clamped against source bounds in the resolver** (plan-review #2). `effective_start = max(0.0, ...)`, `effective_end = min(source.duration_sec or inf, ...)`. Log warning when clamping fires. Defense-in-depth backstop for Phase 10's trim UI; Phase 4 already handles base-bounds.
- **HTTP Range forms supported: `bytes=N-M` and `bytes=N-`** (plan-review #3). Suffix-range `bytes=-N` and multi-range rejected with 416. Browsers only need the two we support for `<video>` streaming.
- **`PRELOAD_AHEAD_SEC = 0.5` named constant** at the top of `PreviewPane.tsx` with tuning rationale comment (plan-review #4). Single tuning knob; easy to revisit.
- **Native `<video>` controls hidden** (`controls={false}`) — plan-review #6. Native scrubber would let user seek out of resolved range; custom controls only.
- **SidePanel extraction is shell-only** (plan-review #5): chrome + close-X + scrollable body slot. ~50 lines, not the ~150 that full pattern unification would be. Page-specific content stays inline.
- **`web/src/playback/` lives as a top-level subsystem directory** (plan-review #7), parallel to `pages/` and `components/`. Makes the "playback is a stateful subsystem" boundary obvious.
- **Video streaming via custom Range-aware route**, not static-file mount. Sources live outside the repo; the custom route honors `source.unavailable`.
- **Two `<video>` elements with `timeupdate`-based swap**, not MediaSource Extensions. Per spec — "smooth enough for rough-assembly review."
- **Range-end detection via `timeupdate` + `effective_end` comparison**, not the file's natural `ended` event.
- **Cross-source UX during load: hold-last-frame + "Loading next clip…" overlay**, not black flash. Worst case ~100–300ms with overlay visible.
- **Tombstone UX in the pane: 2-second placeholder card, then auto-advance**. Replacement UI is Phase 10.
- **`internal_pause_max_sec` expansion implemented in Phase 9 resolver**, field stays `null` everywhere until Phase 10 ships the UI toggle. Resolver code handles null as "no expansion." Missing-transcript fallback: emit a single un-expanded range + log warning.
- **Persistent preview pane lives in App.tsx shell**, outside `<Routes>`. Survives page nav without remounting the `<video>` element.
- **Floating bottom-right + drag-resizable in v0** (Lillian's call). Default 480×270, min 320×180, max 80% of viewport. Resize handle on the top-left corner. Size persisted to `localStorage`. Minimize-to-pill button when out-of-the-way is wanted.
- **Auto-play on click**. Dismissing the pane stops playback.
- **Per-page SidePanel shell extracted as a Phase 9 carry** (~50-line refactor).
- **`/api/sources/{id}/video` is the URL form** — matches `/api/sources/{id}/transcript`.

### Out of scope for Phase 9 (explicit)

- **Per-attempt-clip trim editing UI** — Phase 10. Resolver supports trim offsets; UI ships in Phase 10.
- **"Tighten internal pauses" UI toggle** — Phase 10. Resolver expansion lands now so Phase 10 just flips the field.
- **Replacement UI for tombstones** — Phase 10 (the "pick a replacement" affordance on the attempt clip list).
- **Free-positioning (drag-to-move) preview pane**. v0 stays anchored bottom-right. Drag-to-resize lands in v0 per Lillian's call, but drag-to-move-anywhere is deferred.
- **Keyboard shortcuts** (space-to-pause, arrow-to-seek, etc.). Add when dogfood says they're missed.
- **Cross-source latency mitigation beyond preload-next + hold-last-frame**. MSE / true gapless playback is Stage 2 per the spec.
- **Audio-only preview mode**.
- **Concurrent multi-attempt comparison** (play two attempts side-by-side). Future Idea.
- **Frame-precise scrubbing** (`Cmd+Alt = ±1 frame`). Phase 10. Note: `Source.fps` may be null (Phase 2 ffprobe-failure fallback) — Phase 9 only displays timestamps as `MM:SS`, never frame numbers, so fps isn't read in this phase.

### Carries from prior reviews

- **Cross-source preview blind spot** (Phase 9 advance note): btc.0.4 is single-source, so the ~100–300ms file-load latency at source boundaries won't manifest until the first multi-source dogfood. Recorded; not blocking.
- **SidePanel extraction trigger** (Phase 8 advance note): land as Phase 9 kickoff carry per decision above.

---

## Phase 10 — Attempt editing

*To be planned before execution.*

**Advance notes** (carry into the plan when written):
- Frame-precise nudge (`Cmd+Alt = ±1 frame`) uses `Source.fps`. If `fps is None` for a source (ffprobe failed in Phase 2), fall back to 30 fps with a one-time UI warning per source. See spec → "Source fps detection."
- "Tighten internal pauses" toggle sets `AttemptClip.internal_pause_max_sec` to a sensible default (start with 0.5s). Single button, no slider — the full per-segment aggressiveness UI is v1. Resolver expansion already lands in Phase 9.
- **Trim-clamp test now lands for real**: with real attempts existing, the Phase 4 `clamp_attempt_trims_for_clip()` stub gets its failing-then-passing test. Boundary correction that moves a clip's `start_sec` inward past an `Attempt.clips[i].trim_start_offset` must clamp the offset, not leave the attempt referencing impossible coordinates.
- `continuity_score` recomputation on edits — call `compute_continuity_score` after every clip-list mutation; the on-disk cache stays in sync.
- **Tombstone replacement UI** — "▢ Removed clip — pick a replacement" affordance shipped in Phase 9 as a placeholder. Phase 10 wires the picker (select another clip → swap in via `AttemptClip.clip_id` update, drop `needs_review`).

## Phase 11 — Export

*To be planned before execution.*
