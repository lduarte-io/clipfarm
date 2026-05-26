"""Tests for GET /api/sources/{id}/video — Range-aware streaming."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "clipfarm.json"
    monkeypatch.setenv("CLIPFARM_STATE_PATH", str(state_path))
    from clipfarm.app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


def _make_source(
    client: TestClient,
    tmp_path: Path,
    *,
    stem: str = "alpha",
    ext: str = ".mov",
    content: bytes = b"0123456789" * 1024,  # 10 KB of predictable bytes
    unavailable: bool = False,
    source_id: str = "s1",
) -> tuple[str, Path]:
    """Write a real file on disk + register the source in app.state."""
    media_path = tmp_path / f"{stem}{ext}"
    media_path.write_bytes(content)

    from clipfarm.models import ClipFarmState, Source
    state: ClipFarmState = client.app.state.clipfarm
    state.sources[source_id] = Source(
        filename=f"{stem}{ext}",
        path=str(media_path),
        duration_sec=None,
        added_at=_now(),
        unavailable=unavailable,
    )
    return source_id, media_path


# ─────────────────────────────────────────────────────────────────────────────
# 200 / Accept-Ranges
# ─────────────────────────────────────────────────────────────────────────────


def test_video_200_full_response_with_accept_ranges(client, tmp_path):
    sid, p = _make_source(client, tmp_path, content=b"x" * 5000)
    r = client.get(f"/api/sources/{sid}/video")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"
    assert int(r.headers["content-length"]) == 5000
    assert r.content == b"x" * 5000


# ─────────────────────────────────────────────────────────────────────────────
# 206 / Range forms supported
# ─────────────────────────────────────────────────────────────────────────────


def test_video_206_closed_range_returns_correct_slice(client, tmp_path):
    # 10-byte payload "0123456789"
    sid, p = _make_source(client, tmp_path, content=b"0123456789")
    r = client.get(
        f"/api/sources/{sid}/video",
        headers={"Range": "bytes=2-5"},
    )
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 2-5/10"
    assert int(r.headers["content-length"]) == 4
    assert r.content == b"2345"


def test_video_206_open_ended_range_returns_to_eof(client, tmp_path):
    """bytes=N- form — browsers actually use this for video streaming."""
    sid, p = _make_source(client, tmp_path, content=b"0123456789")
    r = client.get(
        f"/api/sources/{sid}/video",
        headers={"Range": "bytes=7-"},
    )
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 7-9/10"
    assert r.content == b"789"


# ─────────────────────────────────────────────────────────────────────────────
# 416 / Range forms rejected
# ─────────────────────────────────────────────────────────────────────────────


def test_video_416_suffix_range_rejected(client, tmp_path):
    """bytes=-N (suffix range) is rejected — plan-review #3 decision."""
    sid, _ = _make_source(client, tmp_path, content=b"0123456789")
    r = client.get(
        f"/api/sources/{sid}/video",
        headers={"Range": "bytes=-3"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */10"


def test_video_416_multi_range_rejected(client, tmp_path):
    """Multi-range (bytes=N-M,N-M) rejected — browsers don't ask for it."""
    sid, _ = _make_source(client, tmp_path, content=b"0123456789")
    r = client.get(
        f"/api/sources/{sid}/video",
        headers={"Range": "bytes=0-2,5-7"},
    )
    assert r.status_code == 416


def test_video_416_past_eof(client, tmp_path):
    """Range past EOF → 416 with `Content-Range: bytes */total`."""
    sid, _ = _make_source(client, tmp_path, content=b"0123456789")  # 10 bytes
    r = client.get(
        f"/api/sources/{sid}/video",
        headers={"Range": "bytes=100-200"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */10"


# ─────────────────────────────────────────────────────────────────────────────
# 404 / 410
# ─────────────────────────────────────────────────────────────────────────────


def test_video_404_unknown_source(client):
    r = client.get("/api/sources/missing/video")
    assert r.status_code == 404


def test_video_410_unavailable_source(client, tmp_path):
    sid, _ = _make_source(client, tmp_path, unavailable=True)
    r = client.get(f"/api/sources/{sid}/video")
    assert r.status_code == 410
    assert "unavailable" in r.text


# ─────────────────────────────────────────────────────────────────────────────
# Content-Type derivation per extension
# ─────────────────────────────────────────────────────────────────────────────


def test_video_content_type_mkv(client, tmp_path):
    sid, _ = _make_source(client, tmp_path, ext=".mkv")
    r = client.get(f"/api/sources/{sid}/video")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/x-matroska"


def test_video_content_type_mp4(client, tmp_path):
    sid, _ = _make_source(client, tmp_path, ext=".mp4")
    r = client.get(f"/api/sources/{sid}/video")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
