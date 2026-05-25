"""On-disk state I/O for ClipFarm — the single seam to `clipfarm.json`.

The on-disk file is the source of truth. This module:

- Is the only place that opens `clipfarm.json` (CLAUDE.md invariant). Routes
  read in-memory `app.state.clipfarm` via a `Depends` provider; writes go
  through `save_state()` here.
- Loads through the migration runner, **diffs known-vs-raw keys and logs
  per-key warnings** for any key that gets dropped during validation. Pydantic
  models are `extra="ignore"`, so unknowns silently disappear at the model
  level — the diff-and-log pass surfaces them.
- Writes atomically (`tmp` → `fsync` → `rename`) under an `asyncio.Lock`. The
  `snapshot_before_destructive()` helper acquires the same lock so the
  pre-write snapshot + write are one critical section.
- Snapshot filenames include ms + a 4-char content hash so even tests
  generating many snapshots inside the same millisecond produce distinct
  files.
- Runs a source-file integrity check on load — missing `.mov` files flip the
  source to `unavailable: true` rather than crashing.

What this module does NOT do:
- Watch the file. That's `watcher.py`.
- Decide what counts as a "destructive operation." Callers do, by invoking
  `snapshot_before_destructive()` themselves before mutating in-memory state.
- Surface conflict UX. `watcher.py` flips `app.state.writes_frozen`, the
  routes refuse writes, and Phase 2 lands the modal.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from clipfarm.migrations import (
    CURRENT_VERSION,
    needs_migration,
    run_migrations,
)
from clipfarm.models import ClipFarmState, empty_state

log = logging.getLogger("clipfarm.store")

DEFAULT_STATE_FILENAME = "clipfarm.json"
SNAPSHOT_DIR = ".clipfarm/snapshots"
SNAPSHOT_LIMIT = 50

# Filesystem-safe label for snapshot reasons. Spaces / weird chars → hyphens.
_SAFE_LABEL_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class WritesFrozenError(RuntimeError):
    """Raised when `save_state()` is called while writes are frozen due to an
    unresolved external-edit conflict. Surfaces to routes as a 409."""


def _safe_label(reason: str) -> str:
    return _SAFE_LABEL_RE.sub("-", reason.strip()) or "snapshot"


def _iso_filename_ts() -> tuple[str, str]:
    """Return `(iso_no_colons, ms_str)` so the snapshot filename builder can
    compose `<iso>-<ms>-<hash>__<reason>.json`."""
    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H-%M-%S")
    ms = f"{now.microsecond // 1000:03d}"
    return iso, ms


def serialize_state(state: ClipFarmState) -> str:
    """Canonical on-disk JSON form. Stable enough to hash for self-write
    detection."""
    return json.dumps(
        state.model_dump(mode="json"),
        indent=2,
        sort_keys=False,
        ensure_ascii=False,
    )


def hash_serialized(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def hash_bytes_short(data: bytes, length: int = 4) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


# --- Load ---------------------------------------------------------------------


def _known_fields(model_cls: type[BaseModel]) -> set[str]:
    return set(model_cls.model_fields.keys())


def _log_unknown_keys(raw: dict, model_cls: type[BaseModel], path: str = "") -> None:
    """Walk `raw` against the model's declared fields. Log one warning per
    key that's present in `raw` but unknown to the model. Recurses into nested
    StrictModel fields so a hand-edited `"_lillian_note"` inside a nested
    object also gets surfaced.
    """
    if not isinstance(raw, dict):
        return
    known = _known_fields(model_cls)
    for key in list(raw.keys()):
        if key not in known:
            log.warning(
                "clipfarm.json: dropping unknown key %s%s (not in %s schema)",
                path,
                key,
                model_cls.__name__,
            )
            continue
        field = model_cls.model_fields[key]
        annotation = field.annotation
        nested_cls = _nested_model_cls(annotation)
        if nested_cls is None:
            continue
        value = raw[key]
        nested_path = f"{path}{key}."
        if isinstance(value, dict):
            # Either a model directly or a dict-of-model. Heuristic: if the
            # value's first level keys match the nested model's field names,
            # treat it as a model instance; otherwise iterate.
            if _looks_like_dict_of_model(value, nested_cls):
                for sub_key, sub_val in value.items():
                    _log_unknown_keys(sub_val, nested_cls, f"{nested_path}{sub_key}.")
            else:
                _log_unknown_keys(value, nested_cls, nested_path)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                _log_unknown_keys(item, nested_cls, f"{nested_path}[{i}].")


def _nested_model_cls(annotation) -> type[BaseModel] | None:
    """Unwrap Optional / list / dict annotations to find a contained
    StrictModel subclass, if any. Returns None if no model is involved."""
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
        return None
    for arg in args:
        if arg is type(None):
            continue
        cls = _nested_model_cls(arg)
        if cls is not None:
            return cls
    return None


def _looks_like_dict_of_model(value: dict, nested_cls: type[BaseModel]) -> bool:
    """A `dict[str, SomeModel]` field is hard to distinguish from a single
    model instance by annotation walking alone (we already unwrapped). Use a
    heuristic: if every top-level value in `value` is itself a dict whose keys
    overlap the model's fields, treat it as a dict-of-models. Otherwise treat
    it as a single model instance."""
    if not value:
        return False
    known = _known_fields(nested_cls)
    sample_values = list(value.values())
    if not all(isinstance(v, dict) for v in sample_values):
        return False
    overlap_counts = [len(set(v.keys()) & known) for v in sample_values]
    return any(c > 0 for c in overlap_counts)


def load_state(state_path: Path) -> ClipFarmState:
    """Load `clipfarm.json` — the single entry point. Read → migrate → log+drop
    unknowns → validate → integrity-check → return.

    If the file doesn't exist, returns a fresh empty state at the current
    version *without* writing anything to disk. This matches the spec's
    first-launch behavior: the file's existence is the signal that real state
    exists.
    """
    if not state_path.exists():
        return empty_state()

    raw_text = state_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        return empty_state()

    raw = json.loads(raw_text)
    if needs_migration(int(raw.get("version", 1))):
        raw = run_migrations(raw)

    _log_unknown_keys(raw, ClipFarmState)

    state = ClipFarmState.model_validate(raw)
    state = run_source_integrity_check(state)
    return state


def run_source_integrity_check(state: ClipFarmState) -> ClipFarmState:
    """Flip `unavailable: true` for any source whose `path` no longer resolves.

    Run on every load and (in a future phase) on every Library refresh. Tags
    and attempt references stay intact — only playback is gated on a source
    being available.

    Mutates `source.unavailable` in place. We deliberately bypass Pydantic's
    `validate_assignment` (which is off — default) here; if it were on, every
    flip would re-run the model validator, which is unnecessary for this
    derived flag. If `validate_assignment=True` ever gets switched on,
    revisit this to avoid the redundant per-source validation pass.
    """
    for source in state.sources.values():
        try:
            exists = Path(source.path).is_file()
        except OSError:
            exists = False
        source.unavailable = not exists
    return state


# --- Save ---------------------------------------------------------------------


def atomic_write(target: Path, contents: str) -> None:
    """Write `contents` to `target` atomically via tmp → fsync → rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contents)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


