from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from backend.app.runtime import InteractionInput, SpiritKinRuntime
from backend.expression.performance import PerformanceController
from backend.expression.speech import SpeechController, get_speech_controller
from backend.perception.audio.listener import is_probable_asr_hallucination_text, listen_from_microphone_with_metrics

ListenerFn = Callable[..., dict[str, object]]

if TYPE_CHECKING:
    from backend.perception.audio.voice_call import VoiceCallStateMachine


def _safe_print(message: object = "") -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(message).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


@dataclass(frozen=True)
class RealtimeSessionConfig:
    """Fast path toward LPM-like duplex voice without locking to one ASR/TTS backend."""

    listen_timeout: float = 10.0
    phrase_time_limit: float = 8.0
    max_turns: int = 12
    idle_timeouts: int = 4
    speak_responses: bool = True
    emit_ack_events: bool = True
    require_hotword: bool = True
    wake_window_seconds: float = 60.0
    strict_hotword: bool = False
    suppress_playback_echo: bool = True
    playback_echo_grace_seconds: float = 0.45


@dataclass
class RealtimeTurn:
    index: int
    text: str
    cleaned_text: str
    reply: object | None = None
    metrics: dict[str, object] = field(default_factory=dict)
    interrupted_previous_speech: bool = False


class RealtimeDuplexSession:
    """Continuous voice session coordinating Listen/Speak/Silence/interrupt states.

    This first implementation is backend-agnostic: current SpeechRecognition +
    faster-whisper can drive it today, while streaming VAD/ASR can later replace
    only the listener callable.
    """

    def __init__(
        self,
        runtime: SpiritKinRuntime,
        *,
        listener: ListenerFn = listen_from_microphone_with_metrics,
        speech_controller: SpeechController | None = None,
        performance: PerformanceController | None = None,
        config: RealtimeSessionConfig | None = None,
        call_state: VoiceCallStateMachine | None = None,
    ):
        self.runtime = runtime
        self.listener = listener
        self.speech = speech_controller or get_speech_controller(event_sink=runtime._emit_runtime_event, emit_phonemes=True)
        self.performance = performance or PerformanceController(runtime._emit_runtime_event)
        self.config = config or RealtimeSessionConfig()
        self.call_state = call_state
        if not self.config.speak_responses:
            self.config = replace(self.config, suppress_playback_echo=False)
        self.turns: list[RealtimeTurn] = []
        self._pending_interrupt_texts: list[str] = []
        self._stopped = False
        self._wake_active_until = 0.0
        self._last_speech_active_at = 0.0

    def stop(self, *, emit_ended: bool = True) -> None:
        self._stopped = True
        if self.speech.stop():
            self.performance.emit("interrupted", "会话已停止，语音播报已打断。")
        if emit_ended:
            self._set_call_state("ended", "通话已结束。")

    def request_interrupt(self, text: str | None = None) -> bool:
        was_speaking = self.speech.stop()
        if text and text.strip():
            self._activate_wake_window()
            self._pending_interrupt_texts.append(text.strip())
        self.performance.emit(
            "interrupted",
            "我停下来了，你继续说。",
            {"queued_text": text or "", "was_speaking": was_speaking},
        )
        self._set_call_state("interrupted", "已停止播报，继续听你说。", {"was_speaking": was_speaking})
        return was_speaking

    def listen_once(self) -> dict[str, object]:
        if self._pending_interrupt_texts:
            text = self._pending_interrupt_texts.pop(0)
            return {"text": text, "elapsed": 0.0, "source": "queued_interrupt"}

        self._set_call_state("listening", "正在聆听。")

        if self._should_suppress_playback_echo():
            time.sleep(min(0.2, max(0.0, float(self.config.playback_echo_grace_seconds or 0.0))))
            self.performance.emit(
                "attentive_wait",
                "助手正在播报，暂时抑制麦克风输入，避免把扬声器声音识别成指令。",
                {"reason": "playback_echo_suppressed", "duplex": True, "speaking": self.speech.is_speaking()},
            )
            return {"text": None, "elapsed": 0.0, "error": "playback_echo_suppressed", "suppressed_playback": True}

        self.performance.emit(
            "listening",
            "我在听。" if not self.speech.is_speaking() else "我还在说，你可以直接插话。",
            {"duplex": True, "speaking": self.speech.is_speaking()},
        )
        return self.listener(timeout=self.config.listen_timeout, phrase_time_limit=self.config.phrase_time_limit)

    def _should_suppress_playback_echo(self) -> bool:
        if not self.config.suppress_playback_echo:
            return False
        now = time.monotonic()
        if self.speech.is_speaking():
            self._last_speech_active_at = now
            return True
        grace = max(0.0, float(self.config.playback_echo_grace_seconds or 0.0))
        return bool(self._last_speech_active_at and now - self._last_speech_active_at < grace)

    def process_text(self, text: str, *, metrics: dict[str, object] | None = None, visual_context: str = "") -> RealtimeTurn | None:
        raw_text = (text or "").strip()
        if not raw_text:
            return None

        interrupted = False
        if self.speech.is_speaking():
            interrupted = self.speech.stop()
            self.performance.emit("interrupted", "收到你的插话，我先停下。", {"barge_in_text": raw_text})

        cleaned_text, voice_metadata = self.runtime._prepare_voice_text_and_metadata(
            raw_text,
            metadata={"duplex_turn": len(self.turns) + 1, "raw_voice_text": raw_text, "lpm_like": True},
        )
        if self.call_state is not None:
            voice_metadata["voice_call_id"] = self.call_state.call_id
        if metrics:
            voice_metadata["asr_metrics"] = dict(metrics)
        if self.runtime._should_ignore_followup_input(cleaned_text):
            self.performance.emit("attentive_wait", "我在，等你继续说。", {"ignored_text": raw_text})
            return None

        turn = RealtimeTurn(
            index=len(self.turns) + 1,
            text=raw_text,
            cleaned_text=cleaned_text,
            metrics=dict(metrics or {}),
            interrupted_previous_speech=interrupted,
        )
        self._emit_call_transcript("user", cleaned_text)
        self._set_call_state("thinking", "正在理解你的话。", {"turn": turn.index})
        self.performance.emit("thinking", "我听到了，正在理解。", {"turn": turn.index, "text": cleaned_text})
        reply = self.runtime.handle_input(
            InteractionInput(
                text=cleaned_text,
                channel="voice",
                visual_context=visual_context,
                metadata=voice_metadata,
            )
        )
        turn.reply = reply
        self.turns.append(turn)
        self._after_reply(turn)
        return turn

    def _has_active_wake_window(self) -> bool:
        return (not self.config.require_hotword) or (not self.config.strict_hotword and time.monotonic() < self._wake_active_until)

    def _activate_wake_window(self) -> None:
        if self.config.strict_hotword:
            self._wake_active_until = 0.0
            return
        self._wake_active_until = time.monotonic() + max(5.0, float(self.config.wake_window_seconds or 60.0))
        _safe_print(f"[voice] Wake window active, you can speak naturally for {int(self.config.wake_window_seconds)}s")

    def _text_starts_with_hotword(self, text: str) -> bool:
        hotword = (self.runtime.hotword or "").strip()
        if not hotword:
            return True
        compact_text = self.runtime._normalize_voice_text(text)
        compact_hotword = self.runtime._normalize_voice_text(hotword)
        return bool(compact_hotword and compact_text.startswith(compact_hotword))

    def _accepts_text_under_wake_policy(self, text: str) -> bool:
        if not self.config.require_hotword:
            return True
        if self._text_starts_with_hotword(text):
            self._activate_wake_window()
            stripped = self.runtime._strip_hotword_prefix(text)
            if self.runtime._should_ignore_followup_input(stripped):
                self.performance.emit(
                    "attentive_wait",
                    f"我在。请说 {self.runtime.hotword} 加指令。",
                    {"wake_window_seconds": 0 if self.config.strict_hotword else self.config.wake_window_seconds},
                )
                return False
            return True
        if self._has_active_wake_window():
            self._activate_wake_window()
            return True
        msg = f"请先说 {self.runtime.hotword} 唤醒我。"
        _safe_print(f"\n[voice] {msg}\n")
        self.performance.emit("silence_idle", msg, {"reason": "wake_required", "wake_window_seconds": self.config.wake_window_seconds})
        return False

    def _after_reply(self, turn: RealtimeTurn) -> None:
        reply = turn.reply
        if reply is None:
            self._set_call_state("listening", "这句没有听清，等待重试。", {"turn": turn.index})
            self.performance.emit("attentive_wait", "这句我没听清，再说一遍。", {"turn": turn.index})
            return

        metadata = getattr(reply, "metadata", {}) or {}
        if getattr(reply, "requires_confirmation", False):
            self.performance.emit("waiting_confirmation", "这个操作需要你确认。", {"turn": turn.index})
        elif metadata.get("execution"):
            self.performance.emit("acting", "我正在执行。", {"turn": turn.index, "execution": metadata.get("execution")})

        spoken_text = self.runtime._build_speech_output(reply)
        reply_preview = getattr(reply, 'text', '')[:120]
        emotion_str = getattr(reply, 'emotion', '?')
        agent = getattr(reply, 'agent_name', '?')
        _safe_print(f"[agent] {agent} ({emotion_str}): {reply_preview}")
        if self.config.speak_responses and spoken_text:
            self._emit_call_transcript("assistant", spoken_text)
            self._set_call_state("speaking", "正在回应。", {"turn": turn.index, "agent": agent})
            self.performance.emit("speaking", spoken_text, {"turn": turn.index, "agent": agent})
            self.speech.speak(spoken_text, block=False)
            self.runtime._record_spoken_output(spoken_text)
            # Brief pause to let audio playback start before next listen cycle
            time.sleep(0.3)
        else:
            self._emit_call_transcript("assistant", spoken_text or getattr(reply, "text", ""))
            self._set_call_state("listening", "等待你继续说。", {"turn": turn.index})
            self.performance.emit("silence_idle", "", {"turn": turn.index})

    def _set_call_state(self, phase: str, message: str = "", metadata: dict[str, object] | None = None) -> None:
        if self.call_state is not None:
            self.call_state.transition(phase, message, metadata)

    def _emit_call_transcript(self, role: str, text: str) -> None:
        if self.call_state is not None:
            self.call_state.transcript(role, text)

    def run(self, *, max_turns: int | None = None, visual_context: str = "") -> list[RealtimeTurn]:
        idle_count = 0
        turn_limit = self.config.max_turns if max_turns is None else max_turns
        has_turn_limit = turn_limit > 0
        has_idle_limit = self.config.idle_timeouts > 0
        started = time.perf_counter()
        if self.call_state is not None and self.call_state.phase.value == "idle":
            self._set_call_state("connecting", "正在连接语音服务。")
        self.performance.emit(
            "silence_idle",
            "实时双工会话已启动。",
            {
                "max_turns": turn_limit if has_turn_limit else "unlimited",
                "idle_timeouts": self.config.idle_timeouts if has_idle_limit else "disabled",
            },
        )
        while not self._stopped and (not has_turn_limit or len(self.turns) < turn_limit):
            metrics = self.listen_once()
            text = str(metrics.get("text") or "").strip()
            if metrics.get("suppressed_playback"):
                continue
            if not text:
                idle_count += 1
                err_detail = metrics.get("error", "") or ""
                noise_error = err_detail in {"low_rms_noise", "short_non_command_noise"}
                if noise_error:
                    if idle_count == 1 or idle_count % 10 == 0:
                        _safe_print(f"[asr] noise ignored: {err_detail}")
                elif err_detail:
                    _safe_print(f"[asr] transcription error: {err_detail}")
                elif metrics.get("rejected_segments", 0) > 0:
                    _safe_print(f"[asr] heard audio but all {metrics.get('rejected_segments')} segments rejected (low conf/noise filter)")
                elif idle_count == 1:
                    segs = metrics.get("segments", [])
                    if segs:
                        _safe_print(f"[asr] {len(segs)} segments found but all filtered - may be hallucination guard too strict")
                if self.config.require_hotword and self._wake_active_until and time.monotonic() >= self._wake_active_until:
                    self._wake_active_until = 0.0
                    self.performance.emit(
                        "silence_idle",
                        f"超过 {int(self.config.wake_window_seconds)} 秒没有有效输入，请重新说 {self.runtime.hotword} 唤醒我。",
                        {"reason": "wake_window_expired", "idle_count": idle_count},
                    )
                    continue
                if noise_error:
                    continue
                should_exit_on_idle = has_idle_limit and idle_count >= self.config.idle_timeouts
                self.performance.emit(
                    "attentive_wait" if not should_exit_on_idle else "silence_idle",
                    "我还在听，没听到有效语音。" if not should_exit_on_idle else "连续未听到有效语音，会话结束。",
                    {"idle_count": idle_count, "error": metrics.get("error"), "idle_limit": self.config.idle_timeouts if has_idle_limit else "disabled"},
                )
                if should_exit_on_idle:
                    break
                continue
            if is_probable_asr_hallucination_text(text):
                idle_count += 1
                self.performance.emit(
                    "attentive_wait",
                    "忽略了一段疑似 ASR 静音幻听，我还在听。",
                    {"ignored_text": text, "reason": "asr_hallucination", "segments": metrics.get("segments", [])},
                )
                continue
            cleaned_echo_candidate, _ = self.runtime._prepare_voice_text_and_metadata(text)
            if self.runtime._is_probable_playback_echo(cleaned_echo_candidate, metrics):
                idle_count += 1
                self.performance.emit(
                    "attentive_wait",
                    "忽略了一段疑似耳机/扬声器回声，避免把助手播报当成指令。",
                    {"ignored_text": text, "reason": "playback_echo_text_suppressed"},
                )
                continue
            idle_count = 0
            if metrics.get("source") != "queued_interrupt" and not self._accepts_text_under_wake_policy(text):
                continue
            _safe_print(f"[voice] >>> \"{text}\"")
            self.process_text(text, metrics=metrics, visual_context=visual_context)

        self.performance.emit("silence_idle", "实时双工会话已结束。", {"elapsed": time.perf_counter() - started})
        self._set_call_state("ended", "通话已结束。", {"elapsed": time.perf_counter() - started})
        return list(self.turns)


