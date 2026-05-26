"""Tests for `clipfarm/routes/clips.py` — five boundary-correction routes.

Phase 4's headline invariant: **every destructive op produces exactly one
snapshot file**. The `_count_snapshots_after_op` helper wraps every
happy-path test to assert that.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipfarm.transcripts import cache


def _write_pair(folder: Path, stem: str, words: list[tuple[float, float, str]]) -> None:
    (folder / f"{stem}.mov").write_bytes(b"")
    payload = {
        "schema_version": 1,
        "duration": 30.0,
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


def _ingest(client: TestClient, tmp_path: Path) -> str:
    """Ingest a folder with one source that has 5 silence-separated clips
    so every boundary op has something to chew on. Returns the source_id."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(
        folder,
        "src",
        [
            (1.0, 1.5, " one"),
            (5.0, 5.5, " two"),
            (10.0, 10.5, " three"),
            (15.0, 15.5, " four"),
            (20.0, 20.5, " five"),
        ],
    )
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        r = client.post("/api/ingest", json={"folder": str(folder)})
    assert r.status_code == 200, r.text
    sid = next(iter(client.get("/api/state").json()["sources"].keys()))
    return sid


def _count_snapshots(client: TestClient) -> int:
    if not client.snapshot_dir.exists():
        return 0
    return len(list(client.snapshot_dir.glob("*.json")))


def _count_snapshots_after_op(
    client: TestClient, op: Callable[[], None], *, expected_reason: str
) -> Path:
    """Run `op`, assert exactly one new snapshot file appeared whose
    name contains `expected_reason`. Returns the new snapshot path."""
    before = set(client.snapshot_dir.glob("*.json")) if client.snapshot_dir.exists() else set()
    op()
    after = set(client.snapshot_dir.glob("*.json"))
    new = after - before
    assert len(new) == 1, (
        f"expected exactly 1 new snapshot, got {len(new)} "
        f"(before={len(before)}, after={len(after)})"
    )
    snap_path = next(iter(new))
    assert expected_reason in snap_path.name, (
        f"snapshot {snap_path.name} does not carry expected reason "
        f"`{expected_reason}`"
    )
    return snap_path


# ---------- split -------------------------------------------------------------


