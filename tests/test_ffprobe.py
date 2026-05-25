"""Tests for `clipfarm/ffprobe.py` — patches `subprocess.run` to return
canned outputs. Avoids depending on a real ffmpeg install during unit tests
(it's installed for the actual app, but unit tests run cleanly without it).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from clipfarm.ffprobe import probe_video


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _ok_payload(r_frame_rate: str = "60/1", duration: str | None = "120.5") -> str:
    stream: dict = {"r_frame_rate": r_frame_rate}
    if duration is not None:
        stream["duration"] = duration
    return json.dumps({"streams": [stream], "format": {"duration": duration}})


def test_returns_fps_and_duration_on_clean_run(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(0, stdout=_ok_payload("60/1", "120.5")),
    ):
        result = probe_video(path)
    assert result == {"fps": 60.0, "duration_sec": 120.5}


def test_fractional_fps_like_30000_1001(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(0, stdout=_ok_payload("30000/1001", "10.0")),
    ):
        result = probe_video(path)
    assert result["fps"] is not None
    assert abs(result["fps"] - 29.97002997) < 1e-6
    assert result["duration_sec"] == 10.0


def test_zero_zero_frame_rate_is_none(tmp_path: Path):
    """ffprobe emits `0/0` for streams it couldn't parse — must return None,
    not divide-by-zero."""
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(0, stdout=_ok_payload("0/0", "5.0")),
    ):
        result = probe_video(path)
    assert result["fps"] is None
    assert result["duration_sec"] == 5.0


def test_missing_duration_returns_none(tmp_path: Path):
    """No `duration` in stream OR format → duration_sec is None, fps still
    parses."""
    path = tmp_path / "fake.mov"
    path.touch()
    payload = json.dumps({"streams": [{"r_frame_rate": "30/1"}], "format": {}})
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(0, stdout=payload),
    ):
        result = probe_video(path)
    assert result["fps"] == 30.0
    assert result["duration_sec"] is None


def test_ffprobe_exit_nonzero_returns_all_none(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(1, stderr="not a video"),
    ):
        result = probe_video(path)
    assert result == {"fps": None, "duration_sec": None}


def test_ffprobe_binary_missing_returns_all_none(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value=None):
        result = probe_video(path)
    assert result == {"fps": None, "duration_sec": None}


def test_malformed_json_returns_all_none(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        return_value=_completed(0, stdout="not json at all"),
    ):
        result = probe_video(path)
    assert result == {"fps": None, "duration_sec": None}


def test_subprocess_oserror_returns_all_none(tmp_path: Path):
    path = tmp_path / "fake.mov"
    path.touch()
    with patch("clipfarm.ffprobe.shutil.which", return_value="/usr/bin/ffprobe"), patch(
        "clipfarm.ffprobe.subprocess.run",
        side_effect=OSError("boom"),
    ):
        result = probe_video(path)
    assert result == {"fps": None, "duration_sec": None}


# --- Smoke test against real ffprobe + real video, if both are available -----

_REAL_VIDEO = Path(
    "/Users/lillianduarte/Desktop/AdAstra/2ndMind/Creation/PlanetLillian/"
    "Video/Scripts/mp4files/05.19.26/btc.0.4.mov"
)


@pytest.mark.skipif(
    not _REAL_VIDEO.exists(),
    reason="real btc.0.4.mov not present on this machine",
)
def test_probes_a_real_mov_file():
    """Sanity check — if the actual sample file is present (it is on Lillian's
    machine), make sure the wrapper returns plausible values, not just None."""
    result = probe_video(_REAL_VIDEO)
    assert result["fps"] is not None and result["fps"] > 0, result
    assert result["duration_sec"] is not None and result["duration_sec"] > 0, result
