"""Shared zero-domain-logic state helpers (path resolution, JSON read/write,
timestamp, id slugging).

Lives at the backend package root, below the app/orchestrator boundary, so both
layers can use it without creating upward imports.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class StateCorruptionError(RuntimeError):
    """Raised when durable state exists but cannot be decoded safely."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"state corruption at {path}: {reason}")
        self.path = path
        self.reason = reason


def _lock_for_path(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def locked_state_path(path: Path):
    """Serialize state-file access across threads and local processes."""

    with _lock_for_path(path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if sys.platform.startswith("win"):
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except ImportError:
                    pass
            try:
                yield
            finally:
                handle.seek(0)
                if sys.platform.startswith("win"):
                    import msvcrt

                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    try:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except (ImportError, OSError):
                        pass


def now_ts() -> float:
    return time.time()


def resolve_state_path(env_key: str, default: str, path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv(env_key, default)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def resolve_workspace_path(raw: str | os.PathLike[str]) -> Path:
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def read_json_state(
    path: Path,
    fallback: dict[str, Any] | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise StateCorruptionError(path, f"{type(exc).__name__}: {exc}") from exc
        return dict(fallback or {})
    if isinstance(payload, dict):
        return payload
    if strict:
        raise StateCorruptionError(path, "top-level JSON value must be an object")
    return dict(fallback or {})


def write_json_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_id(value: str, fallback: str = "item") -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80] or f"{fallback}-{int(now_ts())}"
