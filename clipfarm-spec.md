# ClipFarm — Vision & Spec

*Working spec. Move into a project folder once we start building.*

---

## The vision

A personal video studio organized around your raw footage as a queryable, AI-indexed library. You feed it everything you record — every take, every session, every offhand idea — and it gives you back organized views of your own material so you can pull finished videos out of it instead of editing them from scratch.

Everything stays tied to its original source video and timestamp forever, so the library never decays into anonymous clips.

You should be able to talk to it both *before* recording (here's the table of contents / argument I'm trying to make) and *during* recording ("good line, add that under section C"), and have those instructions natively shape what it surfaces.

The wedge use case is the current `btc.0.4` problem: you recorded the same script many ways and need to assemble the good version. But the same engine extracts "ideas worth a short," "lines that connect to argument X," and "moments that drifted off-script into something better." It's a tool for harvesting structure out of long unstructured recordings — across recordings, not just within one.

**Philosophy:** built for you, not for everyone. Custom, powerful, extensible. AI assists, doesn't decide — it surfaces, groups, suggests; you pick. Provenance is never lost. When you notice you want a feature, you can add it in an evening.

---

## What the app must let me do

### Feed it raw recordings, get back a library
- Input is `(video file, full transcript with word-level timestamps)` pairs.
- Every clip in the system is identified by `source video name + start + end`. That identity is shown on every card, sortable everywhere, never anonymized.
- The **whole raw transcript of every video stays browsable** at all times. If the AI missed something or grouped it wrong, I can read the transcript and grab any moment as a new clip manually.
- Input pipeline preserves the session structure: which video, which day, which session.

### Fix segmentation when the AI gets it wrong

The 2-second silence-gap heuristic for clip boundaries will make mistakes — clips split mid-sentence, two separate takes merged because you didn't pause long enough between them. The **by-source view** (raw transcript with detected clip boundaries marked inline) is the escape hatch, and these operations are first-class:

- **Split a clip** — click between any two words in the raw transcript, "split here." Creates two new clips. Both inherit the original's `clip_project_tags`; you refine afterward which tags actually belong to which half.
- **Merge adjacent clips** — select two consecutive clips, "merge." Replaces them with one new clip spanning both. Tags from both clips are union-merged; duplicates dedupe.
- **Extend a clip's start or end at the base level** — drag the boundary edge in the transcript view, or use keyboard nudge with the clip selected. Mutates the **base** clip.
- **Shrink a clip's start or end** — same, inward.
- **Create a clip from scratch** — drag-select a word range in the raw transcript that wasn't auto-detected → creates a new clip. Starts untagged; you tag it manually or run a targeted re-tag.
- **Delete a clip** — wrong segmentation, drop it entirely. Tag rows and any attempt references are cleaned up.

**Critical distinction — boundary correction vs. per-attempt trim:**

| | Boundary correction | Per-attempt trim |
|--|---|---|
| What changes | The base clip itself (`start_sec`, `end_sec`) | Only this attempt's view of the clip |
| Propagates to | Every attempt and tag using this clip | Only the one attempt |
| Mental model | "The library is wrong, fix it" | "This attempt wants a slightly different version" |
| Triggered from | The by-source view | The attempt detail view |

Boundary correction mutations propagate by design — if a clip was mis-segmented, every use of it was wrong, and the fix should cascade everywhere.

**Propagation rules — what happens to tags and attempts when boundaries change:**

