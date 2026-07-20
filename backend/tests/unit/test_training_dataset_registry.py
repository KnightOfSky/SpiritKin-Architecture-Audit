from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.model.training.dataset_registry import (
    evaluate_dataset_gate,
    load_dataset_registry,
    register_training_dataset,
)


class TrainingDatasetRegistryTests(unittest.TestCase):
    def test_registers_verified_chat_jsonl_dataset_card(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "train.jsonl"
            registry = root / "datasets.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "打开浏览器"},
                            {"role": "assistant", "content": "应路由到 browser.open_url。"},
                        ],
                        "metadata": {"task_type": "tool_routing", "source": "unit"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            gate = evaluate_dataset_gate(dataset)
            card = register_training_dataset(dataset, source="unit", registry_path=registry, gate=gate)
            snapshot = load_dataset_registry(registry_path=registry)

        self.assertTrue(gate.allowed)
        self.assertEqual(card.status, "training_ready")
        self.assertEqual(card.example_count, 1)
        self.assertEqual(card.task_types, {"tool_routing": 1})
        self.assertEqual(snapshot["dataset_count"], 1)
        self.assertEqual(snapshot["datasets"][0]["dataset_id"], card.dataset_id)

    def test_gate_rejects_secret_like_content(self):
        with TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "train.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "use token"},
                            {"role": "assistant", "content": "api_key=super-secret-token-value"},
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            gate = evaluate_dataset_gate(dataset)

        self.assertFalse(gate.allowed)
        self.assertIn("secret_like_content", {item["code"] for item in gate.issues})


if __name__ == "__main__":
    unittest.main()
