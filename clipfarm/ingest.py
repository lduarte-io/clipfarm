"""Ingest pipeline — walk a folder of `.mov` files, validate their Whisper
sidecars, segment into clips, and mutate the in-memory `ClipFarmState`.

This is the orchestrator. It deliberately does NOT touch `clipfarm.json` —
the route layer calls `commit_state_to_disk(app)` after `ingest_folder`
returns. That keeps the orchestrator a pure mutation over state + a result
summary, easily testable.

Re-ingest semantics (see PHASES.md Phase 2):
- Source already in state, transcript was None, now exists → segment, mark
  `sources_updated`.
- Source already in state, transcript was present, transcript path
  unchanged → skip (no re-segment).
- New source → add + segment if transcript present.
- Sources whose files no longer exist on disk are NOT removed (the
  integrity check on load handles that).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from clipfarm.ffprobe import probe_video
from clipfarm.models import (
    Clip,
    ClipFarmState,
    Source,
    StrictModel,
    WhisperTranscript,
)
from clipfarm.segmentation import segment_words_by_silence

log = logging.getLogger("clipfarm.ingest")

# Filename stem must not contain this separator (spec → "Source filename
# constraint"). It's the clip-ID separator.
RESERVED_SEPARATOR = "__"

# Acceptable video extensions for ingest. Spec covers `.mov` from the
# dogfood folder; tolerating common siblings doesn't hurt and avoids
# false negatives if the user drops a folder with mixed formats.
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".mkv"}

WHISPER_SCHEMA_VERSION = 1


class IngestRejection(StrictModel):
    filename: str
    reason: str  # "filename-contains-__" | "schema-version-mismatch" | "transcript-malformed" | "transcript-unreadable"
    sanitized_rename: Optional[str] = None
    detail: str = ""


class IngestResult(StrictModel):
    sources_added: list[str] = []
    sources_skipped: list[str] = []
    sources_updated: list[str] = []
    rejected: list[IngestRejection] = []
    warnings: list[str] = []
    clips_detected: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_source_id(state: ClipFarmState) -> str:
    """Monotonic string-int IDs. Phase 2 only needs that they're unique +
    stable; opaque after creation."""
    used = {int(k) for k in state.sources.keys() if k.isdigit()}
    return str(max(used) + 1) if used else "1"


def _hms(t: float) -> str:
    """`HH-MM-SS.mmm` — used inside the clip ID's encoded form.

    Dashes (not colons) so the ID is safe in filenames + URLs. The ID is
    opaque after creation, but the encoded form is human-readable at the
    moment a clip is born."""
    total_ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}.{ms:03d}"


def _make_clip_id(source_stem: str, start: float, end: float) -> str:
    return f"{source_stem}{RESERVED_SEPARATOR}{_hms(start)}{RESERVED_SEPARATOR}{_hms(end)}"


def _sanitize_filename_stem(stem: str) -> str:
    """Replace runs of `__` with `_`. Used for the rename suggestion."""
    while RESERVED_SEPARATOR in stem:
        stem = stem.replace(RESERVED_SEPARATOR, "_")
    return stem


def _sidecar_path_for(mov_path: Path) -> Path:
    """Convention: `<stem>.whisper.json` next to the `.mov` (e.g.
    `btc.0.4.whisper.json` for `btc.0.4.mov`).

    Note the stem can itself contain dots (`btc.0.4`) — `Path.stem` strips
    only the final extension, which is what we want.
    """
    return mov_path.parent / f"{mov_path.stem}.whisper.json"


def _load_sidecar(path: Path) -> tuple[Optional[WhisperTranscript], Optional[IngestRejection]]:
    """Returns `(transcript, None)` on success or `(None, rejection)` on
    failure. Splits the failure modes so the orchestrator can report them
    individually."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, IngestRejection(
            filename=path.parent.name + "/" + path.name,
            reason="transcript-unreadable",
            detail=str(e),
        )
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return None, IngestRejection(
            filename=path.name,
            reason="transcript-malformed",
            detail=f"invalid JSON: {e}",
        )

    schema_version = raw.get("schema_version")
    if schema_version != WHISPER_SCHEMA_VERSION:
        return None, IngestRejection(
            filename=path.name,
            reason="schema-version-mismatch",
            detail=(
                f"sidecar reports schema_version={schema_version}; ClipFarm "
                f"supports {WHISPER_SCHEMA_VERSION}. Re-run transcribe.py or "
                f"add an adapter migration."
            ),
        )

    try:
        return WhisperTranscript.model_validate(raw), None
    except ValidationError as e:
        return None, IngestRejection(
            filename=path.name,
            reason="transcript-malformed",
            detail=str(e),
        )


def _flatten_words(transcript: WhisperTranscript) -> list:
    """Pre-Phase-3 the words from every segment are concatenated in order
    (segments come pre-sorted from faster_whisper). Silence segmentation
    operates on the flat list."""
    out = []
    for seg in transcript.segments:
        out.extend(seg.words)
    return out


def _transcript_text_for_range(transcript: WhisperTranscript, start: float, end: float) -> str:
    """Concatenate every word that falls inside `[start, end]`. Word strings
    carry their own leading space (faster_whisper convention) — no separator
    needed."""
    parts: list[str] = []
    for seg in transcript.segments:
        for w in seg.words:
            if w.end <= start:
                continue
            if w.start >= end:
                break
            parts.append(w.word)
        else:
            continue
        # If we broke out of the inner loop, also stop the outer? No — the
        # ranges came from this same word stream, so once we leave the
        # range we're done.
        break
    return "".join(parts).strip()


