"""Tests for `GET /api/projects/{project_id}/take-grid` — end-to-end
through the FastAPI route with real sidecars on disk (so the
`first_word_index` lookup is exercised against actual transcript
loading)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipfarm.transcripts import cache


_BRIEF_V1 = """---
name: take grid test project
script:
  - intro line
  - body line
---

what's good: energy
"""


def _write_pair(folder: Path, stem: str, words: list[tuple[float, float, str]]) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    payload = {
        "schema_version": 1,
        "duration": max(w[1] for w in words) + 1.0,
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
        c.snapshot_dir = state_path.parent / ".clipfarm" / "snapshots"
        yield c
    cache().clear()


def _count_snapshots(client: TestClient) -> int:
    if not client.snapshot_dir.exists():
        return 0
    return len(list(client.snapshot_dir.glob("*.json")))


def _seed(client: TestClient, tmp_path: Path) -> tuple[str, list[str]]:
    """Ingest one source with two clips (separated by a >=2s silence)
    and create one project with two script lines. Returns
    (project_id, ordered_clip_ids)."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "alpha", [
        # First clip — words at 1.0-1.5s
        (1.0, 1.2, " intro"),
        (1.3, 1.5, " line"),
        # Big silence (>2s) splits the clip.
        (5.0, 5.3, " body"),
        (5.4, 5.6, " stuff"),
    ])
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 10.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})

    r = client.post("/api/projects", json={"brief_md": _BRIEF_V1})
    pid = r.json()["project_id"]

    state = client.get("/api/state").json()
    clip_ids = sorted(
        state["clips"].keys(),
        key=lambda cid: state["clips"][cid]["start_sec"],
    )
    return pid, clip_ids


def _project_tag_ids(client: TestClient, project_id: str) -> list[str]:
    """Tag ids of the project's line tags, in order_idx order."""
    state = client.get("/api/state").json()
    tags = state["projects"][project_id]["tags"]
    line_tags = sorted(
        ((tid, t) for tid, t in tags.items() if t["kind"] == "line"),
        key=lambda kv: kv[1]["order_idx"],
    )
    return [tid for tid, _ in line_tags]


# ---------- Happy path ------------------------------------------------------


def test_take_grid_happy_path(client: TestClient, tmp_path: Path):
    pid, clip_ids = _seed(client, tmp_path)
    line_tag_ids = _project_tag_ids(client, pid)
    assert len(clip_ids) == 2
    assert len(line_tag_ids) == 2

    # Manually inject tag rows (skip the LLM).
    state = client.app.state.clipfarm
    from clipfarm.models import ClipProjectTag
    state.clip_project_tags.extend([
        ClipProjectTag(
            clip_id=clip_ids[0], project_id=pid,
            project_tag_id=line_tag_ids[0], category="on-script",
            confidence=0.9,
        ),
        ClipProjectTag(
            clip_id=clip_ids[1], project_id=pid,
            project_tag_id=None, category="standalone-idea",
            confidence=0.7,
        ),
    ])

    r = client.get(f"/api/projects/{pid}/take-grid")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == pid
    # Lines + buckets shape.
    assert {row["tag_id"] for row in body["lines"]} == set(line_tag_ids)
    assert set(body["buckets"].keys()) == {
        "related-but-different", "standalone-idea", "off-topic", "fragment",
    }
    # Line[0] should carry the on-script clip; bucket should carry the other.
    on_script_row = next(
        row for row in body["lines"] if row["tag_id"] == line_tag_ids[0]
    )
    assert [c["clip_id"] for c in on_script_row["cards"]] == [clip_ids[0]]
    standalone = body["buckets"]["standalone-idea"]["cards"]
    assert [c["clip_id"] for c in standalone] == [clip_ids[1]]


def test_take_grid_404_unknown_project(client: TestClient, tmp_path: Path):
    _seed(client, tmp_path)
    r = client.get("/api/projects/9999/take-grid")
    assert r.status_code == 404


# ---------- Read-only guarantee ---------------------------------------------


def test_take_grid_writes_no_snapshot(client: TestClient, tmp_path: Path):
    """Phase 7 invariant: the take-grid endpoint is purely read-only. No
    snapshot side effect even when the underlying state is busy."""
    pid, _ = _seed(client, tmp_path)
    before = _count_snapshots(client)
    # Multiple calls — none should snapshot.
    for _ in range(3):
        r = client.get(f"/api/projects/{pid}/take-grid")
        assert r.status_code == 200
    assert _count_snapshots(client) == before


def test_take_grid_does_not_touch_dirty_flag(
    client: TestClient, tmp_path: Path
):
    """A pure read must not flip `app.state.dirty`. Otherwise the next
    save would spuriously snapshot."""
    pid, _ = _seed(client, tmp_path)
    client.app.state.dirty = False
    client.get(f"/api/projects/{pid}/take-grid")
    assert client.app.state.dirty is False


# ---------- Card content ----------------------------------------------------


def test_take_grid_card_carries_filename_and_word_index(
    client: TestClient, tmp_path: Path
):
    pid, clip_ids = _seed(client, tmp_path)
    line_tag_ids = _project_tag_ids(client, pid)

    state = client.app.state.clipfarm
    from clipfarm.models import ClipProjectTag
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id=clip_ids[0], project_id=pid,
            project_tag_id=line_tag_ids[0], category="on-script",
            confidence=0.9,
        )
    )
    r = client.get(f"/api/projects/{pid}/take-grid")
    body = r.json()
    row = next(r for r in body["lines"] if r["tag_id"] == line_tag_ids[0])
    [card] = row["cards"]
    assert card["filename"] == "alpha.mov"
    # First clip starts at 1.0s — that's word index 0 in the flat list.
    assert card["first_word_index"] == 0


def test_take_grid_second_clip_word_index_offset(
    client: TestClient, tmp_path: Path
):
    """The second clip (start_sec=5.0) lands on word index 2 — the third
    word in the flat list — because the first two words (intro/line) are
    on the earlier clip."""
    pid, clip_ids = _seed(client, tmp_path)
    line_tag_ids = _project_tag_ids(client, pid)

    state = client.app.state.clipfarm
    from clipfarm.models import ClipProjectTag
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id=clip_ids[1], project_id=pid,
            project_tag_id=line_tag_ids[1], category="on-script",
            confidence=0.8,
        )
    )
    r = client.get(f"/api/projects/{pid}/take-grid")
    body = r.json()
    row = next(r for r in body["lines"] if r["tag_id"] == line_tag_ids[1])
    [card] = row["cards"]
    assert card["first_word_index"] == 2


def test_take_grid_summary_numbers_match_state(
    client: TestClient, tmp_path: Path
):
    pid, clip_ids = _seed(client, tmp_path)
    line_tag_ids = _project_tag_ids(client, pid)

    state = client.app.state.clipfarm
    from clipfarm.models import ClipProjectTag
    # Tag only c0 → 1 tagged, 1 untagged.
    state.clip_project_tags.append(
        ClipProjectTag(
            clip_id=clip_ids[0], project_id=pid,
            project_tag_id=line_tag_ids[0], category="on-script",
            confidence=0.7,
        )
    )
    r = client.get(f"/api/projects/{pid}/take-grid")
    summary = r.json()["summary"]
    assert summary["total_tagged"] == 1
    assert summary["untagged_clips"] == 1
    assert summary["stale_clips"] == 0
