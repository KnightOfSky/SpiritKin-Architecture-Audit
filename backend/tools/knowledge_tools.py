from __future__ import annotations

from backend.knowledge.base import BaseKnowledgeRetriever
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec


class KnowledgeSearchTool(BaseTool):
    def __init__(self, retriever: BaseKnowledgeRetriever):
        self._retriever = retriever
        self.spec = ToolSpec(
            name="kb.search",
            description="检索知识库并返回最相关的片段。",
            target="knowledge",
            operation="search",
            read_only=True,
            schema={"query": "str", "top_k": "int"},
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(
                success=False,
                message=f"不支持的工具: {call.name}",
                error_code="tool_not_supported",
                metadata={"tool_name": call.name},
            )

        query = str(call.arguments.get("query", "")).strip()
        if not query:
            return ToolResult(
                success=False,
                message="缺少参数: query",
                error_code="missing_params",
                metadata={"missing_param": "query"},
            )

        top_k = int(call.arguments.get("top_k", 5) or 5)
        hits = self._retriever.retrieve(query, top_k=top_k)
        data = [
            {
                "document_id": hit.chunk.document_id,
                "chunk_id": hit.chunk.chunk_id,
                "source_title": hit.source_title,
                "score": hit.score,
                "text": hit.chunk.text,
            }
            for hit in hits
        ]
        return ToolResult(success=True, message=f"命中 {len(data)} 条知识片段", data=data)