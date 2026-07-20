from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.composer_model_selection import canonicalize_composer_metadata
from backend.app.learning_workflow import AssistModelSettings
from backend.app.runtime import ManagedRouteLlmClient


class ComposerModelSelectionTests(unittest.TestCase):
    def test_canonicalizes_configured_model_without_exposing_key(self):
        model = AssistModelSettings(
            model_id="configured-model",
            display_name="Configured Model",
            provider="openai_compatible",
            endpoint="https://api.example.test/v1",
            model="provider-model-name",
            api_key="secret-key",
        )

        with patch("backend.app.composer_model_selection.load_assist_models", return_value=[model]):
            metadata = canonicalize_composer_metadata(
                {"model_id": "configured-model", "reasoning_effort": "HIGH"}
            )

        self.assertEqual(metadata["model_name"], "provider-model-name")
        self.assertEqual(metadata["reasoning_effort"], "high")
        self.assertEqual(metadata["model_selection_status"], "configured")
        self.assertNotIn("api_key", metadata)

    def test_invalid_or_stale_model_falls_back_to_runtime_route(self):
        with patch("backend.app.composer_model_selection.load_assist_models", return_value=[]):
            metadata = canonicalize_composer_metadata(
                {"model_id": "old-fake-preset", "reasoning_effort": "extreme"}
            )

        self.assertEqual(metadata["model_id"], "")
        self.assertEqual(metadata["model_source"], "runtime_route")
        self.assertEqual(metadata["reasoning_effort"], "auto")
        self.assertEqual(metadata["model_selection_status"], "fallback_unavailable")
        self.assertEqual(metadata["requested_model_id"], "old-fake-preset")

    def test_managed_client_routes_selected_model_endpoint_and_reasoning(self):
        model = AssistModelSettings(
            model_id="configured-model",
            display_name="Configured Model",
            provider="openai_compatible",
            endpoint="https://api.example.test/v1",
            model="provider-model-name",
            api_key="secret-key",
        )
        calls = []

        def base_client(prompt, **kwargs):
            calls.append(kwargs)
            return "ok"

        client = ManagedRouteLlmClient(base_client)
        with patch("backend.app.runtime.resolve_configured_assist_model", return_value=model):
            result = client(
                "hello",
                model_id="configured-model",
                reasoning_effort="high",
                agent_name="main_text",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(calls[0]["provider"], "openai_compatible")
        self.assertEqual(calls[0]["model_name"], "provider-model-name")
        self.assertEqual(calls[0]["base_url"], "https://api.example.test/v1")
        self.assertEqual(calls[0]["api_key"], "secret-key")
        self.assertEqual(calls[0]["reasoning_effort"], "high")

    def test_managed_client_falls_back_to_enabled_route_member_when_model_is_unavailable(self):
        calls = []

        def base_client(prompt, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("model unavailable")
            return "fallback-ok"

        route = {
            "enabled": True,
            "primary_text": {"provider": "primary", "model": "primary-model"},
            "profile": {
                "members": [
                    {"provider": "primary", "model": "primary-model", "enabled": True, "weight": 1.0},
                    {"provider": "fallback", "model": "fallback-model", "enabled": True, "weight": 0.8},
                ]
            },
        }
        client = ManagedRouteLlmClient(base_client)
        with patch("backend.app.runtime.resolve_configured_assist_model", return_value=None), patch(
            "backend.app.runtime.build_active_route_runtime_snapshot", return_value=route
        ):
            result = client("hello")

        self.assertEqual(result, "fallback-ok")
        self.assertEqual(calls[1]["provider"], "fallback")
        self.assertEqual(calls[1]["model_name"], "fallback-model")

    def test_managed_client_falls_back_when_provider_reports_missing_model_as_404(self):
        calls = []

        def base_client(prompt, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("API error: HTTP Error 404: model 'primary-model' not found")
            return "fallback-ok"

        route = {
            "enabled": True,
            "primary_text": {"provider": "primary", "model": "primary-model"},
            "profile": {
                "members": [
                    {"provider": "primary", "model": "primary-model", "enabled": True, "weight": 1.0},
                    {"provider": "fallback", "model": "fallback-model", "enabled": True, "weight": 0.8},
                ]
            },
        }
        client = ManagedRouteLlmClient(base_client)
        with patch("backend.app.runtime.resolve_configured_assist_model", return_value=None), patch(
            "backend.app.runtime.build_active_route_runtime_snapshot", return_value=route
        ):
            result = client("hello")

        self.assertEqual(result, "fallback-ok")
        self.assertEqual(calls[1]["model_name"], "fallback-model")


if __name__ == "__main__":
    unittest.main()
