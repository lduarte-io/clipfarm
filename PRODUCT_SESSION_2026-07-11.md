# PRODUCT SESSION — 2026-07-11 (Fable + Lillian)

Working note for the pre-N4 product/decision session. Answers are recorded per round as they happen; at session end the durable outcomes get distributed to their homes (QUESTIONS.md → Answered, NATIVE_REWRITE_DECISIONS.md, NATIVE_REWRITE_PLAN.md → N12 expansion, PHASES.md backlog / roadmap adds) and this file remains as the session record.

## Session agenda

1. Items waiting on Lillian before/at N4 (below).
2. Round 1: the five N3 PROVISIONALs (plain-language explainers given in-session).
3. Round 2: parked design calls — D35 proxy generation timing + the keyframe-ticks-from-original rule, N11 playhead fencepost semantics, N11 nudge undo coalescing.
4. Round 3+: product direction — matching-script vs assembly-outline split, "ask the library" query affordance, strategy-quality iteration plan, sync stance, roadmap additions. Lillian's note items: (a) plan for improving strategy/batching for clip quality; (b) script matching vs script assembly as separate concepts; (c) overall tactics/technique/strategy for the roadmap.
5. N12 gap pass written into NATIVE_REWRITE_PLAN.md (Fable, this session; Sonnet retrieval helper for lookups as needed).

## Waiting on Lillian (pre-N4 / at the N4 hard stop)

