"""Continuity score — the visual indicator that distinguishes a one-take
straight-through from a heavily-Frankensteined assembly.

Pure function: takes `ClipFarmState` + a list of `AttemptClip` (the
attempt's ordered clips), returns a float in `[0.0, 1.0]`.

**Formula (locked in PHASES.md):** walk the attempt's clips in order,
group consecutive clips into "runs" where each run satisfies BOTH:

- same `source_id`, AND
- the next clip's `start_sec` ≥ current clip's `end_sec` (progressing
  forward in source-time; no jumping back, no inserting earlier
  material from the same source).

Sum each run's wall-clock runtime (`sum(end - start)` for the run's
clips). The continuity score is `max_run_runtime / total_attempt_runtime`.

**Edge cases (deliberately covered):**

- Single-clip attempt = 1.0 (a single clip is itself a run of 1, and
  the run covers 100% of runtime).
- Empty attempt clip list raises `ValueError` — there's no meaningful
  "continuity of nothing." The orchestrator never passes an empty
  list; this is defense-in-depth for future callers.
- Zero-runtime attempt (every clip has `start == end`) raises
  `ValueError` — division by zero would be meaningless and the data
  is broken upstream anyway.
- "0.0 not actually reachable" — a single-clip attempt is its own run
  of 100%. Two clips from different sources give the minimum of
  `min(c1, c2) / (c1 + c2)`, which trends toward 0.5 for equal-
  duration clips and toward 0 for one tiny clip + one huge clip but
  never literally reaches 0. The on-disk cache field is `[0.0, 1.0]`
  for completeness; in practice expect `[0.3, 1.0]`.

**Cache invariant** (CLAUDE.md data-model rule): `Attempt.continuity_score`
is a derived cache. Recompute on every attempt write; readers should
be willing to recompute when they suspect drift.
"""
from __future__ import annotations

from typing import Iterable

from clipfarm.models import AttemptClip, ClipFarmState


def _attempt_clip_runtime(state: ClipFarmState, ac: AttemptClip) -> float:
    """Wall-clock runtime of one `AttemptClip` — base clip end - start,
    minus any per-attempt trim offsets.

    Phase 8 doesn't populate trim offsets (`trim_start_offset` /
    `trim_end_offset` stay at 0.0 for all premade attempts), but
    this function honors them so Phase 10's edits don't need to
    rewrite the formula.
    """
    base = state.clips.get(ac.clip_id)
    if base is None:
        # Orphan attempt clip — boundary correction should clean these
        # up, but defensive: treat as zero-runtime so a stale attempt
        # doesn't crash the continuity calculation.
        return 0.0
    duration = base.end_sec - base.start_sec
    # trim_*_offset positive shrinks; negative extends. v0 doesn't use
    # them but the formula supports both.
    trimmed = duration - ac.trim_start_offset - ac.trim_end_offset
    return max(0.0, trimmed)


def _runs(
    state: ClipFarmState, attempt_clips: list[AttemptClip]
) -> Iterable[tuple[float, list[AttemptClip]]]:
    """Yield `(run_runtime, run_clips)` for each contiguous-in-source
    run of the attempt's clip list.

    Run boundary: source change, OR backward jump in source-time
    (next clip's `start_sec` < current clip's `end_sec`).
    """
    if not attempt_clips:
        return
    current_run: list[AttemptClip] = []
    current_runtime = 0.0
    prev_source: str | None = None
    prev_end: float = -1.0

    for ac in attempt_clips:
        base = state.clips.get(ac.clip_id)
        if base is None:
            # Treat as a run-breaker — an orphan reference can't be
            # contiguous with anything.
            if current_run:
                yield current_runtime, current_run
                current_run = []
                current_runtime = 0.0
            prev_source = None
            prev_end = -1.0
            continue
        starts_new_run = (
            prev_source is None
            or base.source_id != prev_source
            or base.start_sec < prev_end
        )
        if starts_new_run and current_run:
            yield current_runtime, current_run
            current_run = []
            current_runtime = 0.0
        current_run.append(ac)
        current_runtime += _attempt_clip_runtime(state, ac)
        prev_source = base.source_id
        prev_end = base.end_sec

    if current_run:
        yield current_runtime, current_run


def compute_continuity_score(
    state: ClipFarmState, attempt_clips: list[AttemptClip]
) -> float:
    """Return the continuity score for `attempt_clips` against `state`.

    Raises `ValueError` on empty clip list or zero-runtime attempt —
    both indicate broken data upstream that the orchestrator should
    never produce.
    """
    if not attempt_clips:
        raise ValueError("continuity_score is undefined for empty attempt clips")
    runs = list(_runs(state, attempt_clips))
    if not runs:
        # All clips were orphans → degenerate.
        raise ValueError(
            "continuity_score: every clip in the attempt is missing from state.clips"
        )
    total_runtime = sum(rt for rt, _ in runs)
    if total_runtime <= 0.0:
        raise ValueError(
            "continuity_score: attempt has zero total runtime (clip durations sum to 0)"
        )
    max_run = max(rt for rt, _ in runs)
    return max_run / total_runtime


__all__ = ["compute_continuity_score"]
