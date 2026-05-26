"""Settings routes — provider toggle + API key management.

- `GET  /api/settings`               → current settings (API key MASKED).
- `PATCH /api/settings`              → update provider / models.
- `POST /api/settings/anthropic-key` → set the Anthropic API key. Body
                                       `{api_key: ..., test: bool}`.
                                       When `test=True`, validates the
                                       key by making a tiny test call
                                       before saving.
- `DELETE /api/settings/anthropic-key` → clear the API key.

The API key is NEVER returned by GET — only an `anthropic_api_key_set:
bool` indicator so the UI can show "key is set / not set."
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import Field

from clipfarm.llm_anthropic import ping_anthropic
from clipfarm.models import StrictModel
from clipfarm.settings import (
    ANTHROPIC_MODEL_OPTIONS,
    Settings,
    TaggingProvider,
    load_settings,
    save_settings,
)

log = logging.getLogger("clipfarm.routes.settings")

router = APIRouter(prefix="/api", tags=["settings"])


class TaggingSettingsView(StrictModel):
    """Wire shape for GET /api/settings. `anthropic_api_key_set`
    replaces the raw key so the secret never leaves the server."""

    provider: TaggingProvider
    ollama_model: str
    anthropic_model: str
    anthropic_api_key_set: bool


class SettingsView(StrictModel):
    version: int
    tagging: TaggingSettingsView
    # Static info the UI uses to render its model dropdown.
    anthropic_model_options: list[str] = Field(default_factory=list)


class TaggingSettingsPatch(StrictModel):
    """Partial update — fields the user can change on the Settings page.
    All optional; only set fields are applied."""

    provider: Optional[TaggingProvider] = None
    ollama_model: Optional[str] = None
    anthropic_model: Optional[str] = None


class AnthropicKeyBody(StrictModel):
    api_key: str = Field(..., min_length=1)
    # When true, validate the key with a real (tiny) test call before
    # saving. UI's "Set + test" button uses this; "Set without test"
    # button passes false.
    test: bool = True


def _to_view(settings: Settings) -> SettingsView:
    return SettingsView(
        version=settings.version,
        tagging=TaggingSettingsView(
            provider=settings.tagging.provider,
            ollama_model=settings.tagging.ollama_model,
            anthropic_model=settings.tagging.anthropic_model,
            anthropic_api_key_set=bool(settings.tagging.anthropic_api_key),
        ),
        anthropic_model_options=list(ANTHROPIC_MODEL_OPTIONS),
    )


@router.get("/settings", response_model=SettingsView)
def get_settings_route() -> SettingsView:
    return _to_view(load_settings())


@router.patch("/settings", response_model=SettingsView)
def patch_settings_route(patch: TaggingSettingsPatch) -> SettingsView:
    settings = load_settings()
    if patch.provider is not None:
        settings.tagging.provider = patch.provider
    if patch.ollama_model is not None:
        settings.tagging.ollama_model = patch.ollama_model.strip() or settings.tagging.ollama_model
    if patch.anthropic_model is not None:
        settings.tagging.anthropic_model = patch.anthropic_model.strip() or settings.tagging.anthropic_model

    # If switching to anthropic but no key is set, the request is
    # still allowed — the tagging route will surface a clear 400 when
    # the user tries to actually tag. This lets the UI persist the
    # provider choice before the key is entered, so the workflow is:
    # 1. Pick provider → 2. Enter key.
    save_settings(settings)
    return _to_view(settings)


@router.post("/settings/anthropic-key", response_model=SettingsView)
def set_anthropic_key(body: AnthropicKeyBody) -> SettingsView:
    settings = load_settings()
    new_key = body.api_key.strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="api_key is empty")

    if body.test:
        # Test the key with the CURRENT anthropic_model — fall back to
        # the default if the user hasn't picked one yet.
        model = settings.tagging.anthropic_model
        ok, err = ping_anthropic(new_key, model=model)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"API key test failed for model {model!r}: "
                    f"{err or 'unknown error'}. Key was not saved."
                ),
            )

    settings.tagging.anthropic_api_key = new_key
    save_settings(settings)
    log.info("settings: anthropic API key updated (tested=%s)", body.test)
    return _to_view(settings)


@router.delete("/settings/anthropic-key", response_model=SettingsView)
def clear_anthropic_key() -> SettingsView:
    settings = load_settings()
    settings.tagging.anthropic_api_key = None
    save_settings(settings)
    log.info("settings: anthropic API key cleared")
    return _to_view(settings)