- **Split (C → C1, C2)**: tags on C are *cloned* onto both C1 and C2 with `stale: true` so you can refine which actually belong where. Attempt references default to C1 with an attempt-level `needs_review: true` flag (UI surfaces a "this attempt had a referenced clip split — review" banner). C is removed from the active library; if you need the un-split version back, revert from snapshots.
- **Merge (C1, C2 → C3)**: tags from both clips are union-merged onto C3 (duplicate `(project_id, project_tag_id, category)` triples dedupe). All attempt references update to C3. C1 and C2 are removed.
- **Extend / shrink existing boundaries**: clip ID stays the same (it's the same conceptual take). Tags and attempt references stay attached as-is. Per-attempt `trim_*_offset` values are clamped if the new base boundary moved past them.
- **Create from scratch**: a new clip ID, no inbound references. Starts untagged.
- **Delete**: clip removed; `clip_project_tags` entries pointing at it are dropped; affected attempts get `needs_review: true` and the missing clip is rendered as a "removed — pick a replacement" placeholder in the attempt's clip list rather than silently disappearing.

**Undo:** every operation above writes `clipfarm.json` to `.clipfarm/snapshots/<timestamp>.json` *before* the change. Last 50 retained. Revert via Settings → "Restore snapshot" or by copying the file back by hand.

### Give the AI instructions *before* it processes
- A project brief screen / file where I write:
  - The script (if there is one), as a list of lines.
  - A table of contents / argument outline / intended sections.
  - What counts as "good" for this project — tone, length, energy, anything I want to bias toward.
  - Tags I want it to look for.
- The brief shapes how material gets grouped, labeled, and surfaced. Without a brief, it falls back to clustering by similarity.

### Categorize material *gently*, not as "junk"
Multiple soft categories. Demoted material is **sorted, not hidden**:
- **On-script** — matched a script line.
- **Related-but-different** — good lines that don't match a script line but clearly relate to the work.
- **Standalone idea** — could become its own video, short, callout, or callback.
- **Off-topic** — not for this project but worth keeping in the library.
- **Fragment / restart** — false starts, single-word noises. Collapsed by default, expandable.

### View the same library multiple ways
Not one timeline. Multiple views over the same underlying clips:

- **By script line**: each script line gets a row; every attempt at that line is a card in that row. Scan ten deliveries of line 3 side by side.
- **By chapter**: for longer work, lines are grouped into sections / chapters of the larger argument.
- **By script order (TOC view)**: the full script displayed as an outline, **reorderable**. Each line expands into a TOC of clip options. Pick one per line and you've drafted the video.
- **By source recording**: full transcript of one video, with detected clips marked inline. The fallback view when the AI grouping isn't right.
- **By original time**: every clip across every video in recording order. For when I'm asking "what did I actually do that day?"
- **By voice tag** (long-term): clips I flagged out loud while recording.
- **Idea bucket**: standalone-idea + related-but-different, browseable as a list of "things worth making a video about someday."

### Build candidate videos as "attempts"
- Multiple parallel attempts at the same project — named, switchable, persistent.
- **AI premade attempts** are auto-generated up front and **split into two surfaced-separately buckets**. Names should feel like *how I'd describe what I just watched*, not generic editor lingo. Each attempt carries a **continuity score** (0.0–1.0) shown as a visual indicator on the card — how much of the attempt comes from one contiguous take vs. how many distinct source-takes got assembled together.

  **Best plausible videos (3–5)** — actually-good candidates worth shipping or polishing. These are what I scan first when I want a starting point:
  - *"best take of each line, assembled in script order"* — the greatest-hits version. Usually low continuity, high completeness.
  - *"longest contiguous take"* — the closest I got to nailing it in one. High continuity by definition.
  - *"the 3 times I said it in almost one take"* — near-complete end-to-end deliveries grouped together, low restart count, low filler. Highest-continuity straight-throughs.
  - *"shortest complete take of the full script"* — for length-constrained cuts and shorts.
  - *"the take where the energy picked up"* — heuristics on pace / volume change.

  **Diagnostic groupings** — useful for browsing the material, not for shipping. Surfaced in a secondary panel rather than mixed in with shippable candidates:
  - *"versions where I started with [first sentence X]"* — clustered by which line opened the take.
  - *"versions where I skipped line 4"* — what I said when I dropped a beat.
  - *"takes where I ad-libbed extra material"* — best on-script delivery padded with bonus off-script content from that same take.

  Premades are *named filters* over the take library — easy to add new ones as I notice patterns in how I record. New filters can land in either bucket.
- **Hand-built attempts**: start empty and compose clip by clip.
- **Fork any attempt**: I like attempt 4 overall but I want 4 segments from attempt 7 → fork attempt 4, swap those four clips, save as attempt 8.
- **Replace one clip with another**: every clip in an attempt has a "show other options for this line" action that pops up siblings (other takes of the same script line). One click swaps.
- **Add a clip from anywhere**: from the take grid, from another attempt, from the raw transcript, from the idea bucket.
- **Reorder** by drag.
- **Move clips between attempts**.

### Edit clips at the boundaries
- Lengthen on either side (grab more pre-roll / post-roll).
- Shorten on either side.
- Frame-precise nudge with keyboard shortcuts. Microsecond-level in the long term.
- Per-attempt trim: the same source moment can be trimmed differently in different attempts without affecting each other or the base clip.

### Tag and annotate
- Tagging system on clips. Tags can be project-defined (from the brief) or ad-hoc.
- Tags drive views and the TOC. I can ask "show me everything tagged `hook`" or "show me clips tagged `subsection-c`."
- Free-text notes per clip and per attempt.

### Live preview
- "Live see" an attempt — play the assembled sequence instantly, no export step. Just plays the underlying clips back-to-back from the source files.
- Click any clip anywhere in the UI → preview seeks and plays that range from the source video.

### Voice annotations during recording (long-term)
- While recording I can say things like *"good line, save that for section C"* or *"that one was fire."*
- The transcript pipeline detects these annotation cues and surfaces them later as first-class tags attached to the immediately-preceding clip.
- This means I can mark material *while delivering it* and never have to remember it during the edit.

**Caveat — this is harder than it looks.** Distinguishing "good line, save for section C" from actual script content is a real classification problem (the script may legitimately contain phrases like "good line" or "section C"). The pragmatic answer is **a trigger phrase**: every annotation has to start with a configured wake word (e.g. `"clipfarm: ..."`) and ends at the next sentence boundary. Without that, false positives wreck the feature. If trigger-phrase ergonomics feel awkward during a real shoot, this gets pushed to v2+. Don't ship voice annotations casually as a v1 add-on.

### Export
- **Full-quality MP4 with cuts done inside the app** — original codec preserved where possible, no quality loss. There's nothing special about DaVinci's cuts that ClipFarm can't match (the AI-blur and other ML-driven creative effects are real but live elsewhere in the workflow), so the cuts happen here, in the program.
- **FCPXML export to DaVinci Resolve** is the optional handoff path, not the default. Use it when you want Resolve's creative polish layer — color, audio mixing, effects.
- Export any attempt at any time. The export step is independent from the editing step.

---

## Use cases this engine enables

- **Take selection for a single script** (the immediate btc.0.4 unblock).
- **Long-form video assembly** from many sessions of recordings.
- **Video essays and argument creation** — pull together material from across recordings into a structured piece.
- **Shortform clip extraction from longform sources** — the original ClipFarm idea: scan a long recording for sentences and segments that could become standalone shorts.
- **Personal idea library** — every offhand thing I ever said worth keeping, indexed and findable across all my recordings.

---

## UI views to design

I'll sketch each of these in ASCII next if you want, but here's the list:

- [ ] **Take grid** — by script line, each line a row, takes as cards
- [ ] **Script TOC view** — reorderable script outline with collapsible clip options per line
- [ ] **By-source view** — one video's full transcript with detected clips highlighted inline
- [ ] **Attempts panel** — multiple attempts viewable side by side, drag clips between them
- [ ] **Idea bucket** — standalone-idea + related-but-different, browseable list
- [ ] **AI instructions screen** — write the project brief: script, TOC, "what counts as good," tags
- [ ] **Clip detail / replace UI** — when you click a clip in an attempt: trim handles, transcript, notes, "show other options for this line"
- [ ] **Original-time view** — clips across all videos in recording order

---

## Polish layer

These live on top of an already-organized library — don't let them contaminate the core vision. But the headline feature here is genuinely distinctive in its own right and worth pitching properly:

### Three-tier aggressiveness editing (the headline)

Most editors make you decide every cut individually. Most automatic cleanup tools give you one global setting and that's it. This is the in-between that nobody builds:

1. **Global aggressiveness** — one slider for the whole clip. Set it once and 95% of the cuts are right. Where most cleanup tools stop.
2. **Section microadjust** — at any spot the global setting got wrong, two buttons: *a little more generous* / *a little tighter*. No scrubbing, no manual cut placement — bump the local aggressiveness for that span and the cuts recompute. This is the bit that turns 30 minutes of fiddling into 30 seconds.
3. **Frame-precise nudge** — for the small handful of cuts that need surgical work, keyboard shortcuts move the in/out point a few frames (microsecond-precision long-term). For the truly fiddly stuff.

The pitch in one line: *one setting one-shots most of the video, then you tap "more generous" on the two spots where it cut too tight, then nudge two frames on the one spot that still feels off. Whole-video cleanup in under a minute. No scrub wheel required.*

This is what makes the polish layer worth building as part of ClipFarm rather than punting it to Resolve. Resolve's text-based editor and silence detection don't have the microadjust step — they're either "fully automatic" or "manually click every cut." The three-tier model is the actual differentiator.

### Other polish features
- Filler word / false start detection and one-click microcuts.
- Audio crossfade at cut points to eliminate clicks.
- Transcript-driven editing inside a clip — delete or rearrange words by clicking them.

---

## Differentiating philosophy (one-liner each)

- **Library, not timeline.** Raw footage is a queryable database with AI as the librarian.
- **Multiple views, not one canonical edit.** The script is one view; original time is another; idea bucket is another. Same clips, different windows.
- **Provenance forever.** Source video name + timestamp on every card. Always.
- **AI suggests, you pick.** Soft categories, multiple premade attempts, no destructive auto-edits.
- **Movable everything.** Clips swap, attempts fork, scripts reorder, clip boundaries nudge — nothing is locked in.
- **Custom power tool.** One user. Extensible. No product polish trap.

---

## Original Ideation

*Lillian's own framing of the ideas behind ClipFarm, preserved in her own words. The structured spec above is derived from these — when in doubt about intent, read this section.*

**The problem this is solving:**

> "I've been avoiding doing this exact editing task for days because it seems both mind numbing and somehow stressful because I just go crazy watching the same thing."

### Views / ways of seeing the material

- See the different ways you said a script cleaned up and put together — "here's 10 different versions"
- "Here's the 3 times you said it in almost 1 take"
- "Versions where you started with this sentence" — grouping by which line you opened with
- "Live see" a video edited with transcripts — play it as if it were assembled, in real time
- One already-assembled version just put together to watch
- A view by script line — each line is a row, see every attempt at it
- A view by chapter / section for longer work
- A "completed script" view — the full intended script displayed, reorderable
- A by-source view — one video's full transcript, all timestamps visible
- "By original time" — everything in the order you actually recorded it, across all videos
- Idea bucket — off-script good lines that could become their own video or short

### Attempts / assembly

- AI premade attempts generated upfront — multiple parallel candidate videos
- Ability to hand-build your own attempt from scratch
- If you like attempt 4 but prefer 4 segments from attempt 7, swap those in — fork an attempt
- "Replace this clip with another" — show me all other takes of this same line
- Add a clip from anywhere — from the grid, another attempt, the raw transcript
- Reorder clips by drag
- Move clips between attempts

### Clip editing

- Lengthen a clip on either side (grab more pre-roll or post-roll)
- Shorten a clip on either side
- Per-clip trim that doesn't affect the base clip or other attempts
- Every clip always shows original video name + timestamp — provenance never lost

### Input / instructions to the AI

- A screen or file where you write the script as a list of lines
- A place to put the table of contents / argument outline
- Tell it what "good" means for this project — tone, energy, length bias
- Tags you want it to look for — project-defined tags
- Voice annotations while recording: say something like "good line, add that to subsection C" and have it natively surfaced later

### Polish layer

- Window-based cut strength — per-segment aggressiveness slider for silence removal
- One global setting that onshots most of the video, then microadjust buttons ("a little more generous / a little tighter")
- Microsecond keyboard shortcuts for nudging cut points
- Transcript-driven editing inside a clip — delete or rearrange words by clicking them

### Export / output

- Full quality MP4 with cuts done inside the app
- FCPXML export to DaVinci for creative polish
- The ability to make the cuts right there in the program — there's nothing special about DaVinci's cuts (aside from AI blur or other ML-driven creative effects, which live elsewhere in the workflow anyway)

### Philosophy / things you said were non-negotiable

- Everything has to be movable
- Source video name + timestamp on every card, always
- Junk is too harsh — demote don't discard, soft categories
- The whole transcript of every video stays browsable at all times so nothing gets lost
- Built for you, not everyone — custom, powerful, extensible
- AI surfaces and suggests, you pick — no destructive auto-edits
- The LLM can't tell how well I delivered each line — surfacing and organizing is the AI's job, judging delivery is mine
- This is for the "make the video make sense" edit, not the polish edit
- Could be used more in depth for video essays and argument creation

---

## Technical Design

*Decisions made in service of the vision above. Where multiple choices were viable, the recommendation is locked here; alternatives noted where worth knowing.*

### Stack (locked)

- **Backend**: Python 3.12 + FastAPI + uvicorn. Serves the API and hosts the frontend on `localhost:8765`.
- **Storage**: a single `clipfarm.json` at the project root. **The JSON is the source of truth** — human-readable, hand-editable, git-diffable. The app loads it, builds in-memory indexes on startup, writes atomically (`tmp` → `fsync` → `rename`) on save, and watches for external edits via `watchdog` (prompts on conflict if there are unsaved in-memory changes). **Scale caveat**: a 30-min recording produces ~350 clips; the `05.19.26/mp4/` folder alone (~18 recordings) hits ~6k clips. The atomic-write-on-every-debounced-save story holds into the low thousands; beyond that, full-file rewrite cost grows fast. Budget the SQLite migration sooner than "eventually" — see Future Ideas.
- **Snapshots / undo**: every destructive operation (split, merge, delete, retag-clobber) writes a copy of `clipfarm.json` to `.clipfarm/snapshots/<ISO-timestamp>.json` *before* the operation runs. Last 50 retained, older auto-pruned. No formal undo system — just file-level revert from the snapshots directory (via a "Restore snapshot" affordance in Settings, or by hand-copying the file). Cheap insurance against bad splits.
- **Schema versioning**: every `clipfarm.json` carries `"version": N`. A `clipfarm/migrations/` directory holds one function per bump (`v1_to_v2.py`, etc.) that mutates an in-memory dict. On load, if the file's version is older than current, migrations run sequentially and the file is saved back at the new version. Scaffold the directory and an empty `v1_to_v2.py` placeholder at v0 — cheap now, expensive to retrofit.
- **Source file integrity**: on app start (and on every Library refresh), each `sources[i].path` is verified to still resolve to a file. If not, the source is marked `"unavailable": true` and shown greyed-out in the UI rather than crashing the load. Clips, tags, and attempt references stay in the JSON — they become viewable but unplayable until the file is restored or repointed.
- **LLM**: [Ollama](https://ollama.com/) running locally with **Llama 3.1 8B** (4-bit quant) as the **starting model — not fully locked**. Revisit if tagging quality is inadequate. The bigger of the two finalists (Qwen 2.5 7B was the alternative); only ~1.5× slower, and tagging isn't a hot loop, so the size pays off. JSON-schema-constrained outputs (Ollama supports this natively). If a Mac has the RAM (16GB+), Qwen 2.5 14B is a "go bigger" option worth trying.
- **Embeddings**: `sentence-transformers/all-MiniLM-L6-v2` (~80MB). For script-less semantic clustering.
- **String matching**: `rapidfuzz` for script-anchored take matching.
- **Transcripts**: Whisper, via the existing `transcribe.py` pipeline. ClipFarm consumes word-level JSON, doesn't run Whisper itself.
- **Video processing**: FFmpeg subprocess for concat exports.
- **Frontend**: React + Vite + Tailwind, single SPA served by FastAPI. Drag/drop, multiple synchronized views, and live preview are interactive enough that React's component model pays off quickly. (Vanilla considered and rejected — the UI gets complex fast.)

### Naming hierarchy

Three levels. Each clip can carry tags at any subset of these levels, for any number of projects.

| Level | What it is | Example |
|-------|-----------|---------|
| **Project** | An output video you're building. The thing that gets exported. UI label: "Video." | "btc explainer v0.4", "short: hook about local AI", "video essay: nuance" |
| **Section** | A chapter or beat within a project. From the project's TOC. Optional — short scripts can skip sections. | "intro", "the setup", "the punchline", "subsection C" |
| **Line** | A specific script line. Belongs to a section if sections exist, otherwise directly to the project. | "Hey, today I want to talk about..." |

**Key property — clips are multi-project.** A great line about local AI can belong to multiple projects simultaneously. The same clip carries one tag set per project it's been assigned to — each tag set is independently `(section, line, category)`.

When you write a brief for a new project, the LLM scans the *existing library* and tags relevant clips for the new project too. You don't re-record; you re-mine.

### Data model

```json
{
  "version": 1,

  "sources": {
    "1": {
      "filename": "btc.0.4.mov",
      "path": "/Users/lillianduarte/Desktop/.../btc.0.4.mov",
      "duration_sec": 1812.34,
      "fps": 60.0,
      "transcript_path": "/Users/.../btc.0.4.whisper.json",
      "added_at": "2026-05-25T..."
    }
  },

  "clips": {
    "btc.0.4__00-01-12.345__00-01-18.220": {
      "source_id": "1",
      "start_sec": 72.345,
      "end_sec": 78.220,
      "transcript_text": "...",
      "derived_from_clip_id": null,
      "tracks": null,
      "created_at": "..."
    }
  },

  "projects": {
    "1": {
      "name": "btc explainer v0.4",
      "brief_md": "...",
      "script_json": { "lines": [ "..." ] },
      "tags": {
        "1": { "kind": "section", "name": "intro", "parent_id": null, "order_idx": 0 },
        "2": { "kind": "line",    "name": "the hook", "parent_id": "1", "order_idx": 0 }
      },
      "created_at": "..."
    }
  },

  "clip_project_tags": [
    {
      "clip_id": "btc.0.4__00-01-12.345__00-01-18.220",
      "project_id": "1",
      "project_tag_id": "2",
      "category": "on-script",
      "confidence": 0.92,
      "source": "ai",
      "stale": false,
      "notes": ""
    }
  ],

  "attempts": {
    "1": {
      "project_id": "1",
      "name": "the 3 times you said it in almost one take",
      "parent_attempt_id": null,
      "source": "ai-premade",
      "premade_bucket": "best",
      "continuity_score": 0.92,
      "clips": [
        {
          "clip_id": "btc.0.4__00-01-12.345__00-01-18.220",
          "trim_start_offset": 0.0,
          "trim_end_offset": 0.0,
          "internal_pause_max_sec": null,
          "notes": ""
        }
      ],
      "created_at": "..."
    }
  },

  "voice_annotations": [
    {
      "source_id": "1",
      "timestamp_sec": 345.67,
      "text": "good line, save for section C",
      "resolved_clip_id": "btc.0.4__...",
      "target_project_id": "1",
      "target_tag_id": "5"
    }
  ]
}
```

Notes on the shape:
- **`clips` are immutable from per-attempt operations**, but mutable from **boundary correction** (see "Fix segmentation when the AI gets it wrong"). Per-attempt trim uses offsets in `attempts[id].clips[i]` that don't touch the base; boundary correction changes the base and propagates everywhere.
- **`clip_project_tags` is the many-to-many bridge.** A clip can be tagged in N projects with different `(section, line, category)` triples in each. The `stale: true` flag is set when a project's brief changes, prompting the user to re-tag explicitly.
- **`attempts[id].clips[i].trim_*_offset`** carries per-attempt trim — negative extends, positive shrinks. Derived clips only get created if the user "promotes" a trimmed range to a reusable base.
- **`attempts[id].parent_attempt_id`** tracks forks: "Attempt 8 is a fork of Attempt 4." Lets you compare lineages.
- **`attempts[id].continuity_score`** — fraction (0.0–1.0) of the attempt's runtime sourced from one contiguous span in one source video. 1.0 = entirely one take, 0.0 = every clip from a different source-take. Computed at attempt-generation time and recomputed when the attempt's clip list changes. Displayed on attempt cards as a visual indicator so you can tell straight-through assemblies apart from heavily-Frankensteined ones at a glance.
- **`attempts[id].premade_bucket`** — `"best"`, `"diagnostic"`, or `null`. Drives the two-bucket UI layout for premade attempts: ship-worthy candidates in the primary panel, browse-only groupings in a secondary panel. Hand-built attempts and forks have `null`.
- **`attempts[id].clips[i].internal_pause_max_sec`** — when non-null, the preview and export collapse any inter-word gap longer than this value down to this value. Per-attempt-clip, never mutates the base. The resolver produces multiple `(start, end)` sub-ranges from a single attempt-clip when this is set; the seek-on-`ended` trick handles them transparently. v0 ships a single "tighten internal pauses" toggle with a sensible default (e.g. 0.5s); the full per-segment aggressiveness slider is v1 polish layer.
- **All IDs are strings** in JSON (since object keys must be strings). The app coerces them consistently on load.
- **Clip IDs are opaque after creation.** At creation, the ID encodes `source__start__end` (`btc.0.4__00-01-12.345__00-01-18.220`) for human readability. After that, the ID is treated as a stable handle — `start_sec` / `end_sec` can change via boundary correction without changing the ID. The UI always shows current `start_sec` / `end_sec`, not the encoded values in the ID. This keeps cross-references (tags, attempts, voice annotations) stable across boundary edits.
- **`derived_from_clip_id`** points back to a parent clip when a trimmed range gets "promoted" from a per-attempt trim into a reusable base clip — e.g. you've trimmed this clip three different ways across three attempts and want one of those trimmed ranges to become a first-class library entry instead of staying scoped to an attempt. Most clips have this as `null` (originals from ingest or boundary correction). Filled when you save-as-clip from an attempt-level trim.
- **`tracks`** is reserved for **Per-clip media composition** (see Future Ideas). `null` by default and through v0 — the resolver treats `null` as "use the source file's audio and video unchanged." When populated, the structure is:
  ```json
  "tracks": {
    "audio_override": { "file_path": "/path/to/mic.wav", "start_offset_sec": 0.0 } | null,
    "video_override": { "source_id": "2", "start_sec": 100.0, "end_sec": 105.0 } | null,
    "overlays": [
      { "start_sec": 2.0, "end_sec": 5.0, "type": "blackout", "color": "#000000" }
    ]
  }
  ```
  The hook is in the schema now so adding any of the three Per-clip-media-composition operations later is additive — no migration of existing clip records. v0 readers can ignore the field; v0 writers leave it `null`.
- **Editing by hand is supported.** Open `clipfarm.json` in any editor, change a value, save. The app's file watcher picks it up and reloads. If you've made unsaved in-memory edits, you'll be prompted before either side overwrites the other.

### Whisper transcript schema (consumed, not produced)

ClipFarm reads `.whisper.json` sidecars produced by the existing `transcribe.py`. The shape is pinned and verified against all 18 files in `~/Desktop/.../05.19.26/`:

```json
{
  "schema_version": 1,
  "source_filename": "btc.0.4.mov",
  "language": "en",
  "language_probability": 0.9804,
  "duration": 2059.84,
  "model": "small",
  "transcribed_at": "2026-05-19T...",
  "segments": [
    {
      "id": 1,
      "start": 4.37,
      "end": 27.39,
      "text": " She makes me smile all the time...",
      "words": [
        { "start": 4.37, "end": 4.69, "word": " She", "probability": 0.4681 },
        { "start": 4.69, "end": 4.93, "word": " makes", "probability": 0.9718 }
      ]
    }
  ]
}
```

Fields ClipFarm depends on: top-level `schema_version`, `duration`, `segments`; per-segment `start`, `end`, `words`; per-word `start`, `end`, `word`. Other fields are tolerated but ignored. **Word strings carry a leading space when present** (faster_whisper convention) — concatenate raw, don't add separators.

On load, ClipFarm checks `schema_version == 1` and refuses (with a clear error pointing at `transcribe.py`) if it sees a higher version. A `WhisperTranscript` Pydantic model validates the shape at ingest. If `transcribe.py` ever bumps its schema, ClipFarm needs a matching adapter — but it never silently consumes an unknown shape.

### Pipelines

**1. Ingest sources.** Point at a folder of video files in `{.mov, .mp4, .m4v, .mkv}`, each ideally accompanied by a Whisper word-level JSON transcript (`<stem>.whisper.json` sibling, generated upstream by the existing `transcribe.py` — ClipFarm does **not** run Whisper itself in v0). For each pair, ClipFarm:

- Adds an entry to `sources` with `transcript_path` pointing at the sidecar on disk (transcripts are **not** embedded in `clipfarm.json` — they stay as separate files and are read on demand). Source IDs are monotonic stringified integers (`"1"`, `"2"`, ...) — opaque after creation per the data-model invariant.
- Probes `fps` via `ffprobe` (we already ship FFmpeg). If probing fails for any reason, fps is recorded as `null` and frame-precise operations later fall back to 30 fps with a one-time UI warning.
- Resolves `duration_sec` via the **sidecar wins → ffprobe → null** policy. See "Source duration policy" in Decisions locked.
- Validates the source filename — stems containing `__` (the clip-ID separator) are rejected with a clear error and an offer to rename. See the source-filename constraint in "Decisions locked."
- Segments the transcript into candidate `clips` by silence boundary (gap ≥ 2 sec between words).
- **Sidecar problems are non-fatal to the source.** If the sidecar is malformed or reports an unsupported `schema_version`, the source is still added (as footage-only with `transcript_path: null`) and the rejection is reported in the ingest result. Re-running `transcribe.py` and re-ingesting upgrades the source in-place. See "Sidecar errors don't kill the source" in Decisions locked.

On every subsequent startup, source paths are re-verified; missing files mark the source `unavailable: true` and grey-out in the UI rather than crashing the load. No LLM yet, no project yet.

**Transcript-less sources are still ingestable.** A `.mov` without a sibling `.whisper.json` is added to `sources` with `transcript_path: null` and **no auto-detected clips**. It shows in the Library with a "no transcript — footage only" badge. Still useful as:
- A source for **manual clip creation** via direct timestamp entry (`00:01:12.345 → 00:01:18.220`) — no transcript to drag-select on, so the create-from-scratch operation falls back to numeric range input (or, eventually, visual timeline scrubbing).
- A target for the future **per-clip media composition** features (see Future Ideas) — when you want to use footage *visually* (B-roll, video swap, cutaways) without caring about its audio or whether anyone spoke during it. Pairs naturally with the `tracks.video_override` schema hook already reserved on every clip.
- **Re-ingestable later**: run `transcribe.py` on the file, restart ingest, and the source picks up its transcript with full clip detection. Existing manual clips on that source are preserved.

**After ingest, the Library is immediately useful.** You can browse raw transcripts, see all auto-detected clips, run boundary correction, and create new clips by hand — without creating a project. Project + brief + tagging are layered on top whenever you're ready, not required to start.

**2. Create a project (optional, when ready).** Write a brief (markdown): name, script as lines, optional TOC (sections grouping lines), custom tags, "what's good" notes. Parse into `projects` and `project_tags`. The library is fully usable without one.

**3. Tag clips for the project — batched.** For each clip not yet tagged for this project, build a single Ollama call that takes the project brief as a shared system prompt and **N clip transcripts** as the user message, returning N structured `{section_tag_id, line_tag_id, category, confidence}` outputs. Default batch size: ~10 clips per call. The brief is repeated context (small — ~200–500 tokens); each clip transcript is short (~50–200 words). Batching turns 150 sequential calls (~2–3 min staring at a progress bar) into 15 calls (~20 sec). Ollama's KV-cache reuse on the shared prefix speeds it further. Batch size is configurable in settings — drop it if quality regresses, raise it for speed.

**4. Generate premade attempts.** For each premade strategy (longest-contiguous, best-per-line-in-order, started-with-line-X, ad-libbed-takes, etc.) — **filter `clip_project_tags` in memory** to build the clip list, one Ollama call names the attempt naturally ("the 3 times you said it in almost one take"). Insert into `attempts` with the ordered `clips` array.

**5. User edits attempts.** All edits update the active `attempts[id]` object in memory (reorder, trim offsets, add/remove clips). Saves are debounced (~500ms) and written atomically to `clipfarm.json`. Fork = duplicate the attempt object with a new `parent_attempt_id`. Replace clip = swap `clip_id` in the relevant `clips[i]` entry. Trim = update offsets. Never touches base `clips` (boundary correction does that — see above).

**6. Live preview.** Frontend resolves the attempt to a list of `(source_path, effective_start, effective_end)` ranges. Plays them with **two alternating HTML5 `<video>` elements** — one plays the current clip, the other preloads the next at `effective_start`. On `ended`, the elements swap and the just-finished one preloads what comes after. This avoids the visible flash/gap you'd otherwise get from seeking a single `<video>` element on `ended` (browsers take a moment to seek, even on local files). Not as gapless as MediaSource Extensions, but smooth enough for rough-assembly review. **Cross-source caveat**: when consecutive clips come from different `.mov` files, the alternating elements have to load a new file path between clips, which adds a perceptible ~100–300ms gap at the boundary. Accepted v0 tradeoff — single-source transitions stay smooth, and cross-source attempts are still fully usable, just with a beat of latency at each source switch. MSE is the Stage 2 upgrade if it ever matters.

**7. Export.** Resolve attempt to a list of ranges with trim applied. FFmpeg `concat` demuxer with a generated input list — stream copy where codec/fps match, re-encode only where they don't. Output MP4 at original quality. FCPXML is the same range list rendered as `<clip>` elements.

### First-run / startup behavior

**On first launch with no `clipfarm.json` present:**

1. **Empty state with a drop zone.** "Drop a folder of video files (`.mov` / `.mp4` / `.m4v` / `.mkv`, each with its Whisper transcript sidecar) here, or click to pick one." Inline help explains the transcript requirement so you don't get confused about why a folder of bare videos won't auto-segment.
2. **On drop, ingest runs.** Scans the folder, pairs each video with its `<stem>.whisper.json`, segments into clips. Progress bar.
3. **Lands on the Library page** with sources listed and clip counts populated. The **raw-transcript view of the first source is shown by default** — you can immediately browse the recording and grab clips by hand. Useful before any project exists.
4. **A persistent "create a project" CTA** lives in the corner. Optional. The library is fully usable without it.

**On subsequent launches:**
- If a `clipfarm.json` exists: skip the drop zone, load directly into the last-active view (Library, Project view, Attempt — whatever you were on).
- If projects exist but no last-active state: project picker.

**What ClipFarm expects you to drop in:**
ClipFarm consumes video files in `{.mov, .mp4, .m4v, .mkv}` **plus their pre-generated Whisper word-level JSON transcripts** (named `<stem>.whisper.json` next to the video). The dogfood folder is all `.mov`, but tolerating the common siblings avoids false negatives on the next folder Lillian drops. It does **not** run Whisper itself in v0; that's the existing `transcribe.py` pipeline's job. This keeps transcription (slow, can run overnight) and editing (interactive, daytime) cleanly separated. **Long-term**, ClipFarm could shell out to `transcribe.py` automatically when it sees an untranscribed video — captured in Future Ideas, not v0.

### Frontend pages

| Page | Purpose |
|------|---------|
| **Library** | All sources, all clips. Search, filter, browse raw transcripts. The manual escape hatch lives here. |
| **Project view** | Default view inside a project. Sidebar: section/line TOC. Main: take grid by line. |
| **Script TOC view** | Reorderable script outline; each line expands into its clip options. |
| **By-source view** | Full transcript of one source video with detected clips marked inline. |
| **Attempts page** | List of attempts for current project + active attempt detail (drag/trim/swap/fork). |
| **Brief editor** | Write/edit a project's brief (script, TOC, "what's good", tags). |
| **Settings** | Transcript folder, Ollama endpoint, model choice, export defaults. |

A persistent **live-preview pane** (resizable, dismissable) follows whatever clip you last clicked, across all pages.

### Decisions locked

- **Frontend stack**: React + Vite + Tailwind.
- **LLM model (tentative)**: Llama 3.1 8B via Ollama as the **starting** choice. Explicitly **not fully locked** — revisit if tagging quality is inadequate. The only "decision" here is that we start here, not that we end here.
- **Storage**: `clipfarm.json` is the source of truth. In-memory indexes at runtime, atomic writes on save, `watchdog` file watcher for external edits.
- **Snapshots / undo**: every destructive operation writes `.clipfarm/snapshots/<timestamp>.json` first. Last 50 retained. File-level revert; no formal undo system.
- **Schema versioning**: `"version": N` in `clipfarm.json`. A `clipfarm/migrations/` directory holds per-bump functions. Scaffolding set up at v0 with an empty `v1_to_v2.py` placeholder.
- **Sections optional**: a line can attach directly to a project with no section. Short scripts skip the section layer entirely. Handled from day one to avoid null-handling debt everywhere later.
- **Retagging is explicit**: editing a brief sets `stale: true` on affected `clip_project_tags` entries, surfaced as a UI indicator. User clicks "retag" to actually re-run the LLM. No auto-retag.
- **Cross-project clip surfacing**: data model supports it from day one (the many-to-many `clip_project_tags` array makes the query trivial), but the **UI affordance is Stage 2, not v0**. Don't slow down v0 for it.
- **Source file integrity**: missing `.mov`s mark the source `unavailable: true` and grey out in UI rather than crashing. Tags and attempts referencing the source are preserved.
- **Cross-source preview latency**: an attempt that mixes clips from different source files will have a perceptible (~100–300ms) gap at each source boundary, since the alternating `<video>` elements have to load a different file path. Single-source transitions stay smooth. Accepted v0 tradeoff. **Blind-spot watch**: the dogfood video (btc.0.4) is single-source, so v0 won't exercise this gap until the first multi-source assembly — that's the truth test for whether MSE needs to come sooner than Stage 2.
- **Conflict policy on external edit**: if `watchdog` detects an external `clipfarm.json` change while there are unsaved in-memory edits, the app **freezes all writes**, surfaces a conflict modal showing the diff between in-memory and on-disk state, and waits for the user to choose "keep mine" / "use file" / "merge manually." **No auto-resolution under any circumstances.** Phase 1 detects + logs the conflict event; the UX modal lands in Phase 2 alongside the first user-facing routes.
- **Unknown-key tolerance**: `clipfarm.json` is hand-editable by design. Unknown keys at any level are **logged with a warning and dropped on load**, never rejected. Pydantic models use `extra="ignore"`; `load_state()` does a pre-validation diff and emits one warning per dropped key so we know it happened. Our own writers can't produce extra keys (they round-trip through validated models), so the on-write surface is clean by construction — `extra="forbid"` would only ever fire on hand-edits, which is the wrong tradeoff for a file the spec explicitly invites users to edit.
- **Source filename constraint**: source filenames containing `__` are **rejected at ingest** with a clear error and an offered sanitized rename ("`my__file.mov` → `my_file.mov`?"). The `__` substring is reserved as the clip-ID separator. Even though IDs are opaque post-creation, an unparseable ID space hurts debugging and future tooling — cheaper to constrain filenames than to escape the separator.
- **Source fps detection**: at ingest, each source's frame rate is probed via `ffprobe`. On failure, `fps` is recorded as `null` and frame-precise nudge operations (Phase 10, `Cmd+Alt = ±1 frame`) fall back to 30 fps with a one-time UI warning per source. Decision sealed in Phase 2; enforcement in Phase 10.
- **Source duration policy**: `duration_sec` resolves as **sidecar `duration` wins → `ffprobe` falls back → `null`**. Rationale: `transcribe.py` reads actual audio frame counts during transcription, which is generally more accurate than `ffprobe`'s container metadata (especially on truncated or container-quirky files). When no sidecar exists or it lacks `duration`, ffprobe is the fallback. If both fail, the source is still ingested with `duration_sec: null` and the UI shows `—` for duration. Locked in Phase 2.
- **Sidecar errors don't kill the source**: malformed JSON, schema-version mismatch, or any other sidecar load failure adds the source to the ingest result's `rejected` list but **also** registers the `.mov` as a footage-only source (`transcript_path: null`, no auto-detected clips). Rationale: the user almost always wants to retry `transcribe.py` later — losing the source entry on a sidecar problem is surprising and lossy. Re-ingest after fixing the sidecar takes the "transcript newly available" upgrade path. Filename-level errors (`__` in stem) still reject hard, because the source itself is the problem. Locked in Phase 2.
- **Acceptable video extensions**: `{.mov, .mp4, .m4v, .mkv}` at ingest. Spec was originally `.mov`-only to match the dogfood folder, but constraining to one extension would false-negative on the next folder. The set covers what `ffprobe` reliably handles for this workflow.
- **Source IDs**: monotonic stringified integers (`"1"`, `"2"`, ...). JSON object keys must be strings; opaque after creation. If we ever need globally-unique IDs (multi-machine sync, etc.) that's a migration, not a v0 concern.
- **File watcher implementation**: `watchdog.observers.polling.PollingObserver` with a 0.5s poll interval, **not** the platform-default `Observer`. macOS FSEvents has documented reliability issues for rapid back-to-back single-file edits — events can be coalesced or dropped, and Phase 1 verification confirmed this on the dev machine. Polling a single `stat()` every 500ms is cheap, deterministic across platforms, and makes external-edit detection reliable enough to ground the conflict-policy invariant on. Locked in Phase 1.

---

## Future Ideas

*Things I want long-term but explicitly out of scope for v0/v1. Captured here so they're not lost.*

### Trim Mode — keyboard-only precision clip editing

A dedicated mode optimized for keyboard-only operation. The thesis: scrub wheels and hand-positioned dials are slower than the right keyboard intuition. With practice, a key combo for "cut" becomes muscle memory; you develop a feel for how much needs to be trimmed and can eventually type microsecond values directly. No mouse, no dial — the speed editor sold for hundreds of dollars is replaced by a few key combos.

Mechanics:

- **Auto-replay**: the moment you enter Trim Mode on a clip, it loops on a ~1–2 second window centered on the edit point. Every nudge instantly replays — no scrubbing back manually to hear the change.
- **Pausable replay**: spacebar to freeze, spacebar to resume. The loop stays parked at the edit point so the next nudge picks up where you were.
- **Per-side nudge keys**:
  - `[` `]` — nudge in-point left / right (start of clip)
  - `,` `.` — nudge out-point left / right (end of clip)
- **Increment modifiers**:
  - Default: ±100ms
  - Shift: ±10ms
  - Alt: ±1ms
  - Cmd+Alt: ±1 frame (uses source fps)
- **Direct numeric input**: type a number + unit (`-50ms`, `+0.5s`, `+2f` for frames) and it applies directly. For when you already know exactly how much.
- **Permissiveness controls** (per side, one button each):
  - "More permissive left" / "Tighter left" — adjusts the in-point by a configured default
  - "More permissive right" / "Tighter right" — same for out-point
  - Configurable as percentage (e.g. 5% of current clip length) OR absolute (e.g. 100ms)
  - Reasoning: most boundary fixes are one of two cases — start-of-word got cut off (more permissive left), or the clip dragged on past the end (tighter right). One button each, no thinking.

The pitch: enter Trim Mode on a clip. The clip is auto-looping in your ear. You tap `]` — the end extends 100ms — you hear it immediately. Two more taps and it's perfect. Next clip. Whole-video trim in minutes, no mouse, no scrub wheel.

### Auto-clip mode with per-clip permissiveness adjustments

A complementary mode where the AI auto-suggests clip boundaries from silence + transcript structure, and for each suggestion you tap a single permissiveness adjustment until it sounds right. Same keyboard ergonomics as Trim Mode, but applied at the AI-segmentation review layer rather than manual editing. Effectively a "review the AI's work, one tap to fix" loop — go through 50 suggested clips in a couple minutes.

### Per-clip media composition

Basic NLE-style operations that aren't core to take selection but feel like table stakes for any clip-based tool. **Deliberately basic** — ClipFarm doesn't become a full NLE; these serve assembly and preview, not creative polish (DaVinci still owns that):

- **Audio replacement from external file.** Sync an external audio recording (e.g. a separate-mic capture of the same delivery) to a clip, then replace the clip's native audio permanently. Use case: video was recorded with the camera mic, audio was recorded with a real mic on a separate device — once synced, the external track becomes the canonical audio. Data model sketch: clip gets an optional `audio_override: { file_path, start_offset_sec }` field; the resolver swaps the audio track during preview and export.
- **Visual placeholder ranges.** Black out (or color-overlay) a portion of a clip's video while keeping audio intact. Use case: "I want to replace this section with B-roll later in DaVinci, but I want the assembly to show me where the hole is *now*." Marks a `(start_sec, end_sec, color)` overlay range; it's a render-time mask, doesn't destroy the underlying video.
- **Video swap (keep audio).** Replace the video portion of a clip with a different source video while keeping the original audio. Cousin of audio replacement, opposite direction. Useful for cutaways and reaction inserts without leaving ClipFarm.

All three share a theme: clips have **independently-mutable audio and video tracks**. The schema hook for this is **already in place** — every clip has a `tracks: null` field reserved at v0 (see Data model notes for the populated shape). Implementing any of the three operations later is purely additive: writers start filling the field, readers start respecting it, no migration needed for existing clip records.

### End-state: move to a real database (sooner than "eventually")

`clipfarm.json` is the right choice **for v0** — prioritizing inspectability, hand-editability, and fast iteration. But budget the SQLite migration **sooner than "eventually"** based on real scale math: a single 30-min recording produces ~350 clips; the `05.19.26/mp4/` folder alone (~18 recordings) hits ~6k clips. Atomic-write-on-every-debounced-save holds into the low thousands; beyond that, full-file rewrite cost grows fast and starts feeling slow during normal editing.

The migration is mechanical, not a rewrite: every top-level object in the JSON maps cleanly to a table, every nested array maps to a foreign-key relationship, IDs are already strings, and the relationships were designed with relational semantics in mind from day one. When it's time, it's a serialization swap — not redesign. The `clipfarm/migrations/` scaffolding from build step 1 covers the version-bump mechanism cleanly.

**This is a stated end-goal, not a regression.** JSON-first now is right for fast iteration on a personal tool. A real database is the right end-state — likely **v1 territory, not v∞**.

### Other future ideas

- **Voice annotation training** — tag a few real annotations manually to teach the detector your phrasing patterns.
- **Multi-clip mass operations** — select N clips, re-tag / re-categorize / add to attempt all at once.
- **AI-assisted attempt narration** — "walk me through what this attempt does." LLM reads the attempt and explains the through-line in plain English.
- **Smart pre-trim from audio energy** — cut on breaths instead of silence gaps, for tighter starts and ends.
- **B-roll suggestion bucket** — clips tagged "could intercut here" relative to a target attempt.
- **Per-section auto-aggressiveness profiles** — intro gentle, body aggressive; configured per section in the brief.
- **Cross-project clip surfacing UI** — when a clip is tagged in multiple projects, show that in the clip card with a small badge. (Data already supports it; just the UI affordance.)

---

## Build order

A linear path through the spec. Each step delivers something verifiable. Designed so a fresh Claude session (or future-you) can read the spec, pick the next unchecked step, and know what to build without rebuilding context.

**This list is canonical.** `PHASES.md` references these steps by number and adds the per-phase plan + verification artifact — it does **not** duplicate the step descriptions. If a step here changes, `PHASES.md` reflects the change; if `PHASES.md` plan work surfaces a missing decision, the resolution lands here, not in the plan. One source of truth for *what*, one place for *how*.

**0. Environment setup.**
Install Ollama (`brew install ollama`), start the service, pull Llama 3.1 8B (`ollama pull llama3.1:8b`), confirm `localhost:11434` responds and can return JSON-mode output to a test prompt. Confirm at least one finished Whisper word-level JSON exists in `~/Desktop/AdAstra/2ndMind/.../05.19.26/mp4/` to use as test data. Not "build" work but unblocks everything downstream.

**1. FastAPI backend + frontend skeleton + JSON schema + safety scaffolding.**
Create the `clipfarm/` repo. FastAPI app on `localhost:8765`. Pydantic models for every entity (`Source`, `Clip`, `Project`, `ProjectTag`, `ClipProjectTag`, `Attempt`, `AttemptClip`, `VoiceAnnotation`). Atomic save (`tmp` → `fsync` → `rename`). `watchdog` file watcher with reload-on-conflict prompt. **Snapshot helper**: every destructive write routes through a wrapper that copies `clipfarm.json` to `.clipfarm/snapshots/<timestamp>.json` first, then prunes to last 50. **Migrations scaffold**: `clipfarm/migrations/` directory with an empty `v1_to_v2.py` placeholder and a runner that loads, checks version, applies migrations in order, saves at the new version. **Source integrity check** on every load: each `sources[i].path` is verified; missing files set `unavailable: true` rather than crashing. React + Vite + Tailwind scaffold served by FastAPI, empty routed pages (Library, Project, Brief editor, Settings). Confirms the stack works end-to-end before any feature work.

**2. Ingest pipeline.**
Folder-picker UI on the empty Library page. Backend endpoint scans the folder, pairs `.mov` + `<filename>.whisper.json`, segments each transcript into candidate clips by silence boundary (gap ≥ 2 sec between words), writes to `clipfarm.json`. End state: drop the `05.19.26/mp4/` folder, see source entries and detected clips in the JSON. Verify by opening `clipfarm.json` in an editor — the shape should match the spec exactly.

**3. Library page (raw transcript browser).**
Source list (left sidebar). Per-source: full raw transcript with auto-detected clip boundaries marked inline. Click a clip → it highlights. **Freeform text search across all transcripts** — "find every time I said 'self-custody'" returns a list of hits with source + timestamp + surrounding context, click to jump. **Search is word-level case-insensitive substring at v0** — `"custo"` matches the word `"custody"`, but multi-word phrase queries like `"self custody"` return zero hits even when the words appear adjacent. Phrase search and semantic search are both Future Ideas (the embeddings model is reserved for the no-script clustering path). **This alone is a meaningful unblock for btc.0.4** — you can scan the recording without watching it linearly.

**4. Boundary correction.**
Split / merge / extend / shrink / create-from-scratch / delete operations on the Library page. Mutates base clips. Propagates to (currently nonexistent) tags and attempts. Ship before the project layer because it's the manual escape hatch when AI segmentation is wrong — and segmentation will get things wrong on real recordings.

**5. Brief editor + project creation.**
A markdown editor page for writing a project brief: name, script as lines, optional TOC of sections, "what's good" notes, custom project tags. Parse the brief into `projects` and `project_tags` in `clipfarm.json`. No tagging yet — just the project shell.

**6. Ollama tagging (batched).**
Batched calls (~10 clips per call) with the brief as shared system prompt. JSON-schema-constrained output. Write to `clip_project_tags`. Explicit "retag" button; `stale: true` flag set on existing tag rows when the brief changes. Sanity-check the tagging output on a few clips before mass-running across all sources.

**7. Take grid view.**
Per-line rows. Take cards with source-name + timestamp on every card. Soft category buckets (on-script, related, standalone, fragment). **This is the moment editing btc.0.4 actually gets fast** — scan 10 deliveries of line 3 side by side.

**7b. Script TOC view (primary assembly workflow).**
Same data as the take grid, different layout. Script displayed as a reorderable outline with collapsible nodes; each line expands to show its clip options inline; one-click "use this take for this line" assembles the active attempt incrementally. **Chipotle-line style — pick one take per line, top to bottom, you're done.** The take grid (7) is for review across all lines at once; the TOC view (7b) is the actual assembly workflow. Promoted to v0 from v0.5 because assembly is the primary task, not a follow-on.

**8. Premade attempts generation.**
Generates **two buckets** of named attempts by filtering `clip_project_tags` in memory: **best plausible videos** (3–5 ship-worthy candidates — best-per-line, longest-contiguous, near-one-take, shortest-complete, energy-shift) and **diagnostic groupings** (browse-only — started-with-X, skipped-line-N, ad-lib-heavy). Each attempt has a computed `continuity_score` shown on the card. Single LLM call per attempt to name it naturally ("the 3 times you said it in almost one take"). Attempts persist in `clipfarm.json` with their `premade_bucket` field set.

**9. Live preview.**
Two alternating `<video>` elements with preloading the next clip at `effective_start`. Click any clip → plays from its range. Click an attempt → plays through in sequence with the swap-on-`ended` trick. Persistent preview pane across all pages.

**10. Attempt editing.**
Drag to reorder. "Replace this clip" action shows other takes of the same script line. Fork an attempt (duplicate with `parent_attempt_id` set). Per-attempt trim with `[` `]` `,` `.` keyboard nudges (no Trim Mode auto-replay yet — that's Future Ideas). **"Tighten internal pauses" toggle per attempt-clip** — sets `internal_pause_max_sec` to a sensible default (start with 0.5s); the resolver collapses any interior word-gap longer than this so the preview/export plays the cleaned-up version. Single button, no slider — v1 polish layer adds the full aggressiveness UI. All edits debounce-save to `clipfarm.json`.

**11. Export.**
Generate FFmpeg `concat` input list from the active attempt with trim offsets applied. Stream-copy where codec/fps match, re-encode where they don't. Output: MP4 at original quality. FCPXML deferred to v0.5.

---

**After step 11: complete v0 end-to-end.** btc.0.4 recorded → ingested → tagged → assembled → exported as MP4. Everything in Future Ideas (Trim Mode, auto-clip mode, voice annotations, cross-project surfacing UI, database migration) layers on top of this without changing the data model.

**Deferred to v0.5 (after the dogfood loop closes):** FCPXML export, multiple named attempts as switchable tabs (v0 has a single active attempt at a time), idea-bucket-as-its-own-page (v0 surfaces standalone clips inline in the Take grid).
