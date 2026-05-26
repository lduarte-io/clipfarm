"""Thin `ffprobe` subprocess wrapper.

ClipFarm calls this at ingest to capture per-source `fps` and `duration_sec`.
Failure paths are non-fatal — the source still gets ingested with `fps=None`
/ `duration_sec=None`. Phase 10 then falls back to 30 fps for frame-precise
nudging with a one-time UI warning (see spec → "Source fps detection").

Why a tiny wrapper and not a library: the dependency surface is one
subprocess call. Pulling in `pymediainfo` or similar buys nothing for this
much code.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, TypedDict

log = logging.getLogger("clipfarm.ffprobe")


class ProbeResult(TypedDict):
    fps: Optional[float]
    duration_sec: Optional[float]


_FFPROBE_ARGS_TEMPLATE = [
    "ffprobe",
    "-v",
    "error",
    "-select_streams",
    "v:0",
    "-show_entries",
    "stream=r_frame_rate,duration:format=duration",
    "-of",
    "json",
]


def _parse_frame_rate(raw: str) -> Optional[float]:
    """`r_frame_rate` is typically `"60/1"`, `"30000/1001"`, or numeric.
    Returns float or None if it can't be parsed."""
    if not raw or raw == "0/0":
        return None
    try:
        if "/" in raw:
            num, denom = raw.split("/", 1)
            denom_f = float(denom)
            if denom_f == 0:
                return None
            return float(num) / denom_f
        return float(raw)
    except (ValueError, TypeError):
        return None


def _parse_duration(stream_data: dict, format_data: dict) -> Optional[float]:
    """Prefer the stream's `duration`; fall back to the format-level
    `duration` (which is present for most container files even when the
    stream doesn't carry one)."""
    for source in (stream_data, format_data):
        if not source:
            continue
        raw = source.get("duration")
        if raw is None:
            continue
        try:
            return float(raw)
        except (ValueError, TypeError):
            continue
    return None


def probe_video(path: Path) -> ProbeResult:
    """Return `{"fps": ..., "duration_sec": ...}` for the given video file.
    Both fields are `None` on any failure (binary missing, file unreadable,
    malformed output). The call never raises — log + degrade.
    """
    result: ProbeResult = {"fps": None, "duration_sec": None}

    if shutil.which("ffprobe") is None:
        log.warning("ffprobe: binary not found on PATH; cannot probe %s", path)
        return result

    cmd = _FFPROBE_ARGS_TEMPLATE + [str(path)]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            # 30s cushion — metadata reads are near-instant in practice,
            # but ffprobe can stall briefly on large files under system
            # load (e.g. concurrent Ollama tagging burns CPU).
            timeout=30.0,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("ffprobe: subprocess failed on %s: %s", path, e)
        return result

    if completed.returncode != 0:
        log.warning(
            "ffprobe: exit=%d on %s; stderr=%s",
            completed.returncode,
            path,
            (completed.stderr or "").strip()[:200],
        )
        return result

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as e:
        log.warning("ffprobe: malformed JSON for %s: %s", path, e)
        return result

    streams = parsed.get("streams") or []
    stream0 = streams[0] if streams else {}
    fmt = parsed.get("format") or {}

    result["fps"] = _parse_frame_rate(stream0.get("r_frame_rate", ""))
    result["duration_sec"] = _parse_duration(stream0, fmt)
    return result


__all__ = ["ProbeResult", "probe_video"]
