from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib import parse

from backend.state_store import now_ts, read_json_state, resolve_state_path, safe_id, write_json_state

SCHEMA_VERSION = "spiritkin.mcp_management.v2"
DEFAULT_MCP_REGISTRY_PATH = "state/mcp/registry.json"
SUPPORTED_TRANSPORTS = {"stdio", "sse", "http"}
RISK_LEVELS = {"low", "medium", "high"}
APPROVED_REVIEW_STATES = {"approved", "active"}
AUDIT_LIMIT = 120
SENSITIVE_HEADER_NAMES = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
RUNTIME_FAILURE_THRESHOLD = 3


def _runtime_health(server: dict[str, Any]) -> dict[str, Any]:
    raw = _dict_value(server.get("runtime_health"))
    failures = _int_value(raw.get("consecutive_failures"), 0, minimum=0, maximum=9999)
    status = str(raw.get("status") or "unknown").strip().lower()
    if failures >= RUNTIME_FAILURE_THRESHOLD:
        status = "unavailable"
    elif status not in {"unknown", "available", "degraded", "unavailable"}:
        status = "unknown"
    return {
        "status": status,
        "consecutive_failures": failures,
        "last_success_at": _float_value(raw.get("last_success_at"), 0.0, minimum=0.0, maximum=9_999_999_999.0),
        "last_failure_at": _float_value(raw.get("last_failure_at"), 0.0, minimum=0.0, maximum=9_999_999_999.0),
        "last_error": str(raw.get("last_error") or "")[:300],
        "last_transition": str(raw.get("last_transition") or ""),
    }


def resolve_mcp_registry_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MCP_REGISTRY_PATH", DEFAULT_MCP_REGISTRY_PATH, path)


def _now() -> float:
    return now_ts()


def _safe_id(value: str, fallback: str = "mcp-server") -> str:
    return safe_id(value, fallback)


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    return read_json_state(path, fallback)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_state(path, payload)


def _default_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "servers": [], "audit_log": [], "updated_at": 0.0}


