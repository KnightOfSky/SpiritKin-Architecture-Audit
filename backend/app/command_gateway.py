"""Desktop/mobile HTTP command gateway.

TODO(debt-#4): 2100+-line god module (routing table, auth, and every domain
handler in one file). Carve opportunistically by feature, same treatment as
the AgentCluster slices — see docs/ai_collaboration_context.md, 2026-07-03
review item #4. Broad ``except Exception`` sweeps (#6) narrow as parts move.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from backend.agents.base import AgentReply
from backend.app.action_log import build_action_log_snapshot
from backend.app.agent_management import (
    build_agent_management_desktop_snapshot,
    execute_remote_submodule,
    export_remote_submodule,
    push_remote_submodule,
    rollback_remote_submodule,
    save_agent_management_state,
)
from backend.app.audit_reports import build_audit_report_snapshot, generate_audit_report
from backend.app.codex_work_events import TRACE_SCHEMA_VERSION
from backend.app.codex_work_events import install as install_work_event_hooks
from backend.app.collaboration import (
    build_collaboration_snapshot,
    handle_collaboration_action,
    is_human_collaboration_agent,
    post_collaboration_message,
)
from backend.app.collaboration_participants import build_collaboration_participant_registry
from backend.app.context_control import build_context_control_snapshot, load_context_policy, save_context_policy
from backend.app.context_mirror import build_project_context_mirror_from_files
from backend.app.context_write_applier import apply_context_write_intent
from backend.app.desktop_state import load_desktop_state, update_desktop_state
from backend.app.diagnostics import build_desktop_diagnostics_report, handle_desktop_diagnostics_action
from backend.app.ecosystem_review import build_ecosystem_review_snapshot, handle_ecosystem_review_action
from backend.app.evolution_management import build_evolution_management_snapshot, handle_evolution_management_action
from backend.app.file_uploads import ingest_uploaded_files
from backend.app.knowledge_base_management import build_knowledge_base_snapshot, handle_knowledge_base_action
from backend.app.learning_workflow import (
    append_learning_record,
    build_learning_workflow_report,
    build_review_prompt,
    delete_assist_model,
    export_learning_dataset,
    request_model_review,
    request_multi_model_review,
    save_assist_model,
    save_model_provider_settings,
    save_review_committee_policy,
    sync_model_provider,
    test_model_provider_connection,
)
from backend.app.local_model_policy import (
    build_local_model_policy_snapshot,
    evaluate_scheduler_benchmark_suite,
    record_scheduler_benchmark_result,
)
from backend.app.mcp_management import (
    build_mcp_management_snapshot,
    handle_mcp_management_action,
    start_mcp_health_monitor,
)
from backend.app.mobile_management import build_mobile_management_snapshot, handle_mobile_management_action
from backend.app.model_catalog import load_model_catalog, refresh_model_catalog
from backend.app.module_management import build_module_management_snapshot, clear_module_management_cache
from backend.app.operations_center import (
    build_daily_snapshot,
    build_logs_snapshot,
    build_operations_snapshot,
    build_services_snapshot,
    build_sync_snapshot,
    handle_service_action,
    handle_sync_action,
)
from backend.app.project_overview import (
    approve_project_overview_change,
    load_project_overview_review_state,
    reject_project_overview_change,
    update_project_overview,
)
from backend.app.project_runtime import build_project_runtime_snapshot, handle_project_runtime_action
from backend.app.replaceable_brain import build_brain_replacement_snapshot, handle_brain_replacement_action
from backend.app.resource_management import build_resource_management_snapshot, handle_resource_management_action
from backend.app.review_gate import evaluate_review_gate
from backend.app.runtime import (
    EVENT_SCHEMA_VERSION,
    AttachmentRef,
    InteractionInput,
    SpiritKinRuntime,
    dispatch_runtime_event,
    resolve_event_sink_url,
)
from backend.app.runtime_continuity import build_runtime_continuity_snapshot, handle_runtime_continuity_action
from backend.app.search_management import build_search_management_snapshot, handle_search_management_action
from backend.app.service_ports import build_service_port_snapshot, handle_service_port_action, resolve_service_port
from backend.app.skill_router import (
    build_skill_context_pack,
    build_skill_orchestration,
    build_skill_router_snapshot,
    route_skill,
)
from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action
from backend.app.state_maintenance import build_state_maintenance_snapshot, handle_state_maintenance_action
from backend.app.studio_workflow_skills import seed_studio_workflow_skills
from backend.app.tool_authorization import build_tool_authorization_snapshot, handle_tool_authorization_action
from backend.app.workflow_management import build_workflow_management_snapshot, handle_workflow_management_action
from backend.capability.growth.runtime import build_growth_snapshot, handle_growth_action
from backend.channels.wechat_ilink import ILinkIncomingMessage, WeChatILinkChannel, build_ilink_channel_from_env
from backend.code_jury import build_code_jury_snapshot, handle_code_jury_action
from backend.expression.avatar_assets import import_avatar3d_asset, import_live2d_asset
from backend.mobile.artifact_store import MobileArtifactStore
from backend.model.training import (
    TrainingBuildOptions,
    build_cloud_training_package,
    build_training_command,
    build_training_dataset_from_documents,
    build_training_dataset_from_files,
    detect_local_hardware_profile,
    evaluate_dataset_gate,
    load_dataset_registry,
    recommend_training_recipe,
    register_training_dataset,
)
from backend.orchestrator.context_mirror import (
    build_context_write_intent_preview,
)
from backend.orchestrator.context_store import JsonlContextStore
from backend.orchestrator.context_write_intents import (
    approve_context_write_intent,
    context_write_intent_snapshot,
    reject_context_write_intent,
    submit_context_write_intent,
)
from backend.security.http import add_cors_headers, is_local_request, localhost_auth_bypass_enabled, token_matches
from backend.security.safety_control import (
    build_safety_snapshot,
    evaluate_execution_safety,
    evaluate_gateway_request_safety,
    handle_safety_action,
)

DEFAULT_COMMAND_HOST = os.getenv("SPIRITKIN_COMMAND_HOST", "127.0.0.1")
DEFAULT_COMMAND_PORT = resolve_service_port("command_gateway", 8788)
AUTH_HEADER = "X-SpiritKin-Token"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
COLLABORATION_TRACE_SURFACE = "collaboration"


install_work_event_hooks()


def _dispatch_runtime_event_if_available(event: dict[str, object], url: str | None = None) -> bool:
    target_url = url or resolve_event_sink_url()
    if not target_url:
        return False
    if getattr(dispatch_runtime_event, "__module__", "") != "backend.app.runtime":
        return dispatch_runtime_event(target_url, event)
    if not target_url.lower().startswith(("ws://", "wss://")):
        return dispatch_runtime_event(target_url, event)
    try:
        parsed = urlsplit(target_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        with socket.create_connection((host, int(port)), timeout=0.05):
            pass
    except OSError:
        return False
    return dispatch_runtime_event(target_url, event)


def _json_bytes(payload: dict[str, Any], status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _trace_token(value: Any, fallback: str = "item") -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = text.strip("_")[:96]
    return text or fallback


def _collaboration_status(value: Any, *, default: str = "completed") -> str:
    status = str(value or "").strip().lower()
    if status in {"failed", "error", "real_worker_not_enabled"}:
        return "failed" if status != "real_worker_not_enabled" else "blocked"
    if status in {"blocked", "denied", "rejected"}:
        return "blocked"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"queued", "running", "started", "processing", "stream"}:
        return "running"
    return default


def _collaboration_mention_targets(text: str) -> tuple[str, ...]:
    targets: list[str] = []
    try:
        from backend.app.collaboration_participants import (
            build_collaboration_participant_registry,
            normalize_participant_alias,
            resolve_collaboration_participant,
        )

        registry = build_collaboration_participant_registry()
    except Exception:
        registry = None
        normalize_participant_alias = None  # type: ignore[assignment]
        resolve_collaboration_participant = None  # type: ignore[assignment]
    for match in re.finditer(r"(?<![\w./-])@(?P<name>[A-Za-z0-9_.\-\u4e00-\u9fff]{1,64})", text or ""):
        raw = match.group("name").strip()
        target = resolve_collaboration_participant(raw, registry) if registry is not None and resolve_collaboration_participant is not None else ""
        if not target:
            key = re.sub(r"[\s_\-.]+", "", raw.lower())
            target = {
                "codex": "codex",
                "codexcli": "codex",
                "claude": "claude_code",
                "claudecode": "claude_code",
                "claudecli": "claude_code",
                "cc": "claude_code",
                "gpt": "cloud_model",
                "openai": "cloud_model",
                "cloudmodel": "cloud_model",
                "云端模型": "cloud_model",
                "maintext": "main_text",
                "主agent": "main_text",
                "主模型": "main_text",
                "programming": "programming",
                "编程agent": "programming",
                "编程": "programming",
                "visionmodel": "vision_model",
                "视觉agent": "vision_model",
                "视觉": "vision_model",
                "gamedevelopment": "game_development",
                "游戏agent": "game_development",
                "游戏开发": "game_development",
                "ecommerce": "ecommerce",
                "电商agent": "ecommerce",
                "电商": "ecommerce",
                "all": "all",
                "全部": "all",
                "所有": "all",
            }.get(key, "")
        if target and target not in {"human_desktop"} and target not in targets:
            targets.append(target)
    # A model name written as natural text is also an explicit assignment. Keep
    # short/ambiguous aliases mention-only so ordinary prose cannot fan out.
    if registry is not None and normalize_participant_alias is not None:
        participants = registry.get("participants") if isinstance(registry.get("participants"), list) else []
        for item in participants:
            if not isinstance(item, dict) or not bool(item.get("can_chat", False)):
                continue
            target = str(item.get("participant_id") or "").strip()
            if not target or target in {"human_desktop", "all"} or target in targets:
                continue
            aliases = [target, str(item.get("label") or ""), *(str(value) for value in item.get("aliases") or [])]
            matched = next(
                (alias for alias in aliases if _contains_explicit_collaboration_name(text, alias, normalize_participant_alias)),
                "",
            )
            if matched:
                # Labels and runtime model names may be shared by a model entry and
                # its provider entry. Route only to the registry's canonical owner
                # instead of executing the same request twice.
                owner = resolve_collaboration_participant(matched, registry) if resolve_collaboration_participant is not None else ""
                if owner and owner != target:
                    continue
                targets.append(target)
    return tuple(targets)


def _contains_explicit_collaboration_name(text: str, alias: str, normalize_alias: Any) -> bool:
    raw = str(alias or "").strip().lstrip("@")
    if not raw:
        return False
    compact = str(normalize_alias(raw) or "")
    if not compact or compact in {"all", "全部", "所有", "review", "评审", "code", "cc"}:
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", raw))
    if has_cjk:
        return len(compact) >= 2 and raw.lower() in str(text or "").lower()
    if len(compact) < 5 and compact not in {"gpt"}:
        return False
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(raw)}(?![A-Za-z0-9_])", str(text or ""), re.IGNORECASE) is not None


def _strip_collaboration_mentions(text: str) -> str:
    cleaned = str(text or "")
    try:
        from backend.app.collaboration_participants import build_collaboration_participant_registry

        registry = build_collaboration_participant_registry()
        participants = registry.get("participants") if isinstance(registry.get("participants"), list) else []
        aliases = {
            str(alias).strip().lstrip("@")
            for item in participants
            if isinstance(item, dict)
            for alias in [item.get("participant_id"), item.get("label"), *(item.get("aliases") or [])]
            if str(alias or "").strip()
        }
        for alias in sorted(aliases, key=len, reverse=True):
            cleaned = re.sub(
                rf"(?<![\w./-])@{re.escape(alias)}(?=$|[\s,，。！？!?;；:：])",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
    except Exception:
        pass
    cleaned = re.sub(r"(?<![\w./-])@(?P<name>[A-Za-z0-9_.\-\u4e00-\u9fff]{1,64})", "", cleaned).strip()
    cleaned = re.sub(r"^\s*[:：,，]\s*", "", cleaned)
    return cleaned or str(text or "").strip()


def _build_collaboration_context_content(text: str, metadata: dict[str, Any]) -> str:
    lines = [
        "SpiritKin collaboration request.",
        f"Current session: {metadata.get('session_id') or '--'}",
        f"Current project: {metadata.get('project_title') or metadata.get('project_id') or 'Chats'}",
        f"Workspace path: {metadata.get('workspace_path') or metadata.get('workspace') or os.getcwd()}",
    ]
    branch = str(metadata.get("branch") or "").strip()
    if branch:
        lines.append(f"Branch: {branch}")
    lines.extend(["User request:", _strip_collaboration_mentions(text)])
    return "\n".join(lines)


def _build_command_collaboration_response(payload: dict[str, Any], *, client_id: str) -> tuple[int, dict[str, Any]] | None:
    text = str(payload.get("text") or payload.get("command") or "").strip()
    targets = _collaboration_mention_targets(text)
    if not targets:
        return None
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    thread_id = str(
        metadata.get("collaboration_thread_id")
        or metadata.get("session_id")
        or payload.get("session_id")
        or payload.get("conversation_id")
        or f"command-{_trace_token(client_id, 'client')}"
    ).strip()
    content = _build_collaboration_context_content(text, metadata)
    message = post_collaboration_message(
        {
            "task_id": thread_id,
            "thread_id": thread_id,
            "from_agent": "human_desktop",
            "to_agents": list(targets),
            "role": "question",
            "content": content,
            "metadata": {
                "source": "command_gateway_mention_guard",
                "client_id": client_id,
                "permission_mode": metadata.get("permission_mode"),
                "full_access_granted": metadata.get("full_access_granted", False),
            },
        }
    )
    result = {"message": message.snapshot()}
    trace_payload = {
        "action": "post_message",
        "task_id": thread_id,
        "thread_id": thread_id,
        "from_agent": "human_desktop",
        "to_agents": list(targets),
        "request_id": str(metadata.get("request_id") or payload.get("request_id") or ""),
        "session_id": str(metadata.get("session_id") or payload.get("session_id") or ""),
    }
    work_events = _build_collaboration_work_events("post_message", trace_payload, result)
    event = {
        "type": "desktop.collaboration_updated",
        "schema_version": EVENT_SCHEMA_VERSION,
        "payload": {
            "action": "post_message",
            "task_id": thread_id,
            "message_id": message.message_id,
            "updated_at": message.created_at,
        },
    }
    event_sink_url = resolve_event_sink_url()
    if _dispatch_runtime_event_if_available(event, event_sink_url):
        for work_event in work_events:
            _dispatch_runtime_event_if_available(work_event, event_sink_url)
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "reply": None,
        "events": work_events,
        "collaboration_redirect": True,
        "collaboration": {},
        "message": result["message"],
        "event": event,
        "work_events": work_events,
    }


def _collaboration_trace_subject(action: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    message = result.get("message") if isinstance(result.get("message"), dict) else {}
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
    file_claim = result.get("file_claim") if isinstance(result.get("file_claim"), dict) else {}
    context_pack = result.get("context_pack") if isinstance(result.get("context_pack"), dict) else {}
    worker_event = (result.get("agent_route_bus_worker_event") or {}).get("event") if isinstance(result.get("agent_route_bus_worker_event"), dict) else {}
    worker_result = result.get("agent_route_bus_worker") if isinstance(result.get("agent_route_bus_worker"), dict) else {}
    ack = (result.get("agent_route_bus_ack") or {}).get("ack") if isinstance(result.get("agent_route_bus_ack"), dict) else {}
    worker_result_event = worker_result.get("worker_event") if isinstance(worker_result.get("worker_event"), dict) else {}
    tool_call_result = result.get("agent_route_bus_tool_call") if isinstance(result.get("agent_route_bus_tool_call"), dict) else {}
    tool_result_result = result.get("agent_route_bus_tool_result") if isinstance(result.get("agent_route_bus_tool_result"), dict) else {}
    tool_call = tool_call_result.get("tool_call") if isinstance(tool_call_result.get("tool_call"), dict) else {}
    tool_result = tool_result_result.get("tool_result") if isinstance(tool_result_result.get("tool_result"), dict) else {}
    if not tool_call and isinstance(tool_result_result.get("tool_call"), dict):
        tool_call = tool_result_result["tool_call"]

    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or message.get("thread_id") or worker_event.get("context_id") or worker_result_event.get("context_id") or "").strip()
    if not thread_id:
        thread_id = str(thread.get("thread_id") or payload.get("task_id") or message.get("task_id") or task.get("task_id") or tool_call.get("context_id") or "").strip()
    task_id = str(payload.get("task_id") or task.get("task_id") or message.get("task_id") or worker_event.get("task_id") or worker_result_event.get("task_id") or tool_call.get("task_id") or "").strip()
    message_id = str(payload.get("message_id") or message.get("message_id") or worker_event.get("message_id") or worker_result_event.get("message_id") or ack.get("message_id") or tool_call.get("message_id") or "").strip()
    source_id = str(worker_event.get("event_id") or worker_result_event.get("event_id") or tool_result.get("tool_result_id") or tool_call.get("tool_call_id") or "").strip()
    if not source_id:
        source_id = str(
            message_id
            or task.get("task_id")
            or decision.get("decision_id")
            or review.get("review_id")
            or file_claim.get("claim_id")
            or context_pack.get("pack_id")
            or thread.get("thread_id")
            or ack.get("ack_id")
            or f"{action}-{int(time.time() * 1000)}"
        ).strip()

    return {
        "thread_id": thread_id,
        "task_id": task_id,
        "message_id": message_id,
        "source_id": source_id,
        "task": task,
        "message": message,
        "decision": decision,
        "review": review,
        "thread": thread,
        "file_claim": file_claim,
        "context_pack": context_pack,
        "worker_event": worker_event,
        "worker_result": worker_result,
        "worker_result_event": worker_result_event,
        "tool_call": tool_call,
        "tool_result": tool_result,
        "ack": ack,
    }


def _collaboration_trace_agent(action: str, payload: dict[str, Any], subject: dict[str, Any]) -> str:
    worker_event = subject["worker_event"]
    worker_result = subject["worker_result"]
    tool_call = subject.get("tool_call") or {}
    if action in {"record_agent_route_bus_worker_event", "record_route_bus_worker_event"}:
        return str(worker_event.get("agent") or payload.get("agent") or "agent").strip() or "agent"
    if action in {"run_participant_once", "run_collaboration_participant_once", "run_agent_route_bus_worker_once", "route_bus_worker_once", "dry_run_route_bus_worker"}:
        return str(worker_result.get("agent") or payload.get("agent") or "agent").strip() or "agent"
    if action in {"request_tool_call", "request_agent_tool_call", "decide_tool_call", "decide_agent_tool_call", "execute_tool_call", "execute_agent_tool_call"}:
        return str(tool_call.get("agent") or payload.get("agent") or payload.get("actor") or "agent").strip() or "agent"
    if action in {"ack_agent_route_bus_message", "ack_route_bus_message", "ack_agent_message", "mark_message_read", "read_message"}:
        return str(payload.get("consumer") or payload.get("reader") or payload.get("agent") or "agent").strip() or "agent"
    message = subject["message"]
    return str(payload.get("from_agent") or payload.get("actor") or message.get("from_agent") or "collaboration").strip() or "collaboration"


def _collaboration_trace_participant_kind(participant_id: str) -> str:
    normalized = str(participant_id or "").strip().lower()
    if not normalized:
        return ""
    try:
        registry = build_collaboration_participant_registry()
        for item in registry.get("participants") or []:
            if isinstance(item, dict) and str(item.get("participant_id") or "").strip().lower() == normalized:
                return str(item.get("kind") or "").strip()
    except Exception:
        pass
    if normalized.startswith("provider_") or normalized.startswith("model_") or normalized in {
        "cloud_model",
        "openai",
        "gemini",
        "ollama",
        "lmstudio",
        "llamacpp",
        "llama_cpp",
        "llama.cpp",
    }:
        return "model_api"
    if normalized in {"codex", "claude_code"}:
        return "external_cli"
    if normalized in {"local_pc", "remote", "android", "mobile"}:
        return "worker"
    return "local_agent" if normalized.endswith("_agent") else "participant"


def _collaboration_trace_text(action: str, payload: dict[str, Any], subject: dict[str, Any], status: str) -> str:
    thread_id = subject["thread_id"] or subject["task_id"] or "collaboration"
    message = subject["message"]
    task = subject["task"]
    worker_event = subject["worker_event"]
    worker_result = subject["worker_result"]
    tool_call = subject.get("tool_call") or {}
    tool_result = subject.get("tool_result") or {}
    if action in {"request_tool_call", "request_agent_tool_call", "decide_tool_call", "decide_agent_tool_call", "execute_tool_call", "execute_agent_tool_call"}:
        metadata = worker_event.get("metadata") if isinstance(worker_event.get("metadata"), dict) else {}
        output = str(metadata.get("output") or "").strip()
        if output:
            return output
        target = str(tool_call.get("target") or payload.get("target") or "tool").strip()
        operation = str(tool_call.get("operation") or payload.get("operation") or "execute").strip()
        lifecycle = str(metadata.get("lifecycle") or tool_result.get("status") or tool_call.get("status") or status).strip()
        return f"Tool {target}.{operation} {lifecycle}."
    if action in {"post_message", "send_message", "add_message", "request_model_review", "request_review_message"}:
        recipients = message.get("to_agents") if isinstance(message.get("to_agents"), list) else []
        recipient_text = ", ".join(str(item) for item in recipients) or str(message.get("to_model") or "all")
        return f"Collaboration message {message.get('message_id') or subject['message_id']} routed from {message.get('from_agent') or 'agent'} to {recipient_text}."
    if action in {"record_agent_route_bus_worker_event", "record_route_bus_worker_event"}:
        metadata = worker_event.get("metadata") if isinstance(worker_event.get("metadata"), dict) else {}
        output = str(metadata.get("output") or "").strip()
        worker_status = str(worker_event.get("status") or status).strip().lower()
        if worker_status == "stream" and output:
            stream = str(metadata.get("stream") or "stdout").strip() or "stdout"
            return f"{stream}: {output}"
        return f"Agent {worker_event.get('agent') or payload.get('agent') or 'agent'} reported worker status {worker_event.get('status') or status}."
    if action in {"run_participant_once", "run_collaboration_participant_once", "run_agent_route_bus_worker_once", "route_bus_worker_once", "dry_run_route_bus_worker"}:
        return f"Route bus worker {worker_result.get('agent') or payload.get('agent') or 'agent'} finished with status {worker_result.get('status') or status}."
    if action in {"ack_agent_route_bus_message", "ack_route_bus_message", "ack_agent_message"}:
        return f"Agent route bus message {subject['message_id'] or payload.get('message_id') or 'message'} acknowledged."
    if action in {"mark_message_read", "read_message"}:
        return f"Collaboration message {subject['message_id'] or payload.get('message_id') or 'message'} marked read."
    if action in {"create_task", "add_task", "update_task", "set_task_status"}:
        return f"Collaboration task {task.get('task_id') or subject['task_id'] or thread_id} updated: {task.get('title') or payload.get('title') or 'task'}."
    if action in {"record_decision", "add_decision"}:
        return f"Collaboration decision recorded for {thread_id}."
    if action in {"record_review", "add_review"}:
        return f"Collaboration review recorded for {thread_id}."
    if action in {"archive_thread", "restore_thread", "unarchive_thread", "delete_thread", "set_thread_status"}:
        return f"Collaboration thread {thread_id} status changed."
    if action in {"claim_files", "claim_file"}:
        return f"Collaboration file claim recorded for {thread_id}."
    if action in {"build_context_pack", "context_pack"}:
        return f"Collaboration context pack built for {thread_id}."
    return f"Collaboration action {action} completed for {thread_id}."


def _build_collaboration_work_events(action: str, payload: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    if action in {
        "snapshot",
        "refresh",
        "list_participants",
        "participants",
        "participant_registry",
        "list_messages",
        "messages",
        "list_agent_route_bus_messages",
        "agent_route_bus_messages",
        "route_bus_messages",
        "agent_route_bus_worker_status",
        "route_bus_worker_status",
        "dry_run_route_bus_worker_status",
        # 轮次余额查询是 worker 每轮预检的高频只读轮询，
        # 记成 work 事件会在会话里刷出"turn_guard_status completed"噪声卡。
        "turn_guard_status",
        "collaboration_turn_guard_status",
        "turn_status",
    }:
        return []

    subject = _collaboration_trace_subject(action, payload, result)
    run_key = subject["thread_id"] or subject["task_id"] or subject["source_id"] or action
    run_id = f"collab-{_trace_token(run_key, 'run')}"
    source_id = _trace_token(subject["source_id"], action)
    root_span = f"{run_id}:root"
    action_span = f"{run_id}:{_trace_token(action, 'action')}:{source_id}"
    now_seq = int(time.time() * 1_000_000)

    worker_status = ""
    if subject["worker_event"]:
        worker_status = str(subject["worker_event"].get("status") or "")
    elif subject["worker_result"]:
        worker_status = str(subject["worker_result"].get("status") or "")
    status = _collaboration_status(worker_status, default="completed")
    agent_id = _collaboration_trace_agent(action, payload, subject)
    participant_kind = _collaboration_trace_participant_kind(agent_id)
    action_text = _collaboration_trace_text(action, payload, subject, status)
    phase = "tool" if "tool_call" in action else "execution" if "worker" in action or "participant" in action else "route" if "message" in action or "route_bus" in action else "agent"
    worker_metadata = subject["worker_event"].get("metadata") if isinstance(subject["worker_event"].get("metadata"), dict) else {}
    lifecycle = str(worker_metadata.get("lifecycle") or "").strip()
    if lifecycle in {"tool_requested", "permission_required", "approved", "denied", "tool_blocked", "tool_running", "tool_completed", "tool_failed"}:
        phase = "tool"
    tool_call = subject.get("tool_call") or {}
    tool_result = subject.get("tool_result") or {}
    detail = {
        "phase": phase,
        "surface": COLLABORATION_TRACE_SURFACE,
        "action": action,
        "thread_id": subject["thread_id"],
        "task_id": subject["task_id"],
        "message_id": subject["message_id"],
        "agent_id": agent_id,
        "participant_id": agent_id,
        "kind": participant_kind,
        "run_id": run_id,
        "span_id": action_span,
        "parent_id": root_span,
        "source_id": subject["source_id"],
        "tool_call_id": "",
        "target": "",
        "operation": "",
        "stream": "",
        "output": "",
        "status": status,
    }
    if lifecycle:
        detail["lifecycle"] = lifecycle
    if tool_call:
        detail["tool_call"] = tool_call
        detail["tool_call_id"] = str(tool_call.get("tool_call_id") or "")
        detail["target"] = str(tool_call.get("target") or "")
        detail["operation"] = str(tool_call.get("operation") or "")
        detail["requires_review"] = bool(tool_call.get("requires_review", False))
    if tool_result:
        detail["tool_result"] = tool_result
        detail["tool_result_id"] = str(tool_result.get("tool_result_id") or "")
    if worker_metadata:
        detail["stream"] = str(worker_metadata.get("stream") or "")
        detail["output"] = str(worker_metadata.get("output") or "")
        if "tool_call_id" in worker_metadata:
            detail["tool_call_id"] = str(worker_metadata.get("tool_call_id") or detail.get("tool_call_id") or "")
        if "tool_result_id" in worker_metadata:
            detail["tool_result_id"] = str(worker_metadata.get("tool_result_id") or detail.get("tool_result_id") or "")
        if "target" in worker_metadata:
            detail["target"] = str(worker_metadata.get("target") or detail.get("target") or "")
        if "operation" in worker_metadata:
            detail["operation"] = str(worker_metadata.get("operation") or detail.get("operation") or "")
        if "reasoning_visibility" in worker_metadata:
            detail["reasoning_visibility"] = str(worker_metadata.get("reasoning_visibility") or "")
    for key in ("task", "message", "decision", "review", "thread", "file_claim", "context_pack", "worker_event", "worker_result", "ack"):
        if subject[key]:
            detail[key] = subject[key]

    def event(seq_offset: int, text: str, *, span_id: str, parent_id: str, kind: str, event_status: str, is_terminal: bool, event_detail: dict[str, Any]) -> dict[str, Any]:
        seq = now_seq + seq_offset
        normalized_detail = {
            **event_detail,
            "participant_id": agent_id,
            "kind": participant_kind,
            "run_id": run_id,
            "span_id": span_id,
            "parent_id": parent_id,
            "status": event_status,
        }
        return {
            "type": "assistant.work_updated",
            "schema_version": EVENT_SCHEMA_VERSION,
            "payload": {
                "trace_schema_version": TRACE_SCHEMA_VERSION,
                "event_id": f"{run_id}:{source_id}:{seq}",
                "run_id": run_id,
                "seq": seq,
                "span_id": span_id,
                "parent_id": parent_id,
                "agent_id": agent_id,
                "status": event_status,
                "is_terminal": is_terminal,
                "text": text,
                "kind": kind,
                "channel": COLLABORATION_TRACE_SURFACE,
                "surface": COLLABORATION_TRACE_SURFACE,
                "request_id": str(payload.get("request_id") or ""),
                "session_id": str(payload.get("session_id") or ""),
                "detail": normalized_detail,
            },
        }

    message = subject["message"]
    recipients = [str(item).strip() for item in message.get("to_agents") or [] if str(item).strip()]
    is_model_dispatch = (
        action in {"post_message", "send_message", "add_message", "request_model_review", "request_review_message"}
        and is_human_collaboration_agent(message.get("from_agent") or payload.get("from_agent"))
        and any(not is_human_collaboration_agent(agent) and agent != "all" for agent in recipients)
    )
    if is_model_dispatch:
        route_verdict = message.get("route_verdict") if isinstance(message.get("route_verdict"), dict) else {}
        route_bus_event = message.get("route_bus_event") if isinstance(message.get("route_bus_event"), dict) else {}
        model_recipients = [
            item for item in recipients
            if not is_human_collaboration_agent(item) and item != "all"
        ]
        participant_registry = build_collaboration_participant_registry()
        participants = participant_registry.get("participants") if isinstance(participant_registry.get("participants"), list) else []
        participants_by_id = {
            str(item.get("participant_id") or ""): item
            for item in participants
            if isinstance(item, dict) and str(item.get("participant_id") or "")
        }
        call_targets: list[dict[str, str]] = []
        for target_id in model_recipients:
            participant = participants_by_id.get(target_id, {})
            participant_metadata = participant.get("metadata") if isinstance(participant.get("metadata"), dict) else {}
            call_targets.append(
                {
                    "agent_id": target_id,
                    "label": str(participant.get("label") or target_id),
                    "kind": str(participant.get("kind") or "agent"),
                    "provider": str(participant_metadata.get("provider") or ""),
                    "model": str(participant_metadata.get("model") or participant_metadata.get("command") or ""),
                }
            )
        target_text = ", ".join(
            str(item.get("label") or item.get("agent_id") or "") for item in call_targets
        ) or ", ".join(model_recipients) or "target model"
        route_allowed = bool(route_verdict.get("allowed", False))
        mirrored = bool(route_bus_event.get("mirrored", False))
        bus_allowed = bool(route_bus_event.get("route_allowed", mirrored))
        bus_ok = mirrored and bus_allowed
        route_reason = str(route_verdict.get("reason") or "").strip()
        bus_reason = str(route_bus_event.get("route_reason") or route_bus_event.get("error") or "").strip()
        dispatch_detail = {
            **detail,
            "card_kind": "model_dispatch",
            "targets": recipients,
            "call_targets": call_targets,
            "route_verdict": route_verdict,
            "route_bus_event": route_bus_event,
        }
        return [
            event(
                1,
                f"已接收协作请求 {subject['message_id']}，目标：{target_text}。",
                span_id=f"{action_span}:accepted",
                parent_id=root_span,
                kind="thought",
                event_status="completed",
                is_terminal=False,
                event_detail={**dispatch_detail, "phase": "route", "dispatch_stage": "accepted"},
            ),
            event(
                2,
                f"路由策略{'允许' if route_allowed else '阻止'}仅投递给 {target_text}"
                + (f"：{route_reason}" if route_reason else "。"),
                span_id=f"{action_span}:policy",
                parent_id=root_span,
                kind="thought",
                event_status="completed" if route_allowed else "failed",
                is_terminal=False,
                event_detail={**dispatch_detail, "phase": "route", "dispatch_stage": "policy"},
            ),
            event(
                3,
                f"Agent Route Bus {'已接收' if bus_ok else '已拒绝'}发往 {target_text} 的消息"
                + (f"：{bus_reason}" if bus_reason else "。"),
                span_id=f"{action_span}:route_bus",
                parent_id=root_span,
                kind="command",
                event_status="completed" if bus_ok else "failed",
                is_terminal=True,
                event_detail={**dispatch_detail, "phase": "route", "dispatch_stage": "route_bus"},
            ),
        ]

    return [
        event(
            1,
            f"Collaboration action {action} started.",
            span_id=root_span,
            parent_id="",
            kind="thought",
            event_status="started",
            is_terminal=False,
            event_detail={**detail, "phase": "agent", "lifecycle": "started"},
        ),
        event(
            2,
            action_text,
            span_id=action_span,
            parent_id=root_span,
            kind="command" if phase == "execution" else "thought",
            event_status=status,
            is_terminal=False,
            event_detail={**detail, "lifecycle": detail.get("lifecycle") or "completed"},
        ),
        event(
            3,
            "Collaboration update completed.",
            span_id=root_span,
            parent_id="",
            kind="status",
            event_status=status,
            is_terminal=True,
            event_detail={**detail, "phase": "agent", "lifecycle": "terminal"},
        ),
    ]


def token_is_authorized(headers: Any, expected_token: str | None = None, *, client_ip: str = "") -> bool:
    token = (expected_token if expected_token is not None else os.getenv("SPIRITKIN_MOBILE_TOKEN", "")).strip()
    if not token:
        # No token configured: only the explicit dev bypass allows access, and only
        # for requests that are actually local (client_ip preferred over Host header).
        return localhost_auth_bypass_enabled() and is_local_request(headers, client_ip=client_ip)
    return token_matches(headers, expected_token=token, header_name=AUTH_HEADER)


def build_gateway_security_context(headers: Any, *, expected_token: str | None = None, client_ip: str = "") -> dict[str, Any]:
    token = (expected_token if expected_token is not None else os.getenv("SPIRITKIN_MOBILE_TOKEN", "")).strip()
    host = str(headers.get("Host") or "").split(":")[0]
    auth_header = str(headers.get(AUTH_HEADER) or "").strip()
    authorization = str(headers.get("Authorization") or "").strip()
    auth_method = "none"
    if auth_header:
        auth_method = AUTH_HEADER
    elif authorization.startswith("Bearer "):
        auth_method = "bearer"
    local_request = is_local_request(headers, client_ip=client_ip)
    authenticated = token_is_authorized(headers, token, client_ip=client_ip)
    return {
        "host": host,
        "client_ip": client_ip,
        "local_request": local_request,
        "public_access": not local_request,
        "token_required": bool(token) or not localhost_auth_bypass_enabled(),
        "localhost_bypass_enabled": localhost_auth_bypass_enabled(),
        "authenticated": authenticated,
        "auth_method": auth_method,
    }


def build_command_response(runtime: SpiritKinRuntime, payload: dict[str, Any], *, client_id: str = "mobile") -> tuple[int, dict[str, Any]]:
    text = str(payload.get("text") or payload.get("command") or "").strip()
    if not text:
        return 400, {"ok": False, "error": "missing text/command"}
    safety = evaluate_execution_safety(target="command_gateway", operation="command", actor=client_id)
    if not safety.allowed:
        return 423, {
            "ok": False,
            "schema_version": EVENT_SCHEMA_VERSION,
            "error": safety.error_code,
            "message": "安全暂停已开启，当前不会继续规划或执行命令。",
            "safety": safety.snapshot(),
        }

    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("client_id", client_id)
    metadata.setdefault("client_type", "mobile")
    # Full-access is a local desktop confirmation state. Never accept the
    # client-controlled bypass flags from a remote/mobile caller.
    if str(client_id).strip() not in {"127.0.0.1", "::1", "localhost", "desktop", "wpf_desktop"}:
        metadata.pop("permission_mode", None)
        metadata.pop("full_access_granted", None)
    guarded_payload = {**payload, "metadata": metadata}
    collaboration_response = _build_command_collaboration_response(guarded_payload, client_id=client_id)
    if collaboration_response is not None:
        return collaboration_response

    visual_context = str(payload.get("visual_context") or "")
    channel = str(payload.get("channel") or "mobile")
    attachments = _parse_attachment_refs(payload.get("attachments") or metadata.get("attachments") or [])
    documents = payload.get("documents") or metadata.get("documents") or []
    if isinstance(documents, list) and documents:
        metadata["attachment_documents"] = _summarize_attachment_documents(documents)
        metadata["attachment_document_count"] = len(documents)
    if attachments:
        metadata["attachment_count"] = len(attachments)

    interaction = InteractionInput(
        text=text,
        channel=channel,
        visual_context=visual_context,
        attachments=tuple(attachments),
        metadata=metadata,
    )
    try:
        reply = runtime.handle_input(interaction)
    except Exception as exc:
        return _build_runtime_failure_response(exc, interaction)
    if reply is None:
        return 204, {"ok": True, "reply": None}
    response_kind = reply.metadata.get("response_kind") if isinstance(getattr(reply, "metadata", None), dict) else ""
    if response_kind in {"stale_request", "request_cancelled"}:
        return 204, {
            "ok": True,
            "reply": None,
            "stale": response_kind == "stale_request",
            "cancelled": response_kind == "request_cancelled",
            "request_id": reply.metadata.get("request_id", ""),
        }

    output_payload = SpiritKinRuntime.build_output_payload(reply)
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "reply": output_payload,
        "events": SpiritKinRuntime.build_response_events(reply),
    }


def build_proactive_feedback_response(runtime: SpiritKinRuntime, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    signal_id = str(payload.get("signal_id") or "").strip()
    feedback = str(payload.get("feedback") or "").strip().lower()
    if not signal_id:
        return 400, {"ok": False, "error": "missing signal_id"}
    try:
        event = runtime.record_proactive_feedback(signal_id, feedback)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 200, {"ok": True, "schema_version": EVENT_SCHEMA_VERSION, "event": event}


def build_scheduler_intents_response(runtime: SpiritKinRuntime, *, include_finished: bool = True) -> tuple[int, dict[str, Any]]:
    return 200, {"ok": True, "scheduler": runtime.scheduler_snapshot(include_finished=include_finished)}


def build_scheduler_intents_update_response(runtime: SpiritKinRuntime, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "create").strip().lower()
    try:
        result = runtime.update_scheduled_intent(action, payload)
    except (KeyError, ValueError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 200, {"ok": True, "schema_version": EVENT_SCHEMA_VERSION, "action": action, "result": result}


def build_desktop_memory_response(runtime: SpiritKinRuntime) -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "memory_management": runtime.memory_management_snapshot(),
    }


def build_desktop_memory_update_response(runtime: SpiritKinRuntime, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "resolve_conflict").strip().lower()
    if action not in {"resolve", "resolve_conflict"}:
        return 400, {"ok": False, "error": f"unsupported memory action: {action}"}
    conflict_id = str(payload.get("conflict_id") or "").strip()
    resolution = str(payload.get("resolution") or "").strip()
    if not conflict_id or not resolution:
        return 400, {"ok": False, "error": "conflict_id and resolution are required"}
    try:
        conflict = runtime.resolve_memory_conflict(conflict_id, resolution, reason=str(payload.get("reason") or ""))
    except KeyError as exc:
        return 404, {"ok": False, "error": str(exc)}
    except (RuntimeError, ValueError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "conflict": conflict,
        "memory_management": runtime.memory_management_snapshot(),
    }


def _build_runtime_failure_response(exc: Exception, interaction: InteractionInput) -> tuple[int, dict[str, Any]]:
    detail = " ".join(str(exc).replace("\r", "\n").split())
    if len(detail) > 360:
        detail = detail[:357].rstrip() + "..."
    text = "模型服务暂时不可用，当前消息没有完成处理。"
    if detail:
        text = f"{text}错误：{detail}"
    metadata = dict(interaction.metadata or {})
    reply = AgentReply(
        text=text,
        spoken_text="",
        emotion="error",
        action="shake",
        agent_name="command_gateway",
        metadata={
            "response_kind": "task_failed",
            "speech_disabled": True,
            "error": detail,
            "error_type": type(exc).__name__,
            "input_channel": interaction.channel,
            "client_metadata": metadata,
            "request_id": str(metadata.get("request_id") or ""),
            "session_id": str(metadata.get("session_id") or ""),
        },
    )
    output_payload = SpiritKinRuntime.build_output_payload(reply)
    failure_events = SpiritKinRuntime.build_response_events(reply)
    # 失败也要广播到事件桥：否则分身停留在 user_input 触发的 planning 状态。
    for event in failure_events:
        _dispatch_runtime_event_if_available(event)
    return 200, {
        "ok": False,
        "schema_version": EVENT_SCHEMA_VERSION,
        "error": "runtime_failed",
        "reply": output_payload,
        "events": failure_events,
    }


def _parse_attachment_refs(raw_attachments: Any) -> list[AttachmentRef]:
    refs: list[AttachmentRef] = []
    if not isinstance(raw_attachments, list):
        return refs
    for index, item in enumerate(raw_attachments, start=1):
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or f"file_{index}").strip()
        name = str(item.get("name") or item.get("relative_path") or file_id).strip()
        if not file_id or not name:
            continue
        refs.append(
            AttachmentRef(
                file_id=file_id,
                name=name,
                mime_type=str(item.get("mime_type") or "application/octet-stream"),
                uri=str(item.get("uri") or "") or None,
                size_bytes=int(item["size_bytes"]) if str(item.get("size_bytes") or "").isdigit() else None,
                purpose=str(item.get("purpose") or "user_upload"),
            )
        )
    return refs


def _summarize_attachment_documents(documents: list[Any], *, max_documents: int = 6, max_chars_each: int = 1600) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for item in documents[:max_documents]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        summaries.append(
            {
                "path": str(item.get("path") or item.get("name") or ""),
                "text_preview": text[:max_chars_each],
            }
        )
    return summaries


def build_training_dataset_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "build").strip().lower()
    if action in {"list_registry", "list_datasets", "dataset_registry"}:
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "dataset_registry": load_dataset_registry(limit=_int_payload(payload, "limit", 100)),
        }
    if action in {"evaluate_dataset_gate", "validate_dataset", "inspect_dataset"}:
        dataset_path = str(payload.get("dataset_path") or payload.get("dataset") or payload.get("path") or "").strip()
        if not dataset_path:
            return 400, {"ok": False, "error": "missing dataset_path"}
        gate = evaluate_dataset_gate(
            dataset_path,
            min_examples=_int_payload(payload, "min_examples", 1),
            allow_secrets=bool(payload.get("allow_secrets", False)),
            allow_high_risk=bool(payload.get("allow_high_risk_training_samples", False)),
        )
        return 200, {
            "ok": gate.allowed,
            "schema_version": EVENT_SCHEMA_VERSION,
            "dataset_gate": gate.snapshot(),
        }
    if action != "build":
        return 400, {"ok": False, "error": f"unsupported training dataset action: {action}"}

    documents = payload.get("documents")
    paths = payload.get("paths")
    if documents is not None and not isinstance(documents, list):
        return 400, {"ok": False, "error": "documents must be a list"}
    if paths is not None and not isinstance(paths, list):
        return 400, {"ok": False, "error": "paths must be a list"}
    if not documents and not paths:
        return 400, {"ok": False, "error": "missing documents or paths"}

    try:
        output_path = _resolve_training_output_path(str(payload.get("output_path") or "state/training/uploaded_dataset.jsonl"))
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    options = TrainingBuildOptions(
        mode=str(payload.get("mode") or "instruction"),
        max_chars=_int_payload(payload, "max_chars", 6000),
        chunk_chars=_int_payload(payload, "chunk_chars", 1800),
        overlap_chars=_int_payload(payload, "overlap_chars", 120),
        include_code=bool(payload.get("include_code", True)),
        system_prompt=str(payload.get("system_prompt") or TrainingBuildOptions.system_prompt),
    )
    try:
        if documents:
            report = build_training_dataset_from_documents(documents, output_path, options=options)
        else:
            report = build_training_dataset_from_files([str(path) for path in paths or []], output_path, options=options)
    except Exception as exc:
        return 500, {"ok": False, "error": f"dataset build failed: {type(exc).__name__}", "detail": str(exc)}

    profile = detect_local_hardware_profile()
    recipe = recommend_training_recipe(profile)
    base_model = str(payload.get("base_model") or "Qwen/Qwen2.5-3B-Instruct")
    output_dir = str(payload.get("adapter_output_dir") or "runs/lora/spiritkin")
    gate = evaluate_dataset_gate(
        report.output_path,
        min_examples=_int_payload(payload, "min_examples", 1),
        allow_secrets=bool(payload.get("allow_secrets", False)),
        allow_high_risk=bool(payload.get("allow_high_risk_training_samples", False)),
    )
    dataset_card = register_training_dataset(
        report.output_path,
        source="uploaded_documents" if documents else "source_paths",
        source_counts={"sources": int(report.source_count), "examples": int(report.example_count)},
        excluded_count=len(report.skipped),
        base_model_target=base_model,
        reviewer=str(payload.get("reviewer") or ""),
        metadata={
            "mode": options.mode,
            "max_chars": options.max_chars,
            "chunk_chars": options.chunk_chars,
            "overlap_chars": options.overlap_chars,
            "include_code": options.include_code,
            "output_dir": output_dir,
        },
        gate=gate,
    )
    command = build_training_command(
        dataset_path=report.output_path,
        output_dir=output_dir,
        base_model=base_model,
        recipe=recipe,
    )
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "dataset": report.snapshot(),
        "dataset_card": dataset_card.snapshot(),
        "dataset_gate": gate.snapshot(),
        "dataset_registry": load_dataset_registry(limit=20),
        "hardware": profile.__dict__,
        "recipe": recipe.snapshot(),
        "training_command": command,
        "training_command_text": " ".join(command),
    }


def build_training_cloud_package_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    dataset_path = str(payload.get("dataset_path") or payload.get("dataset") or "").strip()
    if not dataset_path:
        return 400, {"ok": False, "error": "missing dataset_path"}
    dataset_gate = evaluate_dataset_gate(
        dataset_path,
        min_examples=_int_payload(payload, "min_examples", 1),
        allow_secrets=bool(payload.get("allow_secrets", False)),
        allow_high_risk=bool(payload.get("allow_high_risk_training_samples", False)),
    )
    if not dataset_gate.allowed:
        return 422, {"ok": False, "error": "dataset_gate_failed", "dataset_gate": dataset_gate.snapshot()}
    decision = evaluate_review_gate(payload, "training.cloud_package", subject=dataset_path)
    if not decision.allowed:
        return 403, {"ok": False, "error": "review_required", "review_gate": decision.snapshot(), "dataset_gate": dataset_gate.snapshot()}
    base_model = str(payload.get("base_model") or "Qwen/Qwen3-Coder-30B-A3B-Instruct").strip()
    try:
        package = build_cloud_training_package(
            dataset_path=dataset_path,
            base_model=base_model,
            package_id=str(payload.get("package_id") or ""),
            adapter_output_dir=str(payload.get("adapter_output_dir") or "outputs/spiritkin-lora"),
            notes=str(payload.get("notes") or ""),
        )
    except Exception as exc:
        return 400, {"ok": False, "error": f"cloud training package failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "cloud_training_package": package.snapshot(),
        "review_gate": decision.snapshot(),
        "dataset_gate": dataset_gate.snapshot(),
    }


def build_model_catalog_response(payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    catalog = load_model_catalog()
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "model_catalog": catalog,
        "local_model_policy": build_local_model_policy_snapshot(model_catalog=catalog),
        "brain_replacement": build_brain_replacement_snapshot(model_catalog=catalog),
    }


def build_model_catalog_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "refresh").strip().lower()
    if action in {"register_brain_adapter", "evaluate_brain_replacement", "brain_replacement_snapshot"}:
        result = handle_brain_replacement_action(payload)
        status = 200 if result.get("ok") else 400
        catalog = load_model_catalog()
        result.setdefault("schema_version", EVENT_SCHEMA_VERSION)
        result.setdefault("model_catalog", catalog)
        result.setdefault("local_model_policy", build_local_model_policy_snapshot(model_catalog=catalog))
        return status, result
    if action not in {"refresh", "sync"}:
        if action != "evaluate_scheduler_benchmark":
            return 400, {"ok": False, "error": f"unsupported model catalog action: {action}"}
        outputs_by_case_id = dict(payload.get("outputs_by_case_id") or {})
        result = evaluate_scheduler_benchmark_suite(outputs_by_case_id)
        history_record = record_scheduler_benchmark_result(result, outputs_by_case_id=outputs_by_case_id)
        catalog = load_model_catalog()
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "model_catalog": catalog,
            "local_model_policy": build_local_model_policy_snapshot(model_catalog=catalog),
            "brain_replacement": build_brain_replacement_snapshot(model_catalog=catalog),
            "scheduler_benchmark_result": result,
            "scheduler_benchmark_history_record": history_record,
        }
    try:
        model_ids = [str(item) for item in payload.get("model_ids") or []] if isinstance(payload.get("model_ids"), list) else None
        catalog = refresh_model_catalog(model_ids=model_ids, timeout=float(payload.get("timeout") or 8.0))
    except Exception as exc:
        return 502, {"ok": False, "error": f"model catalog refresh failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "model_catalog": catalog,
        "local_model_policy": build_local_model_policy_snapshot(model_catalog=catalog),
        "brain_replacement": build_brain_replacement_snapshot(model_catalog=catalog),
    }


def build_avatar_asset_import_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    asset_type = str(payload.get("asset_type") or payload.get("type") or "").strip().lower()
    source_path = str(payload.get("source_path") or payload.get("path") or "").strip()
    if asset_type not in {"live2d", "avatar3d", "3d"}:
        return 400, {"ok": False, "error": "asset_type must be live2d or avatar3d"}
    if not source_path:
        return 400, {"ok": False, "error": "missing source_path"}

    role = str(payload.get("role") or ("spirit3d" if asset_type in {"avatar3d", "3d"} else "spirit"))
    display_name = str(payload.get("display_name") or payload.get("name") or "")
    try:
        if asset_type == "live2d":
            result = import_live2d_asset(source_path, role=role, display_name=display_name)
        else:
            result = import_avatar3d_asset(source_path, role=role, display_name=display_name)
    except Exception as exc:
        return 400, {"ok": False, "error": f"avatar asset import failed: {type(exc).__name__}", "detail": str(exc)}

    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "asset": result.snapshot(),
        "reload_hint": "Refresh live2d.html/avatar_3d.html or reload the native shell webview.",
    }


def build_attachments_ingest_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return 400, {"ok": False, "error": "files must be a non-empty list"}
    try:
        report = ingest_uploaded_files(files, purpose=str(payload.get("purpose") or "user_upload"))
    except Exception as exc:
        return 500, {"ok": False, "error": f"attachment ingest failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "upload": report.snapshot(),
    }


def build_mobile_artifacts_ingest_response(payload: dict[str, Any], *, client_id: str = "mobile") -> tuple[int, dict[str, Any]]:
    try:
        result = MobileArtifactStore().ingest(payload, source=str(payload.get("source") or client_id or "mobile"), device_id=str(payload.get("device_id") or client_id or ""))
    except Exception as exc:
        return 500, {"ok": False, "error": f"mobile artifact ingest failed: {type(exc).__name__}", "detail": str(exc)}
    return (200 if result.get("ok") else 400), {
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
        "mobile_artifacts": MobileArtifactStore().snapshot(),
    }


def build_desktop_state_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "state": load_desktop_state(),
    }


def build_desktop_state_update_response(payload: dict[str, Any], *, client_id: str = "desktop") -> tuple[int, dict[str, Any]]:
    try:
        state = update_desktop_state(payload, client_id=client_id)
    except Exception as exc:
        return 400, {"ok": False, "error": f"desktop state update failed: {type(exc).__name__}", "detail": str(exc)}
    event = {
        "type": "desktop.state_updated",
        "schema_version": EVENT_SCHEMA_VERSION,
        "payload": {
            "revision": state.get("revision"),
            "updated_at": state.get("updated_at"),
            "updated_by": state.get("updated_by"),
        },
    }
    dispatch_runtime_event(resolve_event_sink_url(), event)
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "state": state,
        "event": event,
    }


def build_desktop_diagnostics_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "diagnostics": build_desktop_diagnostics_report().snapshot(),
    }


def build_desktop_diagnostics_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_desktop_diagnostics_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"diagnostics action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_operations_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "operations": build_operations_snapshot().snapshot(),
    }


def build_desktop_services_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "services": build_services_snapshot(),
        "service_ports": build_service_port_snapshot(),
    }


def build_desktop_service_ports_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "service_ports": build_service_port_snapshot(),
    }


def build_desktop_service_ports_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_service_port_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"service port action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", False)),
        "schema_version": EVENT_SCHEMA_VERSION,
        "service_port_action": result,
        "service_ports": result.get("service_ports", build_service_port_snapshot()),
    }


def build_desktop_services_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    gateway_self_action = str(payload.get("service_id") or "").strip() == "command_gateway" and str(
        payload.get("action") or ""
    ).strip().lower() in {"stop", "restart"}
    operations = build_operations_snapshot().snapshot() if gateway_self_action else None
    try:
        result = handle_service_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"service action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", False)),
        "schema_version": EVENT_SCHEMA_VERSION,
        "service_action": result,
        "operations": operations if operations is not None else build_operations_snapshot().snapshot(),
    }


def build_desktop_logs_response(log_id: str = "") -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "logs": build_logs_snapshot(log_id=log_id),
    }


def build_desktop_sync_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "sync": build_sync_snapshot(),
    }


def build_desktop_sync_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_sync_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"sync action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_daily_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "daily": build_daily_snapshot(),
    }


def build_desktop_learning_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "learning": build_learning_workflow_report().snapshot(),
    }


def build_desktop_learning_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "record").strip().lower()
    if action == "record":
        record = append_learning_record(payload.get("record") if isinstance(payload.get("record"), dict) else payload)
        export = export_learning_dataset()
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "record": record.snapshot(),
            "dataset": export.get("export", {}),
            "dataset_gate": export.get("dataset_gate", {}),
            "dataset_card": export.get("dataset_card", {}),
            "dataset_registry": export.get("dataset_registry", {}),
        }
    if action == "export_dataset":
        export = export_learning_dataset(output_path=payload.get("output_path"))
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "dataset": export.get("export", {}),
            "package": export.get("package", {}),
            "dataset_gate": export.get("dataset_gate", {}),
            "dataset_card": export.get("dataset_card", {}),
            "dataset_registry": export.get("dataset_registry", {}),
        }
    if action == "review_prompt":
        prompt = build_review_prompt(
            str(payload.get("problem") or ""),
            skill_name=str(payload.get("skill_name") or ""),
            context=str(payload.get("context") or ""),
        )
        return 200, {"ok": True, "schema_version": EVENT_SCHEMA_VERSION, "prompt": prompt}
    if action == "model_review":
        review = request_model_review(
            str(payload.get("problem") or ""),
            skill_name=str(payload.get("skill_name") or ""),
            context=str(payload.get("context") or ""),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
        )
        return 200 if review.ok or review.status == "not_configured" else 502, {
            "ok": review.ok,
            "schema_version": EVENT_SCHEMA_VERSION,
            "review": review.snapshot(),
        }
    if action == "multi_model_review":
        review = request_multi_model_review(
            str(payload.get("problem") or ""),
            skill_name=str(payload.get("skill_name") or ""),
            context=str(payload.get("context") or ""),
            model_ids=[str(item) for item in payload.get("model_ids") or []] if isinstance(payload.get("model_ids"), list) else None,
        )
        return 200 if review.ok or review.status == "not_configured" else 502, {
            "ok": review.ok,
            "schema_version": EVENT_SCHEMA_VERSION,
            "multi_model_review": review.snapshot(),
        }
    if action == "save_review_committee_policy":
        policy = save_review_committee_policy(payload.get("policy") if isinstance(payload.get("policy"), dict) else payload)
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "review_committee_policy": policy.snapshot(),
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    if action == "save_provider":
        settings = save_model_provider_settings(payload.get("provider") if isinstance(payload.get("provider"), dict) else payload)
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "provider": settings.snapshot(),
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    if action == "sync_provider_models":
        result = sync_model_provider(payload.get("provider") if isinstance(payload.get("provider"), dict) else payload)
        return 200 if result.ok else 502, {
            "ok": result.ok,
            "schema_version": EVENT_SCHEMA_VERSION,
            "provider_action": result.snapshot(),
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    if action == "test_provider":
        result = test_model_provider_connection(payload.get("provider") if isinstance(payload.get("provider"), dict) else payload)
        return 200 if result.ok else 502, {
            "ok": result.ok,
            "schema_version": EVENT_SCHEMA_VERSION,
            "provider_action": result.snapshot(),
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    if action == "save_assist_model":
        try:
            model = save_assist_model(payload.get("model") if isinstance(payload.get("model"), dict) else payload)
        except ValueError as exc:
            return 400, {"ok": False, "error": "invalid assist model", "detail": str(exc)}
        return 200, {
            "ok": True,
            "schema_version": EVENT_SCHEMA_VERSION,
            "assist_model": model.snapshot(),
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    if action == "delete_assist_model":
        result = delete_assist_model(str(payload.get("model_id") or ""))
        return 200, {
            "ok": bool(result.get("deleted")),
            "schema_version": EVENT_SCHEMA_VERSION,
            **result,
            "learning": build_learning_workflow_report(include_improvement=False).snapshot(),
        }
    return 400, {"ok": False, "error": f"unsupported learning action: {action}"}


def build_desktop_code_jury_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "code_jury": build_code_jury_snapshot(),
    }


def build_desktop_code_jury_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_code_jury_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"code jury action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_collaboration_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "collaboration": build_collaboration_snapshot(),
    }


def build_desktop_collaboration_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_collaboration_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"collaboration action failed: {type(exc).__name__}", "detail": str(exc)}
    action = str(payload.get("action") or "snapshot").strip().lower()
    event = {
        "type": "desktop.collaboration_updated",
        "schema_version": EVENT_SCHEMA_VERSION,
        "payload": {
            "action": action,
            "task_id": str(payload.get("task_id") or ""),
            "message_id": str((result.get("message") or {}).get("message_id") or ""),
            "updated_at": (result.get("collaboration") or {}).get("generated_at"),
        },
    }
    event_sink_url = resolve_event_sink_url()
    event_dispatched = _dispatch_runtime_event_if_available(event, event_sink_url)
    work_events = _build_collaboration_work_events(action, payload, result)
    if event_dispatched:
        for work_event in work_events:
            _dispatch_runtime_event_if_available(work_event, event_sink_url)
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        "event": event,
        "work_events": work_events,
        **result,
    }


def build_desktop_evolution_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "evolution": build_evolution_management_snapshot(),
    }


def build_desktop_evolution_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_evolution_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"evolution action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_growth_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "growth": build_growth_snapshot(),
    }


def build_desktop_growth_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_growth_action(payload)
    except PermissionError as exc:
        return 403, {"ok": False, "error": "growth_review_required", "detail": str(exc)}
    except Exception as exc:
        return 400, {"ok": False, "error": f"growth action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_context_response() -> tuple[int, dict[str, Any]]:
    context_snapshot = build_context_control_snapshot().snapshot()
    runtime_context = build_project_context_mirror_from_files().snapshot(view="task")
    context_ledger = JsonlContextStore().ledger_snapshot(view="task", limit=100)
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "context": context_snapshot,
        "runtime_context": runtime_context,
        "context_ledger": context_ledger,
        "write_intent_preview": build_context_write_intent_preview(),
        "write_intents": context_write_intent_snapshot(),
    }


def build_desktop_context_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "").strip().lower()
    if action in {"submit_write_intent", "approve_write_intent", "reject_write_intent", "apply_write_intent", "list_write_intents"}:
        return _build_desktop_context_write_intent_action_response(action, payload)
    write_intent_payload = payload.get("write_intent") if isinstance(payload.get("write_intent"), dict) else payload.get("context_write_intent")
    reserved = {"write_intent", "context_write_intent", "action"}
    if isinstance(payload.get("policy"), dict):
        policy = save_context_policy(payload["policy"])
    else:
        policy_payload = {key: value for key, value in dict(payload or {}).items() if key not in reserved}
        policy = save_context_policy(policy_payload) if policy_payload else load_context_policy()
    context_snapshot = build_context_control_snapshot().snapshot()
    runtime_context = build_project_context_mirror_from_files().snapshot(view="task")
    context_ledger = JsonlContextStore().ledger_snapshot(view="task", limit=100)
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "context": context_snapshot,
        "runtime_context": runtime_context,
        "context_ledger": context_ledger,
        "policy": policy.snapshot(),
        "write_intent_preview": build_context_write_intent_preview(write_intent_payload if isinstance(write_intent_payload, dict) else None),
        "write_intents": context_write_intent_snapshot(),
    }


def _build_desktop_context_write_intent_action_response(action: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    record = None
    apply_result = None
    if action == "submit_write_intent":
        intent_payload = payload.get("write_intent") if isinstance(payload.get("write_intent"), dict) else payload
        record = submit_context_write_intent(intent_payload)
        ok = record.status != "rejected"
        status_code = 200 if ok else 400
    elif action == "approve_write_intent":
        record = approve_context_write_intent(
            str(payload.get("intent_id") or ""),
            reviewer=str(payload.get("reviewer") or "human"),
            review_note=str(payload.get("review_note") or ""),
        )
        ok = record is not None
        status_code = 200 if ok else 404
    elif action == "reject_write_intent":
        record = reject_context_write_intent(
            str(payload.get("intent_id") or ""),
            reviewer=str(payload.get("reviewer") or "human"),
            review_note=str(payload.get("review_note") or ""),
        )
        ok = record is not None
        status_code = 200 if ok else 404
    elif action == "apply_write_intent":
        apply_result = apply_context_write_intent(
            str(payload.get("intent_id") or ""),
            actor=str(payload.get("actor") or "context_write_applier"),
        )
        record = apply_result.intent
        ok = apply_result.ok
        status_code = 200 if ok else (404 if apply_result.status == "missing" else 409)
    else:
        ok = True
        status_code = 200
    context_snapshot = build_context_control_snapshot().snapshot()
    runtime_context = build_project_context_mirror_from_files().snapshot(view="task")
    context_ledger = JsonlContextStore().ledger_snapshot(view="task", limit=100)
    response = {
        "ok": ok,
        "schema_version": EVENT_SCHEMA_VERSION,
        "context": context_snapshot,
        "runtime_context": runtime_context,
        "context_ledger": context_ledger,
        "write_intent": record.snapshot() if record is not None else {},
        "write_apply": apply_result.snapshot() if apply_result is not None else {},
        "write_intents": context_write_intent_snapshot(),
        "write_intent_preview": build_context_write_intent_preview(),
    }
    if not ok:
        response["error"] = apply_result.error if apply_result is not None else ("context write intent not found" if action != "submit_write_intent" else "invalid context write intent")
    return status_code, response


def build_desktop_project_overview_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "project_overview": load_project_overview_review_state().snapshot(),
    }


def build_desktop_project_overview_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        action = str(payload.get("action") or "save").strip().lower()
        if action == "approve":
            approve_project_overview_change(str(payload.get("change_id") or ""), reviewer=str(payload.get("reviewer") or "human"), review_note=str(payload.get("review_note") or ""))
        elif action == "reject":
            reject_project_overview_change(str(payload.get("change_id") or ""), reviewer=str(payload.get("reviewer") or "human"), review_note=str(payload.get("review_note") or ""))
        else:
            update_project_overview(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"project overview update failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "project_overview": load_project_overview_review_state().snapshot(),
    }


def build_desktop_project_runtime_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "project_runtime": build_project_runtime_snapshot(),
    }


def build_desktop_project_runtime_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_project_runtime_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"project runtime action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", False)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_agent_management_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "agent_management": build_agent_management_desktop_snapshot(),
    }


def build_desktop_agent_management_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "save").strip().lower()
    try:
        if action == "export_remote":
            decision = evaluate_review_gate(payload, "remote.export", subject=",".join(str(item) for item in payload.get("skill_names") or []))
            if not decision.allowed:
                return 403, {"ok": False, "schema_version": EVENT_SCHEMA_VERSION, "error": "review_required", "review_gate": decision.snapshot()}
            export = export_remote_submodule(payload)
            return 200, {
                "ok": True,
                "schema_version": EVENT_SCHEMA_VERSION,
                "agent_management": build_agent_management_desktop_snapshot(),
                "export": export,
                "review_gate": decision.snapshot(),
            }
        if action == "push_remote":
            decision = evaluate_review_gate(payload, "remote.push", subject=str(payload.get("package_path") or ""))
            if not decision.allowed:
                return 403, {"ok": False, "schema_version": EVENT_SCHEMA_VERSION, "error": "review_required", "review_gate": decision.snapshot()}
            push = push_remote_submodule(payload)
            return 200, {
                "ok": bool(push.get("ok", False)),
                "schema_version": EVENT_SCHEMA_VERSION,
                "agent_management": build_agent_management_desktop_snapshot(),
                "push": push,
                "review_gate": decision.snapshot(),
            }
        if action == "execute_remote":
            decision = evaluate_review_gate(payload, "remote.execute", subject=str(payload.get("package_path") or ""))
            if not decision.allowed:
                return 403, {"ok": False, "schema_version": EVENT_SCHEMA_VERSION, "error": "review_required", "review_gate": decision.snapshot()}
            execution = execute_remote_submodule(payload)
            return 200, {
                "ok": bool(execution.get("ok", False)),
                "schema_version": EVENT_SCHEMA_VERSION,
                "agent_management": build_agent_management_desktop_snapshot(),
                "remote_execution": execution,
                "review_gate": decision.snapshot(),
            }
        if action == "rollback_remote":
            decision = evaluate_review_gate(payload, "remote.rollback", subject=str(payload.get("package_path") or payload.get("package_id") or ""))
            if not decision.allowed:
                return 403, {"ok": False, "schema_version": EVENT_SCHEMA_VERSION, "error": "review_required", "review_gate": decision.snapshot()}
            rollback = rollback_remote_submodule(payload)
            return 200, {
                "ok": bool(rollback.get("ok", False)),
                "schema_version": EVENT_SCHEMA_VERSION,
                "agent_management": build_agent_management_desktop_snapshot(),
                "remote_rollback": rollback,
                "review_gate": decision.snapshot(),
            }
        state_payload = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        state = save_agent_management_state(state_payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"agent management update failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "agent_management": build_agent_management_desktop_snapshot(state),
    }


def build_desktop_skills_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "skills": build_desktop_skills_snapshot(),
    }


def build_desktop_skills_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        action = str(payload.get("action") or "save").strip().lower()
        result = seed_studio_workflow_skills() if action == "seed_studio_workflow_skills" else handle_desktop_skills_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"skills update failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_skill_router_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "skill_router": build_skill_router_snapshot(),
    }


def build_desktop_skill_router_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "route").strip().lower()
    try:
        if action in {"snapshot", "refresh", "list", "list_registry"}:
            result = {"ok": True, "skill_router": build_skill_router_snapshot(limit=_int_payload(payload, "limit", 200))}
        elif action in {"build_context", "context", "context_pack"}:
            context = build_skill_context_pack(payload)
            result = {"ok": True, "skill_context": context.snapshot(), "skill_router": build_skill_router_snapshot()}
        elif action in {"route", "route_skill", "select_skill"}:
            decision = route_skill(payload)
            result = {
                "ok": decision.allowed,
                "skill_route": decision.snapshot(),
                "skill_router": build_skill_router_snapshot(),
            }
        elif action in {"orchestrate", "build_orchestration", "compile_workflow"}:
            decision = route_skill(payload)
            if not decision.allowed or decision.selected is None:
                result = {
                    "ok": False,
                    "error": "skill_route_failed",
                    "skill_route": decision.snapshot(),
                    "skill_router": build_skill_router_snapshot(),
                }
            else:
                result = {
                    "ok": True,
                    "skill_route": decision.snapshot(),
                    "skill_orchestration": build_skill_orchestration(decision.selected, decision.context).get("orchestration", {}),
                    "skill_router": build_skill_router_snapshot(),
                }
        else:
            return 400, {"ok": False, "error": f"unsupported skill router action: {action}"}
    except Exception as exc:
        return 400, {"ok": False, "error": f"skill router action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_safety_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "safety": build_safety_snapshot(),
    }


def build_desktop_safety_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_safety_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"safety action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_knowledge_base_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "knowledge_base": build_knowledge_base_snapshot(),
    }


def build_desktop_knowledge_base_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_knowledge_base_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"knowledge base action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_search_management_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "search_management": build_search_management_snapshot(),
    }


def build_desktop_search_management_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_search_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"search management action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_mcp_management_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "mcp_management": build_mcp_management_snapshot(),
    }


def build_desktop_mcp_management_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_mcp_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"MCP management action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_tool_authorization_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "tool_authorization": build_tool_authorization_snapshot(),
    }


def build_desktop_tool_authorization_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_tool_authorization_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"tool authorization action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_audit_report_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "audit_report": build_audit_report_snapshot(),
    }


def build_desktop_audit_report_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "generate").strip().lower()
    if action not in {"generate", "refresh"}:
        return 400, {"ok": False, "error": f"unsupported audit report action: {action}"}
    try:
        report = generate_audit_report()
    except Exception as exc:
        return 500, {"ok": False, "error": f"audit report generation failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {"ok": True, "schema_version": EVENT_SCHEMA_VERSION, "audit_report": report}


def build_desktop_mobile_management_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "mobile_management": build_mobile_management_snapshot(),
    }


def build_desktop_mobile_management_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_mobile_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"mobile management action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_control_monitor_response(workspace_id: str = "") -> tuple[int, dict[str, Any]]:
    """Expose the same workspace/worker monitor used by the iOS controller."""
    try:
        from backend.mobile.ios_monitoring import build_ios_monitor_snapshot
        from scripts.control_plane_store import ControlPlaneStore

        monitor = build_ios_monitor_snapshot(ControlPlaneStore(), workspace_id=workspace_id)
    except Exception as exc:
        return 500, {"ok": False, "error": f"control monitor failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {"ok": True, "schema_version": EVENT_SCHEMA_VERSION, "monitor": monitor}


def build_desktop_control_monitor_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "refresh").strip().lower()
    workspace_id = str(payload.get("workspace_id") or "")
    if action not in {"refresh", "snapshot", "auto_repair", "cleanup_stale_bindings"}:
        return 400, {"ok": False, "error": f"unsupported control monitor action: {action}"}
    try:
        from backend.mobile.ios_monitoring import handle_ios_monitor_action
        from scripts.control_plane_store import ControlPlaneStore

        result = handle_ios_monitor_action(ControlPlaneStore(), payload, workspace_id=workspace_id)
    except Exception as exc:
        return 400, {"ok": False, "error": f"control monitor action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {"ok": bool(result.get("ok", True)), "schema_version": EVENT_SCHEMA_VERSION, **result}


def build_desktop_resource_registry_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "resource_management": build_resource_management_snapshot(),
    }


def build_desktop_resource_registry_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_resource_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"resource registry action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_state_maintenance_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "state_maintenance": build_state_maintenance_snapshot(),
    }


def build_desktop_state_maintenance_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_state_maintenance_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"state maintenance action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_action_log_response(*, limit: int = 80, project_root: str | Path | None = None) -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "action_log": build_action_log_snapshot(limit=limit, project_root=project_root),
    }


def build_desktop_ecosystem_review_response() -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "ecosystem_review": build_ecosystem_review_snapshot(),
    }


def build_desktop_ecosystem_review_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_ecosystem_review_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"ecosystem review action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def build_desktop_module_management_response(*, force_refresh: bool = False) -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "module_management": build_module_management_snapshot(fast=True, use_cache=not force_refresh),
    }


def build_desktop_module_management_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "refresh").strip().lower()
    if action in {"refresh", "snapshot"}:
        clear_module_management_cache()
        return build_desktop_module_management_response(force_refresh=True)
    if action in {"scan", "approve", "reject", "apply_approved", "multi_model_review", "reset"}:
        clear_module_management_cache()
        status, response = build_desktop_ecosystem_review_update_response(payload)
        ecosystem = response.get("ecosystem_review") if isinstance(response.get("ecosystem_review"), dict) else None
        response["module_management"] = build_module_management_snapshot(ecosystem_snapshot=ecosystem, fast=True)
        return status, response
    return 400, {"ok": False, "error": f"unsupported module management action: {action}"}


_WORKFLOW_AUTO_ADVANCE_STARTED = False
_WORKFLOW_AUTO_ADVANCE_LOCK = threading.Lock()


def build_desktop_workflows_response(query: dict[str, list[str]] | None = None) -> tuple[int, dict[str, Any]]:
    action = str(((query or {}).get("action") or [""])[0] or "").strip().lower()
    if action in {"schema", "list_node_catalog"}:
        return build_desktop_workflows_update_response({"action": action})
    return 200, {
        "ok": True,
        "schema_version": EVENT_SCHEMA_VERSION,
        "workflows": build_workflow_management_snapshot(),
    }


def build_desktop_workflows_update_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = handle_workflow_management_action(payload)
    except Exception as exc:
        return 400, {"ok": False, "error": f"workflow action failed: {type(exc).__name__}", "detail": str(exc)}
    return 200, {
        "ok": bool(result.get("ok", True)),
        "schema_version": EVENT_SCHEMA_VERSION,
        **result,
    }


def _workflow_auto_advance_enabled() -> bool:
    # RuntimeWorkflowHostService is the only automatic execution owner.
    return False


def _workflow_auto_advance_interval() -> float:
    try:
        return max(1.0, float(os.getenv("SPIRITKIN_WORKFLOW_ADVANCE_INTERVAL", "5") or 5))
    except ValueError:
        return 5.0


def _start_workflow_auto_advance_daemon() -> None:
    global _WORKFLOW_AUTO_ADVANCE_STARTED
    if not _workflow_auto_advance_enabled():
        return
    with _WORKFLOW_AUTO_ADVANCE_LOCK:
        if _WORKFLOW_AUTO_ADVANCE_STARTED:
            return
        _WORKFLOW_AUTO_ADVANCE_STARTED = True

    def _run() -> None:
        interval = _workflow_auto_advance_interval()
        while True:
            try:
                handle_workflow_management_action(
                    {
                        "action": "auto_advance_runs",
                        "actor": "workflow_auto_advance_daemon",
                        "max_runs": 20,
                        "max_steps_per_run": 10,
                    }
                )
            except Exception as exc:
                print(f"[workflow-auto-advance] {type(exc).__name__}: {exc}", flush=True)
            time.sleep(interval)

    thread = threading.Thread(target=_run, name="workflow-auto-advance", daemon=True)
    thread.start()


def _int_payload(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int((query.get(key) or [default])[0])
    except (TypeError, ValueError, IndexError):
        return default


def _resolve_training_output_path(raw_path: str) -> Path:
    root = Path.cwd().resolve()
    target = Path(raw_path)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    allowed_roots = [root / "state", root / "data", root / "runs"]
    if not any(target == allowed or allowed in target.parents for allowed in allowed_roots):
        raise ValueError("output_path must stay under state/, data/, or runs/")
    return target


class CommandGatewayHandler(BaseHTTPRequestHandler):
    runtime: SpiritKinRuntime | None = None
    auth_token: str = ""
    policy_engine: object | None = None
    rate_limiter: object | None = None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        _, body = _json_bytes(payload, status=status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {AUTH_HEADER}", env_key="SPIRITKIN_COMMAND_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path in {
            "/desktop/state",
            "/desktop/diagnostics",
            "/desktop/operations",
            "/desktop/services",
            "/desktop/service-ports",
            "/desktop/logs",
            "/desktop/sync",
            "/desktop/daily",
            "/desktop/learning",
            "/desktop/code-jury",
            "/desktop/collaboration",
            "/desktop/evolution",
            "/desktop/growth",
            "/desktop/runtime-continuity",
            "/desktop/model-catalog",
            "/desktop/context",
            "/desktop/project-overview",
            "/desktop/project-runtime",
            "/desktop/resource-registry",
            "/desktop/agent-management",
            "/desktop/skills",
            "/desktop/safety",
            "/desktop/knowledge-base",
            "/desktop/search-management",
            "/desktop/mcp-management",
            "/desktop/tool-authorization",
            "/desktop/audit-report",
            "/desktop/mobile-management",
            "/desktop/control-monitor",
            "/desktop/action-log",
            "/desktop/state-maintenance",
            "/desktop/ecosystem-review",
            "/desktop/module-management",
            "/desktop/memory",
            "/desktop/workflows",
            "/scheduler/intents",
        }:
            if not token_is_authorized(self.headers, self.auth_token, client_ip=str(self.client_address[0])):
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            if path == "/scheduler/intents":
                include_finished = str((query.get("include_finished") or ["1"])[0]).strip().lower() not in {"0", "false", "no"}
                status, response = build_scheduler_intents_response(self.runtime, include_finished=include_finished)  # type: ignore[arg-type]
            elif path == "/desktop/diagnostics":
                status, response = build_desktop_diagnostics_response()
            elif path == "/desktop/operations":
                status, response = build_desktop_operations_response()
            elif path == "/desktop/services":
                status, response = build_desktop_services_response()
            elif path == "/desktop/service-ports":
                status, response = build_desktop_service_ports_response()
            elif path == "/desktop/logs":
                status, response = build_desktop_logs_response((query.get("log_id") or [""])[0])
            elif path == "/desktop/sync":
                status, response = build_desktop_sync_response()
            elif path == "/desktop/daily":
                status, response = build_desktop_daily_response()
            elif path == "/desktop/learning":
                status, response = build_desktop_learning_response()
            elif path == "/desktop/code-jury":
                status, response = build_desktop_code_jury_response()
            elif path == "/desktop/collaboration":
                status, response = build_desktop_collaboration_response()
            elif path == "/desktop/evolution":
                status, response = build_desktop_evolution_response()
            elif path == "/desktop/growth":
                status, response = build_desktop_growth_response()
            elif path == "/desktop/runtime-continuity":
                status, response = 200, {
                    "ok": True,
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "runtime_continuity": build_runtime_continuity_snapshot(
                        workspace_id=(query.get("workspace_id") or [""])[0]
                    ),
                }
            elif path == "/desktop/model-catalog":
                status, response = build_model_catalog_response()
            elif path == "/desktop/context":
                status, response = build_desktop_context_response()
            elif path == "/desktop/project-overview":
                status, response = build_desktop_project_overview_response()
            elif path == "/desktop/project-runtime":
                status, response = build_desktop_project_runtime_response()
            elif path == "/desktop/resource-registry":
                status, response = build_desktop_resource_registry_response()
            elif path == "/desktop/agent-management":
                status, response = build_desktop_agent_management_response()
            elif path == "/desktop/skills":
                status, response = build_desktop_skills_response()
            elif path == "/desktop/skill-router":
                status, response = build_desktop_skill_router_response()
            elif path == "/desktop/safety":
                status, response = build_desktop_safety_response()
            elif path == "/desktop/knowledge-base":
                status, response = build_desktop_knowledge_base_response()
            elif path == "/desktop/search-management":
                status, response = build_desktop_search_management_response()
            elif path == "/desktop/mcp-management":
                status, response = build_desktop_mcp_management_response()
            elif path == "/desktop/tool-authorization":
                status, response = build_desktop_tool_authorization_response()
            elif path == "/desktop/audit-report":
                status, response = build_desktop_audit_report_response()
            elif path == "/desktop/mobile-management":
                status, response = build_desktop_mobile_management_response()
            elif path == "/desktop/control-monitor":
                status, response = build_desktop_control_monitor_response((query.get("workspace_id") or [""])[0])
            elif path == "/desktop/action-log":
                status, response = build_desktop_action_log_response(limit=_query_int(query, "limit", 80))
            elif path == "/desktop/state-maintenance":
                status, response = build_desktop_state_maintenance_response()
            elif path == "/desktop/ecosystem-review":
                status, response = build_desktop_ecosystem_review_response()
            elif path == "/desktop/module-management":
                status, response = build_desktop_module_management_response()
            elif path == "/desktop/memory":
                status, response = build_desktop_memory_response(self.runtime)  # type: ignore[arg-type]
            elif path == "/desktop/workflows":
                status, response = build_desktop_workflows_response(query)
            else:
                status, response = build_desktop_state_response()
            self._send_json(status, response)
            return
        if path != "/health":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self._send_json(200, {"ok": True, "service": "spiritkin-command-gateway"})

    def do_POST(self) -> None:  # noqa: N802
        path = (urlparse(self.path).path.rstrip("/") or "/")
        if path not in {
            "/command",
            "/proactive/feedback",
            "/scheduler/intents",
            "/training/dataset",
            "/training/cloud-package",
            "/avatar/assets/import",
            "/attachments/ingest",
            "/mobile/artifacts",
            "/desktop/state",
            "/desktop/diagnostics",
            "/desktop/services",
            "/desktop/service-ports",
            "/desktop/sync",
            "/desktop/learning",
            "/desktop/code-jury",
            "/desktop/collaboration",
            "/desktop/evolution",
            "/desktop/growth",
            "/desktop/runtime-continuity",
            "/desktop/model-catalog",
            "/desktop/context",
            "/desktop/project-overview",
            "/desktop/project-runtime",
            "/desktop/resource-registry",
            "/desktop/agent-management",
            "/desktop/skills",
            "/desktop/skill-router",
            "/desktop/safety",
            "/desktop/knowledge-base",
            "/desktop/search-management",
            "/desktop/mcp-management",
            "/desktop/tool-authorization",
            "/desktop/audit-report",
            "/desktop/mobile-management",
            "/desktop/control-monitor",
            "/desktop/state-maintenance",
            "/desktop/ecosystem-review",
            "/desktop/module-management",
            "/desktop/memory",
            "/desktop/workflows",
        }:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if not token_is_authorized(self.headers, self.auth_token, client_ip=str(self.client_address[0])):
            security_context = build_gateway_security_context(self.headers, expected_token=self.auth_token, client_ip=self.client_address[0])
            if self.runtime is not None:
                self.runtime.record_audit_event(
                    "command_unauthorized",
                    actor="command_gateway",
                    channel="mobile",
                    success=False,
                    message="unauthorized command request",
                    metadata={"client_id": self.client_address[0], "security_context": security_context},
                )
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        gateway_safety = evaluate_gateway_request_safety(path=path, method="POST")
        if not gateway_safety.allowed:
            self._send_json(
                423,
                {
                    "ok": False,
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "error": gateway_safety.error_code,
                    "message": gateway_safety.message,
                    "safety": gateway_safety.snapshot(),
                },
            )
            return

        client_key = f"gateway:{self.client_address[0]}"
        if self.rate_limiter is not None and hasattr(self.rate_limiter, "check"):
            if not self.rate_limiter.check(client_key):
                if self.runtime is not None:
                    self.runtime.record_audit_event("rate_limit_violation", actor="command_gateway", channel="mobile", message="rate limit exceeded", metadata={"client_id": self.client_address[0]})
                self._send_json(429, {"ok": False, "error": "rate limit exceeded"})
                return
            self.rate_limiter.record(client_key)

        if self.policy_engine is not None and hasattr(self.policy_engine, "evaluate"):
            decision = self.policy_engine.evaluate(target="command_gateway", operation="command", risk_level="low", actor="mobile", channel="mobile")
            if not decision.allowed:
                self._send_json(403, {"ok": False, "error": "forbidden", "reason": decision.reason})
                return

        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 2 * 1024 * 1024:
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "json body must be object"})
            return
        if path == "/scheduler/intents":
            status, response = build_scheduler_intents_update_response(self.runtime, payload)  # type: ignore[arg-type]
        elif path == "/training/dataset":
            status, response = build_training_dataset_response(payload)
        elif path == "/proactive/feedback":
            status, response = build_proactive_feedback_response(self.runtime, payload)  # type: ignore[arg-type]
        elif path == "/training/cloud-package":
            status, response = build_training_cloud_package_response(payload)
        elif path == "/avatar/assets/import":
            status, response = build_avatar_asset_import_response(payload)
        elif path == "/attachments/ingest":
            status, response = build_attachments_ingest_response(payload)
        elif path == "/mobile/artifacts":
            status, response = build_mobile_artifacts_ingest_response(payload, client_id=self.client_address[0])
        elif path == "/desktop/state":
            status, response = build_desktop_state_update_response(payload, client_id=self.client_address[0])
        elif path == "/desktop/diagnostics":
            status, response = build_desktop_diagnostics_update_response(payload)
        elif path == "/desktop/services":
            status, response = build_desktop_services_update_response(payload)
        elif path == "/desktop/service-ports":
            status, response = build_desktop_service_ports_update_response(payload)
        elif path == "/desktop/sync":
            status, response = build_desktop_sync_update_response(payload)
        elif path == "/desktop/learning":
            status, response = build_desktop_learning_update_response(payload)
        elif path == "/desktop/code-jury":
            status, response = build_desktop_code_jury_update_response(payload)
        elif path == "/desktop/collaboration":
            status, response = build_desktop_collaboration_update_response(payload)
        elif path == "/desktop/evolution":
            status, response = build_desktop_evolution_update_response(payload)
        elif path == "/desktop/growth":
            status, response = build_desktop_growth_update_response(payload)
        elif path == "/desktop/runtime-continuity":
            try:
                status, response = 200, handle_runtime_continuity_action(payload)
            except PermissionError as exc:
                status, response = 403, {"ok": False, "error": "runtime_continuity_confirmation_required", "detail": str(exc)}
            except (KeyError, ValueError, RuntimeError) as exc:
                status, response = 400, {"ok": False, "error": "runtime_continuity_action_failed", "detail": str(exc)}
        elif path == "/desktop/model-catalog":
            status, response = build_model_catalog_update_response(payload)
        elif path == "/desktop/context":
            status, response = build_desktop_context_update_response(payload)
        elif path == "/desktop/project-overview":
            status, response = build_desktop_project_overview_update_response(payload)
        elif path == "/desktop/project-runtime":
            status, response = build_desktop_project_runtime_update_response(payload)
        elif path == "/desktop/resource-registry":
            status, response = build_desktop_resource_registry_update_response(payload)
        elif path == "/desktop/agent-management":
            status, response = build_desktop_agent_management_update_response(payload)
        elif path == "/desktop/skills":
            status, response = build_desktop_skills_update_response(payload)
        elif path == "/desktop/skill-router":
            status, response = build_desktop_skill_router_update_response(payload)
        elif path == "/desktop/safety":
            status, response = build_desktop_safety_update_response(payload)
        elif path == "/desktop/knowledge-base":
            status, response = build_desktop_knowledge_base_update_response(payload)
        elif path == "/desktop/search-management":
            status, response = build_desktop_search_management_update_response(payload)
        elif path == "/desktop/mcp-management":
            status, response = build_desktop_mcp_management_update_response(payload)
        elif path == "/desktop/tool-authorization":
            status, response = build_desktop_tool_authorization_update_response(payload)
        elif path == "/desktop/audit-report":
            status, response = build_desktop_audit_report_update_response(payload)
        elif path == "/desktop/mobile-management":
            status, response = build_desktop_mobile_management_update_response(payload)
        elif path == "/desktop/control-monitor":
            status, response = build_desktop_control_monitor_update_response(payload)
        elif path == "/desktop/state-maintenance":
            status, response = build_desktop_state_maintenance_update_response(payload)
        elif path == "/desktop/ecosystem-review":
            status, response = build_desktop_ecosystem_review_update_response(payload)
        elif path == "/desktop/module-management":
            status, response = build_desktop_module_management_update_response(payload)
        elif path == "/desktop/memory":
            status, response = build_desktop_memory_update_response(self.runtime, payload)  # type: ignore[arg-type]
        elif path == "/desktop/workflows":
            status, response = build_desktop_workflows_update_response(payload)
        else:
            status, response = build_command_response(self.runtime, payload, client_id=self.client_address[0])  # type: ignore[arg-type]
        self._send_json(status, response)

    def handle_one_request(self) -> None:
        started = time.monotonic()
        super().handle_one_request()
        elapsed = time.monotonic() - started
        if elapsed > 2.0:
            print(f"[command-gateway] SLOW {elapsed:.1f}s {getattr(self, 'command', '?')} {getattr(self, 'path', '?')}", flush=True)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[command-gateway] {self.address_string()} - {format % args}")


class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second gateway silently double-bind the
    # port, splitting requests between two processes. Bind exclusively so a
    # duplicate launch fails fast instead.
    allow_reuse_address = os.name != "nt"


def serve_command_gateway(host: str = DEFAULT_COMMAND_HOST, port: int = DEFAULT_COMMAND_PORT) -> None:
    CommandGatewayHandler.runtime = SpiritKinRuntime(emit_runtime_events=True)
    if str(os.getenv("SPIRITKIN_SCHEDULER_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}:
        CommandGatewayHandler.runtime.start_scheduler()
    CommandGatewayHandler.auth_token = os.getenv("SPIRITKIN_MOBILE_TOKEN", "").strip()
    _start_workflow_auto_advance_daemon()
    mcp_health_monitor = start_mcp_health_monitor()
    ilink_channel = _build_wechat_ilink_channel(CommandGatewayHandler.runtime)
    if ilink_channel is not None:
        ilink_channel.start()
    server = _ExclusiveThreadingHTTPServer((host, port), CommandGatewayHandler)
    print(f"[mobile] Command gateway started at http://{host}:{port}/command")
    if CommandGatewayHandler.auth_token:
        print(f"[mobile] Token required via {AUTH_HEADER} or Authorization: Bearer <token>")
    try:
        server.serve_forever()
    finally:
        if ilink_channel is not None:
            ilink_channel.stop()
        mcp_health_monitor.stop()
        if CommandGatewayHandler.runtime is not None:
            CommandGatewayHandler.runtime.close()
        server.server_close()


def _build_wechat_ilink_channel(runtime: SpiritKinRuntime) -> WeChatILinkChannel | None:
    """Start iLink only when explicitly enabled; desktop startup stays local by default."""
    channel = build_ilink_channel_from_env()
    if not channel.config.enabled:
        return None

    def handle_message(message: ILinkIncomingMessage):
        metadata = {
            "client_id": f"wechat:{message.from_user_id}",
            "client_type": "wechat_ilink",
            "session_id": f"wechat:{message.from_user_id}",
            "wechat_message_id": message.message_id,
            "wechat_bot_id": channel.config.bot_id,
        }
        return runtime.handle_input(
            InteractionInput(
                text=message.text,
                channel="wechat",
                metadata=metadata,
            )
        )

    channel.on_message = handle_message
    def publish_status(status: dict[str, Any]) -> None:
        from backend.mobile.ios_channels import persist_wechat_ilink_status

        persist_wechat_ilink_status(status)
        print(
            f"[wechat-ilink] {status.get('phase', 'unknown')}: {status.get('message', '')}",
            flush=True,
        )

    channel.status_sink = publish_status
    return channel


def main() -> None:
    serve_command_gateway()


if __name__ == "__main__":
    main()
