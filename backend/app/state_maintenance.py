from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from backend.app.desktop_state import (
    DESKTOP_STATE_SCHEMA_VERSION,
    load_desktop_state,
    migrate_desktop_state,
    resolve_desktop_state_path,
)
from backend.app.knowledge_base_management import (
    KNOWLEDGE_JOB_SCHEMA_VERSION,
    load_knowledge_job_history,
    resolve_knowledge_job_history_path,
    save_knowledge_job_history,
)
from backend.app.operations_center import DEFAULT_LOG_DIRECTORIES
from backend.app.project_runtime import PROJECT_RUNTIME_AUDIT_SCHEMA_VERSION, resolve_project_runtime_audit_log_path
from backend.app.skills_console import resolve_skill_run_audit_log_path
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.mobile.artifact_store import MobileArtifactStore
from backend.orchestrator.workflow_store import JsonWorkflowStore

SCHEMA_VERSION = "spiritkin.state_maintenance.v1"
DEFAULT_KEEP_WORKFLOW_RUNS = 30
DEFAULT_KEEP_ANDROID_COMMANDS = 300
DEFAULT_KEEP_ANDROID_HISTORY = 120
DEFAULT_KEEP_KB_JOBS = 80
DEFAULT_KEEP_SKILL_RUN_AUDIT_EVENTS = 500
DEFAULT_KEEP_PROJECT_RUNTIME_EVENTS = 500
DEFAULT_KEEP_LOG_BYTES = 5 * 1024 * 1024
GENERATED_BUILD_ARTIFACTS = (
    Path("state/build/bin-verify-desktop"),
    Path("state/build/bin-verify-desktop2"),
    Path("state/build/bin-growth-governance"),
    Path("state/build/bin-growth-governance2"),
    Path("state/build/bin-growth-governance-tests"),
    Path("state/build/bin-verify-tests"),
    Path("state/build/bin-verify-tests2"),
    Path("state/build/wpf-app-bin"),
    Path("state/build/wpf-app-obj"),
    Path("state/build/obj-verify-desktop"),
    Path("state/build/obj-verify-tests"),
    Path("state/build/bin-benchmark-ui"),
    Path("state/build/obj-benchmark-ui"),
    Path("state/build/bin-runtime-continuity"),
    Path("state/build/bin-runtime-continuity-tests"),
)
REPRODUCIBLE_CACHE_ARTIFACTS = (
    Path(".tmp-build"),
    Path(".tmp-test"),
    Path("tmp/lucide-static-package"),
    Path("state/providers/miniconda3/pkgs"),
    Path("output/playwright"),
    Path(".playwright-cli"),
    Path("desktop/SpiritKinDesktop/obj"),
    Path("desktop/SpiritKinDesktop.Tests/bin"),
    Path("desktop/SpiritKinDesktop.Tests/obj"),
)


