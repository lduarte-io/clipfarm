"""Tests for `GET /api/sources/{source_id}/transcript`."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipfarm.transcripts import cache


def _write_pair(folder: Path, stem: str, *, with_transcript: bool = True) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    if with_transcript:
        payload = {
            "schema_version": 1,
            "duration": 5.0,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 1.0,
                    "words": [
                        {"start": 0.0, "end": 0.4, "word": " hello"},
                        {"start": 0.5, "end": 0.9, "word": " world"},
                    ],
                },
                {
                    "id": 1,
                    "start": 3.0,
                    "end": 4.0,
                    "words": [
                        {"start": 3.0, "end": 3.5, "word": " second"},
                        {"start": 3.6, "end": 4.0, "word": " clip"},
                    ],
                },
            ],
        }
        (folder / f"{stem}.whisper.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))
    cache().clear()

    from clipfarm.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        c.state_path = state_path
        yield c
    cache().clear()


def _ingest(client, folder: Path):
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 5.0},
    ):
        r = client.post("/api/ingest", json={"folder": str(folder)})
    assert r.status_code == 200, r.text
    return r.json()


def test_transcript_happy_path(client, tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")
    _ingest(client, folder)

    # Find the source ID for alpha.mov.
    state = client.get("/api/state").json()
    [(sid, _)] = [(k, s) for k, s in state["sources"].items() if s["filename"] == "alpha.mov"]

    r = client.get(f"/api/sources/{sid}/transcript")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == sid
    assert body["filename"] == "alpha.mov"
    assert body["duration_sec"] == 5.0
    assert len(body["segments"]) == 2
    assert body["segments"][0]["words"][0]["word"] == " hello"
    # Two clips detected (silence gap between segments).
    assert len(body["clips"]) == 2
    assert body["clips"][0]["start_sec"] <= body["clips"][1]["start_sec"]


def test_transcript_404_for_unknown_source(client):
    r = client.get("/api/sources/9999/transcript")
    assert r.status_code == 404


def test_transcript_422_for_footage_only(client, tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "no_sidecar", with_transcript=False)
    _ingest(client, folder)
    state = client.get("/api/state").json()
    [(sid, _)] = state["sources"].items()
    r = client.get(f"/api/sources/{sid}/transcript")
    assert r.status_code == 422
    assert "no transcript" in r.json()["detail"]


def test_transcript_500_when_sidecar_disappears(client, tmp_path):
    """State knows about the sidecar but the file is gone. Surface as 500
    (a state-vs-disk drift, not a user input problem)."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")
    _ingest(client, folder)
    # Delete the sidecar on disk.
    (folder / "alpha.whisper.json").unlink()
    cache().clear()  # purge the cached parse

    state = client.get("/api/state").json()
    [(sid, _)] = state["sources"].items()
    r = client.get(f"/api/sources/{sid}/transcript")
    assert r.status_code == 500


def test_transcript_clips_sorted_by_start_sec(client, tmp_path):
    """If state.clips happens to be in non-sorted insertion order, the
    response should still come out sorted."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")
    _ingest(client, folder)

    r = client.get(
        f"/api/sources/{list(client.get('/api/state').json()['sources'].keys())[0]}/transcript"
    )
    clips = r.json()["clips"]
    starts = [c["start_sec"] for c in clips]
    assert starts == sorted(starts)


def test_transcript_response_drops_probability(client, tmp_path):
    """P3.1 #3: payload trim — `probability` is on the validated
    WhisperTranscript model but stripped from the route response. The
    frontend doesn't use it; including it adds ~50% to a 4700-word
    transcript payload for no benefit."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha")
    _ingest(client, folder)

    sid = next(iter(client.get("/api/state").json()["sources"].keys()))
    r = client.get(f"/api/sources/{sid}/transcript")
    assert r.status_code == 200
    body = r.json()
    assert body["segments"], "expected at least one segment"
    for seg in body["segments"]:
        for w in seg["words"]:
            assert "probability" not in w, (
                f"`probability` leaked into the trimmed response payload "
                f"(word={w!r}). See clipfarm/routes/transcripts.py — "
                f"WhisperWordLite must not include it."
            )
            # Sanity: the fields we DO want are still there.
            assert "start" in w and "end" in w and "word" in w
