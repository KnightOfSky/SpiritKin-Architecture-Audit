from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.knowledge.base import BaseEmbeddingProvider
from backend.knowledge.embedding_eval import (
    evaluate_embedding_provider,
    load_latest_embedding_eval_report,
    write_embedding_eval_report,
)


class FixtureEmbeddingProvider(BaseEmbeddingProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        source = text.lower()
        if "代码" in source or "python" in source or "运行" in source:
            return [1.0, 0.0, 0.0]
        if "花" in source or "植物" in source or "浇水" in source:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def snapshot(self):
        return {"provider": "fixture", "degraded": False}


class DegradedFixtureEmbeddingProvider(FixtureEmbeddingProvider):
    def snapshot(self):
        return {"provider": "fixture", "degraded": True, "degraded_reason": "test fallback"}


DATASET = {
    "name": "unit",
    "thresholds": {"recall_at_1": 1.0, "recall_at_k": 1.0, "mrr": 1.0},
    "documents": [
        {"id": "code", "text": "Python 代码运行性能"},
        {"id": "garden", "text": "花园植物养护"},
    ],
    "queries": [
        {"id": "q1", "text": "程序怎样运行更快", "relevant": ["code"]},
        {"id": "q2", "text": "植物多久浇水", "relevant": ["garden"]},
    ],
}


class EmbeddingEvalTests(unittest.TestCase):
    def test_evaluation_reports_recall_mrr_and_passes_thresholds(self):
        report = evaluate_embedding_provider(FixtureEmbeddingProvider(), DATASET, top_k=2)

        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["recall_at_1"], 1.0)
        self.assertEqual(report["metrics"]["recall_at_k"], 1.0)
        self.assertEqual(report["metrics"]["mrr"], 1.0)
        self.assertEqual(report["metrics"]["dimensions"], 3)

    def test_degraded_provider_fails_gate_unless_explicitly_allowed(self):
        failed = evaluate_embedding_provider(DegradedFixtureEmbeddingProvider(), DATASET)
        allowed = evaluate_embedding_provider(DegradedFixtureEmbeddingProvider(), DATASET, allow_degraded=True)

        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["non_degraded"])
        self.assertTrue(allowed["passed"])

    def test_report_round_trip_exposes_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest.json"
            report = evaluate_embedding_provider(FixtureEmbeddingProvider(), DATASET)
            written = write_embedding_eval_report(report, path)
            loaded = load_latest_embedding_eval_report(path)

        self.assertEqual(written, path.resolve())
        self.assertEqual(loaded["status"], "passed")
        self.assertTrue(loaded["report"]["passed"])


if __name__ == "__main__":
    unittest.main()
