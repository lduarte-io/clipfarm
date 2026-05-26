"""Attempt-domain operations: create / fork / rename / patch-clips / delete.

Pure orchestration over `ClipFarmState`. The HTTP layer in
`clipfarm/routes/attempts.py` wraps these with the save-lock +
snapshot-then-commit pattern; these functions just mutate state and
return the resulting `Attempt` (or None for delete).

**`_next_attempt_id` moved here from `premade.py`** so both the
premade orchestrator and the new hand-built/fork/rename/patch/delete
routes can share the same allocator. Same monotonic-string-int
behavior, same comment about not reusing freed slots.

**`replace_attempt_clips` validation rules (Phase 10a plan-review):**

1. PATCH-to-empty allowed; `continuity_score=None` for empty.
2. Existing tombstones in the current attempt (clip_id present on
   the attempt + missing from `state.clips`) pass through verbatim
   on PATCH.
3. New clip_ids in the body must exist in `state.clips`. Submitting
   a clip_id that wasn't already in the attempt AND isn't in
   `state.clips` is data corruption → raise ValueError.
4. Tombstones can be dropped from the list (replace UI is Phase 10b;
   dropping a slot is a pure clip-list edit and works in 10a).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from clipfarm.continuity import compute_continuity_score
from clipfarm.models import Attempt, AttemptClip, ClipFarmState

log = logging.getLogger("clipfarm.attempts_ops")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_attempt_id(state: ClipFarmState) -> str:
    """Monotonic stringified integer over ALL existing attempt IDs.
    Same pattern as `_next_source_id` / `_next_project_id`. Doesn't
    reuse freed slots so snapshot diffs stay readable across
    delete-then-create sequences."""
    used = {int(k) for k in state.attempts.keys() if k.isdigit()}
    return str(max(used) + 1) if used else "1"


def refresh_attempt_continuity(
    state: ClipFarmState, attempt: Attempt,
) -> None:
    """Recompute `attempt.continuity_score` from the current clip list.

    Empty clip list → None.
    All-tombstone clip list → None (no playable runtime).
    Otherwise → `compute_continuity_score(state, clips)`.

    Called after every mutation to the clip list (create / fork /
    patch / boundary-correction propagation). Honors the data-model
    invariant that `continuity_score` is a cache, not a source of
    truth — readers should be willing to recompute.
    """
    if not attempt.clips:
        attempt.continuity_score = None
        return
    try:
        attempt.continuity_score = compute_continuity_score(state, attempt.clips)
    except ValueError as e:
        # Degenerate cases (all orphans, zero total runtime, etc.).
        log.info(
            "attempts: continuity recompute degenerate for attempt clips "
            "(%s); setting to None", e,
        )
        attempt.continuity_score = None


# ─────────────────────────────────────────────────────────────────────────────
# CRUD orchestrators
# ─────────────────────────────────────────────────────────────────────────────


def create_hand_built_attempt(
    state: ClipFarmState,
    project_id: str,
    *,
    name: Optional[str] = None,
    clips: Optional[list[AttemptClip]] = None,
) -> tuple[str, Attempt]:
    """Create a new attempt with `source="hand-built"`.

    Empty `clips` is the default (a fresh blank attempt; user adds
    clips via PATCH later). When clips are supplied, they're
    validated through the same rules as `replace_attempt_clips`:
    every clip_id must exist in `state.clips` (no tombstones on
    create — there's no "existing tombstone" carryover at create
    time).

    Raises `KeyError` if `project_id` doesn't exist.
    Raises `ValueError` if any seed clip_id isn't in `state.clips`.
    """
    if project_id not in state.projects:
        raise KeyError(f"unknown project_id: {project_id}")

    seed_clips: list[AttemptClip] = list(clips or [])
    for ac in seed_clips:
        if ac.clip_id not in state.clips:
            raise ValueError(
                f"unknown clip_id: {ac.clip_id!r} (cannot seed attempt with "
                f"a clip that doesn't exist in state.clips)"
            )

    aid = next_attempt_id(state)
    attempt = Attempt(
        project_id=project_id,
        name=name or "untitled attempt",
        parent_attempt_id=None,
        source="hand-built",
        premade_bucket=None,
        clips=seed_clips,
        needs_review=False,
        created_at=_now_iso(),
    )
    refresh_attempt_continuity(state, attempt)
    state.attempts[aid] = attempt
    return aid, attempt


def fork_attempt(
    state: ClipFarmState, attempt_id: str,
) -> tuple[str, Attempt]:
    """Clone an attempt with `source="fork"`, `parent_attempt_id=<original>`,
    same clip list. Recomputes continuity (in case the original's
    cache was stale).

    Raises `KeyError` if `attempt_id` is unknown.
    """
    original = state.attempts.get(attempt_id)
    if original is None:
        raise KeyError(f"unknown attempt_id: {attempt_id}")

    aid = next_attempt_id(state)
    new_attempt = Attempt(
        project_id=original.project_id,
        name=f"fork of {original.name}",
        parent_attempt_id=attempt_id,
        source="fork",
        premade_bucket=None,
        clips=[ac.model_copy() for ac in original.clips],
        needs_review=False,
        created_at=_now_iso(),
    )
    refresh_attempt_continuity(state, new_attempt)
    state.attempts[aid] = new_attempt
    return aid, new_attempt


def rename_attempt(
    state: ClipFarmState, attempt_id: str, new_name: str,
) -> Attempt:
    """Update an attempt's `name` only. Doesn't touch clips or
    continuity (rename is a no-op on either)."""
    attempt = state.attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(f"unknown attempt_id: {attempt_id}")
    stripped = new_name.strip()
    if not stripped:
        raise ValueError("attempt name must not be empty or whitespace-only")
    attempt.name = stripped
    return attempt


def replace_attempt_clips(
    state: ClipFarmState,
    attempt_id: str,
    new_clips: list[AttemptClip],
) -> Attempt:
    """Replace the attempt's clip list. The full set of validation
    rules per Phase 10a plan-review #1-#4:

    1. Empty list is allowed (`continuity_score=None`).
    2. Existing tombstones (clip_id present on the CURRENT attempt +
       missing from `state.clips`) pass through verbatim if listed.
    3. New clip_ids (not in current attempt + not in `state.clips`)
       → ValueError("unknown clip_id: ...").
    4. Tombstones can be dropped (just omit them from the new list).

    Recomputes continuity on every successful write.
    """
    attempt = state.attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(f"unknown attempt_id: {attempt_id}")

    current_clip_ids = {ac.clip_id for ac in attempt.clips}
    for ac in new_clips:
        if ac.clip_id in state.clips:
            # Live clip — always valid.
            continue
        if ac.clip_id in current_clip_ids:
            # Preserved tombstone from the current attempt — pass through.
            continue
        # New clip_id, not in state.clips, not a preserved tombstone → bad.
        raise ValueError(
            f"unknown clip_id: {ac.clip_id!r} (not in state.clips and not a "
            f"preserved tombstone from the current attempt)"
        )

    attempt.clips = list(new_clips)
    refresh_attempt_continuity(state, attempt)
    return attempt


def delete_attempt(state: ClipFarmState, attempt_id: str) -> Attempt:
    """Remove an attempt from `state.attempts`. Returns the deleted
    `Attempt` for caller logging.

    **Forks-of-deleted-parent semantics** (Phase 10a plan-review #4):
    forks whose `parent_attempt_id` pointed at this attempt are NOT
    cascaded — their `parent_attempt_id` becomes a dangling reference,
    matching Phase 4's tombstone-for-deleted-clip pattern. User work
    is preserved; the UI renders "fork of [deleted attempt #N]" when
    the parent isn't found.

    Raises `KeyError` if `attempt_id` is unknown.
    """
    attempt = state.attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(f"unknown attempt_id: {attempt_id}")
    del state.attempts[attempt_id]
    # Don't cascade to forks. Their parent_attempt_id stays as-is;
    # this is the dangling-reference pattern, matching Phase 4.
    log.info("attempts: deleted attempt %s (%r)", attempt_id, attempt.name)
    return attempt


__all__ = [
    "create_hand_built_attempt",
    "delete_attempt",
    "fork_attempt",
    "next_attempt_id",
    "refresh_attempt_continuity",
    "rename_attempt",
    "replace_attempt_clips",
]
