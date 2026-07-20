from __future__ import annotations

from collections.abc import Callable, Mapping

from backend.agents.base import AgentContext, AgentReply, parse_emotion_action_response
from backend.expression.local_expression_driver import infer_emotion_action, local_expression_enabled
from backend.orchestrator.prompt_context import (
    build_attachment_context,
    build_goal_context,
    build_long_term_memory_context,
    build_plan_mode_steps,
    build_relationship_context,
    format_plan_mode_text,
    goal_metadata,
)
from backend.orchestrator.text_utils import clean_spoken_text, format_current_time_context

ACTION_MAP = {
    "happy": "wave_hand",
    "thinking": "tap_chin",
    "confused": "tilt_head",
    "speechless": "shake",
    "waiting": "await_confirmation",
    "alert": "nod",
    "error": "shake",
    "listening": "listen",
    "surprised": "idle",
    "sad": "idle",
    "neutral": "idle",
}

EMOTION_ACTION_CONTRACT = (
    "Visible reply must be Chinese and must not contain raw metadata, emoji, emoticons, stickers, image captions, or markdown image syntax. After the visible reply, append non-spoken avatar metadata tags exactly as "
    "<emotion:neutral|happy|thinking|confused|speechless|waiting|alert|error|surprised|sad><action:idle|nod|shake|wave_hand|walk>. "
    "Choose emotion/action from the reply meaning: greetings use wave_hand, agreement or positive acknowledgement uses nod, negative or impossible answers use shake, thinking uses tap_chin if available or idle. "
    "speechless means a deadpan or wordless reaction, especially for obvious impossible/negative answers; use idle when no body motion is needed. "
    "Never say the metadata tags aloud, never explain them, and never put them inside the visible sentence."
)


