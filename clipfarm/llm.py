"""Thin Ollama HTTP client.

`chat_with_json_schema(messages, schema, ...)` posts to Ollama's
`/api/chat` endpoint with a JSON-schema-constrained `format` field, gets
back a JSON string in `response.message.content`, parses it, and returns
the parsed dict (or `None` on any failure — never raises). The tagging
orchestrator handles None as "this batch failed" and runs the retry path.

Why a direct httpx wrapper and not the `ollama` Python SDK: the surface
is one POST. Pulling in the SDK adds an entire dependency for what's
five lines of httpx.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger("clipfarm.llm")

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_HOST = "http://localhost:11434"


class OllamaUnreachableError(RuntimeError):
    """Raised by `ping_ollama` when the host doesn't respond. The tagging
    route surfaces this as 502 to the user on the very first call so
    they know the LLM endpoint is down rather than wait through a long
    run of retries."""


def _host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def ping_ollama(*, timeout: float = 3.0) -> bool:
    """Cheap reachability check. Returns True if Ollama responds to
    `GET /api/tags` within the timeout; False otherwise. The tagging
    route uses this as the precondition before kicking off a long run."""
    try:
        r = httpx.get(f"{_host()}/api/tags", timeout=timeout)
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def chat_with_json_schema(
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
) -> Optional[dict[str, Any]]:
    """Post a chat completion to Ollama with a JSON-schema-constrained
    `format` field. Returns the parsed JSON dict on success or `None`
    on any failure mode (HTTP error, malformed JSON, schema mismatch,
    timeout, connection refused).

    Never raises. The orchestrator handles `None` as "this batch
    failed" and runs the retry path; surfacing exceptions to it would
    couple the orchestrator's error handling to httpx's exception
    hierarchy, which is unnecessary noise.
    """
    host = _host()
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "format": schema,
        # Stream=False so the response is one JSON object, not a stream
        # of token events. We get a slightly higher latency but a
        # simpler parse path. Streaming is a Phase 9+ polish add.
        "stream": False,
        # Lower temperature for tagging — we want consistent
        # categorization, not creative writing.
        "options": {"temperature": 0.2},
    }

    try:
        r = httpx.post(f"{host}/api/chat", json=body, timeout=timeout)
    except (httpx.HTTPError, OSError) as e:
        log.warning("ollama: request failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning(
            "ollama: HTTP %s — body=%s", r.status_code, r.text[:300]
        )
        return None

    try:
        wrapper = r.json()
    except json.JSONDecodeError as e:
        log.warning("ollama: response not JSON: %s; body=%s", e, r.text[:300])
        return None

    content = (wrapper.get("message") or {}).get("content")
    if not isinstance(content, str):
        log.warning(
            "ollama: missing message.content in response; got keys=%s",
            list(wrapper.keys()),
        )
        return None

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        log.warning(
            "ollama: content not JSON-parseable (`format` constraint may have "
            "failed): %s; content excerpt=%s",
            e,
            content[:300],
        )
        return None


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "OllamaUnreachableError",
    "chat_with_json_schema",
    "ping_ollama",
]
