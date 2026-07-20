"""Persistent, risk-aware authorization for registered tools.

This registry is deliberately a separate gate from skill and agent scopes:
those scopes answer whether an agent may select a tool, while this registry
lets an operator disable a registered tool across every caller.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.state_store import now_ts, read_json_state, resolve_state_path, write_json_state

SCHEMA_VERSION = "spiritkin.tool_authz.v1"
DEFAULT_TOOL_AUTHZ_PATH = "config/tool_authz.json"
RISK_LEVELS = {"safe", "network", "shell", "fs-write"}
CONFIRMATION_POLICIES = {"never", "once", "always"}


@dataclass(frozen=True)
class ToolAuthzDecision:
    allowed: bool
    tool_id: str
    reason: str
    risk: str
    confirmation_required: bool = False
    confirmation_policy: str = "never"

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "tool_id": self.tool_id,
            "reason": self.reason,
            "risk": self.risk,
            "confirmation_required": self.confirmation_required,
            "confirmation_policy": self.confirmation_policy,
        }


def resolve_tool_authz_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_TOOL_AUTHZ_PATH", DEFAULT_TOOL_AUTHZ_PATH, path)


def infer_tool_risk(spec: Any) -> str:
    """Map the existing ToolSpec vocabulary onto M3's four stable risks."""
    explicit = str(getattr(spec, "authz_risk", "") or "").strip().lower()
    if explicit in RISK_LEVELS:
        return explicit
    if bool(getattr(spec, "read_only", False)):
        return "safe"
    text = " ".join(
        str(value or "").lower()
        for value in (
            getattr(spec, "name", ""),
            getattr(spec, "target", ""),
            getattr(spec, "operation", ""),
        )
    )
    if any(token in text for token in ("shell", "terminal", "powershell", "python", "git", "ffmpeg", "cmd", "game_automation")):
        return "shell"
    if any(token in text for token in ("file", "knowledge", "kb.", "artifact", "workflow", "ecommerce")):
        return "fs-write"
    if any(token in text for token in ("web", "browser", "feishu", "mcp", "remote", "android", "openclaw", "network")):
        return "network"
    return "safe" if str(getattr(spec, "risk_level", "low")).lower() == "low" else "fs-write"


def default_confirmation_policy(risk: str) -> str:
    normalized = str(risk or "safe").strip().lower()
    if normalized == "shell":
        return "always"
    if normalized in {"network", "fs-write"}:
        return "once"
    return "never"


