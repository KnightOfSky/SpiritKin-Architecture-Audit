from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path
from typing import Any

from backend.app.context_control import load_context_policy
from backend.app.learning_workflow import load_learning_records, load_model_provider_settings
from backend.state_store import resolve_state_path

DEFAULT_PROJECT_OVERVIEW_PATH = "docs/project_management_overview.md"
DEFAULT_PROJECT_OVERVIEW_REVIEW_PATH = "state/desktop_console/project_overview_reviews.jsonl"


@dataclass(frozen=True)
class ProjectOverview:
    generated_at: float
    path: str
    markdown: str
    summary: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "path": self.path,
            "markdown": self.markdown,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class ProjectOverviewChange:
    change_id: str
    created_at: float
    author: str
    status: str
    note: str
    base_path: str
    proposed_markdown: str
    diff: str
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    review_note: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "created_at": self.created_at,
            "author": self.author,
            "status": self.status,
            "note": self.note,
            "base_path": self.base_path,
            "proposed_markdown": self.proposed_markdown,
            "diff": self.diff,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
        }


@dataclass(frozen=True)
class ProjectOverviewReviewState:
    overview: ProjectOverview
    changes: tuple[ProjectOverviewChange, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "overview": self.overview.snapshot(),
            "changes": [change.snapshot() for change in self.changes],
            "pending_count": sum(1 for change in self.changes if change.status == "pending"),
        }


def resolve_project_overview_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_PROJECT_OVERVIEW_PATH", DEFAULT_PROJECT_OVERVIEW_PATH, path)


def resolve_project_overview_review_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH", DEFAULT_PROJECT_OVERVIEW_REVIEW_PATH, path)


def load_project_overview(path: str | os.PathLike[str] | None = None) -> ProjectOverview:
    target = resolve_project_overview_path(path)
    if target.exists():
        text = target.read_text(encoding="utf-8", errors="replace")
    else:
        text = build_project_overview_markdown()
        save_project_overview(text, path=target)
    return ProjectOverview(time.time(), str(target), text, _build_summary())


def load_project_overview_review_state(path: str | os.PathLike[str] | None = None) -> ProjectOverviewReviewState:
    return ProjectOverviewReviewState(load_project_overview(path), tuple(load_project_overview_changes()))


def save_project_overview(markdown: str, path: str | os.PathLike[str] | None = None) -> ProjectOverview:
    target = resolve_project_overview_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = markdown.strip() + "\n"
    target.write_text(text, encoding="utf-8")
    return ProjectOverview(time.time(), str(target), text, _build_summary())


def refresh_project_overview(path: str | os.PathLike[str] | None = None) -> ProjectOverview:
    return save_project_overview(build_project_overview_markdown(), path=path)


def propose_project_overview_change(
    proposed_markdown: str,
    *,
    author: str = "desktop",
    note: str = "",
    path: str | os.PathLike[str] | None = None,
) -> ProjectOverviewChange:
    overview = load_project_overview(path)
    diff = build_markdown_diff(overview.markdown, proposed_markdown)
    change = ProjectOverviewChange(
        change_id=f"overview-{uuid.uuid4().hex[:12]}",
        created_at=time.time(),
        author=author,
        status="pending",
        note=note,
        base_path=overview.path,
        proposed_markdown=proposed_markdown.strip() + "\n",
        diff=diff,
    )
    _append_project_overview_change(change)
    return change


def build_markdown_diff(current: str, proposed: str) -> str:
    return "".join(
        unified_diff(
            current.splitlines(keepends=True),
            (proposed.strip() + "\n").splitlines(keepends=True),
            fromfile="current/project_management_overview.md",
            tofile="proposed/project_management_overview.md",
        )
    )


def load_project_overview_changes(path: str | os.PathLike[str] | None = None, *, limit: int = 50) -> list[ProjectOverviewChange]:
    target = resolve_project_overview_review_path(path)
    if not target.exists():
        return []
    changes: list[ProjectOverviewChange] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            changes.append(_change_from_dict(data))
    return changes[-max(1, int(limit)):]


def approve_project_overview_change(change_id: str, *, reviewer: str = "human", review_note: str = "") -> ProjectOverviewChange:
    changes = load_project_overview_changes(limit=500)
    selected = next((change for change in changes if change.change_id == change_id), None)
    if selected is None:
        raise ValueError(f"change not found: {change_id}")
    approved = ProjectOverviewChange(
        **{
            **selected.snapshot(),
            "status": "approved",
            "reviewed_by": reviewer,
            "reviewed_at": time.time(),
            "review_note": review_note,
        }
    )
    save_project_overview(approved.proposed_markdown, path=approved.base_path)
    _rewrite_project_overview_changes([approved if change.change_id == change_id else change for change in changes])
    return approved


