from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

EMOTION_PATTERN = re.compile(r"<emotion:([a-zA-Z0-9_\-]+)(?:\|([a-zA-Z0-9_\-]+))?>")
ACTION_PATTERN = re.compile(r"<action:([a-zA-Z0-9_\-]+)>")

SUPPORTED_EMOTIONS = {
    "neutral",
    "happy",
    "thinking",
    "confused",
    "speechless",
    "waiting",
    "alert",
    "error",
    "listening",
    "surprised",
    "sad",
}

SUPPORTED_ACTIONS = {
    "idle",
    "nod",
    "shake",
    "wave",
    "wave_hand",
    "walk",
    "listen",
    "speak",
    "tap_chin",
    "tilt_head",
    "scan_screen",
    "glance_clock",
    "write_on_board",
    "await_confirmation",
    "execute_task",
    "cancel_execution",
    "plan_development",
    "write_plan",
    "queue_task",
}

EMOTION_ALIASES = {
    "normal": "neutral",
    "calm": "neutral",
    "joy": "happy",
    "smile": "happy",
    "smiling": "happy",
    "think": "thinking",
    "thoughtful": "thinking",
    "pondering": "thinking",
    "unsure": "confused",
    "question": "confused",
    "warning": "alert",
    "warn": "alert",
    "fail": "error",
    "failed": "error",
    "surprise": "surprised",
    "surprised": "surprised",
    "sad": "sad",
    "blank": "speechless",
    "deadpan": "speechless",
    "speechless": "speechless",
    "unamused": "speechless",
}

ACTION_ALIASES = {
    "none": "idle",
    "no_action": "idle",
    "wavehand": "wave_hand",
    "wave_hi": "wave_hand",
    "hello": "wave_hand",
    "greet": "wave_hand",
    "yes": "nod",
    "agree": "nod",
    "confirm": "nod",
    "no": "shake",
    "deny": "shake",
    "negative": "shake",
    "think": "tap_chin",
    "thinking": "tap_chin",
}

_EMOTION_TOKENS = {*SUPPORTED_EMOTIONS, *EMOTION_ALIASES}
_ACTION_TOKENS = {*SUPPORTED_ACTIONS, *ACTION_ALIASES}
_BARE_TAG_ALTERNATION = "|".join(
    re.escape(token) for token in sorted(_EMOTION_TOKENS | _ACTION_TOKENS, key=len, reverse=True)
)
# Models often emit the shorthand <happy|wave_hand> instead of the prompted
# <emotion:...><action:...> form; only known vocabulary is matched so real
# angle-bracket content in replies is never stripped.
BARE_TAG_PATTERN = re.compile(
    rf"<\s*(?P<first>{_BARE_TAG_ALTERNATION})\s*(?:[|,/]\s*(?P<second>{_BARE_TAG_ALTERNATION})\s*)?>",
    re.IGNORECASE,
)


def strip_avatar_tags(text: str) -> str:
    return BARE_TAG_PATTERN.sub("", ACTION_PATTERN.sub("", EMOTION_PATTERN.sub("", text or ""))).strip()


@dataclass
class AgentContext:
    user_input: str
    visual_context: str = ""
    device_name: str = "local_pc"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def combined_input(self) -> str:
        if self.visual_context:
            return f"[视觉提示：{self.visual_context}] {self.user_input}"
        return self.user_input

    @property
    def session_summary(self) -> str:
        return str(self.metadata.get("session_summary", "")).strip()

    @property
    def recent_history(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("recent_history", []) or [])

    @property
    def knowledge_hits(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("knowledge_hits", []) or [])

    @property
    def knowledge_context(self) -> str:
        lines = []
        for index, hit in enumerate(self.knowledge_hits[:3], start=1):
            title = str(hit.get("source_title") or hit.get("document_id") or f"知识片段{index}").strip()
            text = str(hit.get("text", "")).strip()
            if text:
                lines.append(f"[{index}] {title}: {text}")
        if not lines:
            return ""
        return "知识检索结果：\n" + "\n".join(lines)

    @property
    def inventory_context(self) -> str:
        return str(self.metadata.get("inventory_context", "")).strip()

    @property
    def prompt_context(self) -> str:
        parts = []
        if self.session_summary:
            parts.append(f"会话摘要：{self.session_summary}")

        if self.recent_history:
            history_lines = []
            for item in self.recent_history:
                role = "用户" if item.get("role") == "user" else "助手"
                content = str(item.get("content", "")).strip()
                if content:
                    history_lines.append(f"{role}：{content}")
            if history_lines:
                parts.append("最近对话：\n" + "\n".join(history_lines))

        if self.knowledge_context:
            parts.append(self.knowledge_context)

        if self.inventory_context:
            parts.append(self.inventory_context)

        parts.append(f"当前输入：{self.combined_input}")
        return "\n\n".join(parts)


