from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.knowledge.base import KnowledgeChunk, KnowledgeDocument, RetrievalHit
from backend.knowledge.chunking import chunk_text_with_citations
from backend.knowledge.incremental_ingest import DocumentTracker, IncrementalKnowledgeIndexer
from backend.knowledge.reranker import DummyReranker, TokenOverlapReranker, build_reranker
from backend.knowledge.store import InMemoryKnowledgeStore
from backend.knowledge.vault_connector import ObsidianVaultConnector
from backend.knowledge.watcher import DirectoryWatcher, FileChangeEvent


class IncrementalIndexerTests(unittest.TestCase):
    def test_chunk_text_with_citations_preserves_line_ranges(self):
        text = "line one\nline two\nline three\nline four\n"
        chunks = chunk_text_with_citations(text, chunk_size=20, overlap=0)
        self.assertGreaterEqual(len(chunks), 1)
        for _chunk_text, citation in chunks:
            self.assertIsInstance(citation, tuple)
            self.assertEqual(len(citation), 2)
            self.assertLessEqual(citation[0], citation[1])

    def test_chunk_text_with_citations_empty_input(self):
        self.assertEqual(chunk_text_with_citations(""), [])
        self.assertEqual(chunk_text_with_citations(None), [])

    def test_incremental_indexer_upsert_and_delete_file(self):
        store = InMemoryKnowledgeStore()
        indexer = IncrementalKnowledgeIndexer(store)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Test Doc\n\nHello incremental world.")
            tmp_path = f.name
        try:
            doc = indexer.upsert_file(tmp_path)
            self.assertEqual(doc.title, Path(tmp_path).stem)
            retrieved = store.get_document(Path(tmp_path).as_posix())
            self.assertIsNotNone(retrieved)
            self.assertIn("Hello incremental world", retrieved.content)

            removed = indexer.delete_file(tmp_path)
            self.assertTrue(removed)
            self.assertIsNone(store.get_document(Path(tmp_path).as_posix()))
        finally:
            os.unlink(tmp_path)

    def test_incremental_indexer_delete_nonexistent_returns_false(self):
        store = InMemoryKnowledgeStore()
        indexer = IncrementalKnowledgeIndexer(store)
        self.assertFalse(indexer.delete_file("/nonexistent/file.md"))

    def test_incremental_indexer_apply_changes_adds_and_removes(self):
        store = InMemoryKnowledgeStore()
        indexer = IncrementalKnowledgeIndexer(store)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Apply test")
            tmp_path = f.name
        try:
            created_event = FileChangeEvent(path=tmp_path, event_type="created", timestamp=time.time(), size_bytes=100)
            result = indexer.apply_changes([created_event])
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["deleted"], 0)

            mod_events = [FileChangeEvent(path=tmp_path, event_type="modified", timestamp=time.time(), size_bytes=120)]
            result2 = indexer.apply_changes(mod_events)
            self.assertEqual(result2["updated"], 1)

            del_event = FileChangeEvent(path=tmp_path, event_type="deleted", timestamp=time.time())
            result3 = indexer.apply_changes([del_event])
            self.assertEqual(result3["deleted"], 1)
        finally:
            os.unlink(tmp_path)

    def test_document_tracker_stale_detection(self):
        tracker = DocumentTracker()
        tracker.track("doc_a", time.time() - 86400 * 40)
        tracker.track("doc_b", time.time() - 86400 * 10)
        tracker.track("doc_c", time.time())
        stale = tracker.list_stale(ttl_days=30.0)
        self.assertIn("doc_a", stale)
        self.assertNotIn("doc_b", stale)
        self.assertNotIn("doc_c", stale)

    def test_document_tracker_snapshot(self):
        tracker = DocumentTracker()
        tracker.track("doc_a", 1000.0)
        snap = tracker.snapshot()
        self.assertEqual(snap["doc_a"], 1000.0)

    def test_directory_watcher_detects_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            watcher = DirectoryWatcher(tmp, interval_seconds=0.1)
            events = watcher.poll()
            self.assertEqual(events, [])
            new_file = Path(tmp) / "new.md"
            new_file.write_text("# hello", encoding="utf-8")
            events = watcher.poll({".md"})
            created = [e for e in events if e.event_type == "created" and "new.md" in e.path]
            self.assertEqual(len(created), 1)

    def test_directory_watcher_detects_deleted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_file = Path(tmp) / "temp.md"
            new_file.write_text("# temp", encoding="utf-8")
            watcher = DirectoryWatcher(tmp, interval_seconds=0.1)
            watcher.poll({".md"})
            os.unlink(new_file)
            events = watcher.poll({".md"})
            deleted = [e for e in events if e.event_type == "deleted" and "temp.md" in e.path]
            self.assertEqual(len(deleted), 1)

    def test_token_overlap_reranker_boosts_title_matches(self):
        chunk_a = KnowledgeChunk(chunk_id="c1", document_id="d1", text="hello world", metadata={})
        chunk_b = KnowledgeChunk(chunk_id="c2", document_id="d2", text="some other content here", metadata={})
        hits = [
            RetrievalHit(chunk=chunk_a, score=2.0, source_title="python guide"),
            RetrievalHit(chunk=chunk_b, score=2.5, source_title="random notes"),
        ]
        reranker = TokenOverlapReranker()
        result = reranker.rerank("python", hits, top_k=2)
        self.assertTrue(result[0].source_title == "python guide" or result[0].score > result[1].score)
        self.assertIn("rerank_score", result[0].chunk.metadata)
        self.assertIn("original_score", result[0].chunk.metadata)

    def test_dummy_reranker_preserves_order(self):
        chunk = type("Chunk", (), {"chunk_id": "c1", "document_id": "d1", "text": "test", "metadata": {}})()
        hits = [RetrievalHit(chunk=chunk, score=float(i), source_title=f"doc{i}") for i in range(3, 0, -1)]
        reranker = DummyReranker()
        result = reranker.rerank("any", hits, top_k=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].score, 3.0)

    def test_build_reranker_factory(self):
        self.assertIsInstance(build_reranker("dummy"), DummyReranker)
        self.assertIsInstance(build_reranker("token_overlap"), TokenOverlapReranker)
        self.assertIsInstance(build_reranker(""), TokenOverlapReranker)

    def test_obsidian_vault_connector_loads_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "note_a.md").write_text("Content A\n\n[[note_b]] link", encoding="utf-8")
            (vault / "note_b.md").write_text("Content B\n\nBacklink to [[note_a]]", encoding="utf-8")
            connector = ObsidianVaultConnector(vault)
            docs = connector.load_vault()
            self.assertEqual(len(docs), 2)
            titles = {d.title for d in docs}
            self.assertIn("note_a", titles)
            self.assertIn("note_b", titles)

    def test_obsidian_vault_connector_resolves_backlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "a.md").write_text("ref [[b]]", encoding="utf-8")
            (vault / "b.md").write_text("ref [[a]]", encoding="utf-8")
            connector = ObsidianVaultConnector(vault)
            docs = connector.load_vault()
            backlinks = connector.resolve_backlinks(docs)
            self.assertTrue(any("b" in bid for bid in backlinks))
            self.assertTrue(any("a" in bid for bid in backlinks))

    def test_in_memory_store_remove_document_cascades_to_chunks(self):
        store = InMemoryKnowledgeStore()
        from backend.knowledge.ingest import ingest_text_document
        ingest_text_document(store, document_id="d1", title="test", content="hello world", chunk_size=100, overlap=0)
        self.assertGreater(len(store.list_chunks()), 0)
        removed = store.remove_document("d1")
        self.assertTrue(removed)
        self.assertIsNone(store.get_document("d1"))
        self.assertEqual(len(store.list_chunks()), 0)

    def test_in_memory_store_list_stale_documents(self):
        store = InMemoryKnowledgeStore()
        now = time.time()
        doc1 = KnowledgeDocument(document_id="d1", title="A", content="a", expires_at=now - 100)
        doc2 = KnowledgeDocument(document_id="d2", title="B", content="b", expires_at=now + 86400)
        doc3 = KnowledgeDocument(document_id="d3", title="C", content="c")
        store.upsert_document(doc1)
        store.upsert_document(doc2)
        store.upsert_document(doc3)
        stale = store.list_stale_documents(now)
        self.assertIn("d1", stale)
        self.assertNotIn("d2", stale)
        self.assertNotIn("d3", stale)
