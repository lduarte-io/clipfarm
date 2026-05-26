"""Boundary correction — split / merge / adjust / create / delete on base
clips. The spec's "manual escape hatch when the AI gets it wrong."

Pure orchestration: each function takes `(state, args)` and mutates state
in place. No disk I/O, no transcript loading, no snapshot calls. The route
layer (`clipfarm/routes/clips.py`) is the thin shell that:
- Loads the source's `WhisperTranscript` once via the cache.
- Passes it as an explicit `transcript` parameter to any clip-producing op
  that needs to recompute `Clip.transcript_text`.
- Wraps the call in `commit_state_with_snapshot(app, reason=...)` to
  enforce the snapshot-per-op invariant.

The propagation rules (tag clone/union/drop, attempt-ref reassignment,
trim clamping) live in `clipfarm/propagation.py` so they're testable
without going through these orchestrators.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from clipfarm.models import Clip, ClipFarmState, WhisperTranscript
from clipfarm.propagation import (
    clamp_attempt_trims_for_clip,
    clone_tags_to_pair,
    drop_tags_for_clip,
    mark_attempts_needs_review_for_clip,
    reassign_attempt_refs,
    union_merge_tags,
)
from clipfarm.transcripts import transcript_text_for_range


# ---------- ID encoding (matches ingest._make_clip_id) ----------------------


def _hms(t: float) -> str:
    """HH-MM-SS.mmm — same encoding as ingest._hms. Dashes, not colons."""
    total_ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}.{ms:03d}"


def _make_clip_id(source_stem: str, start: float, end: float) -> str:
    return f"{source_stem}__{_hms(start)}__{_hms(end)}"


def _source_stem(state: ClipFarmState, source_id: str) -> str:
    """Filename stem (without extension). Used to encode new clip IDs."""
    from pathlib import Path

    return Path(state.sources[source_id].filename).stem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_or_empty(
    transcript: Optional[WhisperTranscript], start: float, end: float
) -> str:
    """Compute the transcript text for [start, end). Returns "" for
    footage-only (transcript=None) sources, matching the spec's allowance
    for clips on transcript-less sources."""
    if transcript is None:
        return ""
    return transcript_text_for_range(transcript, start, end)


# ---------- Validation helpers ------------------------------------------------


def _other_clips_on_source(
    state: ClipFarmState, source_id: str, exclude: set[str]
) -> list[tuple[str, Clip]]:
    return [
        (cid, c)
        for cid, c in state.clips.items()
        if c.source_id == source_id and cid not in exclude
    ]


def _range_overlaps_any(
    state: ClipFarmState,
    source_id: str,
    start: float,
    end: float,
    exclude: set[str],
) -> Optional[str]:
    """Returns the clip_id of the first existing clip on `source_id`
    (excluding the IDs in `exclude`) whose `[start_sec, end_sec)` overlaps
    `[start, end)`. Returns None if no overlap. Half-open intervals so
    touching endpoints don't trigger a false overlap."""
    for cid, c in _other_clips_on_source(state, source_id, exclude):
        # half-open: [s, e) overlaps [start, end) iff s < end AND start < e
        if c.start_sec < end and start < c.end_sec:
            return cid
    return None


# ---------- Operations --------------------------------------------------------


