import json
import tempfile
import unittest
from pathlib import Path

from backend.orchestrator.ecommerce_task_queue import extract_pdd_link
from backend.skills import SkillRegistry, SkillRunner, SkillSpec, SkillStepSpec
from backend.tools import ToolCall, ToolRegistry, build_default_tool_registry
from backend.tools.ecommerce_task_queue_tools import get_ecommerce_task_queue_tools


class EcommerceTaskQueueToolTests(unittest.TestCase):
    def test_queue_accepts_web_link_and_rejects_miniapp_only_text(self):
        web_link = "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283"

        self.assertEqual(extract_pdd_link(f"分享文本 {web_link}"), web_link)
        self.assertEqual(extract_pdd_link("#小程序://拼多多/UhecnYM1HJR3d5i"), "")

    def test_default_registry_exposes_browser_extension_productdata_tool(self):
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.list_specs()}

        self.assertIn("ecommerce.task_queue.status", names)
        self.assertIn("ecommerce.task_queue.ingest_mobile_links", names)
        self.assertIn("ecommerce.task_queue.attach_productdata", names)
        self.assertNotIn("ecommerce.task_queue.run_adapter", names)
        self.assertIn("ecommerce.task_queue.cleanup_temp", names)

    def test_ingest_mobile_links_skips_test_links_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            links.write_text(
                json.dumps(
                    {
                        "link": "https://mobile.yangkeduo.com/goods.html?ps=localtest",
                        "source": "local-self-test",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            registry = ToolRegistry(get_ecommerce_task_queue_tools())

            result = registry.invoke(
                ToolCall(
                    "ecommerce.task_queue.ingest_mobile_links",
                    {"project_root": str(root), "links_jsonl": str(links)},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["task_count"], 0)
            self.assertEqual(result.data["ignored"], 1)

    def test_skill_runner_imports_phone_web_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            links.write_text(
                json.dumps(
                    {
                        "link": "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                        "source": "android-bridge",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            skill = SkillSpec(
                name="ecommerce.pdd_mobile_link_intake.workflow",
                description="import links",
                input_schema={"project_root": "str", "links_jsonl": "str", "include_latest": "bool"},
                steps=(
                    SkillStepSpec(
                        "ecommerce.task_queue.ingest_mobile_links",
                        {
                            "project_root": "{{project_root}}",
                            "links_jsonl": "{{links_jsonl}}",
                            "include_latest": "{{include_latest}}",
                        },
                    ),
                ),
                tool_allowlist=("ecommerce.task_queue.ingest_mobile_links",),
                metadata={"status": "candidate"},
            )
            runner = SkillRunner(SkillRegistry([skill]), ToolRegistry(get_ecommerce_task_queue_tools()))

            dry = runner.run(
                skill.name,
                {"project_root": str(root), "links_jsonl": str(links), "include_latest": False},
                dry_run=True,
            )
            live = runner.run(
                skill.name,
                {"project_root": str(root), "links_jsonl": str(links), "include_latest": False},
            )

            self.assertTrue(dry.success)
            self.assertTrue(live.success)
            self.assertEqual(live.step_results[0].data["task_count"], 1)

    def test_skill_runner_attaches_browser_extension_productdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            links.write_text(
                json.dumps(
                    {
                        "link": "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                        "source": "android-bridge",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            product_data = root / "product.json"
            product_data.write_text(
                json.dumps(
                    {
                        "goodsId": "680378531283",
                        "listingGate": {"ok": False, "missing": ["skuStockComplete"], "checks": {}},
                    }
                ),
                encoding="utf-8",
            )
            tools = ToolRegistry(get_ecommerce_task_queue_tools())
            ingest = tools.invoke(
                ToolCall(
                    "ecommerce.task_queue.ingest_mobile_links",
                    {"project_root": str(root), "links_jsonl": str(links)},
                )
            )
            task_id = ingest.data["created"][0]
            skill = SkillSpec(
                name="ecommerce.browser_extension_productdata.workflow",
                description="attach browser productData",
                input_schema={"project_root": "str", "task_id": "str", "product_data_json": "str"},
                steps=(
                    SkillStepSpec(
                        "ecommerce.task_queue.attach_productdata",
                        {
                            "project_root": "{{project_root}}",
                            "task_id": "{{task_id}}",
                            "product_data_json": "{{product_data_json}}",
                            "control_plane_artifact_id": "{{control_plane_artifact_id}}",
                        },
                    ),
                ),
                tool_allowlist=("ecommerce.task_queue.attach_productdata",),
                metadata={"status": "candidate"},
            )
            runner = SkillRunner(SkillRegistry([skill]), tools)
            inputs = {
                "project_root": str(root),
                "task_id": task_id,
                "product_data_json": str(product_data),
                "control_plane_artifact_id": "art_product_1",
            }

            dry = runner.run(skill.name, inputs, dry_run=True)
            live = runner.run(skill.name, inputs)

            self.assertTrue(dry.success)
            self.assertTrue(live.success)
            result = live.step_results[0].data
            self.assertEqual(result["task"]["status"], "productdata_ready_with_gaps")
            self.assertFalse(result["validation"]["listingGate"]["ok"])
            artifact_path = Path(result["artifact"]["path"])
            if not artifact_path.is_absolute():
                artifact_path = root / artifact_path
            self.assertTrue(artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
