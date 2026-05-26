"""User settings — tagging provider (Ollama vs Anthropic), model
selection, API key for Anthropic.

Lives separately from `clipfarm.json` because:
1. `clipfarm.json` is the project data (hand-editable, watched by
   `watchdog`, snapshotted on every mutation). Settings are
   per-machine config, not project data.
2. The API key MUST NOT go into `clipfarm.json` — that file is
   designed for sync / inspection / hand-editing, none of which
   should expose a secret. `.clipfarm/` is already gitignored.

Storage: `.clipfarm/settings.json`. Created on first read if absent
with sensible defaults (provider="ollama", model="llama3.1:8b").

Atomic writes via tmp+rename (same pattern as `store.py`). No
watcher; settings rarely change and the route handler explicitly
loads on each access.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, ValidationError

from clipfarm.models import StrictModel

log = logging.getLogger("clipfarm.settings")

# Default model IDs per CLAUDE.md (Claude 4.X family).
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
# Known good Anthropic models exposed in the Settings UI. Free text
# input is also allowed so the user can experiment with new models
# without a code change.
ANTHROPIC_MODEL_OPTIONS: tuple[str, ...] = (
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-haiku-4-5-20251001",
)

TaggingProvider = Literal["ollama", "anthropic"]


class TaggingSettings(StrictModel):
    """Settings for the tagging + premade-attempt-naming LLM calls.

    `anthropic_api_key` is stored at rest in `.clipfarm/settings.json`
    (gitignored). The settings route NEVER returns it; reads return
    `anthropic_api_key_set: bool` instead so the UI can show a "key
    is set" indicator without round-tripping the secret.
    """

    provider: TaggingProvider = "ollama"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    anthropic_api_key: Optional[str] = None


class Settings(StrictModel):
    """Top-level settings container. Single field today; gives us a
    versionable root if we add more sections later."""

    version: int = 1
    tagging: TaggingSettings = Field(default_factory=TaggingSettings)


_DEFAULT_DIR = Path(".clipfarm")
_DEFAULT_FILENAME = "settings.json"


def _resolve_path(base_dir: Optional[Path] = None) -> Path:
    """Resolve the settings file path. Honors `CLIPFARM_SETTINGS_PATH`
    env override for tests."""
    override = os.environ.get("CLIPFARM_SETTINGS_PATH")
    if override:
        return Path(override).resolve()
    if base_dir is None:
        base_dir = _DEFAULT_DIR
    return (base_dir / _DEFAULT_FILENAME).resolve()


def load_settings(base_dir: Optional[Path] = None) -> Settings:
    """Load settings from disk; return defaults if the file doesn't
    exist or fails to parse. Never raises — settings are
    best-effort; a corrupt file shouldn't break tagging."""
    path = _resolve_path(base_dir)
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        log.warning(
            "settings: failed to load %s (%s) — using defaults", path, e
        )
        return Settings()


def save_settings(settings: Settings, base_dir: Optional[Path] = None) -> None:
    """Atomically write settings to disk. Creates the parent dir if
    needed. Same tmp+rename pattern as `store.save_state`.

    The final file is `chmod 0o600` (owner read/write only) after the
    rename because it may contain the Anthropic API key. Single-user
    laptop risk is approximately zero; the chmod is defensive for the
    "Lillian shares this machine" or "Time Machine backup readable
    by another user" cases. POSIX-only (skipped silently on Windows
    where the call is a no-op anyway).
    """
    path = _resolve_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = settings.model_dump_json(indent=2)
    # tmp file in the same dir so rename is atomic.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError as e:
            # Non-fatal — file is written, just not locked down. Log
            # and continue.
            log.warning("settings: chmod 0o600 failed on %s: %s", path, e)
    except Exception:
        # Best-effort cleanup of the tmp file.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


__all__ = [
    "ANTHROPIC_MODEL_OPTIONS",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_OLLAMA_MODEL",
    "Settings",
    "TaggingProvider",
    "TaggingSettings",
    "load_settings",
    "save_settings",
]
