import json
import unittest
from unittest.mock import patch

import backend.services.conversation_engine as conversation_engine


class ConversationEngineTests(unittest.TestCase):
    def setUp(self):
        self._old_engine = conversation_engine._qwen_engine
        self._old_engine_key = conversation_engine._qwen_engine_key
        self._old_error_key = conversation_engine._qwen_engine_error_key
        self._old_error = conversation_engine._qwen_engine_error
        conversation_engine._qwen_engine = None
        conversation_engine._qwen_engine_key = None
        conversation_engine._qwen_engine_error_key = None
        conversation_engine._qwen_engine_error = None

    def tearDown(self):
        conversation_engine._qwen_engine = self._old_engine
        conversation_engine._qwen_engine_key = self._old_engine_key
        conversation_engine._qwen_engine_error_key = self._old_error_key
        conversation_engine._qwen_engine_error = self._old_error

    def test_failed_preload_is_cached_and_not_retried_on_chat(self):
        with patch.dict("os.environ", {"SPIRITKIN_STRICT_LLM_UNAVAILABLE": "1"}), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="local_transformers"), \
             patch.object(conversation_engine, "resolve_text_model", return_value="missing-qwen"), \
             patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "QwenLocalEngine", side_effect=RuntimeError("missing files")) as engine_cls:
            self.assertFalse(conversation_engine.preload_llm_engine())
            reply = conversation_engine.get_llm_response("你好")

        self.assertIn("missing-qwen", reply)
        self.assertIn("本次加载错误", reply)
        self.assertEqual(engine_cls.call_count, 1)

    def test_failed_preload_falls_back_to_realtime_reply_by_default(self):
        prompt = "上下文：当前输入：现在能听到我说话吗\n回答："
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="local_transformers"), \
             patch.object(conversation_engine, "resolve_text_model", return_value="missing-qwen"), \
             patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "QwenLocalEngine", side_effect=RuntimeError("missing files")) as engine_cls:
            self.assertFalse(conversation_engine.preload_llm_engine())
            reply = conversation_engine.get_llm_response(prompt)

        self.assertIn("我听到了", reply)
        self.assertIn("麦克风输入", reply)
        self.assertIn("<emotion:happy>", reply)
        self.assertNotIn("本次加载错误", reply)
        self.assertEqual(engine_cls.call_count, 1)

    def test_openai_compatible_engine_uses_generation_profile(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={"mode": "fast", "temperature": 0.1, "top_p": 0.7, "max_new_tokens": 64}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine("qwen-test", base_url="http://localhost:1234/v1", announce=False)
            self.assertEqual(engine.chat("你好", mode="fast"), "ok")

        self.assertEqual(captured["body"]["temperature"], 0.1)
        self.assertEqual(captured["body"]["top_p"], 0.7)
        self.assertEqual(captured["body"]["max_tokens"], 64)
        self.assertNotEqual(captured["body"]["max_tokens"], 4096)

    def test_openai_compatible_engine_streams_visible_sse_tokens(self):
        captured = {}

        class FakeStreamResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def __iter__(self):
                rows = [
                    'data: {"choices":[{"delta":{"content":"你"}}]}\n',
                    'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n',
                    'data: {"choices":[{"delta":{"content":"好"}}]}\n',
                    'data: [DONE]\n',
                ]
                return iter(row.encode("utf-8") for row in rows)

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeStreamResponse()

        batches = []
        reasoning_batches = []
        with patch.dict("os.environ", {"SPIRITKIN_REASONING_EFFORT": ""}, clear=False), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine("deepseek-test", base_url="https://api.example.test/v1", announce=False)
            result = engine.chat(
                "你好",
                on_token=lambda token, accumulated: batches.append((token, accumulated)),
                on_reasoning=lambda token, accumulated: reasoning_batches.append((token, accumulated)),
            )

        self.assertTrue(captured["body"]["stream"])
        self.assertNotIn("reasoning_effort", captured["body"])
        self.assertEqual(result, "你好")
        self.assertEqual(batches, [("你", "你"), ("好", "你好")])
        self.assertEqual(reasoning_batches, [("hidden", "hidden")])
        self.assertNotIn("hidden", result)

    def test_openai_compatible_engine_forwards_explicit_reasoning_effort(self):
        captured = {}

        class FakeStreamResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def __iter__(self):
                return iter([
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b'data: [DONE]\n',
                ])

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeStreamResponse()

        with patch.dict("os.environ", {"SPIRITKIN_REASONING_EFFORT": "low"}, clear=False), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine(
                "reasoning-test",
                base_url="https://api.example.test/v1",
                announce=False,
            )
            self.assertEqual(engine.chat("test", on_token=lambda *_: None), "ok")

        self.assertEqual(captured["body"]["reasoning_effort"], "low")

    def test_openai_compatible_engine_per_call_reasoning_overrides_environment(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with patch.dict("os.environ", {"SPIRITKIN_REASONING_EFFORT": "low"}, clear=False), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine(
                "reasoning-test",
                base_url="https://api.example.test/v1",
                announce=False,
            )
            self.assertEqual(engine.chat("test", reasoning_effort="high"), "ok")

        self.assertEqual(captured["body"]["reasoning_effort"], "high")

    def test_llamacpp_none_reasoning_disables_thinking_template(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine(
                "qwen-test",
                base_url="http://127.0.0.1:8080/v1",
                announce=False,
            )
            self.assertEqual(engine.chat("test", reasoning_effort="none"), "ok")

        self.assertEqual(captured["body"]["reasoning_effort"], "none")
        self.assertEqual(captured["body"]["chat_template_kwargs"], {"enable_thinking": False})

    def test_openai_compatible_engine_sends_authorization_header(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["authorization"] = req.get_header("Authorization")
            return FakeResponse()

        with patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine("qwen-test", base_url="http://localhost:1234/v1", api_key="runtime-key", announce=False)
            self.assertEqual(engine.chat("你好"), "ok")

        self.assertEqual(captured["authorization"], "Bearer runtime-key")

    def test_preload_openai_compatible_passes_resolved_api_key(self):
        created = []

        class FakeEngine:
            def __init__(self, model_name, *, base_url="", api_key="", announce=True):
                created.append({"model_name": model_name, "base_url": base_url, "api_key": api_key})

            def chat(self, prompt, **kwargs):
                return "ok"

        with patch.object(conversation_engine, "resolve_text_provider", return_value="openai_compatible"), \
             patch.object(conversation_engine, "resolve_text_model", return_value="qwen-cloud"), \
             patch.object(conversation_engine, "resolve_text_base_url", return_value="https://api.example.test/v1"), \
             patch.object(conversation_engine, "resolve_text_api_key", return_value="resolved-key"), \
             patch.object(conversation_engine, "OpenAICompatibleEngine", FakeEngine):
            self.assertTrue(conversation_engine.preload_llm_engine(config_path="config/config.yaml"))

        self.assertEqual(created, [{"model_name": "qwen-cloud", "base_url": "https://api.example.test/v1", "api_key": "resolved-key"}])

    def test_openai_compatible_engine_uses_configurable_request_timeout(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict("os.environ", {"SPIRITKIN_LLM_REQUEST_TIMEOUT": "2.5"}), \
             patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine("qwen-test", base_url="http://localhost:1234/v1", announce=False)
            self.assertEqual(engine.chat("你好"), "ok")

        self.assertEqual(captured["timeout"], 2.5)

    def test_openai_compatible_engine_uses_short_fallback_timeout(self):
        captured = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"message": {"content": "fallback ok"}}).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured.append((req.full_url, timeout))
            if len(captured) == 1:
                raise TimeoutError("primary timed out")
            return FakeResponse()

        with patch.dict("os.environ", {"SPIRITKIN_LLM_REQUEST_TIMEOUT": "3", "SPIRITKIN_LLM_FALLBACK_TIMEOUT": "1.5"}), \
             patch.object(conversation_engine, "show_emotion", lambda *args, **kwargs: None), \
             patch.object(conversation_engine, "resolve_text_generation_profile", return_value={}), \
             patch.object(conversation_engine, "resolve_text_model", return_value="fallback-qwen"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            engine = conversation_engine.OpenAICompatibleEngine("qwen-test", base_url="http://localhost:1234/v1", announce=False)
            self.assertEqual(engine.chat("你好"), "fallback ok")

        self.assertEqual(captured[0][1], 3.0)
        self.assertEqual(captured[1], ("http://localhost:11434/api/chat", 1.5))

    def test_llamacpp_provider_normalizes_to_openai_compatible(self):
        for alias in ("llamacpp", "llama_cpp", "llama.cpp", "llama-cpp", "LlamaCPP"):
            self.assertEqual(
                conversation_engine._normalize_provider_for_text_engine(alias),
                "openai_compatible",
            )

    def test_llamacpp_base_url_defaults_to_local_8080(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="openai_compatible"):
            for alias in ("llamacpp", "llama_cpp", "llama.cpp", "llama-cpp"):
                self.assertEqual(
                    conversation_engine._resolve_text_base_url_for_provider(alias),
                    "http://127.0.0.1:8080/v1",
                )

    def test_llamacpp_base_url_honors_env_override(self):
        with patch.dict("os.environ", {"LLAMACPP_BASE_URL": "http://gpu-host:9000/v1/"}, clear=True):
            self.assertEqual(
                conversation_engine._resolve_text_base_url_for_provider("llamacpp"),
                "http://gpu-host:9000/v1",
            )

    def test_llamacpp_base_url_uses_matching_config(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="llama-cpp"), \
             patch.object(conversation_engine, "resolve_text_base_url", return_value="http://gpu-host:8081/v1/"):
            self.assertEqual(
                conversation_engine._resolve_text_base_url_for_provider("llamacpp"),
                "http://gpu-host:8081/v1",
            )

    def test_llamacpp_base_url_ignores_non_matching_config(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="lmstudio"), \
             patch.object(conversation_engine, "resolve_text_base_url", return_value="http://127.0.0.1:1234/v1"):
            self.assertEqual(
                conversation_engine._resolve_text_base_url_for_provider("llamacpp"),
                "http://127.0.0.1:8080/v1",
            )

    def test_llamacpp_api_key_uses_env_or_matching_config(self):
        with patch.dict("os.environ", {"LLAMACPP_API_KEY": "env-key"}, clear=True):
            self.assertEqual(conversation_engine._resolve_text_api_key_for_provider("llamacpp"), "env-key")

        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="llama_cpp"), \
             patch.object(conversation_engine, "resolve_text_api_key", return_value="config-key"):
            self.assertEqual(conversation_engine._resolve_text_api_key_for_provider("llama-cpp"), "config-key")

    def test_llamacpp_connection_resolves_engine_provider_and_url(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(conversation_engine, "resolve_text_provider", return_value="openai_compatible"):
            engine_provider, base_url, api_key = conversation_engine._resolve_text_engine_connection("llamacpp")
        self.assertEqual(engine_provider, "openai_compatible")
        self.assertEqual(base_url, "http://127.0.0.1:8080/v1")
        self.assertEqual(api_key, "")


if __name__ == "__main__":
    unittest.main()
