# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Source of truth (authoritative)

- **Spec**: @clipfarm-spec.md — full vision, original ideation, technical design, data model, build order, future ideas.

The spec is canonical. If implementation conflicts with the spec, **call it out explicitly** and align the implementation to the spec (or propose a deliberate spec change). Do not silently diverge.

## Where to start

The **build order** is at the bottom of `clipfarm-spec.md`, steps 0–11. Each step delivers something verifiable on its own. Pick the next unchecked step and read its description alongside the relevant spec sections (data model, pipelines, frontend pages).

Do not jump ahead. Each step is designed so the previous one is a working checkpoint.

## How we work — phase-based, with documented assumptions

Build progress is tracked in two files at the repo root: **`PHASES.md`** (upcoming) and **`COMPLETED_PHASES.md`** (done). The build order's steps 0–11 map 1:1 to phases.

Rules:

1. **Plan before executing** non-trivial phases — write a scope/assumptions/verification block into `PHASES.md` *before* writing code. Spec ambiguities get resolved explicitly, in writing, in the plan.
2. **One phase at a time.** Execute, then stop for manual verification from Lillian. Do not auto-advance to the next phase.
3. **Move verified phases to `COMPLETED_PHASES.md`** with assumptions and any plan deviations captured. Trivial phases that didn't need a written plan still get a `COMPLETED_PHASES.md` entry — the audit trail must be complete.
4. **Each completed phase gets two reviews** — a self-assessment in-session, and a separate Claude code-review session. Both work from the `COMPLETED_PHASES.md` entry, so write that entry with enough detail to be reviewed against the spec.

The failure mode this defends against is silent assumption-stacking across a long build, which is the same risk CLAUDE.md already flags as "spec drift."

## Core product principles (non-negotiable)

1. **Library, not timeline.** The Library page is usable before any project exists. Most editors force project-context first; we deliberately don't.
2. **Provenance forever.** Every clip carries source video name + timestamp range. Visible on every card, sortable everywhere. Never anonymized.
3. **AI suggests, you pick.** No destructive auto-edits. Soft categories (on-script, related, standalone, fragment); multiple premade attempts to choose from; explicit retag, never silent.
4. **Multi-project tagging is the engine.** A clip can be tagged in N projects with different `(section, line, category)` triples each. When a new brief lands, the LLM re-mines the existing library for the new project. This is what turns ClipFarm from a one-video tool into a personal idea-engine. **Protect against future "simplify" pressure.**

## Data model invariants (enforce in Pydantic types + tests)

- **Clip IDs are opaque after creation.** At creation the ID encodes `source__start__end` for readability. After that, treat it as a stable handle — `start_sec` / `end_sec` can mutate via boundary correction without changing the ID. UI displays current `start_sec` / `end_sec`, not the encoded values in the ID.
- **Source filenames cannot contain `__`.** Reserved as the clip-ID separator. Ingest rejects them with a sanitized-rename offer. See spec → Decisions locked → "Source filename constraint."
- **Base clips are immutable from per-attempt operations.** Per-attempt trim uses `trim_start_offset` / `trim_end_offset` in `attempts[id].clips[i]`. It must never mutate `clips[clip_id]`.
- **Boundary correction mutates base clips and propagates.** See the spec's "Fix segmentation when the AI gets it wrong" section for split/merge propagation rules — clone tags with `stale: true` on split, union-merge tags on merge, set `needs_review: true` on affected attempts.
- **Per-attempt trim offsets are clamped on boundary correction.** When a base clip's `start_sec` / `end_sec` moves inward past an existing `trim_*_offset`, clamp the offset. Don't let invariants drift.
- **Every destructive operation writes a snapshot first.** Use the snapshot helper to copy `clipfarm.json` to `.clipfarm/snapshots/<ISO-timestamp>-<ms>-<hash>.json` before any split / merge / delete / retag-clobber. Prune to last 50. Filename includes ms + 4-char hash to avoid same-second collisions.
- **`clipfarm.json` is the source of truth, and it's hand-editable.** In-memory indexes are rebuilt on load. Atomic writes (`tmp` → `fsync` → `rename`). All saves go through an `asyncio.Lock` to serialize concurrent route handlers.
- **All JSON access goes through one entry point.** A single `load_state()` does read → migrate → validate → return. Nothing else opens `clipfarm.json` directly — not tests, not routes, not migrations. If you find yourself reaching for `json.load`, you're bypassing the seam.
- **Unknown keys are logged and dropped on load**, never rejected. Models use Pydantic `extra="ignore"`; `load_state()` diffs known-vs-actual keys and warns per drop. See spec → "Unknown-key tolerance."
- **External-edit conflicts freeze writes, surface a modal, never auto-resolve.** `watchdog` detects external `clipfarm.json` changes. If in-memory state is dirty when one lands, writes pause and the user decides. See spec → "Conflict policy on external edit."
- **No module-level globals for shared state.** The state container and the `watchdog` observer live on `app.state` and are injected via FastAPI `Depends`. Resist any drift toward `STATE = ...` at the top of a module.
- **`tracks: null` is the v0 default on every clip.** Reserved for future per-clip media composition (audio replacement, video swap, overlays). v0 readers tolerate it, v0 writers don't populate it.
- **`continuity_score` is derived, not stored long-term as truth.** Recomputed from the attempt's clip list whenever clips change. The on-disk field is a cache, not a source of truth — readers should be willing to recompute.
- **`internal_pause_max_sec` is per-attempt-clip, never mutates the base.** When set, the resolver expands one `AttemptClip` into multiple `(start, end)` sub-ranges; preview and export both honor it.
- **Schema versioning is on from day one.** Every `clipfarm.json` carries `"version": N`. Migrations live in `clipfarm/migrations/`, one function per bump.

