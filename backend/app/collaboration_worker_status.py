from __future__ import annotations

import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from backend.app.agent_management import load_agent_management_state, resolve_agent_management_path

COLLABORATION_WORKER_STATUS_SCHEMA_VERSION = "spiritkin.collaboration_worker_status.v1"
DEFAULT_COLLABORATION_WORKER_AGENTS = ("codex", "claude_code")


def default_assistant_id_for_agent(agent: str) -> str:
    normalized = normalize_agent_id(agent)
    if normalized == "codex":
        return "codex_cli"
    return normalized


def normalize_agent_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    aliases = {
        "codexcli": "codex",
        "claudecode": "claude_code",
        "claude": "claude_code",
        "cc": "claude_code",
    }
    return aliases.get(key, raw.lower().replace("-", "_").replace(" ", "_"))


def build_collaboration_worker_config_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    agents = _normalize_agent_list(payload.get("agents") or payload.get("agent") or payload.get("to_agent"))
    agents = agents or DEFAULT_COLLABORATION_WORKER_AGENTS
    requested_assistant_ids = _assistant_id_overrides(payload.get("assistant_ids"))
    state = load_agent_management_state()
    assistants_by_id = {item.assistant_id: item for item in state.external_assistants}
    script_path = _collaboration_worker_script_path(payload.get("script_path"))
    agent_statuses = []
    for agent in agents:
        assistant_id = requested_assistant_ids.get(agent) or default_assistant_id_for_agent(agent)
        assistant = assistants_by_id.get(assistant_id)
        assistant_status = _assistant_status(agent, assistant_id, assistant)
        agent_statuses.append(
            {
                "agent": agent,
                "assistant_id": assistant_id,
                "can_start_real_worker": bool(assistant_status.get("can_start_real_worker")) and script_path.exists(),
                "external_assistant": assistant_status,
            }
        )
    real_ready = [item for item in agent_statuses if item["can_start_real_worker"]]
    return {
        "schema_version": COLLABORATION_WORKER_STATUS_SCHEMA_VERSION,
        "generated_at": time.time(),
        "agent_management_path": str(resolve_agent_management_path()),
        "worker_script": {
            "path": str(script_path),
            "exists": script_path.exists(),
            "default_transport": "route_bus",
        },
        "real_worker_status": "ready" if real_ready else "not_enabled",
        "real_worker_ready_count": len(real_ready),
        "real_worker_ready_agents": [item["agent"] for item in real_ready],
        "agents": agent_statuses,
    }


def _assistant_status(agent: str, assistant_id: str, assistant: Any) -> dict[str, Any]:
    if assistant is None:
        return {
            "assistant_id": assistant_id,
            "agent": agent,
            "status": "not_configured",
            "enabled": False,
            "configured": False,
            "command": "",
            "command_binary": "",
            "command_executable_found": False,
            "can_start_real_worker": False,
            "review_only": True,
            "allow_write": False,
        }
    command = str(getattr(assistant, "command", "") or "").strip()
    command_binary = _command_binary(command)
    executable_found = _command_executable_found(command_binary)
    enabled = bool(getattr(assistant, "enabled", False))
    configured = bool(command)
    if not configured:
        status = "missing_command"
    elif not enabled:
        status = "disabled"
    elif not executable_found:
        status = "missing_executable"
    else:
        status = "ready"
    return {
        "assistant_id": assistant_id,
        "agent": agent,
        "status": status,
        "enabled": enabled,
        "configured": configured,
        "kind": str(getattr(assistant, "kind", "") or "cli"),
        "command": command,
        "command_binary": command_binary,
        "command_executable_found": executable_found,
        "working_directory": str(getattr(assistant, "working_directory", "") or ""),
        "can_start_real_worker": status == "ready",
        "review_only": bool(getattr(assistant, "review_only", True)),
        "allow_write": bool(getattr(assistant, "allow_write", False)),
        "label": str(getattr(assistant, "label", "") or assistant_id),
    }


def _command_binary(command: str) -> str:
    if not command:
        return ""
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        parts = command.split()
    return str(parts[0] if parts else "").strip().strip('"')


def _command_executable_found(binary: str) -> bool:
    if not binary:
        return False
    path = Path(binary)
    if path.is_absolute() or any(sep in binary for sep in ("/", "\\")):
        return path.exists()
    return shutil.which(binary) is not None


def _normalize_agent_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = [item for item in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    agents: list[str] = []
    for item in raw_items:
        normalized = normalize_agent_id(item)
        if normalized and normalized not in agents:
            agents.append(normalized)
    return tuple(agents)


def _assistant_id_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        normalize_agent_id(agent): str(assistant_id).strip()
        for agent, assistant_id in value.items()
        if normalize_agent_id(agent) and str(assistant_id).strip()
    }


def _collaboration_worker_script_path(value: Any = None) -> Path:
    raw = str(value or "").strip()
    if raw:
        target = Path(raw)
    else:
        target = Path("scripts") / "collaboration_agent_worker.py"
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()
