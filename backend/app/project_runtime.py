from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backend.app.desktop_state import load_desktop_state
from backend.state_store import resolve_state_path

PROJECT_RUNTIME_SCHEMA_VERSION = "spiritkin.project_runtime.v1"
PROJECT_RUNTIME_AUDIT_SCHEMA_VERSION = "spiritkin.project_runtime_audit.v1"
DEFAULT_PROJECT_RUNTIME_AUDIT_LOG = "state/project_runtime/audit.jsonl"
DEFAULT_MAX_COMMAND_LENGTH = 400

_HIGH_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("rm_recursive_force", re.compile(r"\brm\s+-[^\n;&|]*r[^\n;&|]*f", re.IGNORECASE), "recursive force delete"),
    ("remove_item_recursive_force", re.compile(r"\b(remove-item|ri|del|erase)\b[^\n;&|]*(?:-recurse|/s)[^\n;&|]*(?:-force|/q|/f)", re.IGNORECASE), "recursive force delete"),
    ("format_drive", re.compile(r"\bformat(?:\.com|\.exe)?\s+[A-Za-z]:", re.IGNORECASE), "drive format"),
    ("shutdown", re.compile(r"\bshutdown(?:\.exe)?\s+/(?:s|r|p)\b", re.IGNORECASE), "system shutdown"),
    ("git_reset_hard", re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), "hard git reset"),
    ("git_clean_force", re.compile(r"\bgit\s+clean\s+-[^\n;&|]*f[^\n;&|]*d", re.IGNORECASE), "force git clean"),
    ("package_publish", re.compile(r"\b(npm|pnpm|yarn)\s+publish\b", re.IGNORECASE), "package publish"),
    ("pip_uninstall", re.compile(r"\bpip(?:3)?\s+uninstall\b", re.IGNORECASE), "package uninstall"),
)

_REVIEW_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("shell_launcher", re.compile(r"\b(powershell|pwsh|cmd)\b[^\n]*(?:-command|/c)\b", re.IGNORECASE), "nested shell command"),
    ("download_execute", re.compile(r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b", re.IGNORECASE), "network download/request"),
    ("dependency_install", re.compile(r"\b(npm|pnpm|yarn|pip|uv|poetry)\s+(install|add|sync)\b", re.IGNORECASE), "dependency mutation"),
    ("docker_or_cluster", re.compile(r"\b(docker|podman|kubectl|helm)\b", re.IGNORECASE), "container or cluster command"),
    ("start_process", re.compile(r"\bstart-process\b", re.IGNORECASE), "starts external process"),
)

_SAFE_START_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*npm\s+(run\s+)?(dev|start|serve)\b", re.IGNORECASE),
    re.compile(r"^\s*pnpm\s+(run\s+)?(dev|start|serve)\b", re.IGNORECASE),
    re.compile(r"^\s*yarn\s+(run\s+)?(dev|start|serve)\b", re.IGNORECASE),
    re.compile(r"^\s*(python|py|python3)\s+(-m\s+[\w.:-]+|[\w./\\-]+\.py)\b", re.IGNORECASE),
    re.compile(r"^\s*uv\s+run\s+(python|py|python3)\b", re.IGNORECASE),
    re.compile(r"^\s*poetry\s+run\s+(python|py|python3)\b", re.IGNORECASE),
    re.compile(r"^\s*dotnet\s+run\b", re.IGNORECASE),
    re.compile(r"^\s*(node|vite|next)\b", re.IGNORECASE),
)

_CD_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)(?:cd|chdir|set-location|sl)\s+"
    r"(?:(?:-literalpath|-path)\s+)?"
    r"(?P<quote>['\"]?)(?P<path>(?:[A-Za-z]:[\\/][^;&|'\"\n]*|\\\\[^;&|'\"\n]*|\.{1,2}(?:[\\/][^;&|'\"\n]*)?))",
    re.IGNORECASE,
)


def build_project_runtime_snapshot() -> dict[str, Any]:
    state = load_desktop_state()
    projects = [dict(item) for item in state.get("projects") or [] if isinstance(item, dict)]
    profiles: list[dict[str, Any]] = []
    for project in projects:
        policy = evaluate_project_runtime_policy(project)
        profiles.append(
            {
                "project_id": str(project.get("id") or ""),
                "title": str(project.get("title") or ""),
                "status": str(project.get("status") or "active"),
                "workspace_path": str(project.get("workspace_path") or ""),
                "env_file_path": str(project.get("env_file_path") or ""),
                "dependency_file_path": str(project.get("dependency_file_path") or ""),
                "package_manager": str(project.get("package_manager") or "auto"),
                "start_command": str(project.get("start_command") or ""),
                "execution_policy": policy,
            }
        )
    blocked = sum(1 for item in profiles if not item.get("execution_policy", {}).get("allowed"))
    review_required = sum(1 for item in profiles if item.get("execution_policy", {}).get("review_required"))
    return {
        "schema_version": PROJECT_RUNTIME_SCHEMA_VERSION,
        "generated_at": time.time(),
        "policy": _policy_snapshot(),
        "project_count": len(profiles),
        "blocked_count": blocked,
        "review_required_count": review_required,
        "profiles": profiles,
        "recent_events": list_project_runtime_audit_events(limit=20),
    }


