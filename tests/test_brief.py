"""Tests for `clipfarm/brief.py` — the YAML frontmatter + markdown parser."""
from __future__ import annotations

import pytest

from clipfarm.brief import BriefParseError, parse_brief


_FULL_BRIEF = """---
name: btc explainer v0.4
script:
  - Hey, today I want to talk about Bitcoin self-custody.
  - The reason it matters is that you control your money.
  - And here's how to actually do it.
sections:
  - the hook
  - the why
  - the how
tags:
  - hook
  - self-custody
  - mistakes
---

# What's good

Energy, not over-rehearsed. Tone: smart but accessible.

# Notes

- Avoid the "OK so" intro
- Spend more time on the why than the how
"""


def test_parse_full_brief():
    parsed = parse_brief(_FULL_BRIEF)
    assert parsed.name == "btc explainer v0.4"
    assert parsed.script is not None
    assert len(parsed.script.lines) == 3
    assert parsed.script.lines[0].startswith("Hey, today I want")
    assert parsed.sections == ["the hook", "the why", "the how"]
    assert parsed.tags == ["hook", "self-custody", "mistakes"]
    assert "Energy, not over-rehearsed" in parsed.body_md


def test_parse_minimal_brief_just_name():
    parsed = parse_brief("---\nname: just a name\n---\n")
    assert parsed.name == "just a name"
    assert parsed.script is None
    assert parsed.sections == []
    assert parsed.tags == []
    assert parsed.body_md == ""


def test_body_only_brief_rejected():
    """Pure prose without frontmatter is 'not yet a project'."""
    with pytest.raises(BriefParseError, match="frontmatter"):
        parse_brief("Just some notes I'm jotting down.\n\nNo metadata yet.")


def test_missing_name_raises():
    text = "---\nscript:\n  - hello\n---\n"
    with pytest.raises(BriefParseError, match="'name' is required"):
        parse_brief(text)


def test_empty_name_raises():
    text = "---\nname: \"\"\n---\n"
    with pytest.raises(BriefParseError, match="must not be empty"):
        parse_brief(text)


def test_whitespace_only_name_raises():
    text = "---\nname: '   '\n---\n"
    with pytest.raises(BriefParseError, match="must not be empty"):
        parse_brief(text)


def test_non_string_name_raises():
    text = "---\nname: 42\n---\n"
    with pytest.raises(BriefParseError, match="must be a string"):
        parse_brief(text)


def test_malformed_yaml_raises_with_position():
    text = "---\nname: ok\nscript: [unclosed\n---\n"
    with pytest.raises(BriefParseError) as exc:
        parse_brief(text)
    assert "YAML parse error" in str(exc.value)


def test_non_list_script_raises():
    text = "---\nname: ok\nscript: just a string\n---\n"
    with pytest.raises(BriefParseError, match="'script' must be a list"):
        parse_brief(text)


def test_non_string_script_entry_raises():
    text = "---\nname: ok\nscript:\n  - 42\n---\n"
    with pytest.raises(BriefParseError, match="script\\[0\\]"):
        parse_brief(text)


def test_non_list_sections_raises():
    text = "---\nname: ok\nsections: nope\n---\n"
    with pytest.raises(BriefParseError, match="'sections' must be a list"):
        parse_brief(text)


def test_non_list_tags_raises():
    text = "---\nname: ok\ntags: nope\n---\n"
    with pytest.raises(BriefParseError, match="'tags' must be a list"):
        parse_brief(text)


def test_duplicate_script_lines_preserved():
    """Locked policy: tolerate duplicates (don't dedupe, don't reject).
    order_idx + occurrence_index distinguishes them in the ProjectTag set."""
    text = """---
name: x
script:
  - hook line
  - the body
  - hook line
---
"""
    parsed = parse_brief(text)
    assert parsed.script is not None
    assert parsed.script.lines == ["hook line", "the body", "hook line"]


def test_name_strips_surrounding_whitespace():
    text = '---\nname: "  spaced  "\n---\n'
    parsed = parse_brief(text)
    assert parsed.name == "spaced"


def test_body_preserves_markdown_exactly():
    text = """---
name: ok
---

# Heading

Paragraph with **bold** and `code`.

- bullet 1
- bullet 2
"""
    parsed = parse_brief(text)
    # Body starts after the closing '---' newline.
    assert "# Heading" in parsed.body_md
    assert "**bold**" in parsed.body_md
    assert "- bullet 1" in parsed.body_md


def test_frontmatter_with_quoted_special_chars():
    """Script lines with colons / leading hyphens need single quotes. The
    user-facing inline help calls this out; this test locks the parser
    behavior on a real-looking script."""
    text = '''---
name: ok
script:
  - "Today's question: how does this work?"
  - "- this is just a line that starts with a dash"
---
'''
    parsed = parse_brief(text)
    assert parsed.script is not None
    assert parsed.script.lines[0] == "Today's question: how does this work?"
    assert parsed.script.lines[1].startswith("- ")
