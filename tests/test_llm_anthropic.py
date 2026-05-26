"""Tests for `clipfarm/llm_anthropic.py` — Anthropic client.

Uses unittest.mock to stub the anthropic SDK because the SDK is
imported lazily inside the module. The mock pattern: patch the
module's `_ensure_sdk()` to return a fake `anthropic` namespace whose
`Anthropic(...)` returns a client whose `messages.create(...)`
returns a canned response.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from clipfarm.llm_anthropic import (
    AnthropicUnavailableError,
    chat_with_json_schema_anthropic,
    ping_anthropic,
)


def _make_tool_use_response(tool_input: dict) -> SimpleNamespace:
    """Build a fake anthropic response with one tool_use content block."""
    block = SimpleNamespace(
        type="tool_use",
        name="submit_tags",
        input=tool_input,
    )
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def _make_fake_sdk(create_returns: SimpleNamespace | None = None,
                  create_raises: Exception | None = None) -> SimpleNamespace:
    """Build a stand-in for the `anthropic` module."""
    create_mock = MagicMock()
    if create_raises is not None:
        create_mock.side_effect = create_raises
    else:
        create_mock.return_value = create_returns

    client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
    return SimpleNamespace(
        Anthropic=lambda **kw: client,
        NOT_GIVEN=object(),
    ), create_mock


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_returns_tool_use_input_dict():
    expected = {"results": [{"clip_id": "c0", "category": "on-script"}]}
    fake_sdk, create_mock = _make_fake_sdk(
        create_returns=_make_tool_use_response(expected)
    )
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        out = chat_with_json_schema_anthropic(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "tag these"},
            ],
            schema={"type": "object", "properties": {}, "required": []},
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )
    assert out == expected
    # Verify the call shape: system extracted to top-level, tool_choice set,
    # and prompt caching marker on the system block.
    args = create_mock.call_args.kwargs
    assert args["model"] == "claude-sonnet-4-6"
    assert isinstance(args["system"], list)
    assert args["system"][0]["text"] == "system prompt"
    assert args["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert args["tool_choice"] == {"type": "tool", "name": "submit_tags"}
    assert args["tools"][0]["name"] == "submit_tags"
    # Non-system messages survive in the messages array.
    assert args["messages"] == [{"role": "user", "content": "tag these"}]


def test_chat_no_system_message_passes_NOT_GIVEN():
    """If the orchestrator doesn't include a system message, the
    Anthropic SDK's `system` param gets NOT_GIVEN sentinel (so the
    SDK omits it entirely)."""
    fake_sdk, create_mock = _make_fake_sdk(
        create_returns=_make_tool_use_response({"x": 1})
    )
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        chat_with_json_schema_anthropic(
            [{"role": "user", "content": "hi"}],
            schema={"type": "object"},
            api_key="sk-test", model="claude-sonnet-4-6",
        )
    args = create_mock.call_args.kwargs
    # NOT_GIVEN sentinel from the fake SDK is identity-compared by the
    # real SDK; check it's not a list (i.e. system block wasn't built).
    assert args["system"] is fake_sdk.NOT_GIVEN


# ─────────────────────────────────────────────────────────────────────────────
# Failure paths — return None, never raise
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_returns_none_on_empty_api_key():
    out = chat_with_json_schema_anthropic(
        [{"role": "user", "content": "hi"}],
        schema={"type": "object"},
        api_key="", model="claude-sonnet-4-6",
    )
    assert out is None


def test_chat_returns_none_when_sdk_missing(caplog):
    """If anthropic SDK isn't installed, return None + log warning,
    don't crash the tagging run."""
    def raise_unavailable():
        raise AnthropicUnavailableError("sdk not installed")
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", side_effect=raise_unavailable,
    ):
        with caplog.at_level("WARNING", logger="clipfarm.llm_anthropic"):
            out = chat_with_json_schema_anthropic(
                [{"role": "user", "content": "hi"}],
                schema={"type": "object"},
                api_key="sk-test", model="claude-sonnet-4-6",
            )
    assert out is None
    assert any("sdk not installed" in m for m in caplog.messages)


def test_chat_returns_none_when_create_raises():
    """Network errors, auth failures, etc. — return None for the
    orchestrator's retry path to handle."""
    fake_sdk, _ = _make_fake_sdk(
        create_raises=RuntimeError("connection refused")
    )
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        out = chat_with_json_schema_anthropic(
            [{"role": "user", "content": "hi"}],
            schema={"type": "object"},
            api_key="sk-test", model="claude-sonnet-4-6",
        )
    assert out is None


def test_chat_returns_none_when_no_tool_use_block():
    """If the response somehow has no tool_use block (model refused
    the tool), return None."""
    fake_sdk, _ = _make_fake_sdk(
        create_returns=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I can't")],
            stop_reason="end_turn",
        )
    )
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        out = chat_with_json_schema_anthropic(
            [{"role": "user", "content": "hi"}],
            schema={"type": "object"},
            api_key="sk-test", model="claude-sonnet-4-6",
        )
    assert out is None


# ─────────────────────────────────────────────────────────────────────────────
# ping_anthropic
# ─────────────────────────────────────────────────────────────────────────────


def test_ping_returns_false_on_empty_api_key():
    assert ping_anthropic("", model="claude-sonnet-4-6") is False


def test_ping_returns_true_on_valid_response():
    fake_sdk, _ = _make_fake_sdk(
        create_returns=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi")],
        )
    )
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        assert ping_anthropic("sk-test", model="claude-sonnet-4-6") is True


def test_ping_returns_false_when_create_raises():
    fake_sdk, _ = _make_fake_sdk(create_raises=Exception("401"))
    with patch(
        "clipfarm.llm_anthropic._ensure_sdk", return_value=fake_sdk,
    ):
        assert ping_anthropic("sk-test", model="claude-sonnet-4-6") is False
