import contextvars
import hashlib
import json
import os
import re
import threading
import urllib.request
from contextlib import contextmanager

from backend.app.settings import (
    resolve_text_api_key,
    resolve_text_base_url,
    resolve_text_generation_profile,
    resolve_text_model,
    resolve_text_provider,
)
from backend.expression.avatar import show_emotion

_LLM_STREAM_LISTENER = contextvars.ContextVar("spiritkin_llm_stream_listener", default=None)
_LLM_REASONING_STREAM_LISTENER = contextvars.ContextVar("spiritkin_llm_reasoning_stream_listener", default=None)


@contextmanager
def llm_stream_listener(listener, reasoning_listener=None):
    """Attach request-local answer and reasoning listeners without changing Agent APIs."""

    token = _LLM_STREAM_LISTENER.set(listener)
    reasoning_token = _LLM_REASONING_STREAM_LISTENER.set(reasoning_listener)
    try:
        yield
    finally:
        _LLM_REASONING_STREAM_LISTENER.reset(reasoning_token)
        _LLM_STREAM_LISTENER.reset(token)


def _notify_stream_listener(listener, token: str, accumulated: str) -> None:
    if not callable(listener) or not token:
        return
    listener(token, accumulated)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.5, float(raw))
    except ValueError:
        return default


def _llm_request_timeout() -> float:
    return _float_env("SPIRITKIN_LLM_REQUEST_TIMEOUT", 30.0)


def _llm_fallback_timeout() -> float:
    return _float_env("SPIRITKIN_LLM_FALLBACK_TIMEOUT", 8.0)


def _bounded_request_timeout(value: object | None) -> float:
    if value is None:
        return _llm_request_timeout()
    try:
        return max(1.0, min(180.0, float(value)))
    except (TypeError, ValueError):
        return _llm_request_timeout()


def _llm_reasoning_effort() -> str:
    # Leave this unset by default so reasoning-capable OpenAI-compatible models
    # can use their own thinking mode. Operators can still force an explicit
    # provider value (for example "low" or "none") through the environment.
    return os.getenv("SPIRITKIN_REASONING_EFFORT", "").strip()


def _with_reasoning_effort(payload: dict, explicit_effort: str | None = None) -> dict:
    requested = str(explicit_effort or "").strip().lower()
    effort = requested if requested and requested != "auto" else _llm_reasoning_effort()
    if effort:
        payload["reasoning_effort"] = effort
    return payload


def _resolved_reasoning_effort(explicit_effort: str | None = None) -> str:
    requested = str(explicit_effort or "").strip().lower()
    return requested if requested and requested != "auto" else _llm_reasoning_effort().lower()