def start_realtime_session(
    hotword: str | None = None,
    max_turns: int = 0,
    idle_timeouts: int = 0,
    speak_responses: bool = True,
    require_hotword: bool = True,
    strict_hotword: bool = False,
    asr_language: str = "",
    tts_voice: str = "",
    call_mode: bool = False,
    call_id: str = "",
    device_index: int | None = None,
) -> bool:
    import os
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _configure_utf8_stdio()
    os.environ.setdefault("SPIRIT_ASR_BEAM_SIZE", "1")
    os.environ.setdefault("SPIRIT_ASR_VAD_FILTER", "1")
    os.environ.setdefault("SPIRIT_ASR_TEMPERATURE", "0")
    os.environ.setdefault("SPIRITKIN_WAKE_ACK_ENABLED", "0")
    os.environ.setdefault("SPIRITKIN_VOICE_ACK_ENABLED", "0")
    if asr_language.strip():
        os.environ["SPIRITKIN_ASR_LANGUAGE"] = asr_language.strip()
    if tts_voice.strip():
        os.environ["SPIRITKIN_TTS_VOICE"] = tts_voice.strip()
    if device_index is not None:
        os.environ["SPIRITKIN_MIC_INDEX"] = str(int(device_index))

    runtime = SpiritKinRuntime(emit_runtime_events=True)
    call_state = None
    if call_mode:
        from backend.perception.audio.voice_call import VoiceCallStateMachine

        call_state = VoiceCallStateMachine(
            emit=runtime._emit_runtime_event,
            call_id=call_id or f"call_{int(time.time() * 1000)}",
        )
        call_state.transition("connecting", "正在准备语音通话。")
    if hotword:
        runtime.hotword = hotword.strip()

    # Preload ASR model BEFORE the listen loop
    from backend.perception.audio.listener import get_whisper_model, resolve_microphone_device_index
    _safe_print("[voice] Preloading ASR model...")
    try:
        get_whisper_model(allow_fallback=True)
    except Exception as exc:
        if call_state is not None:
            call_state.transition("error", "语音识别模型不可用。", {"recoverable": True, "error": str(exc)})
        _safe_print(f"[voice] ASR model unavailable: {exc}")
        return False
    _safe_print("[voice] ASR model ready")

    # Detect mic
    mic_idx, mic_info = resolve_microphone_device_index()
    _safe_print(f"[voice] Mic: [{mic_idx}] {mic_info.get('name', '?')} (set SPIRITKIN_MIC_INDEX to override)")

    config = RealtimeSessionConfig(
        listen_timeout=10.0,
        phrase_time_limit=8.0,
        max_turns=max(0, int(max_turns)),
        idle_timeouts=max(0, int(idle_timeouts)),
        speak_responses=speak_responses,
        require_hotword=require_hotword,
        wake_window_seconds=60.0,
        strict_hotword=strict_hotword,
        suppress_playback_echo=True,
    )

    speech_controller: SpeechController | None = None
    tts_provider = "disabled"
    if speak_responses:
        try:
            from backend.app.settings import resolve_tts_settings

            tts_settings = resolve_tts_settings()
            speech_controller = get_speech_controller(
                tts_settings=tts_settings,
                event_sink=runtime._emit_runtime_event,
                emit_phonemes=True,
            )
            tts_provider = (
                f"{speech_controller.backend_name} "
                f"(configured={tts_settings.provider}, voice={tts_settings.voice}, rate={tts_settings.rate})"
            )
        except Exception as _e:
            _safe_print(f"[voice] TTS setup failed ({_e}); speech output disabled")
            speech_controller = SpeechController(speaker=lambda _text: None, backend_name="disabled")
            tts_provider = f"disabled (setup failed: {_e})"
    else:
        speech_controller = SpeechController(speaker=lambda _text: None, backend_name="disabled")

    session_holder: dict[str, RealtimeDuplexSession] = {}
    listener = listen_from_microphone_with_metrics
    if call_mode:
        from backend.perception.audio.voice_call import listen_from_streaming_microphone_with_metrics

        def emit_stream_event(event: dict[str, Any]) -> None:
            payload = dict(event.get("payload") or {})
            payload["call_id"] = call_state.call_id if call_state is not None else ""
            runtime._emit_runtime_event({**event, "payload": payload})
            active = session_holder.get("session")
            if event.get("type") == "asr.speech_started" and active is not None and active.speech.is_speaking():
                active.request_interrupt()

        def streaming_listener(**kwargs: object) -> dict[str, object]:
            return listen_from_streaming_microphone_with_metrics(
                timeout=float(kwargs.get("timeout") or config.listen_timeout),
                phrase_time_limit=float(kwargs.get("phrase_time_limit") or config.phrase_time_limit),
                device_index=device_index,
                event_sink=emit_stream_event,
            )

        listener = streaming_listener
        config = replace(config, suppress_playback_echo=False)

    session = RealtimeDuplexSession(
        runtime,
        listener=listener,
        speech_controller=speech_controller,
        config=config,
        call_state=call_state,
    )
    session_holder["session"] = session
    _safe_print(f"[voice] Hotword: {runtime.hotword}")
    _safe_print(f"[voice] Hotword required: {require_hotword}")
    _safe_print(f"[voice] Strict hotword: {strict_hotword}")
    _safe_print(f"[voice] Speak responses: {speak_responses}")
    _safe_print(f"[voice] ASR language: {os.getenv('SPIRITKIN_ASR_LANGUAGE', 'auto')}")
    _safe_print(
        "[voice] ASR gate: "
        f"min_rms={os.getenv('SPIRITKIN_ASR_MIN_RMS', '650')}, "
        f"no_speech={os.getenv('SPIRITKIN_ASR_NO_SPEECH_THRESHOLD', '0.85')}, "
        f"low_logprob={os.getenv('SPIRITKIN_ASR_LOW_LOGPROB_THRESHOLD', '-1.4')}"
    )
    _safe_print(f"[voice] Max turns: {'unlimited' if config.max_turns <= 0 else config.max_turns}")
    _safe_print(f"[voice] Idle timeout limit: {'disabled' if config.idle_timeouts <= 0 else config.idle_timeouts}")
    _safe_print("[voice] ASR model: large-v3-turbo (cached)")
    _safe_print(f"[voice] TTS: {tts_provider}")
    if require_hotword:
        _safe_print(f"[voice] Start speaking... say '{runtime.hotword}' to wake")
    else:
        _safe_print("[voice] Start speaking... hotword disabled")
    _safe_print()

    succeeded = False
    failed = False
    try:
        session.run(visual_context="用户正看着 SpiritKin 面板")
        succeeded = True
    except KeyboardInterrupt:
        _safe_print("\n[voice] Session stopped by user")
    except Exception as exc:
        failed = True
        if call_state is not None and call_state.phase.value != "error":
            call_state.transition("error", "语音通话暂时不可用。", {"recoverable": True, "error": str(exc)})
        _safe_print(f"[voice] Error: {exc}")
    finally:
        if failed:
            session.stop(emit_ended=False)
        else:
            session.stop()
    return succeeded


