"""Tests for `clipfarm/llm.py` — patches `httpx.post` to return canned
responses. Confirms every failure mode degrades to `None` cleanly,
never raises."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clipfarm.llm import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    chat_with_json_schema,
    ping_ollama,
)


def _canned_response(*, status: int = 200, body: object = None, text: str | None = None):
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status
    if text is not None:
        mock.text = text
        mock.json = MagicMock(side_effect=json.JSONDecodeError("x", text, 0))
    else:
        mock.json = MagicMock(return_value=body)
        mock.text = json.dumps(body) if body is not None else ""
    return mock


_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array"},
    },
    "required": ["items"],
}


# ---------- Happy path ------------------------------------------------------


def test_returns_parsed_json_on_clean_response():
    inner = json.dumps({"items": [{"clip_id": "c1", "category": "on-script"}]})
    canned = _canned_response(body={"message": {"content": inner}})
    with patch("clipfarm.llm.httpx.post", return_value=canned):
        result = chat_with_json_schema(
            [{"role": "user", "content": "hi"}], _SCHEMA
        )
    assert result == {"items": [{"clip_id": "c1", "category": "on-script"}]}


def test_request_body_carries_format_schema():
    inner = json.dumps({"items": []})
    canned = _canned_response(body={"message": {"content": inner}})
    with patch("clipfarm.llm.httpx.post", return_value=canned) as post:
        chat_with_json_schema(
            [{"role": "user", "content": "hi"}], _SCHEMA
        )
    sent = post.call_args.kwargs["json"]
    assert sent["format"] == _SCHEMA
    assert sent["stream"] is False
    assert sent["model"] == DEFAULT_MODEL
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


# ---------- Failure modes (all → None, never raise) ------------------------


def test_http_500_returns_none():
    canned = _canned_response(status=500, text="boom")
    with patch("clipfarm.llm.httpx.post", return_value=canned):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


def test_connection_error_returns_none():
    with patch(
        "clipfarm.llm.httpx.post",
        side_effect=httpx.ConnectError("refused"),
    ):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


def test_timeout_returns_none():
    with patch(
        "clipfarm.llm.httpx.post",
        side_effect=httpx.ReadTimeout("slow"),
    ):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


def test_wrapper_response_not_json_returns_none():
    canned = _canned_response(status=200, text="not even valid json")
    with patch("clipfarm.llm.httpx.post", return_value=canned):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


def test_missing_message_content_returns_none():
    """Ollama returned 200 but the wrapper doesn't carry message.content
    (e.g. an empty response body schema)."""
    canned = _canned_response(body={"done": True})
    with patch("clipfarm.llm.httpx.post", return_value=canned):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


def test_inner_content_not_json_returns_none():
    """The LLM ignored the `format` schema and emitted free-form text."""
    canned = _canned_response(
        body={"message": {"content": "I'm sorry, I cannot do that."}}
    )
    with patch("clipfarm.llm.httpx.post", return_value=canned):
        assert chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        ) is None


# ---------- Host override ---------------------------------------------------


def test_ollama_host_env_overrides_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://other-host:9999")
    inner = json.dumps({"items": []})
    canned = _canned_response(body={"message": {"content": inner}})
    with patch("clipfarm.llm.httpx.post", return_value=canned) as post:
        chat_with_json_schema(
            [{"role": "user", "content": "x"}], _SCHEMA
        )
    assert post.call_args.args[0].startswith("http://other-host:9999/")


# ---------- ping_ollama -----------------------------------------------------


def test_ping_ollama_true_on_200():
    canned = MagicMock()
    canned.status_code = 200
    with patch("clipfarm.llm.httpx.get", return_value=canned):
        assert ping_ollama() is True


def test_ping_ollama_false_on_connection_error():
    with patch("clipfarm.llm.httpx.get", side_effect=httpx.ConnectError("x")):
        assert ping_ollama() is False


def test_ping_ollama_false_on_non_200():
    canned = MagicMock()
    canned.status_code = 500
    with patch("clipfarm.llm.httpx.get", return_value=canned):
        assert ping_ollama() is False
