"""Shared pytest fixtures.

The single autouse fixture here isolates the settings file (`.clipfarm/
settings.json`) for every test. Without this, route tests that don't
explicitly set `CLIPFARM_SETTINGS_PATH` would read the developer's real
settings file — which on Lillian's machine has `provider="anthropic"`
plus an API key — and bleed that into tests that expect the Ollama
defaults (e.g. the tagging-route 502 test, premade canned-naming
test). Adding it as autouse ensures complete isolation without every
fixture having to remember to set it.

The `CLIPFARM_STATE_PATH` env var is still set per-test by the fixtures
that need it (because state path also drives the `.clipfarm/`
snapshots dir, which fixtures want to inspect for snapshot-count
assertions).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Point `CLIPFARM_SETTINGS_PATH` at a tmp-path location so the
    developer's real `.clipfarm/settings.json` never leaks into tests.
    Default settings (provider="ollama", no API key) are what tests
    that exercise tagging/premade routes expect."""
    monkeypatch.setenv(
        "CLIPFARM_SETTINGS_PATH", str(tmp_path / "_test_settings.json"),
    )
