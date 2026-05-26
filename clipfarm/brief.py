"""Brief markdown parser.

A brief is YAML frontmatter (between `---` fences) + a markdown body. The
frontmatter carries structured project metadata (`name`, optional `script`,
`sections`, `tags`); the body is free-form prose ("what's good" notes) that
Phase 6's LLM tagging will use as context.

`name` is the only required field; everything else is optional and defaults
sensibly (no script / no sections / no tags). A brief without YAML
frontmatter — or with frontmatter that doesn't include `name` — is invalid
("not yet a project").

Parser is pure: no I/O, no FastAPI, no state mutation. The route layer
calls `parse_brief(text)` and turns `BriefParseError` into a 400 with the
YAML position info preserved.
"""
from __future__ import annotations

import re
import textwrap
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from clipfarm.models import Script, StrictModel

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?\n)---\s*(?:\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)

# Tolerate a leading preamble — blank lines, a title line, a header,
# anything before the first `---` fence. The frontmatter delimiter itself
# is still required (no preamble = no fence = "not yet a project"), but
# we no longer demand that `---` is the literal first character.
#
# Triggered by real dogfood paste (2026-05-25): the Brief textarea had
# "New project" at the top above the `---` fence, which the strict
# `\A---` anchor rejected with a confusing error.
_PREAMBLE_THEN_FRONTMATTER_RE = re.compile(
    r"\A(?P<preamble>(?:[^\n]*\n)*?)---\s*\n(?P<yaml>.*?\n)---\s*(?:\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)

# Keys whose values we'll accept in "loose block list" form (column-0
# dashes, blank lines between items, multi-line continuations). Applied
# only as a fallback when strict YAML parsing fails — well-formed YAML
# is never rewritten.
_LOOSE_LIST_KEYS: tuple[str, ...] = ("script", "sections", "tags")

_TOP_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$")
_INLINE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")
_ITEM_START_RE = re.compile(r"^\s*-\s+(.*)$")


def _loosen_list_blocks(yaml_text: str) -> str:
    """Rewrite the YAML so `script:` / `sections:` / `tags:` blocks
    tolerate natural-paragraph formatting:

      - column-0 dashes (not indented under the key),
      - blank lines between items,
      - multi-line items (lines after a `-` until the next `-` or
        blank line get joined into one item with single spaces),

    Output is canonical (`  - "item"`) YAML that strict parsing accepts.

    Idempotent on already-well-formed YAML: re-running on the output
    produces the same output. Only invoked as a fallback when the
    initial `yaml.safe_load` raises — well-formed briefs never see
    this rewrite path.
    """
    lines = yaml_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _TOP_KEY_RE.match(line)
        if not (m and m.group(1) in _LOOSE_LIST_KEYS):
            out.append(line)
            i += 1
            continue

        # Found a candidate key. Scan its block to the next top-level
        # key (column-0 word followed by `:`) or end-of-text.
        out.append(line)
        i += 1
        items: list[str] = []
        current: list[str] = []
        block_lines_consumed = 0
        block_start = i

        def _flush() -> None:
            joined = " ".join(s.strip() for s in current if s.strip()).strip()
            if joined:
                items.append(joined)
            current.clear()

        while i < len(lines):
            blk = lines[i]
            # Next top-level key (must be col-0, contain `:`, match key pattern) → end of block.
            if blk and not blk[0].isspace() and _INLINE_KEY_RE.match(blk):
                break
            if blk.strip() == "":
                _flush()
                i += 1
                block_lines_consumed += 1
                continue
            im = _ITEM_START_RE.match(blk)
            if im:
                _flush()
                current.append(im.group(1))
                i += 1
                block_lines_consumed += 1
                continue
            # Continuation of the current item (must be non-blank, non-item,
            # not a new key, and we must already have an item in progress).
            if current:
                current.append(blk)
                i += 1
                block_lines_consumed += 1
                continue
            # Stray content before any `-` item under this key — refuse to
            # rewrite this block; bail by restoring scanned lines.
            out.extend(lines[block_start:i + 1])
            i += 1
            block_lines_consumed = 0
            items = []
            current = []
            break
        else:
            # Loop ended at end-of-text — flush.
            _flush()

        # If we reached the end-of-block by hitting a key, flush remaining.
        if current:
            _flush()

        if not items and block_lines_consumed == 0:
            # Nothing scanned (empty block) — leave as-is.
            continue
        if not items:
            # We consumed lines but found no parseable items. Restore
            # them verbatim so the original error (if any) is preserved.
            out.extend(lines[block_start:block_start + block_lines_consumed])
            continue

        # Emit the canonical block list. `yaml.safe_dump` handles all
        # quoting edge cases (apostrophes, leading dashes, colons in text).
        for item in items:
            dumped = yaml.safe_dump(
                item, default_flow_style=True, default_style='"',
                allow_unicode=True, width=10**9,
            ).rstrip("\n")
            out.append(f"  - {dumped}")

    # Preserve trailing newline if the original had one.
    trailing = "\n" if yaml_text.endswith("\n") else ""
    return "\n".join(out) + trailing


class BriefParseError(ValueError):
    """Raised when a brief can't be parsed. Carries the offending line /
    column when the underlying YAML library exposes them, so the route
    layer can pass that through to the user verbatim in the 400 detail.
    """

    def __init__(self, message: str, *, line: Optional[int] = None, column: Optional[int] = None) -> None:
        self.line = line
        self.column = column
        if line is not None and column is not None:
            message = f"{message} (line {line}, col {column})"
        super().__init__(message)


class ParsedBrief(StrictModel):
    """The parsed brief — frontmatter fields + body. The orchestrator in
    `projects.py` reads these to build `Project` + `ProjectTag` entries."""

    name: str = Field(..., min_length=1)
    script: Optional[Script] = None
    sections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    body_md: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _name_must_be_nonempty_string(cls, v):
        if not isinstance(v, str):
            raise BriefParseError(
                f"'name' must be a string, got {type(v).__name__}"
            )
        stripped = v.strip()
        if not stripped:
            raise BriefParseError("'name' must not be empty or whitespace-only")
        return stripped


def parse_brief(text: str) -> ParsedBrief:
    """Parse a brief into a `ParsedBrief`. Raises `BriefParseError` on:
    - Missing or malformed YAML frontmatter.
    - Missing / empty / non-string `name`.
    - YAML syntax errors (with line/column position when available).

    Body-only briefs (no `---` frontmatter at all) are **rejected**:
    pure prose without metadata is "not yet a project" — keeps the door
    closed so Phase 6's tagging code doesn't have to special-case
    structure-less projects.
    """
    # Strip common leading indent from the entire brief. This rescues
    # paste-from-code-block briefs where every line carries the same
    # 2- or 4-space prefix (real dogfood paste, 2026-05-25). Idempotent
    # on already-flush briefs — `textwrap.dedent` is a no-op when the
    # common indent is zero.
    text = textwrap.dedent(text)

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        # Fallback: tolerate a leading preamble (blank lines, a title
        # line, a markdown heading) before the first `---` fence. The
        # fence itself is still required — pure prose without `---` is
        # "not yet a project" and stays rejected.
        match = _PREAMBLE_THEN_FRONTMATTER_RE.match(text)
        if match is None:
            raise BriefParseError(
                "brief must include YAML frontmatter delimited by '---' "
                "fences (a line that is '---' alone, then YAML, then "
                "another '---' on its own line). Pure prose without "
                "frontmatter isn't yet a project."
            )

    yaml_text = match.group("yaml")
    body_md = match.group("body")

    try:
        front = yaml.safe_load(yaml_text)
    except yaml.YAMLError as primary_err:
        # Fallback: try the loose-list rewrite for natural-paragraph
        # script formatting (column-0 dashes, blank lines between items,
        # multi-line continuations). Only kicks in on initial-parse
        # failure — well-formed YAML never hits this path.
        try:
            loosened = _loosen_list_blocks(yaml_text)
            if loosened != yaml_text:
                front = yaml.safe_load(loosened)
            else:
                raise primary_err
        except yaml.YAMLError:
            # Either the rewrite was a no-op OR the rewrite's output
            # still doesn't parse. Surface the ORIGINAL error — it
            # points at the user's actual input, not our rewrite.
            mark = getattr(primary_err, "problem_mark", None)
            line = mark.line + 1 + 1 if mark is not None else None  # +1 for 1-indexed, +1 for '---' line
            column = mark.column + 1 if mark is not None else None
            raise BriefParseError(
                f"YAML parse error: {primary_err}", line=line, column=column
            ) from primary_err

    if front is None:
        front = {}
    if not isinstance(front, dict):
        raise BriefParseError(
            f"frontmatter must be a YAML mapping at the top level, got "
            f"{type(front).__name__}"
        )

    raw_name = front.get("name")
    raw_script = front.get("script")
    raw_sections = front.get("sections") or []
    raw_tags = front.get("tags") or []

    script: Optional[Script] = None
    if raw_script is not None:
        if not isinstance(raw_script, list):
            raise BriefParseError(
                f"'script' must be a list of strings, got {type(raw_script).__name__}"
            )
        # Coerce every line to a string; any non-string at this level is a hard error.
        cleaned: list[str] = []
        for i, line in enumerate(raw_script):
            if not isinstance(line, str):
                raise BriefParseError(
                    f"'script[{i}]' must be a string, got {type(line).__name__}"
                )
            cleaned.append(line)
        script = Script(lines=cleaned)

    if not isinstance(raw_sections, list):
        raise BriefParseError(
            f"'sections' must be a list of strings, got {type(raw_sections).__name__}"
        )
    sections: list[str] = []
    for i, s in enumerate(raw_sections):
        if not isinstance(s, str):
            raise BriefParseError(
                f"'sections[{i}]' must be a string, got {type(s).__name__}"
            )
        sections.append(s)

    if not isinstance(raw_tags, list):
        raise BriefParseError(
            f"'tags' must be a list of strings, got {type(raw_tags).__name__}"
        )
    tags: list[str] = []
    for i, t in enumerate(raw_tags):
        if not isinstance(t, str):
            raise BriefParseError(
                f"'tags[{i}]' must be a string, got {type(t).__name__}"
            )
        tags.append(t)

    if raw_name is None:
        raise BriefParseError(
            "'name' is required in the frontmatter — projects without "
            "names aren't valid"
        )

    try:
        return ParsedBrief(
            name=raw_name,
            script=script,
            sections=sections,
            tags=tags,
            body_md=body_md,
        )
    except BriefParseError:
        raise
    except Exception as e:
        # Pydantic ValidationError from name validator wraps our exception
        # inside; unwrap if possible.
        for err in getattr(e, "errors", lambda: [])():
            ctx = err.get("ctx", {}) or {}
            inner = ctx.get("error")
            if isinstance(inner, BriefParseError):
                raise inner from e
        raise BriefParseError(f"brief validation failed: {e}") from e


__all__ = ["BriefParseError", "ParsedBrief", "parse_brief"]
