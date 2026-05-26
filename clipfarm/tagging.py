"""Tagging orchestrator — batched LLM calls that write `clip_project_tags`
rows for clips that haven't been tagged for this project yet (or have
been marked `stale=True` by a brief edit).

Pure orchestration: mutates `ClipFarmState` in place, returns a
`TaggingResult` summary. The route layer holds the save lock + does the
one commit at the end. The `llm_client` callable is injected so tests
can run the whole orchestrator without hitting Ollama.

LLM-output validation rules (locked in PHASES.md Phase 6):
- Unknown line_tag_id → drop row + log.
- Invalid category → drop row + log.
- Missing required fields (clip_id, category, confidence) → drop + log.
- Out-of-range confidence → clamp to [0, 1] + log, keep row.
- clip_id not in the batch we sent → drop + log.
- Batch-size mismatch: try clip_id reconstruction; keep valid rows;
  only retry-or-bucket if the valid set is empty.

Retry policy: one retry per batch on malformed-or-empty result; on
second failure, batch lands in `untagged_batches` and the run
continues.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from clipfarm.models import (
    Category,
    ClipFarmState,
    ClipProjectTag,
    StrictModel,
)

log = logging.getLogger("clipfarm.tagging")

VALID_CATEGORIES: set[str] = {
    "on-script",
    "related-but-different",
    "standalone-idea",
    "off-topic",
    "fragment",
}

DEFAULT_BATCH_SIZE = 10
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 30


# ---------- Result shapes ----------------------------------------------------


class BatchFailure(StrictModel):
    clip_ids: list[str]
    reason: str
    raw_response_excerpt: str = ""


class TaggingResult(StrictModel):
    batches: int = 0
    clips_tagged: int = 0
    clips_skipped: int = 0  # already-tagged + non-stale
    rows_dropped: int = 0   # per-row validation failures
    untagged_batches: list[BatchFailure] = []
    duration_sec: float = 0.0
    # True iff state was actually mutated this run — either a new
    # ClipProjectTag row was appended OR stale rows were dropped
    # pre-LLM. The route checks this before snapshot+commit so a
    # "every batch failed validation, no stale rows existed" run
    # doesn't spuriously snapshot unchanged state.
    mutated: bool = False


# ---------- LLM client type --------------------------------------------------

LLMClient = Callable[[list[dict[str, str]], dict[str, Any]], Optional[dict[str, Any]]]


def _output_schema() -> dict[str, Any]:
    """JSON schema the LLM is constrained to emit. Required for Ollama's
    `format` parameter; also documents the contract."""
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clip_id": {"type": "string"},
                        "line_tag_id": {"type": ["string", "null"]},
                        "section_tag_id": {"type": ["string", "null"]},
                        "category": {
                            "type": "string",
                            "enum": sorted(VALID_CATEGORIES),
                        },
                        "confidence": {"type": "number"},
                    },
                    "required": ["clip_id", "category", "confidence"],
                },
            }
        },
        "required": ["results"],
    }


# ---------- Brief context for the system prompt ------------------------------


def _system_prompt(project_name: str, project_brief_md: str, project_tags: dict) -> str:
    """Build the system prompt. Includes the project name, the
    "what's good" body, the script lines + their tag IDs, sections +
    their tag IDs, ad-hoc tags + their tag IDs, and the category enum
    description."""
    script_lines: list[str] = []
    sections: list[str] = []
    adhoc_tags: list[str] = []

    for tid, tag in project_tags.items():
        if tag.kind == "line":
            script_lines.append(f"  - id={tid}: {tag.name!r}")
        elif tag.kind == "section":
            sections.append(f"  - id={tid}: {tag.name!r}")
        elif tag.kind == "tag":
            adhoc_tags.append(f"  - id={tid}: {tag.name!r}")

    parts = [
        f"You are tagging clips from raw video footage for a project called \"{project_name}\".",
        "",
        "Project brief (what counts as 'good' for this project — tone, energy, length, etc.):",
        project_brief_md.strip() or "(no notes yet)",
        "",
    ]
    if script_lines:
        parts.append("Script lines (each clip can match at most one of these by content):")
        parts.extend(script_lines)
        parts.append("")
    if sections:
        parts.append("Sections (chapters / beats in the project):")
        parts.extend(sections)
        parts.append("")
    if adhoc_tags:
        parts.append("Ad-hoc tags (project-level labels):")
        parts.extend(adhoc_tags)
        parts.append("")
    parts.extend([
        "For each clip in the user message, return a JSON result with:",
        "  - clip_id: echo back the input clip_id verbatim",
        "  - line_tag_id: the id of a script line this clip matches, or null",
        "  - section_tag_id: the id of a section this clip belongs to, or null (leave null for v0)",
        "  - category: one of",
        "      - on-script: matches a script line directly",
        "      - related-but-different: relevant to the project but not a script line",
        "      - standalone-idea: a good moment that could be its own short / callout",
        "      - off-topic: not for this project",
        "      - fragment: false start, single-word noise, restart, filler",
        "  - confidence: 0.0–1.0",
        "",
        "Return a top-level object {\"results\": [...]} with one entry per input clip, in input order.",
    ])
    return "\n".join(parts)


# ---------- Per-batch user prompt --------------------------------------------


def _user_prompt(batch: list[tuple[str, str]]) -> str:
    """`batch` is a list of (clip_id, transcript_text). Returns the user
    message text — one clip per labeled block."""
    lines = ["Tag these clips:", ""]
    for cid, text in batch:
        lines.append(f"--- clip_id={cid}")
        lines.append(text.strip() or "(no transcript text)")
    return "\n".join(lines)


# ---------- LLM result validation -------------------------------------------


def _validate_row(
    row: dict[str, Any],
    *,
    valid_clip_ids: set[str],
    valid_line_tag_ids: set[str],
) -> Optional[dict[str, Any]]:
    """Validate one row from the LLM result against the v0 rules.
    Returns the row dict (with `confidence` clamped) on success, or
    `None` on any drop condition. Drops + clamps get logged."""
    if not isinstance(row, dict):
        log.warning("tagging: dropping non-dict row: %r", row)
        return None

    cid = row.get("clip_id")
    if not isinstance(cid, str):
        log.warning("tagging: dropping row with missing/non-string clip_id: %r", row)
        return None
    if cid not in valid_clip_ids:
        log.warning(
            "tagging: dropping row with hallucinated clip_id=%r (not in batch)", cid
        )
        return None

    category = row.get("category")
    if category not in VALID_CATEGORIES:
        log.warning(
            "tagging: dropping row for clip=%s with invalid category=%r", cid, category
        )
        return None

    line_tag_id = row.get("line_tag_id")
    if line_tag_id is not None:
        if not isinstance(line_tag_id, str):
            log.warning(
                "tagging: dropping row for clip=%s — line_tag_id must be string or null, got %r",
                cid,
                type(line_tag_id).__name__,
            )
            return None
        if line_tag_id not in valid_line_tag_ids:
            log.warning(
                "tagging: dropping row for clip=%s — hallucinated line_tag_id=%r",
                cid,
                line_tag_id,
            )
            return None

    raw_confidence = row.get("confidence")
    if raw_confidence is None:
        log.warning(
            "tagging: dropping row for clip=%s — confidence missing", cid
        )
        return None
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        log.warning(
            "tagging: dropping row for clip=%s — confidence not coercible to float: %r",
            cid,
            raw_confidence,
        )
        return None
    if confidence < 0.0 or confidence > 1.0:
        log.warning(
            "tagging: clip=%s confidence out of range (%s) — clamping to [0, 1]",
            cid,
            confidence,
        )
        confidence = max(0.0, min(1.0, confidence))

    # `section_tag_id` is extracted from the LLM output (and reserved
    # in the JSON schema) but not surfaced on `ClipProjectTag` for v0 —
    # per Phase 5's flat-lines simplification, section→line parentage
    # isn't expressed yet. Drop the field here so downstream code can't
    # accidentally reach for it.
    return {
        "clip_id": cid,
        "line_tag_id": line_tag_id,
        "category": category,
        "confidence": confidence,
    }


# ---------- Main orchestrator ------------------------------------------------


def tag_project(
    state: ClipFarmState,
    project_id: str,
    *,
    llm_client: LLMClient,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> TaggingResult:
    """Walk every clip not yet tagged for `project_id` (or tagged with
    `stale=True`), batch them, call `llm_client` for each batch,
    validate the responses, write `clip_project_tags` rows.

    Raises `KeyError` if `project_id` doesn't exist. Raises `ValueError`
    if the project has nothing to match against (no script, no sections,
    no ad-hoc tags) — the route handler turns this into a 400.

    `dry_run=True` skips the LLM call and the writes; returns a
    result with batch counts but `clips_tagged=0`. Useful for batch-
    composition debugging.
    """
    if batch_size < MIN_BATCH_SIZE or batch_size > MAX_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be in [{MIN_BATCH_SIZE}, {MAX_BATCH_SIZE}], got {batch_size}"
        )

    project = state.projects.get(project_id)
    if project is None:
        raise KeyError(f"unknown project_id: {project_id}")

    has_script = project.script is not None and bool(project.script.lines)
    has_sections = any(t.kind == "section" for t in project.tags.values())
    has_tags = any(t.kind == "tag" for t in project.tags.values())
    if not (has_script or has_sections or has_tags):
        raise ValueError(
            f"project {project_id!r} ({project.name!r}) has no script lines, "
            f"sections, or tags — add at least one before tagging"
        )

    # Build the candidate clip list: every clip that doesn't have a row
    # for (clip_id, project_id), OR has a row flagged stale.
    rows_by_clip: dict[str, list[ClipProjectTag]] = {}
    for row in state.clip_project_tags:
        if row.project_id == project_id:
            rows_by_clip.setdefault(row.clip_id, []).append(row)

    candidates: list[tuple[str, str]] = []  # (clip_id, transcript_text)
    skipped = 0
    for cid, clip in state.clips.items():
        existing_rows = rows_by_clip.get(cid, [])
        any_stale = any(r.stale for r in existing_rows)
        if existing_rows and not any_stale:
            skipped += 1
            continue
        # Either no rows yet, OR at least one stale row — re-tag in both cases.
        # For stale, drop existing rows for this (cid, project_id) before writing fresh.
        candidates.append((cid, clip.transcript_text or ""))

    valid_line_tag_ids: set[str] = {
        tid for tid, t in project.tags.items() if t.kind == "line"
    }

    started = time.perf_counter()
    result = TaggingResult(clips_skipped=skipped)
    if not candidates:
        result.duration_sec = time.perf_counter() - started
        return result

    schema = _output_schema()
    system_msg = {"role": "system", "content": _system_prompt(
        project.name, project.brief_md, project.tags
    )}

    # Drop stale rows up front for the candidates we'll be re-tagging.
    # This is mutation; we own the save lock at this point (route holds it).
    #
    # **Tradeoff — deliberately accepted (Phase 6.1)**: if all LLM batches
    # then fail validation, the stale rows are gone for good but no fresh
    # rows replace them. The user retries `Tag clips`, which will see the
    # affected clips as "untagged" (no rows at all) and tag them from
    # scratch. The alternative (defer the drop until after a successful
    # batch) leaves stale-and-fresh rows coexisting briefly, which
    # complicates the validator and the UI's stale-vs-fresh disambiguation
    # for a corner case that rarely matters in single-user v0.
    candidate_ids = {cid for cid, _ in candidates}
    if not dry_run:
        before = len(state.clip_project_tags)
        state.clip_project_tags = [
            r for r in state.clip_project_tags
            if not (r.project_id == project_id and r.clip_id in candidate_ids)
        ]
        if len(state.clip_project_tags) != before:
            result.mutated = True

    # Iterate batches.
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        result.batches += 1
        batch_clip_ids = {cid for cid, _ in batch}

        if dry_run:
            continue

        attempt = 0
        success_rows: list[dict[str, Any]] = []
        last_excerpt = ""
        while attempt < 2:
            attempt += 1
            messages = [
                system_msg,
                {"role": "user", "content": _user_prompt(batch)},
            ]
            raw = llm_client(messages, schema)
            if raw is None:
                last_excerpt = "(LLM returned None — see log for details)"
                continue
            results_list = raw.get("results") if isinstance(raw, dict) else None
            if not isinstance(results_list, list):
                last_excerpt = f"missing/invalid 'results' field: {raw!r}"[:300]
                continue
            success_rows = []
            for row in results_list:
                validated = _validate_row(
                    row,
                    valid_clip_ids=batch_clip_ids,
                    valid_line_tag_ids=valid_line_tag_ids,
                )
                if validated is None:
                    result.rows_dropped += 1
                    continue
                success_rows.append(validated)
            if success_rows:
                # Even on partial batch-size mismatch, partial wins are
                # real — don't retry just because some clips got dropped.
                break
            # Empty post-validation set → treat as malformed → retry.
            last_excerpt = (
                f"all {len(results_list)} rows failed validation; "
                f"first row: {results_list[0] if results_list else 'empty'!r}"
            )[:300]

        if not success_rows:
            result.untagged_batches.append(
                BatchFailure(
                    clip_ids=list(batch_clip_ids),
                    reason="LLM result failed validation after retry",
                    raw_response_excerpt=last_excerpt,
                )
            )
            continue

        # Write the rows. Skip rows that would create a uniqueness collision
        # (defense in depth — pre-drop should have removed any prior rows).
        existing_keys = {
            (r.clip_id, r.project_id, r.project_tag_id, r.category)
            for r in state.clip_project_tags
            if r.project_id == project_id
        }
        for row in success_rows:
            key = (
                row["clip_id"], project_id, row["line_tag_id"], row["category"]
            )
            if key in existing_keys:
                log.warning(
                    "tagging: skipping duplicate row %s (uniqueness collision)", key
                )
                continue
            state.clip_project_tags.append(
                ClipProjectTag(
                    clip_id=row["clip_id"],
                    project_id=project_id,
                    project_tag_id=row["line_tag_id"],
                    category=row["category"],
                    confidence=row["confidence"],
                    source="ai",
                    stale=False,
                )
            )
            existing_keys.add(key)
            result.clips_tagged += 1
            result.mutated = True

    result.duration_sec = time.perf_counter() - started
    return result


__all__ = [
    "BatchFailure",
    "DEFAULT_BATCH_SIZE",
    "LLMClient",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "TaggingResult",
    "VALID_CATEGORIES",
    "tag_project",
]