def build_state_maintenance_snapshot(*, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    workflow_store = JsonWorkflowStore(project_root=root)
    workflow_runs = workflow_store.list_runs()
    mobile_artifacts = MobileArtifactStore().snapshot()
    android_companion = AndroidCompanionStore().snapshot()
    desktop_state = load_desktop_state()
    kb_history = load_knowledge_job_history()
    skill_run_audit = _jsonl_snapshot(resolve_skill_run_audit_log_path(), "spiritkin.skill_run_audit.v1")
    project_runtime_audit = _jsonl_snapshot(resolve_project_runtime_audit_log_path(), PROJECT_RUNTIME_AUDIT_SCHEMA_VERSION)
    log_files = _log_file_snapshots(root)
    generated_build = _generated_build_artifact_snapshot(root)
    reproducible_caches = _reproducible_cache_snapshot(root)
    components = [
        _component(
            "desktop_state",
            "Desktop State",
            resolve_desktop_state_path(),
            str(desktop_state.get("schema_version") or DESKTOP_STATE_SCHEMA_VERSION),
            count=sum(len(desktop_state.get(key) or []) for key in ("sessions", "projects", "tasks", "quick_commands", "events")),
            cleanup_action="migrate_state",
            details={
                "revision": int(desktop_state.get("revision") or 0),
                "session_count": len(desktop_state.get("sessions") or []),
                "project_count": len(desktop_state.get("projects") or []),
                "task_count": len(desktop_state.get("tasks") or []),
                "migration_history_count": len(desktop_state.get("migration_history") or []),
            },
        ),
        _component(
            "workflow_runs",
            "Workflow Runs",
            workflow_store._runs_path,
            "spiritkin.workflow_runs.v1",
            count=len(workflow_runs),
            cleanup_action="cleanup_workflow_runs",
            details={"active_count": sum(1 for run in workflow_runs if run.status in {"pending", "running", "waiting", "waiting_review"})},
        ),
        _component(
            "mobile_artifacts",
            "Mobile Artifacts",
            Path(str(mobile_artifacts.get("index_path") or "")),
            str(mobile_artifacts.get("schema_version") or "spiritkin.mobile_artifacts.v1"),
            count=int(mobile_artifacts.get("artifact_count") or 0),
            size_bytes=int(mobile_artifacts.get("total_size_bytes") or 0),
            cleanup_action="cleanup_mobile_artifacts",
            details={"expired_count": int(mobile_artifacts.get("expired_count") or 0), "image_count": int(mobile_artifacts.get("image_count") or 0)},
        ),
        _component(
            "android_command_history",
            "Android Command History",
            Path(str(android_companion.get("state_path") or "")),
            "spiritkin.android_companion_store.v1",
            count=len(android_companion.get("recent_commands") or []),
            cleanup_action="cleanup_android_command_history",
            details={"pending_command_count": int(android_companion.get("pending_command_count") or 0), "device_count": int(android_companion.get("device_count") or 0)},
        ),
        _component(
            "knowledge_jobs",
            "Knowledge Job History",
            resolve_knowledge_job_history_path(),
            str(kb_history.get("schema_version") or KNOWLEDGE_JOB_SCHEMA_VERSION),
            count=len(kb_history.get("jobs") or []),
            cleanup_action="cleanup_knowledge_jobs",
            details={"failed_count": sum(1 for item in kb_history.get("jobs") or [] if isinstance(item, dict) and str(item.get("status") or "") == "failed")},
        ),
        _component(
            "skill_run_audit",
            "Skill Run Audit",
            Path(str(skill_run_audit.get("path") or "")),
            str(skill_run_audit.get("schema_version") or "spiritkin.skill_run_audit.v1"),
            count=int(skill_run_audit.get("count") or 0),
            size_bytes=int(skill_run_audit.get("size_bytes") or 0),
            cleanup_action="cleanup_skill_run_audit",
            details={"invalid_line_count": int(skill_run_audit.get("invalid_line_count") or 0)},
        ),
        _component(
            "project_runtime_audit",
            "Project Runtime Audit",
            Path(str(project_runtime_audit.get("path") or "")),
            str(project_runtime_audit.get("schema_version") or PROJECT_RUNTIME_AUDIT_SCHEMA_VERSION),
            count=int(project_runtime_audit.get("count") or 0),
            size_bytes=int(project_runtime_audit.get("size_bytes") or 0),
            cleanup_action="cleanup_project_runtime_audit",
            details={"invalid_line_count": int(project_runtime_audit.get("invalid_line_count") or 0)},
        ),
        _component(
            "generated_build_artifacts",
            "Generated Verification Outputs",
            root / "state" / "build",
            "filesystem.generated_build.v1",
            count=int(generated_build["count"]),
            size_bytes=int(generated_build["size_bytes"]),
            cleanup_action="cleanup_generated_build_artifacts",
            details={"paths": generated_build["paths"]},
        ),
        _component(
            "reproducible_caches",
            "Reproducible Tool Caches",
            root / "tmp",
            "filesystem.reproducible_caches.v1",
            count=int(reproducible_caches["count"]),
            size_bytes=int(reproducible_caches["size_bytes"]),
            cleanup_action="cleanup_reproducible_caches",
            details={"paths": reproducible_caches["paths"]},
        ),
        _component(
            "logs",
            "Runtime Logs",
            root / "state" / "logs",
            "filesystem.logs.v1",
            count=len(log_files),
            size_bytes=sum(int(item.get("size_bytes") or 0) for item in log_files),
            cleanup_action="truncate_large_logs",
            details={"large_file_count": sum(1 for item in log_files if int(item.get("size_bytes") or 0) > DEFAULT_KEEP_LOG_BYTES)},
        ),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "project_root": str(root),
        "components": components,
        "defaults": {
            "keep_workflow_runs": DEFAULT_KEEP_WORKFLOW_RUNS,
            "keep_android_commands": DEFAULT_KEEP_ANDROID_COMMANDS,
            "keep_android_history": DEFAULT_KEEP_ANDROID_HISTORY,
            "keep_kb_jobs": DEFAULT_KEEP_KB_JOBS,
            "keep_skill_run_audit_events": DEFAULT_KEEP_SKILL_RUN_AUDIT_EVENTS,
            "keep_project_runtime_events": DEFAULT_KEEP_PROJECT_RUNTIME_EVENTS,
            "keep_log_bytes": DEFAULT_KEEP_LOG_BYTES,
        },
        "summary": {
            "component_count": len(components),
            "total_count": sum(int(item.get("count") or 0) for item in components),
            "total_size_bytes": sum(int(item.get("size_bytes") or 0) for item in components),
            "attention_count": sum(1 for item in components if item.get("needs_attention")),
        },
    }


def handle_state_maintenance_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    project_root = str(payload.get("project_root") or "").strip() or None
    actor = str(payload.get("actor") or "state_maintenance").strip() or "state_maintenance"
    if action in {"snapshot", "refresh"}:
        result = {"ok": True, "status": "refreshed", "message": "状态维护快照已刷新。"}
    elif action in {"cleanup_all", "cleanup_state"}:
        result = _cleanup_all(payload, project_root=project_root, actor=actor)
    elif action == "cleanup_workflow_runs":
        result = _cleanup_workflow_runs(payload, project_root=project_root, actor=actor)
    elif action == "cleanup_mobile_artifacts":
        result = _cleanup_mobile_artifacts(payload)
    elif action == "cleanup_android_command_history":
        result = _cleanup_android_command_history(payload)
    elif action == "cleanup_knowledge_jobs":
        result = _cleanup_knowledge_jobs(payload)
    elif action == "cleanup_skill_run_audit":
        result = _cleanup_jsonl_audit(
            resolve_skill_run_audit_log_path(),
            keep=_int(payload.get("keep_skill_run_audit_events"), DEFAULT_KEEP_SKILL_RUN_AUDIT_EVENTS),
            label="Skill run audit",
        )
    elif action == "cleanup_project_runtime_audit":
        result = _cleanup_jsonl_audit(
            resolve_project_runtime_audit_log_path(),
            keep=_int(payload.get("keep_project_runtime_events"), DEFAULT_KEEP_PROJECT_RUNTIME_EVENTS),
            label="Project runtime audit",
        )
    elif action == "truncate_large_logs":
        result = _truncate_large_logs(payload, project_root=project_root)
    elif action == "cleanup_generated_build_artifacts":
        result = _cleanup_generated_build_artifacts(project_root=project_root)
    elif action == "cleanup_reproducible_caches":
        result = _cleanup_reproducible_caches(project_root=project_root)
    elif action in {"migrate", "migrate_state"}:
        result = _migrate_state(project_root=project_root, actor=actor)
    else:
        result = {"ok": False, "status": "unknown_action", "message": f"unknown state maintenance action: {action}"}
    return {
        "ok": bool(result.get("ok")),
        "action": action,
        "result": result,
        "state_maintenance": build_state_maintenance_snapshot(project_root=project_root),
    }


def _cleanup_all(payload: dict[str, Any], *, project_root: str | os.PathLike[str] | None, actor: str) -> dict[str, Any]:
    results = {
        "workflow_runs": _cleanup_workflow_runs(payload, project_root=project_root, actor=actor),
        "mobile_artifacts": _cleanup_mobile_artifacts(payload),
        "android_command_history": _cleanup_android_command_history(payload),
        "knowledge_jobs": _cleanup_knowledge_jobs(payload),
        "skill_run_audit": _cleanup_jsonl_audit(
            resolve_skill_run_audit_log_path(),
            keep=_int(payload.get("keep_skill_run_audit_events"), DEFAULT_KEEP_SKILL_RUN_AUDIT_EVENTS),
            label="Skill run audit",
        ),
        "project_runtime_audit": _cleanup_jsonl_audit(
            resolve_project_runtime_audit_log_path(),
            keep=_int(payload.get("keep_project_runtime_events"), DEFAULT_KEEP_PROJECT_RUNTIME_EVENTS),
            label="Project runtime audit",
        ),
        "logs": _truncate_large_logs(payload, project_root=project_root),
    }
    removed = sum(_removed_count(item) for item in results.values())
    return {"ok": True, "status": "cleaned", "message": f"状态维护清理完成，移除/截断 {removed} 项。", "results": results, "removed": removed}


def _cleanup_workflow_runs(payload: dict[str, Any], *, project_root: str | os.PathLike[str] | None, actor: str) -> dict[str, Any]:
    store = JsonWorkflowStore(project_root=project_root)
    keep_recent = _int(payload.get("keep_workflow_runs") or payload.get("keep_recent"), DEFAULT_KEEP_WORKFLOW_RUNS)
    result = store.cleanup_runs(keep_recent=keep_recent, include_archived=True, actor=actor, reason="State maintenance cleanup")
    return {"ok": True, "status": "cleaned", "message": f"已清理 {result.get('removed', 0)} 个旧 workflow run。", **result}


def _cleanup_mobile_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    keep_recent = _int(payload.get("keep_mobile_artifacts") or payload.get("keep_recent"), 200)
    expired_only = bool(payload.get("expired_only", False))
    result = MobileArtifactStore().cleanup(expired_only=expired_only, keep_recent=keep_recent)
    return {"message": f"已清理 {result.get('removed', 0)} 个移动端 artifact。", **result}


def _cleanup_android_command_history(payload: dict[str, Any]) -> dict[str, Any]:
    keep_commands = _int(payload.get("keep_android_commands"), DEFAULT_KEEP_ANDROID_COMMANDS)
    keep_history = _int(payload.get("keep_android_history"), DEFAULT_KEEP_ANDROID_HISTORY)
    result = AndroidCompanionStore().cleanup_history(keep_recent_commands=keep_commands, keep_recent_history=keep_history)
    removed = int(result.get("removed_command_records") or 0) + int(result.get("removed_history_events") or 0)
    return {"message": f"已清理 {removed} 条 Android 命令/历史记录。", **result}


def _cleanup_knowledge_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    keep_jobs = max(0, _int(payload.get("keep_kb_jobs"), DEFAULT_KEEP_KB_JOBS))
    history = load_knowledge_job_history()
    jobs = [dict(item) for item in history.get("jobs") or [] if isinstance(item, dict)]
    kept = jobs[-keep_jobs:] if keep_jobs else []
    history["jobs"] = kept
    saved = save_knowledge_job_history(history)
    removed = max(0, len(jobs) - len(kept))
    return {"ok": True, "status": "cleaned", "message": f"已清理 {removed} 条 KB job 历史。", "removed": removed, "remaining": len(saved.get("jobs") or [])}


def _cleanup_jsonl_audit(path: Path, *, keep: int, label: str) -> dict[str, Any]:
    keep_recent = max(0, keep)
    snapshot = _jsonl_snapshot(path, "")
    if not path.exists():
        return {"ok": True, "status": "skipped", "message": f"{label} 不存在，无需清理。", "removed": 0, "remaining": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "status": "failed", "message": f"{label} 读取失败：{type(exc).__name__}: {exc}", "removed": 0, "remaining": int(snapshot.get("count") or 0)}
    valid_lines = [line for line in lines if line.strip() and _jsonl_line_is_object(line)]
    kept = valid_lines[-keep_recent:] if keep_recent else []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "status": "failed", "message": f"{label} 写入失败：{type(exc).__name__}: {exc}", "removed": 0, "remaining": len(valid_lines)}
    removed = max(0, len(valid_lines) - len(kept)) + max(0, len([line for line in lines if line.strip()]) - len(valid_lines))
    return {"ok": True, "status": "cleaned", "message": f"已清理 {removed} 条 {label} 记录。", "removed": removed, "remaining": len(kept)}


