from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.evaluation.self_improvement import SelfTrainingPackage, TrainingExample
from backend.model.training import (
    HardwareProfile,
    TrainingBuildOptions,
    build_cloud_training_package,
    build_training_command,
    build_training_dataset_from_documents,
    build_training_dataset_from_files,
    detect_local_hardware_profile,
    export_self_training_dataset,
    recommend_training_recipe,
)


class TrainingWorkbenchTests(unittest.TestCase):
    def test_recommends_conservative_qlora_for_8gb_vram(self):
        recipe = recommend_training_recipe(HardwareProfile(gpu_name="RTX 5060 Ti", vram_gb=8))

        self.assertEqual(recipe.method, "qlora")
        self.assertEqual(recipe.max_model_size, "3B")
        self.assertEqual(recipe.quantization, "4bit")

    def test_recommends_7b_qlora_for_16gb_vram(self):
        recipe = recommend_training_recipe(HardwareProfile(gpu_name="RTX 5060 Ti", vram_gb=16))

        self.assertEqual(recipe.method, "qlora")
        self.assertEqual(recipe.max_model_size, "7B")
        self.assertEqual(recipe.max_seq_length, 4096)

    def test_exports_self_training_package_as_chat_jsonl(self):
        package = SelfTrainingPackage(
            package_id="pkg-1",
            generated_at=1.0,
            purpose="test",
            examples=[
                TrainingExample(
                    example_id="ex-1",
                    source="trajectory",
                    task_type="regression_eval",
                    input_text="打开默认浏览器",
                    expected_behavior="应路由到 launch_app 工具。",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.jsonl"
            result = export_self_training_dataset(package, path)
            row = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(result.count, 1)
        self.assertEqual(result.task_types, {"regression_eval": 1})
        self.assertEqual(row["messages"][1]["content"], "打开默认浏览器")
        self.assertEqual(row["metadata"]["source"], "trajectory")

    def test_builds_unsloth_training_command(self):
        recipe = recommend_training_recipe(HardwareProfile(vram_gb=16))

        command = build_training_command(
            dataset_path="data/train.jsonl",
            output_dir="runs/lora",
            base_model="Qwen/Qwen2.5-3B-Instruct",
            recipe=recipe,
        )

        self.assertIn("backend.model.training.unsloth_lora_train", command)
        self.assertIn("--load-in-4bit", command)
        self.assertIn("4096", command)

    def test_peft_training_command_fails_explicitly_until_trainer_exists(self):
        recipe = recommend_training_recipe(HardwareProfile(vram_gb=16))

        with self.assertRaises(ValueError) as ctx:
            build_training_command(
                dataset_path="data/train.jsonl",
                output_dir="runs/lora",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                recipe=recipe,
                trainer="peft",
            )

        self.assertIn("PEFT training is intentionally not exposed", str(ctx.exception))
        self.assertIn("peft_lora_train", str(ctx.exception))

    def test_builds_cloud_training_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "train.jsonl"
            dataset.write_text('{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"ok"}]}\n', encoding="utf-8")

            package = build_cloud_training_package(
                dataset_path=dataset,
                base_model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
                package_id="unit-package",
                package_root=root / "packages",
            )

            snapshot = package.snapshot()
            self.assertEqual(snapshot["package_id"], "unit-package")
            self.assertTrue(Path(snapshot["manifest_path"]).exists())
            self.assertIn("backend.model.training.unsloth_lora_train", snapshot["command"])

    def test_builds_training_dataset_from_uploaded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc.md").write_text("# OpenClaw\n\n机械臂回零前需要确认。", encoding="utf-8")
            (root / "notes.jsonl").write_text('{"text":"打开默认浏览器应路由到 app.launch"}\n', encoding="utf-8")
            (root / "image.png").write_bytes(b"not text")
            output = root / "training.jsonl"

            report = build_training_dataset_from_files([root], output, options=TrainingBuildOptions(chunk_chars=500))
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(report.source_count, 2)
        self.assertEqual(report.example_count, 2)
        self.assertTrue(any(item["reason"] == "unsupported_suffix" for item in report.skipped))
        self.assertIn("messages", rows[0])
        self.assertIn("source_path", rows[0]["metadata"])

    def test_builds_training_dataset_from_uploaded_document_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "uploaded.jsonl"
            report = build_training_dataset_from_documents(
                [
                    {"path": "docs/voice.md", "text": "语音识别需要 LLM 纠错和回声抑制。"},
                    {"path": "image.png", "text": "not supported"},
                    {"path": "empty.txt", "text": ""},
                ],
                output,
                options=TrainingBuildOptions(chunk_chars=500),
            )
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(report.source_count, 1)
        self.assertEqual(report.example_count, 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(item["reason"] == "unsupported_suffix" for item in report.skipped))
        self.assertTrue(any(item["reason"] == "empty" for item in report.skipped))

    def test_detect_local_hardware_profile_defaults_to_user_gpu(self):
        profile = detect_local_hardware_profile({})

        self.assertEqual(profile.gpu_name, "RTX 5060 Ti")
        self.assertEqual(profile.vram_gb, 16)


if __name__ == "__main__":
    unittest.main()