def ingest_folder(state: ClipFarmState, folder: Path) -> IngestResult:
    """Walk `folder`, ingest every `.mov` + sidecar pair, mutate `state`
    in-place, return a summary.

    Does not touch the on-disk `clipfarm.json` — the caller persists via
    `commit_state_to_disk(app)` after this returns.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"not a directory: {folder}")

    result = IngestResult()
    now = _now()

    # Index existing sources by resolved path for O(1) re-ingest detection.
    sources_by_path: dict[str, tuple[str, Source]] = {
        str(Path(s.path).resolve()): (sid, s) for sid, s in state.sources.items()
    }

    mov_paths = sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )

    for mov in mov_paths:
        stem = mov.stem
        # __ rejection
        if RESERVED_SEPARATOR in stem:
            result.rejected.append(
                IngestRejection(
                    filename=mov.name,
                    reason="filename-contains-__",
                    sanitized_rename=f"{_sanitize_filename_stem(stem)}{mov.suffix}",
                    detail=(
                        "Source filenames cannot contain '__' — it is the "
                        "clip-ID separator. Rename the file and re-ingest."
                    ),
                )
            )
            continue

        resolved = str(mov.resolve())
        existing = sources_by_path.get(resolved)

        sidecar = _sidecar_path_for(mov)
        sidecar_exists = sidecar.is_file()

        # Load sidecar if present; record rejections but don't lose the
        # source — a sidecar problem doesn't kill the .mov entry.
        transcript: Optional[WhisperTranscript] = None
        if sidecar_exists:
            transcript, rej = _load_sidecar(sidecar)
            if rej is not None:
                result.rejected.append(rej)
                # When sidecar rejected: still register the source as
                # transcript-less so the user can re-run transcribe.py later
                # without losing the source entry. UNLESS the source already
                # exists (then we leave it untouched).
                if existing is None:
                    sid = _next_source_id(state)
                    probed = probe_video(mov)
                    state.sources[sid] = Source(
                        filename=mov.name,
                        path=resolved,
                        fps=probed["fps"],
                        duration_sec=probed["duration_sec"],
                        transcript_path=None,
                        added_at=now,
                        unavailable=False,
                    )
                    sources_by_path[resolved] = (sid, state.sources[sid])
                    result.sources_added.append(mov.name)
                continue

        if existing is None:
            # Brand new source.
            sid = _next_source_id(state)
            probed = probe_video(mov)
            # Prefer sidecar `duration` when available; fall back to ffprobe.
            duration = (
                transcript.duration
                if transcript is not None and transcript.duration is not None
                else probed["duration_sec"]
            )
            source = Source(
                filename=mov.name,
                path=resolved,
                fps=probed["fps"],
                duration_sec=duration,
                transcript_path=str(sidecar.resolve()) if sidecar_exists else None,
                added_at=now,
                unavailable=False,
            )
            state.sources[sid] = source
            sources_by_path[resolved] = (sid, source)
            result.sources_added.append(mov.name)
            if transcript is not None:
                added = _segment_into_clips(state, sid, mov.stem, transcript, now)
                result.clips_detected += added
            else:
                # Transcript-less ingest is legal — the source is in the
                # library, just with no auto-detected clips.
                result.warnings.append(
                    f"{mov.name}: no sidecar transcript — added as footage-only"
                )
            continue

        # Source already exists.
        sid, existing_source = existing
        if existing_source.transcript_path is None and transcript is not None:
            # Upgrade path: transcript newly available.
            existing_source.transcript_path = str(sidecar.resolve())
            if existing_source.duration_sec is None and transcript.duration is not None:
                existing_source.duration_sec = transcript.duration
            added = _segment_into_clips(state, sid, mov.stem, transcript, now)
            result.clips_detected += added
            result.sources_updated.append(mov.name)
        else:
            # Already fully ingested (or still has no transcript). No-op.
            result.sources_skipped.append(mov.name)

    return result


def _segment_into_clips(
    state: ClipFarmState,
    source_id: str,
    source_stem: str,
    transcript: WhisperTranscript,
    created_at: str,
) -> int:
    """Compute silence-bounded ranges from `transcript` and add `Clip`
    entries to `state.clips`. Returns the number of clips added.

    Skips ranges whose clip ID already exists (idempotent — though the
    re-ingest pathway prevents this from being hit in practice).
    """
    words = _flatten_words(transcript)
    if not words:
        return 0
    ranges = segment_words_by_silence(words)
    added = 0
    for start, end in ranges:
        clip_id = _make_clip_id(source_stem, start, end)
        if clip_id in state.clips:
            continue
        state.clips[clip_id] = Clip(
            source_id=source_id,
            start_sec=start,
            end_sec=end,
            transcript_text=_transcript_text_for_range(transcript, start, end),
            created_at=created_at,
        )
        added += 1
    return added


__all__ = [
    "IngestRejection",
    "IngestResult",
    "RESERVED_SEPARATOR",
    "VIDEO_EXTENSIONS",
    "ingest_folder",
]