class ToolAuthzRegistry:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = resolve_tool_authz_path(path)
        self._state = self._load()
        self._last_mtime_ns = self._mtime_ns()

    def _default_state(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "entries": [], "updated_at": 0.0}

    def _mtime_ns(self) -> int:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return 0

    def _load(self) -> dict[str, Any]:
        state = read_json_state(self.path, self._default_state())
        entries = state.get("entries")
        state["schema_version"] = SCHEMA_VERSION
        state["entries"] = [self._normalize_entry(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
        state["updated_at"] = float(state.get("updated_at") or 0.0)
        return state

    def _reload_if_changed(self) -> None:
        mtime_ns = self._mtime_ns()
        if mtime_ns and mtime_ns != self._last_mtime_ns:
            self._state = self._load()
            self._last_mtime_ns = mtime_ns

    def _save(self) -> None:
        self._state["schema_version"] = SCHEMA_VERSION
        self._state["entries"] = sorted((self._normalize_entry(item) for item in self._state["entries"]), key=lambda item: item["tool_id"])
        self._state["updated_at"] = now_ts()
        write_json_state(self.path, self._state)
        self._last_mtime_ns = self._mtime_ns()

    @staticmethod
    def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
        tool_id = str(raw.get("tool_id") or raw.get("name") or "").strip()
        risk = str(raw.get("risk") or "safe").strip().lower()
        if risk not in RISK_LEVELS:
            risk = "safe"
        confirmation = str(raw.get("confirmation_policy") or default_confirmation_policy(risk)).strip().lower()
        if confirmation not in CONFIRMATION_POLICIES:
            confirmation = default_confirmation_policy(risk)
        return {
            "tool_id": tool_id,
            "enabled": bool(raw.get("enabled", True)),
            "risk": risk,
            "confirmation_policy": confirmation,
            "source": str(raw.get("source") or "registry"),
            "updated_at": float(raw.get("updated_at") or 0.0),
        }

    def _entry(self, tool_id: str) -> dict[str, Any] | None:
        return next((item for item in self._state["entries"] if item["tool_id"] == tool_id), None)

    def ensure_tool(self, spec: Any, *, legacy_import: bool = False) -> dict[str, Any]:
        self._reload_if_changed()
        tool_id = str(getattr(spec, "name", "") or "").strip()
        if not tool_id:
            raise ValueError("tool spec requires a name")
        existing = self._entry(tool_id)
        if existing is not None:
            return dict(existing)
        risk = infer_tool_risk(spec)
        entry = {
            "tool_id": tool_id,
            "enabled": True,
            "risk": risk,
            # Existing tools are imported without changing their operational
            # behavior; newly registered tools receive the M3 default policy.
            "confirmation_policy": "never" if legacy_import else default_confirmation_policy(risk),
            "source": "legacy_import" if legacy_import else "registry",
            "updated_at": now_ts(),
        }
        self._state["entries"].append(entry)
        self._save()
        return dict(entry)

    def ensure_tools(self, specs: Iterable[Any], *, legacy_import: bool = False) -> None:
        missing = [spec for spec in specs if str(getattr(spec, "name", "") or "").strip() and self._entry(str(getattr(spec, "name", "")).strip()) is None]
        if not missing:
            return
        for spec in missing:
            tool_id = str(getattr(spec, "name", "")).strip()
            risk = infer_tool_risk(spec)
            self._state["entries"].append(
                {
                    "tool_id": tool_id,
                    "enabled": True,
                    "risk": risk,
                    "confirmation_policy": "never" if legacy_import else default_confirmation_policy(risk),
                    "source": "legacy_import" if legacy_import else "registry",
                    "updated_at": now_ts(),
                }
            )
        self._save()

    def evaluate(self, tool_id: str, context: dict[str, Any] | None = None, *, fallback_risk: str = "safe") -> ToolAuthzDecision:
        self._reload_if_changed()
        normalized_id = str(tool_id or "").strip()
        entry = self._entry(normalized_id)
        if entry is None:
            risk = fallback_risk if fallback_risk in RISK_LEVELS else "safe"
            return ToolAuthzDecision(False, normalized_id, "tool_not_registered_in_authz", risk)
        if not bool(entry["enabled"]):
            return ToolAuthzDecision(False, normalized_id, "tool_disabled_by_operator", entry["risk"], confirmation_policy=entry["confirmation_policy"])
        context = dict(context or {})
        confirmation = entry["confirmation_policy"]
        confirmed_tools = {str(item).strip() for item in context.get("session_confirmed_tools") or () if str(item).strip()}
        has_current_confirmation = bool(context.get("authz_confirmed") is True or normalized_id in confirmed_tools or entry["risk"] in confirmed_tools)
        if confirmation == "always" and not bool(context.get("authz_confirmed") is True):
            return ToolAuthzDecision(False, normalized_id, "tool_confirmation_required", entry["risk"], True, confirmation)
        if confirmation == "once" and not has_current_confirmation:
            return ToolAuthzDecision(False, normalized_id, "tool_confirmation_required", entry["risk"], True, confirmation)
        return ToolAuthzDecision(True, normalized_id, "tool_authorized", entry["risk"], False, confirmation)

    def update(self, tool_id: str, *, enabled: bool | None = None, risk: str | None = None, confirmation_policy: str | None = None) -> dict[str, Any]:
        self._reload_if_changed()
        entry = self._entry(str(tool_id or "").strip())
        if entry is None:
            raise ValueError(f"unknown tool authorization entry: {tool_id}")
        if enabled is not None:
            entry["enabled"] = bool(enabled)
        if risk is not None:
            normalized_risk = str(risk).strip().lower()
            if normalized_risk not in RISK_LEVELS:
                raise ValueError(f"unsupported tool risk: {risk}")
            entry["risk"] = normalized_risk
        if confirmation_policy is not None:
            normalized_policy = str(confirmation_policy).strip().lower()
            if normalized_policy not in CONFIRMATION_POLICIES:
                raise ValueError(f"unsupported confirmation policy: {confirmation_policy}")
            entry["confirmation_policy"] = normalized_policy
        entry["updated_at"] = now_ts()
        self._save()
        return dict(entry)

    def snapshot(self) -> dict[str, Any]:
        self._reload_if_changed()
        entries = [dict(item) for item in self._state["entries"]]
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(self.path),
            "entry_count": len(entries),
            "enabled_count": sum(1 for item in entries if item["enabled"]),
            "disabled_count": sum(1 for item in entries if not item["enabled"]),
            "entries": entries,
            "updated_at": self._state["updated_at"],
            "risk_policy": {"safe": "never", "network": "once", "fs-write": "once", "shell": "always"},
        }
