"""One-shot: mark every `clip_project_tags` row for a given project as
`stale=True` so the next `Tag clips` run re-evaluates them with the
current prompt + validator.

Phase 5's normal flow marks rows stale when the brief changes. This
script is for the case where the prompt or validator changed (e.g.,
Phase 6.2's on-script-null-line demotion + stricter prompt) and you
want to force a re-tag without editing the brief.

Snapshots first.

Usage: `uv run python -m scripts.mark_project_tags_stale <project_id> [state_path]`
"""
from __future__ import annotations

import sys
from pathlib import Path

from clipfarm.store import load_state, save_state_with_snapshot_locked


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: uv run python -m scripts.mark_project_tags_stale "
            "<project_id> [state_path]",
            file=sys.stderr,
        )
        return 1

    project_id = sys.argv[1]
    state_path = Path(sys.argv[2] if len(sys.argv) > 2 else "clipfarm.json").resolve()
    if not state_path.exists():
        print(f"error: {state_path} not found", file=sys.stderr)
        return 1

    state = load_state(state_path)
    if project_id not in state.projects:
        print(
            f"error: project_id={project_id!r} not in state. "
            f"available: {list(state.projects.keys())}",
            file=sys.stderr,
        )
        return 1

    affected = [
        r for r in state.clip_project_tags
        if r.project_id == project_id and not r.stale
    ]
    if not affected:
        print(f"no non-stale rows to mark for project {project_id}")
        return 0

    for r in affected:
        r.stale = True

    snap_path, _ = save_state_with_snapshot_locked(
        state, state_path, f"mark-stale-project-{project_id}"
    )
    if snap_path is not None:
        print(f"snapshot written: {snap_path}")
    project = state.projects[project_id]
    print(
        f"marked {len(affected)} rows stale for project {project_id} "
        f"({project.name!r}). Click 'Tag clips' on the Brief page to re-tag."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
