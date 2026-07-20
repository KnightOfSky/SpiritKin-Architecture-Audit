from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from backend.app.settings import (
    resolve_asr_compute_type,
    resolve_asr_device,
    resolve_asr_model_size,
    resolve_asr_profile,
)
from backend.expression.avatar import show_emotion
from backend.prompts.voice import (
    ASR_INITIAL_PROMPT_AUTO,
    ASR_INITIAL_PROMPT_YUE,
    ASR_INITIAL_PROMPT_ZH,
)

LOCAL_ASR_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "models", "asr")
LOCAL_ASR_MODEL_PATH = os.path.join(LOCAL_ASR_MODEL_DIR, "faster-whisper-base")
_recognizer = None
_model = None
_model_size = None


ASR_HALLUCINATION_PHRASES = (
    "请不吝点赞",
    "点赞订阅",
    "订阅转发",
    "请点赞",
    "谢谢观看",
    "感谢观看",
    "下期再见",
    "欢迎订阅",
)

ASR_NOISE_ONLY_TEXTS = {
    "啊",
    "呀",
    "嗯",
    "唉",
    "诶",
    "呃",
    "额",
    "喂",
    "哎",
    "哦",
    "哈",
    "s",
    "ss",
}

ASR_COMMAND_HINT_CHARS = "打开启动运行看读发搜查按点移输关停退写创建新建切换调设帮总结讲说"

DEFAULT_LOOPBACK_INPUT_PATTERNS = (
    "stereo mix",
    "立体声混音",
    "what u hear",
    "loopback",
    "wasapi loopback",
    "monitor of",
    "wave out",
    "virtual audio",
    "vb-audio",
    "voicemeeter",
    "cable output",
    "系统声音",
    "扬声器",
    "sound mapper",
    "映射器",
    "mapper",
)

MIC_PREFERRED_TERMS = (
    "麦克风",
    "microphone",
    "mic",
    "headset",
    "耳机",
)


def _compact_asr_text(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?:：；;、~～…\-—_]+", "", (text or "").strip().lower())


