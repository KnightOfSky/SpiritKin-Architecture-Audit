import unittest
from unittest.mock import patch

from backend.app.settings import (
    DEFAULT_AUDIT_LOG_PATH,
    DEFAULT_HOTWORD,
    DEFAULT_KNOWLEDGE_BACKEND,
    DEFAULT_TEXT_API_KEY,
    DEFAULT_TEXT_MODE,
    DEFAULT_TEXT_MODEL,
    DEFAULT_TEXT_PROVIDER,
    DEFAULT_TTS_PROVIDER,
    DEFAULT_TTS_RATE,
    DEFAULT_TTS_VOICE,
    DEFAULT_VISION_API_KEY,
    DEFAULT_VISION_BASE_URL,
    DEFAULT_VISION_MODE,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_PROVIDER,
    DEFAULT_WORKFLOW_MEMORY_PATH,
    RemoteWorkerNodeSetting,
    describe_model_capabilities,
    resolve_audit_log_path,
    resolve_embedding_base_url,
    resolve_embedding_model,
    resolve_embedding_provider,
    resolve_hotword,
    resolve_knowledge_backend,
    resolve_remote_worker_nodes,
    resolve_reranker_base_url,
    resolve_reranker_model,
    resolve_reranker_provider,
    resolve_text_api_key,
    resolve_text_base_url,
    resolve_text_generation_profile,
    resolve_text_mode,
    resolve_text_model,
    resolve_text_provider,
    resolve_tts_settings,
    resolve_vision_api_key,
    resolve_vision_base_url,
    resolve_vision_generation_profile,
    resolve_vision_mode,
    resolve_vision_model,
    resolve_vision_provider,
    resolve_web_search_provider,
    resolve_workflow_memory_path,
)


