"""Whisper sidecar loading + small in-process LRU cache.

Why a cache at all: the raw-transcript browser (Phase 3) and search both
hit every sidecar. Without a cache, a search across 18 sources would re-read
+ re-validate 18 JSON files per query. The cache turns that into one read
per file per session (or per-mtime-bump), which keeps live-typing search
responsive.

Why mtime in the cache key: if `transcribe.py` re-runs and overwrites a
sidecar, the next search/transcript-load sees the new content without a
server restart. Cheap correctness.

Cache cap: 32 transcripts. Plenty of headroom for the 18-source dogfood
folder. If the library grows, raise this — the size of one transcript is
~250KB max (btc.0.4 is the biggest at ~34min), so 32 × 250KB ≈ 8MB ceiling.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Optional

from pydantic import ValidationError

from clipfarm.models import Source, WhisperTranscript

log = logging.getLogger("clipfarm.transcripts")

_CACHE_CAP = 32


class _TranscriptCache:
    """LRU cache keyed by `(transcript_path, mtime_ns)`. Thread-safe via
    `threading.RLock`; the cache is read from both the asyncio loop and
    (potentially) the watchdog observer thread, so locking is real."""

    def __init__(self, cap: int = _CACHE_CAP) -> None:
        self._cap = cap
        self._lock = RLock()
        self._entries: OrderedDict[tuple[str, int], WhisperTranscript] = OrderedDict()

    def get(self, path: Path, mtime_ns: int) -> Optional[WhisperTranscript]:
        key = (str(path), mtime_ns)
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                return None
            # LRU bump.
            self._entries.move_to_end(key)
            return value

    def put(self, path: Path, mtime_ns: int, transcript: WhisperTranscript) -> None:
        key = (str(path), mtime_ns)
        with self._lock:
            self._entries[key] = transcript
            self._entries.move_to_end(key)
            # Evict oldest entries past the cap. Also evict stale entries for
            # the same path that were keyed by an earlier mtime.
            stale_paths = [
                k for k in self._entries
                if k[0] == str(path) and k != key
            ]
            for k in stale_paths:
                self._entries.pop(k, None)
            while len(self._entries) > self._cap:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Test helper / explicit invalidate."""
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


# Module-level singleton. Tests can call `_cache.clear()` between cases.
_cache = _TranscriptCache()


def cache() -> _TranscriptCache:
    """Exposed so tests can poke at the cache size + clear between runs."""
    return _cache


def load_transcript_for_source(source: Source) -> Optional[WhisperTranscript]:
    """Read + validate the sidecar for `source`. Returns `None` if the
    source has no transcript or the file can't be read/parsed. Never raises.

    On success the parsed transcript is cached by `(path, mtime_ns)`. A
    re-write of the sidecar (e.g. transcribe.py re-runs) invalidates the
    entry automatically because the mtime changes.
    """
    if source.transcript_path is None:
        return None
    path = Path(source.transcript_path)
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError) as e:
        log.warning("transcripts: cannot stat %s: %s", path, e)
        return None

    hit = _cache.get(path, stat.st_mtime_ns)
    if hit is not None:
        return hit

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("transcripts: cannot read %s: %s", path, e)
        return None
    try:
        raw = json.loads(raw_text)
        transcript = WhisperTranscript.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("transcripts: malformed sidecar at %s: %s", path, e)
        return None

    _cache.put(path, stat.st_mtime_ns, transcript)
    return transcript


def transcript_text_for_range(
    transcript: WhisperTranscript, start: float, end: float
) -> str:
    """Concatenate every word from `transcript` whose timing falls inside
    `[start, end)`. Word strings carry their own leading space
    (faster_whisper convention) — no separator needed; the result is
    `.strip()`-ed at the end so the displayed clip text doesn't start
    with an awkward leading space.

    Shared by `ingest._segment_into_clips` (during initial segmentation)
    and `boundary.split_clip` / `boundary.merge_clips` /
    `boundary.adjust_clip_boundaries` / `boundary.create_clip_from_range`
    (during user-driven boundary correction). Pure: no I/O, no state.

    Half-open: a word with `w.start == end` belongs to the NEXT clip;
    matches the half-open `[start, end)` invariant from
    `clipfarm/routes/search.py:_clip_id_for_timestamp`.
    """
    parts: list[str] = []
    for seg in transcript.segments:
        for w in seg.words:
            if w.end <= start:
                continue
            if w.start >= end:
                # Past the range — segments are time-ordered, so we're done.
                return "".join(parts).strip()
            parts.append(w.word)
    return "".join(parts).strip()


__all__ = ["cache", "load_transcript_for_source", "transcript_text_for_range"]