class OpenAICompatibleEngine:
    def __init__(self, model_name: str, *, base_url: str = "http://localhost:11434", api_key: str = "", announce: bool = True):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self._is_ollama = "11434" in self.base_url
        self._is_lmstudio = "1234" in self.base_url
        self._is_llamacpp = "8080" in self.base_url or "llamacpp" in self.base_url.lower()
        if self._is_ollama:
            self._api_url = self.base_url.replace("/v1", "").rstrip("/") + "/api/chat"
        elif self._is_lmstudio:
            self._api_url = self.base_url + "/chat/completions"
        else:
            self._api_url = self.base_url + "/chat/completions"
        if announce:
            show_emotion("thinking", f"LLM: {model_name} @ {self.base_url}")

    def chat(self, prompt: str, **kwargs) -> str:
        on_token = kwargs.pop("on_token", None)
        on_reasoning = kwargs.pop("on_reasoning", None)
        reasoning_effort = kwargs.pop("reasoning_effort", "auto")
        request_timeout = _bounded_request_timeout(kwargs.pop("request_timeout", None))
        profile = resolve_text_generation_profile(mode=kwargs.pop("mode", None), config_path=kwargs.pop("config_path", "config/config.yaml"))
        temperature = kwargs.pop("temperature", None)
        top_p = kwargs.pop("top_p", None)
        max_new_tokens = kwargs.pop("max_new_tokens", None)
        if temperature is None:
            temperature = profile.get("temperature", 0.4)
        if top_p is None:
            top_p = profile.get("top_p", 0.85)
        if max_new_tokens is None:
            max_new_tokens = profile.get("max_new_tokens", 512)
        if callable(on_token):
            try:
                return self._chat_streaming(
                    prompt,
                    temperature=temperature,
                    top_p=top_p,
                    max_new_tokens=max_new_tokens,
                    on_token=on_token,
                    on_reasoning=on_reasoning,
                    reasoning_effort=reasoning_effort,
                    request_timeout=request_timeout,
                )
            except Exception:
                # Preserve the established non-stream fallback if a provider advertises
                # OpenAI compatibility but rejects or truncates an SSE request.
                pass
        if self._is_ollama:
            body = json.dumps({
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "top_p": top_p,
                    "num_predict": max_new_tokens,
                },
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            payload = _with_reasoning_effort({
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_new_tokens,
                "stream": False,
            }, reasoning_effort)
            if self._is_llamacpp and _resolved_reasoning_effort(reasoning_effort) == "none":
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            body = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self._api_url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                reasoning = ""
                # Handle both Ollama native format and OpenAI-compatible format
                if "choices" in data:
                    msg = data["choices"][0].get("message", {})
                    content = msg.get("content", "") or ""
                    reasoning = msg.get("reasoning_content", "") or ""
                    # Thinking model: content is often empty, reasoning has the thinking process
                    # Use content first, fall back to last sentence of reasoning
                    if (not content or len(content.strip()) < 3) and reasoning:
                        # Take the last non-thinking line
                        lines = [line for line in reasoning.split("\n") if line.strip() and not line.startswith(("*", "-", "1.", "2.", "3."))]
                        if lines:
                            content = lines[-1].strip()
                    if not content.strip() and reasoning:
                        content = reasoning.split("\n")[-1].strip()
                else:
                    msg = data.get("message", {})
                    content = msg.get("content", "") or ""
                    thinking = msg.get("thinking", "") or ""
                    reasoning = thinking
                    if not content.strip() and thinking:
                        content = thinking
                _notify_stream_listener(on_reasoning, reasoning, reasoning)
                content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
                content = content.strip()
                return content or "..."
        except Exception as e:
            if not self._is_ollama and self._is_lmstudio:
                # Fallback to Ollama if LM Studio is down
                from backend.app.settings import resolve_text_model
                fallback = resolve_text_model("qwen3-vl:4b")
                fallback_url = "http://localhost:11434/api/chat"
                try:
                    fb_body = json.dumps({"model": fallback, "messages": [{"role": "user", "content": prompt}], "stream": False}).encode("utf-8")
                    fb_req = urllib.request.Request(fallback_url, data=fb_body, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(fb_req, timeout=_llm_fallback_timeout()) as resp:
                        fb_data = json.loads(resp.read().decode("utf-8"))
                        return (fb_data.get("message", {}) or {}).get("content", "") or "..."
                except Exception:
                    pass
            raise RuntimeError(f"API error: {e}") from e

    def _chat_streaming(
        self,
        prompt: str,
        *,
        temperature,
        top_p,
        max_new_tokens,
        on_token,
        on_reasoning=None,
        reasoning_effort="auto",
        request_timeout=None,
    ) -> str:
        if self._is_ollama:
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "top_p": top_p,
                    "num_predict": max_new_tokens,
                },
            }
            headers = {"Content-Type": "application/json"}
        else:
            payload = _with_reasoning_effort({
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_new_tokens,
                "stream": True,
            }, reasoning_effort)
            if self._is_llamacpp and _resolved_reasoning_effort(reasoning_effort) == "none":
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self._api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        with urllib.request.urlopen(req, timeout=_bounded_request_timeout(request_timeout)) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = ""
                reasoning = ""
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else choice.get("message")
                    delta = delta if isinstance(delta, dict) else {}
                    content = str(delta.get("content") or "")
                    reasoning = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
                else:
                    message = data.get("message") if isinstance(data.get("message"), dict) else {}
                    content = str(message.get("content") or data.get("response") or "")
                    reasoning = str(message.get("thinking") or data.get("thinking") or "")

                if reasoning:
                    reasoning_parts.append(reasoning)
                    _notify_stream_listener(on_reasoning, reasoning, "".join(reasoning_parts))
                if content:
                    content_parts.append(content)
                    _notify_stream_listener(on_token, content, "".join(content_parts))

        content = "".join(content_parts).strip()
        if content:
            return content
        reasoning = "".join(reasoning_parts).strip()
        if reasoning:
            lines = [line.strip() for line in reasoning.splitlines() if line.strip()]
            return lines[-1] if lines else reasoning
        raise RuntimeError("stream response contained no visible content")


