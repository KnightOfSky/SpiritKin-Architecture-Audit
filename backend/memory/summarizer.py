from __future__ import annotations


class RollingMemorySummarizer:
    """轻量摘要器：把较早对话压成短摘要，避免直接塞满上下文。"""

    def __init__(self, max_chars: int = 240):
        self.max_chars = max(80, max_chars)

    def summarize(self, entries: list[dict]) -> str:
        if not entries:
            return ""

        lines = []
        for entry in entries:
            role = "用户" if entry.get("role") == "user" else "助手"
            content = str(entry.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"{role}:{content}")

        if not lines:
            return ""

        summary = "；".join(lines)
        if len(summary) <= self.max_chars:
            return summary
        return summary[: self.max_chars - 1].rstrip() + "…"