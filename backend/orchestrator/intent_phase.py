from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext, AgentReply
from backend.executors.base import ExecutionRequest
from backend.orchestrator.planner import ExecutionPlan, Planner
from backend.orchestrator.prompt_context import looks_like_action_request
from backend.orchestrator.reply_metadata import attach_intent_resolution_metadata


@dataclass(frozen=True)
class IntentPhaseServices:
    decision_cache: Any
    intent_resolver: Any
    available_tools: Callable[[], list[Any]]
    execute: Callable[..., AgentReply]
    correct_app_name: Callable[[str], str]
    voice_intent_mode: str


class IntentPhase:
    def __init__(self, services: IntentPhaseServices):
        self._services = services

    def resolve(self, context: AgentContext, source: str = "llm_fallback") -> AgentReply | None:
        channel = str(context.metadata.get("input_channel") or "")
        fingerprint = self._services.decision_cache.fingerprint(context.user_input, channel=channel, agent="intent")
        cached = self._services.decision_cache.lookup(fingerprint)
        if cached is not None:
            request = ExecutionRequest(target=cached.target, operation=cached.operation, params=dict(cached.params))
            if self.has_required_params(request):
                reply = self._services.execute(request, user_input=context.user_input)
                self._note_cache_outcome(reply, fingerprint, request, context, channel)
                reply.metadata = {**dict(reply.metadata or {}), "decision_cache_hit": True}
                return reply

        try:
            resolution = self._services.intent_resolver.resolve(context, self._services.available_tools())
        except Exception:
            return None
        if resolution.status == "execute" and resolution.execution_request is not None:
            request = resolution.execution_request
            if not self.has_required_params(request):
                return None
            if request.operation in {"launch_app", "close_app"}:
                raw_name = str(request.params.get("app_name") or "")
                if raw_name:
                    corrected = self._services.correct_app_name(raw_name)
                    if corrected != raw_name:
                        request.params["app_name"] = corrected
            reply = self._services.execute(request, user_input=context.user_input)
            self._note_cache_outcome(reply, fingerprint, request, context, channel)
            return attach_intent_resolution_metadata(reply, resolution, source=source)
        if resolution.status == "clarify":
            message = resolution.message or "这个动作我还没理解准确，请换个说法。"
            reply = AgentReply(
                text=message,
                spoken_text=message,
                emotion="confused",
                action="tilt_head",
                agent_name="intent_resolver",
                metadata={
                    "response_kind": "intent_clarification",
                    "intent_resolution": {
                        "status": resolution.status,
                        "reason": resolution.reason,
                        "confidence": resolution.confidence,
                        "source": source,
                    },
                },
            )
            if resolution.corrected_text:
                reply.metadata["intent_resolution"]["corrected_text"] = resolution.corrected_text
            return reply
        return None

    def should_run_before_planner(
        self,
        channel: str,
        user_input: str,
        metadata: dict | None = None,
        plan: ExecutionPlan | None = None,
    ) -> bool:
        metadata = dict(metadata or {})
        if metadata.get("attachment_documents") or metadata.get("attachment_count"):
            return False
        if metadata.get("prefer_intent_resolver") is True or metadata.get("route_intent_first") is True:
            return bool(user_input.strip())
        normalized_channel = channel.strip().lower()
        if (
            normalized_channel in {"desktop", "desktop_console", "wpf"}
            and plan is not None
            and plan.route in {"builtin", "development_plan", "tool", "executor", "clarify_openclaw"}
        ):
            return False
        if self._should_run_voice_first(normalized_channel, user_input):
            return True
        if normalized_channel in {"voice", "asr"}:
            return False
        allowed_channels = {
            item.strip().lower()
            for item in os.getenv("SPIRITKIN_INTENT_FIRST_CHANNELS", "mobile,web,desktop").split(",")
            if item.strip()
        }
        if normalized_channel not in allowed_channels:
            return False
        return looks_like_action_request(user_input) or self.looks_like_noisy_openclaw_voice(user_input)

    @staticmethod
    def has_required_params(request: ExecutionRequest) -> bool:
        required_by_operation = {
            "launch_app": ("app_name",),
            "close_app": ("app_name",),
            "browser_open_url": ("url",),
            "browser_search": ("query",),
            "window_activate": ("title",),
            "window_close": ("title",),
            "clipboard_write": ("text",),
            "file_read": ("path",),
            "file_open": ("path",),
            "file_write": ("path", "text"),
            "file_save_as": ("path", "text"),
            "file_search": ("query",),
            "enter_text": ("text",),
            "press_keys": ("keys",),
            "send_message": ("recipient", "text"),
        }
        required = required_by_operation.get(request.operation)
        if not required:
            return True
        params = dict(request.params or {})
        return all(str(params.get(key) or "").strip() for key in required)

    @staticmethod
    def looks_like_noisy_openclaw_voice(user_input: str) -> bool:
        normalized = Planner._normalize_openclaw_asr_text(user_input.strip().lower())
        compact = Planner._compact_voice_text(normalized)
        hints = ("机械臂", "機械臂", "机械b", "機械b", "机械", "機械", "夹爪", "夾爪", "openclaw", "机械手", "機械手")
        return any(hint.lower() in normalized for hint in hints) or "机械b" in compact or "機械b" in compact

    def _should_run_voice_first(self, channel: str, user_input: str) -> bool:
        if channel != "voice":
            return False
        mode = self._services.voice_intent_mode
        if mode in {"", "off", "false", "0", "fallback", "rule_first"}:
            return False
        if mode in {"always", "all"}:
            return bool(user_input.strip())
        if mode in {"first", "voice_first", "llm_first", "agent_first"}:
            return looks_like_action_request(user_input) or self.looks_like_noisy_openclaw_voice(user_input)
        return False

    def _note_cache_outcome(
        self,
        reply: AgentReply,
        fingerprint: str,
        request: ExecutionRequest,
        context: AgentContext,
        channel: str,
    ) -> None:
        metadata = dict(reply.metadata or {})
        if str(metadata.get("response_kind") or "") == "confirmation_request":
            return
        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        if bool(execution.get("success")):
            self._services.decision_cache.record_success(
                fingerprint,
                target=request.target,
                operation=request.operation,
                params=dict(request.params or {}),
                user_input=context.user_input,
                channel=channel,
                agent="intent",
            )
        else:
            self._services.decision_cache.record_failure(fingerprint)