class QwenLocalEngine:
    def __init__(self, model_name=None, *, provider=None, config_path="config/config.yaml", announce=True):
        resolved_provider = resolve_text_provider(provider, config_path=config_path)
        if resolved_provider != "local_transformers":
            raise ValueError(f"unsupported: {resolved_provider}")
        self.model_name = resolve_text_model(model_name, config_path=config_path)
        allow_download = os.getenv("SPIRIT_ALLOW_MODEL_DOWNLOAD", "").strip().lower() in {"1", "true", "yes", "on"}
        if announce:
            show_emotion("thinking", f"Loading local model: {self.model_name}")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True, local_files_only=not allow_download)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, device_map="auto", trust_remote_code=True, torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32, local_files_only=not allow_download)
        if hasattr(self.model, "eval"):
            self.model.eval()

    def chat(self, prompt: str, **kwargs) -> str:
        import torch
        on_token = kwargs.pop("on_token", None)
        gen_config = resolve_text_generation_profile(mode=kwargs.pop("mode", "balanced"), config_path=kwargs.pop("config_path", "config/config.yaml"))
        formatted = f"<|im_start|>system\nYou are Spirit, a helpful Chinese assistant.<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        inputs = self.tokenizer(formatted, return_tensors="pt").to(self.model.device)
        if callable(on_token):
            from transformers import TextIteratorStreamer

            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
            errors: list[BaseException] = []

            def generate() -> None:
                try:
                    with torch.no_grad():
                        self.model.generate(**inputs, **gen_config, streamer=streamer)
                except BaseException as exc:  # surfaced on the request thread below
                    errors.append(exc)
                    try:
                        streamer.on_finalized_text("", stream_end=True)
                    except Exception:
                        pass

            worker = threading.Thread(target=generate, name="main-llm-token-stream", daemon=True)
            worker.start()
            parts: list[str] = []
            for token in streamer:
                if not token:
                    continue
                parts.append(token)
                _notify_stream_listener(on_token, token, "".join(parts))
            worker.join()
            if errors:
                raise errors[0]
            return "".join(parts).strip()
        with torch.no_grad():
            outputs = self.model.generate(**inputs, **gen_config)
        return self.tokenizer.decode(outputs[0][len(inputs.input_ids[0]):], skip_special_tokens=True).strip()


_qwen_engine = None
_qwen_engine_key = None
_qwen_engine_error_key = None
_qwen_engine_error = None


def _canonical_text_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value in {"llama_cpp", "llama.cpp", "llama-cpp"}:
        return "llamacpp"
    if value == "lm-studio":
        return "lmstudio"
    return value


def _provider_matches_configured_text_provider(provider: str, *, config_path: str) -> bool:
    return _canonical_text_provider(resolve_text_provider(config_path=config_path)) == _canonical_text_provider(provider)


def _configured_text_base_url_for_provider(provider: str, *, config_path: str) -> str:
    if not _provider_matches_configured_text_provider(provider, config_path=config_path):
        return ""
    return resolve_text_base_url(config_path=config_path).rstrip("/")


