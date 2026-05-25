"""Placeholder migration. Empty by design at v0.

When CURRENT_VERSION bumps to 2, fill `migrate()` with whatever transforms the
v1 dict into the v2 shape and add `(1, "v1_to_v2")` to the `_MIGRATIONS` list
in `clipfarm/migrations/__init__.py`.
"""
from __future__ import annotations


def migrate(state_dict: dict) -> dict:
    return state_dict
