"""Anthropic API client for tagging + premade-attempt naming.

Same surface as `clipfarm.llm.chat_with_json_schema`: take `messages`
+ `schema`, return parsed dict or `None` on any failure. The
orchestrator never knows which provider it's talking to.

**JSON-schema-constrained output via tool use.** Anthropic doesn't
have Ollama's `format: <schema>` parameter directly — but tool use
gives equivalent (better, actually) structured output: define a
single tool that takes the desired schema as its input, force the
model to call that tool, and parse the tool_use response.

**Prompt caching.** The tagging system prompt (project brief +
script lines + tag enum) is identical across every batch in a run.
Mark it with `cache_control: {type: "ephemeral"}` so subsequent
batches in the same 5-minute window hit the cache. For a 10-batch
chrysalis tagging run that's a real cost + latency win.

**Lazy import** of the `anthropic` SDK. Users who stay on Ollama
never pay the import cost. Settings flip to "anthropic" + the first
tagging call triggers the import.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("clipfarm.llm_anthropic")

# Conservative max-tokens for tagging output. A 10-clip batch returns
# ~500 tokens of structured JSON; 4096 leaves plenty of headroom.
DEFAULT_MAX_TOKENS = 4096


class AnthropicUnavailableError(RuntimeError):
    """Raised when the `anthropic` SDK isn't installed or the API key
    isn't configured. The tagging route surfaces this as a clear
    error to the user."""


def _ensure_sdk():
    """Lazy import. Raises AnthropicUnavailableError on missing pkg."""
    try:
        import anthropic  # type: ignore
        return anthropic
    except ImportError as e:
        raise AnthropicUnavailableError(
            "the `anthropic` Python package isn't installed. "
            "Run `uv sync` or add it to your environment."
        ) from e


def ping_anthropic(
    api_key: str, *, model: str, timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """Test the API key + model with a tiny request.

    Returns `(True, None)` on success, or `(False, error_message)`
    where `error_message` is a short user-facing string explaining
    what failed (auth error, unknown model, network error). The
    settings route includes this in the 400 detail so the UI can show
    the specific cause instead of generic "test failed."

    Cost on success: ~3 input + ~3 output tokens (negligible). On
    failure: 0 tokens billed (request rejected at auth or model
    resolution).
    """
    if not api_key:
        return False, "no API key provided"
    try:
        anthropic = _ensure_sdk()
    except AnthropicUnavailableError as e:
        return False, str(e)
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        response = client.messages.create(
            model=model,
            max_tokens=8,
            messages=[{"role": "user", "content": "say hi"}],
        )
        if not response.content:
            return False, "response had no content blocks"
        return True, None
    except Exception as e:
        # Anthropic SDK raises a handful of typed exception classes
        # (anthropic.APIStatusError, anthropic.APIConnectionError,
        # anthropic.AuthenticationError, etc.). We don't want to
        # depend on SDK-internal types, so extract the most specific
        # short message we can.
        msg = _extract_error_message(e)
        log.info("ping_anthropic: failed (%s)", msg)
        return False, msg


def _extract_error_message(exc: Exception) -> str:
    """Pull the most-specific human-readable message out of an
    arbitrary SDK exception. Falls back to `str(exc)` or the class
    name. Always returns a non-empty string."""
    # Anthropic SDK typically exposes a `.message` attribute on
    # APIStatusError subclasses; some carry `.body` with structured info.
    msg = getattr(exc, "message", None)
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def chat_with_json_schema_anthropic(
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    api_key: str,
    model: str,
    timeout: float = 120.0,
) -> Optional[dict[str, Any]]:
    """Anthropic equivalent of `clipfarm.llm.chat_with_json_schema`.
    Returns the parsed tool_use input dict on success or `None` on
    any failure mode.

    Implementation: define a single `submit_tags` tool whose
    `input_schema` is the caller's schema, force tool use via
    `tool_choice={type: "tool", name: ...}`, and pull the tool input
    from the response's content blocks.

    The system message (if present, first message with role="system")
    is extracted into the top-level `system` parameter and marked
    with `cache_control: ephemeral` so batched runs hit the prompt
    cache.
    """
    if not api_key:
        log.warning("anthropic: no API key configured; returning None")
        return None
    try:
        anthropic = _ensure_sdk()
    except AnthropicUnavailableError as e:
        log.warning("anthropic: %s", e)
        return None

    # Split system / non-system messages. The Anthropic API expects
    # `system` as a top-level param (not a message).
    system_text: Optional[str] = None
    user_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system" and system_text is None:
            system_text = m.get("content", "") or ""
            continue
        user_messages.append(m)

    # Mark the system block with ephemeral cache_control so repeated
    # batched calls in the same run hit the prompt cache (5-min TTL).
    system_param: Any
    if system_text:
        system_param = [{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_param = anthropic.NOT_GIVEN  # SDK sentinel

    tools = [{
        "name": "submit_tags",
        "description": (
            "Submit the structured tagging result for the batch of "
            "clips. Call this exactly once with the full results array."
        ),
        "input_schema": schema,
    }]

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        response = client.messages.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=system_param,
            messages=user_messages,
            tools=tools,
            tool_choice={"type": "tool", "name": "submit_tags"},
        )
    except Exception as e:
        log.warning("anthropic: request failed: %s", e)
        return None

    # Extract the tool_use block.
    for block in response.content or []:
        # Both anthropic.types.ToolUseBlock and dict shapes possible.
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        block_name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        block_input = getattr(block, "input", None)
        if block_input is None and isinstance(block, dict):
            block_input = block.get("input")
        if block_type == "tool_use" and block_name == "submit_tags":
            if isinstance(block_input, dict):
                return block_input
            log.warning(
                "anthropic: tool_use input is not a dict (got %s)",
                type(block_input).__name__,
            )
            return None

    log.warning(
        "anthropic: response had no submit_tags tool_use block; "
        "stop_reason=%s",
        getattr(response, "stop_reason", "?"),
    )
    return None


__all__ = [
    "AnthropicUnavailableError",
    "DEFAULT_MAX_TOKENS",
    "chat_with_json_schema_anthropic",
    "ping_anthropic",
]
