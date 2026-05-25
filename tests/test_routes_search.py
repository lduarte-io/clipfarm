"""Tests for `GET /api/search`."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipfarm.transcripts import cache


def _write_pair(folder: Path, stem: str, words: list[tuple[float, float, str]]) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    payload = {
        "schema_version": 1,
        "duration": 10.0,
        "segments": [
            {
                "id": 0,
                "start": words[0][0],
                "end": words[-1][1],
                "words": [{"start": s, "end": e, "word": w} for (s, e, w) in words],
            }
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


def _ingest_two(client, tmp_path: Path):
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(
        folder,
        "alpha",
        [(0.0, 0.5, " Bitcoin"), (0.6, 1.0, " is"), (1.1, 1.6, " custody")],
    )
    _write_pair(
        folder,
        "beta",
        [(0.0, 0.5, " ethereum"), (0.6, 1.2, " self-custody"), (1.3, 2.0, " rules")],
    )
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 10.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})


def test_search_finds_matches_across_sources(client, tmp_path):
    _ingest_two(client, tmp_path)
    r = client.get("/api/search", params={"q": "custody"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "custody"
    # 'custody' appears in alpha (word ' custody') and beta (' self-custody').
    assert body["total"] == 2
    filenames = sorted(h["filename"] for h in body["hits"])
    assert filenames == ["alpha.mov", "beta.mov"]
    # Every hit carries source_id + timestamp.
    for h in body["hits"]:
        assert h["source_id"]
        assert h["timestamp_sec"] >= 0
        assert h["match"]


def test_search_case_insensitive(client, tmp_path):
    _ingest_two(client, tmp_path)
    lower = client.get("/api/search", params={"q": "bitcoin"})
    upper = client.get("/api/search", params={"q": "BITCOIN"})
    assert lower.json()["total"] == upper.json()["total"] == 1


def test_search_400_on_empty_query(client):
    r = client.get("/api/search", params={"q": ""})
    assert r.status_code == 422 or r.status_code == 400
    r2 = client.get("/api/search", params={"q": "   "})
    assert r2.status_code == 400


def test_search_no_match(client, tmp_path):
    _ingest_two(client, tmp_path)
    r = client.get("/api/search", params={"q": "ethereum-foundation"})
    body = r.json()
    assert body["total"] == 0
    assert body["hits"] == []


def test_search_source_id_filter(client, tmp_path):
    _ingest_two(client, tmp_path)
    state = client.get("/api/state").json()
    alpha_sid = [k for k, s in state["sources"].items() if s["filename"] == "alpha.mov"][0]

    r = client.get("/api/search", params={"q": "custody", "source_id": alpha_sid})
    body = r.json()
    assert body["total"] == 1
    assert body["hits"][0]["filename"] == "alpha.mov"


def test_search_unknown_source_id_404(client, tmp_path):
    _ingest_two(client, tmp_path)
    r = client.get("/api/search", params={"q": "anything", "source_id": "9999"})
    assert r.status_code == 404


def test_search_limit_truncates(client, tmp_path):
    _ingest_two(client, tmp_path)
    # ' ' (space) appears inside every word's text. Use it to force lots of hits.
    # But space gets stripped during query.strip() — use 'o' which appears in
    # both 'Bitcoin' and 'custody'.
    r = client.get("/api/search", params={"q": "o", "limit": 1})
    body = r.json()
    assert body["total"] >= 2  # at least Bitcoin + custody both have 'o'
    assert len(body["hits"]) == 1
    assert body["truncated"] is True


def test_search_hit_carries_clip_id_when_inside_a_clip(client, tmp_path):
    _ingest_two(client, tmp_path)
    r = client.get("/api/search", params={"q": "Bitcoin"})
    body = r.json()
    assert body["total"] == 1
    # The hit timestamp should fall inside one of alpha.mov's clips.
    assert body["hits"][0]["clip_id"] is not None
