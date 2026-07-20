from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_HOTWORD = "Spirit"
DEFAULT_KNOWLEDGE_BACKEND = "embedding"
VALID_KNOWLEDGE_BACKENDS = {"keyword", "embedding"}
DEFAULT_TEXT_PROVIDER = "llamacpp"
DEFAULT_TEXT_MODEL = "qwen/qwen3.6-35b-a3b"
DEFAULT_TEXT_API_KEY = ""
DEFAULT_TEXT_MODE = "balanced"
VALID_TEXT_MODES = {"fast", "balanced", "strong"}
DEFAULT_TEXT_GENERATION_PROFILES = {
    "fast": {"temperature": 0.6, "top_p": 0.85, "max_new_tokens": 256},
    "balanced": {"temperature": 0.4, "top_p": 0.85, "max_new_tokens": 512},
    "strong": {"temperature": 0.2, "top_p": 0.9, "max_new_tokens": 1024},
}
DEFAULT_VISION_PROVIDER = "llamacpp"
DEFAULT_VISION_MODEL = "qwen/qwen3.6-35b-a3b"
DEFAULT_VISION_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_VISION_API_KEY = ""
DEFAULT_VISION_MODE = "default"
VALID_VISION_MODES = {"default", "fast", "detailed"}
DEFAULT_VISION_GENERATION_PROFILES = {
    "default": {"temperature": 0.0, "max_tokens": 25},
    "fast": {"temperature": 0.0, "max_tokens": 20},
    "detailed": {"temperature": 0.0, "max_tokens": 80},
}
DEFAULT_ASR_MODEL_SIZE = "large-v3-turbo"
DEFAULT_ASR_DEVICE = "cpu"
DEFAULT_ASR_COMPUTE_TYPE = "int8"
DEFAULT_ASR_PROFILE = {"beam_size": 5, "vad_filter": True, "temperature": 0.0}
DEFAULT_TTS_PROVIDER = "cosyvoice"
DEFAULT_TTS_FALLBACK_PROVIDER = "edge_tts"
DEFAULT_TTS_VOICE = "Fairy"
DEFAULT_TTS_RATE = "+0%"
DEFAULT_PYTTSX3_RATE = 175
DEFAULT_TTS_VOLUME = 0.95
DEFAULT_TTS_PROFILE_ID = "spiritkin.primary.v1"
DEFAULT_TTS_PROFILE_PATH = "state/voice-profiles/spiritkin.primary.v1/profile.json"
DEFAULT_TTS_BASE_URL = "http://127.0.0.1:50000"
DEFAULT_TTS_TIMEOUT_SECONDS = 30.0
VALID_TTS_PROVIDERS = {"cosyvoice", "edge_tts", "pyttsx3", "disabled"}
DEFAULT_WORKFLOW_MEMORY_PATH = "state/workflow_memory.sqlite3"
DEFAULT_SKILL_STORE_PATH = "state/skills.jsonl"
DEFAULT_LONG_TERM_MEMORY_PATH = "state/long_term_memory.jsonl"
DEFAULT_PERSONALITY_PATH = "state/personality.json"
DEFAULT_RELATIONSHIP_PATH = "state/relationship.json"
DEFAULT_AUDIT_LOG_PATH = "state/audit_log.jsonl"
DEFAULT_WEB_SEARCH_PROVIDER = "brave,duckduckgo"
DEFAULT_EMBEDDING_PROVIDER = "llamacpp"
DEFAULT_EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
DEFAULT_EMBEDDING_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_EMBEDDING_API_KEY = ""
DEFAULT_RERANKER_PROVIDER = "llamacpp"
DEFAULT_RERANKER_MODEL = "qwen/qwen3.6-35b-a3b"
DEFAULT_RERANKER_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_RERANKER_API_KEY = ""


@dataclass(frozen=True)
class RemoteWorkerNodeSetting:
    node_id: str
    base_url: str
    auth_token: str = ""
    aliases: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class TTSSettings:
    provider: str = DEFAULT_TTS_PROVIDER
    voice: str = DEFAULT_TTS_VOICE
    rate: str = DEFAULT_TTS_RATE
    pyttsx3_rate: int = DEFAULT_PYTTSX3_RATE
    volume: float = DEFAULT_TTS_VOLUME
    fallback_provider: str = DEFAULT_TTS_FALLBACK_PROVIDER
    enabled: bool = True
    voice_profile_id: str = DEFAULT_TTS_PROFILE_ID
    voice_profile_path: str = DEFAULT_TTS_PROFILE_PATH
    base_url: str = DEFAULT_TTS_BASE_URL
    timeout_seconds: float = DEFAULT_TTS_TIMEOUT_SECONDS


