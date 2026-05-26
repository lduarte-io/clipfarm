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
    key that's present in `raw` but unknown to the model. Recurses into
    nested StrictModel fields so a hand-edited `"_lillian_note"` inside a
    nested object also gets surfaced.

    Uses `typing.get_origin` + `typing.get_args` to inspect each field's
    annotation deterministically rather than guessing — `dict[str, X]`,
    `Optional[X]`, `list[X]`, and direct-`X` all get walked correctly
    without any "do these keys look like a model?" heuristic. Phase 5
    activated this refactor when `Project.tags: dict[str, ProjectTag]`
    became the first real dict-of-model field; before Phase 5 the v1
    heuristic guessed (and got lucky), now it's typing-driven.
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
        value = raw[key]
        nested_path = f"{path}{key}."
        _walk_annotation(value, field.annotation, nested_path)


def _walk_annotation(value: object, annotation: object, path: str) -> None:
    """Dispatch on the annotation's shape via `typing.get_origin`. Each
    shape gets walked appropriately:

    - Direct `BaseModel` subclass → recurse into `_log_unknown_keys` on
      the dict.
    - `Optional[X]` (== `Union[X, None]`) → unwrap None, recurse with X.
    - `list[X]` → iterate `value`, recurse on each element with X.
    - `dict[K, X]` → iterate `value.values()`, recurse on each with X.
    - Anything else (`int`, `str`, `bool`, plain typed dict) → no model
      to walk; stop.

    Order matters: check `Optional` before `dict` because `Optional[X]`'s
    origin is `Union`, not `X`'s origin.
    """
    import typing
    from types import NoneType

    # Direct model — no generic wrapper.
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, dict):
            _log_unknown_keys(value, annotation, path)
        return

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    # Optional[X] / Union[X, None]: unwrap the None and re-dispatch on X.
    if origin is typing.Union or origin is __import__("types").UnionType:
        non_none = [a for a in args if a is not NoneType]
        if len(non_none) == 1:
            _walk_annotation(value, non_none[0], path)
        return

    # list[X]: walk each element with X.
    if origin in (list, tuple) and args:
        if not isinstance(value, (list, tuple)):
            return
        inner = args[0]
        for i, item in enumerate(value):
            _walk_annotation(item, inner, f"{path}[{i}].")
        return

    # dict[K, X]: walk each value with X.
    if origin is dict and len(args) == 2:
        if not isinstance(value, dict):
            return
        inner = args[1]
        for k, sub in value.items():
            _walk_annotation(sub, inner, f"{path}{k}.")
        return

    # Anything else (plain scalar, untyped dict, etc.) — no model to walk.
    return


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

    `serialize_state` runs INSIDE the lock — Phase 6 race closure. Under
    concurrent route handlers, capturing the serialized form before the
    lock could let two writers race on a stale snapshot of state.
    """
    if writes_frozen:
        raise WritesFrozenError(
            "writes are frozen due to an unresolved external-edit conflict"
        )
    async with lock:
        serialized = serialize_state(state)
        atomic_write(state_path, serialized)
        if post_write is not None:
            post_write(hash_serialized(serialized))
    return serialized


def save_state_locked(
    state: ClipFarmState,
    state_path: Path,
    *,
    writes_frozen: bool = False,
    post_write: "Callable[[str], None] | None" = None,
) -> str:
    """Synchronous variant of `save_state` that assumes the caller already
    holds the save lock — used when a route does
    `async with save_lock: { mutate; await commit_state_to_disk_locked }`
    so mutation + commit happen in one critical section, not two.

    No lock acquisition inside this function. The caller's responsibility
    to hold `app.state.save_lock` across the whole sequence.
    """
    if writes_frozen:
        raise WritesFrozenError(
            "writes are frozen due to an unresolved external-edit conflict"
        )
    serialized = serialize_state(state)
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
    async with lock:
        serialized = serialize_state(state)
        snap_path = snapshot_before_destructive(state_path, reason)
        atomic_write(state_path, serialized)
        if post_write is not None:
            post_write(hash_serialized(serialized))
    return snap_path, serialized


def save_state_with_snapshot_locked(
    state: ClipFarmState,
    state_path: Path,
    reason: str,
    *,
    writes_frozen: bool = False,
    post_write: "Callable[[str], None] | None" = None,
) -> tuple[Path | None, str]:
    """Caller-already-holds-lock variant of `save_state_with_snapshot`.
    Same contract: snapshot the pre-change file, atomic-write the new
    state, install the hash. Used by routes doing one-critical-section-
    per-op patterns.

    No internal lock acquisition; the caller MUST hold
    `app.state.save_lock`.
    """
    if writes_frozen:
        raise WritesFrozenError(
            "writes are frozen due to an unresolved external-edit conflict"
        )
    serialized = serialize_state(state)
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
    "save_state_locked",
    "save_state_sync",
    "save_state_with_snapshot",
    "save_state_with_snapshot_locked",
    "serialize_state",
    "snapshot_before_destructive",
]