def _truncate_large_logs(payload: dict[str, Any], *, project_root: str | os.PathLike[str] | None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    keep_bytes = max(1024, _int(payload.get("keep_log_bytes"), DEFAULT_KEEP_LOG_BYTES))
    truncated: list[dict[str, Any]] = []
    for item in _log_file_snapshots(root):
        path = Path(str(item.get("path") or ""))
        size = int(item.get("size_bytes") or 0)
        if size <= keep_bytes or not path.exists() or not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, size - keep_bytes))
                tail = handle.read()
            path.write_bytes(tail)
        except OSError as exc:
            truncated.append({"path": str(path), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        truncated.append({"path": str(path), "ok": True, "previous_size_bytes": size, "size_bytes": len(tail)})
    return {"ok": True, "status": "cleaned", "message": f"已截断 {sum(1 for item in truncated if item.get('ok'))} 个大日志文件。", "truncated": truncated, "removed": len(truncated)}


def _cleanup_generated_build_artifacts(*, project_root: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Remove only verification outputs created under the project build cache."""

    root = Path(project_root or Path.cwd()).resolve()
    build_root = (root / "state" / "build").resolve()
    removed: list[dict[str, Any]] = []
    for relative in GENERATED_BUILD_ARTIFACTS:
        target = (root / relative).resolve()
        if not target.is_relative_to(build_root):
            return {"ok": False, "status": "unsafe_path", "message": f"拒绝清理越界路径：{target}", "removed": []}
        if not target.exists():
            continue
        if not target.is_dir():
            return {"ok": False, "status": "unsafe_target", "message": f"拒绝清理非目录：{target}", "removed": removed}
        size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
        try:
            shutil.rmtree(target)
        except OSError as exc:
            removed.append({"path": str(target), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        removed.append({"path": str(target), "ok": True, "previous_size_bytes": size})
    succeeded = sum(1 for item in removed if item.get("ok"))
    return {
        "ok": all(bool(item.get("ok")) for item in removed),
        "status": "cleaned" if succeeded else "skipped",
        "message": f"已清理 {succeeded} 个生成验证目录。",
        "removed": removed,
    }


def _generated_build_artifact_snapshot(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for relative in GENERATED_BUILD_ARTIFACTS:
        target = (root / relative).resolve()
        if not target.exists() or not target.is_dir():
            continue
        size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
        entries.append({"path": str(target), "size_bytes": size})
    return {"count": len(entries), "size_bytes": sum(int(item["size_bytes"]) for item in entries), "paths": entries}


def _cleanup_reproducible_caches(*, project_root: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Remove only fixed, downloadable caches; preserve runtime and user data."""

    root = Path(project_root or Path.cwd()).resolve()
    removed: list[dict[str, Any]] = []
    for relative in REPRODUCIBLE_CACHE_ARTIFACTS:
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            return {"ok": False, "status": "unsafe_path", "message": f"拒绝清理越界路径：{target}", "removed": []}
        if not target.exists():
            continue
        if target.is_symlink() or not target.is_dir():
            return {"ok": False, "status": "unsafe_target", "message": f"拒绝清理非普通目录：{target}", "removed": removed}
        size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
        try:
            shutil.rmtree(target, onerror=_retry_readonly_cache_delete)
        except OSError as exc:
            removed.append({"path": str(target), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        removed.append({"path": str(target), "ok": True, "previous_size_bytes": size})
    succeeded = sum(1 for item in removed if item.get("ok"))
    return {
        "ok": all(bool(item.get("ok")) for item in removed),
        "status": "cleaned" if succeeded else "skipped",
        "message": f"已清理 {succeeded} 个可再生工具缓存目录。",
        "removed": removed,
    }


def _retry_readonly_cache_delete(function: Any, path: str, exc_info: tuple[type[BaseException], BaseException, Any]) -> None:
    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _reproducible_cache_snapshot(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for relative in REPRODUCIBLE_CACHE_ARTIFACTS:
        target = (root / relative).resolve()
        if not target.exists() or target.is_symlink() or not target.is_dir():
            continue
        size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
        entries.append({"path": str(target), "size_bytes": size})
    return {"count": len(entries), "size_bytes": sum(int(item["size_bytes"]) for item in entries), "paths": entries}


def _migrate_state(*, project_root: str | os.PathLike[str] | None, actor: str) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    workflow_store = JsonWorkflowStore(project_root=root)
    workflow_store.list_runs()
    desktop = migrate_desktop_state(actor=actor)
    MobileArtifactStore().snapshot()
    android = AndroidCompanionStore().migrate()
    kb_history = load_knowledge_job_history()
    save_knowledge_job_history(kb_history)
    return {"ok": True, "status": "migrated", "message": "状态文件 schema 已按当前版本重写。", "desktop_state": desktop, "android": android}


def _component(
    component_id: str,
    label: str,
    path: Path,
    schema_version: str,
    *,
    count: int = 0,
    size_bytes: int = 0,
    cleanup_action: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = path if path.is_absolute() else Path.cwd() / path
    exists = resolved.exists()
    if size_bytes == 0 and exists and resolved.is_file():
        size_bytes = resolved.stat().st_size
    needs_attention = count > 500 or size_bytes > DEFAULT_KEEP_LOG_BYTES or bool((details or {}).get("large_file_count"))
    return {
        "component_id": component_id,
        "label": label,
        "path": str(resolved),
        "exists": exists,
        "schema_version": schema_version,
        "count": count,
        "size_bytes": size_bytes,
        "cleanup_action": cleanup_action,
        "needs_attention": bool(needs_attention),
        "details": dict(details or {}),
    }


def _log_file_snapshots(root: Path) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for relative in DEFAULT_LOG_DIRECTORIES:
        directory = root / relative
        if not directory.exists():
            continue
        for path in directory.rglob("*.log"):
            try:
                stat = path.stat()
            except OSError:
                continue
            logs.append({"path": str(path.resolve()), "size_bytes": stat.st_size, "updated_at": stat.st_mtime})
    logs.sort(key=lambda item: int(item.get("size_bytes") or 0), reverse=True)
    return logs


def _jsonl_snapshot(path: Path, schema_version: str) -> dict[str, Any]:
    resolved = path if path.is_absolute() else Path.cwd() / path
    if not resolved.exists():
        return {"path": str(resolved), "schema_version": schema_version, "count": 0, "size_bytes": 0, "invalid_line_count": 0}
    try:
        stat = resolved.stat()
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"path": str(resolved), "schema_version": schema_version, "count": 0, "size_bytes": 0, "invalid_line_count": 0}
    count = 0
    invalid = 0
    detected_schema = schema_version
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if not isinstance(payload, dict):
            invalid += 1
            continue
        count += 1
        if not detected_schema and payload.get("schema_version"):
            detected_schema = str(payload.get("schema_version"))
    return {"path": str(resolved), "schema_version": detected_schema, "count": count, "size_bytes": stat.st_size, "invalid_line_count": invalid}


def _jsonl_line_is_object(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except json.JSONDecodeError:
        return False


def _removed_count(result: dict[str, Any]) -> int:
    for key in ("removed", "removed_command_records", "removed_history_events"):
        if key in result:
            return _int(result.get(key), 0)
    return len(result.get("truncated") or []) if isinstance(result.get("truncated"), list) else 0


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
