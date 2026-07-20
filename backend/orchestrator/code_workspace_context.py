from __future__ import annotations

from backend.executors.base import ExecutionRequest

TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "enabled", "enable"}


def code_context_enabled(metadata: dict | None) -> bool:
    metadata = dict(metadata or {})
    value = metadata.get("include_code_workspace_context")
    if value is None:
        value = metadata.get("code_workspace_context_enabled")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUTHY_VALUES


def build_code_workspace_context(*, worker_pool, repo_path: str = "", include_diff: bool = False, max_chars: int = 3000) -> dict[str, object]:
    record: dict[str, object] = {
        "requested": True,
        "repo_path": repo_path,
        "include_diff": bool(include_diff),
        "read_only": True,
        "sections": [],
    }
    status_execution = worker_pool.execute(
        ExecutionRequest(target="git", operation="git.status", params={"repo_path": repo_path} if repo_path else {}),
        actor="programming_agent",
        metadata={"purpose": "code_workspace_context", "section": "git.status"},
    )
    status_record = _section_from_execution("git.status", status_execution, max_chars=max_chars)
    record["sections"].append(status_record)
    record["success"] = bool(status_record.get("success"))
    if include_diff and status_record.get("success"):
        diff_execution = worker_pool.execute(
            ExecutionRequest(target="git", operation="git.diff", params={"repo_path": repo_path} if repo_path else {}),
            actor="programming_agent",
            metadata={"purpose": "code_workspace_context", "section": "git.diff"},
        )
        diff_record = _section_from_execution("git.diff", diff_execution, max_chars=max_chars)
        record["sections"].append(diff_record)
        record["success"] = bool(record["success"] and diff_record.get("success"))
    summary = format_code_workspace_context(record)
    record["summary"] = summary
    return record


def format_code_workspace_context(record: dict[str, object]) -> str:
    sections = record.get("sections")
    if not isinstance(sections, list) or not sections:
        return ""
    lines = ["代码工作区上下文："]
    for section in sections:
        if not isinstance(section, dict):
            continue
        name = str(section.get("operation") or "git").strip()
        if section.get("success"):
            output = str(section.get("output") or "").strip()
            if output:
                lines.append(f"- {name}:")
                lines.append(_indent(output))
            else:
                lines.append(f"- {name}: clean/no output")
        else:
            message = str(section.get("message") or section.get("error_code") or "unavailable").strip()
            lines.append(f"- {name}: unavailable ({message})")
    return "\n".join(lines).strip()


def _section_from_execution(operation: str, worker_execution, *, max_chars: int) -> dict[str, object]:
    result = worker_execution.result
    data = result.data if isinstance(result.data, dict) else {}
    stdout = str(data.get("stdout") or "").strip()
    stderr = str(data.get("stderr") or "").strip()
    output = stdout or stderr
    if len(output) > max_chars:
        output = output[:max_chars] + "\n...[truncated]"
    return {
        "operation": operation,
        "success": bool(result.success),
        "message": result.message,
        "error_code": result.error_code,
        "output": output,
        "repo_path": str(data.get("repo_path") or ""),
        "worker": worker_execution.worker.snapshot() if worker_execution.worker is not None else None,
        "worker_audit": worker_execution.audit_event.snapshot(),
    }


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())
