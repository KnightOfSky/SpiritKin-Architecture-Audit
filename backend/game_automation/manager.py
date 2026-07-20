from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.game_automation.manifest import configured_game_adapter_allowlist, load_game_adapter_manifests
from backend.game_automation.playwright_driver import PlaywrightGameDriver
from backend.game_automation.session import (
    GameAutomationAudit,
    GameAutomationSession,
    GameAutomationStep,
)


class GameAutomationManager:
    def __init__(
        self,
        *,
        manifest_root: str | os.PathLike[str] | None = None,
        allowlist: frozenset[str] | None = None,
        driver_factory: Callable[..., Any] = PlaywrightGameDriver,
        audit_root: str | os.PathLike[str] | None = None,
    ):
        self.manifests = load_game_adapter_manifests(manifest_root)
        self.allowlist = configured_game_adapter_allowlist() if allowlist is None else allowlist
        self.driver_factory = driver_factory
        self.audit_root = Path(audit_root or os.getenv("SPIRITKIN_GAME_AUDIT_ROOT") or "state/game_automation/audit").resolve()
        self._sessions: dict[str, GameAutomationSession] = {}
        self._lock = threading.RLock()

    def run_plan(
        self,
        *,
        adapter_id: str,
        url: str,
        steps: list[dict[str, Any]],
        session_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        normalized_session_id = self._validate_session_id(session_id)
        adapter = self.manifests.get(adapter_id)
        if adapter is None:
            raise ValueError(f"unknown game adapter: {adapter_id}")
        if adapter_id not in self.allowlist:
            raise PermissionError(f"game adapter is not allowlisted: {adapter_id}")
        if not adapter.accepts_url(url):
            raise PermissionError("game URL is outside the adapter target")
        plan = self._parse_steps(steps)
        driver = self.driver_factory(url, headless=headless, artifact_dir=self.audit_root.parent / "frames" / normalized_session_id)
        audit = GameAutomationAudit(self.audit_root / f"{normalized_session_id}.jsonl")
        session = GameAutomationSession(
            adapter,
            driver,
            audit=audit,
            session_id=normalized_session_id,
            authorized=lambda _action, _params: True,
        )
        with self._lock:
            if normalized_session_id in self._sessions:
                driver.close()
                raise ValueError(f"game automation session already exists: {normalized_session_id}")
            self._sessions[normalized_session_id] = session
        try:
            status = session.run(url, plan)
            events = audit.replay()
            return {
                "session_id": normalized_session_id,
                "adapter_id": adapter_id,
                "status": status.value,
                "stop_reason": session.stop_reason,
                "audit_path": str(audit.path),
                "audit_event_count": len(events),
                "action_count": sum(1 for event in events if event.get("type") == "action.completed"),
            }
        finally:
            session.close()
            with self._lock:
                self._sessions.pop(normalized_session_id, None)

    def stop(self, session_id: str, reason: str = "user_stop") -> bool:
        with self._lock:
            session = self._sessions.get(str(session_id or "").strip())
        if session is None:
            return False
        session.request_stop(reason)
        return True

    def active_sessions(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._sessions))

    @staticmethod
    def _validate_session_id(value: str) -> str:
        normalized = str(value or "").strip()
        if not re.fullmatch(r"game_session_[a-zA-Z0-9_-]{4,80}", normalized):
            raise ValueError("game session id must use game_session_* with safe characters")
        return normalized

    @staticmethod
    def _parse_steps(raw_steps: list[dict[str, Any]]) -> list[GameAutomationStep]:
        if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 2000:
            raise ValueError("game automation plan must contain 1 to 2000 steps")
        steps: list[GameAutomationStep] = []
        total_delay = 0
        for raw in raw_steps:
            if not isinstance(raw, dict):
                raise ValueError("game automation steps must be objects")
            delay = max(0, min(5000, int(raw.get("delay_after_ms") or 100)))
            total_delay += delay
            steps.append(GameAutomationStep(str(raw.get("action") or "").strip(), dict(raw.get("params") or {}), delay))
        if total_delay > 5 * 60 * 1000:
            raise ValueError("game automation plan cannot exceed five minutes of scheduled delay")
        return steps


_DEFAULT_MANAGER: GameAutomationManager | None = None
_DEFAULT_MANAGER_LOCK = threading.RLock()


def get_game_automation_manager() -> GameAutomationManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = GameAutomationManager()
        return _DEFAULT_MANAGER


def reset_game_automation_manager() -> None:
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        for session_id in _DEFAULT_MANAGER.active_sessions() if _DEFAULT_MANAGER is not None else ():
            _DEFAULT_MANAGER.stop(session_id, "manager_reset")
        _DEFAULT_MANAGER = None
