from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.proactive.policy import ProactiveDecision, ProactiveHistory, ProactivePolicy, evaluate
from backend.proactive.signals import ProactiveSignal

PROACTIVE_INTENTS = frozenset({"inform", "check_in", "offer_action", "reminder"})
PROACTIVE_FEEDBACK = frozenset({"accepted", "dismissed", "helpful", "ignored", "not_helpful"})


@dataclass(frozen=True)
class ProactiveSuggestion:
    signal_id: str
    intent: str
    text: str
    action_prompt: str
    reason_code: str
    relationship_stage: str
    requires_confirmation: bool = True
    suggestion_id: str = field(default_factory=lambda: f"suggestion-{uuid.uuid4().hex}")
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "signal_id": self.signal_id,
            "intent": self.intent,
            "text": self.text,
            "action_prompt": self.action_prompt,
            "reason_code": self.reason_code,
            "relationship_stage": self.relationship_stage,
            "requires_confirmation": self.requires_confirmation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class ProactiveService:
    """Policy and suggestion state only. This class intentionally owns no execution dependency."""

    def __init__(self, policy: ProactivePolicy | None = None):
        self.policy = policy or ProactivePolicy.from_env()
        self._lock = threading.RLock()
        self._suggested_at: list[float] = []
        self._cooldown_until = 0.0
        self._feedback_cooldown_until = 0.0
        self._dismissed_signal_ids: set[str] = set()
        self._suggestions: dict[str, ProactiveSuggestion] = {}

    def history(self) -> ProactiveHistory:
        return ProactiveHistory(
            suggested_at=tuple(self._suggested_at),
            cooldown_until=self._cooldown_until,
            feedback_cooldown_until=self._feedback_cooldown_until,
            dismissed_signal_ids=frozenset(self._dismissed_signal_ids),
        )

    def evaluate_signal(
        self,
        signal: ProactiveSignal,
        *,
        relationship: dict[str, Any] | None = None,
        presence: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> tuple[ProactiveDecision, ProactiveSuggestion | None, dict[str, object]]:
        current = time.time() if now is None else float(now)
        relationship_snapshot = dict(relationship or {})
        presence_snapshot = dict(presence or {})
        with self._lock:
            decision = evaluate(
                signal,
                relationship_snapshot,
                presence_snapshot,
                self.policy,
                self.history(),
                now=current,
            )
            stage = str(relationship_snapshot.get("stage") or "new")
            if not decision.allowed:
                return decision, None, self._event(
                    "proactive.suppressed",
                    signal=signal,
                    decision=decision,
                    relationship_stage=stage,
                    timestamp=current,
                )

            suggestion = self._build_suggestion(signal, decision, stage, current)
            self._suggested_at.append(current)
            self._suggested_at = self._suggested_at[-200:]
            self._cooldown_until = max(self._cooldown_until, decision.cooldown_until)
            self._suggestions[signal.signal_id] = suggestion
            return decision, suggestion, self._event(
                "proactive.suggested",
                signal=signal,
                decision=decision,
                relationship_stage=stage,
                timestamp=current,
                suggestion=suggestion,
            )

    def record_feedback(
        self,
        signal_id: str,
        feedback: str,
        *,
        relationship_stage: str = "new",
        now: float | None = None,
    ) -> dict[str, object]:
        normalized = str(feedback or "").strip().lower()
        if normalized not in PROACTIVE_FEEDBACK:
            raise ValueError(f"unsupported proactive feedback: {feedback}")
        current = time.time() if now is None else float(now)
        with self._lock:
            if normalized in {"dismissed", "ignored", "not_helpful"}:
                self._dismissed_signal_ids.add(signal_id)
                multiplier = {"dismissed": 3.0, "ignored": 2.0, "not_helpful": 6.0}[normalized]
                self._feedback_cooldown_until = max(
                    self._feedback_cooldown_until,
                    current + self.policy.cooldown_seconds * multiplier,
                )
            suggestion = self._suggestions.get(signal_id)
            payload = {
                "signal_id": signal_id,
                "suggestion_id": suggestion.suggestion_id if suggestion is not None else "",
                "feedback": normalized,
                "reason_code": f"feedback_{normalized}",
                "relationship_stage": relationship_stage,
                "requires_confirmation": True,
                "cooldown_until": max(self._cooldown_until, self._feedback_cooldown_until),
                "timestamp": current,
            }
            return {"type": "proactive.feedback", "schema_version": "v1", "payload": payload}

    def suggestion_for(self, signal_id: str) -> ProactiveSuggestion | None:
        with self._lock:
            return self._suggestions.get(signal_id)

    def restore(self, events: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            for event in events:
                event_type = str(event.get("event_type") or event.get("type") or "")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                timestamp = float(payload.get("timestamp") or event.get("timestamp") or 0.0)
                if event_type == "proactive.suggested" and timestamp:
                    self._suggested_at.append(timestamp)
                    self._cooldown_until = max(self._cooldown_until, float(payload.get("cooldown_until") or 0.0))
                if event_type == "proactive.feedback":
                    feedback = str(payload.get("feedback") or "")
                    signal_id = str(payload.get("signal_id") or "")
                    if feedback in {"dismissed", "ignored", "not_helpful"} and signal_id:
                        self._dismissed_signal_ids.add(signal_id)
                    self._feedback_cooldown_until = max(
                        self._feedback_cooldown_until,
                        float(payload.get("cooldown_until") or 0.0),
                    )
            self._suggested_at = self._suggested_at[-200:]

    @staticmethod
    def _intent_for(signal: ProactiveSignal) -> str:
        return {
            "calendar_due": "reminder",
            "device_anomaly": "offer_action",
            "idle": "check_in",
            "relationship_change": "inform",
            "task_completed": "inform",
            "task_context": "offer_action",
            "task_failed": "offer_action",
        }[signal.kind]

    @classmethod
    def _build_suggestion(
        cls,
        signal: ProactiveSignal,
        decision: ProactiveDecision,
        relationship_stage: str,
        now: float,
    ) -> ProactiveSuggestion:
        intent = cls._intent_for(signal)
        if intent not in PROACTIVE_INTENTS:
            raise ValueError(f"unsupported proactive intent: {intent}")
        configured_text = " ".join(str(signal.metadata.get("suggestion_text") or "").split())
        text = configured_text or cls._default_text(signal)
        configured_prompt = " ".join(str(signal.metadata.get("action_prompt") or "").split())
        action_prompt = configured_prompt or cls._default_action_prompt(signal)
        return ProactiveSuggestion(
            signal_id=signal.signal_id,
            intent=intent,
            text=text[:280],
            action_prompt=action_prompt[:500],
            reason_code=decision.reason_code,
            relationship_stage=relationship_stage,
            requires_confirmation=decision.requires_confirmation,
            created_at=now,
            expires_at=signal.expires_at,
        )

    @staticmethod
    def _default_text(signal: ProactiveSignal) -> str:
        if signal.kind == "task_completed":
            return f"{signal.summary} 已完成。需要我帮你整理结果吗？"
        if signal.kind == "task_failed":
            return f"{signal.summary} 遇到问题。需要我帮你检查失败原因吗？"
        if signal.kind == "calendar_due":
            return f"提醒：{signal.summary}"
        if signal.kind == "device_anomaly":
            return f"检测到设备状态变化：{signal.summary}。需要我帮你检查吗？"
        if signal.kind == "idle":
            return signal.summary
        return f"我注意到你还在处理：{signal.summary}。需要我帮你拆下一步吗？"

    @staticmethod
    def _default_action_prompt(signal: ProactiveSignal) -> str:
        if signal.kind == "task_completed":
            return f"请总结任务结果：{signal.summary}"
        if signal.kind == "task_failed":
            return f"请分析任务失败并给出下一步建议：{signal.summary}"
        if signal.kind == "device_anomaly":
            return f"请检查设备异常并先说明计划，不要直接执行：{signal.summary}"
        return f"请协助处理：{signal.summary}"

    @staticmethod
    def _event(
        event_type: str,
        *,
        signal: ProactiveSignal,
        decision: ProactiveDecision,
        relationship_stage: str,
        timestamp: float,
        suggestion: ProactiveSuggestion | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "signal_id": signal.signal_id,
            "signal_kind": signal.kind,
            "source": signal.source,
            "reason_code": decision.reason_code,
            "relationship_stage": relationship_stage,
            "requires_confirmation": decision.requires_confirmation,
            "cooldown_until": decision.cooldown_until,
            "timestamp": timestamp,
        }
        if suggestion is not None:
            payload.update(suggestion.snapshot())
        return {"type": event_type, "schema_version": "v1", "payload": payload}