- **Before N4 runs:** stop the stale Xcode debug session (suspended pre-N3 ClipFarm instance, was PID 51307 — it makes fresh app instances' windows self-close ~5–10s after launch). Kill signals sent by the N3 session will land when it resumes — expected, nothing of value in it.
- **Before N4 runs (optional, 1 min):** delete stale DerivedData `ClipFarm-dzsggxcpzdvnckfgvjjmixtxvsrb` (the live one is `ClipFarm-evnssetyqxmfyefwuavdrlclsyiw`).
- **At the N4 hard stop:** combined N3+N4 manual-verify checklist (COMPLETED_PHASES.md → N3 → "Manual verify — DEFERRED"): UI ingest (expect 8 sources — 10/91/8 clips + 5 footage-only), tail-policy flip + re-apply + listening check ("clips no longer feel cut short"), Cmd+Z restore, `.snapshots/` + `PRAGMA table_info(sources)` spot-checks.
- **Look-ahead (not N4):** N7 START gate = Ollama running + Anthropic API key provisioned.

## Q&A log

*(filled per round as answers land)*

### Round 1 — N3 PROVISIONALs 1–4 (2026-07-11)

1. **`.mkv` provenance** → **KEEP AS BUILT**: plain `sources.original_path` column. (QUESTIONS.md item flips to Answered.)
2. **Fixed-padding tail clamping** → **KEEP AS BUILT**: clamp to next clip's first word AND source duration.
3. **Re-apply segmentation** → **KEEP AS BUILT**: ID-preserving diff; tags survive unchanged ranges.
4. **Ingest undo granularity** → **KEEP AS BUILT**: one grouped "Ingest Folder" undo step.

### Round 2 — remux failure + parked design calls (2026-07-11)

5. **Failed `.mkv` remux** → **KEEP AS BUILT**: hard reject with `remux-failed` detail pointing at the ffmpeg fix; re-ingest picks it up. (All five N3 PROVISIONALs now answered — flip QUESTIONS.md.)
6. **D35 proxy generation timing** → **BOTH**: on-demand offer (first edit of a heavy source) + per-source "Generate Proxy" action, PLUS a Settings toggle "auto-generate proxies for heavy sources at ingest" (default OFF — no silent multi-GB background encodes). The two paths share detection + generator + cache; the toggle only changes who initiates. Owner: N12.
7. **Trim-mode playhead parked exactly on a cut** → **INCOMING frame** (first frame that survives the cut) — matches NLE convention (Premiere/Avid/Resolve) and the domain's half-open `[s, e)` ranges. Resolves the N2 gate-6 fencepost as a design decision; N11 inherits it as a requirement (small engine accommodation: bias exact-boundary display to the incoming side).
8. **Nudge undo coalescing** → **PER BURST**: nudges on the same edge coalesce until ~1s pause or switching edges; one Cmd+Z returns to the burst start. N11 requirement.

**Companion rule (Fable, confirmed direction):** keyframe ticks / snap-to-keyframe ALWAYS read the ORIGINAL source file, never the proxy — proxy keyframes land elsewhere; snapping to them would silently break the Lossless-tier guarantee. Lands in the N12 plan expansion + D35 annotation.

### Round 3 — product direction (2026-07-11)

9. **Clip-quality iteration** → named phase **after N13** (“N13.5 — quality iteration”) with an **early-pull trigger**: if the N9 or N10 hard stop shows premades are mediocre, it pulls forward immediately. Levers: take-detection tolerance sets, filler/restart detection, LLM-ranked take ordering within lines, tagging batch-size/quality experiments, audio-energy analysis (promoted from N19 — round 4, item 15).
10. **Script split** → **BUILD IN TRACK 1** (shape pinned in round 4, item 13).
11. **“Ask the library”** (natural-language query over all transcripts → ranked clips → optional save-as-project) → roadmap **post-N13** (“N13.6”). FTS5 literal phrase search lands at N4 regardless.
12. **Multi-machine sync** → **NO SYNC, on record as a decision** (single-machine library + local inbox; backup/restore covers machine moves; multi-machine sync stays Future Ideas).

### Round 4 — split shape + pre-authorizations (2026-07-11)

13. **Split shape** → **A + Duplicate Project**: one assembly outline per project — an ordered list of beats, each beat either a script line (string-matched takes) or a free-text intent (reuses the ad-hoc tag machinery; LLM-matched semantically in the same tagging run, same validation). Lands as amendments to **N6** (brief format expresses the outline) / **N7** (tagger matches free-text beats) / **N8** (TOC renders beats). “A different structure = a different project,” made instant by a **Duplicate Project** action (clones brief/outline/tags, zero re-mine) roadmapped in Track 1.5. **Shape B (outlines as first-class objects, multiple per project) explicitly rejected for now** — it would triple-nest project→outline→attempt and make premades/grid/attempts outline-relative (N9–N10 ripple); A migrates additively into B later if real usage ever demands it.
14. **N12 staged split** → **PRE-AUTHORIZED** as contingency (not plan-of-record): if N12 balloons, **N12a** = Standard tier + mode picker + post-export verification (fully WYSIWYG — Standard re-encodes audio, fades honored trivially); **N12b** = Lossless/hybrid/proxies immediately after.
15. **Audio-energy analysis** → **PROMOTED** from the N19 grab-bag into N13.5 (waveforms from N3 already carry per-source loudness; `energy_shift` stops faking it with words-per-second).

### Round 5 — QUESTIONS.md cleanup (2026-07-11)

16. **mkv-after-sibling-mp4 skip (N3 cold-review residual)** → **specific warning** in the ingest summary ("video.mkv skipped — video.mp4 is already in your library; if this is different content, rename and re-ingest"). Auto-disambiguation rejected (usually the same video in a different container → would silently double clips). **Owner: N4** — added to the N4 kickoff and plan entry. QUESTIONS.md Open queue is now fully empty.

### Round 6 — final preparedness sweep (2026-07-11)

17. **Premades × outline** → **FOLLOW THE OUTLINE**: "best-per-line in script order" generalizes to best-per-beat in outline order (line-beats: string-matched takes; free-text beats: highest-confidence semantically-tagged clip; no-clip beats skipped). No-explicit-outline default (script order) keeps the N9 golden masters valid; outline-following behavior gets new named tests. Landed in plan N9 amendment + N10 replace-picker generalization + D37 extension.

**Environment cleanup (2026-07-11, this session):** the stale suspended ClipFarm instance did NOT survive Lillian's restart — no ClipFarm processes exist; nothing to stop, and the window-self-close confound is gone with it. Stale DerivedData `ClipFarm-dzsggxcpzdvnckfgvjjmixtxvsrb` deleted; the live dir is `ClipFarm-evnssetyqxmfyefwuavdrlclsyiw`. Both pre-N4 checklist items are done.

## Research pass (Sonnet Explore helper, 2026-07-11 — findings folded into the plan's N12 expansion)

- **AVMutableMovie stitch (the decided hybrid-writer architecture):** `insertTimeRange(copySampleData: true)` + `defaultMediaDataStorage` + `writeHeader(.addMovieHeaderToDestination)` produces the self-contained file. Gotchas now recorded in the plan: use the MOVIE's timescale for all inserts (media-timescale mismatch caused multi-minute inserts in the wild); prefer `.mov` when any segment is ProRes; elst-at-splice and `.mp4`-brand behavior are under-documented → verify empirically; an optional passthrough AVAssetExportSession pass over the stitch is cheap insurance, not a documented requirement.
- **Proxy encoding:** AVAssetExportSession presets CANNOT control keyframe interval → proxies must be generated via AVAssetWriter + `AVVideoMaxKeyFrameIntervalKey`/`...DurationKey` (~1 s GOP), baseline params from `AVOutputSettingsAssistant`.
- **Anthropic structured outputs (N7 pin, verified via the claude-api reference):** `output_config: {format: {type: "json_schema", schema}}`; top-level `output_format` is deprecated; schemas need `additionalProperties: false` + `required` and do NOT support numeric range constraints (confidence clamp stays domain-side); prompt caching has a model-dependent minimum cacheable prefix (~1–4K tokens) — short briefs silently won't cache; N7's model dropdown should use current IDs (`claude-sonnet-5` / `claude-opus-4-8` / `claude-haiku-4-5`), ideally hydrated via `GET /v1/models`.

## Distribution log (where each outcome lands)

- QUESTIONS.md → the five N3 PROVISIONALs move to **Answered** (items 1–5, all keep-as-built).
- NATIVE_REWRITE_DECISIONS.md → D35 annotated (generation timing = both paths + Settings auto-toggle default OFF; proxy rules); new **D36** (trim-mode edit-point semantics), **D37** (assembly outline shape A + Duplicate Project), **D38** (no sync), **D39** (N12 staged-split pre-auth), **D40** (Track 1.5 phases); summary table extended.
- clipfarm-spec.md → amendment **15** (assembly outline) added to the 2026-07-05 amendment set.
- NATIVE_REWRITE_PLAN.md → N6/N7/N8 outline amendments; N11 gains the D36 requirements; **N12 gap-pass expansion** (spike-decided architecture, sample-level fades, calibrated HDR verify, D35 proxy sub-scope, staged-split note); new **Track 1.5** section (N13.5, N13.6); N19 energy item marked promoted; §2.8 Anthropic structured-outputs shape pinned for N7.
- mac/CLAUDE.md → tier-table note covers Track 1.5.
