from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

OPENING_BUBBLE_KINDS = frozenset({"safety", "recovery", "task", "care", "greeting"})
OPENING_BUBBLE_PRIORITIES = {
    "safety": 500,
    "recovery": 400,
    "task": 300,
    "care": 200,
    "greeting": 100,
}


@dataclass(frozen=True)
class OpeningBubbleCandidate:
    kind: str
    text: str
    action_prompt: str = ""
    source_id: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    motion_policy: str = "subtle"
    emotion: str = "neutral"
    action_hint: str = "nod"
    essential: bool = False

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().lower()
        if kind not in OPENING_BUBBLE_KINDS:
            raise ValueError(f"unsupported opening bubble kind: {self.kind}")
        text = " ".join(str(self.text or "").split())[:280]
        if not text:
            raise ValueError("opening bubble requires text")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "action_prompt", " ".join(str(self.action_prompt or "").split())[:500])
        object.__setattr__(self, "source_id", str(self.source_id or "").strip()[:120])

    @property
    def priority(self) -> int:
        return OPENING_BUBBLE_PRIORITIES[self.kind]

    @property
    def bubble_id(self) -> str:
        identity = self.source_id or f"{self.kind}:{self.text}"
        digest = hashlib.blake2s(identity.encode("utf-8"), digest_size=8).hexdigest()
        return f"bubble-{self.kind}-{digest}"


class OpeningBubbleService:
    def __init__(self, *, dedupe_seconds: float = 6 * 60 * 60):
        self.dedupe_seconds = max(0.0, float(dedupe_seconds))
        self._presented_at: dict[str, float] = {}

    def restore(self, events: Iterable[dict[str, Any]]) -> None:
        for event in events:
            event_type = str(event.get("event_type") or event.get("type") or "")
            if event_type != "opening_bubble.present":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            bubble_id = str(payload.get("bubble_id") or "")
            timestamp = float(payload.get("created_at") or payload.get("timestamp") or event.get("timestamp") or 0.0)
            if bubble_id and timestamp:
                self._presented_at[bubble_id] = max(self._presented_at.get(bubble_id, 0.0), timestamp)

    def startup_event(
        self,
        *,
        agent: Any,
        relationship: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, object] | None:
        current = time.time() if now is None else float(now)
        candidates = self._startup_candidates(agent, current)
        relationship_snapshot = dict(relationship or {})
        proactive_off = str(
            (relationship_snapshot.get("care_strategy") or {}).get("proactive_level")
            if isinstance(relationship_snapshot.get("care_strategy"), dict)
            else ""
        ).lower() == "off"
        if proactive_off:
            candidates = [candidate for candidate in candidates if candidate.kind in {"safety", "recovery"}]
        return self.select_event(candidates, now=current)

    def from_proactive_event(self, event: dict[str, object], *, now: float | None = None) -> dict[str, object] | None:
        if event.get("type") != "proactive.suggested":
            return None
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        current = time.time() if now is None else float(now)
        signal_kind = str(payload.get("signal_kind") or "")
        kind = "recovery" if signal_kind == "task_failed" else "task" if signal_kind in {"task_completed", "task_context", "calendar_due", "device_anomaly"} else "care"
        candidate = OpeningBubbleCandidate(
            kind=kind,
            text=str(payload.get("text") or ""),
            action_prompt=str(payload.get("action_prompt") or ""),
            source_id=str(payload.get("signal_id") or ""),
            created_at=float(payload.get("created_at") or current),
            expires_at=float(payload.get("expires_at") or current + 10 * 60),
            motion_policy="subtle",
            emotion="thinking" if kind in {"task", "recovery"} else "neutral",
            action_hint="nod",
            essential=False,
        )
        return self.select_event([candidate], now=current)

    def select_event(
        self,
        candidates: Iterable[OpeningBubbleCandidate],
        *,
        now: float | None = None,
    ) -> dict[str, object] | None:
        current = time.time() if now is None else float(now)
        eligible = [
            candidate
            for candidate in candidates
            if (not candidate.expires_at or candidate.expires_at > current)
            and not self._recently_presented(candidate.bubble_id, current)
        ]
        if not eligible:
            return None
        selected = max(eligible, key=lambda candidate: (candidate.priority, candidate.created_at))
        self._presented_at[selected.bubble_id] = current
        return self._event(selected, current)

    def _recently_presented(self, bubble_id: str, now: float) -> bool:
        presented_at = self._presented_at.get(bubble_id, 0.0)
        return bool(presented_at and now - presented_at < self.dedupe_seconds)

    @staticmethod
    def _startup_candidates(agent: Any, now: float) -> list[OpeningBubbleCandidate]:
        candidates: list[OpeningBubbleCandidate] = []
        pending = getattr(agent, "pending_execution", None)
        request = getattr(pending, "request", None)
        if pending is not None:
            target = str(getattr(request, "target", "") or "操作")
            operation = str(getattr(request, "operation", "") or "待确认")
            candidates.append(
                OpeningBubbleCandidate(
                    kind="safety",
                    text=f"{target}.{operation} 正在等待你确认。",
                    action_prompt="请显示当前等待确认的操作和风险，不要直接执行。",
                    source_id=f"pending:{target}:{operation}",
                    created_at=now,
                    expires_at=now + 30 * 60,
                    emotion="waiting",
                    action_hint="nod",
                    essential=True,
                )
            )

        tasks = getattr(agent, "task_queue_snapshot", []) or []
        if isinstance(tasks, list):
            unfinished = [item for item in tasks if isinstance(item, dict) and str(item.get("status") or "") not in {"complete", "failed"}]
            if unfinished:
                task = unfinished[0]
                request_text = " ".join(str(task.get("request") or "未完成任务").split())[:120]
                task_id = str(task.get("task_id") or request_text)
                candidates.append(
                    OpeningBubbleCandidate(
                        kind="recovery",
                        text=f"上次的任务还在：{request_text}",
                        action_prompt=f"请恢复并说明这个任务的当前状态：{request_text}",
                        source_id=f"task:{task_id}",
                        created_at=now,
                        expires_at=now + 60 * 60,
                        emotion="thinking",
                        action_hint="nod",
                        essential=True,
                    )
                )
        return candidates

    @staticmethod
    def _event(candidate: OpeningBubbleCandidate, now: float) -> dict[str, object]:
        action = {
            "type": "open_conversation",
            "label": "打开对话",
            "prompt": candidate.action_prompt,
            "source_id": candidate.source_id,
        }
        return {
            "type": "opening_bubble.present",
            "schema_version": "v1",
            "payload": {
                "bubble_id": candidate.bubble_id,
                "kind": candidate.kind,
                "priority": candidate.priority,
                "text": candidate.text,
                "action": action,
                "created_at": now,
                "expires_at": candidate.expires_at or now + 10 * 60,
                "motion_policy": candidate.motion_policy,
                "emotion": candidate.emotion,
                "action_hint": candidate.action_hint,
                "essential": candidate.essential,
                "requires_confirmation": True,
            },
        }
