from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from backend.app.settings import TTSSettings, resolve_tts_settings
from backend.expression.model_interaction import build_speech_interaction
from backend.expression.phoneme_bridge import text_to_phoneme_events

_engine = None
_engine_config_key: tuple[str, int, float] | None = None
_engine_lock = threading.RLock()


def _get_pyttsx3():
    import pyttsx3

    return pyttsx3


def _matches_chinese_voice(voice) -> bool:
    languages = []
    for language in getattr(voice, "languages", []) or []:
        languages.append(language.decode(errors="ignore") if isinstance(language, bytes) else str(language))

    voice_name = str(getattr(voice, "name", ""))
    combined = " ".join(languages + [voice_name]).lower()
    return "zh" in combined or "chinese" in combined


def _initialize_engine(tts_settings: TTSSettings | None = None):
    settings = tts_settings or resolve_tts_settings()
    engine = _get_pyttsx3().init()
    engine.setProperty("rate", settings.pyttsx3_rate)
    engine.setProperty("volume", settings.volume)

    explicit_voice = settings.voice.strip()
    voices = engine.getProperty("voices") or []
    if explicit_voice:
        for voice in voices:
            name = str(getattr(voice, "name", ""))
            voice_id = str(getattr(voice, "id", ""))
            if explicit_voice.lower() in name.lower() or explicit_voice.lower() in voice_id.lower():
                engine.setProperty("voice", voice.id)
                print(f"[tts] Selected pyttsx3 voice: {voice.name}")
                return engine

    chinese_names = ("zh", "chinese", "chinese (simplified)", "中文", "xiaoxiao", "hanhan", "hui", "yaoyao", "kangkang")
    for voice in voices:
        vname = str(getattr(voice, 'name', '')).lower()
        vlang = str(getattr(voice, 'languages', '')).lower()
        if any(cn in vname or cn in vlang for cn in chinese_names):
            engine.setProperty("voice", voice.id)
            print(f"[tts] Auto-selected Chinese: {voice.name}")
            return engine

    if voices:
        engine.setProperty("voice", voices[0].id)
        print(f"[tts] Fallback: {voices[0].name} (no Chinese voice found)")

    return engine


def _ensure_engine(tts_settings: TTSSettings | None = None):
    global _engine, _engine_config_key
    settings = tts_settings or resolve_tts_settings()
    config_key = (settings.voice, settings.pyttsx3_rate, settings.volume)
    with _engine_lock:
        if _engine is None or _engine_config_key != config_key:
            try:
                if _engine is not None:
                    _engine.stop()
            except Exception:
                pass
            _engine = _initialize_engine(settings)
            _engine_config_key = config_key
        return _engine


def _speak_with_pyttsx3(text: str, tts_settings: TTSSettings | None = None) -> None:
    engine = _ensure_engine(tts_settings)
    with _engine_lock:
        engine.say(text)
    engine.runAndWait()


