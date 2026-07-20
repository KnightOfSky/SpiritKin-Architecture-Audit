from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from backend.perception.audio.listener import list_microphone_devices, resolve_microphone_device_index
from backend.perception.audio.streaming_listener import StreamingSession, create_streaming_listener


class VoiceCallPhase(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    ERROR = "error"


_ALLOWED_TRANSITIONS: dict[VoiceCallPhase, set[VoiceCallPhase]] = {
    VoiceCallPhase.IDLE: {VoiceCallPhase.CONNECTING, VoiceCallPhase.ENDED, VoiceCallPhase.ERROR},
    VoiceCallPhase.CONNECTING: {
        VoiceCallPhase.LISTENING,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.LISTENING: {
        VoiceCallPhase.THINKING,
        VoiceCallPhase.SPEAKING,
        VoiceCallPhase.INTERRUPTED,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.THINKING: {
        VoiceCallPhase.LISTENING,
        VoiceCallPhase.SPEAKING,
        VoiceCallPhase.INTERRUPTED,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.SPEAKING: {
        VoiceCallPhase.LISTENING,
        VoiceCallPhase.INTERRUPTED,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.INTERRUPTED: {
        VoiceCallPhase.LISTENING,
        VoiceCallPhase.THINKING,
        VoiceCallPhase.SPEAKING,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.RECONNECTING: {
        VoiceCallPhase.CONNECTING,
        VoiceCallPhase.LISTENING,
        VoiceCallPhase.ENDED,
        VoiceCallPhase.ERROR,
    },
    VoiceCallPhase.ERROR: {
        VoiceCallPhase.IDLE,
        VoiceCallPhase.CONNECTING,
        VoiceCallPhase.RECONNECTING,
        VoiceCallPhase.ENDED,
    },
    VoiceCallPhase.ENDED: set(),
}


class InvalidVoiceCallTransition(ValueError):
    pass


@dataclass
class VoiceCallStateMachine:
    emit: Callable[[dict[str, object]], object] | None = None
    call_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:16]}")
    phase: VoiceCallPhase = VoiceCallPhase.IDLE
    sequence: int = 0

    def transition(
        self,
        phase: VoiceCallPhase | str,
        message: str = "",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        target = VoiceCallPhase(str(phase))
        if target != self.phase and target not in _ALLOWED_TRANSITIONS[self.phase]:
            raise InvalidVoiceCallTransition(f"voice call cannot transition from {self.phase} to {target}")
        previous = self.phase
        self.phase = target
        self.sequence += 1
        event = {
            "type": "voice.call.state",
            "schema_version": "v1",
            "payload": {
                "call_id": self.call_id,
                "phase": target.value,
                "previous_phase": previous.value,
                "message": message,
                "sequence": self.sequence,
                "timestamp": time.time(),
                "metadata": dict(metadata or {}),
            },
        }
        if self.emit is not None:
            self.emit(event)
        return event

    def transcript(self, role: str, text: str, *, final: bool = True) -> dict[str, object] | None:
        clean = (text or "").strip()
        if not clean:
            return None
        event = {
            "type": "voice.call.transcript",
            "schema_version": "v1",
            "payload": {
                "call_id": self.call_id,
                "role": role,
                "text": clean,
                "final": bool(final),
                "timestamp": time.time(),
            },
        }
        if self.emit is not None:
            self.emit(event)
        return event


class MicrophoneAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamingMicrophoneConfig:
    model_size: str = "large-v3-turbo"
    sample_rate: int = 16000
    chunk_size: int = 1024
    silence_timeout: float = 0.75
    vad_threshold: float = 0.5


def listen_from_streaming_microphone_with_metrics(
    *,
    timeout: float = 10.0,
    phrase_time_limit: float = 8.0,
    device_index: int | None = None,
    config: StreamingMicrophoneConfig | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, object]:
    """Capture one utterance as PCM chunks and feed the existing StreamingTranscriber/VAD chain."""

    try:
        import speech_recognition as sr
    except ModuleNotFoundError as exc:
        raise MicrophoneAccessError("speech-recognition is required for voice calls") from exc

    cfg = config or StreamingMicrophoneConfig(
        model_size=(os.getenv("SPIRITKIN_ASR_MODEL") or "large-v3-turbo").strip(),
    )
    if device_index is None:
        resolved_index, device_info = resolve_microphone_device_index()
    else:
        resolved_index = int(device_index)
        device_info = next(
            (device for device in list_microphone_devices() if int(device.get("index", -1)) == resolved_index),
            {"index": resolved_index, "selection": "explicit_index"},
        )
    events: list[dict[str, Any]] = []

    def forward(event: dict[str, Any]) -> None:
        events.append(event)
        if event_sink is not None:
            event_sink(event)

    stream_session: StreamingSession = create_streaming_listener(
        model_size=cfg.model_size,
        silence_timeout=cfg.silence_timeout,
        vad_threshold=cfg.vad_threshold,
        event_sink=forward,
        require_hotword=False,
    )
    started = time.monotonic()
    speech_started = 0.0
    try:
        with sr.Microphone(
            device_index=resolved_index,
            sample_rate=cfg.sample_rate,
            chunk_size=cfg.chunk_size,
        ) as source:
            while True:
                now = time.monotonic()
                if not stream_session.is_speaking and now - started >= max(0.1, timeout):
                    return {
                        "text": None,
                        "elapsed": now - started,
                        "source": "streaming_microphone",
                        "device_index": resolved_index,
                        "device_name": str(device_info.get("name") or ""),
                    }
                if stream_session.is_speaking and speech_started <= 0:
                    speech_started = now
                if speech_started > 0 and now - speech_started >= max(0.25, phrase_time_limit):
                    final_text = stream_session.finalize_utterance(reason="phrase_time_limit")
                    return _streaming_result(final_text, started, resolved_index, device_info, events)

                audio_bytes = source.stream.read(source.CHUNK)
                stream_session.feed_audio(audio_bytes)
                if stream_session.final_text and not stream_session.is_speaking:
                    return _streaming_result(
                        stream_session.final_text,
                        started,
                        resolved_index,
                        device_info,
                        events,
                    )
    except (OSError, AttributeError) as exc:
        raise MicrophoneAccessError(f"microphone unavailable or permission denied: {exc}") from exc


def _streaming_result(
    text: str,
    started: float,
    device_index: int | None,
    device_info: dict[str, object],
    events: list[dict[str, Any]],
) -> dict[str, object]:
    return {
        "text": (text or "").strip() or None,
        "elapsed": time.monotonic() - started,
        "source": "streaming_microphone",
        "device_index": device_index,
        "device_name": str(device_info.get("name") or ""),
        "streaming_events": len(events),
    }
