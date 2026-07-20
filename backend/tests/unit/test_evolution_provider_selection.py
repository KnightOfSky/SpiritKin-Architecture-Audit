import unittest
from unittest.mock import patch

from backend.app.evolution_management import _select_text_provider, _select_vision_provider
from backend.app.learning_workflow import ModelProviderConfig


class EvolutionProviderSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.providers = [
            ModelProviderConfig(
                "lmstudio",
                "legacy-model",
                True,
                "http://127.0.0.1:1234/v1",
                "LMSTUDIO_BASE_URL",
                "LM Studio",
                "assist_models",
                "lm-studio",
            ),
            ModelProviderConfig(
                "llamacpp",
                "qwen/qwen3.6-35b-a3b",
                True,
                "http://127.0.0.1:8080/v1",
                "LLAMACPP_BASE_URL",
                "llama.cpp",
                "local_state",
                "",
            ),
        ]

    @patch("backend.app.evolution_management.resolve_text_provider", return_value="llamacpp")
    @patch("backend.app.evolution_management.discover_model_providers")
    def test_text_extraction_prefers_configured_llamacpp(self, discover, _resolve) -> None:
        discover.return_value = self.providers

        selected = _select_text_provider({})

        self.assertEqual(selected.provider, "llamacpp")
        self.assertEqual(selected.endpoint, "http://127.0.0.1:8080/v1")

    @patch("backend.app.evolution_management.resolve_vision_provider", return_value="llamacpp")
    @patch("backend.app.evolution_management.discover_model_providers")
    def test_vision_extraction_prefers_configured_llamacpp(self, discover, _resolve) -> None:
        discover.return_value = self.providers

        selected = _select_vision_provider({})

        self.assertEqual(selected.provider, "llamacpp")
        self.assertEqual(selected.endpoint, "http://127.0.0.1:8080/v1")


if __name__ == "__main__":
    unittest.main()
