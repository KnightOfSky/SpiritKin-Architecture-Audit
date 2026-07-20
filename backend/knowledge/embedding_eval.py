from __future__ import annotations

import json
from math import sqrt
from pathlib import Path
from time import monotonic
from typing import Any

from backend.knowledge.base import BaseEmbeddingProvider, KnowledgeChunk, RetrievalHit
from backend.knowledge.reranker import BaseReranker

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = ROOT / "config" / "evals" / "embedding_retrieval.json"
DEFAULT_REPORT_PATH = ROOT / "state" / "evaluations" / "embedding" / "latest.json"


def load_embedding_eval_dataset(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_DATASET_PATH).resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("embedding evaluation dataset must be a JSON object")
    documents = payload.get("documents")
    queries = payload.get("queries")
    if not isinstance(documents, list) or not documents:
        raise ValueError("embedding evaluation dataset requires documents")
    if not isinstance(queries, list) or not queries:
        raise ValueError("embedding evaluation dataset requires queries")
    return payload


def evaluate_embedding_provider(
    provider: BaseEmbeddingProvider,
    dataset: dict[str, Any],
    *,
    top_k: int = 3,
    allow_degraded: bool = False,
    reranker: BaseReranker | None = None,
) -> dict[str, Any]:
    documents = _documents(dataset.get("documents"))
    queries = _queries(dataset.get("queries"), set(documents))
    resolved_top_k = max(1, min(int(top_k or 3), len(documents)))
    started = monotonic()
    document_ids = list(documents)
    document_vectors = provider.embed_documents([documents[item] for item in document_ids])
    if len(document_vectors) != len(document_ids):
        raise RuntimeError("embedding evaluation received an incomplete document vector set")

    outcomes: list[dict[str, Any]] = []
    reciprocal_rank_total = 0.0
    recall_at_1_count = 0
    recall_at_k_count = 0
    embedding_reciprocal_rank_total = 0.0
    embedding_recall_at_1_count = 0
    embedding_recall_at_k_count = 0
    dimensions = 0
    for item in queries:
        query_vector = provider.embed_query(item["text"])
        if not query_vector:
            raise RuntimeError(f"embedding evaluation returned an empty vector for {item['id']}")
        dimensions = dimensions or len(query_vector)
        if len(query_vector) != dimensions:
            raise RuntimeError("embedding evaluation query dimensions changed during the run")
        ranked = sorted(
            (
                {"document_id": document_id, "score": _cosine_similarity(query_vector, vector)}
                for document_id, vector in zip(document_ids, document_vectors, strict=True)
            ),
            key=lambda result: (float(result["score"]), str(result["document_id"])),
            reverse=True,
        )
        relevant = set(item["relevant"])
        embedding_first_rank, embedding_recall_at_1, embedding_recall_at_k = _ranking_outcome(
            ranked,
            relevant,
            resolved_top_k,
        )
        final_ranked = _rerank_results(reranker, item["text"], ranked, documents)
        first_rank, recall_at_1, recall_at_k = _ranking_outcome(final_ranked, relevant, resolved_top_k)
        recall_at_1_count += int(recall_at_1)
        recall_at_k_count += int(recall_at_k)
        reciprocal_rank_total += 1.0 / first_rank if first_rank else 0.0
        embedding_recall_at_1_count += int(embedding_recall_at_1)
        embedding_recall_at_k_count += int(embedding_recall_at_k)
        embedding_reciprocal_rank_total += 1.0 / embedding_first_rank if embedding_first_rank else 0.0
        outcomes.append(
            {
                "query_id": item["id"],
                "query": item["text"],
                "relevant": sorted(relevant),
                "first_relevant_rank": first_rank,
                "embedding_first_relevant_rank": embedding_first_rank,
                "embedding_top": ranked[:resolved_top_k],
                "top": final_ranked[:resolved_top_k],
            }
        )

    count = len(queries)
    metrics = {
        "query_count": count,
        "document_count": len(documents),
        "top_k": resolved_top_k,
        "recall_at_1": recall_at_1_count / count,
        "recall_at_k": recall_at_k_count / count,
        "mrr": reciprocal_rank_total / count,
        "dimensions": dimensions,
        "duration_ms": round((monotonic() - started) * 1000.0, 3),
    }
    embedding_metrics = {
        "recall_at_1": embedding_recall_at_1_count / count,
        "recall_at_k": embedding_recall_at_k_count / count,
        "mrr": embedding_reciprocal_rank_total / count,
    }
    thresholds = _thresholds(dataset.get("thresholds"))
    provider_state = provider.snapshot() if hasattr(provider, "snapshot") else {"provider": type(provider).__name__}
    degraded = bool(provider_state.get("degraded"))
    checks = {
        "recall_at_1": metrics["recall_at_1"] >= thresholds["recall_at_1"],
        "recall_at_k": metrics["recall_at_k"] >= thresholds["recall_at_k"],
        "mrr": metrics["mrr"] >= thresholds["mrr"],
        "non_degraded": allow_degraded or not degraded,
    }
    return {
        "schema_version": "spiritkin.embedding_eval.v1",
        "dataset": str(dataset.get("name") or "embedding_retrieval"),
        "passed": all(checks.values()),
        "metrics": metrics,
        "embedding_metrics": embedding_metrics,
        "thresholds": thresholds,
        "checks": checks,
        "provider": provider_state,
        "reranker": type(reranker).__name__ if reranker is not None else "none",
        "outcomes": outcomes,
    }


