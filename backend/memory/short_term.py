from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class MemoryEntry:
    role: str
    content: str
    agent: str = "system"


class ShortTermMemory:
    """保存最近若干轮对话，供集群做工作记忆使用。"""

    def __init__(self, max_turns: int = 20):
        self.max_turns = max(1, max_turns)
        self._entries: list[MemoryEntry] = []

    def add(self, role: str, content: str, agent: str = "system"):
        self._entries.append(MemoryEntry(role=role, content=content, agent=agent))
        self._trim()

    def recent(self, limit: int = 6) -> list[dict]:
        limit = max(1, limit)
        return [asdict(entry) for entry in self._entries[-limit:]]

    def export(self) -> list[dict]:
        return [asdict(entry) for entry in self._entries]

    def _trim(self):
        max_items = max(self.max_turns * 2, 2)
        if len(self._entries) > max_items:
            self._entries = self._entries[-max_items:]

    def __len__(self) -> int:
        return len(self._entries)