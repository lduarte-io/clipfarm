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
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from clipfarm.models import Script, StrictModel

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?\n)---\s*(?:\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)


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
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise BriefParseError(
            "brief must start with YAML frontmatter delimited by '---' "
            "fences (the first line is '---', then YAML, then another "
            "'---' on its own line). Pure prose without frontmatter "
            "isn't yet a project."
        )

    yaml_text = match.group("yaml")
    body_md = match.group("body")

    try:
        front = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 + 1 if mark is not None else None  # +1 for 1-indexed, +1 for '---' line
        column = mark.column + 1 if mark is not None else None
        raise BriefParseError(f"YAML parse error: {e}", line=line, column=column) from e

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