def _configured_text_api_key_for_provider(provider: str, *, config_path: str) -> str:
    if not _provider_matches_configured_text_provider(provider, config_path=config_path):
        return ""
    return resolve_text_api_key(config_path=config_path)


def _normalize_provider_for_text_engine(provider: str) -> str:
    provider = _canonical_text_provider(provider)
    if provider in {"ollama", "lmstudio", "llamacpp", "cloud_openai_compatible", "yundun", "yundun_openai_compatible"}:
        return "openai_compatible"
    return provider


def _resolve_text_base_url_for_provider(provider: str, *, config_path: str = "config/config.yaml") -> str:
    provider = _canonical_text_provider(provider)
    if provider == "ollama":
        return _first_env_value("OLLAMA_HOST").rstrip("/") or _configured_text_base_url_for_provider(provider, config_path=config_path) or "http://127.0.0.1:11434"
    if provider == "lmstudio":
        return _first_env_value("LMSTUDIO_BASE_URL").rstrip("/") or _configured_text_base_url_for_provider(provider, config_path=config_path) or "http://127.0.0.1:1234/v1"
    if provider == "llamacpp":
        return _first_env_value("LLAMACPP_BASE_URL").rstrip("/") or _configured_text_base_url_for_provider(provider, config_path=config_path) or "http://127.0.0.1:8080/v1"
    return resolve_text_base_url(config_path=config_path)