def test_split_happy_path_writes_one_snapshot(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    # Pick a clip with a wide enough range to split.
    clip_id, clip = next(
        (cid, c) for cid, c in state["clips"].items()
        if c["end_sec"] - c["start_sec"] > 0.2
    )
    split_at = (clip["start_sec"] + clip["end_sec"]) / 2

    result_holder = {}

    def op():
        r = client.post(
            f"/api/clips/{clip_id}/split", json={"split_at_sec": split_at}
        )
        assert r.status_code == 200, r.text
        result_holder["body"] = r.json()

    _count_snapshots_after_op(client, op, expected_reason="split-clip")
    body = result_holder["body"]
    assert body["old_clip_id"] == clip_id
    assert len(body["new_clip_ids"]) == 2
    assert body["new_clip_ids"][0] != body["new_clip_ids"][1]
    # Original gone, new clips present.
    state2 = client.get("/api/state").json()
    assert clip_id not in state2["clips"]
    for new_id in body["new_clip_ids"]:
        assert new_id in state2["clips"]


def test_split_out_of_range_400(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    clip_id, clip = next(iter(state["clips"].items()))
    r = client.post(
        f"/api/clips/{clip_id}/split",
        json={"split_at_sec": clip["end_sec"] + 5.0},
    )
    assert r.status_code == 400


def test_split_unknown_clip_404(client: TestClient, tmp_path: Path):
    _ingest(client, tmp_path)
    r = client.post("/api/clips/nope/split", json={"split_at_sec": 1.0})
    assert r.status_code == 404


def test_split_when_writes_frozen_409(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    clip_id = next(iter(state["clips"].keys()))
    client.app.state.writes_frozen = True
    try:
        r = client.post(
            f"/api/clips/{clip_id}/split", json={"split_at_sec": 1.0}
        )
    finally:
        client.app.state.writes_frozen = False
    assert r.status_code == 409


# ---------- merge -------------------------------------------------------------


def test_merge_happy_path_writes_one_snapshot(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    # Pick the two earliest clips on the source — they're guaranteed
    # non-overlapping (ingest segments by silence).
    clips_sorted = sorted(
        state["clips"].items(), key=lambda kv: kv[1]["start_sec"]
    )
    pair = [clips_sorted[0][0], clips_sorted[1][0]]

    result_holder = {}

    def op():
        r = client.post("/api/clips/merge", json={"clip_ids": pair})
        assert r.status_code == 200, r.text
        result_holder["body"] = r.json()

    _count_snapshots_after_op(client, op, expected_reason="merge-clips")
    body = result_holder["body"]
    assert body["merged"] == pair
    # Originals gone, new one present.
    state2 = client.get("/api/state").json()
    for cid in pair:
        assert cid not in state2["clips"]
    assert body["new_clip_id"] in state2["clips"]


def test_merge_cross_source_400(client: TestClient, tmp_path: Path):
    """Set up two sources, attempt to merge across — 400."""
    folder = tmp_path / "media"
    folder.mkdir()
    _write_pair(folder, "a", [(1.0, 1.5, " a"), (5.0, 5.5, " b")])
    _write_pair(folder, "b", [(1.0, 1.5, " c"), (5.0, 5.5, " d")])
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})

    state = client.get("/api/state").json()
    # One clip per source.
    by_source: dict[str, str] = {}
    for cid, c in state["clips"].items():
        by_source.setdefault(c["source_id"], cid)
    pair = list(by_source.values())[:2]
    r = client.post("/api/clips/merge", json={"clip_ids": pair})
    assert r.status_code == 400


def test_merge_unknown_clip_404(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    real_id = next(iter(state["clips"].keys()))
    r = client.post("/api/clips/merge", json={"clip_ids": [real_id, "nope"]})
    assert r.status_code == 404


# ---------- adjust ------------------------------------------------------------


def test_adjust_happy_path_writes_one_snapshot(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    # Pick the first clip (earliest start). Shrink its end by 0.1s — safe;
    # ingest's silence gaps are ≥ 2 sec so no overlap risk.
    clip_id, clip = sorted(
        state["clips"].items(), key=lambda kv: kv[1]["start_sec"]
    )[0]
    new_start, new_end = clip["start_sec"], clip["end_sec"] - 0.1

    def op():
        r = client.patch(
            f"/api/clips/{clip_id}/boundaries",
            json={"start_sec": new_start, "end_sec": new_end},
        )
        assert r.status_code == 200, r.text

    _count_snapshots_after_op(client, op, expected_reason="adjust-boundaries")
    # Clip ID stays the same; boundaries updated.
    state2 = client.get("/api/state").json()
    assert clip_id in state2["clips"]
    assert state2["clips"][clip_id]["end_sec"] == pytest.approx(new_end)


def test_adjust_overlap_400(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    # Find two adjacent clips, then try to extend the first past the second.
    clips_sorted = sorted(
        state["clips"].items(), key=lambda kv: kv[1]["start_sec"]
    )
    first_id, first = clips_sorted[0]
    _, second = clips_sorted[1]
    r = client.patch(
        f"/api/clips/{first_id}/boundaries",
        json={
            "start_sec": first["start_sec"],
            "end_sec": second["end_sec"],  # would swallow the second clip
        },
    )
    assert r.status_code == 400


def test_adjust_invalid_range_400(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    clip_id = next(iter(state["clips"].keys()))
    r = client.patch(
        f"/api/clips/{clip_id}/boundaries",
        json={"start_sec": 5.0, "end_sec": 3.0},
    )
    assert r.status_code == 400


# ---------- create ------------------------------------------------------------


def test_create_happy_path_writes_one_snapshot(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    # Pick a range nicely in the gaps between detected clips. After
    # ingesting at 1s/5s/10s/15s/20s with 0.5s words, the gap [25, 26] is
    # untouched and inside the 30-sec duration.
    def op():
        r = client.post(
            f"/api/sources/{sid}/clips",
            json={"start_sec": 25.0, "end_sec": 26.0},
        )
        assert r.status_code == 200, r.text

    _count_snapshots_after_op(client, op, expected_reason="create-clip")


def test_create_overlap_400(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    # Try to create a clip spanning [0, 6) — that overlaps the ingested
    # clip at ~[1.0, 1.5).
    r = client.post(
        f"/api/sources/{sid}/clips",
        json={"start_sec": 0.0, "end_sec": 6.0},
    )
    assert r.status_code == 400


def test_create_unknown_source_404(client: TestClient, tmp_path: Path):
    _ingest(client, tmp_path)
    r = client.post(
        "/api/sources/9999/clips",
        json={"start_sec": 0.0, "end_sec": 1.0},
    )
    assert r.status_code == 404


def test_create_on_footage_only_source_works_with_empty_text(
    client: TestClient, tmp_path: Path
):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "no_sidecar.mov").write_bytes(b"")
    with patch(
        "clipfarm.ingest.probe_video",
        return_value={"fps": 30.0, "duration_sec": 30.0},
    ):
        client.post("/api/ingest", json={"folder": str(folder)})
    sid = next(iter(client.get("/api/state").json()["sources"].keys()))

    r = client.post(
        f"/api/sources/{sid}/clips",
        json={"start_sec": 1.0, "end_sec": 5.0},
    )
    assert r.status_code == 200
    new_id = r.json()["new_clip_id"]
    state = client.get("/api/state").json()
    assert state["clips"][new_id]["transcript_text"] == ""


# ---------- delete ------------------------------------------------------------


def test_delete_happy_path_writes_one_snapshot(client: TestClient, tmp_path: Path):
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    clip_id = next(iter(state["clips"].keys()))

    result_holder = {}

    def op():
        r = client.delete(f"/api/clips/{clip_id}")
        assert r.status_code == 200, r.text
        result_holder["body"] = r.json()

    _count_snapshots_after_op(client, op, expected_reason="delete-clip")
    body = result_holder["body"]
    assert body["deleted_clip_id"] == clip_id
    # Forward-compatible response shape even at zero today.
    assert body["dropped_tag_rows"] == 0
    assert body["affected_attempts"] == 0
    state2 = client.get("/api/state").json()
    assert clip_id not in state2["clips"]


def test_delete_unknown_404(client: TestClient, tmp_path: Path):
    _ingest(client, tmp_path)
    r = client.delete("/api/clips/nope")
    assert r.status_code == 404


# ---------- Snapshot-per-op invariant across all ops --------------------------


def test_snapshot_count_equals_op_count(client: TestClient, tmp_path: Path):
    """Run one of each op type; assert snapshot count goes up by exactly N."""
    sid = _ingest(client, tmp_path)
    starting = _count_snapshots(client)

    state = client.get("/api/state").json()
    clips_sorted = sorted(
        state["clips"].items(), key=lambda kv: kv[1]["start_sec"]
    )
    first_id, first_clip = clips_sorted[0]
    second_id, _ = clips_sorted[1]

    # 1. Split the first clip.
    split_at = (first_clip["start_sec"] + first_clip["end_sec"]) / 2
    r = client.post(
        f"/api/clips/{first_id}/split", json={"split_at_sec": split_at}
    )
    assert r.status_code == 200
    halves = r.json()["new_clip_ids"]
    # 2. Merge the two halves back together.
    r = client.post("/api/clips/merge", json={"clip_ids": list(halves)})
    assert r.status_code == 200
    merged_id = r.json()["new_clip_id"]
    # 3. Adjust the merged clip's end inward by 0.05s.
    new_end = first_clip["end_sec"] - 0.05
    r = client.patch(
        f"/api/clips/{merged_id}/boundaries",
        json={"start_sec": first_clip["start_sec"], "end_sec": new_end},
    )
    assert r.status_code == 200
    # 4. Create a new clip in an unused range.
    r = client.post(
        f"/api/sources/{sid}/clips",
        json={"start_sec": 25.0, "end_sec": 26.0},
    )
    assert r.status_code == 200
    created_id = r.json()["new_clip_id"]
    # 5. Delete it.
    r = client.delete(f"/api/clips/{created_id}")
    assert r.status_code == 200

    after = _count_snapshots(client)
    assert after - starting == 5, (
        f"expected exactly 5 new snapshots after 5 destructive ops, "
        f"got {after - starting}"
    )


# ---------- Lock invariant (carry from Phase 2.1) ----------------------------


def test_split_route_holds_save_lock_during_orchestrator(
    client: TestClient, tmp_path: Path
):
    """Same structural assertion as the ingest route — wrap the
    orchestrator in a patched fake that records lock state at call time."""
    sid = _ingest(client, tmp_path)
    state = client.get("/api/state").json()
    clip_id, clip = next(iter(state["clips"].items()))

    observed: list[bool] = []

    def fake_split(state, cid, split_at, transcript):
        observed.append(client.app.state.save_lock.locked())
        # Don't actually mutate — we only care about the lock-held assertion.
        # Return a placeholder result.
        return (cid + "_a", cid + "_b")

    with patch("clipfarm.routes.clips.split_clip", side_effect=fake_split):
        # The fake returns synthetic IDs that don't exist in state.clips;
        # the route will still try to commit + the response shape is fine
        # because we're only inspecting `observed`.
        client.post(
            f"/api/clips/{clip_id}/split",
            json={"split_at_sec": (clip["start_sec"] + clip["end_sec"]) / 2},
        )

    assert observed == [True], (
        "split_clip ran without `save_lock` held — the mutation seam is open"
    )
