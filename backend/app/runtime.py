from __future__ import annotations

import asyncio
import difflib
import functools
import inspect
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

from backend.agents.base import AgentReply
from backend.app.agent_cluster_ports import DefaultAgentClusterAppPort
from backend.app.agent_management import build_active_route_runtime_snapshot, build_managed_agent_runtime_snapshot
from backend.app.composer_model_selection import canonicalize_composer_metadata, resolve_configured_assist_model
from backend.app.local_model_policy import build_local_model_policy_snapshot
from backend.app.model_catalog import load_model_catalog
from backend.app.replaceable_brain import build_brain_replacement_snapshot
from backend.app.runtime_state import build_aggregated_runtime_state_event
from backend.app.settings import (
    DEFAULT_LONG_TERM_MEMORY_PATH,
    DEFAULT_PERSONALITY_PATH,
    DEFAULT_RELATIONSHIP_PATH,
    describe_model_capabilities,
    describe_recommended_model_stack,
    resolve_audit_log_path,
    resolve_hotword,
    resolve_knowledge_backend,
    resolve_remote_worker_nodes,
    resolve_skill_store_path,
    resolve_workflow_memory_path,
)
from backend.executors import HttpRemoteNodeClient, NodeRegistry, RemoteNode
from backend.expression.model_interaction import build_response_interaction
from backend.expression.phoneme_bridge import text_to_phoneme_events
from backend.expression.semantic_reaction import enrich_reply_avatar_reaction
from backend.memory import (
    MemoryOrchestrator,
    build_long_term_memory,
    build_personality_store,
    build_relationship_store,
    build_workflow_memory,
)
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.agent_cluster_wiring import AgentClusterWiring
from backend.orchestrator.presence import PresenceManager
from backend.proactive import (
    OpeningBubbleService,
    ProactivePolicy,
    ProactiveService,
    ProactiveSignal,
    signal_from_presence,
)
from backend.remote import RemoteHeartbeatPoller
from backend.runtime.events.persistence import build_event_persistence
from backend.runtime.scheduler import ScheduledIntent, SchedulerService
from backend.security import (
    InMemoryAuditLog,
    PolicyEngine,
    build_audit_log,
    build_default_policy,
)
from backend.security.safety_control import evaluate_execution_safety
from backend.skills.persistence import build_skill_store

EVENT_SCHEMA_VERSION = "v1"
DEFAULT_PENDING_EXECUTION_PATH = "state/run/pending_execution.json"
AVATAR_TAG_PATTERN = re.compile(r"<(?:emotion|action):[^>]+>", re.IGNORECASE)


def resolve_event_sink_url() -> str:
    explicit_url = os.getenv("SPIRITKIN_EVENTS_WS_URL")
    if explicit_url:
        return explicit_url

    legacy_live2d_url = os.getenv("SPIRITKIN_LIVE2D_WS_URL")
    if legacy_live2d_url:
        return legacy_live2d_url

    host = os.getenv("SPIRITKIN_EVENTS_HOST", "127.0.0.1")
    port = os.getenv("SPIRITKIN_EVENTS_PORT", "8765")
    return f"ws://{host}:{port}"


DEFAULT_EVENT_SINK_URL = resolve_event_sink_url()


def _safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(message).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def build_remote_node_registry_from_settings(*, config_path: str = "config/config.yaml") -> NodeRegistry | None:
    node_settings = resolve_remote_worker_nodes(config_path=config_path)
    if not node_settings:
        return None
    nodes: list[RemoteNode] = []
    for setting in node_settings:
        client = HttpRemoteNodeClient(
            setting.base_url,
            auth_token=setting.auth_token,
            timeout_seconds=setting.timeout_seconds,
        )
        nodes.append(
            RemoteNode(
                node_id=setting.node_id,
                client=client,
                aliases=set(setting.aliases),
                metadata={**dict(setting.metadata or {}), "base_url": setting.base_url},
            )
        )
    return NodeRegistry(nodes)


class ManagedRouteLlmClient:
    """Resolve the active desktop route profile when the runtime asks for text generation."""

    def __init__(self, base_client, *, config_path: str = "config/config.yaml"):
        self._base_client = base_client
        self._config_path = config_path

    def __call__(self, prompt: str, *args, **kwargs) -> str:
        requested_model_id = str(kwargs.pop("model_id", "") or "").strip()
        reasoning_effort = str(kwargs.pop("reasoning_effort", "auto") or "auto").strip().lower()
        agent_profile = self._resolve_agent_profile(kwargs.pop("agent_name", None))
        route = build_active_route_runtime_snapshot()
        selected_model = resolve_configured_assist_model(requested_model_id)
        if selected_model is not None:
            source = {
                "provider": selected_model.provider,
                "model": selected_model.model,
                "base_url": selected_model.endpoint,
                "api_key": selected_model.api_key,
            }
        elif agent_profile:
            source = agent_profile
        else:
            source = dict(route.get("primary_text") or {}) if route.get("enabled") else {}
        sources = [source, *self._route_fallback_sources(route, source)]
        for index, candidate in enumerate(sources):
            provider = str(candidate.get("provider") or "").strip()
            model = str(candidate.get("model") or "").strip()
            routed_kwargs = dict(kwargs)
            if provider:
                routed_kwargs["provider"] = provider
            if model:
                routed_kwargs["model_name"] = model
            if candidate.get("base_url"):
                routed_kwargs["base_url"] = str(candidate.get("base_url") or "")
            if candidate.get("api_key"):
                routed_kwargs["api_key"] = str(candidate.get("api_key") or "")
            routed_kwargs.setdefault("reasoning_effort", reasoning_effort)
            routed_kwargs.setdefault("config_path", self._config_path)
            try:
                return self._base_client(prompt, *args, **routed_kwargs)
            except TypeError:
                # A legacy client may not accept routing kwargs; preserve its old fast path.
                return self._base_client(prompt, *args, **kwargs)
            except Exception as exc:
                if index >= len(sources) - 1 or not self._should_try_route_fallback(exc):
                    raise
        raise RuntimeError("model route exhausted")

    def snapshot(self) -> dict[str, object]:
        return build_active_route_runtime_snapshot()

    @staticmethod
    def _route_fallback_sources(route: dict[str, object], primary: dict[str, object]) -> list[dict[str, object]]:
        profile = route.get("profile") if isinstance(route, dict) else None
        members = profile.get("members") if isinstance(profile, dict) else None
        if not isinstance(members, list):
            return []
        primary_key = (str(primary.get("provider") or "").strip().lower(), str(primary.get("model") or "").strip().lower())
        candidates = [item for item in members if isinstance(item, dict) and item.get("enabled") is not False]
        candidates.sort(key=lambda item: float(item.get("weight") or 0), reverse=True)
        result: list[dict[str, object]] = []
        seen = {primary_key}
        for item in candidates:
            key = (str(item.get("provider") or "").strip().lower(), str(item.get("model") or "").strip().lower())
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
            if len(result) >= 2:
                break
        return result

    @staticmethod
    def _should_try_route_fallback(error: Exception) -> bool:
        text = str(error).lower()
        return any(
            token in text
            for token in (
                "timeout",
                "timed out",
                "unavailable",
                "connection",
                "connection refused",
                "connectex",
                "not configured",
                "not found",
                "unknown model",
                "no such model",
                "does not exist",
                "provider",
                "model",
                " 404",
                " 429",
                " 5",
            )
        )

    @staticmethod
    def _resolve_agent_profile(agent_name: object) -> dict[str, object]:
        requested = str(agent_name or "").strip()
        if not requested:
            return {}
        runtime = build_managed_agent_runtime_snapshot()
        profiles = runtime.get("agent_profiles_by_id")
        if not isinstance(profiles, dict):
            return {}
        profile = profiles.get(requested)
        return dict(profile or {}) if isinstance(profile, dict) else {}


async def _send_event_to_websocket(url: str, event: dict[str, object]) -> bool:
    try:
        import websockets
    except Exception:
        return False

    try:
        async with websockets.connect(url) as websocket:
            token = str(os.getenv("SPIRITKIN_DESKTOP_TOKEN") or os.getenv("SPIRITKIN_API_TOKEN") or os.getenv("SPIRITKIN_MOBILE_TOKEN") or "").strip()
            await websocket.send(json.dumps({"type": "runtime.auth", "token": token}, ensure_ascii=False, default=str))
            await websocket.send(json.dumps(event, ensure_ascii=False, default=str))
        return True
    except Exception:
        return False