class SettingsTests(unittest.TestCase):
    def test_resolve_knowledge_backend_uses_config_when_available(self):
        with patch("backend.app.settings._load_yaml_config", return_value={"runtime": {"knowledge_backend": "embedding"}}):
            value = resolve_knowledge_backend(environ={}, config_path="config/config.yaml")

        self.assertEqual(value, "embedding")

    def test_resolve_hotword_uses_config_when_available(self):
        with patch("backend.app.settings._load_yaml_config", return_value={"runtime": {"hotword": "Astra"}}):
            value = resolve_hotword(environ={}, config_path="config/config.yaml")

        self.assertEqual(value, "Astra")

    def test_resolve_runtime_settings_fall_back_to_defaults(self):
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_knowledge_backend(environ={}, config_path="missing.yaml"), DEFAULT_KNOWLEDGE_BACKEND)
            self.assertEqual(resolve_hotword(environ={}, config_path="missing.yaml"), DEFAULT_HOTWORD)

    def test_repository_defaults_use_llamacpp_services(self):
        self.assertEqual(resolve_text_provider(environ={}, config_path="config/config.yaml"), "llamacpp")
        self.assertEqual(resolve_text_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:8080/v1")
        self.assertEqual(resolve_vision_provider(environ={}, config_path="config/config.yaml"), "llamacpp")
        self.assertEqual(resolve_vision_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:8080/v1")
        self.assertEqual(resolve_embedding_provider(environ={}, config_path="config/config.yaml"), "llamacpp")
        self.assertEqual(resolve_embedding_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:8081/v1")
        self.assertEqual(resolve_reranker_provider(environ={}, config_path="config/config.yaml"), "llamacpp")
        self.assertEqual(resolve_reranker_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:8080/v1")

    def test_resolve_text_settings_support_config_and_env(self):
        config = {"models": {"text": {"default": {"provider": "openai_compatible", "model": "Qwen/Qwen3.5-9B", "base_url": "http://127.0.0.1:1234/v1", "api_key": "config-key"}}}}
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_text_provider(environ={}, config_path="config/config.yaml"), "openai_compatible")
            self.assertEqual(resolve_text_model(environ={}, config_path="config/config.yaml"), "Qwen/Qwen3.5-9B")
            self.assertEqual(resolve_text_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:1234/v1")
            self.assertEqual(resolve_text_api_key(environ={}, config_path="config/config.yaml"), "config-key")

        env = {"SPIRIT_TEXT_PROVIDER": "local_transformers", "SPIRIT_TEXT_MODEL": "Qwen/Qwen3.5-14B", "SPIRITKIN_TEXT_API_KEY": "env-key"}
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_text_provider(environ=env, config_path="config/config.yaml"), "local_transformers")
            self.assertEqual(resolve_text_model(environ=env, config_path="config/config.yaml"), "Qwen/Qwen3.5-14B")
            self.assertEqual(resolve_text_api_key(environ=env, config_path="config/config.yaml"), "env-key")

        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_text_api_key(environ={}, config_path="missing.yaml"), DEFAULT_TEXT_API_KEY)

    def test_resolve_vision_settings_support_config_and_defaults(self):
        config = {
            "models": {
                "vision": {
                    "default": {
                        "provider": "openai_compatible",
                        "model": "qwen3-vl:4b",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "api_key": "demo-key",
                    }
                }
            }
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_vision_provider(environ={}, config_path="config/config.yaml"), "openai_compatible")
            self.assertEqual(resolve_vision_model(environ={}, config_path="config/config.yaml"), "qwen3-vl:4b")
            self.assertEqual(resolve_vision_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:11434/v1")
            self.assertEqual(resolve_vision_api_key(environ={}, config_path="config/config.yaml"), "demo-key")

        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_text_provider(environ={}, config_path="missing.yaml"), DEFAULT_TEXT_PROVIDER)
            self.assertEqual(resolve_text_model(environ={}, config_path="missing.yaml"), DEFAULT_TEXT_MODEL)
            self.assertEqual(resolve_vision_provider(environ={}, config_path="missing.yaml"), DEFAULT_VISION_PROVIDER)
            self.assertEqual(resolve_vision_model(environ={}, config_path="missing.yaml"), DEFAULT_VISION_MODEL)
            self.assertEqual(resolve_vision_base_url(environ={}, config_path="missing.yaml"), DEFAULT_VISION_BASE_URL)
            self.assertEqual(resolve_vision_api_key(environ={}, config_path="missing.yaml"), DEFAULT_VISION_API_KEY)

    def test_resolve_search_rag_settings_support_config_and_env(self):
        config = {
            "runtime": {"knowledge_backend": "embedding"},
            "search": {"web_provider": "duckduckgo"},
            "knowledge": {
                "embedding": {"provider": "lmstudio", "model": "embed-local", "base_url": "http://127.0.0.1:1234/v1"},
                "reranker": {"provider": "lmstudio", "model": "rerank-local", "base_url": "http://127.0.0.1:1234/v1"},
            },
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_knowledge_backend(environ={}, config_path="config/config.yaml"), "embedding")
            self.assertEqual(resolve_web_search_provider(environ={}, config_path="config/config.yaml"), "duckduckgo")
            self.assertEqual(resolve_embedding_provider(environ={}, config_path="config/config.yaml"), "lmstudio")
            self.assertEqual(resolve_embedding_model(environ={}, config_path="config/config.yaml"), "embed-local")
            self.assertEqual(resolve_embedding_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:1234/v1")
            self.assertEqual(resolve_reranker_provider(environ={}, config_path="config/config.yaml"), "lmstudio")
            self.assertEqual(resolve_reranker_model(environ={}, config_path="config/config.yaml"), "rerank-local")
            self.assertEqual(resolve_reranker_base_url(environ={}, config_path="config/config.yaml"), "http://127.0.0.1:1234/v1")

        env = {"SPIRITKIN_EMBEDDING_MODEL": "embed-env", "SPIRITKIN_RERANKER_MODEL": "rerank-env"}
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_embedding_model(environ=env, config_path="config/config.yaml"), "embed-env")
            self.assertEqual(resolve_reranker_model(environ=env, config_path="config/config.yaml"), "rerank-env")

    def test_resolve_model_modes_and_generation_profiles(self):
        config = {
            "models": {
                "text": {
                    "default_mode": "strong",
                    "modes": {"strong": {"temperature": 0.1, "top_p": 0.92, "max_new_tokens": 2048}},
                },
                "vision": {
                    "default_mode": "detailed",
                    "modes": {"detailed": {"temperature": 0.0, "max_tokens": 64}},
                },
            }
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            self.assertEqual(resolve_text_mode(environ={}, config_path="config/config.yaml"), "strong")
            self.assertEqual(
                resolve_text_generation_profile(environ={}, config_path="config/config.yaml"),
                {"mode": "strong", "temperature": 0.1, "top_p": 0.92, "max_new_tokens": 2048},
            )
            self.assertEqual(resolve_vision_mode(environ={}, config_path="config/config.yaml"), "detailed")
            self.assertEqual(
                resolve_vision_generation_profile(environ={}, config_path="config/config.yaml"),
                {"mode": "detailed", "temperature": 0.0, "max_tokens": 64},
            )

    def test_generation_profile_env_overrides_take_precedence(self):
        env = {
            "SPIRIT_TEXT_MODE": "fast",
            "SPIRIT_TEXT_TEMPERATURE": "0.55",
            "SPIRIT_TEXT_TOP_P": "0.8",
            "SPIRIT_TEXT_MAX_NEW_TOKENS": "333",
            "SPIRIT_VISION_MODE": "fast",
            "SPIRIT_VISION_TEMPERATURE": "0.0",
            "SPIRIT_VISION_MAX_TOKENS": "18",
        }
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_text_mode(environ=env, config_path="config/config.yaml"), "fast")
            self.assertEqual(
                resolve_text_generation_profile(environ=env, config_path="config/config.yaml"),
                {"mode": "fast", "temperature": 0.55, "top_p": 0.8, "max_new_tokens": 333},
            )
            self.assertEqual(resolve_vision_mode(environ=env, config_path="config/config.yaml"), "fast")
            self.assertEqual(
                resolve_vision_generation_profile(environ=env, config_path="config/config.yaml"),
                {"mode": "fast", "temperature": 0.0, "max_tokens": 18},
            )

    def test_resolve_tts_settings_supports_config_env_and_defaults(self):
        config = {
            "tts": {
                "provider": "pyttsx3",
                "voice": "Config Voice",
                "rate": "+12%",
                "pyttsx3": {"rate": 188, "volume": 0.72},
                "fallback_provider": "disabled",
            }
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            settings = resolve_tts_settings(environ={}, config_path="config/config.yaml")

        self.assertEqual(settings.provider, "pyttsx3")
        self.assertEqual(settings.voice, "Config Voice")
        self.assertEqual(settings.rate, "+12%")
        self.assertEqual(settings.pyttsx3_rate, 188)
        self.assertEqual(settings.volume, 0.72)
        self.assertEqual(settings.fallback_provider, "disabled")
        self.assertTrue(settings.enabled)

        env = {
            "SPIRITKIN_TTS_PROVIDER": "edge-tts",
            "SPIRITKIN_TTS_VOICE": "Env Voice",
            "SPIRITKIN_TTS_RATE": "-5%",
            "SPIRITKIN_PYTTSX3_RATE": "211",
            "SPIRITKIN_TTS_VOLUME": "0.4",
            "SPIRITKIN_TTS_FALLBACK_PROVIDER": "pyttsx3",
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            settings = resolve_tts_settings(environ=env, config_path="config/config.yaml")

        self.assertEqual(settings.provider, "edge_tts")
        self.assertEqual(settings.voice, "Env Voice")
        self.assertEqual(settings.rate, "-5%")
        self.assertEqual(settings.pyttsx3_rate, 211)
        self.assertEqual(settings.volume, 0.4)
        self.assertEqual(settings.fallback_provider, "pyttsx3")

        with patch("backend.app.settings._load_yaml_config", return_value={}):
            settings = resolve_tts_settings(environ={}, config_path="missing.yaml")

        self.assertEqual(settings.provider, DEFAULT_TTS_PROVIDER)
        self.assertEqual(settings.voice, DEFAULT_TTS_VOICE)
        self.assertEqual(settings.rate, DEFAULT_TTS_RATE)
        self.assertTrue(settings.enabled)

    def test_resolve_tts_settings_preserves_disabled_and_zero_values(self):
        config = {
            "tts": {
                "enabled": False,
                "provider": "edge_tts",
                "volume": 0,
                "pyttsx3_rate": 0,
                "fallback_provider": "off",
            }
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            settings = resolve_tts_settings(environ={}, config_path="config/config.yaml")

        self.assertEqual(settings.provider, "disabled")
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.volume, 0.0)
        self.assertEqual(settings.pyttsx3_rate, 40)
        self.assertEqual(settings.fallback_provider, "disabled")

    def test_describe_model_capabilities_reports_modes(self):
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            capabilities = describe_model_capabilities(environ={}, config_path="config/config.yaml")

        self.assertEqual(capabilities["text"]["default_mode"], DEFAULT_TEXT_MODE)
        self.assertIn("balanced", capabilities["text"]["available_modes"])
        self.assertEqual(capabilities["vision"]["default_mode"], DEFAULT_VISION_MODE)
        self.assertIn("default", capabilities["vision"]["available_modes"])
        self.assertEqual(capabilities["tts"]["provider"], DEFAULT_TTS_PROVIDER)
        self.assertEqual(capabilities["tts"]["voice"], DEFAULT_TTS_VOICE)

    def test_resolve_workflow_memory_path_supports_env_and_default(self):
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_workflow_memory_path(environ={}, config_path="missing.yaml"), DEFAULT_WORKFLOW_MEMORY_PATH)

        env = {"SPIRITKIN_WORKFLOW_MEMORY_PATH": "data/custom_workflow.jsonl"}
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_workflow_memory_path(environ=env, config_path="config/config.yaml"), "data/custom_workflow.jsonl")

    def test_resolve_audit_log_path_supports_env_and_default(self):
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_audit_log_path(environ={}, config_path="missing.yaml"), DEFAULT_AUDIT_LOG_PATH)

        env = {"SPIRITKIN_AUDIT_LOG_PATH": "data/audit.jsonl"}
        with patch("backend.app.settings._load_yaml_config", return_value={}):
            self.assertEqual(resolve_audit_log_path(environ=env, config_path="config/config.yaml"), "data/audit.jsonl")

    def test_resolve_remote_worker_nodes_from_env(self):
        env = {
            "SPIRITKIN_REMOTE_WORKER_URL": "http://100.64.0.8:8790/",
            "SPIRITKIN_REMOTE_WORKER_NODE_ID": "office-pc",
            "SPIRITKIN_REMOTE_WORKER_TOKEN": "secret",
            "SPIRITKIN_REMOTE_WORKER_ALIASES": "公司电脑, office",
            "SPIRITKIN_REMOTE_WORKER_TIMEOUT": "2.5",
        }

        nodes = resolve_remote_worker_nodes(environ=env, config_path="missing.yaml")

        self.assertEqual(len(nodes), 1)
        self.assertIsInstance(nodes[0], RemoteWorkerNodeSetting)
        self.assertEqual(nodes[0].node_id, "office-pc")
        self.assertEqual(nodes[0].base_url, "http://100.64.0.8:8790")
        self.assertEqual(nodes[0].auth_token, "secret")
        self.assertEqual(nodes[0].aliases, {"公司电脑", "office"})
        self.assertEqual(nodes[0].timeout_seconds, 2.5)

    def test_resolve_remote_worker_nodes_from_config(self):
        config = {
            "remote": {
                "workers": [
                    {
                        "node_id": "lab-pc",
                        "url": "http://10.0.0.7:8790",
                        "token": "remote-token",
                        "aliases": ["实验电脑", "lab"],
                        "metadata": {"owner": "me"},
                    }
                ]
            }
        }

        with patch("backend.app.settings._load_yaml_config", return_value=config):
            nodes = resolve_remote_worker_nodes(environ={}, config_path="config/config.yaml")

        self.assertEqual(nodes[0].node_id, "lab-pc")
        self.assertEqual(nodes[0].base_url, "http://10.0.0.7:8790")
        self.assertEqual(nodes[0].auth_token, "remote-token")
        self.assertEqual(nodes[0].aliases, {"实验电脑", "lab"})
        self.assertEqual(nodes[0].metadata["owner"], "me")
        self.assertEqual(nodes[0].metadata["configured_from"], "config")


if __name__ == "__main__":
    unittest.main()
