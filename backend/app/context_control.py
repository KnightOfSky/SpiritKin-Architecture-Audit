from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

DEFAULT_CONTEXT_STATE_PATH = "state/desktop_console/context_control.json"


@dataclass(frozen=True)
class ContextPolicy:
    mode: str = "balanced"
    max_recent_messages: int = 12
    summarize_after_messages: int = 20
    include_project_docs: bool = True
    include_recent_events: bool = True
    include_learning_records: bool = True
    pinned_context: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_recent_messages": self.max_recent_messages,
            "summarize_after_messages": self.summarize_after_messages,
            "include_project_docs": self.include_project_docs,
            "include_recent_events": self.include_recent_events,
            "include_learning_records": self.include_learning_records,
            "pinned_context": list(self.pinned_context),
        }


@dataclass(frozen=True)
class ProjectOptimizationSuggestion:
    suggestion_id: str
    title: str
    detail: str
    priority: str = "medium"
    command: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "title": self.title,
            "detail": self.detail,
            "priority": self.priority,
            "command": self.command,
        }


@dataclass(frozen=True)
class ContextControlSnapshot:
    generated_at: float
    policy: ContextPolicy
    active_session: dict[str, Any]
    project_summary: dict[str, Any]
    suggestions: tuple[ProjectOptimizationSuggestion, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "policy": self.policy.snapshot(),
            "active_session": dict(self.active_session),
            "project_summary": dict(self.project_summary),
            "suggestions": [suggestion.snapshot() for suggestion in self.suggestions],
        }


def resolve_context_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_CONTEXT_STATE_PATH", DEFAULT_CONTEXT_STATE_PATH, path)


def load_context_policy(path: str | os.PathLike[str] | None = None) -> ContextPolicy:
    target = resolve_context_state_path(path)
    if not target.exists():
        return ContextPolicy()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ContextPolicy()
    return ContextPolicy(
        mode=str(data.get("mode") or "balanced"),
        max_recent_messages=int(data.get("max_recent_messages") or 12),
        summarize_after_messages=int(data.get("summarize_after_messages") or 20),
        include_project_docs=bool(data.get("include_project_docs", True)),
        include_recent_events=bool(data.get("include_recent_events", True)),
        include_learning_records=bool(data.get("include_learning_records", True)),
        pinned_context=tuple(str(item) for item in data.get("pinned_context") or ()),
    )


def save_context_policy(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> ContextPolicy:
    current = load_context_policy(path)
    merged = {
        **current.snapshot(),
        **dict(payload or {}),
    }
    policy = ContextPolicy(
        mode=str(merged.get("mode") or "balanced"),
        max_recent_messages=max(2, int(merged.get("max_recent_messages") or 12)),
        summarize_after_messages=max(4, int(merged.get("summarize_after_messages") or 20)),
        include_project_docs=bool(merged.get("include_project_docs", True)),
        include_recent_events=bool(merged.get("include_recent_events", True)),
        include_learning_records=bool(merged.get("include_learning_records", True)),
        pinned_context=tuple(str(item) for item in merged.get("pinned_context") or ()),
    )
    target = resolve_context_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(policy.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def _active_session_summary(desktop_state: dict[str, Any]) -> dict[str, Any]:
    sessions = list(desktop_state.get("sessions") or [])
    active_id = str(desktop_state.get("active_session_id") or "")
    active = next((session for session in sessions if str(session.get("id")) == active_id), sessions[0] if sessions else {})
    messages = list(active.get("messages") or []) if isinstance(active, dict) else []
    return {
        "id": active.get("id", ""),
        "title": active.get("title", ""),
        "message_count": len(messages),
        "recent_messages": messages[-8:],
        "updated_at": active.get("updated_at", 0),
    }


def build_project_optimization_suggestions(root: str | os.PathLike[str] | None = None) -> list[ProjectOptimizationSuggestion]:
    project_root = Path(root or Path.cwd()).resolve()
    suggestions: list[ProjectOptimizationSuggestion] = []
    gitignore_text = ""
    try:
        gitignore_text = (project_root / ".gitignore").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    if not (project_root / "backend" / "tests").exists():
        suggestions.append(ProjectOptimizationSuggestion(
            suggestion_id="missing-tests",
            title="缺少后端测试目录",
            detail="建议先补单元测试目录，再允许 Agent 自动生成或修改工具。",
            priority="high",
        ))
    desktop_artifacts_ignored = "desktop/**/bin/" in gitignore_text and "desktop/**/obj/" in gitignore_text
    if (project_root / "desktop" / "SpiritKinDesktop" / "bin").exists() and not desktop_artifacts_ignored:
        suggestions.append(ProjectOptimizationSuggestion(
            suggestion_id="desktop-build-artifacts",
            title="桌面端构建产物已生成",
            detail="建议将 desktop/**/bin、obj、WebView2 用户数据加入 .gitignore，避免误提交。",
            priority="medium",
            command="git status --short desktop",
        ))
    if not (project_root / "docs" / "codex_handoff.md").exists():
        suggestions.append(ProjectOptimizationSuggestion(
            suggestion_id="missing-handoff",
            title="缺少交接文档",
            detail="建议保留当前架构、入口、验证命令和风险说明，方便多 Agent 继续工作。",
            priority="medium",
        ))
    return suggestions


def build_context_control_snapshot(root: str | os.PathLike[str] | None = None) -> ContextControlSnapshot:
    from backend.app.desktop_state import load_desktop_state

    desktop_state = load_desktop_state()
    policy = load_context_policy()
    project_root = Path(root or Path.cwd()).resolve()
    project_summary = {
        "root": str(project_root),
        "desktop_sessions": len(desktop_state.get("sessions") or []),
        "desktop_tasks": len(desktop_state.get("tasks") or []),
        "desktop_projects": len(desktop_state.get("projects") or []),
        "strategy": "use recent messages + summary + pinned context; do not paste full project into every prompt",
    }
    return ContextControlSnapshot(
        generated_at=time.time(),
        policy=policy,
        active_session=_active_session_summary(desktop_state),
        project_summary=project_summary,
        suggestions=tuple(build_project_optimization_suggestions(project_root)),
    )
