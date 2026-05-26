"""Phase 9 — attempt → playback-range resolver.

Pure orchestration. Given an `Attempt`, return the ordered list of
playable spans (`ResolvedRange`) and tombstone placeholders
(`TombstoneRange`) for dangling clips. Phase 9's preview reads this
output; **Phase 11's export reads the same output** — keeping the
trim / gap-drop / clamp rules in one module is the whole point.

### Resolver contract (locked Phase 9 plan)

1. **Item order matches `Attempt.clips` order.** Caller can correlate
   resolved items back to attempt-clip slot index for UI highlighting.
2. **Dangling clip → exactly one `TombstoneRange` item.** Phase 4's
   `delete_clip` removes the base clip from `state.clips` but keeps the
   `AttemptClip` entry on the attempt + sets `Attempt.needs_review =
   True`. The slot is preserved so the user can pick a replacement.
3. **Live clip → ≥1 `ResolvedRange` items.** Multiple only when
   `internal_pause_max_sec` splits the trimmed span on inter-word gaps.
4. **Trim offsets clamped twice**:
   - Base-bounds clamp happens in Phase 4's `clamp_attempt_trims_for_clip`
     on boundary-correction.
   - **Source-bounds clamp happens here**: `effective_start =
     max(0.0, start)`, `effective_end = min(source.duration_sec or
     +inf, end)`. Log a warning when clamping fires (so Phase 10's
     trim UI dev can see they pushed past source bounds).
5. **Missing transcript + `internal_pause_max_sec is not None`** →
   fallback to a single un-expanded `ResolvedRange`. Log warning.
   Phase 11 export uses the same fallback.

### `internal_pause_max_sec` semantic (locked Phase 9 plan + spec)

Inter-word gaps **strictly greater than** `internal_pause_max_sec`
split the trimmed span into multiple `ResolvedRange` items; **the
gap itself is dropped entirely** between sub-ranges (NOT collapsed-
to-max). This is the simpler interpretation that matches what the
preview / export will actually do without inserting silent video.
"""
from __future__ import annotations

import logging
import math
from typing import Literal, Optional, Union

from pydantic import Field

from clipfarm.models import (
    AttemptClip,
    ClipFarmState,
    StrictModel,
)
from clipfarm.transcripts import load_transcript_for_source

log = logging.getLogger("clipfarm.resolver")


# ─────────────────────────────────────────────────────────────────────────────
# Output types — discriminated union by `type`.
# ─────────────────────────────────────────────────────────────────────────────


class ResolvedRange(StrictModel):
    """One playable span. The frontend's `<video>` element gets:
      - `source_id` → builds `/api/sources/{source_id}/video` URL
      - `effective_start_sec` → `video.currentTime` after `loadedmetadata`
      - `effective_end_sec` → `timeupdate` watcher's cutoff
    `clip_id` is informational only (correlate back to the
    `AttemptClip` for UI highlighting).
    """

    type: Literal["range"] = "range"
    clip_id: str
    source_id: str
    effective_start_sec: float
    effective_end_sec: float


class TombstoneRange(StrictModel):
    """Placeholder for a `AttemptClip` whose base clip was deleted in
    boundary correction (Phase 4). The preview pane shows a "▢ Removed
    clip — pick a replacement" card and auto-advances after 2s; the
    Attempts page (Phase 10) will let the user pick a replacement
    clip via the slot."""

    type: Literal["tombstone"] = "tombstone"
    clip_id: str  # the deleted clip's ID, preserved on the AttemptClip
    reason: str = "clip referenced by attempt was deleted (Phase 4 boundary correction)"


ResolvedItem = Union[ResolvedRange, TombstoneRange]