class SpeechController:
    """Interruptible speech facade used by duplex voice sessions.

    The default pyttsx3 backend is still best-effort on Windows, but this
    controller gives the rest of the system a stable async/stop/is_speaking
    contract so streaming TTS can be swapped in later without changing the
    realtime session state machine.
    """

    def __init__(
        self,
        speaker: Callable[[str], None] | None = None,
        *,
        event_sink: Callable[[dict], None] | None = None,
        emit_phonemes: bool = False,
        max_phoneme_events: int = 64,
        backend_name: str = "pyttsx3",
        defer_phonemes: bool = False,
    ):
        self._speaker = speaker or _speak_with_pyttsx3
        self._event_sink = event_sink
        self._emit_phonemes = emit_phonemes
        self._max_phoneme_events = max(0, int(max_phoneme_events))
        self.backend_name = backend_name
        # When the speaker reports real playback starts per segment, phoneme
        # timelines are emitted then instead of before synthesis begins.
        self._defer_phonemes = defer_phonemes
        self._worker: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._speaking = False
        self._current_speech_id = ""

    def is_speaking(self) -> bool:
        with self._state_lock:
            worker_alive = self._worker is not None and self._worker.is_alive()
            return self._speaking or worker_alive

    def stop(self) -> bool:
        was_speaking = self.is_speaking()
        self._stop_requested.set()
        speech_id = self._current_speech_id
        try:
            if _engine is not None:
                _engine.stop()
        except Exception:
            pass
        if was_speaking:
            self._emit("speech.interrupted", {"speech_id": speech_id, "reason": "stop_requested"})
        return was_speaking

    def speak(self, text: str, *, block: bool = False) -> threading.Thread | None:
        if not text or not text.strip():
            return None

        self.stop()
        self._stop_requested.clear()
        self._current_speech_id = f"speech-{uuid.uuid4().hex[:12]}"

        if block:
            self._run_speaker(text)
            return None

        worker = threading.Thread(target=self._run_speaker, args=(text,), daemon=True)
        with self._state_lock:
            self._worker = worker
        worker.start()
        return worker

    def _run_speaker(self, text: str) -> None:
        started_at = time.time()
        speech_id = self._current_speech_id or f"speech-{uuid.uuid4().hex[:12]}"
        with self._state_lock:
            self._speaking = True
        try:
            if not self._stop_requested.is_set():
                self._emit("speech.started", {"speech_id": speech_id, "text": text})
                if not self._defer_phonemes:
                    self._emit_phoneme_timeline(text, speech_id)
                self._speaker(text)
        except Exception as exc:
            print(f"⚠️ 语音播报失败: {exc}")
            self._emit("speech.error", {"speech_id": speech_id, "error": str(exc)})
        finally:
            interrupted = self._stop_requested.is_set()
            with self._state_lock:
                self._speaking = False
            self._emit(
                "speech.ended",
                {
                    "speech_id": speech_id,
                    "interrupted": interrupted,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                },
            )

    def emit_phoneme_timeline_now(self, text: str, duration_ms: int | None = None) -> None:
        """Emit the phoneme timeline for a segment that just started playing."""
        speech_id = self._current_speech_id
        if not speech_id or self._stop_requested.is_set():
            return
        self._emit_phoneme_timeline(text, speech_id, duration_ms=duration_ms)

    def _emit_phoneme_timeline(self, text: str, speech_id: str, duration_ms: int | None = None) -> None:
        if not self._emit_phonemes:
            return
        events = text_to_phoneme_events(text)[: self._max_phoneme_events]
        scale = 1.0
        if duration_ms and duration_ms > 0 and events:
            last = events[-1]
            estimated = int(last.get("timestamp_ms", 0)) + int(last.get("duration_ms", 150))
            if estimated > 0:
                scale = duration_ms / estimated
        for index, event in enumerate(events):
            payload = {
                "speech_id": speech_id,
                "sequence": index,
                "char": event.get("char", ""),
                "phoneme": event.get("phoneme", ""),
                "mouth_shape": event.get("mouth_shape", "mid"),
                "timestamp_ms": int(event.get("timestamp_ms", 0) * scale),
                "duration_ms": int(event.get("duration_ms", 150) * scale),
                "source": "speech_controller",
            }
            self._emit("speech.phoneme", payload)

    def _emit(self, event_type: str, payload: dict) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink({"type": event_type, "schema_version": "v1", "payload": payload})
            if event_type in {"speech.started", "speech.interrupted", "speech.ended", "speech.phoneme", "speech.viseme"}:
                self._event_sink(build_speech_interaction(event_type, payload))
        except Exception:
            pass


_default_controller: SpeechController | None = None
_default_controller_config_key: tuple[object, ...] | None = None


def _controller_config_key(settings: TTSSettings) -> tuple[object, ...]:
    return (
        settings.enabled,
        settings.provider,
        settings.voice,
        settings.rate,
        settings.pyttsx3_rate,
        settings.volume,
        settings.fallback_provider,
        settings.voice_profile_id,
        settings.voice_profile_path,
        settings.base_url,
        settings.timeout_seconds,
    )


def get_speech_controller(
    *,
    use_edge_tts: bool = True,
    tts_settings: TTSSettings | None = None,
    event_sink: Callable[[dict], None] | None = None,
    emit_phonemes: bool = False,
) -> SpeechController:
    global _default_controller, _default_controller_config_key
    settings = tts_settings or resolve_tts_settings()
    if not use_edge_tts and settings.provider == "edge_tts":
        settings = TTSSettings(
            provider="pyttsx3",
            voice=settings.voice,
            rate=settings.rate,
            pyttsx3_rate=settings.pyttsx3_rate,
            volume=settings.volume,
            fallback_provider=settings.fallback_provider,
            enabled=settings.enabled,
            voice_profile_id=settings.voice_profile_id,
            voice_profile_path=settings.voice_profile_path,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
        )
    if event_sink is not None:
        return _create_configured_controller(settings, event_sink=event_sink, emit_phonemes=emit_phonemes)
    config_key = _controller_config_key(settings)
    if _default_controller is None or _default_controller_config_key != config_key:
        _default_controller = _create_configured_controller(settings)
        _default_controller_config_key = config_key
    return _default_controller


def set_speech_controller(controller: SpeechController) -> None:
    global _default_controller, _default_controller_config_key
    _default_controller = controller
    _default_controller_config_key = None


SpeechController.create_with_edge_tts = staticmethod(lambda: _create_edge_tts_controller())


def _create_pyttsx3_controller(
    tts_settings: TTSSettings | None = None,
    *,
    event_sink: Callable[[dict], None] | None = None,
    emit_phonemes: bool = False,
) -> SpeechController:
    settings = tts_settings or resolve_tts_settings()

    def _pyttsx3_speaker(text: str) -> None:
        _speak_with_pyttsx3(text, settings)

    return SpeechController(
        speaker=_pyttsx3_speaker,
        event_sink=event_sink,
        emit_phonemes=emit_phonemes,
        backend_name="pyttsx3",
    )


