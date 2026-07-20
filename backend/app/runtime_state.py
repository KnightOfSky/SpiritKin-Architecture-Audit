from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.app import realtime_contract as contract

RUNTIME_STATE_SCHEMA_VERSION = "spiritkin.aggregated_runtime_state.v1"
RUNTIME_AGGREGATED_STATE_EVENT = contract.RUNTIME_AGGREGATED_STATE

RUNTIME_STATE_META: dict[str, dict[str, object]] = {
    "idle": {"label": "Idle", "priority": 10, "emotion": "neutral", "action": "", "progress": 0.0},
    "waiting": {"label": "Waiting", "priority": 40, "emotion": "waiting", "action": "", "progress": 20.0},
    "completed": {"label": "Completed", "priority": 60, "emotion": "happy", "action": "nod", "progress": 100.0},
    "executing": {"label": "Executing", "priority": 70, "emotion": "neutral", "action": "execute_task", "progress": 55.0},
    "planning": {"label": "Planning", "priority": 80, "emotion": "thinking", "action": "think", "progress": 25.0},
    "need_user": {"label": "Need User", "priority": 90, "emotion": "waiting", "action": "await_confirmation", "progress": 45.0},
    "error": {"label": "Error", "priority": 100, "emotion": "error", "action": "shake", "progress": 100.0},
}

IGNORED_EVENT_TYPES = {
    contract.AVATAR_ACTION,
    contract.AVATAR_MOTION,
    contract.MEMORY_UPDATED,
    contract.MODEL_INTERACTION,
    contract.OPENING_BUBBLE_PRESENT,
    contract.PERSONALITY_UPDATED,
    contract.PRESENCE_UPDATED,
    contract.PROACTIVE_FEEDBACK,
    contract.PROACTIVE_SUGGESTED,
    contract.PROACTIVE_SUPPRESSED,
    contract.RELATIONSHIP_UPDATED,
    contract.RUNTIME_AGGREGATED_STATE,
    contract.RUNTIME_CAPABILITIES,
    contract.RUNTIME_SNAPSHOT,
    contract.RUNTIME_SUBSCRIBE,
    contract.SCHEDULER_INTENT_DUE,
    contract.SCHEDULER_INTENT_SUPPRESSED,
    contract.VOICE_CALL_STATE,
    contract.VOICE_CALL_TRANSCRIPT,
    contract.ASR_SPEECH_STARTED,
    contract.ASR_PARTIAL,
    contract.ASR_FINAL,
    contract.SPEECH_ENDED,
    contract.SPEECH_INTERRUPTED,
    contract.SPEECH_PHONEME,
    contract.SPEECH_STARTED,
    contract.AVATAR_STATE,
    contract.DEVICE_OPENCLAW_STATE_UPDATED,
}

_TEXT_CLEANUP_PATTERN = re.compile(r"<(?:emotion|action):[^>]+>", re.IGNORECASE)


@dataclass(frozen=True)
class ForegroundTask:
    task_id: str
    source: str
    title: str
    status: str = "idle"
    priority: int = 10
    progress: float = 0.0
    message: str = ""
    actor: str = ""
    target: str = ""
    emotion: str = "neutral"
    action: str = ""
    speech_hint: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "progress": round(float(self.progress), 2),
            "message": self.message,
            "actor": self.actor,
            "target": self.target,
            "emotion": self.emotion,
            "action": self.action,
            "speech_hint": self.speech_hint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AggregatedRuntimeState:
    schema_version: str = RUNTIME_STATE_SCHEMA_VERSION
    state: str = "idle"
    dominant_activity: str = "Foreground Task: --"
    highest_priority_event: dict[str, object] = field(default_factory=dict)
    overall_progress: float = 0.0
    emotion: str = "neutral"
    action: str = ""
    speech_hint: str = "等待 Runtime 任务"
    summary: str = "等待 Runtime 任务"
    generated_at: float = 0.0
    task_count: int = 0
    tasks: tuple[ForegroundTask, ...] = ()

    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state,
            "dominant_activity": self.dominant_activity,
            "highest_priority_event": dict(self.highest_priority_event),
            "overall_progress": round(float(self.overall_progress), 2),
            "progress": round(float(self.overall_progress), 2),
            "emotion": self.emotion,
            "action": self.action,
            "speech_hint": self.speech_hint,
            "narration": self.speech_hint,
            "summary": self.summary,
            "generated_at": self.generated_at,
            "task_count": self.task_count,
            "tasks": [task.snapshot() for task in self.tasks],
        }


