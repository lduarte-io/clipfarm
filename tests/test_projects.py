"""Tests for `clipfarm/projects.py` — orchestration + name-keyed merge."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clipfarm.brief import parse_brief
from clipfarm.models import (
    Attempt,
    AttemptClip,
    Clip,
    ClipFarmState,
    ClipProjectTag,
    Source,
)
from clipfarm.projects import (
    ProjectSummary,
    create_project,
    delete_project,
    list_projects,
    update_project,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state() -> ClipFarmState:
    return ClipFarmState()


def _parse(text: str):
    return parse_brief(text)


_BRIEF_V1 = """---
name: btc explainer v0.4
script:
  - intro line
  - body line
  - close line
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


_BRIEF_V2_REORDERED = """---
name: btc explainer v0.4
script:
  - close line
  - intro line
  - body line
sections:
  - the why
  - the hook
tags:
  - mistakes
  - hook
---

# What's good

Energy.
"""


_BRIEF_V3_RENAMED_SECTION = """---
name: btc explainer v0.4
script:
  - intro line
  - body line
  - close line
sections:
  - the opening
  - the why
tags:
  - hook
  - mistakes
---
"""


_BRIEF_DUPLICATE_LINES = """---
name: chorus brief
script:
  - hook line
  - the body
  - hook line
  - the body
---
"""


# ---------- create_project ---------------------------------------------------


def test_create_project_allocates_monotonic_ids():
    state = _state()
    p1 = create_project(state, _parse(_BRIEF_V1))
    # Build a slightly different brief for the second project.
    p2 = create_project(
        state,
        _parse(_BRIEF_V1.replace("btc explainer v0.4", "another project")),
    )
    assert p1 == "1"
    assert p2 == "2"


def test_create_project_builds_section_and_line_and_tag_entries():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1))
    proj = state.projects[pid]
    section_tags = [t for t in proj.tags.values() if t.kind == "section"]
    line_tags = [t for t in proj.tags.values() if t.kind == "line"]
    adhoc_tags = [t for t in proj.tags.values() if t.kind == "tag"]
    assert sorted(t.name for t in section_tags) == ["the hook", "the why"]
    assert sorted(t.name for t in line_tags) == [
        "body line",
        "close line",
        "intro line",
    ]
    assert sorted(t.name for t in adhoc_tags) == ["hook", "mistakes"]


def test_create_project_stores_brief_md_source():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1), brief_md_source=_BRIEF_V1)
    assert state.projects[pid].brief_md == _BRIEF_V1


def test_create_project_with_duplicate_lines_keeps_separate_tags():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_DUPLICATE_LINES))
    proj = state.projects[pid]
    hook_tags = [
        t for t in proj.tags.values()
        if t.kind == "line" and t.name == "hook line"
    ]
    body_tags = [
        t for t in proj.tags.values()
        if t.kind == "line" and t.name == "the body"
    ]
    assert len(hook_tags) == 2  # two occurrences
    assert len(body_tags) == 2
    # Each pair has distinct IDs.
    assert {id(t) for t in hook_tags} == {id(hook_tags[0]), id(hook_tags[1])}


# ---------- update_project — name-keyed merge --------------------------------


def test_update_project_reorder_preserves_tag_ids():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1))
    before_ids_by_name = {
        t.name: tid for tid, t in state.projects[pid].tags.items()
    }

    update_project(state, pid, _parse(_BRIEF_V2_REORDERED))
    after_ids_by_name = {
        t.name: tid for tid, t in state.projects[pid].tags.items()
    }

    # Same set of names → same set of IDs (reorder didn't trash any).
    assert set(before_ids_by_name.keys()) == set(after_ids_by_name.keys())
    for name in before_ids_by_name:
        assert before_ids_by_name[name] == after_ids_by_name[name], (
            f"tag '{name}' got a new ID after reorder — should have been preserved"
        )


def test_update_project_renaming_section_creates_new_tag():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1))
    old_section_ids = {
        t.name: tid
        for tid, t in state.projects[pid].tags.items()
        if t.kind == "section"
    }
    assert "the hook" in old_section_ids
    hook_id_before = old_section_ids["the hook"]

    update_project(state, pid, _parse(_BRIEF_V3_RENAMED_SECTION))

    after_section_ids = {
        t.name: tid
        for tid, t in state.projects[pid].tags.items()
        if t.kind == "section"
    }
    # The renamed section "the opening" got a new ID; the old "the hook" is gone.
    assert "the hook" not in after_section_ids
    assert "the opening" in after_section_ids
    assert after_section_ids["the opening"] != hook_id_before


