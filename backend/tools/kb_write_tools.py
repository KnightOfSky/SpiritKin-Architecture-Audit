from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec


@dataclass
class KBDraftStore:
    _drafts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def upsert_draft(self, title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
        draft_id = f"draft:{title}"
        entry = {"title": title, "content": content, "tags": tags or [], "updated_at": __import__("time").time()}
        self._drafts[draft_id] = entry
        return entry

    def list_drafts(self) -> list[dict[str, Any]]:
        return list(self._drafts.values())

    def archive(self, title: str) -> bool:
        draft_id = f"draft:{title}"
        if draft_id in self._drafts:
            del self._drafts[draft_id]
            return True
        return False


_DEFAULT_KB_DRAFT_STORE = KBDraftStore()


class KBUpsertDraftTool(BaseTool):
    def __init__(self):
        super().__init__()
        self._spec = ToolSpec(
            name="kb.upsert_draft",
            description="将内容写入知识库草稿（不覆盖已确认笔记，待用户审核）",
            target="knowledge",
            operation="upsert_draft",
            risk_level="medium",
            read_only=False,
            schema={"title": "str", "content": "str", "tags": "list[str]"},
        )
        self._store = _DEFAULT_KB_DRAFT_STORE

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, call: ToolCall) -> ToolResult:
        title = str(call.arguments.get("title") or "")
        content = str(call.arguments.get("content") or "")
        tags = call.arguments.get("tags", []) if isinstance(call.arguments.get("tags"), list) else []
        if not title or not content:
            return ToolResult(success=False, message="title 和 content 必填", error_code="missing_fields")
        entry = self._store.upsert_draft(title, content, tags)
        return ToolResult(success=True, message=f"草稿已保存: {title}", data=entry)


class KBLinkSuggestTool(BaseTool):
    def __init__(self):
        super().__init__()
        self._spec = ToolSpec(
            name="kb.link_suggest",
            description="为当前文档/主题建议关联的其他知识条目",
            target="knowledge",
            operation="link_suggest",
            risk_level="low",
            read_only=True,
            schema={"title": "str", "content": "str"},
        )
        self._store = _DEFAULT_KB_DRAFT_STORE

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, call: ToolCall) -> ToolResult:
        title = str(call.arguments.get("title") or "")
        content = str(call.arguments.get("content") or "")
        suggestions: list[str] = []
        for draft in self._store.list_drafts():
            if draft["title"] != title and any(tag in str(content).lower() for tag in draft.get("tags", [])):
                suggestions.append(draft["title"])
        return ToolResult(success=True, message=f"建议关联 {len(suggestions)} 篇", data={"links": suggestions[:10]})


class KBTagSuggestTool(BaseTool):
    def __init__(self):
        super().__init__()
        self._spec = ToolSpec(
            name="kb.tag_suggest",
            description="根据内容自动建议标签",
            target="knowledge",
            operation="tag_suggest",
            risk_level="low",
            read_only=True,
            schema={"content": "str"},
        )
        self._store = _DEFAULT_KB_DRAFT_STORE

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, call: ToolCall) -> ToolResult:
        content = str(call.arguments.get("content") or "").lower()
        candidates = {
            "python": "python",
            "agent": "agent",
            "llm": "llm",
            "memory": "memory",
            "knowledge": "knowledge",
            "tool": "tool",
            "security": "security",
            "voice": "voice",
            "mobile": "mobile",
            "deploy": "deploy",
        }
        matched = [tag for kw, tag in candidates.items() if kw in content]
        return ToolResult(success=True, message=f"建议 {len(matched)} 个标签", data={"tags": matched[:8]})


class KBArchiveTool(BaseTool):
    def __init__(self):
        super().__init__()
        self._spec = ToolSpec(
            name="kb.archive",
            description="归档指定知识库条目（不删除，标记为已归档）",
            target="knowledge",
            operation="archive",
            risk_level="high",
            read_only=False,
            schema={"title": "str"},
        )
        self._store = _DEFAULT_KB_DRAFT_STORE

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, call: ToolCall) -> ToolResult:
        title = str(call.arguments.get("title") or "")
        if not title:
            return ToolResult(success=False, message="title 必填", error_code="missing_fields")
        archived = self._store.archive(title)
        if archived:
            return ToolResult(success=True, message=f"已归档: {title}")
        return ToolResult(success=False, message=f"未找到草稿: {title}", error_code="not_found")


def get_kb_write_tools() -> list[BaseTool]:
    return [KBUpsertDraftTool(), KBLinkSuggestTool(), KBTagSuggestTool(), KBArchiveTool()]
