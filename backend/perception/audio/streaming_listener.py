from __future__ import annotations

import abc
import math
import os
import re
import sys
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class StreamingTranscriber(abc.ABC):
    @abc.abstractmethod
    def process_chunk(self, audio_bytes: bytes) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def finalize(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def reset(self) -> None:
        raise NotImplementedError


class WhisperChunkTranscriber(StreamingTranscriber):
    def __init__(
        self,
        model_size: str = "tiny",
        *,
        model: Any | None = None,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        partial_interval_seconds: float = 0.6,
        minimum_decode_seconds: float = 0.32,
        maximum_buffer_seconds: float = 30.0,
    ):
        self._model_size = model_size
        self._model = model
        self._sample_rate = max(1, int(sample_rate))
        self._sample_width = max(1, int(sample_width))
        self._channels = max(1, int(channels))
        self._partial_interval_bytes = self._seconds_to_bytes(partial_interval_seconds)
        self._minimum_decode_bytes = self._seconds_to_bytes(minimum_decode_seconds)
        self._maximum_buffer_bytes = self._seconds_to_bytes(maximum_buffer_seconds)
        self._accumulated: list[bytes] = []
        self._accumulated_bytes = 0
        self._last_decoded_bytes = 0
        self._partial = ""

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from backend.perception.audio.listener import get_whisper_model

            self._model = get_whisper_model(allow_fallback=True, model_size=self._model_size)
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "streaming ASR requires faster-whisper and numpy; install requirements.txt before enabling microphone streaming"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"streaming ASR model is unavailable: {exc}. Cache a faster-whisper model under backend/models/asr "
                "or set SPIRIT_ALLOW_MODEL_DOWNLOAD=1 for the first download"
            ) from exc
        return self._model

    def process_chunk(self, audio_bytes: bytes) -> str:
        if not audio_bytes:
            return self._partial
        chunk = bytes(audio_bytes)
        self._accumulated.append(chunk)
        self._accumulated_bytes += len(chunk)
        self._trim_buffer()
        if self._accumulated_bytes < self._minimum_decode_bytes:
            return self._partial
        if self._accumulated_bytes - self._last_decoded_bytes < self._partial_interval_bytes:
            return self._partial
        self._partial = self._decode()
        return self._partial

    def finalize(self) -> str:
        if self._accumulated_bytes <= 0:
            return self._partial
        if self._accumulated_bytes != self._last_decoded_bytes or not self._partial:
            self._partial = self._decode()
        return self._partial

    def reset(self) -> None:
        self._accumulated = []
        self._accumulated_bytes = 0
        self._last_decoded_bytes = 0
        self._partial = ""

    def _seconds_to_bytes(self, seconds: float) -> int:
        return max(self._sample_width * self._channels, int(max(0.0, float(seconds)) * self._sample_rate * self._sample_width * self._channels))

    def _trim_buffer(self) -> None:
        if self._maximum_buffer_bytes <= 0 or self._accumulated_bytes <= self._maximum_buffer_bytes:
            return
        overflow = self._accumulated_bytes - self._maximum_buffer_bytes
        while self._accumulated and overflow >= len(self._accumulated[0]):
            removed = len(self._accumulated.pop(0))
            overflow -= removed
            self._accumulated_bytes -= removed
        if overflow > 0 and self._accumulated:
            self._accumulated[0] = self._accumulated[0][overflow:]
            self._accumulated_bytes -= overflow
        self._last_decoded_bytes = 0

    def _decode(self) -> str:
        if self._sample_width != 2:
            raise RuntimeError("streaming ASR currently requires signed 16-bit PCM audio")
        try:
            import numpy as np

            from backend.app.settings import resolve_asr_profile
            from backend.perception.audio.listener import (
                _asr_initial_prompt,
                _asr_language,
                _collect_transcript_segments,
            )
        except ModuleNotFoundError as exc:
            raise RuntimeError("streaming ASR requires numpy; install requirements.txt") from exc

        pcm = b"".join(self._accumulated)
        frame_size = self._sample_width * self._channels
        pcm = pcm[: len(pcm) - (len(pcm) % frame_size)]
        if not pcm:
            return self._partial
        audio = np.frombuffer(pcm, dtype="<i2")
        if self._channels > 1:
            audio = audio.reshape(-1, self._channels).mean(axis=1)
        audio = audio.astype(np.float32) / 32768.0
        profile = resolve_asr_profile()
        segments, _ = self._ensure_model().transcribe(
            audio,
            language=_asr_language(),
            beam_size=1,
            vad_filter=False,
            temperature=float(profile.get("temperature", 0.0)),
            condition_on_previous_text=False,
            initial_prompt=_asr_initial_prompt(),
        )
        text, _, _ = _collect_transcript_segments(segments)
        self._last_decoded_bytes = self._accumulated_bytes
        return text or ""