def _create_configured_controller(
    tts_settings: TTSSettings | None = None,
    *,
    event_sink: Callable[[dict], None] | None = None,
    emit_phonemes: bool = False,
) -> SpeechController:
    settings = tts_settings or resolve_tts_settings()
    if not settings.enabled or settings.provider == "disabled":
        return SpeechController(
            speaker=lambda _text: None,
            event_sink=event_sink,
            emit_phonemes=emit_phonemes,
            backend_name="disabled",
        )
    if settings.provider == "edge_tts":
        return _create_edge_tts_controller(
            tts_settings=settings,
            event_sink=event_sink,
            emit_phonemes=emit_phonemes,
            fallback_to_pyttsx3=settings.fallback_provider == "pyttsx3",
        )
    if settings.provider == "cosyvoice":
        return _create_cosyvoice_controller(
            tts_settings=settings,
            event_sink=event_sink,
            emit_phonemes=emit_phonemes,
        )
    return _create_pyttsx3_controller(settings, event_sink=event_sink, emit_phonemes=emit_phonemes)


def _create_cosyvoice_controller(
    tts_settings: TTSSettings,
    *,
    event_sink: Callable[[dict], None] | None = None,
    emit_phonemes: bool = False,
) -> SpeechController:
    settings = tts_settings
    if settings.voice_profile_path:
        try:
            from backend.expression.cosyvoice_tts import CosyVoiceProvider

            provider = CosyVoiceProvider(
                base_url=settings.base_url,
                profile_path=settings.voice_profile_path,
                timeout_seconds=settings.timeout_seconds,
            )
            if provider.is_available():
                controller_holder: list[SpeechController] = []

                def _cosyvoice_speaker(text: str) -> None:
                    def _on_segment_start(segment: str, duration_s: float) -> None:
                        if controller_holder:
                            controller_holder[0].emit_phoneme_timeline_now(
                                segment,
                                duration_ms=int(duration_s * 1000) if duration_s > 0 else None,
                            )

                    provider.speak_and_play(text, on_segment_start=_on_segment_start)

                controller = SpeechController(
                    speaker=_cosyvoice_speaker,
                    event_sink=event_sink,
                    emit_phonemes=emit_phonemes,
                    backend_name="cosyvoice",
                    defer_phonemes=True,
                )
                controller_holder.append(controller)
                original_stop = controller.stop

                def _stop_cosyvoice() -> bool:
                    provider.stop()
                    return original_stop()

                controller.stop = _stop_cosyvoice
                return controller
        except Exception:
            pass

    if settings.fallback_provider == "edge_tts":
        return _create_edge_tts_controller(
            tts_settings=settings,
            event_sink=event_sink,
            emit_phonemes=emit_phonemes,
            fallback_to_pyttsx3=False,
        )
    if settings.fallback_provider == "pyttsx3":
        return _create_pyttsx3_controller(settings, event_sink=event_sink, emit_phonemes=emit_phonemes)
    return SpeechController(
        speaker=lambda _text: None,
        event_sink=event_sink,
        emit_phonemes=emit_phonemes,
        backend_name="disabled",
    )


def _create_edge_tts_controller(
    tts_settings: TTSSettings | None = None,
    *,
    event_sink: Callable[[dict], None] | None = None,
    emit_phonemes: bool = False,
    fallback_to_pyttsx3: bool = True,
) -> SpeechController:
    settings = tts_settings or resolve_tts_settings()
    try:
        from backend.expression.edge_tts import EdgeTTSProvider

        provider = EdgeTTSProvider(voice=settings.voice, rate=settings.rate)
        if provider.is_available():
            controller_holder: list[SpeechController] = []

            def _edge_speaker(text: str) -> None:
                def _on_segment_start(segment: str, duration_s: float) -> None:
                    if controller_holder:
                        controller_holder[0].emit_phoneme_timeline_now(
                            segment,
                            duration_ms=int(duration_s * 1000) if duration_s > 0 else None,
                        )

                provider.speak_and_play(text, on_segment_start=_on_segment_start)

            controller = SpeechController(
                speaker=_edge_speaker,
                event_sink=event_sink,
                emit_phonemes=emit_phonemes,
                backend_name="edge_tts",
                defer_phonemes=True,
            )
            controller_holder.append(controller)
            original_stop = controller.stop

            def _stop_edge_tts() -> bool:
                provider.stop()
                return original_stop()

            controller.stop = _stop_edge_tts
            return controller
    except Exception:
        pass
    if fallback_to_pyttsx3:
        return _create_pyttsx3_controller(settings, event_sink=event_sink, emit_phonemes=emit_phonemes)
    return SpeechController(
        speaker=lambda _text: None,
        event_sink=event_sink,
        emit_phonemes=emit_phonemes,
        backend_name="disabled",
    )


def speak(text: str):
    if not text or not text.strip():
        return
    get_speech_controller().speak(text, block=True)