def reject_project_overview_change(change_id: str, *, reviewer: str = "human", review_note: str = "") -> ProjectOverviewChange:
    changes = load_project_overview_changes(limit=500)
    selected = next((change for change in changes if change.change_id == change_id), None)
    if selected is None:
        raise ValueError(f"change not found: {change_id}")
    rejected = ProjectOverviewChange(
        **{
            **selected.snapshot(),
            "status": "rejected",
            "reviewed_by": reviewer,
            "reviewed_at": time.time(),
            "review_note": review_note,
        }
    )
    _rewrite_project_overview_changes([rejected if change.change_id == change_id else change for change in changes])
    return rejected


def _change_from_dict(data: dict[str, Any]) -> ProjectOverviewChange:
    return ProjectOverviewChange(
        change_id=str(data.get("change_id") or f"overview-{uuid.uuid4().hex[:12]}"),
        created_at=float(data.get("created_at") or time.time()),
        author=str(data.get("author") or "desktop"),
        status=str(data.get("status") or "pending"),
        note=str(data.get("note") or ""),
        base_path=str(data.get("base_path") or resolve_project_overview_path()),
        proposed_markdown=str(data.get("proposed_markdown") or ""),
        diff=str(data.get("diff") or ""),
        reviewed_by=str(data.get("reviewed_by") or ""),
        reviewed_at=float(data.get("reviewed_at") or 0.0),
        review_note=str(data.get("review_note") or ""),
    )


def _append_project_overview_change(change: ProjectOverviewChange) -> None:
    target = resolve_project_overview_review_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(change.snapshot(), ensure_ascii=False) + "\n")


def _rewrite_project_overview_changes(changes: list[ProjectOverviewChange]) -> None:
    target = resolve_project_overview_review_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(change.snapshot(), ensure_ascii=False) + "\n" for change in changes), encoding="utf-8")