class SoulResponsePhase:
    """Generate the personality response without access to tool definitions."""

    def __init__(self, llm_call: Callable[..., str]) -> None:
        self._llm_call = llm_call

    def respond(self, context: AgentContext, *, inventory: Mapping[str, object] | None = None) -> AgentReply:
        time_context = format_current_time_context(context.metadata)
        inventory_context = self._inventory_context(inventory or {})
        knowledge = self._hit_context("知识检索结果", context.metadata.get("knowledge_hits"))
        web_context = self._hit_context("联网搜索结果", context.metadata.get("web_search_hits"))
        memory_context = build_long_term_memory_context(context.metadata)
        relationship_context = build_relationship_context(context.metadata)
        attachment_context = build_attachment_context(context.metadata)
        history = self._history_context(context)
        visual = f"[视觉提示：{context.visual_context}] " if context.visual_context else ""
        user_input = f"{visual}{context.user_input}"
        is_command = any(
            keyword in context.user_input
            for keyword in ("打开", "关闭", "搜索", "截图", "拍照", "扫描", "列出", "启动", "运行", "切换")
        )
        if is_command:
            prompt = (
                "You are a PC assistant. User wants to execute a command. "
                "Visible reply text must be Chinese, 1 SHORT sentence. Confirm the action you're about to take. "
                + EMOTION_ACTION_CONTRACT
                + " "
                + time_context
                + inventory_context
                + knowledge
                + web_context
                + memory_context
                + relationship_context
                + attachment_context
                + history
                + f"User said: {user_input} Reply:"
            )
        else:
            prompt = (
                "You are Spirit, a friendly Chinese voice assistant. "
                "Visible reply text must be Chinese. 1-3 short sentences. Be warm and conversational. "
                + EMOTION_ACTION_CONTRACT
                + " "
                + time_context
                + knowledge
                + web_context
                + memory_context
                + relationship_context
                + attachment_context
                + history
                + f"User said: {user_input} Reply:"
            )
        raw = self._llm_call(prompt, agent_name="main_text")
        text, emotion, action = parse_emotion_action_response(raw, default_emotion="neutral", default_action="")
        if len(text) > 300:
            text = text[:280] + "..."
        if emotion == "neutral" and local_expression_enabled():
            emotion, action = infer_emotion_action(text, default_emotion=emotion, default_action=action)
        action = action or ACTION_MAP.get(emotion, "idle")
        return AgentReply(
            text=text,
            spoken_text=clean_spoken_text(text),
            emotion=emotion,
            action=action,
            agent_name="general",
        )

    def plan(self, context: AgentContext) -> AgentReply:
        goal_context = build_goal_context(context.metadata)
        relationship_context = build_relationship_context(context.metadata)
        attachment_context = build_attachment_context(context.metadata)
        user_input = context.user_input.strip()
        steps = build_plan_mode_steps(user_input)
        prompt = (
            "You are SpiritKin planning mode. The user explicitly requested planning only. "
            "Do not execute tools, do not operate the PC, do not claim that an action was performed. "
            "Visible reply text must be Chinese. Return a concise actionable plan with 3-6 numbered steps, "
            "important risks, and the next confirmation needed before execution. "
            + EMOTION_ACTION_CONTRACT
            + " "
            + format_current_time_context(context.metadata)
            + goal_context
            + relationship_context
            + attachment_context
            + f"User said: {context.combined_input} Reply:"
        )
        raw = self._llm_call(prompt, agent_name="main_text")
        text, emotion, action = parse_emotion_action_response(
            raw,
            default_emotion="thinking",
            default_action="write_plan",
        )
        if not text.strip():
            text = format_plan_mode_text(steps)
        return AgentReply(
            text=text[:900] if len(text) > 900 else text,
            emotion=emotion or "thinking",
            action=action or "write_plan",
            agent_name="plan_mode",
            metadata={
                "response_kind": "plan_mode",
                "plan_mode": True,
                "execution_blocked": True,
                "plan": {
                    "title": user_input[:80] or "Planning request",
                    "mode": "plan_only",
                    "steps": steps,
                    "requires_confirmation_before_execution": True,
                    "next_confirmation": "确认计划后再切换到普通模式或发送确认执行。",
                },
                "goal": goal_metadata(context.metadata),
            },
        )

    def pursue_goal(self, context: AgentContext) -> AgentReply:
        goal = goal_metadata(context.metadata)
        goal_text = str(goal.get("text") or "").strip()
        goal_state = self._goal_progress_state(context, goal)
        history_lines = []
        for item in context.recent_history[-6:]:
            role = "用户" if item.get("role") == "user" else "助手"
            content = str(item.get("content") or "").strip()
            if content:
                history_lines.append(f"{role}：{content}")
        history = "\n最近推进记录：\n" + "\n".join(history_lines) + "\n" if history_lines else ""
        prompt = (
            "You are SpiritKin goal pursuit mode. Keep working toward the active goal across turns. "
            "Visible reply text must be Chinese. Be concrete: state current progress, the next action or decision, "
            "and any blockers. If the user asks to continue, choose the next useful step for the goal. "
            + EMOTION_ACTION_CONTRACT
            + " "
            + build_goal_context(context.metadata)
            + build_relationship_context(context.metadata)
            + build_attachment_context(context.metadata)
            + history
            + f"Current user input: {context.combined_input} Reply:"
        )
        raw = self._llm_call(prompt, agent_name="main_text")
        text, emotion, action = parse_emotion_action_response(
            raw,
            default_emotion="thinking",
            default_action="write_plan",
        )
        return AgentReply(
            text=text[:900] if len(text) > 900 else text,
            emotion=emotion or "thinking",
            action=action or "write_plan",
            agent_name="goal_pursuit",
            metadata={
                "response_kind": "goal_pursuit",
                "pursue_goal": True,
                "goal": {**goal, **goal_state, "text": goal_text},
            },
        )

    @staticmethod
    def _goal_progress_state(context: AgentContext, goal: dict[str, object]) -> dict[str, object]:
        recent_goal_turns = 0
        for item in context.recent_history:
            content = str(item.get("content") or "")
            if "goal_pursuit" in content or str(goal.get("text") or "")[:20] in content:
                recent_goal_turns += 1
        turn_count = max(1, int(context.metadata.get("goal_turn_count") or 0) + 1)
        normalized = context.user_input.strip().lower()
        status = "active"
        if any(token in normalized for token in ("完成", "已完成", "done", "complete")):
            status = "complete"
        elif any(token in normalized for token in ("阻塞", "卡住", "blocked", "blocker")):
            status = "blocked"
        progress_percent = min(95, max(10, 10 + min(turn_count + recent_goal_turns, 9) * 10))
        if status == "complete":
            progress_percent = 100
        next_action = "继续拆解并推进下一步。"
        if status == "blocked":
            next_action = "先解除阻塞或补充缺失信息。"
        elif status == "complete":
            next_action = "总结结果并清理目标状态。"
        blockers = ["需要用户补充阻塞原因或允许下一步操作。"] if status == "blocked" else []
        return {
            "status": status,
            "progress_percent": progress_percent,
            "turn_count": turn_count,
            "next_action": next_action,
            "blockers": blockers,
            "last_user_input": context.user_input,
        }

    @staticmethod
    def _inventory_context(inventory: Mapping[str, object]) -> str:
        parts: list[str] = []
        software = inventory.get("software")
        if isinstance(software, list):
            apps = [str(item.get("name") or "").strip() for item in software[:50] if isinstance(item, dict)]
            apps = [name for name in apps if name]
            if apps:
                parts.append("Installed apps: " + ", ".join(apps[:40]) + ". ")
        cli_tools = inventory.get("cli_tools")
        if isinstance(cli_tools, list):
            cli = [
                str(item.get("name") or "").strip()
                for item in cli_tools
                if isinstance(item, dict) and item.get("available")
            ]
            cli = [name for name in cli if name]
            if cli:
                parts.append("Available command-line tools: " + ", ".join(cli) + ". ")
        return "".join(parts)

    @staticmethod
    def _hit_context(title: str, hits: object) -> str:
        if not isinstance(hits, list) or not hits:
            return ""
        return f"\n{title}：\n" + "\n".join(f"- {hit}" for hit in hits[:5]) + "\n"

    @staticmethod
    def _history_context(context: AgentContext) -> str:
        lines = []
        for item in context.recent_history or []:
            if not isinstance(item, dict):
                continue
            role = "用户" if item.get("role") == "user" else "助手"
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{role}：{content}")
        return "\n最近对话：\n" + "\n".join(lines) + "\n" if lines else ""