def normalize_runtime_state(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_")
    if not raw:
        return ""
    if raw in {"need_user", "waiting_confirmation", "confirmation_request", "await_confirmation", "confirm_required", "requires_confirmation"}:
        return "need_user"
    if raw in {"error", "failed", "failure", "blocked", "interrupted", "denied", "rejected"}:
        return "error"
    if raw in {"planning", "thinking", "plan", "analyzing", "analysis", "routing"}:
        return "planning"
    if raw in {"executing", "execution", "running", "acting", "working", "in_progress", "active", "started", "speaking"}:
        return "executing"
    if raw in {"waiting", "queued", "pending", "idle_wait", "attentive_wait", "scheduled"}:
        return "waiting"
    if raw in {"completed", "complete", "done", "success", "succeeded", "ok", "healthy"}:
        return "completed"
    if raw == "idle":
        return "idle"
    return ""


def build_aggregated_runtime_state_snapshot(
    events: Iterable[dict[str, object]],
    *,
    now: float | None = None,
    max_tasks: int = 4,
) -> dict[str, object]:
    return aggregate_runtime_state(events, now=now, max_tasks=max_tasks).snapshot()


def build_aggregated_runtime_state_event(
    events: Iterable[dict[str, object]],
    *,
    schema_version: str = "v1",
    now: float | None = None,
    max_tasks: int = 4,
) -> dict[str, object]:
    return {
        "type": RUNTIME_AGGREGATED_STATE_EVENT,
        "schema_version": schema_version,
        "payload": build_aggregated_runtime_state_snapshot(events, now=now, max_tasks=max_tasks),
    }


def aggregate_runtime_state(
    events: Iterable[dict[str, object]],
    *,
    now: float | None = None,
    max_tasks: int = 4,
) -> AggregatedRuntimeState:
    generated_at = float(time.time() if now is None else now)
    task_map: dict[str, ForegroundTask] = {}
    order: list[str] = []

    for event in events:
        task = foreground_task_from_event(event, now=generated_at)
        if task is None:
            continue
        previous = task_map.get(task.task_id)
        if previous is None:
            task_map[task.task_id] = task
            order.append(task.task_id)
        else:
            task_map[task.task_id] = _merge_task(previous, task)

    tasks = list(task_map.values())
    if not tasks:
        return AggregatedRuntimeState(generated_at=generated_at)

    tasks.sort(key=lambda task: (task.priority, task.updated_at, -order.index(task.task_id) if task.task_id in order else 0), reverse=True)
    visible_tasks = tuple(tasks[: max(1, int(max_tasks))])
    top = visible_tasks[0]
    active_tasks = [task for task in tasks if task.status not in {"completed", "error"}]
    progress_base = active_tasks or tasks
    progress = sum(_clamp(task.progress) for task in progress_base) / max(1, len(progress_base))
    state_counts: dict[str, int] = {}
    for task in tasks:
        state_counts[task.status] = state_counts.get(task.status, 0) + 1
    count_text = " / ".join(
        f"{RUNTIME_STATE_META.get(state, RUNTIME_STATE_META['idle'])['label']} {count}"
        for state, count in sorted(
            state_counts.items(),
            key=lambda item: int(RUNTIME_STATE_META.get(item[0], RUNTIME_STATE_META["idle"])["priority"]),
            reverse=True,
        )
    )
    dominant_activity = f"Foreground Task: {top.title}"
    if len(tasks) > 1 and count_text:
        dominant_activity = f"{dominant_activity} · {count_text}"
    speech_hint = top.speech_hint or build_avatar_narration(top)
    meta = RUNTIME_STATE_META.get(top.status, RUNTIME_STATE_META["idle"])
    return AggregatedRuntimeState(
        state=top.status,
        dominant_activity=dominant_activity,
        highest_priority_event=top.snapshot(),
        overall_progress=_clamp(progress),
        emotion=top.emotion or str(meta["emotion"]),
        action=top.action or str(meta["action"]),
        speech_hint=speech_hint,
        summary=speech_hint,
        generated_at=generated_at,
        task_count=len(tasks),
        tasks=visible_tasks,
    )


def foreground_task_from_event(event: dict[str, object], *, now: float | None = None) -> ForegroundTask | None:
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("type") or "")
    if not event_type or event_type in IGNORED_EVENT_TYPES:
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    if not isinstance(payload, dict):
        return None
    source_type = str(payload.get("source_event_type") or event_type)
    state = _state_from_payload(source_type, payload)
    if not state:
        return None
    timestamp = _event_timestamp(event, payload, now)
    nested_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    task_payload = _task_payload(payload)
    meta = RUNTIME_STATE_META.get(state, RUNTIME_STATE_META["idle"])
    task_id = _task_id(source_type, payload, task_payload)
    title = _task_title(source_type, payload, task_payload)
    progress = _progress_from_payload(payload, task_payload, state)
    message = _clean_text(_first_text(payload, nested_data, keys=("spoken_text", "message", "text", "summary", "detail")))
    emotion = str(payload.get("emotion") or nested_data.get("emotion") or meta["emotion"])
    action = str(payload.get("action") or payload.get("motion") or nested_data.get("action") or meta["action"])
    speech_hint = _clean_text(str(payload.get("speech_hint") or ""))
    target = str(payload.get("target") or payload.get("pending_target") or task_payload.get("target") or "")
    actor = str(payload.get("agent_name") or payload.get("actor") or payload.get("source") or "")
    return ForegroundTask(
        task_id=task_id,
        source=source_type,
        title=title,
        status=state,
        priority=int(meta["priority"]),
        progress=progress,
        message=message,
        actor=actor,
        target=target,
        emotion=emotion,
        action=action,
        speech_hint=speech_hint,
        created_at=timestamp,
        updated_at=timestamp,
        expires_at=_expires_at(timestamp, state, source_type),
        metadata={
            "event_type": event_type,
            "source_event_type": source_type,
            "response_kind": payload.get("response_kind") or nested_data.get("response_kind") or "",
            "session_id": payload.get("session_id") or nested_data.get("session_id") or "",
            "request_id": payload.get("request_id") or nested_data.get("request_id") or "",
        },
    )


