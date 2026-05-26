"""Spec → "Unknown-key tolerance": `clipfarm.json` is hand-editable, so
unknown keys at any level must be logged and dropped on load, not rejected.
The on-write surface stays clean because writers round-trip through validated
models."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from clipfarm.store import load_state, save_state_sync


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_top_level_unknown_key_loads_and_is_dropped(tmp_path: Path, caplog):
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {},
                "clips": {},
                "projects": {},
                "clip_project_tags": [],
                "attempts": {},
                "voice_annotations": [],
                "_lillian_note": "hi, hand-edited this",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        loaded = load_state(state_path)

    assert loaded.version == 1
    # The dropped key should not show up in the round-tripped state.
    save_state_sync(loaded, state_path)
    reloaded_raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert "_lillian_note" not in reloaded_raw

    # And the loader should have logged a warning naming the key.
    assert any(
        "_lillian_note" in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), f"expected a warning naming '_lillian_note', got: {[r.getMessage() for r in caplog.records]}"


def test_nested_unknown_key_in_source_is_dropped(tmp_path: Path, caplog):
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    "1": {
                        "filename": "ghost.mov",
                        "path": str(tmp_path / "ghost.mov"),
                        "added_at": _now(),
                        "_my_custom_field": 42,
                    }
                },
                "clips": {},
                "projects": {},
                "clip_project_tags": [],
                "attempts": {},
                "voice_annotations": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        loaded = load_state(state_path)

    assert "1" in loaded.sources
    # The nested unknown key is gone after validation.
    dumped = loaded.model_dump()
    assert "_my_custom_field" not in dumped["sources"]["1"]
    # Warning was emitted naming the key.
    assert any(
        "_my_custom_field" in record.getMessage()
        for record in caplog.records
    ), f"expected a warning naming '_my_custom_field', got: {[r.getMessage() for r in caplog.records]}"


# ---------- Phase 5: stress-test the typing-driven walker --------------------


def _state_with_project(extra_keys: dict) -> dict:
    """Build a state with one Project + one ProjectTag, then merge
    `extra_keys` at whichever level the test cares about."""
    project = {
        "name": "p",
        "brief_md": "",
        "script": {"lines": ["a", "b"]},
        "tags": {
            "1": {
                "kind": "section",
                "name": "the hook",
                "parent_id": None,
                "order_idx": 0,
            },
        },
        "created_at": _now(),
    }
    base = {
        "version": 1,
        "sources": {},
        "clips": {},
        "projects": {"1": project},
        "clip_project_tags": [],
        "attempts": {},
        "voice_annotations": [],
    }
    return base


def test_unknown_key_inside_dict_str_projecttag(tmp_path: Path, caplog):
    """`Project.tags: dict[str, ProjectTag]` — unknown key inside a tag
    value gets logged with full dotted path."""
    state = _state_with_project({})
    state["projects"]["1"]["tags"]["1"]["_secret"] = "boom"
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        load_state(state_path)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "projects.1.tags.1._secret" in messages, (
        f"expected projects.1.tags.1._secret path; got: {messages}"
    )


def test_unknown_key_inside_dict_str_project(tmp_path: Path, caplog):
    """Top-level `projects: dict[str, Project]` — unknown key inside a
    project value gets the projects.<id>.<key> path."""
    state = _state_with_project({})
    state["projects"]["1"]["_secret"] = "boom"
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        load_state(state_path)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "projects.1._secret" in messages, (
        f"expected projects.1._secret path; got: {messages}"
    )


def test_unknown_key_inside_script_model(tmp_path: Path, caplog):
    """`Project.script: Optional[Script]` — unknown key inside the Script
    sub-model gets the projects.1.script.<key> path."""
    state = _state_with_project({})
    state["projects"]["1"]["script"]["_secret"] = "boom"
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        load_state(state_path)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "projects.1.script._secret" in messages, (
        f"expected projects.1.script._secret path; got: {messages}"
    )


def test_unknown_key_inside_list_clip_project_tags(tmp_path: Path, caplog):
    """`clip_project_tags: list[ClipProjectTag]` — unknown key inside
    a list element gets clip_project_tags.[0].<key>."""
    state = _state_with_project({})
    state["clip_project_tags"].append(
        {
            "clip_id": "c1",
            "project_id": "1",
            "project_tag_id": "1",
            "category": "on-script",
            "_secret": "boom",
        }
    )
    state_path = tmp_path / "clipfarm.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="clipfarm.store"):
        load_state(state_path)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "clip_project_tags.[0]._secret" in messages, (
        f"expected clip_project_tags.[0]._secret path; got: {messages}"
    )
