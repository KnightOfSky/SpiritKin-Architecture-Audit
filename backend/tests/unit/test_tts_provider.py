from __future__ import annotations

import threading
import json
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.settings import TTSSettings, resolve_tts_settings
from backend.expression.edge_tts import EdgeTTSProvider, _hidden_subprocess_kwargs
from backend.expression.phoneme_bridge import PhonemeEventEmitter, text_to_phoneme_events
from backend.expression.speech import SpeechController, get_speech_controller
from backend.expression.voice_profiles import load_voice_profile, validate_voice_profile


class TTSTests(unittest.TestCase):
    def test_edge_tts_provider_init(self):
        provider = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
        self.assertEqual(provider.voice, "zh-CN-XiaoxiaoNeural")

    def test_edge_tts_provider_uses_resolved_settings_by_default(self):
        config = {"tts": {"voice": "Config Voice", "rate": "+8%"}}
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            provider = EdgeTTSProvider()

        self.assertEqual(provider.voice, "Config Voice")
        self.assertEqual(provider.rate, "+8%")

    def test_edge_tts_provider_is_not_available_when_edge_tts_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError), \
            patch.dict("sys.modules", {"edge_tts": None}):
            provider = EdgeTTSProvider()
            self.assertFalse(provider.is_available())

    def test_edge_tts_windows_processes_are_created_without_windows(self):
        class FakeStartupInfo:
            def __init__(self):
                self.dwFlags = 0
                self.wShowWindow = None

        with patch("backend.expression.edge_tts.os.name", "nt"), \
            patch("backend.expression.edge_tts.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
            patch("backend.expression.edge_tts.subprocess.STARTUPINFO", FakeStartupInfo, create=True), \
            patch("backend.expression.edge_tts.subprocess.STARTF_USESHOWWINDOW", 1, create=True), \
            patch("backend.expression.edge_tts.subprocess.SW_HIDE", 0, create=True):
            kwargs = _hidden_subprocess_kwargs()

        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertEqual(kwargs["startupinfo"].dwFlags & 1, 1)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)

    def test_phoneme_event_emitter_start_stop(self):
        events = []

        def capture(event):
            events.append(event)

        emitter = PhonemeEventEmitter(on_event=capture)
        emitter.start()
        emitter.emit("a", duration_ms=100)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phoneme, "a")
        emitter.stop()
        emitter.emit("b")
        self.assertEqual(len(events), 1)

    def test_text_to_phoneme_events_non_empty(self):
        events = text_to_phoneme_events("hello world")
        self.assertGreater(len(events), 0)
        for e in events:
            self.assertIn("mouth_shape", e)

    def test_text_to_phoneme_events_empty(self):
        self.assertEqual(text_to_phoneme_events(""), [])
        self.assertEqual(text_to_phoneme_events(None), [])

    def test_speech_controller_emits_start_phoneme_and_end_events(self):
        events = []
        spoken = []
        controller = SpeechController(speaker=spoken.append, event_sink=events.append, emit_phonemes=True)

        controller.speak("你好", block=True)

        event_types = [event["type"] for event in events]
        self.assertIn("speech.started", event_types)
        self.assertIn("speech.phoneme", event_types)
        self.assertIn("speech.ended", event_types)
        self.assertIn("model.interaction", event_types)
        self.assertEqual(spoken, ["你好"])
        self.assertEqual([e for e in events if e["type"] == "speech.phoneme"][0]["payload"]["source"], "speech_controller")
        self.assertEqual([e for e in events if e["type"] == "model.interaction"][0]["payload"]["protocol"], "spiritkin.model_interaction.v1")

    def test_speech_controller_stop_emits_interrupted_event(self):
        events = []
        entered = threading.Event()
        release = threading.Event()

        def speaker(_text):
            entered.set()
            release.wait(timeout=1.0)

        controller = SpeechController(speaker=speaker, event_sink=events.append)
        controller.speak("长句", block=False)
        self.assertTrue(entered.wait(timeout=1.0))

        self.assertTrue(controller.stop())
        release.set()
        time.sleep(0.05)

        event_types = [event["type"] for event in events]
        self.assertIn("speech.interrupted", event_types)
        self.assertIn("model.interaction", event_types)

    def test_get_speech_controller_uses_configured_edge_tts_when_available(self):
        settings = TTSSettings(provider="edge_tts", voice="Test Voice", rate="+3%")
        with patch("backend.expression.edge_tts.EdgeTTSProvider.is_available", return_value=True):
            controller = get_speech_controller(tts_settings=settings, event_sink=lambda _event: None)

        self.assertEqual(controller.backend_name, "edge_tts")

    def test_get_speech_controller_falls_back_to_pyttsx3_when_edge_unavailable(self):
        settings = TTSSettings(provider="edge_tts", voice="Test Voice", rate="+3%", fallback_provider="pyttsx3")
        with patch("backend.expression.edge_tts.EdgeTTSProvider.is_available", return_value=False):
            controller = get_speech_controller(tts_settings=settings, event_sink=lambda _event: None)

        self.assertEqual(controller.backend_name, "pyttsx3")

    def test_get_speech_controller_can_disable_edge_fallback(self):
        settings = TTSSettings(provider="edge_tts", voice="Test Voice", rate="+3%", fallback_provider="disabled")
        with patch("backend.expression.edge_tts.EdgeTTSProvider.is_available", return_value=False):
            controller = get_speech_controller(tts_settings=settings, event_sink=lambda _event: None)

        self.assertEqual(controller.backend_name, "disabled")

    def test_cosyvoice_settings_keep_explicit_edge_fallback(self):
        config = {
            "tts": {
                "provider": "cosyvoice",
                "voice_profile": "spiritkin.primary.v1",
                "voice_profile_path": "state/voice-profiles/spiritkin.primary.v1/profile.json",
                "base_url": "http://127.0.0.1:50000",
                "fallback_provider": "edge_tts",
            }
        }
        with patch("backend.app.settings._load_yaml_config", return_value=config):
            settings = resolve_tts_settings(environ={})

        self.assertEqual(settings.provider, "cosyvoice")
        self.assertEqual(settings.voice_profile_id, "spiritkin.primary.v1")
        self.assertEqual(settings.fallback_provider, "edge_tts")

    def test_cosyvoice_controller_uses_profile_when_local_provider_is_ready(self):
        settings = TTSSettings(
            provider="cosyvoice",
            voice_profile_path="state/voice-profiles/spiritkin.primary.v1/profile.json",
            base_url="http://127.0.0.1:50000",
            fallback_provider="edge_tts",
        )
        with patch("backend.expression.cosyvoice_tts.CosyVoiceProvider.__init__", return_value=None), \
            patch("backend.expression.cosyvoice_tts.CosyVoiceProvider.is_available", return_value=True):
            controller = get_speech_controller(tts_settings=settings, event_sink=lambda _event: None)

        self.assertEqual(controller.backend_name, "cosyvoice")

    def test_voice_profile_validates_reference_hash_and_local_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "reference.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 64)
            provenance = root / "provenance.json"
            provenance.write_text("{}", encoding="utf-8")
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "voice_id": "spiritkin.primary.v1",
                        "display_name": "Fairy Mechanical",
                        "speech_provider": "cosyvoice",
                        "speech_model": "Fun-CosyVoice3-0.5B",
                        "reference_audio": str(audio),
                        "reference_text": "胜败乃兵家常事。",
                        "language": "zh-CN",
                        "allowed_uses": ["assistant_speech_local"],
                        "reference_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                        "provenance_record": str(provenance),
                    }
                ),
                encoding="utf-8",
            )

            profile = load_voice_profile(profile_path)

            self.assertEqual(profile.voice_id, "spiritkin.primary.v1")
            self.assertEqual(validate_voice_profile(profile), (True, "ready"))