## Tech stack

- **Backend**: Python 3.12 + FastAPI + uvicorn on `localhost:8765`
- **LLM**: Ollama with **Llama 3.1 8B** as the starting model (tentative — revisit if tagging quality is inadequate; Qwen 2.5 14B is the "go bigger" option if RAM allows)
- **Embeddings**: `sentence-transformers/all-MiniLM-L6-v2` (for no-script semantic clustering)
- **String matching**: `rapidfuzz` (for script-anchored take matching)
- **Frontend**: React + Vite + Tailwind, served by FastAPI as static files on the same port
- **Storage**: `clipfarm.json` (atomic writes, snapshots, watchdog)
- **Video processing**: FFmpeg subprocess for export
- **Transcripts**: Whisper word-level JSON, generated upstream by **the existing `transcribe.py`** at:
  `~/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/Video/Scripts/mp4files/transcribe.py`
  - Uses `faster_whisper` with the `small` model (CPU, int8).
  - For each media file, writes `<filename>.whisper.json` and `<filename>.txt` as **siblings** to the source. Same stem, different suffix.
  - Idempotent — re-runs skip files that already have a non-empty `.whisper.json`. Safe to re-run anytime.
  - **Convention ClipFarm relies on**: `<name>.mov` is paired with `<name>.whisper.json` in the same directory. ClipFarm does not run Whisper itself in v0; it only reads these files.
  - Ready-to-ingest sample data lives at `~/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/Video/Scripts/mp4files/05.19.26/` — multiple `.mov` files with transcripts already generated (including `btc.0.4` which is the dogfood video).

No external services, no auth, no network calls except Ollama on localhost. Built for one user.

## Build behavior guardrails

- **Don't invent product behavior.** If the spec doesn't define it, propose 2-3 options with tradeoffs and ask. Do not implement assumptions.
- **Surface uncertainty rather than silently picking a policy.**
- **One step at a time from the build order.** Each step is verifiable on its own. Don't jump ahead.
- **If something feels complex, it's probably wrong.** The spec is opinionated but not complicated.
- **Spec drift is the failure mode to avoid.** If implementation needs to diverge from the spec, propose updating the spec first, then implement.

## Code hygiene

- No global singletons; shared state lives on `app.state` and is injected via FastAPI `Depends`. See the data model invariants above.
- Pydantic models for every entity in the data model. Validate at the boundary, trust internally. Models use `extra="ignore"` (the loader logs+strips unknowns rather than forbidding them).
- Pure functions for domain rules (silence segmentation, tag propagation on split/merge, attempt resolution, scoring heuristics, `continuity_score` computation, `internal_pause_max_sec` range expansion). Tested independently of FastAPI / I/O.
- I/O (JSON read/write, FFmpeg subprocess, Ollama HTTP) lives in dedicated modules, not mixed into route handlers. All `clipfarm.json` reads/writes go through `store.load_state()` / `store.save_state()` — nothing else touches the file directly.
- All saves serialize through an `asyncio.Lock`. Snapshot-then-save is a single locked critical section so a destructive op can't race a concurrent save.
- No premature abstractions — three similar lines is better than the wrong abstraction. Wait until you see the third use.

## Project structure (target — populate as we build)

```
clipfarm/
├── clipfarm-spec.md           # canonical spec
├── CLAUDE.md                  # this file
├── README.md
├── pyproject.toml
├── clipfarm.json              # state (gitignored)
├── .clipfarm/snapshots/       # auto-pruned (gitignored)
├── clipfarm/                  # backend package
│   ├── app.py                 # FastAPI entry, page routing
│   ├── models.py              # Pydantic models for all entities
│   ├── store.py               # JSON load/save, atomic writes, snapshots, watchdog
│   ├── migrations/            # v1_to_v2.py and the runner
│   ├── ingest.py              # source folder scan + silence segmentation
│   ├── tag.py                 # Ollama batched tagging
│   ├── attempts.py            # premade generation, fork, replace-clip, trim resolve
│   ├── export.py              # FFmpeg concat list generation
│   └── routes/                # FastAPI route modules per page
├── web/                       # React + Vite + Tailwind frontend
│   ├── src/
│   └── package.json
└── tests/
    ├── test_segmentation.py
    ├── test_boundary_correction.py
    ├── test_propagation.py
    └── test_attempts.py
```

## Commands

(Populate as we build — `uv run`, `npm run dev`, `pytest`, etc.)
