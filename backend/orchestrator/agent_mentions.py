from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MENTION_PATTERN = re.compile(r"(?<![\w./-])@(?P<name>[\w\u4e00-\u9fff][\w.\-\u4e00-\u9fff]{0,63})")


@dataclass(frozen=True)
class AgentMention:
    raw: str
    agent_id: str
    label: str
    text_without_mention: str
    intent: str = "route"

    def snapshot(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "agent_id": self.agent_id,
            "label": self.label,
            "text_without_mention": self.text_without_mention,
            "intent": self.intent,
        }


def parse_agent_mention(text: str, agents: list[dict[str, Any]] | dict[str, dict[str, Any]]) -> AgentMention | None:
    source = text or ""
    match = MENTION_PATTERN.search(source)
    if match is None:
        return None
    raw = match.group("name").strip()
    agent_records = _agent_records(agents)
    target = _resolve_agent(raw, agent_records)
    if target is None:
        return None
    text_without = (source[: match.start()] + source[match.end() :]).strip()
    text_without = re.sub(r"\s+", " ", text_without).strip()
    agent_id = str(target.get("agent_id") or target.get("id") or "").strip()
    label = str(target.get("label") or agent_id).strip()
    return AgentMention(raw=raw, agent_id=agent_id, label=label, text_without_mention=text_without, intent=infer_agent_mention_intent(text_without))


def infer_agent_mention_intent(text: str) -> str:
    normalized = (text or "").strip().lower()
    status_keywords = (
        "状态",
        "工作情况",
        "进度",
        "在干什么",
        "忙什么",
        "当前任务",
        "任务情况",
        "队列",
        "status",
        "progress",
        "what are you doing",
    )
    chat_keywords = ("聊", "聊天", "问一下", "对话", "chat")
    if any(keyword in normalized for keyword in status_keywords):
        return "status"
    if any(keyword in normalized for keyword in chat_keywords):
        return "chat"
    return "route"


def _agent_records(agents: list[dict[str, Any]] | dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(agents, dict):
        return [dict(value, agent_id=str(key)) if "agent_id" not in value else dict(value) for key, value in agents.items() if isinstance(value, dict)]
    return [dict(item) for item in agents if isinstance(item, dict)]


def _resolve_agent(raw: str, agents: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = _normalize(raw)
    if not needle:
        return None
    aliases = {
        "主agent": "main_text",
        "主": "main_text",
        "main": "main_text",
        "mainagent": "main_text",
        "编程": "programming",
        "代码": "programming",
        "code": "programming",
        "coding": "programming",
        "视觉": "vision_model",
        "vision": "vision_model",
        "视频": "video_animation",
        "动画": "video_animation",
        "video": "video_animation",
        "game": "game_development",
        "游戏": "game_development",
        "电商": "ecommerce",
        "commerce": "ecommerce",
        "skill": "skill_runner",
        "skills": "skill_runner",
        "reviewer": "external_reviewer",
        "评审": "external_reviewer",
    }
    alias_target = aliases.get(needle)
    for agent in agents:
        agent_id = str(agent.get("agent_id") or agent.get("id") or "").strip()
        label = str(agent.get("label") or "").strip()
        if not agent_id:
            continue
        values = {
            _normalize(agent_id),
            _normalize(label),
            _normalize(agent_id.replace("_", "")),
            _normalize(label.replace(" Agent", "").replace("Agent", "")),
        }
        if needle in values or (alias_target and agent_id == alias_target):
            return agent
    return None


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-·:：]+", "", (value or "").strip().lower())
