"""Schema migrations for `clipfarm.json`.

Each version bump gets its own module (`vN_to_vN+1.py`) exporting a `migrate(d:
dict) -> dict` function that mutates the raw dict in place (or returns a new
one) before Pydantic validation. The runner here applies them in order.

The point of the scaffolding existing at v0 is that adding a real migration
later is a known-shape operation — write the file, bump CURRENT_VERSION, done.
No retrofitting the mechanism in a hurry when we actually need it.
"""
from __future__ import annotations

from importlib import import_module
from typing import Callable

CURRENT_VERSION = 1

# Ordered list of (from_version, module_name). To add a migration:
#   1. Bump CURRENT_VERSION
#   2. Add ("vN_to_vN+1") to this list
#   3. Implement migrate() in the module
_MIGRATIONS: list[tuple[int, str]] = [
    # (1, "v1_to_v2"),   # placeholder — uncomment when bumping to v2
]


def _load_migration(module_name: str) -> Callable[[dict], dict]:
    mod = import_module(f"clipfarm.migrations.{module_name}")
    return mod.migrate  # type: ignore[no-any-return]


def needs_migration(file_version: int) -> bool:
    return file_version < CURRENT_VERSION


def run_migrations(state_dict: dict) -> dict:
    """Apply all migrations needed to bring `state_dict` to CURRENT_VERSION.

    Returns the migrated dict (which the caller should then pass through
    `ClipFarmState.model_validate`). Idempotent — calling on a current-version
    dict is a no-op.
    """
    version = int(state_dict.get("version", 1))
    if version > CURRENT_VERSION:
        raise ValueError(
            f"clipfarm.json reports version={version} but this app supports "
            f"up to version={CURRENT_VERSION}. Refusing to downgrade."
        )
    for from_version, module_name in _MIGRATIONS:
        if version == from_version:
            migrate_fn = _load_migration(module_name)
            state_dict = migrate_fn(state_dict)
            version = from_version + 1
            state_dict["version"] = version
    return state_dict
