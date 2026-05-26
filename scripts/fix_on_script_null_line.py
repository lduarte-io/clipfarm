"""One-shot migration: demote any `on-script` tag rows with
`project_tag_id=None` to `related-but-different`.

Caught during Phase 9 dogfood (2026-05-26): the Phase 6 LLM tagging
sometimes emits on-script + null line_tag_id when it senses content
relevance but can't pin a specific script line. Phase 6.2 patches the
validator to demote these on future runs; this script fixes existing
rows in-place so the user doesn't have to re-run the LLM.

Snapshots the state before mutating per the data-model invariant
("every destructive operation writes a snapshot first").

Usage: `uv run python scripts/fix_on_script_null_line.py [state_path]`
(state_path defaults to ./clipfarm.json)
"""
from __future__ import annotations

import sys
from pathlib import Path

from clipfarm.store import load_state, save_state_with_snapshot_locked


def main() -> int:
    state_path = Path(sys.argv[1] if len(sys.argv) > 1 else "clipfarm.json").resolve()
    if not state_path.exists():
        print(f"error: {state_path} not found", file=sys.stderr)
        return 1

    state = load_state(state_path)

    # Find on-script rows with null line_tag_id.
    affected = [
        r for r in state.clip_project_tags
        if r.category == "on-script" and r.project_tag_id is None
    ]
    if not affected:
        print(f"no rows need migration in {state_path}")
        return 0

    # Group by project for the report.
    by_project: dict[str, int] = {}
    for r in affected:
        by_project[r.project_id] = by_project.get(r.project_id, 0) + 1

    print(f"found {len(affected)} on-script rows with null line_tag_id:")
    for pid, n in sorted(by_project.items()):
        project = state.projects.get(pid)
        name = project.name if project else "(unknown project)"
        print(f"  project {pid} ({name}): {n} rows")
    print()

    # Mutate: demote to related-but-different.
    for r in affected:
        r.category = "related-but-different"

    # Snapshot + write.
    snap_path, _ = save_state_with_snapshot_locked(
        state, state_path, "phase-6.2-fix-on-script-null-line"
    )
    if snap_path is not None:
        print(f"snapshot written: {snap_path}")
    print(f"demoted {len(affected)} rows to related-but-different in {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
