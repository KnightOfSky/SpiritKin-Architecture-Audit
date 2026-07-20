from __future__ import annotations

import json
import logging
import os
import threading
from hashlib import blake2s
from math import sqrt
from time import monotonic
from typing import Any
from urllib import error, request

from backend.knowledge.base import BaseEmbeddingProvider, EmbeddingVector
from backend.knowledge.store import tokenize_text

logger = logging.getLogger(__name__)


class HashingEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, dimensions: int = 64):
        self._dimensions = max(8, dimensions)

    def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> EmbeddingVector:
        return self._embed(text)

    def _embed(self, text: str) -> EmbeddingVector:
        vector = [0.0] * self._dimensions
        for token in tokenize_text(text):
            index = int.from_bytes(blake2s(token.encode("utf-8"), digest_size=8).digest(), "big") % self._dimensions
            vector[index] += 1.0
        norm = sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        query_prefix: str | None = None,
        document_prefix: str | None = None,
    ):
        from backend.app.settings import resolve_embedding_api_key, resolve_embedding_base_url, resolve_embedding_model

        self.base_url = (base_url or resolve_embedding_base_url()).rstrip("/")
        self.model = model or resolve_embedding_model() or "text-embedding-model"
        self.api_key = api_key if api_key is not None else resolve_embedding_api_key()
        self.timeout = max(1.0, float(timeout))
        default_query_prefix, default_document_prefix = _default_embedding_prefixes(self.model)
        self.query_prefix = _resolve_embedding_prefix("SPIRITKIN_EMBEDDING_QUERY_PREFIX", query_prefix, default_query_prefix)
        self.document_prefix = _resolve_embedding_prefix(
            "SPIRITKIN_EMBEDDING_DOCUMENT_PREFIX",
            document_prefix,
            default_document_prefix,
        )

    def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        return self._embed([f"{self.document_prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> EmbeddingVector:
        vectors = self._embed([f"{self.query_prefix}{text}"])
        return vectors[0] if vectors else []

    def snapshot(self) -> dict[str, object]:
        return {
            "provider": type(self).__name__,
            "base_url": self.base_url,
            "model": self.model,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
            "timeout_seconds": self.timeout,
        }

    def _embed(self, texts: list[str]) -> list[EmbeddingVector]:
        if not self.model or not self.base_url:
            raise ValueError("embedding base_url/model is not configured")
        body = json.dumps({"model": self.model, "input": texts}, ensure_ascii=False).encode("utf-8")
        req = request.Request(f"{self.base_url}/embeddings", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
        data = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return []
        ordered = sorted((item for item in data if isinstance(item, dict)), key=lambda item: int(item.get("index") or 0))
        vectors: list[EmbeddingVector] = []
        for item in ordered:
            embedding = item.get("embedding")
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        return vectors


class FallbackEmbeddingProvider(BaseEmbeddingProvider):
    """Keep one provider mode for the lifetime of an index.

    Once the primary provider fails, both document and query embeddings use the
    deterministic fallback. This avoids mixing vector dimensions after a local
    LM Studio outage while keeping the degraded path observable.
    """

    def __init__(self, primary: BaseEmbeddingProvider, fallback: BaseEmbeddingProvider):
        self.primary = primary
        self.fallback = fallback
        self.degraded = False
        self.degraded_reason = ""
        self._lock = threading.RLock()

    def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        return self._call("embed_documents", texts)

    def embed_query(self, text: str) -> EmbeddingVector:
        return self._call("embed_query", text)

    def snapshot(self) -> dict[str, object]:
        primary_state = self.primary.snapshot() if hasattr(self.primary, "snapshot") else {}
        return {
            "provider": type(self.primary).__name__,
            "fallback": type(self.fallback).__name__,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "primary_state": primary_state,
        }

    def _call(self, method: str, value):
        with self._lock:
            if self.degraded:
                return getattr(self.fallback, method)(value)
            try:
                result = getattr(self.primary, method)(value)
                if method == "embed_documents" and len(result) != len(value):
                    raise RuntimeError(f"embedding provider returned {len(result)} vectors for {len(value)} documents")
                if method == "embed_query" and not result:
                    raise RuntimeError("embedding provider returned an empty query vector")
                return result
            except Exception as exc:
                self.degraded = True
                self.degraded_reason = f"{type(exc).__name__}: {str(exc)[:240]}"
                logger.warning("embedding provider degraded to %s: %s", type(self.fallback).__name__, self.degraded_reason)
                return getattr(self.fallback, method)(value)


class EmbeddingService(BaseEmbeddingProvider):
    """Process-shared embedding boundary with runtime health telemetry."""

    def __init__(self, provider: BaseEmbeddingProvider, *, service_key: str):
        self.provider = provider
        self.service_key = service_key
        self._lock = threading.RLock()
        self._calls = 0
        self._texts = 0
        self._failures = 0
        self._last_error = ""
        self._last_latency_ms = 0.0
        self._dimensions = 0

    def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        vectors = self._call("embed_documents", texts, text_count=len(texts))
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding service returned {len(vectors)} vectors for {len(texts)} documents")
        return vectors

    def embed_query(self, text: str) -> EmbeddingVector:
        vector = self._call("embed_query", text, text_count=1)
        if not vector:
            raise RuntimeError("embedding service returned an empty query vector")
        return vector

    def snapshot(self) -> dict[str, Any]:
        provider_snapshot = self.provider.snapshot() if hasattr(self.provider, "snapshot") else {}
        with self._lock:
            return {
                "service_key": self.service_key,
                "provider": type(self.provider).__name__,
                "calls": self._calls,
                "texts": self._texts,
                "failures": self._failures,
                "last_error": self._last_error,
                "last_latency_ms": round(self._last_latency_ms, 3),
                "dimensions": self._dimensions,
                "degraded": bool(provider_snapshot.get("degraded")),
                "degraded_reason": str(provider_snapshot.get("degraded_reason") or ""),
                "provider_state": provider_snapshot,
            }

    def _call(self, method: str, value: Any, *, text_count: int):
        started = monotonic()
        try:
            result = getattr(self.provider, method)(value)
            dimensions = _embedding_dimensions(result, method)
        except Exception as exc:
            with self._lock:
                self._calls += 1
                self._texts += text_count
                self._failures += 1
                self._last_error = f"{type(exc).__name__}: {str(exc)[:240]}"
                self._last_latency_ms = (monotonic() - started) * 1000.0
            raise
        with self._lock:
            self._calls += 1
            self._texts += text_count
            self._last_error = ""
            self._last_latency_ms = (monotonic() - started) * 1000.0
            if dimensions:
                self._dimensions = dimensions
        return result


_SHARED_SERVICES: dict[str, EmbeddingService] = {}
_SHARED_SERVICES_LOCK = threading.RLock()


def hashing_embeddings_enabled() -> bool:
    return os.getenv("SPIRITKIN_ALLOW_HASHING_EMBEDDINGS", "").strip().lower() in {"1", "true", "yes", "on"}


def build_embedding_provider(name: str | None = None, *, timeout: float | None = None) -> BaseEmbeddingProvider:
    from backend.app.settings import resolve_embedding_provider

    provider = resolve_embedding_provider(name).strip().lower()
    if provider in {
        "openai",
        "openai_compatible",
        "lmstudio",
        "lm-studio",
        "llamacpp",
        "llama_cpp",
        "llama.cpp",
        "llama-cpp",
    }:
        primary = OpenAICompatibleEmbeddingProvider(timeout=timeout if timeout is not None else 30.0)
        fallback = HashingEmbeddingProvider(dimensions=int(os.getenv("SPIRITKIN_HASHING_EMBEDDING_DIMENSIONS", "64") or 64))
        return FallbackEmbeddingProvider(primary, fallback)
    if provider in {"hashing", ""} and not hashing_embeddings_enabled():
        raise RuntimeError(
            "hashing embeddings are a dev fallback, not semantic retrieval; set SPIRITKIN_ALLOW_HASHING_EMBEDDINGS=1 to enable explicitly"
        )
    return HashingEmbeddingProvider(dimensions=int(os.getenv("SPIRITKIN_HASHING_EMBEDDING_DIMENSIONS", "64") or 64))


def get_embedding_service(name: str | None = None, *, refresh: bool = False) -> EmbeddingService:
    from backend.app.settings import resolve_embedding_base_url, resolve_embedding_model, resolve_embedding_provider

    provider_name = resolve_embedding_provider(name).strip().lower()
    model = resolve_embedding_model().strip()
    base_url = resolve_embedding_base_url().strip().rstrip("/")
    timeout = _embedding_timeout_seconds()
    dimensions = int(os.getenv("SPIRITKIN_HASHING_EMBEDDING_DIMENSIONS", "64") or 64)
    query_prefix = os.getenv("SPIRITKIN_EMBEDDING_QUERY_PREFIX", "<auto>")
    document_prefix = os.getenv("SPIRITKIN_EMBEDDING_DOCUMENT_PREFIX", "<auto>")
    service_key = "|".join(
        (provider_name or "hashing", model, base_url, str(timeout), str(dimensions), query_prefix, document_prefix)
    )
    with _SHARED_SERVICES_LOCK:
        if refresh:
            _SHARED_SERVICES.pop(service_key, None)
        service = _SHARED_SERVICES.get(service_key)
        if service is None:
            service = EmbeddingService(
                build_embedding_provider(provider_name, timeout=timeout),
                service_key=service_key,
            )
            _SHARED_SERVICES[service_key] = service
        return service


def embedding_services_snapshot() -> dict[str, Any]:
    with _SHARED_SERVICES_LOCK:
        services = [service.snapshot() for service in _SHARED_SERVICES.values()]
    return {
        "service_count": len(services),
        "services": services,
        "degraded_count": sum(1 for item in services if item.get("degraded")),
    }


def reset_embedding_services() -> None:
    with _SHARED_SERVICES_LOCK:
        _SHARED_SERVICES.clear()


def _embedding_timeout_seconds() -> float:
    raw = os.getenv("SPIRITKIN_EMBEDDING_TIMEOUT_SECONDS", "5")
    try:
        return max(0.25, min(120.0, float(raw or 5.0)))
    except (TypeError, ValueError):
        return 5.0


def _embedding_dimensions(result: Any, method: str) -> int:
    if method == "embed_query":
        return len(result) if isinstance(result, list) else 0
    if not isinstance(result, list) or not result:
        return 0
    dimensions = {len(vector) for vector in result if isinstance(vector, list)}
    if len(dimensions) > 1:
        raise RuntimeError("embedding provider returned inconsistent vector dimensions")
    return next(iter(dimensions), 0)


def _default_embedding_prefixes(model: str) -> tuple[str, str]:
    normalized = str(model or "").strip().lower()
    if "nomic" in normalized:
        return "search_query: ", "search_document: "
    return "", ""


def _resolve_embedding_prefix(env_key: str, explicit: str | None, default: str) -> str:
    if explicit is not None:
        return str(explicit)
    if env_key in os.environ:
        return str(os.environ.get(env_key) or "")
    return default