class _EventSinkSender:
    """常驻事件发送线程：复用一条到事件桥的 WebSocket 连接，按入队顺序推事件。

    旧实现每个事件都 asyncio.run + websockets.connect + 关闭握手，单次实测 3-8s
    （2026-07-08：网关处理 worker 流式事件的 HTTP 请求被拖到 5-8s/次，思考链与
    回复在桌面滞后真实生成几分钟）。这里改为进程级单例：调用方只做入队（微秒级），
    发送线程持有长连接串行发送，断线自动重连一次，仍失败则丢弃该事件
    （与旧行为一致：事件通道尽力而为，权威数据始终走落库路径）。
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue(maxsize=4096)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, url: str, event: dict[str, object]) -> bool:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="event-sink-sender", daemon=True)
                self._thread.start()
        try:
            self._queue.put_nowait((url, event))
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        try:
            asyncio.run(self._pump())
        except Exception:
            pass  # 线程退出后下次 enqueue 会重新拉起

    async def _pump(self) -> None:
        try:
            import websockets
        except Exception:
            return
        loop = asyncio.get_running_loop()
        connection = None
        connected_url = ""
        while True:
            # queue.get 必须带超时：默认 executor 线程非 daemon，无超时阻塞会让
            # concurrent.futures 的 atexit join 永远等不到线程归还，进程无法退出
            #（pytest/网关关停都会挂死）。1s 轮询间隙也让 drain 任务得到调度。
            try:
                url, event = await loop.run_in_executor(None, functools.partial(self._queue.get, True, 1.0))
            except queue.Empty:
                continue
            payload = json.dumps(event, ensure_ascii=False, default=str)
            for attempt in range(2):
                try:
                    if connection is None or connected_url != url:
                        if connection is not None:
                            try:
                                await connection.close()
                            except Exception:
                                pass
                        connection = await websockets.connect(url, open_timeout=3, close_timeout=1)
                        connected_url = url
                        token = str(os.getenv("SPIRITKIN_DESKTOP_TOKEN") or os.getenv("SPIRITKIN_API_TOKEN") or os.getenv("SPIRITKIN_MOBILE_TOKEN") or "").strip()
                        await connection.send(json.dumps({"type": "runtime.auth", "token": token}, ensure_ascii=False, default=str))
                        # 桥会把广播事件也发给本连接（快照+全量回声）。必须持续消费丢弃，
                        # 否则接收队列(max_queue)堆满后 TCP 窗口收满，桥的串行广播循环
                        # 会被本连接卡死，所有桌面客户端表现为"实时断连"。
                        asyncio.ensure_future(self._drain(connection))
                    await connection.send(payload)
                    break
                except Exception:
                    if connection is not None:
                        try:
                            await connection.close()
                        except Exception:
                            pass
                    connection = None
                    if attempt == 1:
                        break  # 重连一次仍失败：丢弃该事件，继续处理队列

    @staticmethod
    async def _drain(connection) -> None:
        try:
            async for _ in connection:
                pass
        except Exception:
            pass  # 连接被替换/关闭：drain 随之结束


_EVENT_SINK_SENDER = _EventSinkSender()


def dispatch_runtime_event(url: str | None, event: dict[str, object]) -> bool:
    if not url:
        return False
    return _EVENT_SINK_SENDER.enqueue(url, event)


@dataclass(frozen=True)
class AttachmentRef:
    file_id: str
    name: str
    mime_type: str = "application/octet-stream"
    uri: str | None = None
    size_bytes: int | None = None
    purpose: str = "user_upload"


@dataclass(frozen=True)
class InteractionInput:
    text: str
    channel: str = "text"
    visual_context: str = ""
    attachments: tuple[AttachmentRef, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


class SpiritKinRuntime:
    """主应用运行时：负责装配感知、编排与输出链路。"""

    def __init__(
        self,
        agent=None,
        hotword: str | None = None,
        knowledge_backend: str | None = None,
        node_registry=None,
        remote_heartbeat_interval_seconds: float = 10.0,
        remote_heartbeat_ttl_seconds: float = 30.0,
        openclaw_state_path: str | None = None,
        audit_log: InMemoryAuditLog | None = None,
        config_path: str = "config/config.yaml",
        event_sink_url: str | None = None,
        emit_runtime_events: bool = False,
    ):
        self.config_path = config_path
        resolved_knowledge_backend = resolve_knowledge_backend(knowledge_backend, config_path=config_path)
        if node_registry is None:
            node_registry = build_remote_node_registry_from_settings(config_path=config_path)
        if agent is None:
            from backend.services.conversation_engine import get_llm_response

            workflow_memory = build_workflow_memory(resolve_workflow_memory_path(config_path=config_path))
            skill_store = build_skill_store(resolve_skill_store_path(config_path=config_path))
            ltm = build_long_term_memory(DEFAULT_LONG_TERM_MEMORY_PATH)
            personality = build_personality_store(DEFAULT_PERSONALITY_PATH)
            relationship = build_relationship_store(DEFAULT_RELATIONSHIP_PATH)
            self.memory_orchestrator = MemoryOrchestrator(
                long_term=ltm,
                personality_store=personality,
                relationship_store=relationship,
                workflow=workflow_memory,
            )
            policy_engine = PolicyEngine(build_default_policy())
            managed_route_llm = ManagedRouteLlmClient(get_llm_response, config_path=config_path)
            wiring_kwargs = {
                "knowledge_backend": resolved_knowledge_backend,
                "workflow_memory": workflow_memory,
                "skill_store": skill_store,
                "policy_engine": policy_engine,
                "long_term_memory": ltm,
                "personality_store": personality,
                "relationship_store": relationship,
                "managed_agents": build_managed_agent_runtime_snapshot(),
                "app_port": DefaultAgentClusterAppPort(),
                "pending_execution_path": os.getenv("SPIRITKIN_PENDING_EXECUTION_PATH", DEFAULT_PENDING_EXECUTION_PATH),
            }
            if node_registry is not None:
                wiring_kwargs["node_registry"] = node_registry
            if openclaw_state_path is not None:
                wiring_kwargs["openclaw_state_path"] = openclaw_state_path
            self.agent = AgentCluster(
                llm_client=managed_route_llm,
                wiring=AgentClusterWiring(**wiring_kwargs),
            )
        else:
            self.agent = agent
            self.memory_orchestrator = getattr(agent, "memory_orchestrator", None)
        self.hotword = resolve_hotword(hotword, config_path=config_path)
        self.knowledge_backend = resolved_knowledge_backend
        self.node_registry = node_registry
        self.presence = PresenceManager()
        self.event_persistence = build_event_persistence("state/events.jsonl")
        self.proactive = ProactiveService(ProactivePolicy.from_env())
        self.opening_bubbles = OpeningBubbleService()
        recent_events = self.event_persistence.recent(limit=5000)
        self.proactive.restore(recent_events)
        self.opening_bubbles.restore(recent_events)
        self.scheduler: SchedulerService | None = None
        self._runtime_event_lock = threading.RLock()
        self.remote_heartbeat_interval_seconds = max(1.0, float(remote_heartbeat_interval_seconds))
        self.remote_heartbeat_ttl_seconds = max(1.0, float(remote_heartbeat_ttl_seconds))
        self.openclaw_state_path = openclaw_state_path
        self.audit_log = audit_log or build_audit_log(resolve_audit_log_path(config_path=config_path))
        self.event_sink_url = event_sink_url or resolve_event_sink_url()
        self.emit_runtime_events = emit_runtime_events
        self._recent_spoken_outputs: list[dict[str, object]] = []
        self._last_spoken_output_ended_at = 0.0
        self._remote_heartbeat_poller = None
        self._start_remote_heartbeat_poller_if_needed()
        if self.emit_runtime_events and self.proactive.policy.enabled:
            self.start_proactive_interaction()

    def _start_remote_heartbeat_poller_if_needed(self) -> None:
        registry = self.node_registry
        list_nodes = getattr(registry, "list_nodes", None)
        if registry is None or not callable(list_nodes):
            return
        nodes = list(list_nodes() or [])
        if not nodes:
            return
        self._remote_heartbeat_poller = RemoteHeartbeatPoller(
            registry,
            interval_seconds=self.remote_heartbeat_interval_seconds,
            ttl_seconds=self.remote_heartbeat_ttl_seconds,
        )
        self._remote_heartbeat_poller.start()

    def close(self) -> None:
        self.stop_proactive_interaction()
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        poller = self._remote_heartbeat_poller
        self._remote_heartbeat_poller = None
        if poller is not None:
            poller.stop()

    def _emit_runtime_event(self, event: dict[str, object]) -> bool:
        if not self.emit_runtime_events:
            return False
        return dispatch_runtime_event(self.event_sink_url, event)

    def _emit_runtime_events(self, events: list[dict[str, object]]) -> None:
        for event in events:
            self._emit_runtime_event(event)

    def start_proactive_interaction(self) -> None:
        if self.presence is None or not self.proactive.policy.enabled:
            return
        startup_event = self.opening_bubbles.startup_event(
            agent=self.agent,
            relationship=self._relationship_snapshot(),
        )
        if startup_event is not None:
            self._persist_and_emit_runtime_event(startup_event)
        self.presence.set_checkin_callback(self._handle_presence_checkin)
        self.presence.start_checkin_timer()

    def stop_proactive_interaction(self) -> None:
        if self.presence is not None:
            self.presence.stop()

    def start_scheduler(self) -> SchedulerService:
        if self.scheduler is None:
            self.scheduler = SchedulerService(
                os.getenv("SPIRITKIN_SCHEDULER_PATH", "state/scheduler/jobs.sqlite3"),
                event_sink=self._handle_scheduler_event,
                safety_gate=lambda intent: evaluate_execution_safety(
                    target="scheduler",
                    operation="trigger_intent",
                    actor="scheduler",
                ),
                misfire_grace_time=int(os.getenv("SPIRITKIN_SCHEDULER_MISFIRE_GRACE_SECONDS", "60")),
                max_instances=int(os.getenv("SPIRITKIN_SCHEDULER_MAX_INSTANCES", "1")),
            )
        self.scheduler.start()
        return self.scheduler

    def _handle_scheduler_event(self, event: dict[str, object]) -> None:
        self._persist_and_emit_runtime_event(event)
        if event.get("type") != "scheduler.intent_due":
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        intent_type = str(payload.get("intent_type") or "reminder")
        priority = max(0.0, min(1.0, float(payload.get("priority") or 70) / 100.0))
        if intent_type == "action":
            priority = max(priority, 0.9)
        timestamp = float(payload.get("timestamp") or time.time())
        signal = ProactiveSignal(
            signal_id=f"signal-scheduler-{payload.get('delivery_id') or payload.get('intent_id')}",
            kind="calendar_due",
            summary=str(payload.get("text") or "定时提醒"),
            source="scheduler",
            value_score=priority,
            created_at=timestamp,
            expires_at=timestamp + 10 * 60,
            metadata={
                "suggestion_text": str(payload.get("text") or "定时提醒"),
                "action_prompt": str(payload.get("action_prompt") or f"请处理定时事项：{payload.get('text') or ''}"),
                "intent_id": str(payload.get("intent_id") or ""),
                "delivery_id": str(payload.get("delivery_id") or ""),
            },
        )
        self.handle_proactive_signal(signal, now=timestamp)

    def scheduler_snapshot(self, *, include_finished: bool = True) -> dict[str, object]:
        scheduler = self.start_scheduler()
        intents = scheduler.list(include_finished=include_finished)
        return {
            "schema_version": "spiritkin.scheduler.v1",
            "count": len(intents),
            "intents": intents,
            "job_defaults": {
                "coalesce": scheduler.coalesce,
                "misfire_grace_time": scheduler.misfire_grace_time,
                "max_instances": scheduler.max_instances,
            },
        }

    def update_scheduled_intent(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        scheduler = self.start_scheduler()
        normalized = str(action or "create").strip().lower()
        if normalized == "create":
            intent_payload = payload.get("intent") if isinstance(payload.get("intent"), dict) else payload
            return scheduler.add(ScheduledIntent.from_snapshot(dict(intent_payload)))
        intent_id = str(payload.get("intent_id") or "").strip()
        if not intent_id:
            raise ValueError("missing intent_id")
        if normalized == "pause":
            return scheduler.pause(intent_id)
        if normalized == "resume":
            return scheduler.resume(intent_id)
        if normalized == "cancel":
            return scheduler.cancel(intent_id)
        if normalized in {"run", "run_now", "test"}:
            return scheduler.run_now(intent_id)
        if normalized in {"update", "modify"}:
            updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else {}
            return scheduler.update(intent_id, dict(updates))
        raise ValueError(f"unsupported scheduler action: {normalized}")

    def memory_management_snapshot(self) -> dict[str, object]:
        memory = getattr(self, "memory_orchestrator", None)
        if memory is None or not hasattr(memory, "memory_management_snapshot"):
            return {
                "schema_version": "spiritkin.memory_management.v1",
                "available": False,
                "stats": {},
                "recent_memories": [],
                "conflicts": [],
                "audit": {},
            }
        return dict(memory.memory_management_snapshot())

    def resolve_memory_conflict(self, conflict_id: str, resolution: str, *, reason: str = "") -> dict[str, object]:
        memory = getattr(self, "memory_orchestrator", None)
        if memory is None or not hasattr(memory, "resolve_memory_conflict"):
            raise RuntimeError("long-term memory conflict management is unavailable")
        result = dict(memory.resolve_memory_conflict(conflict_id, resolution, reason=reason))
        self.record_audit_event(
            "memory_conflict_resolved",
            actor="memory_manager",
            channel="desktop",
            target=str(conflict_id or ""),
            operation=str(result.get("resolution") or resolution),
            success=True,
            message=str(reason or "")[:160],
            metadata={
                "source_entry_id": result.get("source_entry_id"),
                "target_entry_id": result.get("target_entry_id"),
                "status": result.get("status"),
            },
        )
        self._emit_runtime_events(self.build_lpm_state_events())
        return result

    def _relationship_snapshot(self) -> dict[str, object]:
        memory = getattr(self, "memory_orchestrator", None)
        if memory is None or not hasattr(memory, "snapshot"):
            return {}
        try:
            snapshot = memory.snapshot()
        except Exception:
            return {}
        relationship = snapshot.get("relationship") if isinstance(snapshot, dict) else None
        return dict(relationship or {}) if isinstance(relationship, dict) else {}

    def handle_proactive_signal(self, signal: ProactiveSignal, *, now: float | None = None) -> dict[str, object]:
        relationship = self._relationship_snapshot()
        presence = self.presence.snapshot() if self.presence is not None else {}
        _, _, event = self.proactive.evaluate_signal(
            signal,
            relationship=relationship,
            presence=presence,
            now=now,
        )
        self._persist_and_emit_runtime_event(event)
        bubble_event = self.opening_bubbles.from_proactive_event(event, now=now)
        if bubble_event is not None:
            self._persist_and_emit_runtime_event(bubble_event)
        return event

    def record_proactive_feedback(
        self,
        signal_id: str,
        feedback: str,
        *,
        now: float | None = None,
    ) -> dict[str, object]:
        relationship = self._relationship_snapshot()
        event = self.proactive.record_feedback(
            signal_id,
            feedback,
            relationship_stage=str(relationship.get("stage") or "new"),
            now=now,
        )
        self._persist_and_emit_runtime_event(event)
        return event

    def _persist_and_emit_runtime_event(self, event: dict[str, object]) -> None:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        with self._runtime_event_lock:
            self.event_persistence.record(str(event.get("type") or "runtime.event"), payload)
            self._emit_runtime_event(event)

    def accept_proactive_suggestion(
        self,
        signal_id: str,
        *,
        channel: str = "desktop",
        metadata: dict[str, object] | None = None,
    ) -> AgentReply | None:
        suggestion = self.proactive.suggestion_for(signal_id)
        if suggestion is None:
            raise ValueError(f"unknown proactive signal: {signal_id}")
        self.record_proactive_feedback(signal_id, "accepted")
        interaction_metadata = dict(metadata or {})
        interaction_metadata.update(
            {
                "proactive_acceptance": True,
                "proactive_signal_id": signal_id,
                "proactive_suggestion_id": suggestion.suggestion_id,
            }
        )
        return self.handle_input(
            InteractionInput(
                text=suggestion.action_prompt,
                channel=channel,
                metadata=interaction_metadata,
            )
        )

    def _handle_presence_checkin(self) -> None:
        try:
            snapshot = self.presence.snapshot()
            signal = signal_from_presence(snapshot)
            if signal is not None:
                self.handle_proactive_signal(signal)
        except Exception:
            return

    def _process_user_input(self, interaction: InteractionInput) -> AgentReply:
        if not hasattr(self.agent, "process"):
            raise TypeError("agent 必须实现 process(user_input, visual_context='')")
        process_fn = self.agent.process
        params = inspect.signature(process_fn).parameters
        kwargs = {"visual_context": interaction.visual_context}
        if "channel" in params:
            kwargs["channel"] = interaction.channel
        if "input_metadata" in params:
            kwargs["input_metadata"] = dict(interaction.metadata)
        return process_fn(interaction.text.strip(), **kwargs)

    @staticmethod
    def _summarize_text_for_voice(text: str) -> str:
        text = SpiritKinRuntime._strip_avatar_tags(text)
        for separator in ("。", "！", "？", "\n"):
            if separator in text:
                summary, remainder = text.split(separator, 1)
                summary = summary.strip()
                if summary and remainder.strip():
                    return f"{summary}。"

        if len(text) <= 60:
            return text

        return f"{text[:60].rstrip()}。"

    @classmethod
    def _build_speech_output(cls, reply: AgentReply) -> str:
        if isinstance(reply.metadata, dict) and reply.metadata.get("speech_disabled"):
            return ""
        if reply.spoken_text:
            return cls._strip_avatar_tags(reply.spoken_text)
        return cls._summarize_text_for_voice(reply.text)

    @staticmethod
    def _strip_avatar_tags(text: str) -> str:
        return AVATAR_TAG_PATTERN.sub("", text or "").strip()

    @staticmethod
    def _normalize_voice_text(text: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()

    @staticmethod
    def correct_voice_transcript(text: str) -> str:
        corrected = (text or "").strip()
        if not corrected:
            return corrected

        replacements = {
            "speechtree": "SpeedTree",
            "speech tree": "SpeedTree",
            "speechtri": "SpeedTree",
            "開啟": "打开",
            "打開": "打开",
            "關閉": "关闭",
            "關掉": "关闭",
            "搜尋": "搜索",
            "搵": "搜索",
            "確認": "确认",
            "確認執行": "确认执行",
            "執行": "执行",
            "取消執行": "取消执行",
            "飛書": "飞书",
            "瀏覽器": "浏览器",
            "游覽器": "浏览器",
            "螢幕": "屏幕",
            "睇下": "看一下",
            "睇吓": "看一下",
            "幫我": "帮我",
            "機械B": "机械臂",
            "机械B": "机械臂",
            "機械b": "机械臂",
            "机械b": "机械臂",
            "机械毕竟": "机械臂",
            "非书": "飞书",
            "飞鼠": "飞书",
            "飞输": "飞书",
            "菲书": "飞书",
            "游览器": "浏览器",
            "留览器": "浏览器",
            "刘览器": "浏览器",
            "爱居浏览器": "Edge浏览器",
            "艾居浏览器": "Edge浏览器",
            "edge浏览器": "Edge浏览器",
            "Edge 浏览器": "Edge浏览器",
            "谷哥浏览器": "谷歌浏览器",
            "骨歌浏览器": "谷歌浏览器",
            "火爆浏览器": "火豹浏览器",
            "火暴浏览器": "火豹浏览器",
            "火包浏览器": "火豹浏览器",
            "火狐游览器": "火狐浏览器",
            "火狐留览器": "火狐浏览器",
        }
        for source, target in replacements.items():
            corrected = re.sub(re.escape(source), target, corrected, flags=re.IGNORECASE)

        corrected = re.sub(r"^[sSＳｓ]+(?=[\u4e00-\u9fff])", "", corrected)
        corrected = re.sub(r"(?<!打)開(?=啟|浏览器|瀏覽器|飞书|飛書|微信|钉钉|釘釘|edge|chrome|firefox|brave|opera)", "打开", corrected, flags=re.IGNORECASE)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))新的浏览器", r"\1Edge浏览器", corrected)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))\s+edge\s+浏览器", r"\1Edge浏览器", corrected, flags=re.IGNORECASE)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))\s+fire\s*fox\s*浏览器", r"\1Firefox浏览器", corrected, flags=re.IGNORECASE)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))\s+chrome\s*浏览器", r"\1Chrome浏览器", corrected, flags=re.IGNORECASE)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))\s+brave\s*浏览器", r"\1Brave浏览器", corrected, flags=re.IGNORECASE)
        corrected = re.sub(r"((?:打开|启动|运行|开一下|帮我开一下))\s+opera\s*浏览器", r"\1Opera浏览器", corrected, flags=re.IGNORECASE)
        if any(keyword in corrected for keyword in ("听到", "听见", "聽到", "聽見")):
            corrected = re.sub(r"我所谓", "我说话", corrected)
            corrected = re.sub(r"你所谓", "你说话", corrected)
        corrected = re.sub(r"([。！？!?])\1+", r"\1", corrected)
        # Let IntentResolver (LLM) handle all app name correction
        # Hardcoded rules only do basic homophone fixes
        return corrected.strip()

    def _prepare_voice_text_and_metadata(self, text: str, metadata: dict | None = None) -> tuple[str, dict[str, object]]:
        raw_text = (text or "").strip()
        stripped_text = self._strip_hotword_prefix(raw_text)
        corrected_text = self.correct_voice_transcript(stripped_text)
        voice_metadata: dict[str, object] = dict(metadata or {})
        voice_metadata.setdefault("raw_voice_text", raw_text)
        if stripped_text != raw_text:
            voice_metadata["hotword_stripped_text"] = stripped_text
        if corrected_text != stripped_text:
            voice_metadata["asr_original_text"] = stripped_text
            voice_metadata["asr_corrected_text"] = corrected_text
        return corrected_text, voice_metadata

    def _strip_hotword_prefix(self, text: str) -> str:
        raw_text = (text or "").strip()
        raw_hotword = (self.hotword or "").strip()
        if not raw_text or not raw_hotword:
            return raw_text

        stripped = re.sub(
            rf"^{re.escape(raw_hotword)}[\s,，。.!！?？:：、-]*",
            "",
            raw_text,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        return stripped or raw_text

    def _should_ignore_followup_input(self, text: str) -> bool:
        normalized_text = self._normalize_voice_text(text)
        normalized_hotword = self._normalize_voice_text(self.hotword)
        if not normalized_text:
            return True
        if normalized_hotword and normalized_text == normalized_hotword:
            return True
        if normalized_text in {"啊", "呀", "喂", "嗯", "唉", "诶", "呃", "额", "哦", "哈", "哎", "喔", "吓", "下", "咦", "s", "ss"}:
            return True
        return False

    @staticmethod
    def _voice_visual_context_enabled() -> bool:
        return os.getenv("SPIRITKIN_ENABLE_VOICE_VISION_CONTEXT", "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _command_listen_delay_seconds() -> float:
        raw_delay = os.getenv("SPIRITKIN_COMMAND_LISTEN_DELAY", "0.25").strip()
        try:
            return max(0.0, float(raw_delay))
        except ValueError:
            return 0.25

    @staticmethod
    def _post_tts_listen_cooldown_seconds() -> float:
        raw_delay = os.getenv("SPIRITKIN_POST_TTS_LISTEN_COOLDOWN", "0.9").strip()
        try:
            return max(0.0, float(raw_delay))
        except ValueError:
            return 0.9

    @staticmethod
    def _hotword_timeout_seconds() -> float:
        raw_timeout = os.getenv("SPIRITKIN_HOTWORD_TIMEOUT", "0.8").strip()
        try:
            return max(0.2, float(raw_timeout))
        except ValueError:
            return 0.8

    @staticmethod
    def _hotword_phrase_time_limit_seconds() -> float:
        raw_limit = os.getenv("SPIRITKIN_HOTWORD_PHRASE_TIME_LIMIT", "1.0").strip()
        try:
            return max(0.35, float(raw_limit))
        except ValueError:
            return 1.0

    @staticmethod
    def _preload_text_model_enabled() -> bool:
        return os.getenv("SPIRITKIN_PRELOAD_TEXT_MODEL", "1").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _voice_session_max_turns() -> int:
        raw_turns = os.getenv("SPIRITKIN_VOICE_SESSION_MAX_TURNS", "12").strip()
        try:
            return max(1, int(raw_turns))
        except ValueError:
            return 12

    @staticmethod
    def _voice_session_idle_timeouts() -> int:
        raw_timeouts = os.getenv("SPIRITKIN_VOICE_SESSION_IDLE_TIMEOUTS", "4").strip()
        try:
            return max(1, int(raw_timeouts))
        except ValueError:
            return 4

    @staticmethod
    def _voice_session_active_seconds() -> float:
        raw_seconds = os.getenv("SPIRITKIN_VOICE_SESSION_ACTIVE_SECONDS", "60").strip()
        try:
            return max(5.0, float(raw_seconds))
        except ValueError:
            return 60.0

    @staticmethod
    def _voice_command_timeout_seconds() -> float:
        raw_timeout = os.getenv("SPIRITKIN_VOICE_COMMAND_TIMEOUT", "8").strip()
        try:
            return max(1.0, float(raw_timeout))
        except ValueError:
            return 8.0

    @staticmethod
    def _voice_phrase_time_limit_seconds() -> float:
        raw_limit = os.getenv("SPIRITKIN_VOICE_PHRASE_TIME_LIMIT", "8").strip()
        try:
            return max(2.0, float(raw_limit))
        except ValueError:
            return 8.0

    @staticmethod
    def _env_flag(names: tuple[str, ...], default: str = "1") -> bool:
        raw_value = None
        for name in names:
            value = os.getenv(name)
            if value is not None:
                raw_value = value
                break
        if raw_value is None:
            raw_value = default
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _voice_ack_enabled() -> bool:
        return SpiritKinRuntime._env_flag(("SPIRITKIN_VOICE_ACK_ENABLED", "SPIRITKIN_VOICE_ACK"), default="0")

    @staticmethod
    def _wake_ack_enabled() -> bool:
        return SpiritKinRuntime._env_flag(("SPIRITKIN_WAKE_ACK_ENABLED", "SPIRITKIN_WAKE_ACK"), default="0")

    @staticmethod
    def _startup_voice_prompt_enabled() -> bool:
        return SpiritKinRuntime._env_flag(("SPIRITKIN_STARTUP_VOICE_PROMPT", "SPIRITKIN_STARTUP_PROMPT"), default="0")

    @staticmethod
    def _build_voice_ack_text(user_input: str) -> str:
        text = user_input.strip()
        if len(text) > 28:
            text = text[:28].rstrip() + "……"
        return f"我听到：{text}"

    @staticmethod
    def _playback_echo_suppression_enabled() -> bool:
        return SpiritKinRuntime._env_flag(
            ("SPIRITKIN_PLAYBACK_ECHO_SUPPRESSION", "SPIRITKIN_SUPPRESS_PLAYBACK_ECHO"),
            default="1",
        )

    @staticmethod
    def _playback_echo_cooldown_seconds() -> float:
        raw_seconds = os.getenv("SPIRITKIN_PLAYBACK_ECHO_COOLDOWN", "1.2").strip()
        try:
            return max(0.0, float(raw_seconds))
        except ValueError:
            return 1.2

    @staticmethod
    def _playback_echo_memory_seconds() -> float:
        raw_seconds = os.getenv("SPIRITKIN_PLAYBACK_ECHO_MEMORY", "4.0").strip()
        try:
            return max(0.5, float(raw_seconds))
        except ValueError:
            return 4.0

    @staticmethod
    def _playback_echo_similarity_threshold() -> float:
        raw_threshold = os.getenv("SPIRITKIN_PLAYBACK_ECHO_SIMILARITY", "0.72").strip()
        try:
            return min(0.95, max(0.5, float(raw_threshold)))
        except ValueError:
            return 0.72

    @classmethod
    def _is_control_confirmation_text(cls, text: str) -> bool:
        normalized_text = cls._normalize_voice_text(text)
        if not normalized_text:
            return False
        exact_controls = {
            "确认",
            "确认执行",
            "可以执行",
            "执行吧",
            "继续执行",
            "取消",
            "取消执行",
            "不要执行",
            "别执行",
            "停止",
            "停止执行",
            "中止执行",
            "退出",
            "再见",
        }
        if normalized_text in exact_controls:
            return True
        return len(normalized_text) <= 8 and any(
            keyword in normalized_text
            for keyword in ("确认", "取消", "别执行", "不要执行", "停止执行", "中止执行")
        )

    @classmethod
    def _playback_echo_text_variants(cls, text: str) -> list[str]:
        normalized_text = cls._normalize_voice_text(text)
        if not normalized_text:
            return []
        variants = [normalized_text]
        for prefix in ("我听到", "听到", "我在", "好的", "收到"):
            if normalized_text.startswith(prefix):
                stripped = normalized_text[len(prefix) :]
                if stripped:
                    variants.append(stripped)
        return list(dict.fromkeys(variants))

    def _record_spoken_output(self, text: str) -> None:
        if not self._playback_echo_suppression_enabled():
            return
        variants = self._playback_echo_text_variants(text)
        if not variants:
            return

        now = time.monotonic()
        self._last_spoken_output_ended_at = now
        self._recent_spoken_outputs.append(
            {
                "text": text,
                "variants": variants,
                "ended_at": now,
            }
        )
        memory_seconds = self._playback_echo_memory_seconds()
        self._recent_spoken_outputs = [
            item
            for item in self._recent_spoken_outputs[-8:]
            if now - float(item.get("ended_at") or 0.0) <= memory_seconds
        ]

    def _is_probable_playback_echo(self, text: str, metrics: dict[str, object] | None = None) -> bool:
        if not self._playback_echo_suppression_enabled():
            return False
        if self._is_control_confirmation_text(text):
            return False

        heard_variants = self._playback_echo_text_variants(text)
        if not heard_variants:
            return False

        now = time.monotonic()
        memory_seconds = self._playback_echo_memory_seconds()
        cooldown_seconds = self._playback_echo_cooldown_seconds()
        threshold = self._playback_echo_similarity_threshold()

        for item in list(self._recent_spoken_outputs):
            ended_at = float(item.get("ended_at") or 0.0)
            age = now - ended_at
            if age > memory_seconds:
                continue
            spoken_variants = [str(value) for value in item.get("variants", []) if str(value)]
            for heard in heard_variants:
                for spoken in spoken_variants:
                    if heard == spoken:
                        return True
                    if age <= cooldown_seconds and min(len(heard), len(spoken)) >= 4 and (heard in spoken or spoken in heard):
                        return True
                    if min(len(heard), len(spoken)) >= 6 and difflib.SequenceMatcher(None, heard, spoken).ratio() >= threshold:
                        return True
        return False

    @staticmethod
    def _serialize_attachments(attachments: tuple[AttachmentRef, ...] | list[AttachmentRef] | None) -> list[dict[str, object]]:
        return [asdict(attachment) for attachment in list(attachments or [])]

    @staticmethod
    def _resolve_response_kind(reply: AgentReply, metadata: dict[str, object]) -> str:
        if metadata.get("response_kind"):
            return str(metadata["response_kind"])
        if reply.requires_confirmation:
            return "confirmation_request"
        return "message"

    @classmethod
    def _build_presentation_hints(cls, reply: AgentReply, metadata: dict[str, object]) -> dict[str, object]:
        response_kind = cls._resolve_response_kind(reply, metadata)
        primary = "bubble"
        layout = "chat"
        emphasis = "normal"

        if response_kind in {"development_plan", "repair_plan"}:
            primary = "card"
            layout = "chat_with_details"
            emphasis = "structured"
        elif metadata.get("task"):
            primary = "card"
            layout = "chat_with_details"
            emphasis = "progress"
        elif response_kind == "confirmation_request" or reply.requires_confirmation:
            primary = "confirm_sheet"
            layout = "chat_with_action_bar"
            emphasis = "high_risk"

        return {
            "primary": primary,
            "layout": layout,
            "emphasis": emphasis,
            "show_spoken_text_as_subtitle": True,
            "show_attachments_as_chips": bool(metadata.get("input_attachments")),
            "show_task_status": bool(metadata.get("task")),
            "show_project_status": bool(metadata.get("project")),
        }

    @staticmethod
    def _metadata_scope(metadata: dict[str, object]) -> dict[str, str]:
        client_metadata = metadata.get("client_metadata")
        if not isinstance(client_metadata, dict):
            client_metadata = {}
        return {
            "session_id": str(metadata.get("session_id") or client_metadata.get("session_id") or ""),
            "request_id": str(metadata.get("request_id") or client_metadata.get("request_id") or ""),
        }

    @classmethod
    def _build_live2d_payload(cls, reply: AgentReply, speech_output: str, metadata: dict[str, object]) -> dict[str, object]:
        response_kind = cls._resolve_response_kind(reply, metadata)
        scope = cls._metadata_scope(metadata)
        return {
            "type": "avatar.state",
            "schema_version": EVENT_SCHEMA_VERSION,
            "emotion": reply.emotion,
            "speaking": bool(speech_output),
            "action": reply.action,
            "message": speech_output,
            "response_kind": response_kind,
            "agent_name": reply.agent_name,
            "requires_confirmation": reply.requires_confirmation,
            "session_id": scope["session_id"],
            "request_id": scope["request_id"],
        }

    def _build_tooling_snapshot(self) -> dict[str, object]:
        tools = list(getattr(self.agent, "available_tools", []) or [])
        skills = list(getattr(self.agent, "available_skills", []) or [])
        active_route = build_active_route_runtime_snapshot()
        managed_agents = build_managed_agent_runtime_snapshot()
        capability_graph = getattr(self.agent, "capability_graph_snapshot", {}) or {}
        worker_pool = getattr(self.agent, "worker_pool_snapshot", {}) or {}
        brain_router = getattr(self.agent, "brain_router_snapshot", {}) or {}
        return {
            "tool_count": len(tools),
            "skill_count": len(skills),
            "capability_count": int(capability_graph.get("total") or 0) if isinstance(capability_graph, dict) else 0,
            "worker_count": int(worker_pool.get("total") or 0) if isinstance(worker_pool, dict) else 0,
            "brain_route_count": len(brain_router.get("audit") or []) if isinstance(brain_router, dict) else 0,
            "active_route_profile_id": active_route.get("active_route_profile_id", ""),
            "active_route": active_route.get("profile", {}),
            "primary_text_route": active_route.get("primary_text", {}),
            "managed_agents": managed_agents,
            "capability_graph": capability_graph,
            "worker_pool": worker_pool,
            "brain_router": brain_router,
            "tools": [
                {
                    "name": getattr(tool, "name", ""),
                    "target": getattr(tool, "target", ""),
                    "operation": getattr(tool, "operation", ""),
                    "risk_level": getattr(tool, "risk_level", "medium"),
                    "read_only": bool(getattr(tool, "read_only", False)),
                }
                for tool in tools[:24]
            ],
            "skills": [
                {
                    "name": getattr(skill, "name", ""),
                    "description": getattr(skill, "description", ""),
                }
                for skill in skills[:12]
            ],
        }

    def _build_inventory_snapshot(self) -> dict[str, object]:
        inventory = getattr(self.agent, "recent_inventory", {}) or {}
        software = list(inventory.get("software", []) or []) if isinstance(inventory, dict) else []
        hardware = list(inventory.get("hardware", []) or []) if isinstance(inventory, dict) else []
        devices = inventory.get("devices", {}) if isinstance(inventory, dict) else {}
        scopes = []
        if isinstance(devices, dict):
            for scope_id, record in list(devices.items())[:8]:
                if not isinstance(record, dict):
                    continue
                scopes.append(
                    {
                        "scope": scope_id,
                        "label": str(record.get("label") or scope_id),
                        "software_count": len(list(record.get("software", []) or [])),
                        "hardware_count": len(list(record.get("hardware", []) or [])),
                    }
                )
        return {
            "software_count": len(software),
            "hardware_count": len(hardware),
            "device_scope_count": len(scopes),
            "scopes": scopes,
        }

    def _build_workflow_snapshot(self) -> dict[str, object]:
        records = list(getattr(self.agent, "workflow_memory_snapshot", []) or [])
        latest = records[-1] if records else {}
        stats = dict(getattr(self.agent, "workflow_memory_stats", {}) or {})
        skill_candidates = list(getattr(self.agent, "workflow_skill_candidates", []) or [])
        return {
            "recent_count": len(records),
            "latest_workflow_id": latest.get("workflow_id"),
            "latest_operation": latest.get("operation"),
            "latest_target": latest.get("target"),
            "stats": stats,
            "skill_candidate_count": len(skill_candidates),
            "skill_candidates": skill_candidates[:5],
        }

    def _build_safety_snapshot(self) -> dict[str, object]:
        pending = getattr(self.agent, "pending_execution", None)
        return {
            "pending_confirmation": pending is not None,
            "pending_target": getattr(getattr(pending, "request", None), "target", None),
            "pending_operation": getattr(getattr(pending, "request", None), "operation", None),
            "risk_level": getattr(pending, "risk_level", None),
        }

    def _build_remote_nodes_snapshot(self) -> dict[str, object]:
        if self.node_registry is None:
            return {"total": 0, "status_counts": {}, "nodes": []}
        snapshot = self.node_registry.snapshot()
        nodes = list(snapshot.get("nodes", []) or [])
        snapshot["nodes"] = nodes[:12]
        return snapshot

    def _build_audit_snapshot(self) -> dict[str, object]:
        return self.audit_log.summary(limit=30)

    def record_audit_event(self, event_type: str, **kwargs: object) -> None:
        self.audit_log.record(event_type, **kwargs)

    def _audit_input(self, interaction: InteractionInput) -> None:
        if interaction.channel not in {"mobile", "web", "desktop"}:
            return
        metadata = canonicalize_composer_metadata(dict(interaction.metadata or {}))
        self.record_audit_event(
            "command_received",
            actor=str(metadata.get("client_type") or interaction.channel),
            channel=interaction.channel,
            message=interaction.text[:160],
            metadata={"client_id": metadata.get("client_id"), "has_attachments": bool(interaction.attachments)},
        )

    def _audit_reply(self, reply: AgentReply) -> None:
        metadata = dict(reply.metadata or {})
        response_kind = str(metadata.get("response_kind") or "")
        if response_kind == "confirmation_request" or reply.requires_confirmation:
            self.record_audit_event(
                "confirmation_requested",
                actor="execution_guard",
                channel=str(metadata.get("input_channel") or ""),
                target=str(metadata.get("pending_target") or ""),
                operation=str(metadata.get("pending_operation") or ""),
                risk_level=str(metadata.get("risk_level") or "high"),
                message=reply.text[:160],
                metadata={"agent_name": reply.agent_name},
            )
        if response_kind == "confirmation_cancelled":
            self.record_audit_event(
                "confirmation_cancelled",
                actor="execution_guard",
                channel=str(metadata.get("input_channel") or ""),
                target=str(metadata.get("cancelled_target") or ""),
                operation=str(metadata.get("cancelled_operation") or ""),
                risk_level="high",
                success=False,
                message=reply.text[:160],
            )

        execution = metadata.get("execution")
        if isinstance(execution, dict):
            execution_metadata = dict(execution.get("metadata") or {})
            target = str(execution.get("target") or "")
            self.record_audit_event(
                "execution_result",
                actor=str(reply.agent_name or "executor"),
                channel=str(metadata.get("input_channel") or ""),
                target=target,
                operation=str(execution.get("operation") or ""),
                risk_level=str(metadata.get("risk_level") or ""),
                success=execution.get("success") is not False,
                message=str(execution.get("error") or reply.text)[:160],
                metadata={
                    "node_id": execution_metadata.get("node_id"),
                    "remote_target": execution_metadata.get("remote_target"),
                    "error_code": execution.get("error_code"),
                },
            )

    @classmethod
    def build_input_payload(cls, interaction: InteractionInput) -> dict[str, object]:
        return {
            "type": "user_input",
            "schema_version": EVENT_SCHEMA_VERSION,
            "channel": interaction.channel,
            "text": interaction.text,
            "visual_context": interaction.visual_context,
            "attachments": cls._serialize_attachments(interaction.attachments),
            "metadata": dict(interaction.metadata or {}),
        }

    @classmethod
    def build_output_payload(cls, reply: AgentReply) -> dict[str, object]:
        speech_output = cls._build_speech_output(reply)
        metadata = dict(reply.metadata or {})
        response_kind = cls._resolve_response_kind(reply, metadata)
        presentation = cls._build_presentation_hints(reply, metadata)
        live2d_payload = cls._build_live2d_payload(reply, speech_output, metadata)
        payload = {
            "type": "agent_reply",
            "schema_version": EVENT_SCHEMA_VERSION,
            "text": reply.text,
            "spoken_text": speech_output,
            "emotion": reply.emotion,
            "action": reply.action,
            "agent_name": reply.agent_name,
            "requires_confirmation": reply.requires_confirmation,
            "response_kind": response_kind,
            "attachments": list(metadata.get("input_attachments", []) or []),
            "presentation": presentation,
            "data": metadata,
            "live2d": live2d_payload,
            "avatar_reaction": dict(metadata.get("avatar_reaction") or {}),
        }
        payload["model_interaction"] = build_response_interaction(payload)["payload"]
        return payload

    @classmethod
    def build_response_events(cls, reply: AgentReply) -> list[dict[str, object]]:
        payload = cls.build_output_payload(reply)
        execution_payload = payload["data"].get("execution")
        scope = {
            "session_id": str(payload["model_interaction"].get("session_id") or ""),
            "request_id": str(payload["model_interaction"].get("request_id") or ""),
        }
        events: list[dict[str, object]] = [
            {
                "type": "assistant.message",
                "schema_version": EVENT_SCHEMA_VERSION,
                "payload": payload,
            }
        ]

        if payload["response_kind"] == "confirmation_request" or reply.requires_confirmation:
            events.append(
                {
                    "type": "assistant.confirmation_requested",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "payload": {
                        "text": reply.text,
                        "spoken_text": payload["spoken_text"],
                        "action": reply.action,
                        "risk_level": payload["data"].get("risk_level", "high"),
                        "pending_target": payload["data"].get("pending_target"),
                        "pending_operation": payload["data"].get("pending_operation"),
                        **scope,
                    },
                }
            )

        if payload["data"].get("task"):
            events.append(
                {
                    "type": "assistant.task_updated",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "payload": payload["data"]["task"],
                }
            )

        if payload["data"].get("project"):
            events.append(
                {
                    "type": "assistant.project_updated",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "payload": payload["data"]["project"],
                }
            )

        if payload["response_kind"] == "execution_result" and execution_payload:
            if isinstance(execution_payload, dict):
                execution_payload = dict(execution_payload)
                execution_payload.setdefault("session_id", scope["session_id"])
                execution_payload.setdefault("request_id", scope["request_id"])
            events.append(
                {
                    "type": "assistant.execution_updated",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "payload": execution_payload,
                }
            )

            execution_metadata = execution_payload.get("metadata", {})
            device_target = execution_metadata.get("remote_target") or execution_payload.get("target")
            if device_target == "openclaw" and isinstance(execution_payload.get("data"), dict):
                events.append(
                    {
                        "type": "device.openclaw_state_updated",
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "payload": {
                            "target": device_target,
                            "operation": execution_payload.get("operation"),
                            "state": execution_payload.get("data"),
                            "metadata": execution_metadata,
                        },
                    }
                )

        events.append(
            {
                "type": "avatar.state",
                "schema_version": EVENT_SCHEMA_VERSION,
                "payload": payload["live2d"],
            }
        )
        events.append(build_response_interaction(payload))
        events.append(build_aggregated_runtime_state_event(events, schema_version=EVENT_SCHEMA_VERSION))
        events.extend(cls.build_speech_phoneme_events(str(payload.get("spoken_text") or "")))
        return events

    @classmethod
    def build_speech_phoneme_events(cls, speech_text: str, *, max_events: int = 48) -> list[dict[str, object]]:
        if not speech_text.strip():
            return []
        phonemes = text_to_phoneme_events(speech_text)[: max(0, int(max_events))]
        return [
            {
                "type": "speech.phoneme",
                "schema_version": EVENT_SCHEMA_VERSION,
                "payload": {
                    "sequence": index,
                    "char": event.get("char", ""),
                    "phoneme": event.get("phoneme", ""),
                    "mouth_shape": event.get("mouth_shape", "mid"),
                    "timestamp_ms": event.get("timestamp_ms", 0),
                    "duration_ms": event.get("duration_ms", 150),
                    "source": "text_timeline",
                },
            }
            for index, event in enumerate(phonemes)
        ]

    def build_capabilities_payload(self) -> dict[str, object]:
        model_capabilities = describe_model_capabilities(config_path=self.config_path)
        return {
            "type": "runtime.capabilities",
            "schema_version": EVENT_SCHEMA_VERSION,
            "channels": ["text", "voice", "mobile", "desktop", "web", "wechat"],
            "supports": {
                "attachments": True,
                "voice_input": True,
                "voice_summary": True,
                "visual_context": True,
                "confirmation_gate": True,
                "execution_events": True,
                "device_state_events": True,
                "duplex_voice_session": True,
                "interruptible_speech": True,
                "phoneme_events": True,
                "lpm_state_events": True,
                "relationship_state_events": True,
                "proactive_suggestions": True,
                "proactive_feedback": True,
                "opening_bubbles": True,
                "persistent_scheduler": True,
                "performance_events": True,
                "task_queue": True,
                "task_status_events": True,
                "project_state_events": True,
                "aggregated_runtime_state": True,
                "avatar_narrator": True,
            },
            "preferences": {
                "text_modes": model_capabilities["text"]["available_modes"],
                "default_text_mode": model_capabilities["text"]["default_mode"],
                "vision_modes": model_capabilities["vision"]["available_modes"],
                "default_vision_mode": model_capabilities["vision"]["default_mode"],
            },
            "models": model_capabilities,
            "recommended_model_stack": describe_recommended_model_stack(),
            "model_catalog": self._build_model_catalog_snapshot(),
            "tooling": self._build_tooling_snapshot(),
            "inventory": self._build_inventory_snapshot(),
            "workflow_memory": self._build_workflow_snapshot(),
            "safety": self._build_safety_snapshot(),
            "remote_nodes": self._build_remote_nodes_snapshot(),
            "audit": self._build_audit_snapshot(),
            "presence": self.presence.snapshot() if self.presence else {},
            "memory": self.memory_orchestrator.snapshot() if getattr(self, "memory_orchestrator", None) is not None else {},
            "event_store": self.event_persistence.stats() if self.event_persistence else {},
        }

    def _build_model_catalog_snapshot(self) -> dict[str, object]:
        catalog = load_model_catalog()
        models = list(catalog.get("models") or []) if isinstance(catalog, dict) else []
        local_policy = build_local_model_policy_snapshot(model_catalog=catalog if isinstance(catalog, dict) else {})
        brain_replacement = build_brain_replacement_snapshot(model_catalog=catalog if isinstance(catalog, dict) else {})
        return {
            "schema_version": catalog.get("schema_version", "") if isinstance(catalog, dict) else "",
            "source": catalog.get("source", "bundled") if isinstance(catalog, dict) else "bundled",
            "updated_at": catalog.get("updated_at", 0) if isinstance(catalog, dict) else 0,
            "online": bool(catalog.get("online", False)) if isinstance(catalog, dict) else False,
            "model_count": len(models),
            "models": models[:12],
            "failure_count": len(list(catalog.get("failures") or [])) if isinstance(catalog, dict) else 0,
            "local_model_policy": local_policy,
            "brain_replacement": brain_replacement,
        }

    @staticmethod
    def _current_time_context() -> dict[str, str]:
        now = datetime.now().astimezone()
        return {
            "iso": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "timezone": now.tzname() or str(now.utcoffset() or ""),
        }

    def handle_input(self, interaction: InteractionInput) -> AgentReply | None:
        if interaction.channel == "voice":
            corrected_text, voice_metadata = self._prepare_voice_text_and_metadata(interaction.text, interaction.metadata)
            interaction = InteractionInput(
                text=corrected_text,
                channel=interaction.channel,
                visual_context=interaction.visual_context,
                attachments=interaction.attachments,
                metadata=voice_metadata,
            )
        metadata = dict(interaction.metadata or {})
        metadata.setdefault("current_time", self._current_time_context())
        metadata.setdefault("input_channel", interaction.channel)
        interaction = InteractionInput(
            text=interaction.text,
            channel=interaction.channel,
            visual_context=interaction.visual_context,
            attachments=interaction.attachments,
            metadata=metadata,
        )
        normalized_text = interaction.text.strip()
        if not normalized_text:
            return None

        self.presence.on_activity()
        self.event_persistence.record("user_input", {"text": normalized_text[:200], "channel": interaction.channel})
        self._audit_input(interaction)
        self._emit_runtime_event(self.build_input_payload(interaction))
        reply = self._process_user_input(interaction)
        reply.text = self._strip_avatar_tags(reply.text)
        if reply.spoken_text:
            reply.spoken_text = self._strip_avatar_tags(reply.spoken_text)
        enrich_reply_avatar_reaction(reply)
        reply.metadata.setdefault("input_channel", interaction.channel)
        if interaction.attachments:
            reply.metadata.setdefault("input_attachments", self._serialize_attachments(interaction.attachments))
        if interaction.metadata:
            reply.metadata.setdefault("client_metadata", dict(interaction.metadata))
            request_id = str(interaction.metadata.get("request_id") or "").strip()
            if request_id:
                reply.metadata.setdefault("request_id", request_id)
            session_id = str(interaction.metadata.get("session_id") or "").strip()
            if session_id:
                reply.metadata.setdefault("session_id", session_id)

        if interaction.channel == "voice" and not reply.requires_confirmation and (not reply.spoken_text or reply.spoken_text == reply.text):
            reply.spoken_text = self._summarize_text_for_voice(reply.text)

        self._audit_reply(reply)
        self._record_lpm_interaction(normalized_text, reply)
        self._emit_runtime_events(self.build_response_events(reply) + self.build_lpm_state_events())
        return reply

    def _record_lpm_interaction(self, user_input: str, reply: AgentReply) -> None:
        memory = getattr(self, "memory_orchestrator", None)
        if memory is None or not hasattr(memory, "record_interaction"):
            return
        execution = reply.metadata.get("execution") if isinstance(reply.metadata, dict) else None
        success = True if not isinstance(execution, dict) else bool(execution.get("success", True))
        try:
            memory.record_interaction(user_input=user_input, reply_text=reply.spoken_text or reply.text, success=success)
        except Exception:
            return

    def build_lpm_state_events(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if self.presence is not None:
            events.append({"type": "presence.updated", "schema_version": EVENT_SCHEMA_VERSION, "payload": self.presence.snapshot()})
        memory = getattr(self, "memory_orchestrator", None)
        if memory is not None and hasattr(memory, "snapshot"):
            snapshot = memory.snapshot()
            if snapshot:
                events.append({"type": "memory.updated", "schema_version": EVENT_SCHEMA_VERSION, "payload": snapshot})
                personality = snapshot.get("personality") if isinstance(snapshot, dict) else None
                if personality:
                    events.append({"type": "personality.updated", "schema_version": EVENT_SCHEMA_VERSION, "payload": personality})
                relationship = snapshot.get("relationship") if isinstance(snapshot, dict) else None
                if relationship:
                    events.append({"type": "relationship.updated", "schema_version": EVENT_SCHEMA_VERSION, "payload": relationship})
        return events

    def handle_text_input(self, text: str, visual_context: str = "") -> AgentReply | None:
        return self.handle_input(InteractionInput(text=text, channel="text", visual_context=visual_context))

    def handle_voice_input(self, text: str, visual_context: str = "", metadata: dict | None = None) -> AgentReply | None:
        return self.handle_input(InteractionInput(text=text, channel="voice", visual_context=visual_context, metadata=dict(metadata or {})))

    def _handle_voice_input_compat(self, text: str, *, visual_context: str = "", metadata: dict | None = None) -> AgentReply | None:
        try:
            params = inspect.signature(self.handle_voice_input).parameters
        except (TypeError, ValueError):
            params = {}
        if "metadata" in params:
            return self.handle_voice_input(text, visual_context=visual_context, metadata=metadata)
        return self.handle_voice_input(text, visual_context=visual_context)

    @staticmethod
    def _load_runtime_dependencies():
        import speech_recognition as sr

        from backend.expression.avatar import trigger_emotion
        from backend.expression.speech import speak
        from backend.perception.audio.hotword import detect_hotword, get_wake_model
        from backend.perception.audio.listener import (
            calibrate_microphone,
            configure_recognizer_for_hotword,
            get_whisper_model,
            listen_from_microphone_with_metrics,
            resolve_microphone_device_index,
        )
        from backend.perception.vision_analyzer import analyze_gesture
        from backend.services.conversation_engine import preload_llm_engine

        return {
            "sr": sr,
            "trigger_emotion": trigger_emotion,
            "speak": speak,
            "analyze_gesture": analyze_gesture,
            "calibrate_microphone": calibrate_microphone,
            "preload_asr_model": get_whisper_model,
            "preload_hotword_model": get_wake_model,
            "preload_text_model": preload_llm_engine,
            "listen_from_microphone_with_metrics": listen_from_microphone_with_metrics,
            "detect_hotword": detect_hotword,
            "configure_recognizer_for_hotword": configure_recognizer_for_hotword,
            "resolve_microphone_device_index": resolve_microphone_device_index,
        }

    def run(self):
        deps = self._load_runtime_dependencies()
        sr = deps["sr"]
        trigger_emotion = deps["trigger_emotion"]
        speak = deps["speak"]
        analyze_gesture = deps["analyze_gesture"]
        calibrate_microphone = deps["calibrate_microphone"]
        preload_asr_model = deps.get("preload_asr_model")
        preload_hotword_model = deps.get("preload_hotword_model")
        preload_text_model = deps.get("preload_text_model")
        listen_from_microphone_with_metrics = deps.get("listen_from_microphone_with_metrics")
        if listen_from_microphone_with_metrics is None and deps.get("listen_from_microphone") is not None:
            legacy_listen_from_microphone = deps["listen_from_microphone"]

            def listen_from_microphone_with_metrics(timeout=8, phrase_time_limit=12):
                return {"text": legacy_listen_from_microphone(timeout=timeout, phrase_time_limit=phrase_time_limit)}
        detect_hotword = deps["detect_hotword"]
        configure_recognizer_for_hotword = deps.get("configure_recognizer_for_hotword")
        resolve_microphone_device_index = deps.get("resolve_microphone_device_index")

        def speak_and_record(text: str) -> None:
            speak(text)
            self._record_spoken_output(text)

        def wait_after_tts_before_listening() -> None:
            cooldown = self._post_tts_listen_cooldown_seconds()
            if cooldown > 0:
                time.sleep(cooldown)

        self._emit_runtime_event(self.build_capabilities_payload())

        recognizer = calibrate_microphone(duration=2) or sr.Recognizer()
        if configure_recognizer_for_hotword is not None:
            recognizer = configure_recognizer_for_hotword(recognizer) or recognizer
        hotword_microphone_index = None
        if resolve_microphone_device_index is not None:
            try:
                hotword_microphone_index, microphone_metadata = resolve_microphone_device_index()
                _safe_print(f"[🎙️] 热词监听麦克风: {microphone_metadata}")
            except Exception as exc:
                _safe_print(f"[⚠️] 热词麦克风选择失败，改用系统默认输入: {exc}")

        if self._startup_voice_prompt_enabled():
            speak_and_record("你好！我是Spirit，请说“Spirit”唤醒我～")
        if preload_asr_model is not None:
            preload_asr_model()
        if preload_hotword_model is not None:
            preload_hotword_model()
        if preload_text_model is not None and self._preload_text_model_enabled():
            if not preload_text_model():
                _safe_print("[⚠️] 本地文本模型未就绪，通用闲聊会返回离线提示；硬件/工具指令不受影响。")

        waiting_hotword_logged = False
        while True:
            try:
                if not waiting_hotword_logged:
                    _safe_print(f"[👂] 等待唤醒词“{self.hotword}”...")
                    waiting_hotword_logged = True

                microphone_kwargs = {"device_index": hotword_microphone_index} if hotword_microphone_index is not None else {}
                with sr.Microphone(**microphone_kwargs) as source:
                    audio = recognizer.listen(
                        source,
                        timeout=self._hotword_timeout_seconds(),
                        phrase_time_limit=self._hotword_phrase_time_limit_seconds(),
                    )
                    if not detect_hotword(audio, self.hotword):
                        continue

                    waiting_hotword_logged = False

                    if self._wake_ack_enabled():
                        speak_and_record("我在～")
                        wait_after_tts_before_listening()
                    trigger_emotion("happy", speaking=True, action="wave_hand")
                    time.sleep(self._command_listen_delay_seconds())

                    if self._voice_visual_context_enabled():
                        try:
                            gesture_desc = analyze_gesture()
                        except Exception as exc:
                            _safe_print(f"⚠️ 视觉分析失败: {exc}")
                            gesture_desc = ""
                    else:
                        gesture_desc = ""

                    should_exit = False
                    handled_followup = False
                    ignored_followups = 0
                    idle_timeouts = 0
                    active_deadline = time.monotonic() + self._voice_session_active_seconds()
                    for turn_index in range(self._voice_session_max_turns()):
                        if time.monotonic() >= active_deadline:
                            _safe_print("[↩️] 唤醒后 1 分钟内没有有效输入，已回到待唤醒状态。")
                            break
                        if turn_index > 0:
                            _safe_print("[👂] 连续会话中，可直接继续说下一条指令...")

                        asr_metrics = listen_from_microphone_with_metrics(
                            timeout=self._voice_command_timeout_seconds(),
                            phrase_time_limit=self._voice_phrase_time_limit_seconds(),
                        )
                        user_input = str(asr_metrics.get("text") or "").strip()
                        if not user_input:
                            idle_timeouts += 1
                            if idle_timeouts >= self._voice_session_idle_timeouts():
                                _safe_print("[↩️] 会话连续超时/没听清，已回到待唤醒状态。")
                                break
                            _safe_print("[👂] 这轮没听清，我还在，直接继续说下一条就行。")
                            speak_and_record("我还在，直接继续说就行。")
                            wait_after_tts_before_listening()
                            continue
                        idle_timeouts = 0

                        _safe_print(f"[🎤] 识别到语音指令: {user_input}")
                        processed_user_input, _ = self._prepare_voice_text_and_metadata(user_input)

                        if self._is_probable_playback_echo(processed_user_input, asr_metrics):
                            ignored_followups += 1
                            _safe_print("[↩️] 忽略疑似耳机/扬声器回声输入，避免把助手播报当成你的指令。")
                            if ignored_followups >= 2:
                                _safe_print("[↩️] 连续识别到播放回声，已回到待唤醒状态。")
                                break
                            continue

                        if self._should_ignore_followup_input(processed_user_input):
                            ignored_followups += 1
                            _safe_print("[↩️] 忽略热词后的残留语音/短噪声，请直接说具体指令。")
                            if ignored_followups >= 2:
                                _safe_print("[↩️] 多次只有残留热词，已回到待唤醒状态。")
                                break
                            continue

                        active_deadline = time.monotonic() + self._voice_session_active_seconds()

                        if any(kw in processed_user_input for kw in ["退出", "再见"]):
                            speak_and_record("再见啦～")
                            trigger_emotion("neutral", speaking=False)
                            should_exit = True
                            handled_followup = True
                            break

                        if self._voice_ack_enabled():
                            ack_text = self._build_voice_ack_text(processed_user_input)
                            trigger_emotion("thinking", speaking=True, action="voice_ack", message=ack_text, metadata={"response_kind": "voice_ack"})
                            speak_and_record(ack_text)
                            wait_after_tts_before_listening()

                        result = self._handle_voice_input_compat(user_input, visual_context=gesture_desc, metadata={"asr_metrics": asr_metrics})
                        handled_followup = True
                        if result is None:
                            break

                        speech_output = self._build_speech_output(result)
                        trigger_emotion(
                            emotion=result.emotion,
                            speaking=True,
                            action=result.action,
                            message=speech_output,
                            metadata={
                                "response_kind": result.metadata.get("response_kind", "message"),
                                "agent_name": result.agent_name,
                                "requires_confirmation": result.requires_confirmation,
                            },
                        )
                        speak_and_record(speech_output)
                        wait_after_tts_before_listening()
                        trigger_emotion("neutral", speaking=False)
                    else:
                        _safe_print("[↩️] 连续会话达到轮数上限，已回到待唤醒状态。")

                    if should_exit:
                        break
                    if not handled_followup:
                        trigger_emotion("neutral", speaking=False)

            except sr.WaitTimeoutError:
                continue
            except KeyboardInterrupt:
                break
            except Exception as exc:
                trigger_emotion("error", speaking=False)
                _safe_print(f"❌ 主循环异常: {exc}")


def install_runtime_work_event_hooks() -> None:
    try:
        from backend.app.codex_work_events import install
    except Exception:
        return
    install()


install_runtime_work_event_hooks()


def main():
    SpiritKinRuntime(emit_runtime_events=True).run()
