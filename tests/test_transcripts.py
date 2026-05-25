"""Tests for `clipfarm/transcripts.py` — sidecar loading + LRU cache."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from clipfarm.models import Source
from clipfarm.transcripts import cache, load_transcript_for_source


@pytest.fixture(autouse=True)
def _clean_cache():
    cache().clear()
    yield
    cache().clear()


def _src(path: Optional[Path], filename: str = "x.mov") -> Source:
    return Source(
        filename=filename,
        path=f"/fake/{filename}",
        transcript_path=str(path) if path else None,
        added_at=datetime.now(timezone.utc).isoformat(),
    )


# Import after Source defined to avoid forward-ref headaches.
from typing import Optional  # noqa: E402


def _write_sidecar(folder: Path, name: str, words: list[tuple[float, float, str]]) -> Path:
    payload = {
        "schema_version": 1,
        "duration": 5.0,
        "segments": [
            {
                "id": 0,
                "start": words[0][0] if words else 0.0,
                "end": words[-1][1] if words else 0.0,
                "words": [{"start": s, "end": e, "word": w} for (s, e, w) in words],
            }
        ]
        if words
        else [],
    }
    path = folder / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_returns_parsed_transcript(tmp_path: Path):
    sidecar = _write_sidecar(
        tmp_path, "a.whisper.json", [(0.0, 0.5, " hi"), (0.6, 1.0, " there")]
    )
    src = _src(sidecar)
    transcript = load_transcript_for_source(src)
    assert transcript is not None
    assert transcript.schema_version == 1
    assert transcript.segments[0].words[0].word == " hi"


def test_returns_none_when_transcript_path_is_none():
    src = _src(None)
    assert load_transcript_for_source(src) is None


def test_returns_none_when_file_missing(tmp_path: Path):
    src = _src(tmp_path / "does-not-exist.json")
    assert load_transcript_for_source(src) is None


def test_returns_none_when_malformed(tmp_path: Path):
    bad = tmp_path / "bad.whisper.json"
    bad.write_text("not json {{", encoding="utf-8")
    src = _src(bad)
    assert load_transcript_for_source(src) is None


def test_cache_hit_does_not_re_read_disk(tmp_path: Path):
    sidecar = _write_sidecar(tmp_path, "a.whisper.json", [(0.0, 0.5, " hi")])
    src = _src(sidecar)

    # First call populates the cache.
    first = load_transcript_for_source(src)
    assert first is not None
    assert cache().size() == 1

    # Second call must not touch read_text. Patch it to raise so a re-read
    # would fail the test loudly.
    with patch(
        "clipfarm.transcripts.Path.read_text",
        side_effect=AssertionError("re-read on cache hit"),
    ):
        second = load_transcript_for_source(src)
    assert second is not None
    assert second.model_dump() == first.model_dump()


def test_cache_invalidates_on_mtime_change(tmp_path: Path):
    sidecar = _write_sidecar(tmp_path, "a.whisper.json", [(0.0, 0.5, " hi")])
    src = _src(sidecar)
    first = load_transcript_for_source(src)
    assert first is not None
    assert first.segments[0].words[0].word == " hi"

    # Rewrite with different content + bump the mtime.
    time.sleep(0.01)  # mtime resolution paranoia on some filesystems
    new_mtime = os.path.getmtime(sidecar) + 1
    _write_sidecar(tmp_path, "a.whisper.json", [(0.0, 0.5, " bye")])
    os.utime(sidecar, (new_mtime, new_mtime))

    second = load_transcript_for_source(src)
    assert second is not None
    assert second.segments[0].words[0].word == " bye"
    # And the stale (old-mtime) entry got evicted, not kept alongside.
    assert cache().size() == 1


def test_cache_cap_evicts_oldest(tmp_path: Path):
    """Loading more than cap sidecars caps the cache at exactly cap. After
    eviction, the oldest must miss — verify by clearing the cache, loading
    one specific file, evicting it via cap+1 other loads, and checking that
    the original key is no longer present."""
    from clipfarm import transcripts

    cap = transcripts._CACHE_CAP
    paths = []
    for i in range(cap + 5):
        p = _write_sidecar(tmp_path, f"s{i}.whisper.json", [(0.0, 0.1, f" w{i}")])
        paths.append(p)
        load_transcript_for_source(_src(p, filename=f"s{i}.mov"))

    assert cache().size() == cap

    # The oldest one (paths[0]) should have been evicted. Confirm by direct
    # lookup against the cache (uses (path, mtime_ns) key).
    import os as _os
    evicted_key_mtime = _os.stat(paths[0]).st_mtime_ns
    assert cache().get(paths[0], evicted_key_mtime) is None
    # The most-recent one should still be present.
    last_mtime = _os.stat(paths[-1]).st_mtime_ns
    assert cache().get(paths[-1], last_mtime) is not None
