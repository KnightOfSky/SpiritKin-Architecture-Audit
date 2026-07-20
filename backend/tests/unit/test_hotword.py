import unittest
from unittest.mock import patch

import backend.perception.audio.hotword as hotword_module
from backend.perception.audio.hotword import detect_hotword, get_wake_model


class _FakeAudio:
    def get_wav_data(self):
        return b"RIFFfake"


class _FakeSegment:
    def __init__(self, text):
        self.text = text


class HotwordTests(unittest.TestCase):
    def tearDown(self):
        hotword_module._wake_model = None

    def test_detect_hotword_matches_case_insensitive_transcript(self):
        fake_audio = _FakeAudio()

        class FakeModel:
            def transcribe(self, *args, **kwargs):
                return ([_FakeSegment(" spirit. ")], {"language": "en"})

        with patch("backend.perception.audio.hotword.get_wake_model", return_value=FakeModel()):
            self.assertTrue(detect_hotword(fake_audio, "Spirit"))

    def test_detect_hotword_prefers_english_transcription_for_english_hotword(self):
        fake_audio = _FakeAudio()
        captured = {}

        class FakeModel:
            def transcribe(self, *args, **kwargs):
                captured.update(kwargs)
                return ([_FakeSegment("spirit")], {})

        with patch("backend.perception.audio.hotword.get_wake_model", return_value=FakeModel()):
            self.assertTrue(detect_hotword(fake_audio, "Spirit"))

        self.assertEqual(captured.get("language"), "en")

    def test_get_wake_model_downloads_tiny_model_into_project_cache(self):
        created = []

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                created.append((args, kwargs))

        with patch("backend.perception.audio.hotword._wake_model", None), \
             patch("backend.perception.audio.hotword.os.makedirs") as makedirs, \
             patch("backend.perception.audio.hotword._has_cached_model", side_effect=lambda model_size: False), \
             patch("backend.perception.audio.hotword.show_emotion"), \
             patch("faster_whisper.WhisperModel", FakeWhisperModel):
            get_wake_model()

        makedirs.assert_called_once()
        self.assertEqual(created[0][0], ("tiny",))
        self.assertIn("download_root", created[0][1])

    def test_get_wake_model_prefers_local_cached_tiny_model(self):
        created = []

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                created.append((args, kwargs))

        with patch("backend.perception.audio.hotword._wake_model", None), \
             patch("backend.perception.audio.hotword.os.makedirs"), \
             patch("backend.perception.audio.hotword._has_cached_model", side_effect=lambda model_size: model_size == "tiny"), \
             patch("faster_whisper.WhisperModel", FakeWhisperModel):
            get_wake_model()

        self.assertEqual(created[0][0], ("tiny",))
        self.assertTrue(created[0][1].get("local_files_only"))

    def test_get_wake_model_falls_back_to_local_base_when_tiny_download_fails(self):
        created = []
        cached = {"base": False, "tiny": False}

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                if args == ("tiny",) and not kwargs.get("local_files_only"):
                    cached["base"] = True
                    raise RuntimeError("timeout")
                created.append((args, kwargs))

        with patch("backend.perception.audio.hotword._wake_model", None), \
             patch("backend.perception.audio.hotword.os.makedirs"), \
             patch(
                 "backend.perception.audio.hotword._has_cached_model",
                 side_effect=lambda model_size: cached[model_size],
             ), \
             patch("backend.perception.audio.hotword.show_emotion"), \
             patch("faster_whisper.WhisperModel", FakeWhisperModel):
            get_wake_model()

        self.assertEqual(created[0][0], ("base",))
        self.assertTrue(created[0][1].get("local_files_only"))

    def test_get_wake_model_uses_cached_base_without_attempting_tiny_download(self):
        created = []

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                created.append((args, kwargs))

        with patch("backend.perception.audio.hotword._wake_model", None), \
             patch("backend.perception.audio.hotword.os.makedirs"), \
             patch(
                 "backend.perception.audio.hotword._has_cached_model",
                 side_effect=lambda model_size: model_size == "base",
             ), \
             patch("backend.perception.audio.hotword.show_emotion"), \
             patch("faster_whisper.WhisperModel", FakeWhisperModel):
            get_wake_model()

        self.assertEqual(created, [(("base",), {
            "device": "cpu",
            "compute_type": "int8",
            "download_root": hotword_module.LOCAL_HOTWORD_MODEL_DIR,
            "local_files_only": True,
        })])


if __name__ == "__main__":
    unittest.main()