def _split_env_terms(value: str | None) -> list[str]:
    return [term.strip().lower() for term in re.split(r"[,，;；|]", value or "") if term.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def list_microphone_devices() -> list[dict[str, object]]:
    sr = _get_sr()
    names = list(sr.Microphone.list_microphone_names() or [])
    block_terms = _microphone_block_terms()
    input_channels: dict[int, int] = {}
    audio = None
    try:
        pyaudio = sr.Microphone.get_pyaudio()
        audio = pyaudio.PyAudio()
        for index in range(len(names)):
            info = audio.get_device_info_by_index(index)
            input_channels[index] = max(0, int(info.get("maxInputChannels") or 0))
    except Exception:
        input_channels = {}
    finally:
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
    return [
        {
            "index": index,
            "name": name,
            "input_channels": input_channels.get(index),
            "blocked": _matches_any_term(name, block_terms) or input_channels.get(index) == 0,
        }
        for index, name in enumerate(names)
    ]


def _microphone_block_terms() -> list[str]:
    custom_terms = _split_env_terms(os.getenv("SPIRITKIN_MIC_BLOCKLIST") or os.getenv("SPIRIT_MIC_BLOCKLIST"))
    return [*DEFAULT_LOOPBACK_INPUT_PATTERNS, *custom_terms]


def _matches_any_term(name: str, terms: list[str] | tuple[str, ...]) -> bool:
    normalized = str(name or "").strip().lower()
    return bool(normalized and any(term and term in normalized for term in terms))


def resolve_microphone_device_index() -> tuple[int | None, dict[str, object]]:
    explicit_index = (
        os.getenv("SPIRITKIN_MIC_INDEX")
        or os.getenv("SPIRITKIN_MIC_DEVICE_INDEX")
        or os.getenv("SPIRIT_MIC_INDEX")
        or os.getenv("SPIRIT_MIC_DEVICE_INDEX")
        or ""
    ).strip()
    if explicit_index:
        index = int(explicit_index)
        return index, {"index": index, "selection": "explicit_index"}

    devices = list_microphone_devices()
    allow_terms = _split_env_terms(os.getenv("SPIRITKIN_MIC_ALLOWLIST") or os.getenv("SPIRIT_MIC_ALLOWLIST"))
    block_terms = _microphone_block_terms()

    if allow_terms:
        for device in devices:
            name = str(device.get("name") or "")
            if _matches_any_term(name, allow_terms):
                return int(device["index"]), {"index": device["index"], "name": name, "selection": "allowlist"}

    if _env_bool("SPIRITKIN_MIC_AUTO_SELECT", True):
        preferred = None
        for device in devices:
            name = str(device.get("name") or "")
            if _matches_any_term(name, block_terms):
                continue
            if _matches_any_term(name, MIC_PREFERRED_TERMS):
                return int(device["index"]), {"index": device["index"], "name": name, "selection": "auto_preferred_mic"}
            if preferred is None:
                preferred = device
        if preferred is not None:
            name = str(preferred.get("name") or "")
            return int(preferred["index"]), {"index": preferred["index"], "name": name, "selection": "auto_non_loopback"}

    return None, {"index": None, "selection": "system_default"}


def is_probable_asr_hallucination_text(text: str) -> bool:
    compact = _compact_asr_text(text)
    if not compact:
        return False

    if compact in ASR_NOISE_ONLY_TEXTS:
        return True

    if any(phrase in compact for phrase in ASR_HALLUCINATION_PHRASES):
        return True

    if len(compact) <= 3 and not re.search(f"[{ASR_COMMAND_HINT_CHARS}]", compact):
        if re.fullmatch(r"[a-z]+", compact) or compact in ASR_NOISE_ONLY_TEXTS:
            return True

    if len(compact) <= 2 and not re.search(f"[{ASR_COMMAND_HINT_CHARS}0-9A-Za-z]", compact):
        return True

    if 3 <= len(compact) <= 10:
        most_common_short = max((compact.count(char) for char in set(compact)), default=0)
        if most_common_short / len(compact) >= 0.75:
            return True

    if len(compact) >= 12:
        most_common = max((compact.count(char) for char in set(compact)), default=0)
        if most_common >= 8 and most_common / len(compact) >= 0.45:
            return True

    return False


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _min_command_rms() -> int:
    return _env_int("SPIRITKIN_ASR_MIN_RMS", 650, minimum=0, maximum=5000)


def _asr_language() -> str | None:
    raw = (os.getenv("SPIRITKIN_ASR_LANGUAGE") or os.getenv("SPIRITKIN_VOICE_LOCALE") or "auto").strip().lower()
    aliases = {
        "auto": "",
        "detect": "",
        "auto-detect": "",
        "cantonese": "yue",
        "粤语": "yue",
        "粵語": "yue",
        "zh-hk": "yue",
        "zh_hk": "yue",
        "hongkong": "yue",
        "mandarin": "zh",
        "putonghua": "zh",
        "zh-cn": "zh",
        "zh_cn": "zh",
    }
    selected = aliases.get(raw, raw or "zh")
    return selected or None


def _asr_initial_prompt() -> str:
    if _asr_language() == "yue":
        return ASR_INITIAL_PROMPT_YUE
    if _asr_language() is None:
        return ASR_INITIAL_PROMPT_AUTO
    return ASR_INITIAL_PROMPT_ZH


def _is_too_short_non_command_text(text: str) -> bool:
    compact = _compact_asr_text(text)
    if not compact:
        return True
    if re.search(f"[{ASR_COMMAND_HINT_CHARS}0-9A-Za-z]", compact):
        return False
    return len(compact) <= _env_int("SPIRITKIN_ASR_MIN_NON_COMMAND_CHARS", 4, minimum=1, maximum=20)


def configure_recognizer_for_voice_commands(recognizer):
    """Tune SpeechRecognition for short command turns instead of long dictation."""

    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = _env_float("SPIRITKIN_ASR_PAUSE_THRESHOLD", 0.9, minimum=0.2, maximum=2.0)
    recognizer.phrase_threshold = _env_float("SPIRITKIN_ASR_PHRASE_THRESHOLD", 0.45, minimum=0.1, maximum=2.0)
    recognizer.non_speaking_duration = _env_float("SPIRITKIN_ASR_NON_SPEAKING_DURATION", 0.45, minimum=0.1, maximum=1.5)
    return recognizer


def configure_recognizer_for_hotword(recognizer):
    """Tune SpeechRecognition for fastest possible short hotword capture."""

    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = _env_float("SPIRITKIN_HOTWORD_PAUSE_THRESHOLD", 0.18, minimum=0.08, maximum=0.8)
    recognizer.phrase_threshold = _env_float("SPIRITKIN_HOTWORD_PHRASE_THRESHOLD", 0.08, minimum=0.03, maximum=0.8)
    recognizer.non_speaking_duration = _env_float("SPIRITKIN_HOTWORD_NON_SPEAKING_DURATION", 0.08, minimum=0.03, maximum=0.5)
    return recognizer


class AsrModelUnavailableError(RuntimeError):
    """Raised when the requested ASR model is unavailable and download/fallback is disabled."""


def _has_cached_asr_model(model_size: str = "base") -> bool:
    model_dir = Path(LOCAL_ASR_MODEL_DIR)
    if (model_dir / f"faster-whisper-{model_size}").exists():
        return True

    cache_dir = model_dir / f"models--Systran--faster-whisper-{model_size}" / "snapshots"
    return cache_dir.exists() and any(cache_dir.iterdir())


def _asr_model_reference(model_size: str) -> str:
    direct_model_dir = Path(LOCAL_ASR_MODEL_DIR) / f"faster-whisper-{model_size}"
    if direct_model_dir.exists():
        return str(direct_model_dir)
    return model_size


def _allow_model_download() -> bool:
    return os.getenv("SPIRIT_ALLOW_MODEL_DOWNLOAD", "").strip().lower() in {"1", "true", "yes", "on"}


def _requested_asr_model_size(override: str | None = None) -> str:
    return (override or os.getenv("SPIRITKIN_ASR_MODEL_SIZE") or resolve_asr_model_size()).strip().lower()


def resolve_asr_model_selection(*, allow_fallback: bool = True, model_size: str | None = None) -> dict[str, object]:
    requested = _requested_asr_model_size(model_size)
    allow_download = _allow_model_download()

    if _has_cached_asr_model(requested):
        return {
            "requested": requested,
            "selected": requested,
            "cached": True,
            "local_only": True,
            "allow_download": allow_download,
            "fallback": False,
            "available": True,
        }

    if allow_download:
        return {
            "requested": requested,
            "selected": requested,
            "cached": False,
            "local_only": False,
            "allow_download": True,
            "fallback": False,
            "available": True,
        }

    if allow_fallback:
        for candidate in ("large-v3-turbo", "medium", "small", "base", "tiny"):
            if _has_cached_asr_model(candidate):
                return {
                    "requested": requested,
                    "selected": candidate,
                    "cached": True,
                    "local_only": True,
                    "allow_download": False,
                    "fallback": True,
                    "available": True,
                }

    return {
        "requested": requested,
        "selected": requested,
        "cached": False,
        "local_only": False,
        "allow_download": False,
        "fallback": False,
        "available": False,
    }


def _get_sr():
    import speech_recognition as sr

    return sr


def _get_recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = _get_sr().Recognizer()
    return _recognizer


def calibrate_microphone(duration=2):
    recognizer = _get_recognizer()
    configure_recognizer_for_voice_commands(recognizer)
    sr = _get_sr()
    show_emotion("listening", "正在校准麦克风环境噪音，请保持安静...")
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=duration)
    # Cap the threshold: too high = misses quiet speech
    max_threshold = int(os.getenv("SPIRITKIN_MIC_MAX_THRESHOLD", "600"))
    if recognizer.energy_threshold > max_threshold:
        recognizer.energy_threshold = max_threshold
    show_emotion("done", f"校准完成，energy_threshold = {recognizer.energy_threshold:.2f}")
    return recognizer