async def save_state(
    state: ClipFarmState,
    state_path: Path,
    lock: asyncio.Lock,
    *,
    writes_frozen: bool = False,
    post_write: "Callable[[str], None] | None" = None,
) -> str:
    """Serialize and atomically write the state under the supplied
    `asyncio.Lock`. Returns the serialized form.

    `post_write`, if supplied, runs **inside the same locked critical
    section** with the serialized form's sha256 hex digest. The watcher's
    `update_last_known_hash` is the canonical caller — installing the new
    hash inside the lock closes the race where the polling observer could
    fire between the lock release and the hash install, see an "external"
    change, and spuriously freeze writes.

    `writes_frozen=True` raises `WritesFrozenError` instead of writing — used
    by the conflict-freeze path. Callers should consult
    `app.state.writes_frozen` before calling.
    """
    if writes_frozen:
        raise WritesFrozenError(
            "writes are frozen due to an unresolved external-edit conflict"
        )
    serialized = serialize_state(state)
    async with lock:
        atomic_write(state_path, serialized)
        if post_write is not None:
            post_write(hash_serialized(serialized))
    return serialized


async def save_state_with_snapshot(
    state: ClipFarmState,
    state_path: Path,
    lock: asyncio.Lock,
    reason: str,
    *,
    writes_frozen: bool = False,
    post_write: "Callable[[str], None] | None" = None,
) -> tuple[Path | None, str]:
    """Snapshot the on-disk file, then atomically write the new state — both
    inside the same `asyncio.Lock` critical section.

    This is the helper destructive operations (split, merge, delete, retag-
    clobber) call. Coupling the snapshot to the save under one lock makes a
    concurrent save impossible to slip in between them, so the snapshot is
    always the exact pre-change state.

    Returns `(snapshot_path_or_None, serialized_form)`. The snapshot path is
    `None` only if the state file didn't exist on disk yet (nothing to
    snapshot — same semantics as `snapshot_before_destructive`).
    """
    if writes_frozen:
        raise WritesFrozenError(
            "writes are frozen due to an unresolved external-edit conflict"
        )
    serialized = serialize_state(state)
    async with lock:
        snap_path = snapshot_before_destructive(state_path, reason)
        atomic_write(state_path, serialized)
        if post_write is not None:
            post_write(hash_serialized(serialized))
    return snap_path, serialized


