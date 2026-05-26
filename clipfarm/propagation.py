"""Pure propagation rules for boundary correction.

When a base clip is split / merged / deleted, the spec defines what happens
to:
- `state.clip_project_tags` rows pointing at the affected clip(s)
- `state.attempts[*].clips[*].clip_id` references to the affected clip(s)
- `state.attempts[*].clips[*].trim_*_offset` values when the base range
  shifts under them (`adjust_clip_boundaries`)

These functions are pure: they take `ClipFarmState`, mutate it in place,
and return counts. No I/O, no snapshots — the route layer (and
`commit_state_with_snapshot`) handle persistence.

Phase 4 tests these against synthetic `clip_project_tags` + `Attempt`
state. Phase 6 (LLM tagging) and Phase 8 (premade attempts) are the first
real writers; by the time they ship, these rules are tested code paths.
"""
from __future__ import annotations

import logging

from clipfarm.models import ClipFarmState, ClipProjectTag

log = logging.getLogger("clipfarm.propagation")


# ---------- Tag propagation ---------------------------------------------------


def clone_tags_to_pair(
    state: ClipFarmState,
    src_clip_id: str,
    dst_clip_ids: list[str],
    *,
    stale: bool,
) -> int:
    """For a split (`C → C1, C2`): clone every `clip_project_tags` row
    pointing at `src_clip_id` to each clip in `dst_clip_ids`, with the
    requested `stale` flag.

    Spec: tags are cloned (not split) so the user can later refine which
    half each tag actually belongs to. `stale=True` is the UI signal that
    a tag needs review after a destructive op.

    Does NOT remove the source rows — `split_clip` removes the source
    clip's tags separately so callers that need different drop semantics
    (e.g. delete) aren't forced into one policy.
    """
    sources = [t for t in state.clip_project_tags if t.clip_id == src_clip_id]
    added = 0
    for src in sources:
        for dst_id in dst_clip_ids:
            state.clip_project_tags.append(
                ClipProjectTag(
                    clip_id=dst_id,
                    project_id=src.project_id,
                    project_tag_id=src.project_tag_id,
                    category=src.category,
                    confidence=src.confidence,
                    source=src.source,
                    stale=stale,
                    notes=src.notes,
                )
            )
            added += 1
    return added


def union_merge_tags(
    state: ClipFarmState,
    src_clip_ids: list[str],
    dst_clip_id: str,
) -> int:
    """For a merge (`C1, C2 → C3`): collect every tag row across
    `src_clip_ids`, retarget them to `dst_clip_id`, dedupe on
    `(project_id, project_tag_id, category)`.

    Locked spec policy on dedupe collisions: keep the first row encountered
    in iteration order. `confidence`, `source`, `stale`, `notes` from the
    first row survive; the duplicate is dropped. Phase 6 may want to take
    `max(confidence)` instead, but the v0 dedupe is simpler and consistent.

    Returns the number of tag rows present for `dst_clip_id` after the
    union (i.e. the deduped count, not the pre-dedupe sum).
    """
    src_set = set(src_clip_ids)
    moved: list[ClipProjectTag] = []
    keep: list[ClipProjectTag] = []
    for t in state.clip_project_tags:
        if t.clip_id in src_set:
            moved.append(t)
        else:
            keep.append(t)

    seen: set[tuple] = set()
    deduped: list[ClipProjectTag] = []
    for t in moved:
        key = (t.project_id, t.project_tag_id, t.category)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            ClipProjectTag(
                clip_id=dst_clip_id,
                project_id=t.project_id,
                project_tag_id=t.project_tag_id,
                category=t.category,
                confidence=t.confidence,
                source=t.source,
                stale=t.stale,
                notes=t.notes,
            )
        )

    state.clip_project_tags = keep + deduped
    return len(deduped)


def drop_tags_for_clip(state: ClipFarmState, clip_id: str) -> int:
    """Remove every `clip_project_tags` row pointing at `clip_id`. Returns
    the number of rows dropped. Used by `split_clip` (drops the
    pre-clone source rows) and `delete_clip` (final removal)."""
    before = len(state.clip_project_tags)
    state.clip_project_tags = [
        t for t in state.clip_project_tags if t.clip_id != clip_id
    ]
    return before - len(state.clip_project_tags)


# ---------- Attempt-ref propagation -------------------------------------------


def reassign_attempt_refs(
    state: ClipFarmState,
    src_clip_id: str,
    dst_clip_id: str,
    *,
    mark_needs_review: bool,
) -> int:
    """Swap every `Attempt.clips[i].clip_id` that points at `src_clip_id`
    to `dst_clip_id`. If `mark_needs_review=True`, flips
    `Attempt.needs_review=True` on every attempt that owned at least one
    such reference.

    Used by `split_clip` (reassigns to the first half + flags review) and
    `merge_clips` (reassigns from each source clip to the new merged clip;
    no review flag — merge is a clean substitution).

    Returns the count of distinct attempts touched (not the total number
    of `AttemptClip` entries swapped, since one attempt can reference the
    same clip multiple times).
    """
    affected_attempts = 0
    for attempt in state.attempts.values():
        swapped = False
        for ac in attempt.clips:
            if ac.clip_id == src_clip_id:
                ac.clip_id = dst_clip_id
                swapped = True
        if swapped:
            affected_attempts += 1
            if mark_needs_review:
                attempt.needs_review = True
    return affected_attempts