def get_whisper_model(*, allow_fallback: bool = True, model_size: str | None = None):
    global _model, _model_size
    selection = resolve_asr_model_selection(allow_fallback=allow_fallback, model_size=model_size)
    selected = str(selection["selected"])

    if not selection["available"]:
        requested = selection["requested"]
        raise AsrModelUnavailableError(
            f"未找到 faster-whisper-{requested} 本地缓存，且 SPIRIT_ALLOW_MODEL_DOWNLOAD 未开启。"
            "请先设置 SPIRIT_ALLOW_MODEL_DOWNLOAD=1 下载模型，或改用已缓存模型，"
            "或显式允许回退到本地低质量模型。"
        )

    if _model is None or _model_size != selected:
        from faster_whisper import WhisperModel

        os.makedirs(LOCAL_ASR_MODEL_DIR, exist_ok=True)
        device = resolve_asr_device()
        compute_type = resolve_asr_compute_type()
        _model_size = selected
        if selection["fallback"]:
            show_emotion(
                "thinking",
                f"未找到 faster-whisper-{selection['requested']} 缓存，改用本地可用模型: faster-whisper-{selected}",
            )
        local_only = bool(selection["local_only"])
        model_reference = _asr_model_reference(selected)
        try:
            if local_only:
                show_emotion("thinking", f"加载本地语音识别模型缓存: faster-whisper-{selected}")
                _model = WhisperModel(
                    model_reference,
                    device=device,
                    compute_type=compute_type,
                    download_root=LOCAL_ASR_MODEL_DIR,
                    local_files_only=True,
                )
            else:
                show_emotion("thinking", f"首次使用：正在下载 Faster-Whisper {selected} 模型到项目目录...")
                _model = WhisperModel(model_reference, device=device, compute_type=compute_type, download_root=LOCAL_ASR_MODEL_DIR)
                show_emotion("done", "语音模型下载完成，已保存至项目 models/asr/ 目录")
        except Exception as exc:
            _model = None
            _model_size = None
            operation = "加载本地" if local_only else "下载/加载"
            raise AsrModelUnavailableError(f"{operation} faster-whisper-{selected} 失败: {exc}") from exc
    return _model


