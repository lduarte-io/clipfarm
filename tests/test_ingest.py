"""End-to-end tests for the ingest orchestrator.

We don't depend on a real `ffprobe` here — those zero-byte synthetic `.mov`
files would fail probing anyway, which is the documented fallback path
(fps=None, duration_sec=None). Real-data validation happens in the live
verification step.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from clipfarm.ingest import (
    RESERVED_SEPARATOR,
    ingest_folder,
)
from clipfarm.models import ClipFarmState


def _write_sidecar(
    folder: Path,
    stem: str,
    *,
    schema_version: int = 1,
    duration: Optional[float] = 60.0,
    word_groups: Optional[list[list[tuple[float, float, str]]]] = None,
) -> Path:
    """Write a synthetic `<stem>.whisper.json` to `folder`. `word_groups` is
    a list of segments; each segment is a list of `(start, end, text)` words.
    """
    if word_groups is None:
        # Default: two segments separated by a 3-second silence gap → two
        # clips after segmentation.
        word_groups = [
            [(0.0, 0.5, " hello"), (0.6, 1.0, " world")],
            [(4.0, 4.5, " second"), (4.6, 5.0, " clip")],
        ]
    segments = []
    for i, group in enumerate(word_groups):
        if not group:
            continue
        segments.append(
            {
                "id": i,
                "start": group[0][0],
                "end": group[-1][1],
                "words": [
                    {"start": s, "end": e, "word": w, "probability": 0.9}
                    for (s, e, w) in group
                ],
            }
        )
    payload = {
        "schema_version": schema_version,
        "source_filename": f"{stem}.mov",
        "duration": duration,
        "segments": segments,
    }
    sidecar = folder / f"{stem}.whisper.json"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return sidecar


def _touch_mov(folder: Path, name: str) -> Path:
    """Create an empty placeholder `.mov` file. ffprobe will fail on these,
    which is the documented fallback path."""
    p = folder / name
    p.write_bytes(b"")
    return p


def _stub_ffprobe(fps: Optional[float] = 60.0, duration: Optional[float] = 12.5):
    """Context manager-style patcher for `clipfarm.ingest.probe_video`."""
    return patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": fps, "duration_sec": duration},
    )


def test_happy_path_two_paired_sources(tmp_path: Path):
    _touch_mov(tmp_path, "alpha.mov")
    _write_sidecar(tmp_path, "alpha")
    _touch_mov(tmp_path, "beta.mov")
    _write_sidecar(tmp_path, "beta")

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert sorted(result.sources_added) == ["alpha.mov", "beta.mov"]
    assert result.sources_skipped == []
    assert result.rejected == []
    # Each transcript has two silence-separated segments → 2 clips per source.
    assert result.clips_detected == 4
    assert len(state.sources) == 2
    assert len(state.clips) == 4
    # Every clip is attached to a source that exists.
    source_ids = set(state.sources.keys())
    assert {c.source_id for c in state.clips.values()} <= source_ids
    # fps + duration filled from the stub.
    for src in state.sources.values():
        assert src.fps == 60.0
        # Prefer sidecar duration (60.0) over ffprobe (12.5).
        assert src.duration_sec == 60.0


def test_transcript_less_source_is_footage_only(tmp_path: Path):
    _touch_mov(tmp_path, "no_transcript.mov")
    # No sidecar written.

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert result.sources_added == ["no_transcript.mov"]
    assert result.clips_detected == 0
    assert len(state.sources) == 1
    src = next(iter(state.sources.values()))
    assert src.transcript_path is None
    assert "no sidecar transcript" in " ".join(result.warnings)


def test_reserved_separator_in_filename_rejected(tmp_path: Path):
    _touch_mov(tmp_path, "good.mov")
    _write_sidecar(tmp_path, "good")
    _touch_mov(tmp_path, "bad__file.mov")

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert result.sources_added == ["good.mov"]
    assert len(result.rejected) == 1
    rej = result.rejected[0]
    assert rej.filename == "bad__file.mov"
    assert rej.reason == "filename-contains-__"
    assert rej.sanitized_rename == "bad_file.mov"
    # No partial-state damage — bad file is not in sources.
    filenames = [s.filename for s in state.sources.values()]
    assert "bad__file.mov" not in filenames


def test_schema_version_mismatch_rejects_but_continues(tmp_path: Path):
    _touch_mov(tmp_path, "good.mov")
    _write_sidecar(tmp_path, "good")
    _touch_mov(tmp_path, "from_the_future.mov")
    _write_sidecar(tmp_path, "from_the_future", schema_version=2)

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert "good.mov" in result.sources_added
    assert "from_the_future.mov" in result.sources_added  # still registered as footage-only
    assert any(r.reason == "schema-version-mismatch" for r in result.rejected)


def test_malformed_transcript_rejected_source_still_added_as_footage_only(tmp_path: Path):
    _touch_mov(tmp_path, "broken.mov")
    (tmp_path / "broken.whisper.json").write_text("not valid json{", encoding="utf-8")

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert any(r.reason == "transcript-malformed" for r in result.rejected)
    assert "broken.mov" in result.sources_added
    src = state.sources[next(iter(state.sources.keys()))]
    assert src.transcript_path is None  # transcript was unusable
    # No clips because no usable transcript.
    assert len(state.clips) == 0


def test_re_ingest_is_idempotent(tmp_path: Path):
    _touch_mov(tmp_path, "alpha.mov")
    _write_sidecar(tmp_path, "alpha")

    state = ClipFarmState()
    with _stub_ffprobe():
        ingest_folder(state, tmp_path)
        result2 = ingest_folder(state, tmp_path)

    assert result2.sources_added == []
    assert result2.sources_skipped == ["alpha.mov"]
    assert result2.clips_detected == 0
    # State unchanged on second run.
    assert len(state.sources) == 1
    assert len(state.clips) == 2


def test_transcript_appearing_later_upgrades_source(tmp_path: Path):
    """If a source was ingested without a transcript, then the user runs
    transcribe.py and re-ingests, the source upgrades and clips appear."""
    _touch_mov(tmp_path, "alpha.mov")
    state = ClipFarmState()
    with _stub_ffprobe():
        first = ingest_folder(state, tmp_path)
    assert first.sources_added == ["alpha.mov"]
    assert first.clips_detected == 0

    # Now the sidecar appears.
    _write_sidecar(tmp_path, "alpha")
    with _stub_ffprobe():
        second = ingest_folder(state, tmp_path)
    assert second.sources_updated == ["alpha.mov"]
    assert second.clips_detected == 2
    # Same source ID retained.
    assert len(state.sources) == 1


def test_filenames_with_spaces_and_special_chars_round_trip(tmp_path: Path):
    """Sample folder includes names like `cuddlingchai content.mov`,
    `is my face crooked??.mov`, `more test videos <3.mov`. All must survive."""
    weird_names = [
        "cuddlingchai content.mov",
        "is my face crooked??.mov",
        "more test videos <3.mov",
    ]
    for name in weird_names:
        _touch_mov(tmp_path, name)
        _write_sidecar(tmp_path, Path(name).stem)

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)

    assert sorted(result.sources_added) == sorted(weird_names)
    # Round-trip through JSON.
    payload = state.model_dump(mode="json")
    again = state.model_validate(payload)
    assert sorted(s.filename for s in again.sources.values()) == sorted(weird_names)


def test_double_dunder_in_directory_path_is_fine(tmp_path: Path):
    """Only the filename stem is constrained — directory components with
    `__` are fine."""
    sub = tmp_path / "session__1"
    sub.mkdir()
    _touch_mov(sub, "clean.mov")
    _write_sidecar(sub, "clean")

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, sub)
    assert result.sources_added == ["clean.mov"]
    assert result.rejected == []


def test_dotted_stem_handled_correctly(tmp_path: Path):
    """The real dogfood file is `btc.0.4.mov` — `Path.stem` should give
    `btc.0.4`, the sidecar should be `btc.0.4.whisper.json`."""
    _touch_mov(tmp_path, "btc.0.4.mov")
    _write_sidecar(tmp_path, "btc.0.4")

    state = ClipFarmState()
    with _stub_ffprobe():
        result = ingest_folder(state, tmp_path)
    assert result.sources_added == ["btc.0.4.mov"]
    assert result.clips_detected == 2
    # The encoded clip ID contains the dotted stem + the separator.
    assert any(
        cid.startswith(f"btc.0.4{RESERVED_SEPARATOR}") for cid in state.clips.keys()
    )


def test_not_a_directory_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        ingest_folder(ClipFarmState(), tmp_path / "nope")