RECOMMENDED_MODEL_STACK = {
    "llm_reasoning": {"role": "通用推理/领域智能体/计划生成", "recommended": "Qwen2.5/3 7B-14B Instruct 或同级本地/服务端模型"},
    "intent_router": {"role": "ASR 纠错、工具映射、参数补全", "recommended": "低延迟 instruct LLM，可与主 LLM 共用 fast profile"},
    "vision_language": {"role": "屏幕理解、图像/视频帧描述、手势理解", "recommended": "Qwen2.5-VL / Qwen3-VL 4B-7B 或 OpenAI-compatible VLM"},
    "asr": {"role": "语音转文本", "recommended": "faster-whisper large-v3-turbo；弱设备可用 small/base"},
    "wakeword": {"role": "低延迟唤醒", "recommended": "当前 Whisper hotword；完全体建议 Porcupine/openWakeWord/Vosk keyword"},
    "tts": {"role": "语音输出", "recommended": "当前 Edge-TTS/pyttsx3；完全体建议 CosyVoice/Fish-Speech"},
    "embedding": {"role": "长期记忆/RAG/工作流召回", "recommended": "bge-m3 或 nomic-embed-text"},
    "reranker": {"role": "知识库和 workflow 候选重排", "recommended": "bge-reranker-v2-m3 或轻量 cross-encoder"},
    "policy_guard": {"role": "权限、风险、确认门、审计", "recommended": "规则优先 + 小模型/LLM 审核辅助"},
}


# mtime/size keyed cache: snapshot builds hit this hundreds of times per request,
# and re-parsing the same YAML dominated gateway latency. Callers treat the result
# as read-only, so returning the shared parsed dict is safe.
_YAML_CONFIG_CACHE: dict[str, tuple[tuple[float, int], dict[str, Any]]] = {}


