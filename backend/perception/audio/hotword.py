from __future__ import annotations

import os
import re
import tempfile
import time

from backend.expression.avatar import show_emotion

_wake_model = None
LOCAL_HOTWORD_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "models", "asr")


def _has_cached_model(model_size: str) -> bool:
    cache_dir = os.path.join(
        LOCAL_HOTWORD_MODEL_DIR,
        f"models--Systran--faster-whisper-{model_size}",
        "snapshots",
    )
    return os.path.isdir(cache_dir) and bool(os.listdir(cache_dir))


def _normalize_hotword_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _resolve_hotword_language(hotword: str) -> str | None:
    normalized = (hotword or "").strip()
    if normalized and re.fullmatch(r"[A-Za-z][A-Za-z0-9 _\-]*", normalized):
        return "en"
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return "zh"
    return None


def get_wake_model():
    global _wake_model
    if _wake_model is None:
        from faster_whisper import WhisperModel

        os.makedirs(LOCAL_HOTWORD_MODEL_DIR, exist_ok=True)
        try:
            if _has_cached_model("tiny"):
                show_emotion("thinking", "加载本地热词模型缓存: faster-whisper-tiny")
                _wake_model = WhisperModel(
                    "tiny",
                    device="cpu",
                    compute_type="int8",
                    download_root=LOCAL_HOTWORD_MODEL_DIR,
                    local_files_only=True,
                )
            elif _has_cached_model("base"):
                show_emotion("thinking", "未找到 Tiny 热词模型，直接使用本地 Base 模型...")
                _wake_model = WhisperModel(
                    "base",
                    device="cpu",
                    compute_type="int8",
                    download_root=LOCAL_HOTWORD_MODEL_DIR,
                    local_files_only=True,
                )
            else:
                show_emotion("thinking", "首次使用：正在下载 Faster-Whisper Tiny 热词模型到项目目录...")
                _wake_model = WhisperModel(
                    "tiny",
                    device="cpu",
                    compute_type="int8",
                    download_root=LOCAL_HOTWORD_MODEL_DIR,
                )
                show_emotion("done", "热词模型下载完成，已保存至项目 backend/models/asr/ 目录")
        except Exception as exc:
            if not _has_cached_model("base"):
                raise RuntimeError("热词模型下载失败，且本地没有可回退的 Base 模型缓存，请检查网络后重试。") from exc

            show_emotion("thinking", "Tiny 热词模型不可用，回退到本地 Base 模型...")
            _wake_model = WhisperModel(
                "base",
                device="cpu",
                compute_type="int8",
                download_root=LOCAL_HOTWORD_MODEL_DIR,
                local_files_only=True,
            )
    return _wake_model


def detect_hotword(audio_data, hotword="Spirit"):
    """检测音频中是否包含热词。"""
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(audio_data.get_wav_data())

    try:
        language = _resolve_hotword_language(hotword)
        beam_size = int(os.getenv("SPIRITKIN_HOTWORD_BEAM_SIZE", "1").strip() or "1")
        fast_mode = os.getenv("SPIRITKIN_HOTWORD_FAST", "1").strip().lower() in {"1", "true", "yes", "on"}
        default_vad = "0" if fast_mode else "1"
        vad_filter = os.getenv("SPIRITKIN_HOTWORD_VAD", default_vad).strip().lower() in {"1", "true", "yes", "on"}
        initial_prompt = os.getenv("SPIRITKIN_HOTWORD_INITIAL_PROMPT", hotword).strip() or None
        segments, _ = get_wake_model().transcribe(
            tmp_path,
            language=language,
            beam_size=max(1, beam_size),
            temperature=0.0,
            vad_filter=vad_filter,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
        )
        text = "".join(segment.text for segment in segments).strip()
        normalized_text = _normalize_hotword_text(text)
        normalized_hotword = _normalize_hotword_text(hotword)
        if os.getenv("SPIRITKIN_DEBUG_HOTWORD", "").strip() in {"1", "true", "yes", "on"}:
            elapsed = time.perf_counter() - started
            print(f"[hotword] heard={text!r} normalized={normalized_text!r} target={normalized_hotword!r} vad={vad_filter} elapsed={elapsed:.3f}s")
        return bool(normalized_hotword) and normalized_hotword in normalized_text
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)