def diagnose_voice_issues():
    _safe_print("=== SpiritKin Voice Self-Diagnosis ===\n")
    issues = []
    ok = []

    try:
        from backend.perception.audio.listener import (
            get_whisper_model,
            list_microphone_devices,
            resolve_microphone_device_index,
        )
        devices = list_microphone_devices()
        real_mics = [d for d in devices if "output" not in str(d.get("name","")).lower() and "mapper" not in str(d.get("name","")).lower() and "spdif" not in str(d.get("name","")).lower()]
        if real_mics:
            ok.append(f"Mic devices found: {len(real_mics)} input devices")
        else:
            issues.append("No microphone input devices detected")
    except Exception as e:
        issues.append(f"Mic detection failed: {e}")

    try:
        import edge_tts  # noqa: F401 — availability probe
        ok.append("edge-tts Python API: available (zh-CN voices ready)")
    except ImportError:
        issues.append("edge-tts not installed — run: pip install edge-tts")

    try:
        import pyttsx3
        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        zh = [v for v in voices if any(cn in str(getattr(v,'name','')).lower() for cn in ("zh","chinese","中文","xiaoxiao","hui","hanhan"))]
        if zh:
            ok.append(f"pyttsx3 Chinese voices: {len(zh)} ({zh[0].name})")
        else:
            issues.append("pyttsx3: no Chinese TTS voice installed — use edge-tts or install Chinese language pack")
    except Exception as e:
        issues.append(f"pyttsx3 init failed: {e}")

    try:
        model = get_whisper_model(allow_fallback=True)
        ok.append(f"ASR model: {type(model).__name__} (cached)")
    except Exception as e:
        issues.append(f"ASR model not available: {e}")

    try:
        from backend.perception.audio.listener import resolve_microphone_device_index
        idx, info = resolve_microphone_device_index()
        ok.append(f"Mic auto-select: [{idx}] {info.get('name','?')}")
    except Exception as e:
        issues.append(f"Mic calibration failed: {e}")

    _safe_print("PASS:")
    for o in ok:
        _safe_print(f"  [OK] {o}")
    _safe_print()
    if issues:
        _safe_print("ISSUES:")
        for i in issues:
            _safe_print(f"  [!!] {i}")
    else:
        _safe_print("  No issues found - ready for voice session")
    _safe_print()
    _safe_print("Recommended command:")
    if issues:
        _safe_print("  Fix the issues above first, then run:")
    _safe_print("  python -m backend.perception.audio.realtime_session")
    return len(issues) == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SpiritKin Real-time Duplex Voice Session")
    parser.add_argument("--hotword", default="", help="Wake word (default: Spirit)")
    parser.add_argument("--max-turns", type=int, default=0, help="Max turns (0=unlimited)")
    parser.add_argument("--idle-timeouts", type=int, default=0, help="Consecutive empty listens before exit (0=never exit on idle)")
    parser.add_argument("--no-speak", action="store_true", help="Disable TTS speech output")
    parser.add_argument("--no-hotword", action="store_true", help="Disable hotword requirement")
    parser.add_argument("--strict-hotword", action="store_true", help="Require the hotword on every microphone command")
    parser.add_argument("--wake-window", action="store_true", help="Default mode: after hotword, allow follow-up commands without repeating it")
    parser.add_argument("--asr-language", default="", help="ASR language: zh, yue/zh-HK, or auto")
    parser.add_argument("--tts-voice", default="", help="Edge TTS voice, e.g. zh-HK-HiuMaanNeural")
    parser.add_argument("--call-mode", action="store_true", help="Enable desktop voice-call state and transcript events")
    parser.add_argument("--call-id", default="", help="Stable desktop call identifier")
    parser.add_argument("--device-index", type=int, default=None, help="Microphone input device index")
    parser.add_argument("--diagnose", action="store_true", help="Run self-diagnosis and exit")
    args = parser.parse_args()

    if args.diagnose:
        diagnose_voice_issues()
    else:
        ok = start_realtime_session(
            hotword=args.hotword or "",
            max_turns=args.max_turns,
            idle_timeouts=args.idle_timeouts,
            speak_responses=not args.no_speak,
            require_hotword=not args.no_hotword,
            strict_hotword=args.strict_hotword,
            asr_language=args.asr_language,
            tts_voice=args.tts_voice,
            call_mode=args.call_mode,
            call_id=args.call_id,
            device_index=args.device_index,
        )
        raise SystemExit(0 if ok else 2)
