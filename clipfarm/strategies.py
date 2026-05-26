"""Phase 8 premade-attempt strategies — eight pure functions over
`ClipFarmState`, each producing zero or more `StrategyResult` records.

The orchestrator in `clipfarm/premade.py` runs every strategy, dedups
across them by clip-list equality, names the survivors via a batched
LLM call, and persists the result as `Attempt` records.

### Strategy taxonomy

**Best plausible (`premade_bucket="best"`) — 5 strategies:**

1. `best_per_line_in_script_order` — highest-confidence on-script clip per script line, assembled in script order. The greatest-hits version.
2. `longest_contiguous_take` — the single take that covers the most distinct script lines on-script (tiebreak: total runtime).
3. `near_one_take` — up to 3 SEPARATE attempts, one per qualifying take with ≥ 70% line coverage and ≤ 2 internal fragments. Each carries continuity ≈ 1.0.
4. `shortest_complete` — shortest on-script clip per line. Every line covered, minimum runtime. For length-constrained cuts.
5. `energy_shift` — highest words-per-second on-script clip per line. **v0 heuristic — revisit when audio analysis lands.**

**Diagnostic (`premade_bucket="diagnostic"`) — 3 strategies:**

6. `started_with_line` — groups takes by which line opened them; one attempt per group with ≥ 2 takes. Cap at top 3 groups.
7. `skipped_line` — groups takes by which line they skipped (covered ≥ 60% of OTHER lines but not this one). Cap at top 3 lines.
8. `ad_libbed` — takes with ≥ 2 ad-lib clips (related-but-different / standalone-idea) intermixed with their on-script runs. Cap at top 3 by ad-lib count.

### "Take" definition (shared by strategies 3, 6, 7, 8)

A *take* is a maximal contiguous-in-source run of clips that contains
at least one on-script clip for this project, allowing up to 2 fragment
clips inside without breaking the run. Different sources are different
takes by definition; within a source, takes are separated either by
non-fragment/non-on-script clips (off-topic / standalone-idea /
related-but-different) or by a run of > 2 consecutive fragments.

This shared definition keeps strategies 3 / 6 / 7 / 8 consistent — a
"take" means the same thing across all of them.

### Pure-function contract

- No I/O except reading Whisper sidecars via the existing cache (for
  `energy_shift` only). No mutations of `state`.
- Returns `list[StrategyResult]`. Empty list = "this strategy can't
  produce anything meaningful for this project's current data."
- Diagnostic strategies cap at 3 results each.
- Best-plausible strategies (except `near_one_take`) return 0 or 1
  result; `near_one_take` returns 0 / 1 / 2 / 3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from clipfarm.models import (
    AttemptClip,
    Category,
    Clip,
    ClipFarmState,
    ClipProjectTag,
    PremadeBucket,
)
from clipfarm.transcripts import load_transcript_for_source

log = logging.getLogger("clipfarm.strategies")

# Internal tolerance for "near-one-take" / take-segmentation: how many
# fragment clips can sit inside an otherwise-on-script run before the
# run is considered broken. Spec wording: "low restart count."
MAX_FRAGMENTS_IN_TAKE = 2

# "Near-one-take" coverage threshold: a take qualifies only if its
# distinct on-script line tags cover ≥ this fraction of the project's
# script lines.
NEAR_ONE_TAKE_COVERAGE = 0.70

# `skipped_line` coverage threshold: a take qualifies as having
# "skipped line N" only if it covered ≥ this fraction of the OTHER
# lines on-script.
SKIPPED_LINE_OTHER_COVERAGE = 0.60

# `ad_libbed`: minimum ad-lib clip count for a take to qualify.
AD_LIBBED_MIN_AD_LIBS = 2

# Diagnostic-strategy cap on results per strategy.
DIAGNOSTIC_CAP = 3


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StrategyResult:
    """One generated attempt-candidate from a strategy.

    The orchestrator transforms a `StrategyResult` into a persisted
    `Attempt` after dedup + LLM-naming + continuity-score computation.
    `name_hint` is a short strategy-specific phrase the LLM can use
    when generating the final natural-language name (and is also the
    fallback canned name when the LLM call fails).
    """

    strategy_id: str
    premade_bucket: PremadeBucket
    name_hint: str
    clips: list[AttemptClip] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _on_script_rows_for_project(
    state: ClipFarmState, project_id: str
) -> list[ClipProjectTag]:
    return [
        r for r in state.clip_project_tags
        if r.project_id == project_id
        and r.category == "on-script"
        and r.project_tag_id is not None
    ]


def _rows_by_category_for_project(
    state: ClipFarmState, project_id: str
) -> dict[str, list[ClipProjectTag]]:
    out: dict[str, list[ClipProjectTag]] = {}
    for r in state.clip_project_tags:
        if r.project_id == project_id:
            out.setdefault(r.category, []).append(r)
    return out


def _line_tag_ids_in_order(
    state: ClipFarmState, project_id: str
) -> list[str]:
    project = state.projects[project_id]
    line_tags = [
        (tid, t) for tid, t in project.tags.items() if t.kind == "line"
    ]
    line_tags.sort(key=lambda kv: (kv[1].order_idx, kv[0]))
    return [tid for tid, _ in line_tags]


def _clips_in_source_by_time(
    state: ClipFarmState, source_id: str
) -> list[tuple[str, Clip]]:
    """Return `(clip_id, Clip)` tuples for `source_id`, sorted by
    `start_sec`."""
    items = [
        (cid, c) for cid, c in state.clips.items() if c.source_id == source_id
    ]
    items.sort(key=lambda kv: kv[1].start_sec)
    return items


def _category_by_clip_for_project(
    state: ClipFarmState, project_id: str
) -> dict[str, Category]:
    """For each clip with any row for this project, return its
    most-meaningful category (preference: on-script > standalone-idea >
    related-but-different > off-topic > fragment). A clip without any
    row is absent from the dict — caller treats absence as 'untagged.'"""
    preference: list[Category] = [
        "on-script",
        "standalone-idea",
        "related-but-different",
        "off-topic",
        "fragment",
    ]
    rank = {c: i for i, c in enumerate(preference)}
    best: dict[str, Category] = {}
    for r in state.clip_project_tags:
        if r.project_id != project_id:
            continue
        cur = best.get(r.clip_id)
        if cur is None or rank[r.category] < rank[cur]:
            best[r.clip_id] = r.category
    return best


def _line_tag_by_clip_for_project(
    state: ClipFarmState, project_id: str
) -> dict[str, str]:
    """For each on-script row, return the clip's `project_tag_id`
    (line tag). If a clip has multiple on-script rows for different
    lines (rare), the first one wins; documented edge."""
    out: dict[str, str] = {}
    for r in state.clip_project_tags:
        if (
            r.project_id == project_id
            and r.category == "on-script"
            and r.project_tag_id is not None
            and r.clip_id not in out
        ):
            out[r.clip_id] = r.project_tag_id
    return out


def _confidence_by_clip_and_line(
    state: ClipFarmState, project_id: str
) -> dict[tuple[str, str], float]:
    """For each `(clip_id, project_tag_id)` pair under this project
    with an on-script row, return the row's confidence."""
    out: dict[tuple[str, str], float] = {}
    for r in state.clip_project_tags:
        if (
            r.project_id == project_id
            and r.category == "on-script"
            and r.project_tag_id is not None
        ):
            out[(r.clip_id, r.project_tag_id)] = r.confidence
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Take segmentation (shared by strategies 3, 6, 7, 8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Take:
    """A maximal contiguous-in-source run anchored by on-script clips.

    `clip_ids` is the source-order list of all clips inside the take
    (on-script + fragments). `on_script_clip_ids` is the subset that's
    on-script for this project. `opening_line_tag_id` is the line tag
    of the first on-script clip in source order.
    """

    source_id: str
    clip_ids: list[str]
    on_script_clip_ids: list[str]
    on_script_line_tag_ids: list[str]
    opening_line_tag_id: str
    fragments_inside: int


_DEFAULT_TOLERATED: frozenset[Category] = frozenset({"fragment"})
_AD_LIBBED_TOLERATED: frozenset[Category] = frozenset({
    "fragment", "standalone-idea", "related-but-different",
})


def _detect_takes(
    state: ClipFarmState,
    project_id: str,
    *,
    tolerated_inside: frozenset[Category] = _DEFAULT_TOLERATED,
) -> list[Take]:
    """Walk each source's clip sequence in time order. Build maximal
    runs anchored by on-script clips, tolerating up to
    `MAX_FRAGMENTS_IN_TAKE` fragments inside the run.

    `tolerated_inside` controls which non-on-script categories DON'T
    break the run. By default only fragments are tolerated — that's
    the right rule for "clean take" strategies like
    `longest_contiguous_take` and `near_one_take`. For `ad_libbed` we
    pass `_AD_LIBBED_TOLERATED` so standalone-idea + related-but-
    different clips count as part of the take, letting the strategy
    surface a take with its ad-libs preserved in source order.

    Categories NOT in `tolerated_inside` (other than on-script itself)
    ALWAYS break the run. Untagged clips also break (they're foreign
    material the LLM didn't classify).

    Returns the list of takes in (source_id, start_sec) order.
    """
    cat_by_clip = _category_by_clip_for_project(state, project_id)
    line_by_clip = _line_tag_by_clip_for_project(state, project_id)

    takes: list[Take] = []
    for source_id in state.sources:
        ordered = _clips_in_source_by_time(state, source_id)

        current_clip_ids: list[str] = []
        current_on_script: list[str] = []
        current_lines: list[str] = []
        current_fragments = 0
        opening_line: Optional[str] = None

        def flush() -> None:
            if opening_line is not None and current_on_script:
                takes.append(Take(
                    source_id=source_id,
                    clip_ids=list(current_clip_ids),
                    on_script_clip_ids=list(current_on_script),
                    on_script_line_tag_ids=list(current_lines),
                    opening_line_tag_id=opening_line,
                    fragments_inside=current_fragments,
                ))

        for cid, _clip in ordered:
            cat = cat_by_clip.get(cid)
            if cat == "on-script":
                if opening_line is None:
                    opening_line = line_by_clip.get(cid) or ""
                current_clip_ids.append(cid)
                current_on_script.append(cid)
                line_id = line_by_clip.get(cid)
                if line_id is not None:
                    current_lines.append(line_id)
            elif cat == "fragment" and cat in tolerated_inside:
                if opening_line is None:
                    # Don't open a take with a fragment.
                    continue
                current_fragments += 1
                if current_fragments > MAX_FRAGMENTS_IN_TAKE:
                    # Too many restarts — flush and reset.
                    flush()
                    current_clip_ids = []
                    current_on_script = []
                    current_lines = []
                    current_fragments = 0
                    opening_line = None
                else:
                    current_clip_ids.append(cid)
            elif cat is not None and cat in tolerated_inside:
                # Other tolerated category (e.g. ad-libs for ad_libbed).
                # Doesn't break, doesn't count as a fragment, doesn't
                # open a take on its own.
                if opening_line is None:
                    continue
                current_clip_ids.append(cid)
            else:
                # Foreign category (or untagged) → run break.
                flush()
                current_clip_ids = []
                current_on_script = []
                current_lines = []
                current_fragments = 0
                opening_line = None

        flush()

    return takes


# ─────────────────────────────────────────────────────────────────────────────
# Best-plausible strategies
# ─────────────────────────────────────────────────────────────────────────────


def best_per_line_in_script_order(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Highest-confidence on-script clip per script line, in script
    order. Skip lines with no on-script match.

    The orchestrator's namer suffixes "(N of M lines covered)" when
    coverage is partial (Phase 8 advisory item #5).
    """
    line_ids = _line_tag_ids_in_order(state, project_id)
    conf = _confidence_by_clip_and_line(state, project_id)
    # Index on-script rows by line tag for fast lookup.
    rows_by_line: dict[str, list[ClipProjectTag]] = {}
    for r in _on_script_rows_for_project(state, project_id):
        rows_by_line.setdefault(r.project_tag_id, []).append(r)  # type: ignore[arg-type]

    picks: list[AttemptClip] = []
    covered = 0
    for tid in line_ids:
        rows = rows_by_line.get(tid, [])
        if not rows:
            continue
        # Highest confidence; tie-break on earliest start_sec.
        best = max(
            rows,
            key=lambda r: (
                r.confidence,
                -state.clips[r.clip_id].start_sec
                if r.clip_id in state.clips
                else 0,
            ),
        )
        if best.clip_id not in state.clips:
            continue
        picks.append(AttemptClip(clip_id=best.clip_id))
        covered += 1

    if not picks:
        return []
    return [StrategyResult(
        strategy_id="best_per_line_in_script_order",
        premade_bucket="best",
        name_hint=f"best take of each line, in script order ({covered} of {len(line_ids)} lines)",
        clips=picks,
    )]


def longest_contiguous_take(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """The take (per the shared definition) that covers the most
    distinct script lines on-script. Tiebreak on total runtime."""
    takes = _detect_takes(state, project_id)
    if not takes:
        return []
    # Score per take: (distinct_lines_covered, total_runtime).
    def score(t: Take) -> tuple[int, float]:
        distinct = len(set(t.on_script_line_tag_ids))
        runtime = sum(
            state.clips[cid].end_sec - state.clips[cid].start_sec
            for cid in t.clip_ids
            if cid in state.clips
        )
        return (distinct, runtime)

    best = max(takes, key=score)
    distinct = len(set(best.on_script_line_tag_ids))
    return [StrategyResult(
        strategy_id="longest_contiguous_take",
        premade_bucket="best",
        name_hint=f"longest contiguous take ({distinct} lines covered)",
        clips=[AttemptClip(clip_id=cid) for cid in best.clip_ids],
    )]


def near_one_take(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Up to 3 separate attempts, each a single qualifying take.

    A take qualifies if it covers ≥ NEAR_ONE_TAKE_COVERAGE of the
    project's script lines on-script. The 3 are picked by
    (distinct_lines_covered, total_runtime) descending.
    """
    line_ids = _line_tag_ids_in_order(state, project_id)
    if not line_ids:
        return []
    threshold = max(1, int(NEAR_ONE_TAKE_COVERAGE * len(line_ids)))

    qualifying: list[Take] = []
    for t in _detect_takes(state, project_id):
        distinct = len(set(t.on_script_line_tag_ids))
        if distinct >= threshold:
            qualifying.append(t)

    if not qualifying:
        return []

    def score(t: Take) -> tuple[int, float]:
        distinct = len(set(t.on_script_line_tag_ids))
        runtime = sum(
            state.clips[cid].end_sec - state.clips[cid].start_sec
            for cid in t.clip_ids
            if cid in state.clips
        )
        return (distinct, runtime)

    qualifying.sort(key=score, reverse=True)
    top = qualifying[:3]
    results: list[StrategyResult] = []
    for i, t in enumerate(top, start=1):
        distinct = len(set(t.on_script_line_tag_ids))
        first_clip = state.clips.get(t.clip_ids[0]) if t.clip_ids else None
        when = (
            f" ({first_clip.start_sec:.0f}s in)"
            if first_clip is not None
            else ""
        )
        results.append(StrategyResult(
            strategy_id="near_one_take",
            premade_bucket="best",
            name_hint=f"near-one-take #{i}: {distinct} lines{when}",
            clips=[AttemptClip(clip_id=cid) for cid in t.clip_ids],
        ))
    return results


def shortest_complete(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Shortest on-script clip per line, in script order. Same shape
    as best-per-line but optimizing for runtime, not confidence."""
    line_ids = _line_tag_ids_in_order(state, project_id)
    rows_by_line: dict[str, list[ClipProjectTag]] = {}
    for r in _on_script_rows_for_project(state, project_id):
        rows_by_line.setdefault(r.project_tag_id, []).append(r)  # type: ignore[arg-type]

    picks: list[AttemptClip] = []
    total_runtime = 0.0
    covered = 0
    for tid in line_ids:
        rows = rows_by_line.get(tid, [])
        valid = [r for r in rows if r.clip_id in state.clips]
        if not valid:
            continue
        shortest = min(
            valid,
            key=lambda r: (
                state.clips[r.clip_id].end_sec - state.clips[r.clip_id].start_sec
            ),
        )
        clip = state.clips[shortest.clip_id]
        total_runtime += clip.end_sec - clip.start_sec
        picks.append(AttemptClip(clip_id=shortest.clip_id))
        covered += 1

    if not picks:
        return []
    return [StrategyResult(
        strategy_id="shortest_complete",
        premade_bucket="best",
        name_hint=f"shortest complete take ({total_runtime:.0f}s, {covered} of {len(line_ids)} lines)",
        clips=picks,
    )]


def _words_per_second(
    state: ClipFarmState, clip: Clip
) -> Optional[float]:
    """Count Whisper words inside [clip.start_sec, clip.end_sec). Returns
    None when the source has no transcript or the duration is zero.

    Cached via `load_transcript_for_source`, which keys on
    (path, mtime_ns) — warm-cache cost is a dict lookup.
    """
    duration = clip.end_sec - clip.start_sec
    if duration <= 0:
        return None
    source = state.sources.get(clip.source_id)
    if source is None:
        return None
    transcript = load_transcript_for_source(source)
    if transcript is None:
        return None
    count = 0
    for seg in transcript.segments:
        for w in seg.words:
            if w.start >= clip.end_sec:
                # Words are time-ordered; bail.
                return count / duration
            if w.start >= clip.start_sec:
                count += 1
    return count / duration


def energy_shift(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Highest words-per-second on-script clip per line.

    v0 heuristic — revisit when audio analysis lands. The spec wants
    "the take where the energy picked up"; pace (words/sec) is a real
    signal but not the only one. Real "energy" includes volume,
    pitch range, and per-syllable timing; v0 only has Whisper
    timestamps, so we use the cheapest meaningful proxy.

    Falls back to ranking by confidence when a clip's source has no
    transcript (footage-only sources are still usable; we just don't
    have a pace signal for them).
    """
    line_ids = _line_tag_ids_in_order(state, project_id)
    rows_by_line: dict[str, list[ClipProjectTag]] = {}
    for r in _on_script_rows_for_project(state, project_id):
        rows_by_line.setdefault(r.project_tag_id, []).append(r)  # type: ignore[arg-type]

    picks: list[AttemptClip] = []
    for tid in line_ids:
        rows = rows_by_line.get(tid, [])
        valid = [r for r in rows if r.clip_id in state.clips]
        if not valid:
            continue
        # Score each candidate by words-per-second; fall back to
        # confidence if wps unavailable.
        def keyfn(r):
            wps = _words_per_second(state, state.clips[r.clip_id])
            return (wps if wps is not None else -1.0, r.confidence)
        best = max(valid, key=keyfn)
        picks.append(AttemptClip(clip_id=best.clip_id))

    if not picks:
        return []
    return [StrategyResult(
        strategy_id="energy_shift",
        premade_bucket="best",
        name_hint="the takes where the energy picked up",
        clips=picks,
    )]


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostic strategies
# ─────────────────────────────────────────────────────────────────────────────


def started_with_line(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Group takes by their opening on-script line. Each group with
    ≥ 2 takes becomes one diagnostic attempt that concatenates the
    takes' clips in (source_id, start_sec) order.

    Cap at top 3 groups by group size.
    """
    takes = _detect_takes(state, project_id)
    if not takes:
        return []

    by_opening: dict[str, list[Take]] = {}
    for t in takes:
        by_opening.setdefault(t.opening_line_tag_id, []).append(t)

    # Keep only groups with ≥ 2 takes.
    qualifying: list[tuple[str, list[Take]]] = [
        (tid, ts) for tid, ts in by_opening.items() if len(ts) >= 2
    ]
    if not qualifying:
        return []

    qualifying.sort(key=lambda x: (-len(x[1]), x[0]))
    top = qualifying[:DIAGNOSTIC_CAP]

    project = state.projects[project_id]
    results: list[StrategyResult] = []
    for line_tag_id, ts in top:
        line_name = project.tags.get(line_tag_id)
        line_label = line_name.name if line_name else f"line {line_tag_id}"
        # Concatenate all takes' clips, sources/start_sec ordered.
        ordered_clip_ids: list[str] = []
        for t in sorted(ts, key=lambda t: (t.source_id, state.clips[t.clip_ids[0]].start_sec if t.clip_ids and t.clip_ids[0] in state.clips else 0)):
            ordered_clip_ids.extend(t.clip_ids)
        results.append(StrategyResult(
            strategy_id="started_with_line",
            premade_bucket="diagnostic",
            name_hint=f"versions that started with \"{line_label}\" ({len(ts)} takes)",
            clips=[AttemptClip(clip_id=cid) for cid in ordered_clip_ids],
        ))
    return results


def skipped_line(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """For each line tag N, find takes where ≥ SKIPPED_LINE_OTHER_COVERAGE
    of OTHER lines were covered on-script but line N was NOT. Group
    by N, return one attempt per qualifying line.

    Cap at top 3 lines by "skipped count" (how many takes skipped them).
    """
    line_ids = _line_tag_ids_in_order(state, project_id)
    if len(line_ids) < 2:
        # With < 2 lines, "skipped" doesn't have a meaningful baseline.
        return []
    takes = _detect_takes(state, project_id)
    if not takes:
        return []

    # For each take, compute which lines it hit.
    take_lines: list[tuple[Take, set[str]]] = [
        (t, set(t.on_script_line_tag_ids)) for t in takes
    ]

    skipped_per_line: dict[str, list[Take]] = {}
    for line_id in line_ids:
        other_lines = [l for l in line_ids if l != line_id]
        threshold = max(1, int(SKIPPED_LINE_OTHER_COVERAGE * len(other_lines)))
        for t, hit in take_lines:
            if line_id in hit:
                continue  # didn't skip it
            other_hit = sum(1 for l in other_lines if l in hit)
            if other_hit >= threshold:
                skipped_per_line.setdefault(line_id, []).append(t)

    qualifying = [
        (tid, ts) for tid, ts in skipped_per_line.items() if len(ts) >= 1
    ]
    if not qualifying:
        return []
    qualifying.sort(key=lambda x: (-len(x[1]), x[0]))
    top = qualifying[:DIAGNOSTIC_CAP]

    project = state.projects[project_id]
    results: list[StrategyResult] = []
    for line_tag_id, ts in top:
        line_name = project.tags.get(line_tag_id)
        line_label = line_name.name if line_name else f"line {line_tag_id}"
        ordered_clip_ids: list[str] = []
        for t in sorted(
            ts,
            key=lambda t: (
                t.source_id,
                state.clips[t.clip_ids[0]].start_sec
                if t.clip_ids and t.clip_ids[0] in state.clips
                else 0,
            ),
        ):
            ordered_clip_ids.extend(t.clip_ids)
        results.append(StrategyResult(
            strategy_id="skipped_line",
            premade_bucket="diagnostic",
            name_hint=f"versions that skipped \"{line_label}\" ({len(ts)} takes)",
            clips=[AttemptClip(clip_id=cid) for cid in ordered_clip_ids],
        ))
    return results


def ad_libbed(
    state: ClipFarmState, project_id: str
) -> list[StrategyResult]:
    """Takes with ≥ AD_LIBBED_MIN_AD_LIBS ad-lib clips (related-but-
    different or standalone-idea) preserved inside.

    Uses the broader `_AD_LIBBED_TOLERATED` set for take detection so
    ad-libs land INSIDE the take's clip list rather than breaking it.
    The attempt's clip list is the take's full source-order sequence
    (on-script + fragments + ad-libs), giving the user the on-script
    delivery WITH the bonus material preserved.

    Cap at top 3 takes by ad-lib count.
    """
    takes = _detect_takes(
        state, project_id, tolerated_inside=_AD_LIBBED_TOLERATED
    )
    if not takes:
        return []
    cat_by_clip = _category_by_clip_for_project(state, project_id)

    qualifying: list[tuple[Take, int]] = []
    for t in takes:
        ad_lib_count = sum(
            1 for cid in t.clip_ids
            if cat_by_clip.get(cid) in ("standalone-idea", "related-but-different")
        )
        if ad_lib_count >= AD_LIBBED_MIN_AD_LIBS:
            qualifying.append((t, ad_lib_count))

    if not qualifying:
        return []
    qualifying.sort(key=lambda x: -x[1])
    top = qualifying[:DIAGNOSTIC_CAP]

    results: list[StrategyResult] = []
    for t, ad_libs in top:
        when = (
            f" ({state.clips[t.on_script_clip_ids[0]].start_sec:.0f}s in)"
            if t.on_script_clip_ids
            and t.on_script_clip_ids[0] in state.clips
            else ""
        )
        results.append(StrategyResult(
            strategy_id="ad_libbed",
            premade_bucket="diagnostic",
            name_hint=f"the take where you ad-libbed {ad_libs} bonus ideas{when}",
            clips=[AttemptClip(clip_id=cid) for cid in t.clip_ids],
        ))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Strategy registry — ordered so dedup is deterministic
# ─────────────────────────────────────────────────────────────────────────────


ALL_STRATEGIES = (
    best_per_line_in_script_order,
    longest_contiguous_take,
    near_one_take,
    shortest_complete,
    energy_shift,
    started_with_line,
    skipped_line,
    ad_libbed,
)


STRATEGY_CANNED_NAMES: dict[str, str] = {
    "best_per_line_in_script_order": "best take of each line, in script order",
    "longest_contiguous_take": "the longest contiguous take",
    "near_one_take": "the times you said it in almost one take",
    "shortest_complete": "the shortest complete take of the full script",
    "energy_shift": "the take where the energy picked up",
    "started_with_line": "versions clustered by opening line",
    "skipped_line": "versions where you skipped a line",
    "ad_libbed": "the take where you ad-libbed bonus material",
}


__all__ = [
    "ALL_STRATEGIES",
    "AD_LIBBED_MIN_AD_LIBS",
    "DIAGNOSTIC_CAP",
    "MAX_FRAGMENTS_IN_TAKE",
    "NEAR_ONE_TAKE_COVERAGE",
    "SKIPPED_LINE_OTHER_COVERAGE",
    "STRATEGY_CANNED_NAMES",
    "StrategyResult",
    "Take",
    "ad_libbed",
    "best_per_line_in_script_order",
    "energy_shift",
    "longest_contiguous_take",
    "near_one_take",
    "shortest_complete",
    "skipped_line",
    "started_with_line",
]
