from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

DEFAULT_SELF_HEAL_LOG = "state/self_heal.jsonl"
_LOCK = threading.Lock()


def resolve_self_heal_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SELF_HEAL_LOG", DEFAULT_SELF_HEAL_LOG, path)


def append_self_heal_event(event: dict[str, Any], *, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_self_heal_log_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max(64 * 1024, int(os.getenv("SPIRITKIN_SELF_HEAL_LOG_MAX_BYTES", str(4 * 1024 * 1024))))
    record = {"schema_version": "spiritkin.self_heal.v1", "at": time.time(), **dict(event or {})}
    encoded = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    try:
        with _LOCK:
            if target.exists() and target.stat().st_size + len(encoded) > max_bytes:
                rotated = target.with_suffix(target.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                target.replace(rotated)
            with target.open("ab") as stream:
                stream.write(encoded)
    except OSError as exc:
        record["log_error"] = str(exc)
    return record
