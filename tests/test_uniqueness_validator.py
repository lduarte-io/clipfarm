"""Tests for the `ClipProjectTag` uniqueness validator activated in Phase 6.

Uniqueness key: `(clip_id, project_id, project_tag_id, category)`. Same
`project_tag_id` with a different `category` is NOT a duplicate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipfarm.models import ClipFarmState, ClipProjectTag
from clipfarm.store import load_state, save_state_sync


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tag(
    *,
    clip_id: str = "c1",
    project_id: str = "p1",
    project_tag_id: str = "t1",
    category: str = "on-script",
) -> ClipProjectTag:
    return ClipProjectTag(
        clip_id=clip_id,
        project_id=project_id,
        project_tag_id=project_tag_id,
        category=category,
    )


# ---------- Construction-time validation ------------------------------------


def test_duplicate_full_key_raises_on_construct():
    with pytest.raises(ValidationError, match="duplicate clip_project_tag"):
        ClipFarmState(
            clip_project_tags=[_tag(), _tag()],
        )


def test_duplicate_with_null_project_tag_id_raises():
    """The validator must treat `None` as a value, not bypass uniqueness."""
    with pytest.raises(ValidationError):
        ClipFarmState(
            clip_project_tags=[
                _tag(project_tag_id=None),
                _tag(project_tag_id=None),
            ],
        )


def test_different_category_same_tag_is_not_duplicate():
    """A clip can be on-script AND standalone-idea for the same line tag.
    Uniqueness is on the full 4-tuple."""
    state = ClipFarmState(
        clip_project_tags=[
            _tag(category="on-script"),
            _tag(category="standalone-idea"),
        ]
    )
    assert len(state.clip_project_tags) == 2


def test_different_clip_same_tag_is_not_duplicate():
    state = ClipFarmState(
        clip_project_tags=[
            _tag(clip_id="c1"),
            _tag(clip_id="c2"),
        ]
    )
    assert len(state.clip_project_tags) == 2


def test_different_project_same_tag_is_not_duplicate():
    """Multi-project tagging — same clip in two projects with the same
    triple is fine because project_id differs."""
    state = ClipFarmState(
        clip_project_tags=[
            _tag(project_id="p1"),
            _tag(project_id="p2"),
        ]
    )
    assert len(state.clip_project_tags) == 2


# ---------- Load-time validation -------------------------------------------


def test_duplicate_on_load_raises(tmp_path: Path):
    """A hand-edited clipfarm.json with duplicate triples must fail to
    load (loud, not silent — the loader is the gatekeeper)."""
    payload = {
        "version": 1,
        "sources": {},
        "clips": {},
        "projects": {},
        "clip_project_tags": [
            {
                "clip_id": "c1", "project_id": "p1",
                "project_tag_id": "t1", "category": "on-script",
            },
            {
                "clip_id": "c1", "project_id": "p1",
                "project_tag_id": "t1", "category": "on-script",
            },
        ],
        "attempts": {},
        "voice_annotations": [],
    }
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate"):
        load_state(state_path)


def test_clean_state_round_trips_through_validator(tmp_path: Path):
    """The validator must NOT reject valid states — sanity check that
    the activation didn't break ordinary use."""
    state = ClipFarmState(
        clip_project_tags=[
            _tag(clip_id="c1"),
            _tag(clip_id="c2"),
            _tag(clip_id="c1", category="standalone-idea"),
        ]
    )
    state_path = tmp_path / "clipfarm.json"
    save_state_sync(state, state_path)
    loaded = load_state(state_path)
    assert len(loaded.clip_project_tags) == 3
