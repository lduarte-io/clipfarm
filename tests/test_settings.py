"""Tests for `clipfarm/settings.py` — load/save round-trip + defaults."""
from __future__ import annotations

import json
from pathlib import Path

from clipfarm.settings import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OLLAMA_MODEL,
    Settings,
    TaggingSettings,
    load_settings,
    save_settings,
)


def test_load_returns_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(tmp_path / "missing.json"))
    s = load_settings()
    assert s.tagging.provider == "ollama"
    assert s.tagging.ollama_model == DEFAULT_OLLAMA_MODEL
    assert s.tagging.anthropic_model == DEFAULT_ANTHROPIC_MODEL
    assert s.tagging.anthropic_api_key is None


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(p))
    s = Settings(tagging=TaggingSettings(
        provider="anthropic",
        anthropic_api_key="sk-ant-test-key",
        anthropic_model="claude-haiku-4-5-20251001",
    ))
    save_settings(s)
    assert p.exists()
    loaded = load_settings()
    assert loaded.tagging.provider == "anthropic"
    assert loaded.tagging.anthropic_api_key == "sk-ant-test-key"
    assert loaded.tagging.anthropic_model == "claude-haiku-4-5-20251001"


def test_corrupt_file_falls_back_to_defaults_without_raising(
    tmp_path, monkeypatch, caplog,
):
    p = tmp_path / "settings.json"
    p.write_text("not json {", encoding="utf-8")
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(p))
    with caplog.at_level("WARNING", logger="clipfarm.settings"):
        s = load_settings()
    assert s.tagging.provider == "ollama"
    assert any("failed to load" in m for m in caplog.messages)


def test_atomic_write_uses_rename(tmp_path, monkeypatch):
    """The save path should produce a final file with no leftover
    .tmp files in the directory."""
    p = tmp_path / "settings.json"
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(p))
    save_settings(Settings())
    files = list(tmp_path.iterdir())
    assert any(f.name == "settings.json" for f in files)
    assert not any(f.name.startswith(".settings-") for f in files)


def test_save_creates_parent_dir(tmp_path, monkeypatch):
    """If `.clipfarm/` doesn't exist yet, the save path creates it."""
    p = tmp_path / "newdir" / "settings.json"
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(p))
    save_settings(Settings())
    assert p.exists()


def test_secret_persists_to_disk_in_plain_text(tmp_path, monkeypatch):
    """Documenting the on-disk storage contract: the API key IS in the
    file at rest (gitignored). The route layer's GET masks it; the
    file itself is plain JSON."""
    p = tmp_path / "settings.json"
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(p))
    s = Settings(tagging=TaggingSettings(
        provider="anthropic", anthropic_api_key="sk-secret-XYZ",
    ))
    save_settings(s)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["tagging"]["anthropic_api_key"] == "sk-secret-XYZ"