def _is_probably_low_confidence_segment(segment) -> bool:
    text = (getattr(segment, "text", "") or "").strip()
    if not text:
        return True

    if is_probable_asr_hallucination_text(text):
        return True

    no_speech_prob = getattr(segment, "no_speech_prob", None)
    if no_speech_prob is not None and no_speech_prob >= _env_float("SPIRITKIN_ASR_NO_SPEECH_THRESHOLD", 0.85):
        return True

    avg_logprob = getattr(segment, "avg_logprob", None)
    if avg_logprob is not None and avg_logprob <= _env_float("SPIRITKIN_ASR_LOW_LOGPROB_THRESHOLD", -1.4):
        return True

    return False


def _collect_transcript_segments(segments) -> tuple[str | None, list[dict[str, object]], int]:
    accepted_texts: list[str] = []
    diagnostics: list[dict[str, object]] = []
    rejected = 0

    for segment in segments:
        text = (getattr(segment, "text", "") or "").strip()
        low_confidence = _is_probably_low_confidence_segment(segment)
        diagnostics.append(
            {
                "text": text,
                "start": getattr(segment, "start", None),
                "end": getattr(segment, "end", None),
                "avg_logprob": getattr(segment, "avg_logprob", None),
                "no_speech_prob": getattr(segment, "no_speech_prob", None),
                "hallucination": is_probable_asr_hallucination_text(text),
                "accepted": bool(text and not low_confidence),
            }
        )
        if text and not low_confidence:
            accepted_texts.append(text)
        elif text:
            rejected += 1

    text = "".join(accepted_texts).strip()
    return (text or None), diagnostics, rejected