def handle_project_runtime_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "status"}:
        return {"ok": True, "status": "snapshot", "project_runtime": build_project_runtime_snapshot()}
    if action in {"evaluate_start_command", "evaluate", "check_start_command"}:
        project = _project_payload(payload)
        policy = evaluate_project_runtime_policy(project)
        return {
            "ok": bool(policy.get("allowed")),
            "status": _policy_status(policy),
            "execution_policy": policy,
            "project_runtime": build_project_runtime_snapshot(),
        }
    if action in {"record_start_command", "record", "audit_start_command"}:
        project = _project_payload(payload)
        policy = evaluate_project_runtime_policy(project)
        requested_status = str(payload.get("status") or "").strip().lower()
        status = requested_status or ("started" if policy.get("allowed") else "blocked")
        if not policy.get("allowed") and status not in {"blocked", "canceled", "cancelled", "failed"}:
            status = "blocked"
        event = append_project_runtime_audit_event(
            action="start_command",
            status=status,
            project=project,
            policy=policy,
            actor=str(payload.get("actor") or "desktop"),
            message=str(payload.get("message") or ""),
        )
        ok = bool(policy.get("allowed")) and status not in {"blocked", "canceled", "cancelled", "failed"}
        return {
            "ok": ok,
            "status": status,
            "execution_policy": policy,
            "audit_event": event,
            "project_runtime": build_project_runtime_snapshot(),
        }
    raise ValueError(f"unsupported project runtime action: {action}")


def evaluate_project_runtime_policy(project: dict[str, Any]) -> dict[str, Any]:
    command = str(project.get("start_command") or project.get("command") or "").strip()
    workspace = _resolve_workspace_path(project.get("workspace_path"))
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not command:
        _add_issue(blockers, "project_start_command_missing", "Project start command is empty.")
    if len(command) > _max_command_length():
        _add_issue(blockers, "project_command_too_long", f"Command length exceeds {_max_command_length()} characters.")
    if not workspace:
        _add_issue(blockers, "project_workspace_missing", "Workspace path is empty.")
    elif not workspace.exists() or not workspace.is_dir():
        _add_issue(blockers, "project_workspace_not_found", f"Workspace does not exist: {workspace}")

    if command:
        for issue_id, pattern, detail in _HIGH_RISK_PATTERNS:
            if pattern.search(command):
                _add_issue(blockers, f"project_command_high_risk_{issue_id}", f"Command contains {detail}.")
        for issue_id, pattern, detail in _REVIEW_PATTERNS:
            if pattern.search(command):
                _add_issue(warnings, f"project_command_review_{issue_id}", f"Command contains {detail}.")
        if workspace:
            for move in _workspace_moves(command, workspace):
                if not _path_is_within(move["resolved_path"], workspace):
                    _add_issue(
                        blockers,
                        "project_command_workspace_escape",
                        f"Command changes directory outside the project workspace: {move['raw_path']}",
                    )
                elif str(move["raw_path"]).strip():
                    _add_issue(warnings, "project_command_changes_directory", f"Command changes directory to {move['raw_path']}.")

    recognized_safe = bool(command) and any(pattern.search(command) for pattern in _SAFE_START_PATTERNS)
    if command and not recognized_safe:
        _add_issue(warnings, "project_command_unknown_start_shape", "Command is not a recognized low-risk project start shape.")

    allowed = not blockers
    review_required = allowed and bool(warnings) and _review_medium_risk()
    risk_level = "blocked" if not allowed else "medium" if warnings else "low"
    next_actions = _next_actions(blockers, warnings, review_required)
    return {
        "schema_version": PROJECT_RUNTIME_SCHEMA_VERSION,
        "allowed": allowed,
        "review_required": review_required,
        "risk_level": risk_level,
        "recognized_safe_start": recognized_safe,
        "workspace_path": str(workspace) if workspace else "",
        "workspace_exists": bool(workspace and workspace.exists() and workspace.is_dir()),
        "command": command,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions,
        "policy": _policy_snapshot(),
    }


def append_project_runtime_audit_event(
    *,
    action: str,
    status: str,
    project: dict[str, Any],
    policy: dict[str, Any],
    actor: str = "desktop",
    message: str = "",
) -> dict[str, Any]:
    now = time.time()
    event = {
        "schema_version": PROJECT_RUNTIME_AUDIT_SCHEMA_VERSION,
        "event_id": f"project-runtime-{uuid.uuid4().hex[:16]}",
        "at": now,
        "action": action,
        "status": status,
        "success": status in {"started", "completed", "ok"},
        "actor": actor or "desktop",
        "project_id": str(project.get("id") or project.get("project_id") or ""),
        "project_title": str(project.get("title") or ""),
        "workspace_path": str(policy.get("workspace_path") or project.get("workspace_path") or ""),
        "command": str(policy.get("command") or project.get("start_command") or project.get("command") or ""),
        "risk_level": str(policy.get("risk_level") or ""),
        "review_required": bool(policy.get("review_required")),
        "message": message or _event_message(status, policy),
        "policy": policy,
    }
    path = resolve_project_runtime_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return event


