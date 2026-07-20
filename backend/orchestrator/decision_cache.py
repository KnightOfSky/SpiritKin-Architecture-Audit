"""Decision-path reuse cache for repeated, successful operational requests.

The goal is to avoid burning cloud LLM tokens re-resolving intents that have
already been resolved *and executed successfully* before. We cache the pure,
JSON-serializable decision (``target``/``operation``/``params`` — i.e. an
``ExecutionRequest``) rather than raw natural-language text, because the
decision is more stable and less prone to staleness.

Safety invariants (guarded by tests):
- Disabled by default (``SPIRITKIN_DECISION_CACHE_ENABLED`` off). When disabled
  every method is a no-op and behavior is identical to not having the cache.
- A hit only replays the *decision*; it is still routed through
  ``_handle_execution`` so ``ExecutionGuard`` confirmation is NOT bypassed.
- A fingerprint is only "hittable" after it has succeeded at least
  ``SPIRITKIN_DECISION_CACHE_MIN_SUCCESS`` times with no more-recent failure.

Persistence reuses ``state_store`` JSON helpers (no bespoke IO).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.state_store import (
    now_ts,
    read_json_state,
    resolve_state_path,
    write_json_state,
)

_ENABLED_ENV = "SPIRITKIN_DECISION_CACHE_ENABLED"
_MIN_SUCCESS_ENV = "SPIRITKIN_DECISION_CACHE_MIN_SUCCESS"
_PATH_ENV = "SPIRITKIN_DECISION_CACHE_PATH"
_DEFAULT_PATH = "runtime/decision_cache_state.json"
_DEFAULT_MIN_SUCCESS = 2

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_WHITESPACE = re.compile(r"\s+")


def _env_enabled() -> bool:
    return str(os.getenv(_ENABLED_ENV, "")).strip().lower() in _TRUE_VALUES


def _env_min_success() -> int:
    raw = str(os.getenv(_MIN_SUCCESS_ENV, "")).strip()
    if not raw:
        return _DEFAULT_MIN_SUCCESS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_SUCCESS
    return value if value >= 1 else _DEFAULT_MIN_SUCCESS


@dataclass(frozen=True)
class CachedDecision:
    target: str
    operation: str
    params: dict[str, Any]
    success_count: int
    fingerprint: str


class DecisionCache:
    def __init__(
        self,
        *,
        path: str | os.PathLike[str] | None = None,
        enabled: bool | None = None,
        min_success: int | None = None,
    ) -> None:
        self._enabled = _env_enabled() if enabled is None else bool(enabled)
        self._min_success = _env_min_success() if min_success is None else max(1, int(min_success))
        self._path: Path = resolve_state_path(_PATH_ENV, _DEFAULT_PATH, path=path)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def fingerprint(user_input: str, channel: str = "", agent: str = "") -> str:
        normalized = _WHITESPACE.sub(" ", str(user_input or "").strip().lower())
        channel_key = str(channel or "").strip().lower()
        agent_key = str(agent or "").strip().lower()
        digest_source = f"{channel_key}␟{agent_key}␟{normalized}"
        return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Any]:
        state = read_json_state(self._path, {"entries": {}})
        entries = state.get("entries")
        if not isinstance(entries, dict):
            state["entries"] = {}
        return state

    def _save(self, state: dict[str, Any]) -> None:
        write_json_state(self._path, state)

    def lookup(self, fingerprint: str) -> CachedDecision | None:
        if not self._enabled or not fingerprint:
            return None
        entry = self._load().get("entries", {}).get(fingerprint)
        if not isinstance(entry, dict):
            return None
        success_count = int(entry.get("success_count") or 0)
        if success_count < self._min_success:
            return None
        last_success = float(entry.get("last_success_ts") or 0.0)
        last_failure = float(entry.get("last_failure_ts") or 0.0)
        if last_failure and last_failure >= last_success:
            return None
        target = str(entry.get("target") or "").strip()
        operation = str(entry.get("operation") or "").strip()
        if not target or not operation:
            return None
        params = entry.get("params")
        return CachedDecision(
            target=target,
            operation=operation,
            params=dict(params) if isinstance(params, dict) else {},
            success_count=success_count,
            fingerprint=fingerprint,
        )

    def record_success(
        self,
        fingerprint: str,
        *,
        target: str,
        operation: str,
        params: dict[str, Any] | None = None,
        user_input: str = "",
        channel: str = "",
        agent: str = "",
    ) -> None:
        if not self._enabled or not fingerprint:
            return
        target = str(target or "").strip()
        operation = str(operation or "").strip()
        if not target or not operation:
            return
        state = self._load()
        entries = state["entries"]
        existing = entries.get(fingerprint) if isinstance(entries.get(fingerprint), dict) else {}
        prior_count = int(existing.get("success_count") or 0)
        entries[fingerprint] = {
            "target": target,
            "operation": operation,
            "params": dict(params or {}),
            "success_count": prior_count + 1,
            "last_success_ts": now_ts(),
            # 成功即冲销此前的失败：靠时间戳比较判"更近的失败"在 Windows 上
            # 会因 time.time() 同刻度而随机误判（同 tick 时 failure >= success）。
            "last_failure_ts": 0.0,
            "user_input": str(user_input or existing.get("user_input") or "")[:400],
            "channel": str(channel or existing.get("channel") or ""),
            "agent": str(agent or existing.get("agent") or ""),
        }
        self._save(state)

    def record_failure(self, fingerprint: str) -> None:
        if not self._enabled or not fingerprint:
            return
        state = self._load()
        entries = state["entries"]
        existing = entries.get(fingerprint)
        if not isinstance(existing, dict):
            return
        existing["last_failure_ts"] = now_ts()
        self._save(state)
