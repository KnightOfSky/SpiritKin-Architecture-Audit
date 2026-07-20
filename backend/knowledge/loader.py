from __future__ import annotations

from pathlib import Path

from backend.knowledge.base import BaseKnowledgeRetriever
from backend.knowledge.embedding_retriever import build_embedding_retriever_from_store
from backend.knowledge.ingest import ingest_text_document
from backend.knowledge.reranker import BaseReranker, build_reranker
from backend.knowledge.retriever import SimpleKnowledgeRetriever
from backend.knowledge.store import InMemoryKnowledgeStore

SUPPORTED_TEXT_EXTENSIONS = {".md", ".txt", ".rst"}
_PROJECT_DOCS_RETRIEVER_CACHE: dict[tuple[str, str, str], BaseKnowledgeRetriever] = {}


def ingest_file(
    store: InMemoryKnowledgeStore,
    file_path: str | Path,
    *,
    document_id: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
):
    path = Path(file_path)
    content = path.read_text(encoding="utf-8", errors="ignore")
    merged_metadata = {"source_path": path.as_posix(), **(metadata or {})}
    doc_id = document_id or path.as_posix()
    doc_title = title or path.stem
    return ingest_text_document(store, document_id=doc_id, title=doc_title, content=content, metadata=merged_metadata)


def ingest_directory(
    store: InMemoryKnowledgeStore,
    directory: str | Path,
    *,
    recursive: bool = True,
    extensions: set[str] | None = None,
) -> list[str]:
    root = Path(directory)
    allowed_extensions = {ext.lower() for ext in (extensions or SUPPORTED_TEXT_EXTENSIONS)}
    pattern = "**/*" if recursive else "*"
    ingested: list[str] = []

    for path in sorted(root.glob(pattern)):
        if not path.is_file() or path.suffix.lower() not in allowed_extensions:
            continue
        ingest_file(store, path)
        ingested.append(path.as_posix())

    return ingested


def build_retriever_from_directory(
    directory: str | Path,
    *,
    recursive: bool = True,
    extensions: set[str] | None = None,
) -> BaseKnowledgeRetriever:
    store = InMemoryKnowledgeStore()
    ingest_directory(store, directory, recursive=recursive, extensions=extensions)
    return SimpleKnowledgeRetriever(store)


def clear_project_docs_retriever_cache() -> None:
    _PROJECT_DOCS_RETRIEVER_CACHE.clear()


def _resolve_docs_dir(project_root: str | Path) -> Path:
    return (Path(project_root) / "docs").resolve()


def _build_project_docs_retriever(
    project_root: str | Path,
    *,
    backend: str,
    use_cache: bool = True,
    vector_store_path: str | Path | None = None,
) -> BaseKnowledgeRetriever | None:
    docs_dir = _resolve_docs_dir(project_root)
    if not docs_dir.exists() or not docs_dir.is_dir():
        return None

    cache_key = (backend, docs_dir.as_posix(), str(vector_store_path or ""))
    if use_cache and cache_key in _PROJECT_DOCS_RETRIEVER_CACHE:
        return _PROJECT_DOCS_RETRIEVER_CACHE[cache_key]

    if backend == "embedding":
        retriever = build_embedding_retriever_from_directory(docs_dir, vector_store_path=vector_store_path)
    else:
        retriever = build_retriever_from_directory(docs_dir)

    if use_cache and retriever is not None:
        _PROJECT_DOCS_RETRIEVER_CACHE[cache_key] = retriever
    return retriever


def build_project_docs_retriever(
    project_root: str | Path = ".",
    *,
    use_cache: bool = True,
) -> BaseKnowledgeRetriever | None:
    return _build_project_docs_retriever(project_root, backend="keyword", use_cache=use_cache)


def build_embedding_retriever_from_directory(
    directory: str | Path,
    *,
    recursive: bool = True,
    extensions: set[str] | None = None,
    vector_store_path: str | Path | None = None,
    reranker: BaseReranker | str | None = "auto",
) -> BaseKnowledgeRetriever:
    store = InMemoryKnowledgeStore()
    ingest_directory(store, directory, recursive=recursive, extensions=extensions)
    resolved_reranker = build_reranker(reranker) if isinstance(reranker, str) else reranker
    return build_embedding_retriever_from_store(store, vector_store_path=vector_store_path, reranker=resolved_reranker)


def build_project_docs_embedding_retriever(
    project_root: str | Path = ".",
    *,
    use_cache: bool = True,
    vector_store_path: str | Path | None = None,
) -> BaseKnowledgeRetriever | None:
    return _build_project_docs_retriever(project_root, backend="embedding", use_cache=use_cache, vector_store_path=vector_store_path)
