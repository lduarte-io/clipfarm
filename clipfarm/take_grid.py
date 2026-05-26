"""Take Grid view — Phase 7 read-side of the project layer.

After Phase 6's `tag_project` writes rows to `state.clip_project_tags`,
this module makes them legible: lays out each script line as a row of
"take cards" plus four collapsible buckets for the off-line categories.

Pure orchestration over `ClipFarmState` + the Whisper sidecars (read via
the existing `transcripts.load_transcript_for_source` cache — so a
take-grid build for a fresh project that just got tagged costs one
sidecar read per source, and subsequent builds are warm-cache cheap).

No mutations. The route layer that wraps this acquires no lock and
writes no snapshot — see `clipfarm/routes/take_grid.py`.

**Sort order (locked Phase 7 plan):**
- Cards within a line row: `confidence DESC, start_sec ASC`. Best match
  first; ties broken by recording order.
- Cards within a bucket: `start_sec ASC` only. Buckets aren't ranked
  against a specific match target, so confidence isn't a meaningful
  primary key — recording order is what the user wants to scan.

**`first_word_index`** is computed server-side per card so the frontend
can navigate `/library?source=<sid>&word=<idx>` without walking the
transcript itself. We need the sidecar anyway for nothing else in v0,
but the existing transcript cache makes this nearly free. If the source
has no sidecar (footage-only) or the sidecar fails to load, the card
gets `first_word_index=None` and the Open-in-Library affordance falls
back to the source-only URL (Library will land on the source with no
focus jump).
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from clipfarm.models import Category, ClipFarmState, StrictModel
from clipfarm.transcripts import load_transcript_for_source

# The four bucket keys are exactly the non-"on-script" categories — the
# `on-script` rows live inside `lines[]` and don't appear in `buckets`.
BUCKET_CATEGORIES: tuple[Category, ...] = (
    "related-but-different",
    "standalone-idea",
    "off-topic",
    "fragment",
)


class TakeCard(StrictModel):
    """One card in the take grid.

    `project_tag_id` carries the line tag id if any (only populated for
    cards in `lines[].cards`; bucket cards have it None unless the LLM
    tagged the clip both as e.g. `standalone-idea` AND attached a line —
    which is rare but possible under our v0 schema).
    """

    clip_id: str
    source_id: str
    filename: str
    start_sec: float
    end_sec: float
    transcript_text: str
    category: Category
    confidence: float
    project_tag_id: Optional[str] = None
    stale: bool = False
    # Index into the source's flattened Whisper word list of the first
    # word whose `start >= clip.start_sec`. `None` when the source has no
    # sidecar or the sidecar can't be loaded. Frontend uses this for
    # `/library?source=<id>&word=<idx>` Open-in-Library navigation.
    first_word_index: Optional[int] = None


class LineRow(StrictModel):
    tag_id: str
    name: str
    order_idx: int
    cards: list[TakeCard] = Field(default_factory=list)


class BucketView(StrictModel):
    cards: list[TakeCard] = Field(default_factory=list)


class TakeGridSummary(StrictModel):
    untagged_clips: int
    stale_clips: int
    total_tagged: int


class TakeGridView(StrictModel):
    """The full project take-grid payload.

    `lines` always contains one entry per `ProjectTag(kind="line")` in
    the project, in `order_idx` order — even lines with zero matched
    takes appear as empty rows (so the user can see "I have 12 lines, 11
    have matches, line 4 has nothing").

    `buckets` is keyed by the four bucket category names. Entries are
    always present (possibly empty).
    """

    project_id: str
    name: str
    lines: list[LineRow] = Field(default_factory=list)
    buckets: dict[str, BucketView] = Field(default_factory=dict)
    summary: TakeGridSummary


# --- Pure orchestrator -------------------------------------------------------


def _first_word_index_for_clip(
    word_starts_by_source: dict[str, Optional[list[float]]],
    state: ClipFarmState,
    clip_id: str,
) -> Optional[int]:
    """Look up the first-word-index for `clip_id`. `word_starts_by_source`
    is the per-source memoization dict the caller built earlier in
    `build_take_grid` — None means "transcript unavailable for this
    source"; a list is the cached flat-word `start` sequence."""
    clip = state.clips.get(clip_id)
    if clip is None:
        return None
    starts = word_starts_by_source.get(clip.source_id)
    if starts is None:
        return None
    # Linear scan is fine — even btc.0.4's ~4700 words is sub-millisecond.
    # bisect would be tighter but adds zero perceptible speed for ~hundreds
    # of cards * one scan each, and the linear form reads more obviously.
    target = clip.start_sec
    for idx, ws in enumerate(starts):
        if ws >= target:
            return idx
    # All words end before this clip starts → degenerate but tolerated;
    # return None rather than the out-of-range last index.
    return None


def build_take_grid(state: ClipFarmState, project_id: str) -> TakeGridView:
    """Build the take grid for `project_id`. Raises `KeyError` if the
    project doesn't exist — the route layer translates that to a 404.

    Pure: reads `state` + the on-disk Whisper sidecars (via the cache),
    returns a fresh `TakeGridView`. No mutations.
    """
    project = state.projects.get(project_id)
    if project is None:
        raise KeyError(f"unknown project_id: {project_id}")

    # Filename resolution per source — saves the frontend an N+1 lookup.
    filename_by_source: dict[str, str] = {
        sid: src.filename for sid, src in state.sources.items()
    }

    # Memoized per-source flat-word `start` lists, lazily populated as
    # we encounter clips on each source. Sentinel: a key mapped to None
    # means "we tried, no transcript available."
    word_starts_by_source: dict[str, Optional[list[float]]] = {}

    def _ensure_word_starts(source_id: str) -> None:
        if source_id in word_starts_by_source:
            return
        source = state.sources.get(source_id)
        if source is None:
            word_starts_by_source[source_id] = None
            return
        transcript = load_transcript_for_source(source)
        if transcript is None:
            word_starts_by_source[source_id] = None
            return
        word_starts_by_source[source_id] = [
            w.start for seg in transcript.segments for w in seg.words
        ]

    # Index every clip_project_tag for this project.
    rows_for_project = [
        r for r in state.clip_project_tags if r.project_id == project_id
    ]

    # Build the per-line + per-bucket card lists.
    cards_by_line: dict[str, list[TakeCard]] = {}
    cards_by_bucket: dict[str, list[TakeCard]] = {
        cat: [] for cat in BUCKET_CATEGORIES
    }

    for row in rows_for_project:
        clip = state.clips.get(row.clip_id)
        if clip is None:
            # Defensive: a clip referenced by a tag row but missing
            # from state.clips. Shouldn't happen in a well-formed state
            # (boundary correction cleans tag rows on delete), but skip
            # silently rather than crash the grid build.
            continue
        _ensure_word_starts(clip.source_id)
        card = TakeCard(
            clip_id=row.clip_id,
            source_id=clip.source_id,
            filename=filename_by_source.get(clip.source_id, "?"),
            start_sec=clip.start_sec,
            end_sec=clip.end_sec,
            transcript_text=clip.transcript_text,
            category=row.category,
            confidence=row.confidence,
            project_tag_id=row.project_tag_id,
            stale=row.stale,
            first_word_index=_first_word_index_for_clip(
                word_starts_by_source, state, row.clip_id
            ),
        )
        if row.category == "on-script" and row.project_tag_id is not None:
            cards_by_line.setdefault(row.project_tag_id, []).append(card)
        elif row.category in BUCKET_CATEGORIES:
            cards_by_bucket[row.category].append(card)
        # Edge: `on-script` with no `project_tag_id` is an LLM-output
        # quirk — the orchestrator already drops these via the
        # hallucinated-line-id guard, but if one slipped in we treat it
        # like a bucket-less orphan and drop it from the grid.

    # Materialize `lines` in tag order. Every line gets a row, even
    # empty ones — the user wants to see "line 4 has zero matches" as a
    # visible gap, not a hidden one.
    line_tags = sorted(
        (
            (tid, tag) for tid, tag in project.tags.items() if tag.kind == "line"
        ),
        key=lambda kv: (kv[1].order_idx, kv[0]),
    )
    lines: list[LineRow] = []
    for tid, tag in line_tags:
        cards = cards_by_line.get(tid, [])
        # confidence DESC, start_sec ASC.
        cards.sort(key=lambda c: (-c.confidence, c.start_sec))
        lines.append(
            LineRow(
                tag_id=tid,
                name=tag.name,
                order_idx=tag.order_idx,
                cards=cards,
            )
        )

    # Materialize buckets. start_sec ASC.
    buckets: dict[str, BucketView] = {}
    for cat in BUCKET_CATEGORIES:
        cards = cards_by_bucket[cat]
        cards.sort(key=lambda c: c.start_sec)
        buckets[cat] = BucketView(cards=cards)

    # Summary counters.
    #
    # Definitions (locked in PHASES.md):
    # - `untagged_clips`: clips that exist in `state.clips` but have NO
    #   row in `clip_project_tags` for this project. These are the
    #   "Tag clips" button's queue.
    # - `stale_clips`: clips that have AT LEAST ONE row for this
    #   project flagged stale. A clip with one stale + one fresh row
    #   counts in both `stale_clips` and `total_tagged`. The sets are
    #   not required to be disjoint.
    # - `total_tagged`: clips that have at least one row for this
    #   project (stale or fresh). Tagged-with-only-stale-rows still
    #   counts toward total_tagged.
    clips_with_any_row: set[str] = set()
    clips_with_stale_row: set[str] = set()
    for row in rows_for_project:
        clips_with_any_row.add(row.clip_id)
        if row.stale:
            clips_with_stale_row.add(row.clip_id)
    untagged = sum(1 for cid in state.clips if cid not in clips_with_any_row)

    return TakeGridView(
        project_id=project_id,
        name=project.name,
        lines=lines,
        buckets=buckets,
        summary=TakeGridSummary(
            untagged_clips=untagged,
            stale_clips=len(clips_with_stale_row),
            total_tagged=len(clips_with_any_row),
        ),
    )


__all__ = [
    "BUCKET_CATEGORIES",
    "BucketView",
    "LineRow",
    "TakeCard",
    "TakeGridSummary",
    "TakeGridView",
    "build_take_grid",
]