def _first_env_value(*names: str) -> str:
    for name in names:
        value = (os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _resolve_text_api_key_for_provider(provider: str, *, config_path: str = "config/config.yaml") -> str:
    provider = _canonical_text_provider(provider)
    generic_override = _first_env_value("SPIRITKIN_TEXT_API_KEY", "SPIRIT_TEXT_API_KEY")
    if generic_override:
        return generic_override
    if provider == "ollama":
        return ""
    if provider == "llamacpp":
        return _first_env_value("LLAMACPP_API_KEY") or _configured_text_api_key_for_provider(provider, config_path=config_path)
    if provider == "lmstudio":
        return _first_env_value("LMSTUDIO_API_KEY") or resolve_text_api_key(config_path=config_path)
    if provider in {"yundun", "yundun_openai_compatible"}:
        return _first_env_value("YUNDUN_API_KEY", "CLOUD_MODEL_API_KEY") or resolve_text_api_key(config_path=config_path)
    if provider == "cloud_openai_compatible":
        return _first_env_value("CLOUD_MODEL_API_KEY", "OPENAI_API_KEY", "SPIRITKIN_OPENAI_API_KEY") or resolve_text_api_key(config_path=config_path)
    return resolve_text_api_key(config_path=config_path)


def _api_key_fingerprint(api_key: str) -> str:
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def _text_engine_cache_key(provider: str, model_name: str, *, engine_provider: str, base_url: str = "", api_key: str = ""):
    if engine_provider == "openai_compatible":
        return (provider, model_name, base_url.rstrip("/"), _api_key_fingerprint(api_key))
    return (provider, model_name)


def _resolve_text_engine_connection(
    provider: str,
    *,
    config_path: str = "config/config.yaml",
    base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str, str]:
    engine_provider = _normalize_provider_for_text_engine(provider)
    if engine_provider != "openai_compatible":
        return engine_provider, "", ""
    resolved_base_url = str(base_url).rstrip("/") if base_url is not None else _resolve_text_base_url_for_provider(provider, config_path=config_path)
    resolved_api_key = str(api_key) if api_key is not None else _resolve_text_api_key_for_provider(provider, config_path=config_path)
    return engine_provider, resolved_base_url, resolved_api_key


def preload_llm_engine(*, model_name=None, provider=None, base_url=None, api_key=None, config_path="config/config.yaml", announce=True):
    global _qwen_engine, _qwen_engine_key, _qwen_engine_error_key, _qwen_engine_error
    rp = resolve_text_provider(provider, config_path=config_path)
    rm = resolve_text_model(model_name, config_path=config_path)
    engine_provider, bu, resolved_api_key = _resolve_text_engine_connection(
        rp,
        config_path=config_path,
        base_url=base_url,
        api_key=api_key,
    )
    key = _text_engine_cache_key(rp, rm, engine_provider=engine_provider, base_url=bu, api_key=resolved_api_key)
    if _qwen_engine is not None and _qwen_engine_key == key:
        return True
    if _qwen_engine_error_key == key:
        return False
    try:
        if engine_provider == "openai_compatible":
            _qwen_engine = OpenAICompatibleEngine(rm, base_url=bu, api_key=resolved_api_key, announce=announce)
        else:
            _qwen_engine = QwenLocalEngine(rm, provider=engine_provider, config_path=config_path, announce=announce)
        _qwen_engine_key = key
        _qwen_engine_error_key = None
        _qwen_engine_error = None
        return True
    except Exception as e:
        _qwen_engine = None
        _qwen_engine_key = None
        _qwen_engine_error_key = key
        _qwen_engine_error = e
        if announce:
            show_emotion("confused", f"LLM load failed: {e}")
        return False


def _build_unavailable_reply(query: str, model_name: str, error: object) -> str:
    strict = os.getenv("SPIRITKIN_STRICT_LLM_UNAVAILABLE", "").strip().lower() in {"1", "true", "yes", "on"}
    if strict:
        return f"模型 {model_name} 暂时不可用（本次加载错误：{error}）。请检查模型文件或环境配置后重试。<emotion:confused>"
    looks_like_voice = any(token in (query or "") for token in ("当前输入", "听到", "麦克风", "说话", "语音"))
    if looks_like_voice:
        return "我听到了你的声音，但目前本地模型还没就绪。可以告诉我要做的具体指令，或检查一下麦克风输入和模型文件。<emotion:happy>"
    import random
    fb = ["Voice online. Try a specific command.", "Heard you. Say a command like 'search Python'.", "Listening. What can I do for you?"]
    return f"{random.choice(fb)}<emotion:neutral>"


def get_llm_response(query, *, model_name=None, provider=None, base_url=None, api_key=None, reasoning_effort="auto", mode=None, temperature=None, top_p=None, max_new_tokens=None, request_timeout=None, config_path="config/config.yaml", on_token=None):
    global _qwen_engine, _qwen_engine_key, _qwen_engine_error_key, _qwen_engine_error
    rp = resolve_text_provider(provider, config_path=config_path)
    rm = resolve_text_model(model_name, config_path=config_path)
    engine_provider, bu, resolved_api_key = _resolve_text_engine_connection(
        rp,
        config_path=config_path,
        base_url=base_url,
        api_key=api_key,
    )
    key = _text_engine_cache_key(rp, rm, engine_provider=engine_provider, base_url=bu, api_key=resolved_api_key)
    if _qwen_engine_error_key == key:
        return _build_unavailable_reply(query, rm, _qwen_engine_error)
    if _qwen_engine is None or _qwen_engine_key != key:
        if not preload_llm_engine(
            model_name=rm,
            provider=rp,
            base_url=bu,
            api_key=resolved_api_key,
            config_path=config_path,
            announce=False,
        ):
            return _build_unavailable_reply(query, rm, _qwen_engine_error)
    listener = on_token if callable(on_token) else _LLM_STREAM_LISTENER.get()
    reasoning_listener = _LLM_REASONING_STREAM_LISTENER.get()
    return _qwen_engine.chat(
        query,
        mode=mode,
        temperature=temperature,
        top_p=top_p,
        max_new_tokens=max_new_tokens,
        config_path=config_path,
        on_token=listener,
        on_reasoning=reasoning_listener,
        reasoning_effort=reasoning_effort,
        request_timeout=request_timeout,
    )
