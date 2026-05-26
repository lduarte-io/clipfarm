"""Batched LLM-based attempt naming with per-strategy canned fallback.

Phase 8 generates up to 16 attempts per project (5 best-plausible + 9
diagnostic). Sequential per-attempt LLM calls would be ~8 minutes on
Llama 3.1 8B; one batched call is ~30 seconds.

**Contract:**

- `name_attempts(summaries, llm_client) → list[NamedAttempt]` — returns a
  per-attempt name + a single `naming_source` flag indicating whether
  the LLM came through ("llm") or the fallback was used ("canned").
- If the LLM call fails ENTIRELY (returns None, raises, malformed JSON),
  every attempt gets its strategy's canned name. `naming_source="canned"`.
- If the LLM returns SOME valid names but is missing or malformed for
  others, those individual attempts fall back to canned names. The
  overall `naming_source` is `"llm"` (some came from the LLM).
- Names are validated: trimmed, non-empty, ≤ 200 chars. Invalid names
  fall through to canned.

The canned names live in `clipfarm.strategies.STRATEGY_CANNED_NAMES` —
one per strategy id, matching the spec's wording verbatim so the user
recognizes the names even when Ollama is unavailable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from clipfarm.strategies import STRATEGY_CANNED_NAMES, StrategyResult

log = logging.getLogger("clipfarm.attempt_naming")

# Same LLMClient shape as `clipfarm.tagging.LLMClient` — `(messages,
# schema) → parsed_dict_or_None`. Imported lazily to keep this module
# free of tagging dependencies.
LLMClient = Callable[
    [list[dict[str, str]], dict[str, Any]], Optional[dict[str, Any]]
]

MAX_NAME_LENGTH = 200


@dataclass
class AttemptNameSummary:
    """One row of input to the namer — what the LLM sees about each
    attempt-to-name. Keep small; the prompt grows linearly with the
    number of attempts and we want to ship one call."""

    strategy_id: str
    name_hint: str
    continuity_score: float
    clip_count: int
    # First 30 chars of each clip's transcript_text, joined; gives the
    # LLM enough to sense what the attempt sounds like.
    transcript_preview: str


@dataclass
class NamedAttempt:
    """The naming result for one input summary."""

    strategy_id: str
    name: str
    name_source: str  # "llm" or "canned"


def _output_schema(n: int) -> dict[str, Any]:
    """JSON schema for the LLM's batched response. Forces a `names`
    array of exactly `n` strings."""
    return {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "minItems": n,
                "maxItems": n,
                "items": {"type": "string"},
            }
        },
        "required": ["names"],
    }


def _system_prompt() -> str:
    return (
        "You write short, distinctive names for video edits, in the voice "
        "of an indie filmmaker labeling her own takes. 5-12 words each. "
        "Names should feel like how someone would describe what they just "
        "watched — not generic editor lingo. Examples:\n"
        "  - 'the 3 times you said it in almost one take'\n"
        "  - 'best take of each line, in script order'\n"
        "  - 'the version where you started with the BTC line'\n"
        "  - 'the take where the energy picked up'\n\n"
        "You will be given a numbered list of attempts with a short hint "
        "describing each. Return ONE name per attempt, in the same order. "
        "Don't number the names — just return the 'names' array."
    )


def _user_prompt(summaries: list[AttemptNameSummary]) -> str:
    lines = [f"There are {len(summaries)} attempts to name:", ""]
    for i, s in enumerate(summaries, start=1):
        continuity_pct = int(s.continuity_score * 100)
        lines.append(
            f"{i}. strategy={s.strategy_id}, "
            f"hint='{s.name_hint}', "
            f"continuity={continuity_pct}%, "
            f"clips={s.clip_count}"
        )
        if s.transcript_preview:
            preview = s.transcript_preview[:240]
            lines.append(f"   sounds like: {preview!r}")
    return "\n".join(lines)


def _validate_name(raw: Any) -> Optional[str]:
    """Return a normalized name string, or None if invalid."""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name:
        return None
    if len(name) > MAX_NAME_LENGTH:
        # Truncate with ellipsis rather than reject — the LLM
        # occasionally pads with a justification we don't want.
        name = name[: MAX_NAME_LENGTH - 1].rstrip() + "…"
    return name


def name_attempts(
    summaries: list[AttemptNameSummary],
    llm_client: Optional[LLMClient],
) -> list[NamedAttempt]:
    """Batched naming. Returns one `NamedAttempt` per input summary,
    in the same order.

    `llm_client=None` is supported (skips the LLM call entirely, uses
    canned names). Tests use this; the route layer passes a real client.
    """
    if not summaries:
        return []

    canned: list[str] = [
        STRATEGY_CANNED_NAMES.get(s.strategy_id, s.name_hint or s.strategy_id)
        for s in summaries
    ]

    if llm_client is None:
        return [
            NamedAttempt(
                strategy_id=s.strategy_id, name=name, name_source="canned"
            )
            for s, name in zip(summaries, canned)
        ]

    schema = _output_schema(len(summaries))
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_prompt(summaries)},
    ]
    try:
        raw = llm_client(messages, schema)
    except Exception as e:
        log.warning("attempt_naming: LLM client raised, using canned: %s", e)
        raw = None

    if raw is None:
        log.info("attempt_naming: LLM returned None, all canned names")
        return [
            NamedAttempt(strategy_id=s.strategy_id, name=name, name_source="canned")
            for s, name in zip(summaries, canned)
        ]

    names_field = raw.get("names") if isinstance(raw, dict) else None
    if not isinstance(names_field, list):
        log.warning(
            "attempt_naming: missing/invalid 'names' field — using canned. raw=%r",
            raw,
        )
        return [
            NamedAttempt(strategy_id=s.strategy_id, name=name, name_source="canned")
            for s, name in zip(summaries, canned)
        ]

    # Validate per-name; missing or invalid entries fall back to canned.
    result: list[NamedAttempt] = []
    for i, summary in enumerate(summaries):
        candidate = names_field[i] if i < len(names_field) else None
        validated = _validate_name(candidate)
        if validated is None:
            log.warning(
                "attempt_naming: invalid name for attempt %d (%s), using canned",
                i, summary.strategy_id,
            )
            result.append(NamedAttempt(
                strategy_id=summary.strategy_id,
                name=canned[i],
                name_source="canned",
            ))
        else:
            result.append(NamedAttempt(
                strategy_id=summary.strategy_id,
                name=validated,
                name_source="llm",
            ))
    return result


__all__ = [
    "AttemptNameSummary",
    "LLMClient",
    "MAX_NAME_LENGTH",
    "NamedAttempt",
    "name_attempts",
]