def split_clip(
    state: ClipFarmState,
    clip_id: str,
    split_at_sec: float,
    transcript: Optional[WhisperTranscript],
) -> tuple[str, str]:
    """Split `clip_id` at `split_at_sec` into two new clips C1 (left) and
    C2 (right). Original clip is removed.

    Propagation: every `clip_project_tags` row pointing at the original is
    cloned to BOTH new clips with `stale=True`; original tags are dropped.
    Every `Attempt.clips[*]` reference is reassigned to C1 with
    `Attempt.needs_review=True`.

    Returns `(c1_id, c2_id)`. Raises `KeyError` if clip not found,
    `ValueError` if `split_at_sec` is not strictly inside the clip's
    range.
    """
    clip = state.clips.get(clip_id)
    if clip is None:
        raise KeyError(f"unknown clip_id: {clip_id}")
    if not (clip.start_sec < split_at_sec < clip.end_sec):
        raise ValueError(
            f"split_at_sec={split_at_sec} must be strictly inside "
            f"[{clip.start_sec}, {clip.end_sec})"
        )

    stem = _source_stem(state, clip.source_id)
    c1_id = _make_clip_id(stem, clip.start_sec, split_at_sec)
    c2_id = _make_clip_id(stem, split_at_sec, clip.end_sec)
    if c1_id in state.clips or c2_id in state.clips:
        # Encoded IDs collide with an existing clip — extraordinarily rare
        # (would require nanosecond-aligned timestamps with a pre-existing
        # clip), but worth surfacing rather than overwriting.
        raise ValueError(
            f"new clip ID would collide with existing clip "
            f"({c1_id} or {c2_id} already in state.clips)"
        )

    now = _now()
    state.clips[c1_id] = Clip(
        source_id=clip.source_id,
        start_sec=clip.start_sec,
        end_sec=split_at_sec,
        transcript_text=_text_or_empty(transcript, clip.start_sec, split_at_sec),
        derived_from_clip_id=None,
        tracks=None,
        created_at=now,
    )
    state.clips[c2_id] = Clip(
        source_id=clip.source_id,
        start_sec=split_at_sec,
        end_sec=clip.end_sec,
        transcript_text=_text_or_empty(transcript, split_at_sec, clip.end_sec),
        derived_from_clip_id=None,
        tracks=None,
        created_at=now,
    )

    # Tag propagation: clone to both, then drop the source rows.
    clone_tags_to_pair(state, clip_id, [c1_id, c2_id], stale=True)
    drop_tags_for_clip(state, clip_id)

    # Attempt-ref propagation: reassign every reference to C1 with review flag.
    reassign_attempt_refs(state, clip_id, c1_id, mark_needs_review=True)

    del state.clips[clip_id]
    return c1_id, c2_id


def merge_clips(
    state: ClipFarmState,
    clip_ids: list[str],
    transcript: Optional[WhisperTranscript],
) -> str:
    """Merge ≥ 2 non-overlapping clips on the same source into one new
    clip spanning `(min_start, max_end)`. Silence between clips is folded
    into the merged range (the spec's "merged because you didn't pause
    long enough" path).

    Propagation: tag rows from every source clip are union-merged onto
    the new clip with dedupe on `(project_id, project_tag_id, category)`.
    Every `Attempt.clips[*]` reference to any source clip is reassigned
    to the new clip (no review flag — merge is a clean substitution).

    Returns the new clip's ID. Raises `ValueError` on cross-source,
    overlap, or fewer than 2 clips. `KeyError` if any clip not found.
    """
    if len(clip_ids) < 2:
        raise ValueError(f"merge requires >= 2 clips, got {len(clip_ids)}")
    if len(set(clip_ids)) != len(clip_ids):
        raise ValueError(f"merge clip_ids must be unique, got {clip_ids}")

    clips: list[tuple[str, Clip]] = []
    for cid in clip_ids:
        c = state.clips.get(cid)
        if c is None:
            raise KeyError(f"unknown clip_id: {cid}")
        clips.append((cid, c))

    source_ids = {c.source_id for _, c in clips}
    if len(source_ids) != 1:
        raise ValueError(
            f"merge requires clips from the same source, got source_ids={source_ids}"
        )

    clips.sort(key=lambda pair: pair[1].start_sec)
    for (_, prev), (next_id, nxt) in zip(clips, clips[1:]):
        if nxt.start_sec < prev.end_sec:
            raise ValueError(
                f"merge clips must not overlap; clip {next_id} starts at "
                f"{nxt.start_sec} but previous ends at {prev.end_sec}"
            )

    source_id = next(iter(source_ids))
    new_start = clips[0][1].start_sec
    new_end = clips[-1][1].end_sec
    stem = _source_stem(state, source_id)
    new_id = _make_clip_id(stem, new_start, new_end)
    if new_id in state.clips and new_id not in {cid for cid, _ in clips}:
        # Collision with an unrelated existing clip — surface it.
        raise ValueError(f"merged clip ID would collide with existing {new_id}")

    state.clips[new_id] = Clip(
        source_id=source_id,
        start_sec=new_start,
        end_sec=new_end,
        transcript_text=_text_or_empty(transcript, new_start, new_end),
        derived_from_clip_id=None,
        tracks=None,
        created_at=_now(),
    )

    # Tag propagation: union + dedupe.
    union_merge_tags(state, clip_ids, new_id)

    # Attempt-ref propagation: reassign every source-clip reference to
    # the new clip. No review flag (merge is a clean substitution).
    for cid in clip_ids:
        reassign_attempt_refs(state, cid, new_id, mark_needs_review=False)

    for cid in clip_ids:
        if cid != new_id:
            state.clips.pop(cid, None)

    return new_id