def write_embedding_eval_report(report: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path or DEFAULT_REPORT_PATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_latest_embedding_eval_report(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_REPORT_PATH).resolve()
    if not target.exists():
        return {"status": "not_run", "path": str(target)}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid", "path": str(target)}
    if not isinstance(payload, dict):
        return {"status": "invalid", "path": str(target)}
    return {"status": "passed" if payload.get("passed") else "failed", "path": str(target), "report": payload}


def _documents(value: Any) -> dict[str, str]:
    documents: dict[str, str] = {}
    for item in value or []:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        if document_id and text:
            documents[document_id] = text
    if not documents:
        raise ValueError("embedding evaluation dataset has no valid documents")
    return documents


def _queries(value: Any, document_ids: set[str]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        query_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        relevant = [str(candidate) for candidate in item.get("relevant") or [] if str(candidate) in document_ids]
        if query_id and text and relevant:
            queries.append({"id": query_id, "text": text, "relevant": relevant})
    if not queries:
        raise ValueError("embedding evaluation dataset has no valid queries")
    return queries


def _thresholds(value: Any) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}
    return {
        "recall_at_1": max(0.0, min(1.0, float(source.get("recall_at_1", 0.8)))),
        "recall_at_k": max(0.0, min(1.0, float(source.get("recall_at_k", 1.0)))),
        "mrr": max(0.0, min(1.0, float(source.get("mrr", 0.85)))),
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _ranking_outcome(ranked: list[dict[str, Any]], relevant: set[str], top_k: int) -> tuple[int, bool, bool]:
    first_rank = next((index for index, result in enumerate(ranked, start=1) if result["document_id"] in relevant), 0)
    recall_at_1 = bool(ranked and ranked[0]["document_id"] in relevant)
    recall_at_k = any(result["document_id"] in relevant for result in ranked[:top_k])
    return first_rank, recall_at_1, recall_at_k


def _rerank_results(
    reranker: BaseReranker | None,
    query: str,
    ranked: list[dict[str, Any]],
    documents: dict[str, str],
) -> list[dict[str, Any]]:
    if reranker is None:
        return ranked
    hits = [
        RetrievalHit(
            chunk=KnowledgeChunk(
                chunk_id=str(item["document_id"]),
                document_id=str(item["document_id"]),
                text=documents[str(item["document_id"])],
            ),
            score=float(item["score"]),
            source_title=str(item["document_id"]),
        )
        for item in ranked
    ]
    reranked = reranker.rerank(query, hits, top_k=len(hits))
    return [{"document_id": hit.chunk.document_id, "score": float(hit.score)} for hit in reranked]