def test_update_project_flips_clip_project_tags_to_stale():
    state = _state()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True
    )
    state.clips["c1"] = Clip(
        source_id="1", start_sec=0.0, end_sec=1.0, created_at=_now()
    )
    pid = create_project(state, _parse(_BRIEF_V1))

    # Manually inject a clip_project_tags row pointing at this project.
    a_line_tag_id = next(
        tid
        for tid, t in state.projects[pid].tags.items()
        if t.kind == "line"
    )
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c1",
            project_id=pid,
            project_tag_id=a_line_tag_id,
            category="on-script",
            stale=False,
        )
    )

    staled = update_project(state, pid, _parse(_BRIEF_V2_REORDERED))
    assert staled == 1
    assert all(
        row.stale
        for row in state.clip_project_tags
        if row.project_id == pid
    )


def test_update_project_dangling_tombstone_for_removed_tag():
    """When a section/line is renamed, its old ProjectTag ID disappears.
    Any clip_project_tags row pointing at it stays in place (dangling
    tombstone) with stale=True. The user's next Retag rewrites it."""
    state = _state()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True
    )
    state.clips["c1"] = Clip(
        source_id="1", start_sec=0.0, end_sec=1.0, created_at=_now()
    )
    pid = create_project(state, _parse(_BRIEF_V1))
    hook_section_id = next(
        tid
        for tid, t in state.projects[pid].tags.items()
        if t.name == "the hook"
    )
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c1",
            project_id=pid,
            project_tag_id=hook_section_id,
            category="on-script",
        )
    )

    update_project(state, pid, _parse(_BRIEF_V3_RENAMED_SECTION))

    # The tag row is still here, still points at the now-vanished tag ID,
    # and is marked stale.
    row = next(
        r for r in state.clip_project_tags if r.project_id == pid
    )
    assert row.project_tag_id == hook_section_id
    assert row.stale is True
    # And the tag itself is gone from project.tags.
    assert hook_section_id not in state.projects[pid].tags


def test_update_project_unknown_id_raises():
    state = _state()
    with pytest.raises(KeyError):
        update_project(state, "999", _parse(_BRIEF_V1))


# ---------- delete_project ---------------------------------------------------


def test_delete_project_removes_state():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1))
    dropped, deleted_attempts = delete_project(state, pid)
    assert pid not in state.projects
    assert dropped == 0
    assert deleted_attempts == 0


def test_delete_project_drops_clip_project_tags():
    state = _state()
    state.sources["1"] = Source(
        filename="x.mov", path="/x.mov", added_at=_now(), unavailable=True
    )
    state.clips["c1"] = Clip(
        source_id="1", start_sec=0.0, end_sec=1.0, created_at=_now()
    )
    pid = create_project(state, _parse(_BRIEF_V1))
    a_line_tag_id = next(
        tid for tid, t in state.projects[pid].tags.items() if t.kind == "line"
    )
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c1",
            project_id=pid,
            project_tag_id=a_line_tag_id,
            category="on-script",
        )
    )
    # Add an unrelated row that shouldn't be touched.
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id="c1",
            project_id="999",  # different project
            project_tag_id="t1",
            category="on-script",
        )
    )

    dropped, _ = delete_project(state, pid)
    assert dropped == 1
    assert all(r.project_id != pid for r in state.clip_project_tags)
    assert any(r.project_id == "999" for r in state.clip_project_tags)


def test_delete_project_hard_deletes_attempts():
    state = _state()
    pid = create_project(state, _parse(_BRIEF_V1))
    state.attempts["a1"] = Attempt(
        project_id=pid, name="x", clips=[], created_at=_now()
    )
    state.attempts["a2"] = Attempt(
        project_id="999", name="y", clips=[], created_at=_now()
    )
    _, deleted = delete_project(state, pid)
    assert deleted == 1
    assert "a1" not in state.attempts
    assert "a2" in state.attempts


def test_delete_project_unknown_raises():
    state = _state()
    with pytest.raises(KeyError):
        delete_project(state, "999")


# ---------- list_projects ----------------------------------------------------


def test_list_projects_returns_summaries():
    state = _state()
    p1 = create_project(state, _parse(_BRIEF_V1))
    summaries = list_projects(state)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.project_id == p1
    assert s.name == "btc explainer v0.4"
    assert s.section_count == 2
    # 3 script lines (kind="line")
    assert s.line_count == 3
    # 2 sections + 3 lines + 2 ad-hoc tags = 7
    assert s.tag_count == 7