def adjust_clip_boundaries(
    state: ClipFarmState,
    clip_id: str,
    new_start: float,
    new_end: float,
    transcript: Optional[WhisperTranscript],
) -> None:
    """Extend or shrink an existing clip's `start_sec` / `end_sec`. Clip
    ID stays the same (spec invariant: ID is opaque after creation). Tags
    and attempt references stay attached.

    Validations:
    - `new_start < new_end` (raises `ValueError`).
    - If source has known `duration_sec`: `new_end <= duration_sec` and
      `new_start >= 0` (raises `ValueError`).
    - No overlap with any other clip on the same source — hard reject;
      no auto-shrink of neighbors (raises `ValueError`).

    Calls `clamp_attempt_trims_for_clip` after updating the base so any
    positive per-attempt trim offsets stay coherent (see propagation.py
    for the four-case rule).
    """
    clip = state.clips.get(clip_id)
    if clip is None:
        raise KeyError(f"unknown clip_id: {clip_id}")
    if new_start >= new_end:
        raise ValueError(f"new_start={new_start} must be < new_end={new_end}")

    source = state.sources.get(clip.source_id)
    if source is not None:
        if new_start < 0:
            raise ValueError(f"new_start={new_start} cannot be negative")
        if source.duration_sec is not None and new_end > source.duration_sec:
            raise ValueError(
                f"new_end={new_end} exceeds source duration "
                f"{source.duration_sec}"
            )

    overlap_id = _range_overlaps_any(
        state, clip.source_id, new_start, new_end, exclude={clip_id}
    )
    if overlap_id is not None:
        raise ValueError(
            f"new range [{new_start}, {new_end}) overlaps existing clip "
            f"{overlap_id} on the same source"
        )

    old_start, old_end = clip.start_sec, clip.end_sec
    clip.start_sec = new_start
    clip.end_sec = new_end
    clip.transcript_text = _text_or_empty(transcript, new_start, new_end)

    clamp_attempt_trims_for_clip(
        state, clip_id, old_start=old_start, old_end=old_end
    )


def create_clip_from_range(
    state: ClipFarmState,
    source_id: str,
    start_sec: float,
    end_sec: float,
    transcript: Optional[WhisperTranscript],
) -> str:
    """Create a brand-new clip on `source_id`. Starts untagged with no
    inbound references. `transcript_text` is computed from the supplied
    transcript over `[start_sec, end_sec)`, or "" for footage-only
    sources (`transcript=None`).

    Hard 400 on overlap with any existing clip on the same source.
    """
    if source_id not in state.sources:
        raise KeyError(f"unknown source_id: {source_id}")
    if start_sec >= end_sec:
        raise ValueError(f"start_sec={start_sec} must be < end_sec={end_sec}")
    if start_sec < 0:
        raise ValueError(f"start_sec={start_sec} cannot be negative")

    source = state.sources[source_id]
    if source.duration_sec is not None and end_sec > source.duration_sec:
        raise ValueError(
            f"end_sec={end_sec} exceeds source duration {source.duration_sec}"
        )

    overlap_id = _range_overlaps_any(
        state, source_id, start_sec, end_sec, exclude=set()
    )
    if overlap_id is not None:
        raise ValueError(
            f"range [{start_sec}, {end_sec}) overlaps existing clip "
            f"{overlap_id} on the same source"
        )

    stem = _source_stem(state, source_id)
    new_id = _make_clip_id(stem, start_sec, end_sec)
    if new_id in state.clips:
        raise ValueError(f"clip ID would collide with existing {new_id}")

    state.clips[new_id] = Clip(
        source_id=source_id,
        start_sec=start_sec,
        end_sec=end_sec,
        transcript_text=_text_or_empty(transcript, start_sec, end_sec),
        derived_from_clip_id=None,
        tracks=None,
        created_at=_now(),
    )
    return new_id


def delete_clip(state: ClipFarmState, clip_id: str) -> tuple[int, int]:
    """Remove `clip_id` from `state.clips`. Drops every matching
    `clip_project_tags` row. Marks every attempt that referenced this
    clip with `needs_review=True` — the `AttemptClip.clip_id` stays
    pointing at the deleted ID (deliberate tombstone; the resolver
    surfaces a "removed — pick a replacement" placeholder).

    Returns `(dropped_tag_rows, affected_attempts)`. Raises `KeyError`
    if clip not found.
    """
    if clip_id not in state.clips:
        raise KeyError(f"unknown clip_id: {clip_id}")

    dropped_tags = drop_tags_for_clip(state, clip_id)
    affected = mark_attempts_needs_review_for_clip(state, clip_id)
    del state.clips[clip_id]
    return dropped_tags, affected


__all__ = [
    "adjust_clip_boundaries",
    "create_clip_from_range",
    "delete_clip",
    "merge_clips",
    "split_clip",
]