class SileroVADWrapper:
    def __init__(self, threshold: float = 0.5, min_speech_duration_ms: float = 250.0, min_silence_duration_ms: float = 500.0):
        self._threshold = threshold
        self._min_speech_duration_ms = min_speech_duration_ms
        self._min_silence_duration_ms = min_silence_duration_ms
        self._model = None
        self._speech_active = False
        self._speech_start = 0.0
        self._silence_start = 0.0

    def is_speech(self, audio_chunk: bytes, sample_rate: int = 16000) -> bool:
        if len(audio_chunk) < 2:
            return False
        samples = array("h")
        samples.frombytes(audio_chunk[: len(audio_chunk) - (len(audio_chunk) % 2)])
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return False
        rms = math.sqrt(sum(int(sample) * int(sample) for sample in samples) / len(samples))
        configured = os.getenv("SPIRITKIN_STREAMING_VAD_RMS", "").strip()
        threshold = float(configured) if configured else 250.0 + max(0.0, min(1.0, self._threshold)) * 500.0
        return rms >= threshold

    def reset(self) -> None:
        self._speech_active = False
        self._speech_start = 0.0
        self._silence_start = 0.0


def _normalize_wake_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (text or "").strip().lower())


@dataclass
class StreamingWakewordGate:
    hotword: str = "Spirit"
    wake_window_seconds: float = 60.0
    require_hotword: bool = True
    active_until: float = 0.0

    def is_active(self) -> bool:
        return (not self.require_hotword) or time.monotonic() < self.active_until

    def process_transcript(self, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        normalized = _normalize_wake_text(raw)
        normalized_hotword = _normalize_wake_text(self.hotword)
        activated = bool(normalized_hotword and normalized.startswith(normalized_hotword))
        accepted = self.is_active() or activated
        cleaned = raw
        if activated:
            self.active_until = time.monotonic() + max(5.0, float(self.wake_window_seconds or 60.0))
            cleaned = re.sub(re.escape(self.hotword), "", raw, count=1, flags=re.IGNORECASE).strip() or raw
        return {
            "accepted": accepted,
            "activated": activated,
            "cleaned_text": cleaned,
            "reason": "accepted" if accepted else "wake_required",
            "wake_active": self.is_active(),
            "wake_window_seconds": self.wake_window_seconds,
        }


@dataclass
class StreamingSession:
    transcriber: StreamingTranscriber
    vad: SileroVADWrapper | None = None
    partial_text: str = ""
    final_text: str = ""
    is_speaking: bool = False
    speech_started_at: float = 0.0
    silence_started_at: float = 0.0
    silence_timeout_seconds: float = 1.5
    event_sink: Callable[[dict[str, Any]], None] | None = None
    wakeword_gate: StreamingWakewordGate | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def feed_audio(self, audio_bytes: bytes) -> str:
        now = time.time()
        has_speech = self.vad.is_speech(audio_bytes) if self.vad else bool(audio_bytes)

        if has_speech:
            if not self.is_speaking:
                self.is_speaking = True
                self.speech_started_at = now
                self._emit("asr.speech_started", {"timestamp": now})
            self.silence_started_at = 0.0
            self.partial_text = self.transcriber.process_chunk(audio_bytes)
            self._emit("asr.partial", {"text": self.partial_text, "speaking": True})
        else:
            if self.is_speaking:
                if self.silence_started_at == 0.0:
                    self.silence_started_at = now
                elif now - self.silence_started_at >= self.silence_timeout_seconds:
                    self.finalize_utterance()
        return self.partial_text or self.final_text

    def reset(self) -> None:
        self.transcriber.reset()
        if self.vad:
            self.vad.reset()
        self.partial_text = ""
        self.final_text = ""
        self.is_speaking = False

    def finalize_utterance(self, *, reason: str = "silence") -> str:
        self.final_text = self.transcriber.finalize()
        self.transcriber.reset()
        self.is_speaking = False
        self.partial_text = ""
        self.silence_started_at = 0.0
        gate = self.wakeword_gate.process_transcript(self.final_text) if self.wakeword_gate else {
            "accepted": True,
            "cleaned_text": self.final_text,
            "reason": "accepted",
        }
        self._emit("asr.final", {"text": self.final_text, "finalize_reason": reason, **gate})
        return self.final_text

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink({"type": event_type, "schema_version": "v1", "payload": payload})
        except Exception:
            pass


def create_streaming_listener(
    model_size: str = "tiny",
    *,
    silence_timeout: float = 1.5,
    vad_threshold: float = 0.5,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    hotword: str = "Spirit",
    require_hotword: bool = False,
) -> StreamingSession:
    transcriber = WhisperChunkTranscriber(model_size=model_size)
    vad = SileroVADWrapper(threshold=vad_threshold)
    gate = StreamingWakewordGate(hotword=hotword, require_hotword=require_hotword) if require_hotword else None
    return StreamingSession(transcriber=transcriber, vad=vad, silence_timeout_seconds=silence_timeout, event_sink=event_sink, wakeword_gate=gate)
