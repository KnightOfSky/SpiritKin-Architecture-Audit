from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.executors.base import ExecutionRequest, ExecutionResult

DEFAULT_RUNTIME_TRAJECTORY_LOG = "state/evolution/trajectories.jsonl"
_GROWTH_OBSERVER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spiritkin-growth")


def resolve_runtime_trajectory_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_TRAJECTORY_LOG", DEFAULT_RUNTIME_TRAJECTORY_LOG)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def trajectory_logging_enabled() -> bool:
    return os.getenv("SPIRITKIN_DISABLE_RUNTIME_TRAJECTORY_LOG", "").strip().lower() not in {"1", "true", "yes", "on"}


def append_runtime_trajectory(record: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    normalized = normalize_runtime_trajectory(record)
    target = resolve_runtime_trajectory_log_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    if (
        os.getenv("SPIRITKIN_GROWTH_AUTO_OBSERVE_TRAJECTORIES", "1").strip().lower() not in {"0", "false", "no", "off"}
        and _trajectory_needs_growth_observation(normalized)
    ):
        if os.getenv("SPIRITKIN_GROWTH_OBSERVER_SYNC", "0").strip().lower() in {"1", "true", "yes", "on"}:
            _observe_growth_trajectory(normalized)
        else:
            try:
                _GROWTH_OBSERVER_EXECUTOR.submit(_observe_growth_trajectory, normalized)
            except RuntimeError:
                pass
    return normalized


def _observe_growth_trajectory(trajectory: dict[str, Any]) -> None:
    try:
        from backend.capability.growth.runtime import GrowthRuntime

        GrowthRuntime().observe_trajectory(trajectory)
    except Exception:
        # Growth is an audit-sidecar; a broken candidate writer must never
        # turn a successful trajectory write into a runtime failure.
        pass


def _trajectory_needs_growth_observation(trajectory: dict[str, Any]) -> bool:
    if not bool(trajectory.get("overall_success", False)):
        return True
    metadata = trajectory.get("metadata") if isinstance(trajectory.get("metadata"), dict) else {}
    if str(metadata.get("workflow_name") or "").strip():
        return True
    return any(
        isinstance(step, dict)
        and isinstance(step.get("metadata"), dict)
        and str(step["metadata"].get("workflow_name") or "").strip()
        for step in trajectory.get("steps") or []
    )


def trajectory_from_execution(
    *,
    user_input: str,
    request: ExecutionRequest,
    result: ExecutionResult,
    actor: str = "",
    worker_id: str = "",
    worker_audit: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    success = bool(result.success)
    stage = "executor"
    return normalize_runtime_trajectory(
        {
            "user_input": user_input,
            "overall_success": success,
            "score": 1.0 if success else 0.0,
            "agent_id": actor,
            "domain": _domain_for_request(request),
            "bottleneck_stage": "" if success else stage,
            "execution_result": result.message,
            "steps": [
                {
                    "stage": stage,
                    "detail": result.message,
                    "success": success,
                    "error_code": "" if success else (result.error_code or "executor_failed"),
                    "metadata": {
                        "target": request.target,
                        "operation": request.operation,
                        "params": _safe_params(request.params),
                        "worker_id": worker_id,
                        "worker_audit_id": str((worker_audit or {}).get("event_id") or ""),
                    },
                }
            ],
            "metadata": {
                **dict(metadata or {}),
                "source": "agent_cluster.execution",
                "target": request.target,
                "operation": request.operation,
                "worker_id": worker_id,
                "worker_audit": dict(worker_audit or {}),
                "result_metadata": dict(result.metadata or {}),
            },
        }
    )


def trajectory_from_failure(
    *,
    stage: str,
    actor: str,
    message: str,
    user_input: str = "",
    error_code: str = "",
    tool_name: str = "",
    execution_request: ExecutionRequest | None = None,
    arguments: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_target = execution_request.target if execution_request is not None else ""
    request_operation = execution_request.operation if execution_request is not None else ""
    return normalize_runtime_trajectory(
        {
            "user_input": user_input,
            "overall_success": False,
            "score": 0.0,
            "agent_id": actor,
            "domain": _domain_for_request(execution_request),
            "bottleneck_stage": stage,
            "execution_result": message,
            "steps": [
                {
                    "stage": stage,
                    "detail": message,
                    "success": False,
                    "error_code": error_code or "unknown_failure",
                    "metadata": {
                        "actor": actor,
                        "tool_name": tool_name,
                        "target": request_target,
                        "operation": request_operation,
                        "arguments": _safe_params(arguments if isinstance(arguments, dict) else {}),
                    },
                }
            ],
            "metadata": {
                **dict(metadata or {}),
                "source": "agent_cluster.failure",
                "actor": actor,
                "tool_name": tool_name,
                "target": request_target,
                "operation": request_operation,
            },
        }
    )


def trajectory_from_skill_run(
    *,
    skill_name: str,
    success: bool,
    message: str,
    inputs: dict[str, Any] | None = None,
    step_results: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_inputs = _safe_params(inputs or {})
    steps = []
    for index, result in enumerate(step_results or []):
        result_metadata = getattr(result, "metadata", {}) or {}
        tool_name = str(result_metadata.get("tool_name") or result_metadata.get("name") or "")
        steps.append(
            {
                "stage": "skill_step",
                "detail": str(getattr(result, "message", "") or ""),
                "success": bool(getattr(result, "success", False)),
                "error_code": "" if bool(getattr(result, "success", False)) else str(getattr(result, "error_code", "") or "tool_failed"),
                "metadata": {
                    "step_index": index,
                    "tool_name": tool_name,
                    "result_metadata": _safe_params(dict(result_metadata) if isinstance(result_metadata, dict) else {}),
                },
            }
        )
    if not steps:
        steps.append(
            {
                "stage": "skill",
                "detail": message,
                "success": bool(success),
                "error_code": "" if success else str((metadata or {}).get("error_code") or "skill_failed"),
                "metadata": {"skill_name": skill_name},
            }
        )
    return normalize_runtime_trajectory(
        {
            "user_input": str((inputs or {}).get("user_input") or (inputs or {}).get("query") or ""),
            "overall_success": bool(success),
            "score": 1.0 if success else 0.0,
            "agent_id": str((inputs or {}).get("actor") or "skill_runner"),
            "domain": str((metadata or {}).get("domain") or "skill"),
            "bottleneck_stage": "" if success else "skill",
            "execution_result": message,
            "steps": steps,
            "metadata": {
                **dict(metadata or {}),
                "source": "skill_runner.run",
                "skill_name": skill_name,
                "inputs": safe_inputs,
            },
        }
    )


def trajectory_from_workflow_node(
    *,
    workflow_name: str,
    workflow_version: str = "",
    run_id: str = "",
    node_id: str,
    node_type: str,
    tool_name: str = "",
    skill_name: str = "",
    success: bool,
    message: str,
    error_code: str = "",
    execution_request: ExecutionRequest | None = None,
    outputs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_inputs = _safe_params(inputs or {})
    request_target = execution_request.target if execution_request is not None else ""
    request_operation = execution_request.operation if execution_request is not None else ""
    return normalize_runtime_trajectory(
        {
            "user_input": str((inputs or {}).get("user_input") or (inputs or {}).get("request") or ""),
            "overall_success": bool(success),
            "score": 1.0 if success else 0.0,
            "agent_id": str((inputs or {}).get("actor") or "workflow_runner"),
            "domain": str((metadata or {}).get("domain") or _domain_for_request(execution_request) or "workflow"),
            "bottleneck_stage": "" if success else "workflow_node",
            "execution_result": message,
            "steps": [
                {
                    "stage": "workflow_node",
                    "detail": message,
                    "success": bool(success),
                    "error_code": "" if success else (error_code or "workflow_node_failed"),
                    "metadata": {
                        "workflow_name": workflow_name,
                        "workflow_version": workflow_version,
                        "run_id": run_id,
                        "node_id": node_id,
                        "node_type": node_type,
                        "tool_name": tool_name,
                        "skill_name": skill_name,
                        "target": request_target,
                        "operation": request_operation,
                    },
                }
            ],
            "metadata": {
                **dict(metadata or {}),
                "source": "workflow_runner.node",
                "workflow_name": workflow_name,
                "workflow_version": workflow_version,
                "run_id": run_id,
                "node_id": node_id,
                "node_type": node_type,
                "tool_name": tool_name,
                "skill_name": skill_name,
                "target": request_target,
                "operation": request_operation,
                "inputs": safe_inputs,
                "outputs": _safe_params(outputs or {}),
            },
        }
    )


def trajectory_from_collaboration_worker_event(event: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(event.get("metadata") or {}) if isinstance(event.get("metadata"), dict) else {}
    status = str(event.get("status") or "").strip().lower()
    lifecycle = str(metadata.get("lifecycle") or status).strip().lower()
    output = str(metadata.get("output") or event.get("error") or status or "collaboration worker event")
    success = status in {"processed", "completed"}
    target = str(metadata.get("target") or "")
    operation = str(metadata.get("operation") or "")
    request = ExecutionRequest(target, operation, _safe_params(metadata.get("params") if isinstance(metadata.get("params"), dict) else {})) if target or operation else None
    event_id = str(event.get("event_id") or "")
    return normalize_runtime_trajectory(
        {
            "trajectory_id": f"traj-{event_id}" if event_id else "",
            "created_at": float(event.get("created_at") or time.time()),
            "user_input": str(metadata.get("user_input") or metadata.get("prompt") or metadata.get("output") or ""),
            "overall_success": success,
            "score": 1.0 if success else 0.0,
            "agent_id": str(event.get("agent") or ""),
            "domain": _domain_for_request(request) or "collaboration",
            "bottleneck_stage": "" if success else "collaboration_worker",
            "execution_result": output,
            "steps": [
                {
                    "stage": "collaboration_worker",
                    "detail": output,
                    "success": success,
                    "error_code": "" if success else str(event.get("error") or metadata.get("error_code") or lifecycle or "collaboration_worker_failed"),
                    "metadata": {
                        "event_id": event_id,
                        "status": status,
                        "lifecycle": lifecycle,
                        "message_id": str(event.get("message_id") or ""),
                        "context_id": str(event.get("context_id") or ""),
                        "task_id": str(event.get("task_id") or ""),
                        "transport": str(event.get("transport") or ""),
                        "dry_run": bool(event.get("dry_run", False)),
                        "stream": str(metadata.get("stream") or ""),
                        "target": target,
                        "operation": operation,
                        "tool_call_id": str(metadata.get("tool_call_id") or ""),
                        "tool_result_id": str(metadata.get("tool_result_id") or ""),
                    },
                }
            ],
            "metadata": {
                "source": "collaboration.worker_event",
                "event_id": event_id,
                "agent": str(event.get("agent") or ""),
                "status": status,
                "message_id": str(event.get("message_id") or ""),
                "context_id": str(event.get("context_id") or ""),
                "task_id": str(event.get("task_id") or ""),
                "transport": str(event.get("transport") or ""),
                "dry_run": bool(event.get("dry_run", False)),
                "metadata": _safe_params(metadata),
            },
        }
    )


def trajectory_from_android_command_result(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "").strip().lower()
    success = bool(record.get("success", status == "completed"))
    operation = str(record.get("operation") or "")
    request = ExecutionRequest("android", operation, {})
    message = str(record.get("message") or status or "android command result")
    command_id = str(record.get("command_id") or "")
    return normalize_runtime_trajectory(
        {
            "trajectory_id": f"traj-android-{command_id}" if command_id else "",
            "created_at": float(record.get("completed_at") or record.get("reported_at") or time.time()),
            "user_input": message,
            "overall_success": success,
            "score": 1.0 if success else 0.0,
            "agent_id": str(record.get("actor") or "android_worker"),
            "domain": "mobile",
            "bottleneck_stage": "" if success else "mobile_worker",
            "execution_result": message,
            "steps": [
                {
                    "stage": "mobile_worker",
                    "detail": message,
                    "success": success,
                    "error_code": "" if success else str(record.get("error_code") or "android_command_failed"),
                    "metadata": {
                        "device_id": str(record.get("device_id") or ""),
                        "command_id": command_id,
                        "operation": operation,
                        "status": status,
                        "target": request.target,
                    },
                }
            ],
            "metadata": {
                "source": "android.command_result",
                "device_id": str(record.get("device_id") or ""),
                "command_id": command_id,
                "operation": operation,
                "status": status,
                "result": _safe_params(record.get("result") if isinstance(record.get("result"), dict) else {}),
            },
        }
    )


def trajectory_from_remote_worker_result(
    *,
    node_id: str,
    action: str,
    payload: dict[str, Any] | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    operation = str(payload.get("operation") or action) if isinstance(payload, dict) else action
    target = str(payload.get("target") or "remote_worker") if isinstance(payload, dict) else "remote_worker"
    request = ExecutionRequest(target, operation, dict(payload.get("params") or {}) if isinstance(payload, dict) and isinstance(payload.get("params"), dict) else {})
    success = bool(result.get("ok", result.get("success", False)))
    status = str(result.get("status") or ("completed" if success else "failed"))
    message = str(result.get("message") or result.get("error") or status)
    return normalize_runtime_trajectory(
        {
            "user_input": str((payload or {}).get("user_input") or (payload or {}).get("reason") or ""),
            "overall_success": success,
            "score": 1.0 if success else 0.0,
            "agent_id": str((payload or {}).get("actor") or node_id or "remote_worker"),
            "domain": _domain_for_request(request) or "remote",
            "bottleneck_stage": "" if success else "remote_worker",
            "execution_result": message,
            "steps": [
                {
                    "stage": "remote_worker",
                    "detail": message,
                    "success": success,
                    "error_code": "" if success else str(result.get("error_code") or status or "remote_worker_failed"),
                    "metadata": {
                        "node_id": node_id,
                        "action": action,
                        "target": request.target,
                        "operation": request.operation,
                        "status": status,
                        "package_id": str(result.get("package_id") or result.get("from_package_id") or ""),
                    },
                }
            ],
            "metadata": {
                "source": "remote.worker_result",
                "node_id": node_id,
                "action": action,
                "target": request.target,
                "operation": request.operation,
                "status": status,
                "payload": _safe_params(payload or {}),
                "package_id": str(result.get("package_id") or result.get("from_package_id") or ""),
            },
        }
    )


def normalize_runtime_trajectory(record: dict[str, Any]) -> dict[str, Any]:
    created_at = float(record.get("created_at") or time.time())
    trajectory_id = str(record.get("trajectory_id") or f"traj-{int(created_at * 1000)}")
    steps = [dict(item) for item in record.get("steps") or [] if isinstance(item, dict)]
    return {
        "trajectory_id": trajectory_id,
        "created_at": created_at,
        "user_input": str(record.get("user_input") or ""),
        "overall_success": bool(record.get("overall_success", True)),
        "score": float(record.get("score", 0.0) or 0.0),
        "agent_id": str(record.get("agent_id") or ""),
        "domain": str(record.get("domain") or ""),
        "bottleneck_stage": str(record.get("bottleneck_stage") or ""),
        "ci_log": str(record.get("ci_log") or ""),
        "execution_result": str(record.get("execution_result") or ""),
        "steps": steps,
        "metadata": dict(record.get("metadata") or {}),
    }


def _domain_for_request(request: ExecutionRequest | None) -> str:
    if request is None:
        return ""
    target = str(request.target or "")
    operation = str(request.operation or "")
    source = f"{target}.{operation}".lower()
    if "browser" in source or target in {"local_pc", "desktop", "app", "software", "window", "clipboard", "file"}:
        return "desktop"
    if "git" in source:
        return "repository"
    if "ffmpeg" in source or "media" in source:
        return "media"
    if "knowledge" in source or "rag" in source or "embedding" in source:
        return "knowledge"
    if "openclaw" in source or "arm" in source:
        return "robotics"
    if "android" in source or "ios" in source:
        return "mobile"
    return target or "general"


def _safe_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in params.items():
        key_text = str(key)
        if any(secret in key_text.lower() for secret in ("token", "secret", "password", "api_key")):
            safe[key_text] = "<redacted>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key_text] = value
        elif isinstance(value, (list, tuple)):
            safe[key_text] = [item if isinstance(item, (str, int, float, bool)) or item is None else str(type(item).__name__) for item in value[:20]]
        else:
            safe[key_text] = str(type(value).__name__)
    return safe