def _load_yaml_config(config_path: str | Path = "config/config.yaml") -> dict[str, Any]:
    path = Path(config_path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    if not path.is_file():
        return {}

    cache_key = str(path.resolve())
    signature = (stat.st_mtime, stat.st_size)
    cached = _YAML_CONFIG_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    try:
        import yaml
    except Exception:
        return {}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    result = data if isinstance(data, dict) else {}
    _YAML_CONFIG_CACHE[cache_key] = (signature, result)
    return result


def _get_nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _select_environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _normalize_non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if normalized:
        return normalized
    return None


def _first_normalized(normalizer, *values: Any) -> Any:
    for value in values:
        normalized = normalizer(value)
        if normalized is not None:
            return normalized
    return None


def _split_csv(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {item.strip() for item in str(value).split(",") if item.strip()}


def _derive_remote_node_id(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or "remote-worker"
    port = f"-{parsed.port}" if parsed.port else ""
    return f"{host}{port}".replace(".", "-")


def _normalize_mode(value: Any, *, valid_modes: set[str]) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in valid_modes:
        return normalized
    return None


def _normalize_tts_provider(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "edge": "edge_tts",
        "edgetts": "edge_tts",
        "edge_tts": "edge_tts",
        "microsoft_edge": "edge_tts",
        "cosy": "cosyvoice",
        "cosy_voice": "cosyvoice",
        "cosyvoice": "cosyvoice",
        "system": "pyttsx3",
        "windows": "pyttsx3",
        "local": "pyttsx3",
        "pyttsx3": "pyttsx3",
        "none": "disabled",
        "off": "disabled",
        "false": "disabled",
        "0": "disabled",
        "disabled": "disabled",
    }
    provider = aliases.get(normalized, normalized)
    if provider in VALID_TTS_PROVIDERS:
        return provider
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _resolve_string_setting(
    explicit: str | None,
    *,
    env_key: str,
    config_paths: tuple[tuple[str, ...], ...],
    default: str,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(explicit)
    if explicit_value:
        return explicit_value

    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get(env_key))
    if from_env:
        return from_env

    config = _load_yaml_config(config_path)
    for path_keys in config_paths:
        from_config = _normalize_non_empty_string(_get_nested(config, *path_keys))
        if from_config:
            return from_config

    return default


def _normalize_knowledge_backend(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in VALID_KNOWLEDGE_BACKENDS:
        return normalized
    return None


def resolve_knowledge_backend(
    knowledge_backend: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit = _normalize_knowledge_backend(knowledge_backend)
    if explicit:
        return explicit

    env = _select_environ(environ)
    from_env = _normalize_knowledge_backend(env.get("SPIRIT_KNOWLEDGE_BACKEND"))
    if from_env:
        return from_env

    config = _load_yaml_config(config_path)
    from_config = _normalize_knowledge_backend(
        _get_nested(config, "runtime", "knowledge_backend") or _get_nested(config, "knowledge", "backend")
    )
    if from_config:
        return from_config

    return DEFAULT_KNOWLEDGE_BACKEND


def resolve_hotword(
    hotword: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit = _normalize_non_empty_string(hotword)
    if explicit:
        return explicit

    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get("SPIRIT_HOTWORD"))
    if from_env:
        return from_env

    config = _load_yaml_config(config_path)
    from_config = _get_nested(config, "runtime", "hotword")
    if isinstance(from_config, str) and from_config.strip():
        return from_config.strip()

    return DEFAULT_HOTWORD


def resolve_text_provider(
    provider: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        provider,
        env_key="SPIRIT_TEXT_PROVIDER",
        config_paths=(("models", "text", "default", "provider"),),
        default=DEFAULT_TEXT_PROVIDER,
        environ=environ,
        config_path=config_path,
    )


def resolve_text_model(
    model_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        model_name,
        env_key="SPIRIT_TEXT_MODEL",
        config_paths=(("models", "text", "default", "model"), ("llm", "model")),
        default=DEFAULT_TEXT_MODEL,
        environ=environ,
        config_path=config_path,
    )


def resolve_text_mode(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit = _normalize_mode(mode, valid_modes=VALID_TEXT_MODES)
    if explicit:
        return explicit

    env = _select_environ(environ)
    from_env = _normalize_mode(env.get("SPIRIT_TEXT_MODE"), valid_modes=VALID_TEXT_MODES)
    if from_env:
        return from_env

    config = _load_yaml_config(config_path)
    from_config = _normalize_mode(
        _get_nested(config, "models", "text", "default_mode") or _get_nested(config, "models", "text", "selected_mode"),
        valid_modes=VALID_TEXT_MODES,
    )
    if from_config:
        return from_config

    return DEFAULT_TEXT_MODE


def resolve_text_generation_profile(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> dict[str, float | int | str]:
    resolved_mode = resolve_text_mode(mode, environ=environ, config_path=config_path)
    env = _select_environ(environ)
    config = _load_yaml_config(config_path)

    profile = dict(DEFAULT_TEXT_GENERATION_PROFILES[resolved_mode])
    from_config = _get_nested(config, "models", "text", "modes", resolved_mode)
    if isinstance(from_config, Mapping):
        temperature = _coerce_float(from_config.get("temperature"))
        top_p = _coerce_float(from_config.get("top_p"))
        max_new_tokens = _coerce_int(from_config.get("max_new_tokens"))
        if temperature is not None:
            profile["temperature"] = temperature
        if top_p is not None:
            profile["top_p"] = top_p
        if max_new_tokens is not None:
            profile["max_new_tokens"] = max_new_tokens

    env_temperature = _coerce_float(env.get("SPIRIT_TEXT_TEMPERATURE"))
    env_top_p = _coerce_float(env.get("SPIRIT_TEXT_TOP_P"))
    env_max_new_tokens = _coerce_int(env.get("SPIRIT_TEXT_MAX_NEW_TOKENS"))
    if env_temperature is not None:
        profile["temperature"] = env_temperature
    if env_top_p is not None:
        profile["top_p"] = env_top_p
    if env_max_new_tokens is not None:
        profile["max_new_tokens"] = env_max_new_tokens

    return {"mode": resolved_mode, **profile}


def resolve_vision_provider(
    provider: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        provider,
        env_key="SPIRIT_VISION_PROVIDER",
        config_paths=(("models", "vision", "default", "provider"),),
        default=DEFAULT_VISION_PROVIDER,
        environ=environ,
        config_path=config_path,
    )


def resolve_vision_model(
    model_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        model_name,
        env_key="SPIRIT_VISION_MODEL",
        config_paths=(("models", "vision", "default", "model"), ("vision", "qwen_vl_model")),
        default=DEFAULT_VISION_MODEL,
        environ=environ,
        config_path=config_path,
    )


def resolve_vision_base_url(
    base_url: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        base_url,
        env_key="SPIRIT_VISION_BASE_URL",
        config_paths=(("models", "vision", "default", "base_url"), ("vision", "base_url")),
        default=DEFAULT_VISION_BASE_URL,
        environ=environ,
        config_path=config_path,
    )


def resolve_text_base_url(
    base_url: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        base_url,
        env_key="SPIRITKIN_TEXT_BASE_URL",
        config_paths=(("models", "text", "default", "base_url"),),
        default="http://127.0.0.1:8080/v1",
        environ=environ,
        config_path=config_path,
    )


def resolve_text_api_key(
    api_key: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(api_key)
    if explicit_value:
        return explicit_value

    env = _select_environ(environ)
    for env_key in ("SPIRITKIN_TEXT_API_KEY", "SPIRIT_TEXT_API_KEY"):
        from_env = _normalize_non_empty_string(env.get(env_key))
        if from_env:
            return from_env

    config = _load_yaml_config(config_path)
    from_config = _normalize_non_empty_string(_get_nested(config, "models", "text", "default", "api_key"))
    if from_config:
        return from_config

    for env_key in ("CLOUD_MODEL_API_KEY", "YUNDUN_API_KEY", "OPENAI_API_KEY", "SPIRITKIN_OPENAI_API_KEY", "LMSTUDIO_API_KEY"):
        from_env = _normalize_non_empty_string(env.get(env_key))
        if from_env:
            return from_env

    return DEFAULT_TEXT_API_KEY


def resolve_web_search_provider(
    provider: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        provider,
        env_key="SPIRITKIN_WEB_SEARCH_PROVIDER",
        config_paths=(("search", "web_provider"), ("search", "web_search_provider")),
        default=DEFAULT_WEB_SEARCH_PROVIDER,
        environ=environ,
        config_path=config_path,
    )


def resolve_embedding_provider(
    provider: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        provider,
        env_key="SPIRITKIN_EMBEDDING_PROVIDER",
        config_paths=(("knowledge", "embedding", "provider"), ("search", "embedding", "provider")),
        default=DEFAULT_EMBEDDING_PROVIDER,
        environ=environ,
        config_path=config_path,
    )


def resolve_embedding_model(
    model: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        model,
        env_key="SPIRITKIN_EMBEDDING_MODEL",
        config_paths=(("knowledge", "embedding", "model"), ("search", "embedding", "model")),
        default=DEFAULT_EMBEDDING_MODEL,
        environ=environ,
        config_path=config_path,
    )


def resolve_embedding_base_url(
    base_url: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        base_url,
        env_key="SPIRITKIN_EMBEDDING_BASE_URL",
        config_paths=(("knowledge", "embedding", "base_url"), ("search", "embedding", "base_url")),
        default=DEFAULT_EMBEDDING_BASE_URL,
        environ=environ,
        config_path=config_path,
    )


def resolve_embedding_api_key(
    api_key: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        api_key,
        env_key="SPIRITKIN_EMBEDDING_API_KEY",
        config_paths=(("knowledge", "embedding", "api_key"), ("search", "embedding", "api_key")),
        default=DEFAULT_EMBEDDING_API_KEY,
        environ=environ,
        config_path=config_path,
    )


def resolve_reranker_provider(
    provider: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(provider)
    if explicit_value:
        return explicit_value
    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get("SPIRITKIN_RERANKER_PROVIDER") or env.get("SPIRITKIN_RERANKER"))
    if from_env:
        return from_env
    config = _load_yaml_config(config_path)
    from_config = _normalize_non_empty_string(
        _get_nested(config, "knowledge", "reranker", "provider") or _get_nested(config, "search", "reranker", "provider")
    )
    return from_config or DEFAULT_RERANKER_PROVIDER


def resolve_reranker_model(
    model: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(model)
    if explicit_value:
        return explicit_value
    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get("SPIRITKIN_RERANKER_MODEL"))
    if from_env:
        return from_env
    config = _load_yaml_config(config_path)
    from_config = _normalize_non_empty_string(
        _get_nested(config, "knowledge", "reranker", "model") or _get_nested(config, "search", "reranker", "model")
    )
    return from_config or resolve_text_model(environ=environ, config_path=config_path) or DEFAULT_RERANKER_MODEL


def resolve_reranker_base_url(
    base_url: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(base_url)
    if explicit_value:
        return explicit_value
    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get("SPIRITKIN_RERANKER_BASE_URL"))
    if from_env:
        return from_env
    config = _load_yaml_config(config_path)
    from_config = _normalize_non_empty_string(
        _get_nested(config, "knowledge", "reranker", "base_url") or _get_nested(config, "search", "reranker", "base_url")
    )
    return from_config or resolve_text_base_url(environ=environ, config_path=config_path) or DEFAULT_RERANKER_BASE_URL


def resolve_reranker_api_key(
    api_key: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit_value = _normalize_non_empty_string(api_key)
    if explicit_value:
        return explicit_value
    env = _select_environ(environ)
    from_env = _normalize_non_empty_string(env.get("SPIRITKIN_RERANKER_API_KEY"))
    if from_env:
        return from_env
    config = _load_yaml_config(config_path)
    from_config = _normalize_non_empty_string(
        _get_nested(config, "knowledge", "reranker", "api_key") or _get_nested(config, "search", "reranker", "api_key")
    )
    return from_config or DEFAULT_RERANKER_API_KEY


def resolve_vision_api_key(
    api_key: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        api_key,
        env_key="SPIRIT_VISION_API_KEY",
        config_paths=(("models", "vision", "default", "api_key"), ("vision", "api_key")),
        default=DEFAULT_VISION_API_KEY,
        environ=environ,
        config_path=config_path,
    )


def resolve_vision_mode(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    explicit = _normalize_mode(mode, valid_modes=VALID_VISION_MODES)
    if explicit:
        return explicit

    env = _select_environ(environ)
    from_env = _normalize_mode(env.get("SPIRIT_VISION_MODE"), valid_modes=VALID_VISION_MODES)
    if from_env:
        return from_env

    config = _load_yaml_config(config_path)
    from_config = _normalize_mode(
        _get_nested(config, "models", "vision", "default_mode") or _get_nested(config, "models", "vision", "selected_mode"),
        valid_modes=VALID_VISION_MODES,
    )
    if from_config:
        return from_config

    return DEFAULT_VISION_MODE


def resolve_vision_generation_profile(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> dict[str, float | int | str]:
    resolved_mode = resolve_vision_mode(mode, environ=environ, config_path=config_path)
    env = _select_environ(environ)
    config = _load_yaml_config(config_path)

    profile = dict(DEFAULT_VISION_GENERATION_PROFILES[resolved_mode])
    from_config = _get_nested(config, "models", "vision", "modes", resolved_mode)
    if isinstance(from_config, Mapping):
        temperature = _coerce_float(from_config.get("temperature"))
        max_tokens = _coerce_int(from_config.get("max_tokens"))
        if temperature is not None:
            profile["temperature"] = temperature
        if max_tokens is not None:
            profile["max_tokens"] = max_tokens

    env_temperature = _coerce_float(env.get("SPIRIT_VISION_TEMPERATURE"))
    env_max_tokens = _coerce_int(env.get("SPIRIT_VISION_MAX_TOKENS"))
    if env_temperature is not None:
        profile["temperature"] = env_temperature
    if env_max_tokens is not None:
        profile["max_tokens"] = env_max_tokens

    return {"mode": resolved_mode, **profile}


def resolve_asr_model_size(
    model_size: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        model_size,
        env_key="SPIRIT_ASR_MODEL_SIZE",
        config_paths=(("models", "asr", "default", "model_size"), ("asr", "model_size")),
        default=DEFAULT_ASR_MODEL_SIZE,
        environ=environ,
        config_path=config_path,
    )


def resolve_asr_device(
    device: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        device,
        env_key="SPIRIT_ASR_DEVICE",
        config_paths=(("models", "asr", "default", "device"),),
        default=DEFAULT_ASR_DEVICE,
        environ=environ,
        config_path=config_path,
    )


def resolve_asr_compute_type(
    compute_type: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        compute_type,
        env_key="SPIRIT_ASR_COMPUTE_TYPE",
        config_paths=(("models", "asr", "default", "compute_type"),),
        default=DEFAULT_ASR_COMPUTE_TYPE,
        environ=environ,
        config_path=config_path,
    )


def resolve_asr_profile(
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> dict[str, object]:
    env = _select_environ(environ)
    config = _load_yaml_config(config_path)
    profile = dict(DEFAULT_ASR_PROFILE)
    from_config = _get_nested(config, "models", "asr", "transcribe")
    if isinstance(from_config, Mapping):
        beam_size = _coerce_int(from_config.get("beam_size"))
        vad_filter = _coerce_bool(from_config.get("vad_filter"))
        temperature = _coerce_float(from_config.get("temperature"))
        if beam_size is not None:
            profile["beam_size"] = beam_size
        if vad_filter is not None:
            profile["vad_filter"] = vad_filter
        if temperature is not None:
            profile["temperature"] = temperature

    env_beam_size = _coerce_int(env.get("SPIRIT_ASR_BEAM_SIZE"))
    env_vad_filter = _coerce_bool(env.get("SPIRIT_ASR_VAD_FILTER"))
    env_temperature = _coerce_float(env.get("SPIRIT_ASR_TEMPERATURE"))
    if env_beam_size is not None:
        profile["beam_size"] = env_beam_size
    if env_vad_filter is not None:
        profile["vad_filter"] = env_vad_filter
    if env_temperature is not None:
        profile["temperature"] = env_temperature
    return profile


def resolve_tts_settings(
    *,
    provider: str | None = None,
    voice: str | None = None,
    rate: str | None = None,
    pyttsx3_rate: int | str | None = None,
    volume: float | str | None = None,
    voice_profile_id: str | None = None,
    voice_profile_path: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | str | None = None,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> TTSSettings:
    env = _select_environ(environ)
    config = _load_yaml_config(config_path)

    enabled = True
    config_enabled = _coerce_bool(
        _first_present(_get_nested(config, "tts", "enabled"), _get_nested(config, "models", "tts", "enabled"))
    )
    env_enabled = _first_normalized(_coerce_bool, env.get("SPIRITKIN_TTS_ENABLED"), env.get("SPIRIT_TTS_ENABLED"))
    if config_enabled is not None:
        enabled = config_enabled
    if env_enabled is not None:
        enabled = env_enabled

    resolved_provider = (
        _normalize_tts_provider(provider)
        or _first_normalized(_normalize_tts_provider, env.get("SPIRITKIN_TTS_PROVIDER"), env.get("SPIRIT_TTS_PROVIDER"))
        or _first_normalized(
            _normalize_tts_provider,
            _get_nested(config, "tts", "provider"),
            _get_nested(config, "models", "tts", "default", "provider"),
            _get_nested(config, "voice", "tts_provider"),
        )
        or DEFAULT_TTS_PROVIDER
    )
    if not enabled:
        resolved_provider = "disabled"

    resolved_voice = (
        _normalize_non_empty_string(voice)
        or _first_normalized(
            _normalize_non_empty_string,
            env.get("SPIRITKIN_TTS_VOICE"),
            env.get("SPIRITKIN_EDGE_TTS_VOICE"),
            env.get("SPIRIT_TTS_VOICE"),
        )
        or _first_normalized(
            _normalize_non_empty_string,
            _get_nested(config, "tts", "voice"),
            _get_nested(config, "tts", "edge", "voice"),
            _get_nested(config, "models", "tts", "default", "voice"),
        )
        or DEFAULT_TTS_VOICE
    )

    resolved_rate = (
        _normalize_non_empty_string(rate)
        or _first_normalized(
            _normalize_non_empty_string,
            env.get("SPIRITKIN_TTS_RATE"),
            env.get("SPIRITKIN_EDGE_TTS_RATE"),
            env.get("SPIRIT_TTS_RATE"),
        )
        or _first_normalized(
            _normalize_non_empty_string,
            _get_nested(config, "tts", "rate"),
            _get_nested(config, "tts", "edge", "rate"),
            _get_nested(config, "models", "tts", "default", "rate"),
        )
        or DEFAULT_TTS_RATE
    )

    config_pyttsx3_rate = _first_present(
        _get_nested(config, "tts", "pyttsx3_rate"),
        _get_nested(config, "tts", "pyttsx3", "rate"),
        _get_nested(config, "models", "tts", "pyttsx3", "rate"),
    )
    resolved_pyttsx3_rate = _first_present(
        _coerce_int(pyttsx3_rate),
        _first_normalized(_coerce_int, env.get("SPIRITKIN_PYTTSX3_RATE"), env.get("SPIRITKIN_TTS_PYTTSX3_RATE")),
        _coerce_int(config_pyttsx3_rate),
        DEFAULT_PYTTSX3_RATE,
    )

    config_volume = _first_present(
        _get_nested(config, "tts", "volume"),
        _get_nested(config, "tts", "pyttsx3", "volume"),
        _get_nested(config, "models", "tts", "default", "volume"),
    )
    resolved_volume = _first_present(
        _coerce_float(volume),
        _coerce_float(env.get("SPIRITKIN_TTS_VOLUME")),
        _coerce_float(config_volume),
        DEFAULT_TTS_VOLUME,
    )

    fallback_provider = (
        _first_normalized(_normalize_tts_provider, env.get("SPIRITKIN_TTS_FALLBACK_PROVIDER"))
        or _normalize_tts_provider(_get_nested(config, "tts", "fallback_provider"))
        or DEFAULT_TTS_FALLBACK_PROVIDER
    )
    if resolved_provider == "edge_tts" and fallback_provider == "edge_tts":
        fallback_provider = "pyttsx3"

    resolved_voice_profile_id = (
        _normalize_non_empty_string(voice_profile_id)
        or _normalize_non_empty_string(env.get("SPIRITKIN_TTS_VOICE_PROFILE"))
        or _normalize_non_empty_string(_get_nested(config, "tts", "voice_profile"))
        or DEFAULT_TTS_PROFILE_ID
    )
    resolved_voice_profile_path = (
        _normalize_non_empty_string(voice_profile_path)
        or _normalize_non_empty_string(env.get("SPIRITKIN_TTS_VOICE_PROFILE_PATH"))
        or _normalize_non_empty_string(_get_nested(config, "tts", "voice_profile_path"))
        or DEFAULT_TTS_PROFILE_PATH
    )
    resolved_base_url = (
        _normalize_non_empty_string(base_url)
        or _normalize_non_empty_string(env.get("SPIRITKIN_TTS_BASE_URL"))
        or _normalize_non_empty_string(_get_nested(config, "tts", "base_url"))
        or DEFAULT_TTS_BASE_URL
    )
    resolved_timeout = _first_present(
        _coerce_float(timeout_seconds),
        _coerce_float(env.get("SPIRITKIN_TTS_TIMEOUT_SECONDS")),
        _coerce_float(_get_nested(config, "tts", "timeout_seconds")),
        DEFAULT_TTS_TIMEOUT_SECONDS,
    )

    return TTSSettings(
        provider=resolved_provider,
        voice=resolved_voice,
        rate=resolved_rate,
        pyttsx3_rate=max(40, min(400, resolved_pyttsx3_rate)),
        volume=max(0.0, min(1.0, resolved_volume)),
        fallback_provider=fallback_provider,
        enabled=enabled and resolved_provider != "disabled",
        voice_profile_id=resolved_voice_profile_id,
        voice_profile_path=resolved_voice_profile_path,
        base_url=resolved_base_url,
        timeout_seconds=max(1.0, min(300.0, resolved_timeout)),
    )


def resolve_workflow_memory_path(
    workflow_memory_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        workflow_memory_path,
        env_key="SPIRITKIN_WORKFLOW_MEMORY_PATH",
        config_paths=(("runtime", "workflow_memory_path"), ("memory", "workflow", "path")),
        default=DEFAULT_WORKFLOW_MEMORY_PATH,
        environ=environ,
        config_path=config_path,
    )


def resolve_skill_store_path(
    skill_store_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        skill_store_path,
        env_key="SPIRITKIN_SKILL_STORE_PATH",
        config_paths=(("runtime", "skill_store_path"), ("skills", "store", "path")),
        default=DEFAULT_SKILL_STORE_PATH,
        environ=environ,
        config_path=config_path,
    )


def resolve_audit_log_path(
    audit_log_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> str:
    return _resolve_string_setting(
        audit_log_path,
        env_key="SPIRITKIN_AUDIT_LOG_PATH",
        config_paths=(("runtime", "audit_log_path"), ("security", "audit_log_path")),
        default=DEFAULT_AUDIT_LOG_PATH,
        environ=environ,
        config_path=config_path,
    )


def resolve_remote_worker_nodes(
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> list[RemoteWorkerNodeSetting]:
    env = _select_environ(environ)
    env_url = _normalize_non_empty_string(env.get("SPIRITKIN_REMOTE_WORKER_URL"))
    if env_url:
        node_id = (
            _normalize_non_empty_string(env.get("SPIRITKIN_REMOTE_WORKER_NODE_ID"))
            or _normalize_non_empty_string(env.get("SPIRITKIN_REMOTE_NODE_ID"))
            or _derive_remote_node_id(env_url)
        )
        token = _normalize_non_empty_string(env.get("SPIRITKIN_REMOTE_WORKER_TOKEN")) or ""
        timeout = _coerce_float(env.get("SPIRITKIN_REMOTE_WORKER_TIMEOUT")) or 5.0
        return [
            RemoteWorkerNodeSetting(
                node_id=node_id,
                base_url=env_url.rstrip("/"),
                auth_token=token,
                aliases=_split_csv(env.get("SPIRITKIN_REMOTE_WORKER_ALIASES")),
                metadata={"configured_from": "env"},
                timeout_seconds=max(0.5, timeout),
            )
        ]

    config = _load_yaml_config(config_path)
    raw_nodes = (
        _get_nested(config, "remote", "workers")
        or _get_nested(config, "remote", "nodes")
        or _get_nested(config, "runtime", "remote_nodes")
        or []
    )
    if isinstance(raw_nodes, Mapping):
        raw_nodes = [raw_nodes]
    if not isinstance(raw_nodes, list):
        return []

    nodes: list[RemoteWorkerNodeSetting] = []
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            continue
        base_url = _normalize_non_empty_string(raw.get("base_url") or raw.get("url"))
        if not base_url:
            continue
        node_id = _normalize_non_empty_string(raw.get("node_id") or raw.get("id") or raw.get("name")) or _derive_remote_node_id(base_url)
        timeout = _coerce_float(raw.get("timeout_seconds") or raw.get("timeout")) or 5.0
        metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), Mapping) else {}
        metadata.setdefault("configured_from", "config")
        nodes.append(
            RemoteWorkerNodeSetting(
                node_id=node_id,
                base_url=base_url.rstrip("/"),
                auth_token=str(raw.get("auth_token") or raw.get("token") or "").strip(),
                aliases=_split_csv(raw.get("aliases")),
                metadata=metadata,
                timeout_seconds=max(0.5, timeout),
            )
        )
    return nodes


def resolve_growth_sandbox_execution(
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> dict[str, Any]:
    env = _select_environ(environ)
    config = _load_yaml_config(config_path)
    configured_enabled = _get_nested(config, "growth", "sandbox", "execution_enabled")
    raw_enabled = (
        env.get("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED")
        if "SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED" in env
        else configured_enabled
    )
    enabled = _coerce_bool(raw_enabled)
    if enabled is None:
        enabled = False

    if "SPIRITKIN_GROWTH_SANDBOX_IMAGES" in env:
        raw_images: Any = env.get("SPIRITKIN_GROWTH_SANDBOX_IMAGES") or ""
    else:
        raw_images = _get_nested(config, "growth", "sandbox", "approved_images") or []
    if isinstance(raw_images, str):
        images = [item.strip() for item in raw_images.replace(";", ",").split(",") if item.strip()]
    elif isinstance(raw_images, (list, tuple)):
        images = [str(item).strip() for item in raw_images if str(item).strip()]
    else:
        images = []
    raw_probe = (
        env.get("SPIRITKIN_GROWTH_SANDBOX_PROBE_COMMAND_JSON")
        if "SPIRITKIN_GROWTH_SANDBOX_PROBE_COMMAND_JSON" in env
        else _get_nested(config, "growth", "sandbox", "probe_command")
    )
    if isinstance(raw_probe, str):
        try:
            import json

            raw_probe = json.loads(raw_probe)
        except (ValueError, TypeError):
            raw_probe = []
    probe_command = [str(item) for item in raw_probe or []] if isinstance(raw_probe, (list, tuple)) else []
    probe_command = [item for item in probe_command if item and len(item) <= 240][:24]
    return {"enabled": bool(enabled), "images": list(dict.fromkeys(images)), "probe_command": probe_command}


def describe_model_capabilities(
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = "config/config.yaml",
) -> dict[str, object]:
    tts_settings = resolve_tts_settings(environ=environ, config_path=config_path)
    return {
        "text": {
            "provider": resolve_text_provider(environ=environ, config_path=config_path),
            "model": resolve_text_model(environ=environ, config_path=config_path),
            "default_mode": resolve_text_mode(environ=environ, config_path=config_path),
            "available_modes": sorted(VALID_TEXT_MODES),
            "generation": resolve_text_generation_profile(environ=environ, config_path=config_path),
        },
        "vision": {
            "provider": resolve_vision_provider(environ=environ, config_path=config_path),
            "model": resolve_vision_model(environ=environ, config_path=config_path),
            "default_mode": resolve_vision_mode(environ=environ, config_path=config_path),
            "available_modes": sorted(VALID_VISION_MODES),
            "generation": resolve_vision_generation_profile(environ=environ, config_path=config_path),
            "base_url": resolve_vision_base_url(environ=environ, config_path=config_path),
        },
        "asr": {
            "model_size": resolve_asr_model_size(environ=environ, config_path=config_path),
            "device": resolve_asr_device(environ=environ, config_path=config_path),
            "compute_type": resolve_asr_compute_type(environ=environ, config_path=config_path),
            "transcribe": resolve_asr_profile(environ=environ, config_path=config_path),
        },
        "tts": {
            "provider": tts_settings.provider,
            "voice": tts_settings.voice,
            "rate": tts_settings.rate,
            "pyttsx3_rate": tts_settings.pyttsx3_rate,
            "volume": tts_settings.volume,
            "fallback_provider": tts_settings.fallback_provider,
            "enabled": tts_settings.enabled,
        },
    }


def describe_recommended_model_stack() -> dict[str, object]:
    return dict(RECOMMENDED_MODEL_STACK)
