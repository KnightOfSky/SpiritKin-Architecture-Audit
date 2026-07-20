from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.orchestrator.resource_registry import (
    JsonResourceRegistryStore,
    ResourceRecord,
    ResourceRegistry,
    build_resource_registry_store,
    load_resource_registry,
    normalize_resource_id,
    register_runtime_resources,
    resource_from_worker_descriptor,
    save_resource_registry,
)
from backend.orchestrator.worker_pool import WorkerDescriptor


class ResourceRegistryTests(unittest.TestCase):
    def test_resource_registry_registers_and_filters_resources(self):
        registry = ResourceRegistry()
        registry.register(
            ResourceRecord(
                resource_id="Shop A",
                label="Douyin Shop A",
                resource_type="shop",
                platform="douyin",
                owner_agent="ecommerce",
                credential_ref="vault:douyin_shop_a",
                supported_capabilities=("commerce.product.publish", "commerce.price.update"),
                policies={"risk": "medium", "budget_daily": 1000},
                health_status="ready",
            )
        )
        registry.register(
            ResourceRecord(
                resource_id="repo:spiritkin",
                label="SpiritKinAI Repository",
                resource_type="repository",
                platform="git",
                owner_agent="programming",
                supported_capabilities=("code.generate", "git.diff"),
                health_status="ready",
            )
        )

        commerce = registry.list_records(owner_agent="ecommerce")
        price_resources = registry.list_records(capability_id="commerce.price.update")
        snapshot = registry.snapshot(resource_type="shop")

        self.assertEqual(commerce[0].resource_id, "shop_a")
        self.assertEqual(price_resources[0].label, "Douyin Shop A")
        self.assertEqual(snapshot["total"], 1)
        self.assertEqual(snapshot["type_counts"]["shop"], 1)
        self.assertEqual(snapshot["owner_counts"]["ecommerce"], 1)

    def test_resource_registry_merges_existing_resource(self):
        registry = ResourceRegistry(
            [
                ResourceRecord(
                    resource_id="phone:1",
                    label="Phone 1",
                    resource_type="device",
                    supported_capabilities=("android.tap",),
                    health_status="degraded",
                )
            ]
        )

        merged = registry.register(
            ResourceRecord(
                resource_id="phone:1",
                label="Android Phone 1",
                owner_agent="mobile_agent",
                supported_capabilities=("android.input",),
                health_status="ready",
            )
        )

        self.assertEqual(merged.label, "Android Phone 1")
        self.assertEqual(merged.owner_agent, "mobile_agent")
        self.assertEqual(merged.supported_capabilities, ("android.tap", "android.input"))
        self.assertEqual(merged.health_status, "ready")

    def test_resource_registry_reports_contract_gaps(self):
        registry = ResourceRegistry(
            [
                ResourceRecord(resource_id="shop:b", label="Shop B", credential_ref="plain:secret"),
            ]
        )

        gap_ids = {gap["gap_id"] for gap in registry.gaps()}

        self.assertIn("resource_owner_missing", gap_ids)
        self.assertIn("resource_capabilities_missing", gap_ids)
        self.assertIn("resource_credential_ref_weak", gap_ids)

    def test_resource_registry_persists_json_snapshot(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "resources.json"
            registry = ResourceRegistry(
                [
                    ResourceRecord(
                        resource_id="shop:a",
                        label="Douyin Shop A",
                        resource_type="shop",
                        platform="douyin",
                        owner_agent="ecommerce",
                        credential_ref="vault:shop_a",
                        supported_capabilities=("commerce.product.publish",),
                        health_status="ready",
                    )
                ]
            )

            saved = save_resource_registry(registry, path)
            loaded = load_resource_registry(path)

            self.assertEqual(saved["path"], str(path.resolve()))
            self.assertEqual(loaded.get("shop:a").label, "Douyin Shop A")
            self.assertEqual(loaded.get("shop:a").credential_ref, "vault:shop_a")

    def test_json_resource_registry_store_round_trips(self):
        with TemporaryDirectory() as tmp:
            store = JsonResourceRegistryStore(Path(tmp) / "resources.json")
            registry = ResourceRegistry(
                [
                    ResourceRecord(
                        resource_id="browser:profile-a",
                        label="Chrome Profile A",
                        resource_type="browser_profile",
                        platform="chrome",
                        owner_agent="main_text",
                        supported_capabilities=("browser_open_url",),
                    )
                ]
            )

            store.save(registry)
            reloaded = store.load()

            self.assertEqual(reloaded.get("browser:profile-a").resource_type, "browser_profile")

    def test_resource_from_worker_descriptor_projects_worker_as_resource(self):
        worker = WorkerDescriptor(
            worker_id="android:phone-1",
            label="Android Phone",
            worker_type="device_worker",
            capabilities=("android.tap", "android.input"),
            health_status="ready",
        )

        resource = resource_from_worker_descriptor(worker)

        self.assertEqual(resource.resource_id, "worker:android:phone-1")
        self.assertEqual(resource.resource_type, "worker")
        self.assertEqual(resource.platform, "device_worker")
        self.assertEqual(resource.supported_capabilities, ("android.tap", "android.input"))

    def test_resource_from_worker_descriptor_uses_namespace_target_and_operations(self):
        worker = WorkerDescriptor(
            worker_id="executor:browser_worker",
            label="browser_worker",
            worker_type="browser_worker",
            worker_subtype="local_browser_worker",
            capability_namespaces=("browser",),
            targets=("browser",),
            operations=("browser.health_check", "browser_open_url"),
            health_status="ready",
        )

        resource = resource_from_worker_descriptor(worker)

        self.assertIn("browser", resource.supported_capabilities)
        self.assertIn("browser_open_url", resource.supported_capabilities)
        self.assertNotIn("resource_capabilities_missing", {gap["gap_id"] for gap in ResourceRegistry([resource]).gaps()})

    def test_normalize_resource_id_keeps_namespaced_ids(self):
        self.assertEqual(normalize_resource_id("Repo:SpiritKin AI"), "repo:spiritkin_ai")

    def test_build_resource_registry_store_requires_path_or_env(self):
        with patch.dict(os.environ, {"SPIRITKIN_RESOURCE_REGISTRY_PATH": ""}):
            self.assertIsNone(build_resource_registry_store(None))
        with TemporaryDirectory() as tmp:
            store = build_resource_registry_store(Path(tmp) / "resources.json")
            self.assertIsInstance(store, JsonResourceRegistryStore)

    def test_register_runtime_resources_registers_runtime_records(self):
        registry = ResourceRegistry()
        worker = WorkerDescriptor(
            worker_id="executor:python_worker",
            label="Python Worker",
            worker_type="python_worker",
            capabilities=("python.run",),
        )

        register_runtime_resources(
            registry,
            workers=[worker],
            device_name="pc-1",
            device_ready=True,
            projects=[{"project_id": "p1", "goal": "sell"}, "not-a-dict", {}],
        )

        ids = {record.resource_id for record in registry.list_records()}
        self.assertIn("worker:executor:python_worker", ids)
        self.assertIn("device:local_pc", ids)
        self.assertIn("commerce_project:p1", ids)
        self.assertTrue(any(record_id.startswith("repo:") for record_id in ids))
        self.assertEqual(registry.get("device:local_pc").health_status, "ready")


if __name__ == "__main__":
    unittest.main()
