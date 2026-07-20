from __future__ import annotations

import unittest

from backend.orchestrator.brain_router import BrainRouter
from backend.orchestrator.model_call_coordinator import ModelCallCoordinator


class ModelCallCoordinatorTests(unittest.TestCase):
    def test_primary_call_uses_validated_composer_model_and_reasoning(self):
        calls = []
        metadata = {
            "model_selection_validated": True,
            "model_id": "configured-id",
            "model_provider": "openai_compatible",
            "model_name": "provider-model",
            "reasoning_effort": "high",
        }
        coordinator = ModelCallCoordinator(
            lambda prompt, **kwargs: calls.append((prompt, kwargs)) or "ok",
            BrainRouter(),
            input_metadata=lambda: metadata,
        )

        result = coordinator.call("hello", agent_name="main_text")

        self.assertEqual(result, "ok")
        self.assertEqual(calls[0][1]["provider"], "openai_compatible")
        self.assertEqual(calls[0][1]["model_name"], "provider-model")
        self.assertEqual(calls[0][1]["model_id"], "configured-id")
        self.assertEqual(calls[0][1]["reasoning_effort"], "high")
        self.assertEqual(calls[0][1]["brain_profile"], "composer_configured-id")

    def test_composer_override_does_not_replace_specialist_profile(self):
        calls = []
        router = BrainRouter(
            agent_profiles={
                "programming": {
                    "provider": "ollama",
                    "model": "qwen-coder",
                    "brain_profile": "programming-local",
                }
            }
        )
        coordinator = ModelCallCoordinator(
            lambda prompt, **kwargs: calls.append(kwargs) or "ok",
            router,
            input_metadata=lambda: {
                "model_selection_validated": True,
                "model_id": "main-cloud",
                "model_provider": "openai_compatible",
                "model_name": "main-model",
                "reasoning_effort": "high",
            },
        )

        coordinator.call("你是编程助理，请检查代码")

        self.assertEqual(calls[0]["agent_name"], "programming")
        self.assertEqual(calls[0]["provider"], "ollama")
        self.assertEqual(calls[0]["model_name"], "qwen-coder")
        self.assertNotIn("model_id", calls[0])
        self.assertNotIn("reasoning_effort", calls[0])

    def test_unvalidated_model_id_is_not_forwarded(self):
        calls = []
        coordinator = ModelCallCoordinator(
            lambda prompt, **kwargs: calls.append(kwargs) or "ok",
            BrainRouter(),
            input_metadata=lambda: {
                "model_selection_validated": False,
                "model_id": "untrusted-id",
                "reasoning_effort": "low",
            },
        )

        coordinator.call("hello", agent_name="main_text")

        self.assertNotIn("model_id", calls[0])
        self.assertEqual(calls[0]["reasoning_effort"], "low")

    def test_mobile_fast_profile_is_forwarded_with_bounded_token_limit(self):
        calls = []
        coordinator = ModelCallCoordinator(
            lambda prompt, **kwargs: calls.append(kwargs) or "ok",
            BrainRouter(),
            input_metadata=lambda: {
                "input_channel": "ios",
                "text_mode": "fast",
                "max_new_tokens": 96,
                "reasoning_effort": "none",
                "model_timeout_seconds": 90,
            },
        )

        coordinator.call("hello")

        self.assertEqual(calls[0]["mode"], "fast")
        self.assertEqual(calls[0]["max_new_tokens"], 96)
        self.assertEqual(calls[0]["reasoning_effort"], "none")
        self.assertEqual(calls[0]["request_timeout"], 90)

    def test_mobile_tuning_applies_to_router_and_specialist_calls(self):
        calls = []
        coordinator = ModelCallCoordinator(
            lambda prompt, **kwargs: calls.append(kwargs) or "ok",
            BrainRouter(
                agent_profiles={
                    "programming": {
                        "provider": "ollama",
                        "model": "qwen-coder",
                        "brain_profile": "programming-local",
                    }
                }
            ),
            input_metadata=lambda: {
                "input_channel": "ios",
                "text_mode": "fast",
                "max_new_tokens": 96,
                "reasoning_effort": "none",
                "model_timeout_seconds": 90,
            },
        )

        coordinator.call("你是编程助理，请检查代码")

        self.assertEqual(calls[0]["agent_name"], "programming")
        self.assertEqual(calls[0]["mode"], "fast")
        self.assertEqual(calls[0]["max_new_tokens"], 96)
        self.assertEqual(calls[0]["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
