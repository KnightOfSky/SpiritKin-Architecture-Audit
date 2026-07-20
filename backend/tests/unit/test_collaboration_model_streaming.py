from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "collaboration_agent_worker.py"


def load_worker_module():
    spec = importlib.util.spec_from_file_location("collaboration_agent_worker_streaming", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load collaboration_agent_worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollaborationModelStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    def test_stream_openai_compatible_reply_emits_provider_tokens(self):
        provider = self.worker.ModelProviderConfig(
            "openai_compatible",
            "model-a",
            True,
            "http://provider.test/v1",
            "OPENAI_API_KEY",
            "Provider",
            "test",
            "key",
        )
        tokens: list[str] = []
        events: list[str] = []

        class FakeResponse:
            def __enter__(self):
                return iter(
                    [
                        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n',
                        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
                        b"data: [DONE]\n",
                    ]
                )

            def __exit__(self, *_args):
                return False

        with patch.object(self.worker.urllib.request, "urlopen", return_value=FakeResponse()):
            result = self.worker.stream_openai_compatible_reply(
                "prompt",
                provider,
                "model-a",
                timeout=3,
                on_token=lambda token, _meta: tokens.append(token),
                on_event=lambda lifecycle, _output, _meta: events.append(lifecycle),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["response_text"], "hello")
        self.assertEqual(tokens, ["hel", "lo"])
        self.assertIn("request_stream_started", events)

    def test_provider_reasoning_visibility_exposes_only_deepseek_process(self):
        self.assertEqual(
            self.worker.provider_reasoning_visibility("openai_compatible", "deepseek-v4-pro"),
            "process",
        )
        self.assertEqual(
            self.worker.provider_reasoning_visibility("lmstudio", "qwen/qwen3.6-35b-a3b"),
            "private",
        )

    def test_stream_ollama_reply_emits_provider_tokens(self):
        provider = self.worker.ModelProviderConfig(
            "ollama",
            "model-b",
            True,
            "http://127.0.0.1:11434",
            "OLLAMA_HOST",
            "Ollama",
            "test",
            "",
        )
        tokens: list[str] = []

        class FakeResponse:
            def __enter__(self):
                return iter(
                    [
                        b'{"message":{"content":"ni"},"done":false}\n',
                        b'{"message":{"content":"hao"},"done":false}\n',
                        b'{"done":true}\n',
                    ]
                )

            def __exit__(self, *_args):
                return False

        with patch.object(self.worker.urllib.request, "urlopen", return_value=FakeResponse()):
            result = self.worker.stream_ollama_reply(
                "prompt",
                provider,
                "model-b",
                timeout=3,
                on_token=lambda token, _meta: tokens.append(token),
                on_event=lambda *_args: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["response_text"], "nihao")
        self.assertEqual(tokens, ["ni", "hao"])

    def test_resolve_streaming_provider_prefers_model_match_over_name_match(self):
        # 事故场景：本地 LM Studio（qwen）与云端 DeepSeek 同属 openai 兼容家族，
        # 只按 provider 名匹配会把 qwen 模型名发到 DeepSeek 端点（HTTP 400）。
        lmstudio = self.worker.ModelProviderConfig(
            "lmstudio", "qwen/qwen3.6-35b-a3b", True, "http://127.0.0.1:1234/v1", "", "LM Studio", "assist_models", ""
        )
        deepseek = self.worker.ModelProviderConfig(
            "openai_compatible", "deepseek-v4-pro", True, "https://api.deepseek.com/v1", "", "deepseek", "assist_models", "key"
        )

        with patch.object(self.worker, "discover_model_providers", return_value=[deepseek, lmstudio]):
            qwen = self.worker.resolve_streaming_provider({}, provider="openai_compatible", model="qwen/qwen3.6-35b-a3b")
            self.assertEqual(qwen.endpoint, "http://127.0.0.1:1234/v1")

            ds = self.worker.resolve_streaming_provider({}, provider="openai_compatible", model="deepseek-v4-pro")
            self.assertEqual(ds.endpoint, "https://api.deepseek.com/v1")

            # 没有 model 精确匹配时保持旧行为：按 provider 名匹配。
            fallback = self.worker.resolve_streaming_provider({}, provider="openai_compatible", model="some-new-model")
            self.assertEqual(fallback.endpoint, "https://api.deepseek.com/v1")
            self.assertEqual(fallback.model, "some-new-model")

    def test_resolve_streaming_provider_model_match_does_not_cross_incompatible_families(self):
        anthropic = self.worker.ModelProviderConfig(
            "anthropic", "claude-3", True, "https://api.anthropic.com", "", "Anthropic", "env", "key"
        )
        lmstudio = self.worker.ModelProviderConfig(
            "lmstudio", "claude-3", True, "http://127.0.0.1:1234/v1", "", "LM Studio", "assist_models", ""
        )

        with patch.object(self.worker, "discover_model_providers", return_value=[lmstudio, anthropic]):
            selected = self.worker.resolve_streaming_provider({}, provider="anthropic", model="claude-3")
            self.assertEqual(selected.provider, "anthropic")

    def test_unscoped_streaming_prefers_llamacpp_over_lmstudio(self):
        lmstudio = self.worker.ModelProviderConfig(
            "lmstudio", "legacy", True, "http://127.0.0.1:1234/v1", "", "LM Studio", "providers", ""
        )
        llamacpp = self.worker.ModelProviderConfig(
            "llamacpp", "qwen", True, "http://127.0.0.1:8080/v1", "", "llama.cpp", "providers", ""
        )

        with patch.object(self.worker, "discover_model_providers", return_value=[lmstudio, llamacpp]):
            selected = self.worker.resolve_streaming_provider({}, provider="", model="")

        self.assertEqual(selected.provider, "llamacpp")
        self.assertEqual(selected.endpoint, "http://127.0.0.1:8080/v1")


if __name__ == "__main__":
    unittest.main()
