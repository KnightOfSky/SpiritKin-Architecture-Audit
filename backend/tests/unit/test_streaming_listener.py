from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.perception.audio.streaming_listener import (
    SileroVADWrapper,
    StreamingSession,
    StreamingTranscriber,
    StreamingWakewordGate,
    WhisperChunkTranscriber,
    create_streaming_listener,
)


class FakeStreamingTranscriber(StreamingTranscriber):
    def __init__(self, final_text="Spirit 打开浏览器"):
        self.final_text = final_text

    def process_chunk(self, audio_bytes: bytes) -> str:
        return "partial text"

    def finalize(self) -> str:
        return self.final_text

    def reset(self) -> None:
        pass


class FakeWhisperSegment:
    text = "hello world"
    start = 0.0
    end = 1.0
    avg_logprob = -0.1
    no_speech_prob = 0.01


class FakeWhisperModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter([FakeWhisperSegment()]), object()


class StreamingListenerTests(unittest.TestCase):
    def test_whisper_transcriber_process_chunk(self):
        model = FakeWhisperModel()
        t = WhisperChunkTranscriber(
            model_size="tiny",
            model=model,
            sample_rate=16,
            partial_interval_seconds=0.25,
            minimum_decode_seconds=0.25,
        )

        partial = t.process_chunk((1000).to_bytes(2, "little", signed=True) * 8)

        self.assertEqual(partial, "hello world")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][1]["beam_size"], 1)
        self.assertFalse(model.calls[0][1]["vad_filter"])

    def test_whisper_transcriber_finalize(self):
        model = FakeWhisperModel()
        t = WhisperChunkTranscriber(model=model, sample_rate=16, minimum_decode_seconds=1.0)
        t.process_chunk((1000).to_bytes(2, "little", signed=True) * 4)

        self.assertEqual(t.finalize(), "hello world")

    def test_whisper_transcriber_reports_actionable_dependency_error(self):
        t = WhisperChunkTranscriber(sample_rate=16, partial_interval_seconds=0.25, minimum_decode_seconds=0.25)

        with patch("backend.perception.audio.listener.get_whisper_model", side_effect=ModuleNotFoundError("faster_whisper")):
            with self.assertRaisesRegex(RuntimeError, "install requirements.txt"):
                t.process_chunk((1000).to_bytes(2, "little", signed=True) * 8)

    def test_whisper_transcriber_reset(self):
        model = FakeWhisperModel()
        t = WhisperChunkTranscriber(model=model, sample_rate=16, partial_interval_seconds=0.25, minimum_decode_seconds=0.25)
        t.process_chunk((1000).to_bytes(2, "little", signed=True) * 8)
        t.reset()

        self.assertEqual(t.finalize(), "")

    def test_silero_vad_is_speech_for_large_chunk(self):
        vad = SileroVADWrapper(threshold=0.5)

        self.assertFalse(vad.is_speech(b"\x00\x00" * 160))
        self.assertTrue(vad.is_speech((4000).to_bytes(2, "little", signed=True) * 160))

    def test_create_streaming_listener_returns_session(self):
        session = create_streaming_listener(model_size="tiny", silence_timeout=1.0)
        self.assertIsInstance(session, StreamingSession)

    def test_streaming_session_feed_audio_detects_speech(self):
        t = WhisperChunkTranscriber(
            model=FakeWhisperModel(),
            sample_rate=16,
            partial_interval_seconds=0.25,
            minimum_decode_seconds=0.25,
        )
        vad = SileroVADWrapper(threshold=0.5)
        session = StreamingSession(transcriber=t, vad=vad, silence_timeout_seconds=1.5)

        self.assertEqual(session.feed_audio((4000).to_bytes(2, "little", signed=True) * 8), "hello world")
        self.assertTrue(session.is_speaking)

    def test_streaming_session_emits_partial_and_final_events(self):
        events = []
        session = StreamingSession(transcriber=FakeStreamingTranscriber("hello"), vad=None, silence_timeout_seconds=0.0, event_sink=events.append)

        self.assertEqual(session.feed_audio(b"voice"), "partial text")
        session.feed_audio(b"")
        final = session.feed_audio(b"")

        self.assertEqual(final, "hello")
        self.assertIn("asr.speech_started", [event["type"] for event in events])
        self.assertIn("asr.partial", [event["type"] for event in events])
        self.assertIn("asr.final", [event["type"] for event in events])

    def test_streaming_wakeword_gate_accepts_and_strips_hotword(self):
        gate = StreamingWakewordGate(hotword="Spirit", require_hotword=True)

        blocked = gate.process_transcript("打开浏览器")
        accepted = gate.process_transcript("Spirit 打开浏览器")

        self.assertFalse(blocked["accepted"])
        self.assertTrue(accepted["accepted"])
        self.assertTrue(accepted["activated"])
        self.assertEqual(accepted["cleaned_text"], "打开浏览器")

    def test_create_streaming_listener_can_enable_wakeword_gate(self):
        session = create_streaming_listener(require_hotword=True, hotword="Spirit")

        self.assertIsNotNone(session.wakeword_gate)