def mark_attempts_needs_review_for_clip(
    state: ClipFarmState, clip_id: str
) -> int:
    """For `delete_clip`: flip `Attempt.needs_review=True` on every
    attempt that references `clip_id`, but **leave the
    `AttemptClip.clip_id` pointing at the now-deleted ID**. The resolver
    (Phase 7+) detects `state.clips.get(clip_id) is None` and renders a
    "removed — pick a replacement" placeholder. The dangling reference is
    deliberate; it's a tombstone the user can replace, not silent data
    loss.

    Returns the count of attempts flagged.
    """
    affected = 0
    for attempt in state.attempts.values():
        if any(ac.clip_id == clip_id for ac in attempt.clips):
            attempt.needs_review = True
            affected += 1
    return affected


# ---------- Per-attempt trim clamping -----------------------------------------


def clamp_attempt_trims_for_clip(
    state: ClipFarmState,
    clip_id: str,
    *,
    old_start: float,
    old_end: float,
) -> int:
    """After `adjust_clip_boundaries` has updated `clip.start_sec` /
    `clip.end_sec` for `clip_id`, walk every `AttemptClip` referencing it
    and adjust positive `trim_*_offset` values so each attempt's intended
    absolute effective range stays anchored as the base shifts under it.

    Trim convention (per spec):
      - `effective_start = clip.start_sec + trim_start_offset`
      - `effective_end   = clip.end_sec   - trim_end_offset`
      - Positive offsets shrink the effective range inward of the base;
        negative offsets extend past the base into raw source range.

    Clamp rules (the four cases — see PHASES.md Phase 4):

    1. Base `start_sec` moves inward (`new_start > old_start`). The
       attempt's intended absolute effective start was
       `old_start + trim_start_offset`. The new trim that preserves that
       absolute point is `(old_start + trim_start_offset) - new_start`,
       clamped to ≥ 0. If the new base start has already passed the
       intended effective start, the trim collapses to 0 (the effective
       start now sits exactly at the new base).
    2. Base `end_sec` moves inward (`new_end < old_end`). Symmetric.
       `effective_end_abs = old_end - trim_end_offset`. New trim is
       `new_end - effective_end_abs`, clamped to ≥ 0.
    3. Base moves outward (either side). Positive offsets still sit
       inside the new base; no clamp needed.
    4. Pathological clamp: after clamping, `effective_start >=
       effective_end`. The new base is too narrow for both trims to
       coexist. Collapse both offsets to 0 (effective range == new base),
       emit a `WARNING` log naming `(attempt_id, clip_id)`. The boundary
       adjustment is the user's explicit ask; we don't reject it on a
       downstream trim conflict. `Attempt.needs_review` (set by the
       calling op) gives the UI a hook to surface "trim was reset."

    Negative offsets (extend past the base into raw source range) are NOT
    clamped against the base — they're bounded by source duration, which
    isn't this function's concern. Only positive offsets are touched.

    Returns count of `AttemptClip` entries whose offsets were modified.
    """
    clip = state.clips.get(clip_id)
    if clip is None:
        return 0
    new_start, new_end = clip.start_sec, clip.end_sec
    modified = 0

    for attempt_id, attempt in state.attempts.items():
        for ac in attempt.clips:
            if ac.clip_id != clip_id:
                continue

            old_trim_s, old_trim_e = ac.trim_start_offset, ac.trim_end_offset
            new_trim_s, new_trim_e = old_trim_s, old_trim_e

            # Case 1: start side moved INWARD (new_start > old_start) with a
            # positive trim. Preserve the absolute effective start.
            # Case 3: outward move (new_start <= old_start) leaves positive
            # trims alone — they're still valid relative to the new base.
            if old_trim_s > 0 and new_start > old_start:
                effective_start_abs = old_start + old_trim_s
                new_trim_s = max(0.0, effective_start_abs - new_start)

            # Case 2: end side moved INWARD (new_end < old_end) with a
            # positive trim. Symmetric. Case 3 outward → no change.
            if old_trim_e > 0 and new_end < old_end:
                effective_end_abs = old_end - old_trim_e
                new_trim_e = max(0.0, new_end - effective_end_abs)

            # Case 4: pathological.
            eff_start = new_start + new_trim_s
            eff_end = new_end - new_trim_e
            if eff_start >= eff_end:
                log.warning(
                    "propagation: trim collapsed on attempt=%s clip=%s — "
                    "new base [%.3f, %.3f] too narrow for trims "
                    "(start_offset=%.3f, end_offset=%.3f); zeroing both",
                    attempt_id,
                    clip_id,
                    new_start,
                    new_end,
                    new_trim_s,
                    new_trim_e,
                )
                new_trim_s = 0.0
                new_trim_e = 0.0

            if new_trim_s != old_trim_s or new_trim_e != old_trim_e:
                ac.trim_start_offset = new_trim_s
                ac.trim_end_offset = new_trim_e
                modified += 1

    return modified


__all__ = [
    "clamp_attempt_trims_for_clip",
    "clone_tags_to_pair",
    "drop_tags_for_clip",
    "mark_attempts_needs_review_for_clip",
    "reassign_attempt_refs",
    "union_merge_tags",
]
