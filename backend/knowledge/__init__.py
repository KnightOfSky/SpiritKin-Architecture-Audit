from backend.knowledge.base import (
    BaseEmbeddingProvider,
    BaseKnowledgeRetriever,
    BaseKnowledgeStore,
    BaseVectorStore,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalHit,
    VectorRecord,
)
from backend.knowledge.chunking import chunk_text_with_citations
from backend.knowledge.embedding import (
    EmbeddingService,
    FallbackEmbeddingProvider,
    HashingEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    build_embedding_provider,
    embedding_services_snapshot,
    get_embedding_service,
    reset_embedding_services,
)
from backend.knowledge.embedding_eval import (
    evaluate_embedding_provider,
    load_embedding_eval_dataset,
    load_latest_embedding_eval_report,
    write_embedding_eval_report,
)
from backend.knowledge.embedding_retriever import (
    EmbeddingKnowledgeRetriever,
    build_embedding_retriever_from_store,
    build_vector_store,
)
from backend.knowledge.incremental_ingest import DocumentTracker, IncrementalKnowledgeIndexer
from backend.knowledge.indexer import SimpleKnowledgeIndexer
from backend.knowledge.ingest import ingest_text_document
from backend.knowledge.loader import (
    build_embedding_retriever_from_directory,
    build_project_docs_embedding_retriever,
    build_project_docs_retriever,
    build_retriever_from_directory,
    clear_project_docs_retriever_cache,
    ingest_directory,
    ingest_file,
)
from backend.knowledge.registry import KnowledgeRegistry
from backend.knowledge.reranker import (
    BaseReranker,
    DummyReranker,
    EmbeddingReranker,
    OpenAICompatibleReranker,
    TokenOverlapReranker,
    build_reranker,
)
from backend.knowledge.retriever import SimpleKnowledgeRetriever
from backend.knowledge.store import InMemoryKnowledgeStore
from backend.knowledge.vault_connector import ObsidianVaultConnector
from backend.knowledge.vector_store import InMemoryVectorStore, JsonVectorStore
from backend.knowledge.watcher import DirectoryWatcher, FileChangeEvent

__all__ = [
    "KnowledgeChunk",
    "KnowledgeDocument",
    "RetrievalHit",
    "BaseKnowledgeRetriever",
    "BaseEmbeddingProvider",
    "BaseKnowledgeStore",
    "BaseVectorStore",
    "VectorRecord",
    "HashingEmbeddingProvider",
    "EmbeddingService",
    "FallbackEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "build_embedding_provider",
    "get_embedding_service",
    "embedding_services_snapshot",
    "reset_embedding_services",
    "EmbeddingKnowledgeRetriever",
    "build_embedding_retriever_from_store",
    "build_vector_store",
    "evaluate_embedding_provider",
    "load_embedding_eval_dataset",
    "load_latest_embedding_eval_report",
    "write_embedding_eval_report",
    "build_embedding_retriever_from_directory",
    "build_project_docs_embedding_retriever",
    "build_project_docs_retriever",
    "build_retriever_from_directory",
    "clear_project_docs_retriever_cache",
    "ingest_directory",
    "ingest_file",
    "SimpleKnowledgeIndexer",
    "IncrementalKnowledgeIndexer",
    "DocumentTracker",
    "ingest_text_document",
    "KnowledgeRegistry",
    "SimpleKnowledgeRetriever",
    "InMemoryKnowledgeStore",
    "InMemoryVectorStore",
    "JsonVectorStore",
    "BaseReranker",
    "DummyReranker",
    "EmbeddingReranker",
    "OpenAICompatibleReranker",
    "TokenOverlapReranker",
    "build_reranker",
    "ObsidianVaultConnector",
    "DirectoryWatcher",
    "FileChangeEvent",
    "chunk_text_with_citations",
]
