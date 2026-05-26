"""Tests for the settings routes — GET/PATCH/POST/DELETE."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """TestClient with isolated state + settings paths."""
    state_path = tmp_path / "clipfarm.json"
    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))
    monkeypatch.setenv("CLIPFARM_SETTINGS_PATH", str(settings_path))
    from clipfarm.app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        c.settings_path = settings_path
        yield c


def test_get_returns_defaults_on_fresh_state(client: TestClient):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["tagging"]["provider"] == "ollama"
    assert body["tagging"]["anthropic_api_key_set"] is False
    # The model options for the UI dropdown.
    assert "claude-sonnet-4-6" in body["anthropic_model_options"]


def test_get_never_returns_raw_api_key(client: TestClient, tmp_path: Path):
    """Even when a key IS set on disk, GET returns the masked indicator
    only — never the raw value."""
    # Write a settings file with a key directly.
    from clipfarm.settings import Settings, TaggingSettings, save_settings
    save_settings(Settings(tagging=TaggingSettings(
        provider="anthropic", anthropic_api_key="sk-ant-SECRET",
    )))

    r = client.get("/api/settings")
    body = r.json()
    assert body["tagging"]["anthropic_api_key_set"] is True
    # Raw key must NOT appear anywhere in the response.
    assert "sk-ant-SECRET" not in r.text


def test_patch_provider_to_anthropic(client: TestClient):
    r = client.patch("/api/settings", json={"provider": "anthropic"})
    assert r.status_code == 200
    assert r.json()["tagging"]["provider"] == "anthropic"
    # Persisted.
    r2 = client.get("/api/settings")
    assert r2.json()["tagging"]["provider"] == "anthropic"


def test_patch_provider_invalid_value_rejected(client: TestClient):
    r = client.patch("/api/settings", json={"provider": "openai"})
    assert r.status_code == 422  # pydantic validation


def test_patch_models(client: TestClient):
    r = client.patch("/api/settings", json={
        "ollama_model": "qwen2.5:14b",
        "anthropic_model": "claude-haiku-4-5-20251001",
    })
    body = r.json()
    assert body["tagging"]["ollama_model"] == "qwen2.5:14b"
    assert body["tagging"]["anthropic_model"] == "claude-haiku-4-5-20251001"


def test_set_anthropic_key_with_test_success(client: TestClient):
    """test=True → ping_anthropic is called; on success, key is saved."""
    with patch(
        "clipfarm.routes.settings.ping_anthropic", return_value=True,
    ) as mock_ping:
        r = client.post("/api/settings/anthropic-key", json={
            "api_key": "sk-ant-good",
            "test": True,
        })
    assert r.status_code == 200
    assert r.json()["tagging"]["anthropic_api_key_set"] is True
    mock_ping.assert_called_once()


def test_set_anthropic_key_with_test_failure_does_not_persist(
    client: TestClient,
):
    """test=True → ping_anthropic returns False → 400, key NOT saved."""
    with patch(
        "clipfarm.routes.settings.ping_anthropic", return_value=False,
    ):
        r = client.post("/api/settings/anthropic-key", json={
            "api_key": "sk-ant-bad",
            "test": True,
        })
    assert r.status_code == 400
    assert "test failed" in r.json()["detail"]
    # Key was not persisted.
    r2 = client.get("/api/settings")
    assert r2.json()["tagging"]["anthropic_api_key_set"] is False


def test_set_anthropic_key_without_test_skips_validation(
    client: TestClient,
):
    """test=False → ping_anthropic NOT called; key saved blindly. The
    UI's 'set without test' affordance uses this when the user knows
    the key is good but doesn't want to spend a test call."""
    with patch(
        "clipfarm.routes.settings.ping_anthropic", return_value=False,
    ) as mock_ping:
        r = client.post("/api/settings/anthropic-key", json={
            "api_key": "sk-ant-untested",
            "test": False,
        })
    assert r.status_code == 200
    mock_ping.assert_not_called()


def test_set_anthropic_key_empty_rejected(client: TestClient):
    """Pydantic min_length=1 + post-strip check."""
    r = client.post("/api/settings/anthropic-key", json={
        "api_key": "",
        "test": False,
    })
    assert r.status_code == 422


def test_delete_anthropic_key(client: TestClient):
    """Set then delete; flag drops to False."""
    with patch(
        "clipfarm.routes.settings.ping_anthropic", return_value=True,
    ):
        client.post("/api/settings/anthropic-key", json={
            "api_key": "sk-ant-good", "test": True,
        })
    r = client.delete("/api/settings/anthropic-key")
    assert r.status_code == 200
    assert r.json()["tagging"]["anthropic_api_key_set"] is False


def test_tagging_route_400_when_anthropic_selected_but_no_key(
    client: TestClient, tmp_path: Path,
):
    """Provider=anthropic + no key + tag clips → clear 400."""
    # Switch to anthropic without setting a key.
    client.patch("/api/settings", json={"provider": "anthropic"})
    # Seed a project + clips minimally.
    from datetime import datetime, timezone
    from clipfarm.models import (
        Clip, ClipFarmState, Project, ProjectTag, Script, Source,
    )
    state: ClipFarmState = client.app.state.clipfarm
    state.sources["s1"] = Source(
        filename="x.mov", path="/x.mov",
        added_at=datetime.now(timezone.utc).isoformat(),
        unavailable=True,
    )
    state.clips["c0"] = Clip(
        source_id="s1", start_sec=0, end_sec=1,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    state.projects["p1"] = Project(
        name="x", script=Script(lines=["a"]),
        tags={"L0": ProjectTag(kind="line", name="a", order_idx=0)},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    r = client.post("/api/projects/p1/tag")
    assert r.status_code == 400
    assert "no API key" in r.json()["detail"]