def save_state_sync(state: ClipFarmState, state_path: Path) -> str:
    """Sync variant for tests + startup (where we don't have a running loop).
    Real route handlers must use `save_state` so concurrent writes serialize.
    """
    serialized = serialize_state(state)
    atomic_write(state_path, serialized)
    return serialized


# --- Snapshots ----------------------------------------------------------------


def snapshot_before_destructive(state_path: Path, reason: str) -> Path | None:
    """Copy `clipfarm.json` to
    `.clipfarm/snapshots/<ISO>-<ms>-<hash4>__<reason>.json` and prune to the
    last `SNAPSHOT_LIMIT`. Returns the snapshot path (or None if there's
    nothing on disk yet to snapshot).

    The hash suffix defends against same-ms collisions (tests can generate
    them; humans rarely will). Pruning keeps `.clipfarm/snapshots/` bounded
    without a separate maintenance pass.
    """
    if not state_path.exists():
        return None

    snap_dir = state_path.parent / SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)

    body = state_path.read_bytes()
    iso, ms = _iso_filename_ts()
    h4 = hash_bytes_short(body)
    snap_name = f"{iso}-{ms}-{h4}__{_safe_label(reason)}.json"
    snap_path = snap_dir / snap_name
    snap_path.write_bytes(body)

    _prune_snapshots(snap_dir, SNAPSHOT_LIMIT)
    return snap_path


def _prune_snapshots(snap_dir: Path, limit: int) -> None:
    snapshots = sorted(
        (p for p in snap_dir.glob("*.json") if p.is_file()),
        key=lambda p: p.name,
    )
    excess = len(snapshots) - limit
    for old in snapshots[: max(0, excess)]:
        try:
            old.unlink()
        except OSError:
            pass


def list_snapshots(state_path: Path) -> list[Path]:
    snap_dir = state_path.parent / SNAPSHOT_DIR
    if not snap_dir.exists():
        return []
    return sorted(snap_dir.glob("*.json"), key=lambda p: p.name, reverse=True)


__all__ = [
    "CURRENT_VERSION",
    "DEFAULT_STATE_FILENAME",
    "SNAPSHOT_DIR",
    "SNAPSHOT_LIMIT",
    "WritesFrozenError",
    "atomic_write",
    "hash_bytes_short",
    "hash_serialized",
    "list_snapshots",
    "load_state",
    "run_source_integrity_check",
    "save_state",
    "save_state_sync",
    "save_state_with_snapshot",
    "serialize_state",
    "snapshot_before_destructive",
]