# ─────────────────────────────────────────────────────────────────────────────
# Pure resolver
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_one(
    state: ClipFarmState, ac: AttemptClip
) -> list[ResolvedItem]:
    """Resolve a single `AttemptClip` into ≥1 `ResolvedRange` items, or
    exactly one `TombstoneRange` if the base clip is missing."""
    clip = state.clips.get(ac.clip_id)
    if clip is None:
        return [TombstoneRange(clip_id=ac.clip_id)]

    source = state.sources.get(clip.source_id)
    # Source might be missing too (orphan record); treat as tombstone-ish.
    # Resolved as a single range with the data we have; the video route
    # will 404/410 on the source_id and the player will surface the error.
    if source is None:
        log.warning(
            "resolver: clip %s references missing source %s — emitting range anyway",
            ac.clip_id, clip.source_id,
        )

    # Raw trimmed span before source-bounds clamping.
    raw_start = clip.start_sec + ac.trim_start_offset
    raw_end = clip.end_sec - ac.trim_end_offset

    # Source-bounds clamping (plan-review #2). Phase 4 handles base-bounds;
    # this is the source-bounds backstop Phase 4 explicitly declined.
    source_duration = (
        source.duration_sec
        if source is not None and source.duration_sec is not None
        else math.inf
    )
    effective_start = max(0.0, raw_start)
    effective_end = min(source_duration, raw_end)
    if effective_start != raw_start:
        log.warning(
            "resolver: clip %s effective_start clamped from %.3f to %.3f (source 0.0)",
            ac.clip_id, raw_start, effective_start,
        )
    if effective_end != raw_end:
        log.warning(
            "resolver: clip %s effective_end clamped from %.3f to %.3f (source duration %.3f)",
            ac.clip_id, raw_end, effective_end, source_duration,
        )

    if effective_end <= effective_start:
        # Defense-in-depth — should never reach here in well-formed state.
        raise ValueError(
            f"resolver: clip {ac.clip_id} has zero/negative effective duration "
            f"after clamp ({effective_start:.3f} → {effective_end:.3f})"
        )

    # No internal-pause expansion → single range.
    if ac.internal_pause_max_sec is None:
        return [ResolvedRange(
            clip_id=ac.clip_id,
            source_id=clip.source_id,
            effective_start_sec=effective_start,
            effective_end_sec=effective_end,
        )]

    # internal_pause_max_sec set — try to expand by walking the
    # transcript words inside the trimmed span. If the source has no
    # transcript (or it fails to load), fall back to single range +
    # warning (contract rule #5).
    if source is None:
        log.warning(
            "resolver: internal_pause_max_sec set on clip %s but source is missing; "
            "falling back to single un-expanded range",
            ac.clip_id,
        )
        return [ResolvedRange(
            clip_id=ac.clip_id,
            source_id=clip.source_id,
            effective_start_sec=effective_start,
            effective_end_sec=effective_end,
        )]
    transcript = load_transcript_for_source(source)
    if transcript is None:
        log.warning(
            "resolver: internal_pause_max_sec set on clip %s but transcript "
            "unavailable for source %s; falling back to single un-expanded range",
            ac.clip_id, clip.source_id,
        )
        return [ResolvedRange(
            clip_id=ac.clip_id,
            source_id=clip.source_id,
            effective_start_sec=effective_start,
            effective_end_sec=effective_end,
        )]

    # Walk words inside [effective_start, effective_end). Find inter-word
    # gaps strictly > max_pause; split the span at each.
    max_pause = ac.internal_pause_max_sec
    # Flatten words.
    words = [w for seg in transcript.segments for w in seg.words]
    in_range = [w for w in words if w.start >= effective_start and w.end <= effective_end]

    if len(in_range) < 2:
        # Zero or one word inside the span — no internal gaps to consider.
        return [ResolvedRange(
            clip_id=ac.clip_id,
            source_id=clip.source_id,
            effective_start_sec=effective_start,
            effective_end_sec=effective_end,
        )]

    sub_ranges: list[ResolvedRange] = []
    sub_start = effective_start
    prev_word_end = in_range[0].end
    for w in in_range[1:]:
        gap = w.start - prev_word_end
        if gap > max_pause:
            # Emit sub-range ending at prev_word_end. Drop the gap entirely.
            sub_ranges.append(ResolvedRange(
                clip_id=ac.clip_id,
                source_id=clip.source_id,
                effective_start_sec=sub_start,
                effective_end_sec=prev_word_end,
            ))
            sub_start = w.start
        prev_word_end = w.end
    # Trailing sub-range.
    sub_ranges.append(ResolvedRange(
        clip_id=ac.clip_id,
        source_id=clip.source_id,
        effective_start_sec=sub_start,
        effective_end_sec=effective_end,
    ))
    return sub_ranges


def resolve_attempt(
    state: ClipFarmState, attempt_id: str
) -> list[ResolvedItem]:
    """Return ordered playback items for `attempt_id`.

    Raises `KeyError` on unknown attempt; route layer translates to 404.
    """
    attempt = state.attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(f"unknown attempt_id: {attempt_id}")
    items: list[ResolvedItem] = []
    for ac in attempt.clips:
        items.extend(_resolve_one(state, ac))
    return items


__all__ = [
    "ResolvedItem",
    "ResolvedRange",
    "TombstoneRange",
    "resolve_attempt",
]
