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
