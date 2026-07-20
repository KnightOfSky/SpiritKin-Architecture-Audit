"""Low-latency conversational path for the authenticated iOS controller."""

from __future__ import annotations

from typing import Any

from backend.agents.base import AgentReply
from backend.orchestrator.prompt_context import looks_like_action_request
from backend.services.conversation_engine import get_llm_response

_RUNTIME_HINTS = (
    "/",
    "terminal",
    "workflow",
    "skill",
    "resource",
    "终端",
    "工作流",
    "技能",
    "资源",
    "设备",
    "文件",
    "目录",
    "项目",
    "运行时",
    "状态",
    "监控",
    "诊断",
    "修复",
    "创建",
    "删除",
    "修改",
    "执行",
    "安装",
    "发布",
    "上架",
    "桌面端",
    "remote worker",
)


def _bounded_number(value: object, default: float, *, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


def _recent_session_context(text: str, metadata: dict[str, Any]) -> str:
    session_id = str(metadata.get("session_id") or "").strip()
    workspace_id = str(metadata.get("workspace_id") or "").strip()
    if not session_id or not workspace_id:
        return ""
    try:
        from backend.mobile.ios_sessions import ios_sessions_snapshot

        sessions = ios_sessions_snapshot(workspace_id=workspace_id).get("sessions") or []
    except Exception:
        return ""
    session = next((item for item in sessions if str(item.get("id") or "") == session_id), None)
    if not isinstance(session, dict):
        return ""
    messages = [item for item in session.get("messages") or [] if isinstance(item, dict)]
    if messages and str(messages[-1].get("role") or "") == "user" and str(messages[-1].get("text") or "").strip() == text:
        messages = messages[:-1]
    lines: list[str] = []
    used = 0
    for message in reversed(messages[-6:]):
        content = str(message.get("text") or "").strip().replace("\r", " ").replace("\n", " ")[:240]
        if not content:
            continue
        role = "用户" if str(message.get("role") or "").lower() == "user" else "助手"
        line = f"{role}：{content}"
        if used + len(line) > 600:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(reversed(lines))


def should_use_ios_direct_chat(text: str, metadata: dict[str, Any] | None = None) -> bool:
    """Return true only when the message cannot cause a desktop-side action."""

    metadata = metadata or {}
    if metadata.get("full_runtime") is True or metadata.get("route_full_runtime") is True:
        return False
    normalized = str(text or "").strip().lower().replace(" ", "")
    if not normalized or looks_like_action_request(normalized):
        return False
    return not any(hint.replace(" ", "") in normalized for hint in _RUNTIME_HINTS)


def handle_ios_direct_chat(text: str, metadata: dict[str, Any] | None = None) -> AgentReply:
    """Answer a pure chat message without invoking planners, agents, or tools."""

    metadata = dict(metadata or {})
    user_text = str(text or "").strip()
    if not user_text:
        raise ValueError("missing text")
    recent_context = _recent_session_context(user_text, metadata)
    context_block = f"最近对话：\n{recent_context}\n" if recent_context else ""
    prompt = (
        "你是 SpiritKin 的 iOS 对话助手。直接回答用户，不调用工具，不声称已经执行操作。"
        "使用用户的语言，内容清楚、简洁。\n"
        f"{context_block}"
        f"用户：{user_text[:1200]}\n助手："
    )
    answer = get_llm_response(
        prompt,
        mode="fast",
        max_new_tokens=int(_bounded_number(metadata.get("max_new_tokens"), 96, minimum=16, maximum=128)),
        reasoning_effort="none",
        request_timeout=_bounded_number(metadata.get("model_timeout_seconds"), 45, minimum=5, maximum=90),
    ).strip()
    return AgentReply(
        text=answer or "我暂时没有生成有效回复，请重试。",
        emotion="neutral",
        action="idle",
        agent_name="main_text",
        spoken_text=answer,
        metadata={
            "response_kind": "message",
            "input_channel": "ios",
            "workspace_id": str(metadata.get("workspace_id") or ""),
            "ios_direct_chat": True,
        },
    )
