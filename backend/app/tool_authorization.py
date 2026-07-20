from __future__ import annotations

from typing import Any

from backend.security.tool_authz import ToolAuthzRegistry
from backend.tools.registry import build_default_tool_registry


def _registries_with_current_tools() -> tuple[ToolAuthzRegistry, Any]:
    tool_registry = build_default_tool_registry()
    tools = tool_registry.list_specs()
    registry = ToolAuthzRegistry()
    registry.ensure_tools(tools, legacy_import=True)
    return registry, tool_registry


def build_tool_authorization_snapshot() -> dict[str, Any]:
    registry, tool_registry = _registries_with_current_tools()
    return {**registry.snapshot(), "manifest_discovery": tool_registry.manifest_discovery_snapshot()}


def handle_tool_authorization_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    registry, tool_registry = _registries_with_current_tools()
    if action in {"snapshot", "refresh", "list"}:
        return {
            "ok": True,
            "tool_authorization": {
                **registry.snapshot(),
                "manifest_discovery": tool_registry.manifest_discovery_snapshot(),
            },
        }
    if action in {"enable", "enable_tool", "disable", "disable_tool", "update", "update_tool"}:
        tool_id = str(payload.get("tool_id") or payload.get("name") or "").strip()
        if not tool_id:
            raise ValueError("tool_id is required")
        enabled = payload.get("enabled") if action in {"update", "update_tool"} else action in {"enable", "enable_tool"}
        entry = registry.update(
            tool_id,
            enabled=bool(enabled) if enabled is not None else None,
            risk=str(payload["risk"]) if payload.get("risk") is not None else None,
            confirmation_policy=str(payload["confirmation_policy"]) if payload.get("confirmation_policy") is not None else None,
        )
        return {
            "ok": True,
            "entry": entry,
            "tool_authorization": {
                **registry.snapshot(),
                "manifest_discovery": tool_registry.manifest_discovery_snapshot(),
            },
        }
    raise ValueError(f"unsupported tool authorization action: {action}")