def build_project_overview_markdown() -> str:
    from backend.app.desktop_state import load_desktop_state

    state = load_desktop_state()
    policy = load_context_policy()
    provider = load_model_provider_settings()
    records = load_learning_records(limit=20)
    projects = list(state.get("projects") or [])
    tasks = list(state.get("tasks") or [])
    sessions = list(state.get("sessions") or [])
    pending_tasks = [task for task in tasks if str(task.get("status") or "") not in {"complete", "done", "closed"}]
    updated = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# SpiritKinAI Project Management Overview",
        "",
        f"Last updated: {updated}",
        "",
        "## Purpose",
        "",
        "This document is the shared project brief for human operators, the main Agent, and external model reviewers. Use it to understand the current system before proposing changes.",
        "",
        "## Runtime Entries",
        "",
        "- Native desktop app: `desktop/SpiritKinDesktop/SpiritKinDesktop.csproj`",
        "- Command gateway: `http://127.0.0.1:8788`",
        "- Realtime event bridge: `ws://127.0.0.1:8765`",
        "- 3D avatar page: `frontend/avatar_3d.html`",
        "",
        "## Desktop Modules",
        "",
        "- Chat: main Agent conversation and command execution.",
        "- Sessions: local conversation history and switching.",
        "- Projects: tracked work areas for the current repository.",
        "- Tasks: local task queue with running, complete, and blocked states.",
        "- Diagnostics: ports, dependencies, files, sync state, and repair steps.",
        "- Learning: human or external-model corrections converted into self-training samples.",
        "- Context: prompt policy, pinned context, and project optimization hints.",
        "- Project Overview: this shared management document with diff-based human approval.",
        "- Agent Cluster: Skill assistance switches, external CLI reviewers, route profiles, and remote package export.",
        "",
        "## Current State",
        "",
        f"- Sessions: {len(sessions)}",
        f"- Projects: {len(projects)}",
        f"- Tasks: {len(tasks)}",
        f"- Open tasks: {len(pending_tasks)}",
        f"- Learning records: {len(records)}",
        f"- Context mode: {policy.mode}",
        f"- Recent messages kept: {policy.max_recent_messages}",
        f"- Cloud model configured: {'yes' if provider.configured else 'no'}",
        f"- Cloud model endpoint: `{provider.endpoint or 'not set'}`",
        f"- Cloud model name: `{provider.model or 'not set'}`",
        "",
        "## Projects",
        "",
    ]
    if projects:
        for project in projects[-20:]:
            lines.append(f"- {project.get('title', '未命名项目')} [{project.get('status', 'active')}]")
    else:
        lines.append("- No tracked projects yet.")
    lines.extend(["", "## Tasks", ""])
    if tasks:
        for task in tasks[-40:]:
            detail = str(task.get("detail") or "").strip()
            suffix = f" - {detail}" if detail else ""
            lines.append(f"- {task.get('title', '未命名任务')} [{task.get('status', 'pending')}]{suffix}")
    else:
        lines.append("- No tracked tasks yet.")
    lines.extend([
        "",
        "## Learning Workflow",
        "",
        "1. Human or Agent records a concrete failure in the Learning tab.",
        "2. Optional external cloud model review produces a candidate correction.",
        "3. Human accepts or edits the correction.",
        "4. The sample is saved into `state/learning/learning_records.jsonl`.",
        "5. The training dataset is exported to `state/learning/self_training_dataset.jsonl`.",
        "6. Skill or Agent changes must still pass tests before promotion.",
        "",
        "## Skill Assistance",
        "",
        "- Skill assistance can be disabled, human-only, cloud-model reviewed, or external-CLI reviewed.",
        "- Assistance before execution is optional; assistance on failure is recommended.",
        "- External helpers such as Codex or Claude Code should default to review-only. Allow file writes only for explicitly approved workflows.",
        "",
        "## Agent Route Profiles",
        "",
        "Route profiles make model combinations explicit. Typical patterns include primary text model + visual model, primary model + reviewer model, and fallback chains.",
        "",
        "## Remote Export",
        "",
        "Skill learning outputs can be exported as remote packages under `state/remote_exports/`. Packages include target node, Skill names, verification commands, and rollback notes before remote control is enabled.",
        "",
        "## External Model Guidance",
        "",
        "The preferred cloud model connection is OpenAI-compatible: configure Base URL, model name, and API Key in the desktop Learning tab. Do not put API keys into this document.",
        "",
        "## Context Policy",
        "",
        "Use recent messages, summaries, pinned context, and this overview. Avoid sending the whole repository into every prompt.",
        "",
        "## Verification Commands",
        "",
        "```powershell",
        "python -m unittest backend.tests.unit.test_command_gateway -v",
        "dotnet build desktop\\SpiritKinDesktop\\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false",
        "```",
    ])
    return "\n".join(lines)


def _build_summary() -> dict[str, Any]:
    from backend.app.desktop_state import load_desktop_state

    state = load_desktop_state()
    provider = load_model_provider_settings()
    return {
        "sessions": len(state.get("sessions") or []),
        "projects": len(state.get("projects") or []),
        "tasks": len(state.get("tasks") or []),
        "cloud_model_configured": provider.configured,
        "cloud_model": provider.model,
        "overview_path": str(resolve_project_overview_path()),
    }


def update_project_overview(payload: dict[str, Any]) -> ProjectOverview:
    action = str(payload.get("action") or "save").strip().lower()
    if action == "refresh":
        proposed = build_project_overview_markdown()
        if bool(payload.get("propose", True)):
            propose_project_overview_change(proposed, author=str(payload.get("author") or "desktop"), note="Regenerated project overview", path=payload.get("path"))
            return load_project_overview(payload.get("path"))
        return refresh_project_overview(payload.get("path"))
    if action == "save":
        proposed = str(payload.get("markdown") or "")
        if bool(payload.get("propose", True)):
            propose_project_overview_change(proposed, author=str(payload.get("author") or "desktop"), note=str(payload.get("note") or "Manual edit"), path=payload.get("path"))
            return load_project_overview(payload.get("path"))
        return save_project_overview(proposed, path=payload.get("path"))
    if action == "append":
        current = load_project_overview(payload.get("path")).markdown.rstrip()
        addition = str(payload.get("markdown") or "").strip()
        proposed = f"{current}\n\n{addition}" if addition else current
        if bool(payload.get("propose", True)):
            propose_project_overview_change(proposed, author=str(payload.get("author") or "desktop"), note=str(payload.get("note") or "Append overview text"), path=payload.get("path"))
            return load_project_overview(payload.get("path"))
        return save_project_overview(proposed, path=payload.get("path"))
    raise ValueError(f"unsupported project overview action: {action}")