def build_avatar_narration(task_or_state: ForegroundTask | AggregatedRuntimeState | str, title: str = "", progress: float = 0.0) -> str:
    if isinstance(task_or_state, ForegroundTask):
        state = task_or_state.status
        title = task_or_state.title
        progress = task_or_state.progress
    elif isinstance(task_or_state, AggregatedRuntimeState):
        state = task_or_state.state
        title = task_or_state.highest_priority_event.get("title") or task_or_state.dominant_activity
        progress = task_or_state.overall_progress
    else:
        state = normalize_runtime_state(task_or_state) or "idle"
    title = _clean_text(str(title or "Foreground Task")) or "Foreground Task"
    if state == "need_user":
        return f"{title} 需要你确认"
    if state == "error":
        return f"{title} 遇到错误，需要处理"
    if state == "planning":
        return f"正在规划：{title}"
    if state == "executing":
        percent = int(round(_clamp(progress)))
        return f"正在执行：{title}（{percent}%）" if percent else f"正在执行：{title}"
    if state == "waiting":
        return f"{title} 正在等待资源或排队"
    if state == "completed":
        return f"{title} 已完成"
    return "等待 Runtime 任务"


def _merge_task(previous: ForegroundTask, task: ForegroundTask) -> ForegroundTask:
    previous_rank = _event_rank(previous.source)
    next_rank = _event_rank(task.source)
    keep_previous_state = (
        previous.status in {"need_user", "error", "completed"}
        and next_rank < previous_rank
        and task.status not in {"need_user", "error"}
    )
    status = previous.status if keep_previous_state else task.status
    meta = RUNTIME_STATE_META.get(status, RUNTIME_STATE_META["idle"])
    progress = max(previous.progress, task.progress) if status == previous.status else task.progress
    title = task.title if task.title != "Foreground Task" or previous.title == "Foreground Task" else previous.title
    message = task.message or previous.message
    speech_hint = task.speech_hint or previous.speech_hint
    return ForegroundTask(
        task_id=previous.task_id,
        source=task.source if next_rank >= previous_rank else previous.source,
        title=title,
        status=status,
        priority=int(meta["priority"]),
        progress=_clamp(progress),
        message=message,
        actor=task.actor or previous.actor,
        target=task.target or previous.target,
        emotion=task.emotion or previous.emotion or str(meta["emotion"]),
        action=task.action or previous.action or str(meta["action"]),
        speech_hint=speech_hint,
        created_at=previous.created_at,
        updated_at=max(previous.updated_at, task.updated_at),
        expires_at=max(previous.expires_at, task.expires_at),
        metadata={**previous.metadata, **task.metadata},
    )


