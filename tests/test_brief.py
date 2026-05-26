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


# ---- Loose-list fallback (natural-paragraph script formatting) ----


def test_loose_script_column_zero_dashes_and_blank_lines():
    """A brief pasted in 'natural paragraph' form — script lines at
    column 0, separated by blank lines — should parse via the fallback
    rewrite. Mirrors the brief Lillian hit in dogfood (2026-05-25).
    """
    text = """---
name: break the chrysalis
script:
  - Working on my goals hasn't brought me closer to having them done.

- I have a mostly finished app, a ton of unedited videos, and a note with 200 ideas.
And the issue is that you can't multiply your way out of 0.

- I turned 23 last week and decided it's the last birthday I'll feel slow.

- I want to be downright overwhelmed next year.
sections:
  - the hook
  - the why
tags:
  - hook
  - mistakes
---

# What's good

Energy.
"""
    parsed = parse_brief(text)
    assert parsed.name == "break the chrysalis"
    assert parsed.script is not None
    assert len(parsed.script.lines) == 4
    # Multi-line item got joined into one string.
    assert parsed.script.lines[1].startswith("I have a mostly finished app")
    assert "multiply your way out of 0" in parsed.script.lines[1]
    # Sections + tags blocks below the loose script block still parsed.
    assert parsed.sections == ["the hook", "the why"]
    assert parsed.tags == ["hook", "mistakes"]


def test_loose_script_all_column_zero_items():
    """Edge case: every item is at column 0, not just some — still
    rewritten correctly."""
    text = """---
name: x
script:
- one
- two
- three
---
"""
    parsed = parse_brief(text)
    assert parsed.script is not None
    assert parsed.script.lines == ["one", "two", "three"]


def test_loose_rewrite_idempotent_on_well_formed_yaml():
    """Already-well-formed briefs MUST NOT take the rewrite path at all
    — the original `yaml.safe_load` succeeds first. Lock that with a
    spy: when we patch the rewriter to raise, well-formed input still
    parses (proving the rewriter wasn't reached)."""
    from unittest.mock import patch

    text = """---
name: ok
script:
  - line one
  - line two
sections:
  - alpha
---
"""

    def fail(_):
        raise AssertionError("loose-rewrite should NOT run on well-formed YAML")

    with patch("clipfarm.brief._loosen_list_blocks", side_effect=fail):
        parsed = parse_brief(text)
    assert parsed.script is not None
    assert parsed.script.lines == ["line one", "line two"]


def test_loose_rewrite_failure_surfaces_original_error():
    """If the YAML is genuinely broken (not just loose-list formatting),
    the user gets the ORIGINAL error message — pointing at their
    actual input — not an artifact of our rewrite attempt. Here the
    error is an unclosed quote on `name`, which the loose-list rewrite
    can't touch (it only rewrites script/sections/tags blocks)."""
    text = '''---
name: "ok
script:
  - one
---
'''
    with pytest.raises(BriefParseError) as exc_info:
        parse_brief(text)
    # Original error mentions YAML parse problem.
    assert "YAML parse error" in str(exc_info.value)


def test_loose_apostrophe_preserved():
    """Common-case dogfood text contains apostrophes; the rewrite must
    quote properly so they round-trip."""
    text = """---
name: x
script:
- I'm calling it break the chrysalis because I don't think it was wasted.
---
"""
    parsed = parse_brief(text)
    assert parsed.script is not None
    assert "I'm calling it" in parsed.script.lines[0]
    assert "don't think" in parsed.script.lines[0]


def test_leading_preamble_before_frontmatter_tolerated():
    """A leading title / header line above the `---` fence shouldn't
    reject the brief. Mirrors a real dogfood paste (2026-05-25) where
    'New project' UI text got captured above the frontmatter."""
    text = """New project
---
name: ok
script:
  - one
---

# What's good

energy
"""
    parsed = parse_brief(text)
    assert parsed.name == "ok"
    assert parsed.script is not None
    assert parsed.script.lines == ["one"]
    assert "energy" in parsed.body_md


def test_blank_lines_before_frontmatter_tolerated():
    text = "\n\n\n---\nname: ok\n---\n"
    parsed = parse_brief(text)
    assert parsed.name == "ok"


def test_multi_line_preamble_tolerated():
    text = """# My draft notes

Some prose I forgot to delete.

---
name: ok
---
"""
    parsed = parse_brief(text)
    assert parsed.name == "ok"


def test_no_frontmatter_at_all_still_rejected():
    """The preamble-tolerance must not paper over a brief that has no
    `---` fence anywhere. Pure prose is still 'not yet a project'."""
    text = "Just some prose with no frontmatter at all.\n"
    with pytest.raises(BriefParseError) as exc_info:
        parse_brief(text)
    assert "frontmatter" in str(exc_info.value)


def test_loose_continuation_lines_joined_with_single_space():
    """Continuation lines (non-blank, non-item) get joined with single
    spaces into the previous item — no preserved line breaks."""
    text = """---
name: x
script:
- first item with
  some continuation
  and more

- second item
---
"""
    parsed = parse_brief(text)
    assert parsed.script is not None
    assert parsed.script.lines == [
        "first item with some continuation and more",
        "second item",
    ]
