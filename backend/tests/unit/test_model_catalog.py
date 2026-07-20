import unittest

from backend.app.model_catalog import bundled_model_catalog


class ModelCatalogTests(unittest.TestCase):
    def test_bundled_catalog_is_mixed_provider_not_qwen_only(self):
        catalog = bundled_model_catalog()
        models = catalog["models"]
        providers = {item["provider"] for item in models}
        model_ids = {item["model_id"] for item in models}

        self.assertIn("anthropic", providers)
        self.assertIn("gemini", providers)
        self.assertIn("openai", providers)
        self.assertIn("deepseek", providers)
        self.assertIn("huggingface", providers)
        self.assertIn("qwen3.7-max", model_ids)
        self.assertIn("Qwen/Qwen3.6-27B", model_ids)
        self.assertEqual(models[0]["provider"], "anthropic")
        self.assertNotEqual(models[0]["model_id"].split("/", 1)[0].lower(), "qwen")
        qwen27 = next(item for item in models if item["model_id"] == "Qwen/Qwen3.6-27B")
        self.assertEqual(qwen27["metadata"]["local_role_policy"], "27b_specialist_candidate")
        self.assertIn("Q4_K_M", qwen27["metadata"]["quantization_profiles"])


if __name__ == "__main__":
    unittest.main()
