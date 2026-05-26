"""Phase 8 orchestrator — turn strategy outputs into persisted
`Attempt` records.

Pure mutation of `ClipFarmState` (no I/O except the optional LLM
naming call). The route layer in `clipfarm/routes/premade.py` holds
the save lock + handles the snapshot-then-commit (Phase 6 pattern).

Pipeline:

1. Pre-check: project exists, has at least one on-script tag row. Else
   raises (route → 400).
2. Run every strategy in `ALL_STRATEGIES` order.
3. Dedup: if two strategies produced identical clip lists (e.g.
   `best_per_line` and `shortest_complete` agree on a sparse project),
   keep the first by strategy order, drop the second.
4. Compute `continuity_score` for each surviving result.
5. Build name summaries; batched LLM call (or canned fallback).
6. Optionally replace existing `source="ai-premade"` attempts.
7. Allocate `_next_attempt_id` per new attempt; persist.

Returns a `PremadeResult` carrying the new attempt IDs + a
`naming_source` summary the route includes in the response.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from clipfarm.attempt_naming import (
    AttemptNameSummary,
    LLMClient,
    NamedAttempt,
    name_attempts,
)
from clipfarm.continuity import compute_continuity_score
from clipfarm.models import Attempt, ClipFarmState
from clipfarm.strategies import ALL_STRATEGIES, StrategyResult

log = logging.getLogger("clipfarm.premade")

# Phase 8.1 — same shape as tagging's ProgressCallback. Called at known
# phase transitions; exceptions are swallowed.
ProgressCallback = Callable[[dict[str, Any]], None]


def _safe_progress(progress: Optional[ProgressCallback], info: dict[str, Any]) -> None:
    if progress is None:
        return
    try:
        progress(info)
    except Exception:
        log.exception("premade: progress callback raised; ignoring")


@dataclass
class PremadeResult:
    """Summary of one orchestrator run."""

    generated_count: int = 0
    replaced_count: int = 0
    new_attempt_ids: list[str] = field(default_factory=list)
    # "llm" if any attempt's name came from the LLM, "canned" if every
    # attempt fell back to canned names, "mixed" if both. The route
    # forwards this in the response so the UI can surface it.
    naming_source: str = "canned"
    # User-facing message when `generated_count == 0` (every strategy
    # returned [] or produced only orphan clips). Empty string on
    # normal runs.
    reason: str = ""
    mutated: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_attempt_id(state: ClipFarmState) -> str:
    """Monotonic stringified integer, matching `_next_source_id` /
    `_next_project_id`. Walks BOTH existing attempt IDs (so re-running
    after a partial replace doesn't collide) AND the IDs of attempts
    we're about to delete (so we don't reuse a freed slot mid-run and
    confuse anyone reading the snapshot trail).
    """
    used = {int(k) for k in state.attempts.keys() if k.isdigit()}
    return str(max(used) + 1) if used else "1"


def _build_name_summaries(
    state: ClipFarmState, results: list[StrategyResult],
    continuity_scores: list[float],
) -> list[AttemptNameSummary]:
    """Per-strategy summary fed to the namer. Transcript preview is
    the joined first-30-chars of each clip's `transcript_text` — gives
    the LLM enough context to write a name without bloating the prompt.
    """
    out: list[AttemptNameSummary] = []
    for r, score in zip(results, continuity_scores):
        previews: list[str] = []
        for ac in r.clips[:6]:  # cap at 6 clips of preview per attempt
            clip = state.clips.get(ac.clip_id)
            if clip is None or not clip.transcript_text:
                continue
            previews.append(clip.transcript_text[:30].strip())
        out.append(AttemptNameSummary(
            strategy_id=r.strategy_id,
            name_hint=r.name_hint,
            continuity_score=score,
            clip_count=len(r.clips),
            transcript_preview=" · ".join(previews),
        ))
    return out


def _dedupe(results: list[StrategyResult]) -> list[StrategyResult]:
    """Keep the first occurrence of each unique clip-id sequence.
    Strategy order in `ALL_STRATEGIES` decides who wins on collision.
    """
    seen: set[tuple[str, ...]] = set()
    out: list[StrategyResult] = []
    for r in results:
        key = tuple(ac.clip_id for ac in r.clips)
        if not key:
            continue
        if key in seen:
            log.info(
                "premade: dropping duplicate clip list from %s (matched earlier strategy)",
                r.strategy_id,
            )
            continue
        seen.add(key)
        out.append(r)
    return out


def generate_premade_attempts(
    state: ClipFarmState,
    project_id: str,
    *,
    llm_client: Optional[LLMClient] = None,
    replace_existing: bool = True,
    progress: Optional[ProgressCallback] = None,
) -> PremadeResult:
    """Run every strategy, dedup, name, persist.

    Raises:
      KeyError — unknown `project_id`.
      ValueError — project has no on-script tag rows (caller surfaces 400).

    `replace_existing=True` drops every existing `source="ai-premade"`
    attempt for this project before persisting new ones. Hand-built
    (`source="hand-built"`) and forks (`source="fork"`) are NEVER
    touched.
    """
    project = state.projects.get(project_id)
    if project is None:
        raise KeyError(f"unknown project_id: {project_id}")

    has_on_script = any(
        r.project_id == project_id and r.category == "on-script"
        for r in state.clip_project_tags
    )
    if not has_on_script:
        raise ValueError(
            f"project {project_id!r} has no on-script tag rows — "
            f"tag clips before generating premade attempts"
        )

    result = PremadeResult()
    started = time.perf_counter()
    _safe_progress(progress, {
        "phase": "preflight",
        "total_strategies": len(ALL_STRATEGIES),
        "elapsed_sec": 0.0,
    })

    # 1. Run strategies.
    all_results: list[StrategyResult] = []
    for i, strat in enumerate(ALL_STRATEGIES, start=1):
        _safe_progress(progress, {
            "phase": "running_strategies",
            "current_strategy": i,
            "total_strategies": len(ALL_STRATEGIES),
            "strategy_name": strat.__name__,
            "elapsed_sec": time.perf_counter() - started,
        })
        try:
            strat_out = strat(state, project_id)
        except Exception:
            log.exception("premade: strategy %s raised; skipping", strat.__name__)
            continue
        all_results.extend(strat_out)

    # 2. Dedup across strategies.
    deduped = _dedupe(all_results)

    if not deduped:
        result.reason = (
            "All strategies produced no results for this project. "
            "Tag more clips before generating premade attempts."
        )
        log.info("premade: no strategy produced results for project %s", project_id)
        return result

    # 3. Continuity score per result.
    continuity_scores: list[float] = []
    valid_results: list[StrategyResult] = []
    for r in deduped:
        try:
            score = compute_continuity_score(state, r.clips)
        except ValueError as e:
            log.warning(
                "premade: skipping %s — continuity undefined (%s)",
                r.strategy_id, e,
            )
            continue
        continuity_scores.append(score)
        valid_results.append(r)

    if not valid_results:
        result.reason = (
            "Strategy results produced no playable attempts "
            "(zero-runtime clip lists)."
        )
        return result

    # 4. Batched naming.
    _safe_progress(progress, {
        "phase": "naming",
        "attempt_count": len(valid_results),
        "elapsed_sec": time.perf_counter() - started,
    })
    summaries = _build_name_summaries(state, valid_results, continuity_scores)
    named: list[NamedAttempt] = name_attempts(summaries, llm_client)
    sources = {n.name_source for n in named}
    if sources == {"llm"}:
        result.naming_source = "llm"
    elif sources == {"canned"}:
        result.naming_source = "canned"
    else:
        result.naming_source = "mixed"

    # 5. Replace existing ai-premade if requested.
    if replace_existing:
        to_drop = [
            aid for aid, att in state.attempts.items()
            if att.project_id == project_id and att.source == "ai-premade"
        ]
        for aid in to_drop:
            del state.attempts[aid]
        result.replaced_count = len(to_drop)
        if to_drop:
            result.mutated = True

    # 6. Allocate IDs + persist.
    _safe_progress(progress, {
        "phase": "persisting",
        "attempt_count": len(valid_results),
        "elapsed_sec": time.perf_counter() - started,
    })
    now = _now_iso()
    for r, score, naming in zip(valid_results, continuity_scores, named):
        aid = _next_attempt_id(state)
        state.attempts[aid] = Attempt(
            project_id=project_id,
            name=naming.name,
            parent_attempt_id=None,
            source="ai-premade",
            premade_bucket=r.premade_bucket,
            continuity_score=score,
            clips=r.clips,
            needs_review=False,
            created_at=now,
        )
        result.new_attempt_ids.append(aid)
        result.mutated = True

    result.generated_count = len(result.new_attempt_ids)
    log.info(
        "premade: project=%s generated=%d replaced=%d naming=%s elapsed=%.2fs",
        project_id, result.generated_count, result.replaced_count,
        result.naming_source, time.perf_counter() - started,
    )
    return result


__all__ = ["PremadeResult", "generate_premade_attempts"]
