"""Watchdog-based file watcher for `clipfarm.json`.

The watcher exists so external edits to `clipfarm.json` (hand-edit in an
editor, restoring a snapshot) get picked up by the running app without a
restart. The two hard things it does:

1. **Self-write filtering.** Every time the app saves, it records the hash of
   what it just wrote. The watchdog event handler reads the file, hashes the
   contents, and compares — match = our own write, ignore. Mismatch = external
   edit.
2. **Conflict detection.** If the in-memory state has unsaved changes when an
   external write lands, we surface a conflict event so the UI can prompt
   "keep yours / keep theirs / merge by hand." Phase 1 just *detects and logs*
   conflicts — surfacing the prompt is later.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from clipfarm.store import hash_serialized

# Polling rather than FSEvents/inotify. FSEvents on macOS has documented
# reliability issues with rapid back-to-back edits to a single file — events
# can be coalesced or dropped entirely. The state file is tiny (single
# `stat()` per poll) and the polling interval below is short enough that a
# hand-edit feels instant. Pin the implementation rather than letting platform
# defaults drift between machines.
_WATCH_POLL_INTERVAL_SEC = 0.5

log = logging.getLogger(__name__)


@dataclass
class WatcherCallbacks:
    """Callbacks the app registers with the watcher.

    - `on_external_change(path)`: an external write was detected (file hash
      changed from what we last wrote). The app should reload from disk.
    - `on_conflict(path)`: an external write landed AND the in-memory state
      has unsaved changes. The app should surface a conflict UI.
    - `has_unsaved_changes()`: lets the watcher ask the app whether reloading
      would clobber in-memory edits. Returning True routes to `on_conflict`
      instead of `on_external_change`.
    """

    on_external_change: Callable[[Path], None]
    on_conflict: Callable[[Path], None]
    has_unsaved_changes: Callable[[], bool]


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        target_path: Path,
        callbacks: WatcherCallbacks,
        get_last_known_hash: Callable[[], Optional[str]],
        lock: threading.Lock,
    ) -> None:
        super().__init__()
        self._target = target_path.resolve()
        self._callbacks = callbacks
        self._get_last_known_hash = get_last_known_hash
        self._lock = lock

    def _event_targets_state_file(self, event: FileSystemEvent) -> bool:
        try:
            return Path(event.src_path).resolve() == self._target
        except (OSError, ValueError):
            return False

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._event_targets_state_file(event):
            return
        self._maybe_fire_change()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not self._event_targets_state_file(event):
            return
        self._maybe_fire_change()

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        try:
            dest = Path(getattr(event, "dest_path", "")).resolve()
        except (OSError, ValueError):
            return
        if dest == self._target:
            self._maybe_fire_change()

    def _maybe_fire_change(self) -> None:
        with self._lock:
            try:
                contents = self._target.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError) as e:
                log.debug("watcher: read failed (%s); ignoring event", e)
                return
            current_hash = hash_serialized(contents)
            last_known = self._get_last_known_hash()
            log.debug(
                "watcher: event fired, current_hash=%s last_known=%s",
                current_hash[:8],
                (last_known or "<none>")[:8],
            )
            if last_known is not None and current_hash == last_known:
                # Our own write echoing back through the OS event loop.
                log.debug("watcher: filtered as self-write")
                return
            if self._callbacks.has_unsaved_changes():
                log.warning(
                    "watcher: external write to %s while in-memory state is dirty",
                    self._target,
                )
                self._callbacks.on_conflict(self._target)
            else:
                log.info("watcher: external write to %s — reloading", self._target)
                self._callbacks.on_external_change(self._target)


class StateFileWatcher:
    """Wraps watchdog. One observer per app instance."""

    def __init__(self, state_path: Path, callbacks: WatcherCallbacks) -> None:
        self._state_path = state_path
        self._callbacks = callbacks
        self._last_known_hash: Optional[str] = None
        # RLock not Lock: `_maybe_fire_change` holds the lock while invoking
        # `on_external_change`, which calls back into `update_last_known_hash`,
        # which acquires the same lock. With a plain Lock that's a deadlock —
        # the watchdog thread hangs after the first event. RLock is reentrant
        # so the same thread can re-acquire safely.
        self._lock = threading.RLock()
        self._observer: Optional[PollingObserver] = None

    def update_last_known_hash(self, new_hash: Optional[str]) -> None:
        """Called by the app every time it writes the state file. The next
        watchdog event with this hash gets filtered as a self-write."""
        with self._lock:
            self._last_known_hash = new_hash

    def _get_last_known_hash(self) -> Optional[str]:
        return self._last_known_hash

    def start(self) -> None:
        if self._observer is not None:
            return
        # Watch the *directory* — a not-yet-existing file can't be watched
        # directly, and the directory is stable.
        watch_dir = self._state_path.parent.resolve()
        watch_dir.mkdir(parents=True, exist_ok=True)
        handler = _Handler(
            self._state_path,
            self._callbacks,
            self._get_last_known_hash,
            self._lock,
        )
        observer = PollingObserver(timeout=_WATCH_POLL_INTERVAL_SEC)
        observer.schedule(handler, str(watch_dir), recursive=False)
        observer.start()
        self._observer = observer
        log.info("watcher: started on %s", watch_dir)

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
        log.info("watcher: stopped")
