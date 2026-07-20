from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_service_rag_worker_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="rag.search",
                description="Search the configured knowledge retriever through the Service RAG worker.",
                target="knowledge",
                operation="rag.search",
                risk_level="low",
                read_only=True,
                schema={"query": "str", "top_k": "int"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="knowledge.retrieve",
                description="Retrieve knowledge chunks through the Service RAG worker.",
                target="knowledge",
                operation="knowledge.retrieve",
                risk_level="low",
                read_only=True,
                schema={"query": "str", "top_k": "int"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="embedding.create",
                description="Create embeddings with the configured embedding provider.",
                target="knowledge",
                operation="embedding.create",
                risk_level="low",
                read_only=True,
                schema={"text": "str", "texts": "list[str]", "provider": "str"},
            )
        ),
    ]