def _event_rank(event_type: str) -> int:
    return {
        "assistant.confirmation_requested": 100,
        "assistant.execution_updated": 100,
        "assistant.task_updated": 90,
        "assistant.project_updated": 80,
        "assistant.message": 70,
        "device.openclaw_state_updated": 70,
        "model.interaction": 60,
        "performance.state": 50,
        "user_input": 50,
        "avatar.state": 30,
    }.get(event_type, 40)


def _state_from_payload(event_type: str, payload: dict[str, object]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else data.get("execution") if isinstance(data.get("execution"), dict) else {}
    response_kind = str(payload.get("response_kind") or data.get("response_kind") or "")
    if payload.get("requires_confirmation") or response_kind == "confirmation_request" or event_type == "assistant.confirmation_requested":
        return "need_user"
    if event_type == "assistant.execution_updated" or response_kind == "execution_result":
        if payload.get("success") is False or execution.get("success") is False:
            return "error"
        if normalize_runtime_state(payload.get("status")) == "error" or normalize_runtime_state(execution.get("status")) == "error":
            return "error"
        return "completed"
    state = normalize_runtime_state(
        payload.get("status")
        or payload.get("state")
        or payload.get("phase")
        or payload.get("performance_phase")
        or payload.get("current_stage_status")
        or data.get("status")
        or data.get("phase")
    )
    if state:
        return state
    action = str(payload.get("action") or payload.get("motion") or "").lower()
    if action in {"write_plan", "plan_development", "think", "thinking", "tap_chin"}:
        return "planning"
    if action in {"queue_task"}:
        return "waiting"
    if action in {"execute_task", "open_app", "launch_app", "run_app"}:
        return "executing"
    if event_type == "user_input":
        return "planning"
    if event_type == "assistant.message":
        return "completed"
    if event_type.startswith("workflow.") or event_type.startswith("node_"):
        return "executing"
    if event_type == "device.openclaw_state_updated":
        return "completed"
    return ""


def _task_payload(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    task = data.get("task") if isinstance(data.get("task"), dict) else payload.get("task")
    if isinstance(task, dict):
        return task
    project = data.get("project") if isinstance(data.get("project"), dict) else payload.get("project")
    if isinstance(project, dict):
        return project
    return payload


def _task_id(event_type: str, payload: dict[str, object], task_payload: dict[str, object]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    candidates = [
        task_payload.get("task_id"),
        task_payload.get("id"),
        task_payload.get("project_id"),
        payload.get("task_id"),
        payload.get("id"),
        payload.get("project_id"),
        payload.get("request_id"),
        data.get("request_id"),
        payload.get("session_id"),
        data.get("session_id"),
        _pending_key(payload, data),
        _execution_key(payload, data),
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return event_type.replace(".", "_") or "foreground"


def _pending_key(payload: dict[str, object], data: dict[str, object]) -> str:
    target = payload.get("pending_target") or data.get("pending_target")
    operation = payload.get("pending_operation") or data.get("pending_operation")
    if target or operation:
        return f"pending:{target or 'target'}:{operation or 'operation'}"
    return ""


def _execution_key(payload: dict[str, object], data: dict[str, object]) -> str:
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else data.get("execution") if isinstance(data.get("execution"), dict) else {}
    target = payload.get("target") or execution.get("target")
    operation = payload.get("operation") or execution.get("operation")
    if target or operation:
        return f"execution:{target or 'target'}:{operation or 'operation'}"
    return ""


def _task_title(event_type: str, payload: dict[str, object], task_payload: dict[str, object]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    operation = payload.get("operation") or payload.get("pending_operation") or task_payload.get("operation")
    target = payload.get("target") or payload.get("pending_target") or task_payload.get("target")
    if operation and target:
        op_target = f"{operation} {target}"
    else:
        op_target = operation or target
    candidates = [
        task_payload.get("title"),
        task_payload.get("request"),
        task_payload.get("name"),
        task_payload.get("project_type"),
        payload.get("title"),
        payload.get("request"),
        op_target,
        payload.get("text"),
        payload.get("spoken_text"),
        payload.get("message"),
        data.get("text"),
    ]
    for candidate in candidates:
        value = _clean_text(str(candidate or ""))
        if value:
            return value[:52] + "..." if len(value) > 54 else value
    if event_type == "assistant.project_updated":
        return "Runtime Project"
    if event_type == "assistant.task_updated":
        return "Runtime Task"
    if event_type == "assistant.execution_updated":
        return "Runtime Execution"
    if event_type == "user_input":
        return "User Request"
    return "Foreground Task"


def _progress_from_payload(payload: dict[str, object], task_payload: dict[str, object], state: str) -> float:
    for key in ("progress_percent", "progress", "percent"):
        if task_payload.get(key) is not None:
            return _normal_progress(task_payload.get(key))
        if payload.get(key) is not None:
            return _normal_progress(payload.get(key))
    stages = task_payload.get("stages")
    if isinstance(stages, list) and stages:
        done = 0.0
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_state = normalize_runtime_state(stage.get("status") or stage.get("state"))
            if stage_state == "completed":
                done += 1.0
            elif stage_state in {"executing", "planning"}:
                done += 0.55
            elif stage_state in {"need_user", "waiting"}:
                done += 0.35
        return _clamp(done / max(1, len(stages)) * 100.0)
    return float(RUNTIME_STATE_META.get(state, RUNTIME_STATE_META["idle"])["progress"])


def _normal_progress(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number <= 1.0:
        number *= 100.0
    return _clamp(number)


def _event_timestamp(event: dict[str, object], payload: dict[str, object], now: float | None) -> float:
    for value in (event.get("timestamp"), payload.get("timestamp"), payload.get("updated_at")):
        try:
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return float(time.time() if now is None else now)


def _expires_at(timestamp: float, state: str, event_type: str) -> float:
    if event_type in {"assistant.message", "avatar.state", "model.interaction"} and state in {"planning", "executing", "waiting"}:
        return timestamp + 12.0
    if state in {"completed", "error"}:
        return timestamp + 8 * 60.0
    return timestamp + 45 * 60.0


def _first_text(*payloads: dict[str, object], keys: tuple[str, ...]) -> str:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def _clean_text(value: str) -> str:
    text = _TEXT_CLEANUP_PATTERN.sub("", value)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))
