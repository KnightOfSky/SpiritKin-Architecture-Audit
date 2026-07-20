from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from math import sqrt
from urllib import error, request

from backend.knowledge.base import BaseEmbeddingProvider, RetrievalHit
from backend.knowledge.embedding import get_embedding_service
from backend.knowledge.store import tokenize_text


class BaseReranker(ABC):
    @abstractmethod
    def rerank(self, query: str, hits: list[RetrievalHit], top_k: int = 5) -> list[RetrievalHit]:
        raise NotImplementedError


class DummyReranker(BaseReranker):
    def rerank(self, query: str, hits: list[RetrievalHit], top_k: int = 5) -> list[RetrievalHit]:
        return hits[:top_k]


class TokenOverlapReranker(BaseReranker):
    def rerank(self, query: str, hits: list[RetrievalHit], top_k: int = 5) -> list[RetrievalHit]:
        if not hits:
            return []

        terms = tokenize_text(query)
        if not terms:
            return hits[:top_k]

        scored: list[tuple[RetrievalHit, float]] = []
        for hit in hits:
            haystack = f"{hit.source_title} {hit.chunk.text}".lower()
            overlap = sum(1.0 for term in terms if term in haystack)
            title_bonus = 0.5 * sum(1.0 for term in terms if term in hit.source_title.lower())
            density = overlap / max(1, len(haystack.split()))
            rerank_score = (hit.score * 0.4) + (overlap * 0.4) + (title_bonus * 0.2) + (density * 50.0)

            new_metadata = dict(hit.chunk.metadata)
            new_metadata["rerank_score"] = rerank_score
            new_metadata["original_score"] = hit.score
            reranked_hit = RetrievalHit(chunk=replace(hit.chunk, metadata=new_metadata), score=rerank_score, source_title=hit.source_title)
            scored.append((reranked_hit, rerank_score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return [item[0] for item in scored[:top_k]]


class EmbeddingReranker(BaseReranker):
    def __init__(self, embedding_provider: BaseEmbeddingProvider | None = None):
        self._embedding_provider = embedding_provider or get_embedding_service()
        self._fallback = TokenOverlapReranker()

    def rerank(self, query: str, hits: list[RetrievalHit], top_k: int = 5) -> list[RetrievalHit]:
        if not hits:
            return []
        texts = [query, *[f"{hit.source_title}\n{hit.chunk.text}" for hit in hits]]
        try:
            vectors = self._embedding_provider.embed_documents(texts)
        except Exception:
            return self._fallback.rerank(query, hits, top_k=top_k)
        if len(vectors) != len(texts):
            return self._fallback.rerank(query, hits, top_k=top_k)
        query_vector = vectors[0]
        reranked: list[RetrievalHit] = []
        for hit, vector in zip(hits, vectors[1:], strict=False):
            semantic_score = _cosine_similarity(query_vector, vector)
            metadata = dict(hit.chunk.metadata)
            metadata["rerank_score"] = semantic_score
            metadata["original_score"] = hit.score
            reranked.append(
                RetrievalHit(
                    chunk=replace(hit.chunk, metadata=metadata),
                    score=semantic_score,
                    source_title=hit.source_title,
                )
            )
        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked[:top_k]


class OpenAICompatibleReranker(BaseReranker):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        cooldown_seconds: float | None = None,
    ):
        from backend.app.settings import resolve_reranker_api_key, resolve_reranker_base_url, resolve_reranker_model

        self.base_url = (base_url or resolve_reranker_base_url()).rstrip("/")
        self.model = model or resolve_reranker_model() or "local-reranker"
        self.api_key = api_key if api_key is not None else resolve_reranker_api_key()
        self.timeout = _bounded_float(
            timeout if timeout is not None else os.getenv("SPIRITKIN_RERANKER_TIMEOUT_SECONDS"),
            default=5.0,
            minimum=0.5,
            maximum=30.0,
        )
        self.cooldown_seconds = _bounded_float(
            cooldown_seconds if cooldown_seconds is not None else os.getenv("SPIRITKIN_RERANKER_COOLDOWN_SECONDS"),
            default=30.0,
            minimum=1.0,
            maximum=300.0,
        )
        self._unavailable_until = 0.0
        self._last_error = ""
        self._fallback = TokenOverlapReranker()

    def rerank(self, query: str, hits: list[RetrievalHit], top_k: int = 5) -> list[RetrievalHit]:
        if not hits:
            return []
        if time.monotonic() < self._unavailable_until:
            return self._fallback.rerank(query, hits, top_k=top_k)
        try:
            order = self._request_order(query, hits)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"[:500]
            self._unavailable_until = time.monotonic() + self.cooldown_seconds
            return self._fallback.rerank(query, hits, top_k=top_k)
        self._last_error = ""
        self._unavailable_until = 0.0
        by_id = {str(index + 1): hit for index, hit in enumerate(hits)}
        reranked = [by_id[item] for item in order if item in by_id]
        seen = {id(item) for item in reranked}
        reranked.extend(hit for hit in hits if id(hit) not in seen)
        return reranked[:top_k]

    def _request_order(self, query: str, hits: list[RetrievalHit]) -> list[str]:
        candidates = [
            {
                "id": str(index + 1),
                "title": hit.source_title,
                "text": hit.chunk.text[:900],
                "score": hit.score,
            }
            for index, hit in enumerate(hits[:12])
        ]
        prompt = {
            "query": query,
            "candidates": candidates,
            "instruction": "Return JSON only: {\"order\":[\"candidate_id\", ...]} ordered by relevance.",
        }
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You rerank retrieval candidates. Return valid JSON only."},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = request.Request(f"{self.base_url}/chat/completions", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
        message = payload.get("choices", [{}])[0].get("message", {})
        text = str(message.get("content") or message.get("reasoning_content") or "").strip()
        text = _extract_json_object_text(text)
        data = json.loads(text)
        order = data.get("order") if isinstance(data, dict) else []
        return [str(item) for item in order if str(item).strip()]


def build_reranker(name: str | None = "token_overlap") -> BaseReranker:
    if name is not None and not str(name).strip():
        name_lower = "token_overlap"
    elif name is None or str(name).strip().lower() == "auto":
        from backend.app.settings import resolve_reranker_provider

        name_lower = resolve_reranker_provider(None).lower()
    else:
        name_lower = str(name).strip().lower()
    if name_lower == "dummy":
        return DummyReranker()
    if name_lower in {
        "openai",
        "openai_compatible",
        "lmstudio",
        "llm",
        "llamacpp",
        "llama_cpp",
        "llama.cpp",
        "llama-cpp",
    }:
        return OpenAICompatibleReranker()
    if name_lower in {"embedding", "semantic", "cosine"}:
        return EmbeddingReranker()
    if name_lower in ("token_overlap", "token-overlap", "overlap"):
        return TokenOverlapReranker()
    return TokenOverlapReranker()


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _extract_json_object_text(text: str) -> str:
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