def load_mcp_registry(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state = _load_json(resolve_mcp_registry_path(path), _default_state())
    servers = state.get("servers")
    audit_log = state.get("audit_log")
    state["schema_version"] = SCHEMA_VERSION
    state["servers"] = [dict(item) for item in servers if isinstance(item, dict)] if isinstance(servers, list) else []
    state["audit_log"] = [dict(item) for item in audit_log if isinstance(item, dict)][-AUDIT_LIMIT:] if isinstance(audit_log, list) else []
    state.setdefault("updated_at", 0.0)
    return state


def save_mcp_registry(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state["schema_version"] = SCHEMA_VERSION
    state["audit_log"] = [dict(item) for item in state.get("audit_log") or [] if isinstance(item, dict)][-AUDIT_LIMIT:]
    state["updated_at"] = _now()
    _save_json(resolve_mcp_registry_path(path), state)
    return state


def _server_by_id(state: dict[str, Any], server_id: str) -> dict[str, Any] | None:
    return next((item for item in state.get("servers", []) if str(item.get("server_id") or "") == server_id), None)


def _payload_server_id(payload: dict[str, Any]) -> str:
    raw = str(payload.get("server_id") or payload.get("id") or "").strip()
    if not raw:
        raise ValueError("server_id is required")
    return _safe_id(raw)


def _bool_value(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


_HEADER_ENV_REF = re.compile(r"^\s*([^<>=:\s]+)\s*(?:<-|=|:)\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


def _header_env_from_refs(value: Any) -> dict[str, str]:
    """Accept auth mappings from legacy desktop env_refs without storing secrets.

    The WPF client predates the explicit ``header_env`` field and already sends
    ``env_refs``.  A declaration such as ``Authorization <- MCP_TOKEN`` is
    interpreted as a header-to-environment-name mapping; plain names remain
    ordinary process environment references for stdio servers.
    """
    result: dict[str, str] = {}
    for item in _string_list(value):
        match = _HEADER_ENV_REF.match(item)
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
    return result


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _float_value(value: Any, fallback: float, *, minimum: float, maximum: float) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = fallback
    return max(minimum, min(maximum, resolved))


def _int_value(value: Any, fallback: int, *, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = fallback
    return max(minimum, min(maximum, resolved))


def _url_host(url: str) -> str:
    try:
        return parse.urlparse(url).hostname or ""
    except Exception:
        return ""


def _append_audit(
    state: dict[str, Any],
    action: str,
    *,
    server_id: str = "",
    actor: str = "desktop",
    success: bool = True,
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "at": _now(),
        "action": action,
        "server_id": server_id,
        "actor": actor or "desktop",
        "success": bool(success),
        "message": message,
        "metadata": dict(metadata or {}),
    }
    state["audit_log"] = [*list(state.get("audit_log") or []), event][-AUDIT_LIMIT:]
    return event


def _normalize_tool(server_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    mcp_tool = str(raw.get("mcp_tool_name") or raw.get("name") or raw.get("tool_name") or "").strip()
    internal_name = str(raw.get("internal_tool_name") or raw.get("internal_name") or "").strip()
    if not internal_name and mcp_tool:
        internal_name = f"mcp.{_safe_id(server_id)}.{_safe_id(mcp_tool)}"
    risk = str(raw.get("risk_level") or "medium").strip().lower()
    if risk not in RISK_LEVELS:
        risk = "medium"
    return {
        "mcp_tool_name": mcp_tool,
        "internal_tool_name": internal_name,
        "description": str(raw.get("description") or ""),
        "target": str(raw.get("target") or "mcp"),
        "operation": str(raw.get("operation") or mcp_tool),
        "risk_level": risk,
        "read_only": _bool_value(raw.get("read_only"), False),
        "confirmation_required": _bool_value(raw.get("confirmation_required"), risk != "low"),
        "schema_override": dict(raw.get("schema_override") or raw.get("schema") or {}) if isinstance(raw.get("schema_override") or raw.get("schema"), dict) else {},
        "enabled": _bool_value(raw.get("enabled"), True),
    }


def _normalize_server(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = dict(existing or {})
    server_id = _safe_id(str(payload.get("server_id") or payload.get("id") or existing.get("server_id") or payload.get("label") or payload.get("url") or payload.get("command") or "mcp-server"))
    transport = str(payload.get("transport") or existing.get("transport") or "stdio").strip().lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(f"unsupported MCP transport: {transport}")
    command = str(payload.get("command") if payload.get("command") is not None else existing.get("command") or "").strip()
    url = str(payload.get("url") if payload.get("url") is not None else existing.get("url") or "").strip()
    if transport == "stdio" and not command:
        raise ValueError("stdio MCP server requires command")
    if transport in {"sse", "http"} and not url:
        raise ValueError(f"{transport} MCP server requires url")
    env_refs = payload.get("env_refs") if "env_refs" in payload else existing.get("env_refs", [])
    headers = payload.get("headers") if "headers" in payload else existing.get("headers", {})
    header_env = payload.get("header_env") if "header_env" in payload else existing.get("header_env", {})
    filesystem_scopes = payload.get("filesystem_scopes") if "filesystem_scopes" in payload else existing.get("filesystem_scopes", [])
    network_scopes = payload.get("network_scopes") if "network_scopes" in payload else existing.get("network_scopes", [])
    resources = payload.get("resources") if "resources" in payload else existing.get("resources", [])
    prompts = payload.get("prompts") if "prompts" in payload else existing.get("prompts", [])
    tool_payloads = payload.get("tools") if "tools" in payload else existing.get("tools", [])
    tools = [_normalize_tool(server_id, item) for item in _dict_list(tool_payloads)]
    normalized_headers = {str(key).strip(): str(value).strip() for key, value in _dict_value(headers).items() if str(key).strip() and str(value).strip()}
    sensitive_headers = [key for key in normalized_headers if key.lower() in SENSITIVE_HEADER_NAMES or "token" in key.lower()]
    if sensitive_headers:
        raise ValueError(f"sensitive MCP headers must use header_env: {', '.join(sorted(sensitive_headers))}")
    normalized_header_env = _header_env_from_refs(env_refs)
    normalized_header_env.update({str(key).strip(): str(value).strip() for key, value in _dict_value(header_env).items() if str(key).strip() and str(value).strip()})
    normalized_env_refs = list(dict.fromkeys([*_string_list(env_refs), *normalized_header_env.values()]))
    runtime_health = _runtime_health(existing)
    requested_review_state = str(payload.get("review_state") or existing.get("review_state") or "candidate").strip().lower()
    if requested_review_state not in {"candidate", "approved", "active", "rejected"}:
        requested_review_state = "candidate"
    requested_trust = str(payload.get("trust_level") or existing.get("trust_level") or "untrusted").strip().lower()
    if requested_trust not in {"untrusted", "verified", "trusted"}:
        requested_trust = "untrusted"
    return {
        "server_id": server_id,
        "label": str(payload.get("label") or existing.get("label") or server_id),
        "transport": transport,
        "command": command,
        "args": _string_list(payload.get("args") if "args" in payload else existing.get("args", [])),
        "url": url,
        "enabled": _bool_value(payload.get("enabled") if "enabled" in payload else existing.get("enabled"), False),
        "review_state": requested_review_state,
        "trust_level": requested_trust,
        "workspace_scope": str(payload.get("workspace_scope") or existing.get("workspace_scope") or "project").strip().lower(),
        "owner_agent_ids": _string_list(payload.get("owner_agent_ids") if "owner_agent_ids" in payload else existing.get("owner_agent_ids", [])),
        "env_refs": normalized_env_refs,
        "headers": normalized_headers,
        "header_env": normalized_header_env,
        "timeout_seconds": _float_value(
            payload.get("timeout_seconds") if "timeout_seconds" in payload else existing.get("timeout_seconds"),
            30.0,
            minimum=1.0,
            maximum=120.0,
        ),
        "max_retries": _int_value(
            payload.get("max_retries") if "max_retries" in payload else existing.get("max_retries"),
            2,
            minimum=0,
            maximum=5,
        ),
        "runtime_health": runtime_health,
        "filesystem_scopes": _string_list(filesystem_scopes),
        "network_scopes": _string_list(network_scopes),
        "resources": _string_list(resources),
        "prompts": _string_list(prompts),
        "permissions": _dict_value(payload.get("permissions") if "permissions" in payload else existing.get("permissions", {})),
        "tools": tools,
        "notes": str(payload.get("notes") or existing.get("notes") or ""),
        "metadata": _dict_value(payload.get("metadata") or existing.get("metadata") or {}),
        "created_at": float(existing.get("created_at") or _now()),
        "updated_at": _now(),
    }


def _server_health(server: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    runtime = _runtime_health(server)
    transport = str(server.get("transport") or "")
    enabled = bool(server.get("enabled"))
    review_state = str(server.get("review_state") or "")
    if transport == "stdio" and not str(server.get("command") or "").strip():
        issues.append("missing_command")
    if transport in {"sse", "http"} and not str(server.get("url") or "").strip():
        issues.append("missing_url")
    if transport in {"sse", "http"} and parse.urlparse(str(server.get("url") or "")).scheme not in {"http", "https"}:
        issues.append("invalid_url_scheme")
    if transport in {"sse", "http"} and str(server.get("url") or "").startswith("http://") and _url_host(str(server.get("url") or "")) not in {"127.0.0.1", "localhost"}:
        issues.append("plain_http_remote")
    if enabled and review_state not in APPROVED_REVIEW_STATES:
        issues.append("enabled_without_review")
    if enabled and review_state == "rejected":
        issues.append("enabled_rejected_server")
    if enabled and not _string_list(server.get("owner_agent_ids")):
        issues.append("missing_agent_allowlist")
    if enabled and transport in {"sse", "http"} and _url_host(str(server.get("url") or "")) not in {"", "127.0.0.1", "localhost"} and not _string_list(server.get("network_scopes")):
        issues.append("remote_network_scope_missing")
    if not server.get("tools"):
        issues.append("no_declared_tools")
    for tool in _dict_list(server.get("tools")):
        if not bool(tool.get("enabled", True)):
            continue
        if not str(tool.get("mcp_tool_name") or "").strip():
            issues.append("tool_missing_name")
        if not str(tool.get("internal_tool_name") or "").strip():
            issues.append("tool_missing_internal_name")
        if str(tool.get("risk_level") or "medium").lower() == "high" and not bool(tool.get("confirmation_required", True)):
            issues.append("high_risk_tool_without_confirmation")
    if runtime["status"] == "unavailable":
        issues.append("runtime_unavailable")
    issues = sorted(set(issues))
    status = "ready" if enabled and not issues else ("disabled" if not enabled else "needs_attention")
    return {"status": status, "issues": issues, "runtime": runtime, "checked_at": _now()}


def _tool_mapping_entry(server: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "mcp_server": server["server_id"],
        "server_label": str(server.get("label") or server["server_id"]),
        "server_transport": str(server.get("transport") or ""),
        "mcp_tool_name": str(tool.get("mcp_tool_name") or ""),
        "internal_tool_name": str(tool.get("internal_tool_name") or ""),
        "target": str(tool.get("target") or "mcp"),
        "operation": str(tool.get("operation") or tool.get("mcp_tool_name") or ""),
        "risk_level": str(tool.get("risk_level") or "medium"),
        "read_only": bool(tool.get("read_only", False)),
        "confirmation_required": bool(tool.get("confirmation_required", True)),
        "schema_override": dict(tool.get("schema_override") or {}),
        "owner_agent_ids": _string_list(server.get("owner_agent_ids")),
        "workspace_scope": str(server.get("workspace_scope") or "project"),
        "command": str(server.get("command") or ""),
        "args": _string_list(server.get("args")),
        "env_refs": _string_list(server.get("env_refs")),
        "working_directory": str(server.get("metadata", {}).get("working_directory") or ""),
        "url": str(server.get("url") or ""),
        "headers": _dict_value(server.get("headers")),
        "header_env": _dict_value(server.get("header_env")),
        "timeout_seconds": _float_value(server.get("timeout_seconds"), 30.0, minimum=1.0, maximum=120.0),
        "max_retries": _int_value(server.get("max_retries"), 2, minimum=0, maximum=5),
    }


def build_mcp_management_snapshot() -> dict[str, Any]:
    state = load_mcp_registry()
    servers = []
    tool_mappings = []
    for raw in state["servers"]:
        server = dict(raw)
        server["health"] = _server_health(server)
        servers.append(server)
        server_ready = bool(server.get("enabled")) and str(server.get("review_state") or "") in APPROVED_REVIEW_STATES and server["health"].get("status") == "ready"
        if not server_ready:
            continue
        for tool in server.get("tools") or []:
            if not isinstance(tool, dict) or not bool(tool.get("enabled", True)):
                continue
            if not str(tool.get("mcp_tool_name") or "").strip() or not str(tool.get("internal_tool_name") or "").strip():
                continue
            tool_mappings.append(_tool_mapping_entry(server, tool))
    ready_count = sum(1 for item in servers if item.get("health", {}).get("status") == "ready")
    attention_count = sum(1 for item in servers if item.get("health", {}).get("status") == "needs_attention")
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_path": str(resolve_mcp_registry_path()),
        "server_count": len(servers),
        "enabled_count": sum(1 for item in servers if bool(item.get("enabled"))),
        "ready_count": ready_count,
        "attention_count": attention_count,
        "servers": servers,
        "tool_mappings": tool_mappings,
        "ready_mapping_count": len(tool_mappings),
        "audit_log": list(state.get("audit_log") or [])[-30:],
        "audit_count": len(state.get("audit_log") or []),
        "policy": {
            "external_launch_enabled": False,
            "requires_review_before_tool_export": True,
            "requires_enabled_server_before_tool_export": True,
            "requires_agent_allowlist": True,
            "tool_execution_mode": "transport_proxy_execution",
        },
        "updated_at": state.get("updated_at", 0.0),
    }


def _requires_connection_review(existing: dict[str, Any], server: dict[str, Any]) -> bool:
    for key in ("transport", "command", "args", "env_refs", "headers", "header_env", "filesystem_scopes", "network_scopes"):
        if server.get(key) != existing.get(key):
            return True
    old_url = str(existing.get("url") or "")
    new_url = str(server.get("url") or "")
    if old_url == new_url:
        return False
    old = parse.urlparse(old_url)
    new = parse.urlparse(new_url)
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    return not (
        (old.hostname or "").lower() in loopback_hosts
        and (new.hostname or "").lower() in loopback_hosts
        and old.scheme == new.scheme
        and old.path == new.path
        and old.params == new.params
        and old.query == new.query
    )


def save_mcp_server(payload: dict[str, Any]) -> dict[str, Any]:
    state = load_mcp_registry()
    server_id_seed = str(payload.get("server_id") or payload.get("id") or payload.get("label") or payload.get("url") or payload.get("command") or "")
    server_id = _safe_id(server_id_seed)
    existing = _server_by_id(state, server_id)
    server = _normalize_server(payload, existing)
    if existing is not None and _requires_connection_review(existing, server):
        server["enabled"] = False
        server["review_state"] = "candidate"
        server["trust_level"] = "untrusted"
    servers = [item for item in state["servers"] if str(item.get("server_id") or "") != server["server_id"]]
    servers.append(server)
    state["servers"] = sorted(servers, key=lambda item: str(item.get("server_id") or ""))
    _append_audit(
        state,
        "save_server",
        server_id=server["server_id"],
        actor=str(payload.get("reviewer") or payload.get("actor") or "desktop"),
        message="MCP server saved to registry",
        metadata={"transport": server["transport"], "tool_count": len(server.get("tools") or [])},
    )
    save_mcp_registry(state)
    return {"ok": True, "server": server, "mcp_management": build_mcp_management_snapshot()}


def delete_mcp_server(payload: dict[str, Any]) -> dict[str, Any]:
    server_id = _payload_server_id(payload)
    state = load_mcp_registry()
    before = len(state["servers"])
    existing = _server_by_id(state, server_id)
    state["servers"] = [item for item in state["servers"] if str(item.get("server_id") or "") != server_id]
    _append_audit(
        state,
        "delete_server",
        server_id=server_id,
        actor=str(payload.get("reviewer") or payload.get("actor") or "desktop"),
        success=existing is not None,
        message="MCP server deleted" if existing is not None else "MCP server delete requested but not found",
    )
    save_mcp_registry(state)
    return {"ok": len(state["servers"]) != before, "deleted": server_id, "mcp_management": build_mcp_management_snapshot()}


def set_mcp_server_enabled(payload: dict[str, Any], enabled: bool) -> dict[str, Any]:
    state = load_mcp_registry()
    server_id = _payload_server_id(payload)
    server = _server_by_id(state, server_id)
    if server is None:
        raise ValueError(f"unknown MCP server: {server_id}")
    server["enabled"] = enabled
    server["updated_at"] = _now()
    health = _server_health(server)
    _append_audit(
        state,
        "enable_server" if enabled else "disable_server",
        server_id=server_id,
        actor=str(payload.get("reviewer") or payload.get("actor") or "desktop"),
        message="MCP server enabled" if enabled else "MCP server disabled",
        metadata={"health_status": health["status"], "issues": health["issues"]},
    )
    save_mcp_registry(state)
    return {"ok": True, "server": server, "mcp_management": build_mcp_management_snapshot()}


def review_mcp_server(payload: dict[str, Any]) -> dict[str, Any]:
    state = load_mcp_registry()
    server_id = _payload_server_id(payload)
    server = _server_by_id(state, server_id)
    if server is None:
        raise ValueError(f"unknown MCP server: {server_id}")
    decision = str(payload.get("decision") or payload.get("review_state") or "approved").strip().lower()
    if decision not in {"approved", "active", "rejected", "candidate"}:
        decision = "candidate"
    server["review_state"] = decision
    server["reviewed_by"] = str(payload.get("reviewer") or "desktop")
    server["reviewed_at"] = _now()
    server["updated_at"] = _now()
    _append_audit(
        state,
        "review_server",
        server_id=server_id,
        actor=str(payload.get("reviewer") or "desktop"),
        message=f"MCP server review decision: {decision}",
        metadata={"decision": decision, "trust_level": server.get("trust_level")},
    )
    save_mcp_registry(state)
    return {"ok": True, "server": server, "mcp_management": build_mcp_management_snapshot()}


def record_mcp_runtime_audit(
    server_id: str,
    action: str,
    *,
    success: bool,
    message: str,
    metadata: dict[str, Any] | None = None,
    actor: str = "mcp_adapter",
    track_health: bool = True,
) -> dict[str, Any]:
    state = load_mcp_registry()
    server = _server_by_id(state, _safe_id(server_id))
    runtime: dict[str, Any] | None = None
    transition = ""
    if server is not None and track_health:
        runtime = _runtime_health(server)
        prior_status = runtime["status"]
        now = _now()
        if success:
            runtime.update(
                {
                    "status": "available",
                    "consecutive_failures": 0,
                    "last_success_at": now,
                    "last_error": "",
                }
            )
            if prior_status == "unavailable":
                transition = "recovered"
        else:
            failures = int(runtime["consecutive_failures"]) + 1
            runtime.update(
                {
                    "status": "unavailable" if failures >= RUNTIME_FAILURE_THRESHOLD else "degraded",
                    "consecutive_failures": failures,
                    "last_failure_at": now,
                    "last_error": str(message or "MCP runtime request failed")[:300],
                }
            )
            if prior_status != "unavailable" and runtime["status"] == "unavailable":
                transition = "unavailable"
        runtime["last_transition"] = transition or str(runtime.get("last_transition") or "")
        server["runtime_health"] = runtime
    event = _append_audit(
        state,
        str(action or "mcp_runtime"),
        server_id=_safe_id(server_id),
        actor=actor,
        success=success,
        message=str(message or "")[:300],
        metadata={
            **dict(metadata or {}),
            **({"runtime_health": dict(runtime)} if runtime is not None else {}),
        },
    )
    if transition:
        _append_audit(
            state,
            "server_runtime_recovered" if transition == "recovered" else "server_runtime_unavailable",
            server_id=_safe_id(server_id),
            actor=actor,
            success=transition == "recovered",
            message=("MCP server recovered after a successful heartbeat" if transition == "recovered" else f"MCP server disabled after {RUNTIME_FAILURE_THRESHOLD} consecutive runtime failures"),
            metadata={"runtime_health": dict(runtime or {})},
        )
    save_mcp_registry(state)
    return {"event": event, "runtime_health": runtime or {}, "transition": transition}


def mcp_server_available(server_id: str) -> bool | None:
    server = _server_by_id(load_mcp_registry(), _safe_id(server_id))
    if server is None:
        return None
    health = _server_health(server)
    return health["status"] == "ready"


def _probeable_server_entries(server_ids: set[str] | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for server in load_mcp_registry().get("servers", []):
        server_id = str(server.get("server_id") or "")
        if server_ids is not None and server_id not in server_ids:
            continue
        health = _server_health(server)
        static_issues = [issue for issue in health["issues"] if issue != "runtime_unavailable"]
        if not bool(server.get("enabled")) or str(server.get("review_state") or "") not in APPROVED_REVIEW_STATES or static_issues:
            continue
        tool = next((item for item in _dict_list(server.get("tools")) if bool(item.get("enabled", True))), None)
        if tool is not None:
            entries.append(_tool_mapping_entry(server, tool))
    return entries


def probe_mcp_servers(server_ids: list[str] | None = None) -> list[dict[str, Any]]:
    requested_ids = {_safe_id(value) for value in (server_ids or []) if str(value).strip()} or None
    entries = _probeable_server_entries(requested_ids)
    if not entries:
        return []
    try:
        from backend.tools.mcp_adapter import build_mcp_adapter_from_config

        return build_mcp_adapter_from_config(entries).probe_servers()
    except Exception as exc:
        return [{"server_id": entry["mcp_server"], "ok": False, "message": str(exc)[:300]} for entry in entries]


class MCPHealthMonitor:
    """Periodically probes approved MCP servers so unavailable tools can recover."""

    def __init__(self, *, interval_seconds: float = 30.0):
        self.interval_seconds = max(5.0, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="spiritkin-mcp-health", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            probe_mcp_servers()
            self._stop_event.wait(self.interval_seconds)


_health_monitor: MCPHealthMonitor | None = None
_health_monitor_lock = threading.Lock()


def start_mcp_health_monitor(*, interval_seconds: float | None = None) -> MCPHealthMonitor:
    global _health_monitor
    configured_interval = interval_seconds if interval_seconds is not None else _float_value(os.getenv("SPIRITKIN_MCP_HEALTH_INTERVAL_SECONDS"), 30.0, minimum=5.0, maximum=3600.0)
    with _health_monitor_lock:
        if _health_monitor is None:
            _health_monitor = MCPHealthMonitor(interval_seconds=configured_interval)
        _health_monitor.start()
        return _health_monitor


def mcp_adapter_config_entries() -> list[dict[str, Any]]:
    return list(build_mcp_management_snapshot().get("tool_mappings") or [])


def handle_mcp_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "list"}:
        return {"ok": True, "mcp_management": build_mcp_management_snapshot()}
    if action in {"probe", "probe_server", "heartbeat"}:
        server_id = str(payload.get("server_id") or payload.get("id") or "").strip()
        results = probe_mcp_servers([server_id] if server_id else None)
        return {"ok": all(bool(item.get("ok")) for item in results), "probe_results": results, "mcp_management": build_mcp_management_snapshot()}
    if action in {"save_server", "upsert_server", "register_server"}:
        return save_mcp_server(payload)
    if action in {"delete_server", "remove_server"}:
        return delete_mcp_server(payload)
    if action in {"enable_server", "enable"}:
        return set_mcp_server_enabled(payload, True)
    if action in {"disable_server", "disable"}:
        return set_mcp_server_enabled(payload, False)
    if action in {"review_server", "approve_server", "reject_server"}:
        if action == "approve_server" and not payload.get("decision"):
            payload = {**payload, "decision": "approved"}
        if action == "reject_server" and not payload.get("decision"):
            payload = {**payload, "decision": "rejected"}
        return review_mcp_server(payload)
    raise ValueError(f"unsupported MCP management action: {action}")
