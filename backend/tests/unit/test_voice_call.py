from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.runtime import SpiritKinRuntime
from backend.expression.performance import PerformanceController
from backend.perception.audio.realtime_session import RealtimeDuplexSession, RealtimeSessionConfig
from backend.perception.audio.voice_call import (
    InvalidVoiceCallTransition,
    MicrophoneAccessError,
    VoiceCallPhase,
    VoiceCallStateMachine,
    listen_from_streaming_microphone_with_metrics,
)


class _Agent:
    def process(self, user_input, visual_context="", channel=None, input_metadata=None):
        return AgentReply(text=f"回复：{user_input}", spoken_text=f"回复：{user_input}", emotion="happy")


class _Speech:
    def __init__(self):
        self.active = False
        self.stop_calls = 0

    def is_speaking(self):
        return self.active

    def stop(self):
        self.stop_calls += 1
        was_active = self.active
        self.active = False
        return was_active

    def speak(self, text, *, block=False):
        self.active = True


class VoiceCallTests(unittest.TestCase):
    def test_state_machine_accepts_canonical_call_sequence(self):
        events = []
        state = VoiceCallStateMachine(emit=events.append, call_id="call_test")

        for phase in ("connecting", "listening", "thinking", "speaking", "interrupted", "listening", "ended"):
            state.transition(phase)

        self.assertEqual(state.phase, VoiceCallPhase.ENDED)
        self.assertEqual([event["payload"]["phase"] for event in events], [
            "connecting", "listening", "thinking", "speaking", "interrupted", "listening", "ended",
        ])
        self.assertEqual([event["payload"]["sequence"] for event in events], list(range(1, 8)))

    def test_state_machine_rejects_invalid_transition(self):
        state = VoiceCallStateMachine(call_id="call_test")

        with self.assertRaises(InvalidVoiceCallTransition):
            state.transition("speaking")

    def test_transcript_contains_only_text_metadata_not_audio(self):
        events = []
        state = VoiceCallStateMachine(emit=events.append, call_id="call_test")

        state.transcript("user", "  你好  ")

        self.assertEqual(events[0]["payload"]["text"], "你好")
        self.assertEqual(events[0]["payload"]["call_id"], "call_test")
        self.assertNotIn("audio", events[0]["payload"])

    def test_duplex_session_emits_state_and_matching_subtitles(self):
        events = []
        state = VoiceCallStateMachine(emit=events.append, call_id="call_test")
        runtime = SpiritKinRuntime(agent=_Agent(), hotword="Spirit")
        speech = _Speech()
        queue = [{"text": "请帮我整理今天的任务", "source": "streaming_microphone"}]
        session = RealtimeDuplexSession(
            runtime,
            listener=lambda **_: queue.pop(0),
            speech_controller=speech,
            performance=PerformanceController(lambda _event: None),
            config=RealtimeSessionConfig(max_turns=1, require_hotword=False, suppress_playback_echo=False),
            call_state=state,
        )

        turns = session.run()

        self.assertEqual(turns[0].cleaned_text, "请帮我整理今天的任务")
        transcript_events = [event for event in events if event["type"] == "voice.call.transcript"]
        self.assertEqual([(event["payload"]["role"], event["payload"]["text"]) for event in transcript_events], [
            ("user", "请帮我整理今天的任务"),
            ("assistant", "回复：请帮我整理今天的任务"),
        ])
        self.assertEqual(state.phase, VoiceCallPhase.ENDED)

    def test_barge_in_moves_to_interrupted_and_stops_tts(self):
        events = []
        state = VoiceCallStateMachine(emit=events.append, call_id="call_test")
        state.transition("connecting")
        state.transition("listening")
        speech = _Speech()
        speech.active = True
        session = RealtimeDuplexSession(
            SpiritKinRuntime(agent=_Agent(), hotword="Spirit"),
            listener=lambda **_: {"text": ""},
            speech_controller=speech,
            performance=PerformanceController(lambda _event: None),
            config=RealtimeSessionConfig(require_hotword=False, suppress_playback_echo=False),
            call_state=state,
        )

        self.assertTrue(session.request_interrupt())

        self.assertEqual(state.phase, VoiceCallPhase.INTERRUPTED)
        self.assertEqual(speech.stop_calls, 1)

    def test_stop_cleans_tts_and_ends_call(self):
        state = VoiceCallStateMachine(call_id="call_test")
        state.transition("connecting")
        state.transition("listening")
        speech = _Speech()
        speech.active = True
        session = RealtimeDuplexSession(
            SpiritKinRuntime(agent=_Agent(), hotword="Spirit"),
            speech_controller=speech,
            performance=PerformanceController(lambda _event: None),
            call_state=state,
        )

        session.stop()

        self.assertFalse(speech.active)
        self.assertEqual(state.phase, VoiceCallPhase.ENDED)

    def test_streaming_microphone_returns_vad_final_text(self):
        class FakeStreamingSession:
            def __init__(self):
                self.is_speaking = False
                self.final_text = ""

            def feed_audio(self, audio_bytes):
                self.final_text = "流式文本"
                return self.final_text

            def finalize_utterance(self, *, reason="silence"):
                return self.final_text

        class FakeStream:
            def read(self, _chunk):
                return b"\x01\x00" * 16

        class FakeMicrophone:
            def __init__(self, **_kwargs):
                self.CHUNK = 1024
                self.stream = FakeStream()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        fake_sr = types.SimpleNamespace(Microphone=FakeMicrophone)
        with patch.dict(sys.modules, {"speech_recognition": fake_sr}), \
             patch("backend.perception.audio.voice_call.create_streaming_listener", return_value=FakeStreamingSession()), \
             patch("backend.perception.audio.voice_call.resolve_microphone_device_index", return_value=(2, {"name": "USB Mic"})):
            result = listen_from_streaming_microphone_with_metrics(timeout=1, phrase_time_limit=1)

        self.assertEqual(result["text"], "流式文本")
        self.assertEqual(result["device_index"], 2)
        self.assertEqual(result["device_name"], "USB Mic")

    def test_microphone_permission_failure_is_recoverable_error(self):
        class DeniedMicrophone:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                raise OSError("access denied")

            def __exit__(self, *_args):
                return False

        fake_sr = types.SimpleNamespace(Microphone=DeniedMicrophone)
        with patch.dict(sys.modules, {"speech_recognition": fake_sr}), \
             patch("backend.perception.audio.voice_call.create_streaming_listener"), \
             patch("backend.perception.audio.voice_call.resolve_microphone_device_index", return_value=(1, {"name": "Mic"})):
            with self.assertRaisesRegex(MicrophoneAccessError, "permission denied"):
                listen_from_streaming_microphone_with_metrics(timeout=1)


if __name__ == "__main__":
    unittest.main()
