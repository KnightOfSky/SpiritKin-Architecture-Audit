from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.knowledge.base import BaseEmbeddingProvider, KnowledgeChunk, RetrievalHit
from backend.knowledge.embedding import (
    EmbeddingService,
    FallbackEmbeddingProvider,
    HashingEmbeddingProvider,
    build_embedding_provider,
    get_embedding_service,
    reset_embedding_services,
)
from backend.knowledge.embedding_retriever import build_embedding_retriever_from_store
from backend.knowledge.ingest import ingest_text_document
from backend.knowledge.reranker import EmbeddingReranker
from backend.knowledge.store import InMemoryKnowledgeStore
from backend.memory.activation import MemoryActivationPolicy
from backend.memory.long_term import JsonlLongTermMemoryStore, LongTermMemoryStore


class SemanticFixtureProvider(BaseEmbeddingProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        normalized = text.lower()
        if any(token in normalized for token in ("代码", "程序", "运行", "性能", "python", "runtime")):
            return [1.0, 0.0, 0.0]
        if any(token in normalized for token in ("花园", "植物", "garden")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class AlwaysFailProvider(BaseEmbeddingProvider):
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise ConnectionError("LM Studio unavailable")

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        raise ConnectionError("LM Studio unavailable")


class FailAfterIndexProvider(BaseEmbeddingProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        raise ConnectionError("LM Studio disconnected after indexing")

class MemorySemanticTests(unittest.TestCase):
    def tearDown(self):
        reset_embedding_services()

    def test_embedding_service_is_shared_and_reports_runtime_health(self):
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_EMBEDDING_PROVIDER": "hashing",
                "SPIRITKIN_ALLOW_HASHING_EMBEDDINGS": "1",
                "SPIRITKIN_HASHING_EMBEDDING_DIMENSIONS": "16",
            },
            clear=False,
        ):
            first = get_embedding_service()
            second = get_embedding_service()
            vector = first.embed_query("shared embedding service")

        self.assertIs(first, second)
        self.assertIsInstance(first, EmbeddingService)
        self.assertEqual(len(vector), 16)
        self.assertEqual(first.snapshot()["calls"], 1)
        self.assertEqual(first.snapshot()["dimensions"], 16)

    def test_embedding_provider_falls_back_once_and_reports_degraded_state(self):
        primary = AlwaysFailProvider()
        provider = FallbackEmbeddingProvider(primary, HashingEmbeddingProvider(dimensions=16))

        documents = provider.embed_documents(["alpha", "beta"])
        query = provider.embed_query("alpha")

        self.assertEqual(primary.calls, 1)
        self.assertEqual(len(documents), 2)
        self.assertEqual(len(documents[0]), len(query))
        self.assertTrue(provider.snapshot()["degraded"])
        self.assertIn("ConnectionError", provider.snapshot()["degraded_reason"])

    def test_factory_automatically_degrades_when_openai_compatible_local_server_is_unreachable(self):
        for provider_name in ("openai_compatible", "llamacpp", "llama.cpp"):
            with self.subTest(provider=provider_name), patch.dict(
                "os.environ",
                {
                    "SPIRITKIN_EMBEDDING_PROVIDER": provider_name,
                    "SPIRITKIN_EMBEDDING_MODEL": "nomic-embed",
                },
                clear=False,
            ), patch("backend.knowledge.embedding.request.urlopen", side_effect=ConnectionError("offline")):
                provider = build_embedding_provider(timeout=0.1)
                vector = provider.embed_query("fallback stays available")

            self.assertTrue(vector)
            self.assertIsInstance(provider, FallbackEmbeddingProvider)
            self.assertTrue(provider.degraded)

    def test_semantic_memory_recall_ranks_meaning_without_keyword_overlap(self):
        store = LongTermMemoryStore(embedding_provider=SemanticFixtureProvider())
        store.add("preference", "希望程序运行得更快", importance=0.8)
        store.add("preference", "喜欢在阳台照料植物", importance=0.9)

        results = store.recall("Python 性能优化", top_k=2)

        self.assertEqual(len(results), 1)
        self.assertIn("程序运行", results[0].content)
        self.assertGreaterEqual(results[0].activation, 30.0)
        self.assertEqual(results[0].memory_state, "active")

    def test_late_provider_failure_reembeds_incompatible_stored_vectors(self):
        provider = FallbackEmbeddingProvider(FailAfterIndexProvider(), HashingEmbeddingProvider(dimensions=16))
        store = LongTermMemoryStore(embedding_provider=provider)
        entry = store.add("preference", "alpha preference", importance=0.9)
        self.assertEqual(len(entry.metadata["semantic_embedding"]), 2)

        results = store.recall("alpha preference", top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].metadata["semantic_embedding"]), 16)
        self.assertTrue(provider.degraded)

    def test_knowledge_retriever_reindexes_after_provider_dimension_transition(self):
        provider = FallbackEmbeddingProvider(FailAfterIndexProvider(), HashingEmbeddingProvider(dimensions=16))
        knowledge = InMemoryKnowledgeStore()
        ingest_text_document(knowledge, "alpha", "Alpha", "alpha preference")
        retriever = build_embedding_retriever_from_store(
            knowledge,
            embedding_provider=provider,
            reranker=None,
        )

        results = retriever.retrieve("alpha preference", top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk.document_id, "alpha")
        self.assertTrue(provider.degraded)

    def test_activation_policy_has_active_dormant_archived_boundaries(self):
        policy = MemoryActivationPolicy()

        self.assertEqual(policy.state(30.0), "active")
        self.assertEqual(policy.state(29.999), "dormant")
        self.assertEqual(policy.state(0.0), "archived")
        self.assertGreater(policy.on_user_hit(20.0, 2), 30.0)
        self.assertLess(policy.decay(80.0, days_idle=5.0, days_since_maintenance=5.0, intrinsic_value=0.5), 80.0)

    def test_embedding_reranker_uses_cosine_similarity(self):
        hits = [
            RetrievalHit(KnowledgeChunk("1", "d1", "照料花园"), score=0.9, source_title="花园"),
            RetrievalHit(KnowledgeChunk("2", "d2", "优化 runtime"), score=0.1, source_title="代码"),
        ]

        reranked = EmbeddingReranker(SemanticFixtureProvider()).rerank("Python 性能", hits, top_k=2)

        self.assertEqual([item.chunk.chunk_id for item in reranked], ["2", "1"])
        self.assertGreater(reranked[0].score, reranked[1].score)

    def test_jsonl_store_migrates_legacy_activation_and_keeps_ids_monotonic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            path.write_text(
                '{"entry_id":"ltm-000007","category":"preference","content":"legacy","importance":0.8,"activation":0.5}\n',
                encoding="utf-8",
            )
            store = JsonlLongTermMemoryStore(path)

            created = store.add("preference", "new", importance=0.8)
            legacy = store._entries["ltm-000007"]

        self.assertEqual(legacy.activation, 50.0)
        self.assertEqual(legacy.memory_state, "active")
        self.assertEqual(created.entry_id, "ltm-000008")


if __name__ == "__main__":
    unittest.main()
