from __future__ import annotations

from backend.search import SearchOptions, SearchProvider, build_default_search_provider
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec


class WebSearchTool(BaseTool):
    def __init__(self, provider: SearchProvider | None = None):
        self._provider = provider or build_default_search_provider()
        self.spec = ToolSpec(
            name="web.search",
            description="联网搜索网页结果，返回标题、URL、摘要和来源；用于需要把结果带回 Agent 总结的查询。",
            target="web",
            operation="search",
            read_only=True,
            schema={"query": "str", "count": "int", "provider": "str", "freshness": "str"},
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(
                success=False,
                message=f"不支持的工具: {call.name}",
                error_code="tool_not_supported",
                metadata={"tool_name": call.name},
            )
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            return ToolResult(
                success=False,
                message="缺少参数: query",
                error_code="missing_params",
                metadata={"missing_param": "query"},
            )
        options = SearchOptions(
            count=int(call.arguments.get("count") or call.arguments.get("top_k") or 5),
            country=str(call.arguments.get("country") or ""),
            language=str(call.arguments.get("language") or ""),
            freshness=str(call.arguments.get("freshness") or ""),
            safe_search=str(call.arguments.get("safe_search") or "moderate"),
        )
        try:
            results = self._provider.search(query, options=options)
        except Exception as exc:
            return ToolResult(
                success=False,
                message=f"联网搜索失败：{exc}",
                error_code="web_search_failed",
                metadata={"provider": getattr(self._provider, "name", self._provider.__class__.__name__)},
            )
        data = [result.snapshot() for result in results]
        return ToolResult(
            success=True,
            message=f"联网搜索命中 {len(data)} 条结果",
            data=data,
            metadata={"provider": getattr(self._provider, "name", self._provider.__class__.__name__)},
        )