def listen_from_microphone_with_metrics(timeout=8, phrase_time_limit=12) -> dict[str, object]:
    metrics: dict[str, object] = {
        "text": None,
        "listen_elapsed": 0.0,
        "transcribe_elapsed": 0.0,
        "elapsed": 0.0,
        "microphone": {},
        "segments": [],
        "rejected_segments": 0,
        "error": None,
    }
    started = time.perf_counter()
    try:
        recognizer = _get_recognizer()
        configure_recognizer_for_voice_commands(recognizer)
        sr = _get_sr()
        microphone_index, microphone_metadata = resolve_microphone_device_index()
        metrics["microphone"] = microphone_metadata

        with sr.Microphone(device_index=microphone_index) as source:
            show_emotion("listening", "正在监听...（请说话）")
            listen_started = time.perf_counter()
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            metrics["listen_elapsed"] = time.perf_counter() - listen_started
            # Debug mic signal level
            rms = None
            try:
                import audioop
                rms = audioop.rms(audio.get_wav_data(), 2)
                metrics["rms"] = rms
                min_rms = _min_command_rms()
                if rms < min_rms:
                    metrics["error"] = "low_rms_noise"
                    metrics["noise_gate"] = {"rms": rms, "min_rms": min_rms}
                    print(f"[mic] noise gated rms={rms} < {min_rms}")
                    return metrics
                else:
                    print(f"[mic] signal ok rms={rms}")
            except Exception:
                pass

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = tmp_file.name
                tmp_file.write(audio.get_wav_data())

            try:
                asr_profile = resolve_asr_profile()
                transcribe_started = time.perf_counter()
                segments, _ = get_whisper_model().transcribe(
                    tmp_path,
                    language=_asr_language(),
                    beam_size=int(asr_profile.get("beam_size", 5)),
                    vad_filter=bool(asr_profile.get("vad_filter", True)),
                    temperature=float(asr_profile.get("temperature", 0.0)),
                    condition_on_previous_text=False,
                    initial_prompt=_asr_initial_prompt(),
                )
                text, segment_diagnostics, rejected_segments = _collect_transcript_segments(segments)
                metrics["transcribe_elapsed"] = time.perf_counter() - transcribe_started
                if text and _is_too_short_non_command_text(text):
                    metrics["text"] = None
                    metrics["error"] = "short_non_command_noise"
                    metrics["noise_gate"] = {"text": text, "reason": "too_short_without_command_hint"}
                    print(f"[asr] noise gated short text: {text}")
                else:
                    metrics["text"] = text
                metrics["segments"] = segment_diagnostics
                metrics["rejected_segments"] = rejected_segments
                return metrics
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        metrics["error"] = f"missing_dependency:{missing}"
        show_emotion("error", f"语音监听依赖缺失: {missing}。请安装 requirements.txt 中的 SpeechRecognition/PyAudio 后重试。")
        return metrics
    except sr.WaitTimeoutError:
        metrics["error"] = "wait_timeout"
        show_emotion("waiting", "监听超时，未检测到语音")
        return metrics
    except Exception as exc:
        metrics["error"] = str(exc)
        show_emotion("error", f"语音识别失败: {exc}")
        return metrics
    finally:
        metrics["elapsed"] = time.perf_counter() - started


def listen_from_microphone(timeout=8, phrase_time_limit=12):
    return listen_from_microphone_with_metrics(timeout=timeout, phrase_time_limit=phrase_time_limit).get("text")
