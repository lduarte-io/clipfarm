"""Tests for the migrations runner."""
from __future__ import annotations

import pytest

from clipfarm import migrations as m


def test_no_migrations_at_current_version_is_noop():
    d = {"version": m.CURRENT_VERSION, "clips": {}}
    out = m.run_migrations(d)
    assert out["version"] == m.CURRENT_VERSION


def test_needs_migration_helper():
    assert not m.needs_migration(m.CURRENT_VERSION)
    if m.CURRENT_VERSION > 1:
        assert m.needs_migration(1)


def test_future_version_refuses_to_downgrade():
    with pytest.raises(ValueError):
        m.run_migrations({"version": m.CURRENT_VERSION + 99})


def test_chained_migrations_apply_in_order(monkeypatch):
    """Synthesize a migration table and prove it applies in order."""

    calls: list[int] = []

    def make_migrate(label: int):
        def migrate(d):
            calls.append(label)
            d.setdefault("trail", []).append(label)
            return d

        return migrate

    fake_modules = {
        "_fake_1_to_2": type("M", (), {"migrate": make_migrate(2)}),
        "_fake_2_to_3": type("M", (), {"migrate": make_migrate(3)}),
    }
    monkeypatch.setattr(m, "_MIGRATIONS", [(1, "_fake_1_to_2"), (2, "_fake_2_to_3")])
    monkeypatch.setattr(m, "CURRENT_VERSION", 3)
    monkeypatch.setattr(
        m,
        "_load_migration",
        lambda name: fake_modules[name].migrate,
    )

    out = m.run_migrations({"version": 1})
    assert calls == [2, 3]
    assert out["version"] == 3
    assert out["trail"] == [2, 3]