def list_project_runtime_audit_events(limit: int = 80) -> list[dict[str, Any]]:
    normalized_limit = max(1, min(500, int(limit or 80)))
    path = resolve_project_runtime_audit_log_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-normalized_limit * 2 :]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    events.sort(key=lambda item: float(item.get("at") or 0.0), reverse=True)
    return events[:normalized_limit]


def resolve_project_runtime_audit_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG", DEFAULT_PROJECT_RUNTIME_AUDIT_LOG, path)


def _project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project = dict(payload.get("project") or {}) if isinstance(payload.get("project"), dict) else {}
    for source_key, target_key in (
        ("project_id", "id"),
        ("id", "id"),
        ("title", "title"),
        ("workspace_path", "workspace_path"),
        ("env_file_path", "env_file_path"),
        ("dependency_file_path", "dependency_file_path"),
        ("package_manager", "package_manager"),
        ("start_command", "start_command"),
        ("command", "start_command"),
    ):
        value = payload.get(source_key)
        if value not in (None, ""):
            project[target_key] = value
    return project


def _resolve_workspace_path(raw: Any) -> Path | None:
    value = str(raw or "").strip().strip('"')
    if not value:
        return None
    expanded = os.path.expandvars(value)
    target = Path(expanded)
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        return target.resolve()
    except OSError:
        return target.absolute()


def _workspace_moves(command: str, workspace: Path) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for match in _CD_PATTERN.finditer(command):
        raw_path = match.group("path").strip().strip('"').strip("'")
        if not raw_path:
            continue
        target = Path(os.path.expandvars(raw_path))
        if not target.is_absolute():
            target = workspace / target
        try:
            resolved = target.resolve()
        except OSError:
            resolved = target.absolute()
        moves.append({"raw_path": raw_path, "resolved_path": resolved})
    return moves


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def _add_issue(items: list[dict[str, str]], issue_id: str, detail: str) -> None:
    if any(item.get("issue_id") == issue_id and item.get("detail") == detail for item in items):
        return
    items.append({"issue_id": issue_id, "detail": detail})


def _policy_status(policy: dict[str, Any]) -> str:
    if not policy.get("allowed"):
        return "blocked"
    if policy.get("review_required"):
        return "review_required"
    return "allowed"


def _next_actions(blockers: list[dict[str, str]], warnings: list[dict[str, str]], review_required: bool) -> list[str]:
    actions: list[str] = []
    issue_ids = {item.get("issue_id") for item in blockers + warnings}
    if "project_workspace_missing" in issue_ids or "project_workspace_not_found" in issue_ids:
        actions.append("Set an existing project workspace path before running the start command.")
    if "project_command_workspace_escape" in issue_ids:
        actions.append("Remove cd/Set-Location steps that leave the project workspace.")
    if any(str(issue_id).startswith("project_command_high_risk_") for issue_id in issue_ids):
        actions.append("Move destructive maintenance commands into a reviewed workflow or manual terminal step.")
    if "project_command_unknown_start_shape" in issue_ids:
        actions.append("Prefer a standard start command such as npm run dev, pnpm dev, python -m app, uv run python -m app, or dotnet run.")
    if review_required:
        actions.append("Confirm the medium-risk command before starting it from the desktop UI.")
    if not actions and not blockers:
        actions.append("Run from the project integrated terminal with the project runtime environment.")
    return actions


def _event_message(status: str, policy: dict[str, Any]) -> str:
    if status in {"blocked", "failed"}:
        blockers = policy.get("blockers") if isinstance(policy.get("blockers"), list) else []
        first = blockers[0].get("detail") if blockers and isinstance(blockers[0], dict) else "blocked by project runtime policy"
        return str(first)
    if status in {"canceled", "cancelled"}:
        return "Project start command canceled before launch."
    return "Project start command recorded."


def _max_command_length() -> int:
    try:
        return max(80, int(os.getenv("SPIRITKIN_PROJECT_RUNTIME_MAX_COMMAND_LENGTH", str(DEFAULT_MAX_COMMAND_LENGTH))))
    except ValueError:
        return DEFAULT_MAX_COMMAND_LENGTH


def _review_medium_risk() -> bool:
    value = os.getenv("SPIRITKIN_PROJECT_RUNTIME_REVIEW_MEDIUM_RISK", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _policy_snapshot() -> dict[str, Any]:
    return {
        "max_command_length": _max_command_length(),
        "review_medium_risk": _review_medium_risk(),
        "audit_log": str(resolve_project_runtime_audit_log_path()),
        "high_risk_blockers": [item[0] for item in _HIGH_RISK_PATTERNS],
        "review_triggers": [item[0] for item in _REVIEW_PATTERNS],
    }
