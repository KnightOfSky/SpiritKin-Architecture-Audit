from __future__ import annotations

from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.knowledge.base import BaseKnowledgeRetriever


class ServiceRAGWorkerExecutor(BaseExecutor):
    """Read-only service worker for knowledge retrieval and optional embeddings."""

    name = "service_rag_worker"
    supported_targets = ("knowledge", "vector_store")
    supported_operations = ("rag.search", "knowledge.retrieve", "embedding.create")

    def __init__(self, retriever: BaseKnowledgeRetriever | None = None):
        self._retriever = retriever

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in self.supported_targets and request.operation in self.supported_operations

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"Unsupported Service RAG worker request: {request.target}.{request.operation}",
                error_code="service_rag_worker_unsupported_request",
                metadata={"target": request.target, "operation": request.operation},
            )
        if request.operation in {"rag.search", "knowledge.retrieve"}:
            return self._retrieve(request)
        if request.operation == "embedding.create":
            return self._create_embedding(request)
        return ExecutionResult(success=False, message="Unsupported Service RAG operation", error_code="service_rag_worker_unsupported_operation")

    def _retrieve(self, request: ExecutionRequest) -> ExecutionResult:
        if self._retriever is None:
            return ExecutionResult(
                success=False,
                message="Knowledge retriever is not configured",
                error_code="service_rag_worker_not_configured",
                metadata=self._base_metadata(),
            )
        params = dict(request.params or {})
        query = str(params.get("query") or "").strip()
        if not query:
            return ExecutionResult(
                success=False,
                message="query is required",
                error_code="service_rag_worker_missing_query",
                metadata=self._base_metadata(),
            )
        top_k = _coerce_top_k(params.get("top_k"))
        hits = self._retriever.retrieve(query, top_k=top_k)
        data = [
            {
                "document_id": hit.chunk.document_id,
                "chunk_id": hit.chunk.chunk_id,
                "source_title": hit.source_title,
                "score": hit.score,
                "text": hit.chunk.text,
                "metadata": dict(hit.chunk.metadata or {}),
            }
            for hit in hits
        ]
        return ExecutionResult(
            success=True,
            message=f"Service RAG worker returned {len(data)} hits",
            data={"query": query, "top_k": top_k, "hits": data},
            metadata=self._base_metadata(read_only=True),
        )

    def _create_embedding(self, request: ExecutionRequest) -> ExecutionResult:
        params = dict(request.params or {})
        texts = params.get("texts")
        if texts is None and params.get("text") is not None:
            texts = [params.get("text")]
        if not isinstance(texts, (list, tuple)) or not texts:
            return ExecutionResult(
                success=False,
                message="texts is required",
                error_code="service_rag_worker_missing_texts",
                metadata=self._base_metadata(read_only=True),
            )
        try:
            from backend.knowledge.embedding import build_embedding_provider

            provider = build_embedding_provider(params.get("provider"))
            embeddings = provider.embed_documents([str(item) for item in texts])
        except Exception as exc:
            return ExecutionResult(
                success=False,
                message=f"Embedding provider is not configured or failed: {exc}",
                error_code="service_rag_worker_embedding_not_configured",
                metadata=self._base_metadata(read_only=True),
            )
        return ExecutionResult(
            success=True,
            message=f"Service RAG worker created {len(embeddings)} embeddings",
            data={"count": len(embeddings), "embeddings": embeddings},
            metadata=self._base_metadata(read_only=True),
        )

    def _base_metadata(self, *, read_only: bool = True) -> dict[str, Any]:
        return {
            "executor": self.name,
            "worker_maturity": "real",
            "read_only": read_only,
            "retriever_configured": self._retriever is not None,
        }


def _coerce_top_k(value: Any) -> int:
    try:
        return max(1, min(50, int(value or 5)))
    except (TypeError, ValueError):
        return 5
