from __future__ import annotations

from backend.agents.base import AgentContext, AgentReply
from backend.memory.short_term import ShortTermMemory
from backend.memory.summarizer import RollingMemorySummarizer


class SessionManager:
    """管理工作记忆、近期对话和注入给 agent 的上下文摘要。"""

    def __init__(self, memory_limit: int = 20, context_window: int = 6, summarizer=None):
        self._memory = ShortTermMemory(max_turns=memory_limit)
        self._context_window = max(2, context_window)
        self._summarizer = summarizer or RollingMemorySummarizer()

    @property
    def transcript(self) -> list[dict]:
        return self._memory.export()

    def build_context(self, user_input: str, visual_context: str, device_name: str, metadata=None) -> AgentContext:
        metadata = dict(metadata or {})
        transcript = self._memory.export()
        history_prefix = transcript[: -self._context_window] if len(transcript) > self._context_window else []

        metadata.update(
            {
                "recent_history": self._memory.recent(limit=self._context_window),
                "session_summary": self._summarizer.summarize(history_prefix),
            }
        )

        return AgentContext(
            user_input=user_input,
            visual_context=visual_context,
            device_name=device_name,
            metadata=metadata,
        )

    def record_user_turn(self, user_input: str):
        self._memory.add(role="user", content=user_input, agent="user")

    def record_agent_turn(self, reply: AgentReply):
        self._memory.add(role="assistant", content=reply.text, agent=reply.agent_name)