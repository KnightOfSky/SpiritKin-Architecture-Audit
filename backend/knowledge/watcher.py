from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileChangeEvent:
    path: str
    event_type: str
    timestamp: float
    size_bytes: int = 0


class DirectoryWatcher:
    def __init__(self, root: str | Path, *, interval_seconds: float = 30.0):
        self._root = Path(root).resolve()
        self._interval = max(1.0, interval_seconds)
        self._snapshot: dict[str, float] = {}

    def _build_snapshot(self, extensions: set[str] | None = None) -> dict[str, float]:
        allowed = {ext.lower() for ext in (extensions or {".md", ".txt", ".rst"})}
        snapshot: dict[str, float] = {}
        if not self._root.exists() or not self._root.is_dir():
            return snapshot
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed:
                continue
            try:
                stat = path.stat()
                snapshot[path.as_posix()] = stat.st_mtime
            except OSError:
                continue
        return snapshot

    def poll(self, extensions: set[str] | None = None) -> list[FileChangeEvent]:
        current = self._build_snapshot(extensions)
        events: list[FileChangeEvent] = []
        now = time.time()

        for path, mtime in current.items():
            if path not in self._snapshot:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                events.append(FileChangeEvent(path=path, event_type="created", timestamp=now, size_bytes=size))
            elif mtime > self._snapshot[path]:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                events.append(FileChangeEvent(path=path, event_type="modified", timestamp=now, size_bytes=size))

        for path in self._snapshot:
            if path not in current:
                events.append(FileChangeEvent(path=path, event_type="deleted", timestamp=now))

        self._snapshot = current
        return events

    def watch(self, extensions: set[str] | None = None) -> Iterator[list[FileChangeEvent]]:
        self._snapshot = self._build_snapshot(extensions)
        while True:
            time.sleep(self._interval)
            events = self.poll(extensions)
            yield events