@dataclass
class AgentReply:
    text: str
    emotion: str = "neutral"
    action: str = "idle"
    agent_name: str = "general"
    spoken_text: str | None = None
    requires_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    name = "base"
    domain = "general"
    routing_priority = 0
    resource_profile = "gpu_heavy"

    @abstractmethod
    def can_handle(self, context: AgentContext) -> bool:
        raise NotImplementedError

    @abstractmethod
    def handle(self, context: AgentContext) -> AgentReply:
        raise NotImplementedError

    def match_score(self, context: AgentContext) -> int:
        return 1 if self.can_handle(context) else 0


def parse_tagged_response(raw: str, default_emotion: str = "neutral") -> tuple[str, str]:
    text, emotion, _ = parse_emotion_action_response(raw, default_emotion=default_emotion)
    return text, emotion


def parse_emotion_action_response(
    raw: str,
    default_emotion: str = "neutral",
    default_action: str = "idle",
) -> tuple[str, str, str]:
    payload = _parse_json_response(raw)
    if payload is not None:
        text = str(payload.get("text") or payload.get("reply") or payload.get("message") or "").strip()
        emotion = _normalize_emotion(payload.get("emotion"), default_emotion)
        action = _normalize_action(payload.get("action") or payload.get("motion"), default_action)
        return text or "我暂时还没有整理好回答。", emotion, action

    source = raw or ""
    emotion_match = EMOTION_PATTERN.search(source)
    action_match = ACTION_PATTERN.search(source)
    bare_emotion, bare_action = _classify_bare_tokens(BARE_TAG_PATTERN.search(source))
    emotion_value = emotion_match.group(1) if emotion_match else bare_emotion
    action_value = (
        action_match.group(1)
        if action_match
        else (emotion_match.group(2) if emotion_match and emotion_match.group(2) else bare_action)
    )
    emotion = _normalize_emotion(emotion_value, default_emotion)
    action = _normalize_action(action_value, default_action)
    text = strip_avatar_tags(source)
    return text or "我暂时还没有整理好回答。", emotion, action


def _classify_bare_tokens(match: re.Match | None) -> tuple[str | None, str | None]:
    if match is None:
        return None, None
    first, second = match.group("first"), match.group("second")

    def norm(token: str | None) -> str:
        return str(token or "").strip().lower().replace("-", "_")

    def is_emotion(token: str | None) -> bool:
        return norm(token) in _EMOTION_TOKENS

    def is_action(token: str | None) -> bool:
        return norm(token) in _ACTION_TOKENS

    if second:
        emotion = first if is_emotion(first) else (second if is_emotion(second) else None)
        action = second if is_action(second) else (first if is_action(first) else None)
        return emotion, action
    if is_action(first) and not is_emotion(first):
        return None, first
    if is_emotion(first):
        return first, None
    return None, first if is_action(first) else None


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    source = (raw or "").strip()
    if not source:
        return None
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*", "", source, flags=re.IGNORECASE)
        source = re.sub(r"\s*```$", "", source).strip()
    if not (source.startswith("{") and source.endswith("}")):
        return None
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_emotion(value: object, default: str = "neutral") -> str:
    key = str(value or default or "neutral").strip().lower().replace("-", "_")
    key = EMOTION_ALIASES.get(key, key)
    return key if key in SUPPORTED_EMOTIONS else "neutral"


def _normalize_action(value: object, default: str = "idle") -> str:
    raw_value = value if str(value or "").strip() else default
    if not str(raw_value or "").strip() and default == "":
        return ""
    key = str(raw_value or "idle").strip().lower().replace("-", "_")
    key = ACTION_ALIASES.get(key, key)
    return key if key in SUPPORTED_ACTIONS else "idle"
