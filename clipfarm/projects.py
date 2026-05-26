"""Project orchestration — create / update / delete + the name-keyed
merge that preserves ProjectTag IDs across brief edits.

Pure: mutates `ClipFarmState` in place, returns counts. No I/O, no
snapshot. The route layer wraps the call in
`commit_state_with_snapshot(app, reason="...")`.

Identity rules for the merge (from PHASES.md Phase 5 → "Name-keyed tag
merge"):

- Section identity = `name`. Reordering preserves ProjectTag IDs;
  renaming creates a new ProjectTag and removes the old (clip refs to the
  removed one become dangling tombstones with `stale: true`).
- Line identity = `(parent_section_name, line_text, occurrence_index)`.
  Reordering within the section preserves IDs; renaming creates a new
  one; moving between sections creates a new one. `occurrence_index`
  differentiates duplicate lines.
- Tags (ad-hoc `tags:` in the brief) are name-keyed at the project level
  (no parent).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from clipfarm.brief import ParsedBrief
from clipfarm.models import (
    ClipFarmState,
    Project,
    ProjectTag,
    Script,
    StrictModel,
)


# ---------- Helpers -----------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_project_id(state: ClipFarmState) -> str:
    used = {int(k) for k in state.projects.keys() if k.isdigit()}
    return str(max(used) + 1) if used else "1"


def _next_tag_id(existing_ids: set[str]) -> str:
    used = {int(k) for k in existing_ids if k.isdigit()}
    return str(max(used) + 1) if used else "1"


def _build_tags_from_brief(
    parsed: ParsedBrief,
    *,
    existing_tags: Optional[dict[str, ProjectTag]] = None,
) -> dict[str, ProjectTag]:
    """Materialize the brief's sections + lines + ad-hoc tags into a
    `dict[str, ProjectTag]`. If `existing_tags` is supplied, performs the
    name-keyed merge that preserves IDs for sections/lines whose identity
    survived the edit.

    Returns the new `tags` dict. The caller can compare against the old
    one to figure out which tag IDs disappeared (those need their
    `clip_project_tags` rows flipped to `stale: true` and left as
    dangling tombstones — same pattern as Phase 4's `delete_clip`).
    """
    existing_tags = existing_tags or {}

    # Index the existing tag set by identity so we can preserve IDs.
    # Build three identity maps:
    #   sections_by_name[name] -> tag_id
    #   lines_by_identity[(parent_section_name, line_text, occurrence_idx)] -> tag_id
    #   adhoc_by_name[name] -> tag_id
    sections_by_name: dict[str, str] = {}
    lines_by_identity: dict[tuple[str, str, int], str] = {}
    adhoc_by_name: dict[str, str] = {}

    # First pass: index sections so we can resolve parent IDs to names for
    # the line identity lookup.
    section_id_to_name: dict[str, str] = {}
    for tid, tag in existing_tags.items():
        if tag.kind == "section" and tag.parent_id is None:
            section_id_to_name[tid] = tag.name

    # Adhoc tags are line-kind with parent_id None? Spec ambiguous — model
    # only has 'section' / 'line'. Adhoc tags from `tags:` in the brief
    # land as `kind="line"` with `parent_id=None` (they're project-level
    # line-like labels). Differentiate by parent_id alone: parent=None +
    # kind=line + appearing in the existing adhoc list previously means
    # adhoc; we can't tell from the model alone, but we don't need to —
    # the brief tells us which is which on each build.
    # So: when building from the brief, we re-derive adhocs fresh and look
    # up by name against any existing tag with kind=line and parent_id=None.
    for tid, tag in existing_tags.items():
        if tag.kind == "section" and tag.parent_id is None:
            sections_by_name[tag.name] = tid
        elif tag.kind == "tag":
            adhoc_by_name[tag.name] = tid

    # Pre-compute occurrence indexes for existing lines so we can match
    # the new brief's lines against them. Lines can be top-level
    # (parent_id=None — the v0 default) or grouped under a section.
    existing_line_occurrence: dict[tuple[str, str], list[str]] = {}
    for tid, tag in existing_tags.items():
        if tag.kind == "line":
            parent_name = (
                section_id_to_name.get(tag.parent_id, "<unknown>")
                if tag.parent_id is not None
                else ""
            )
            existing_line_occurrence.setdefault(
                (parent_name, tag.name), []
            ).append(tid)

    # Sort each occurrence list by the existing tag's order_idx so the
    # first occurrence of a name within a section maps to that section's
    # earliest tag.
    for key, ids in existing_line_occurrence.items():
        ids.sort(key=lambda t: existing_tags[t].order_idx)
        lines_by_identity_index = 0
        for tid in ids:
            lines_by_identity[(key[0], key[1], lines_by_identity_index)] = tid
            lines_by_identity_index += 1

    # Allocate IDs not present in existing_tags. The `next_tag_id` helper
    # keeps allocating bigger numbers; we never reuse a freed slot to
    # avoid cross-edit ID collisions.
    used_ids = set(existing_tags.keys())

    new_tags: dict[str, ProjectTag] = {}

    # 1. Build sections in brief order.
    section_name_to_id: dict[str, str] = {}
    for order, section_name in enumerate(parsed.sections):
        existing_id = sections_by_name.get(section_name)
        if existing_id is not None:
            tag_id = existing_id
        else:
            tag_id = _next_tag_id(used_ids)
            used_ids.add(tag_id)
        new_tags[tag_id] = ProjectTag(
            kind="section",
            name=section_name,
            parent_id=None,
            order_idx=order,
        )
        section_name_to_id[section_name] = tag_id

    # 2. Build script lines. Without a sections-to-lines mapping in the
    # brief, every script line is project-level (parent_id=None) for v0.
    # The hierarchy "lines belong to sections" lands as a brief-format
    # extension in a future phase; for now sections are just labels and
    # lines hang flat off the project.
    #
    # Track occurrence per (parent, text) tuple to match duplicates.
    if parsed.script is not None:
        occurrence_seen: dict[tuple[str, str], int] = {}
        for order, line_text in enumerate(parsed.script.lines):
            parent_name = ""  # v0: no section parents for lines
            parent_id = None
            key_count = occurrence_seen.get((parent_name, line_text), 0)
            occurrence_seen[(parent_name, line_text)] = key_count + 1
            identity = (parent_name, line_text, key_count)
            existing_id = lines_by_identity.get(identity)
            if existing_id is not None:
                tag_id = existing_id
            else:
                tag_id = _next_tag_id(used_ids)
                used_ids.add(tag_id)
            new_tags[tag_id] = ProjectTag(
                kind="line",
                name=line_text,
                parent_id=parent_id,
                order_idx=order,
            )

    # 3. Build ad-hoc tags from the `tags:` array. Kind="tag" (distinct
    # from script lines, which are kind="line"), parent=None. Name-keyed
    # identity since they're project-level labels.
    for order, tag_name in enumerate(parsed.tags):
        existing_id = adhoc_by_name.get(tag_name)
        if existing_id is not None:
            tag_id = existing_id
        else:
            tag_id = _next_tag_id(used_ids)
            used_ids.add(tag_id)
        new_tags[tag_id] = ProjectTag(
            kind="tag",
            name=tag_name,
            parent_id=None,
            order_idx=order,
        )

    return new_tags


def _full_brief_md(parsed: ParsedBrief, source_text: Optional[str]) -> str:
    """Project.brief_md stores the original text the user typed; if the
    caller passes that via `source_text`, use it. Otherwise reconstruct a
    minimal canonical form."""
    if source_text is not None:
        return source_text
    # Minimal reconstruction — only used when we don't have the source.
    lines = ["---", f"name: {parsed.name}"]
    if parsed.script is not None and parsed.script.lines:
        lines.append("script:")
        for line in parsed.script.lines:
            lines.append(f"  - {line}")
    if parsed.sections:
        lines.append("sections:")
        for s in parsed.sections:
            lines.append(f"  - {s}")
    if parsed.tags:
        lines.append("tags:")
        for t in parsed.tags:
            lines.append(f"  - {t}")
    lines.append("---")
    if parsed.body_md:
        lines.append("")
        lines.append(parsed.body_md.rstrip("\n"))
    return "\n".join(lines) + "\n"


# ---------- Read-side -----------------------------------------------------------


class ProjectSummary(StrictModel):
    project_id: str
    name: str
    created_at: str
    line_count: int
    section_count: int
    tag_count: int


def list_projects(state: ClipFarmState) -> list[ProjectSummary]:
    out: list[ProjectSummary] = []
    for pid, proj in state.projects.items():
        section_count = sum(
            1 for t in proj.tags.values()
            if t.kind == "section" and t.parent_id is None
        )
        line_count = sum(
            1 for t in proj.tags.values() if t.kind == "line"
        )
        adhoc_count = sum(
            1 for t in proj.tags.values() if t.kind == "tag"
        )
        out.append(
            ProjectSummary(
                project_id=pid,
                name=proj.name,
                created_at=proj.created_at,
                line_count=line_count,
                section_count=section_count,
                tag_count=line_count + section_count + adhoc_count,
            )
        )
    out.sort(key=lambda p: p.created_at)
    return out


# ---------- Mutating ops ------------------------------------------------------


def create_project(
    state: ClipFarmState,
    parsed: ParsedBrief,
    *,
    brief_md_source: Optional[str] = None,
) -> str:
    """Allocate a new project_id, build Project + ProjectTag entries from
    the parsed brief, mutate state. Returns the new project_id."""
    pid = _next_project_id(state)
    tags = _build_tags_from_brief(parsed)
    state.projects[pid] = Project(
        name=parsed.name,
        brief_md=_full_brief_md(parsed, brief_md_source),
        script=parsed.script,
        tags=tags,
        created_at=_now(),
    )
    return pid


def update_project(
    state: ClipFarmState,
    project_id: str,
    parsed: ParsedBrief,
    *,
    brief_md_source: Optional[str] = None,
) -> int:
    """Re-build the project's tags using the name-keyed merge. Existing
    tag IDs whose identity survives in the new brief are preserved;
    tag IDs that disappeared become dangling tombstones (their
    `clip_project_tags` rows are flipped to `stale: true` with
    `project_tag_id` left as the dangling reference). Every remaining
    `clip_project_tags` row pointing at this project is also flipped to
    `stale: true` — the spec's "explicit retag after brief edit" rule.

    Returns the count of `clip_project_tags` rows flipped to stale (which
    is every row pointing at this project_id, since brief edits stale
    them all).
    """
    proj = state.projects.get(project_id)
    if proj is None:
        raise KeyError(f"unknown project_id: {project_id}")

    new_tags = _build_tags_from_brief(parsed, existing_tags=proj.tags)
    proj.name = parsed.name
    proj.brief_md = _full_brief_md(parsed, brief_md_source)
    proj.script = parsed.script
    proj.tags = new_tags

    # Every tag row for this project is now stale (brief edit invalidates
    # prior tagging). Rows whose project_tag_id no longer exists become
    # dangling tombstones — left as-is, the next "Retag" run (Phase 6)
    # rewrites them. We don't remove them here.
    staled = 0
    for row in state.clip_project_tags:
        if row.project_id == project_id and not row.stale:
            row.stale = True
            staled += 1
    return staled


def delete_project(state: ClipFarmState, project_id: str) -> tuple[int, int]:
    """Remove the project + every `clip_project_tags` row referencing it
    + every `Attempt` for the project. Returns
    `(dropped_tag_rows, deleted_attempts)`.

    Hard-deletes attempts (Phase 5 decision — no live attempt data
    exists yet; snapshot is the safety net). Raises `KeyError` if
    project not found.
    """
    if project_id not in state.projects:
        raise KeyError(f"unknown project_id: {project_id}")

    # Drop tag rows.
    before_tags = len(state.clip_project_tags)
    state.clip_project_tags = [
        r for r in state.clip_project_tags if r.project_id != project_id
    ]
    dropped = before_tags - len(state.clip_project_tags)

    # Hard-delete attempts.
    attempts_to_delete = [
        aid for aid, a in state.attempts.items() if a.project_id == project_id
    ]
    for aid in attempts_to_delete:
        del state.attempts[aid]

    # Drop the project itself.
    del state.projects[project_id]
    return dropped, len(attempts_to_delete)


__all__ = [
    "ProjectSummary",
    "create_project",
    "delete_project",
    "list_projects",
    "update_project",
]
