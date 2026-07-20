import os
import sys
import types
import unittest
from unittest.mock import patch

from backend.perception.audio import listener


class AudioListenerModelSelectionTests(unittest.TestCase):
    def tearDown(self):
        listener._model = None
        listener._model_size = None

    def test_requested_model_without_cache_is_unavailable_when_download_and_fallback_disabled(self):
        env = {
            "SPIRIT_ASR_MODEL_SIZE": "large-v3-turbo",
            "SPIRIT_ALLOW_MODEL_DOWNLOAD": "0",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(listener, "_has_cached_asr_model", return_value=False):
            selection = listener.resolve_asr_model_selection(allow_fallback=False)

        self.assertFalse(selection["available"])
        self.assertEqual(selection["requested"], "large-v3-turbo")
        self.assertFalse(selection["fallback"])

    def test_requested_model_can_explicitly_fallback_to_cached_base(self):
        def cached(model_size: str) -> bool:
            return model_size == "base"

        env = {
            "SPIRIT_ASR_MODEL_SIZE": "large-v3-turbo",
            "SPIRIT_ALLOW_MODEL_DOWNLOAD": "0",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(listener, "_has_cached_asr_model", side_effect=cached):
            selection = listener.resolve_asr_model_selection(allow_fallback=True)

        self.assertTrue(selection["available"])
        self.assertEqual(selection["selected"], "base")
        self.assertTrue(selection["fallback"])

    def test_get_whisper_model_fails_before_listening_when_model_is_unavailable(self):
        env = {
            "SPIRIT_ASR_MODEL_SIZE": "large-v3-turbo",
            "SPIRIT_ALLOW_MODEL_DOWNLOAD": "0",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(listener, "_has_cached_asr_model", return_value=False):
            with self.assertRaises(listener.AsrModelUnavailableError):
                listener.get_whisper_model(allow_fallback=False)

    def test_get_whisper_model_wraps_download_timeout_as_actionable_asr_error(self):
        fake_module = types.ModuleType("faster_whisper")

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                raise TimeoutError("connect timed out")

        fake_module.WhisperModel = FakeWhisperModel
        env = {
            "SPIRIT_ASR_MODEL_SIZE": "large-v3-turbo",
            "SPIRIT_ALLOW_MODEL_DOWNLOAD": "1",
        }

        with (
            patch.dict(os.environ, env, clear=False),
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch.object(listener, "_has_cached_asr_model", return_value=False),
        ):
            with self.assertRaises(listener.AsrModelUnavailableError) as ctx:
                listener.get_whisper_model(allow_fallback=False)

        self.assertIn("下载/加载 faster-whisper-large-v3-turbo 失败", str(ctx.exception))

    def test_listen_from_microphone_reports_missing_speech_recognition_without_crashing(self):
        with (
            patch.object(listener, "_get_recognizer", side_effect=ModuleNotFoundError("speech_recognition")),
            patch.object(listener, "show_emotion") as show_emotion,
        ):
            text = listener.listen_from_microphone(timeout=1, phrase_time_limit=1)

        self.assertIsNone(text)
        show_emotion.assert_called_once()
        self.assertEqual(show_emotion.call_args.args[0], "error")
        self.assertIn("语音监听依赖缺失", show_emotion.call_args.args[1])

    def test_collect_transcript_segments_rejects_probable_silence_hallucination(self):
        class Segment:
            def __init__(self, text, no_speech_prob, avg_logprob=-0.2):
                self.text = text
                self.no_speech_prob = no_speech_prob
                self.avg_logprob = avg_logprob
                self.start = 0.0
                self.end = 1.0

        text, diagnostics, rejected = listener._collect_transcript_segments(
            [Segment("谢谢观看", 0.96), Segment("打开浏览器", 0.1)]
        )

        self.assertEqual(text, "打开浏览器")
        self.assertEqual(rejected, 1)
        self.assertFalse(diagnostics[0]["accepted"])
        self.assertTrue(diagnostics[1]["accepted"])

    def test_rejects_short_video_call_to_action_hallucination(self):
        self.assertTrue(listener.is_probable_asr_hallucination_text("请不吝点赞 订阅 转 转 转 转 转 转 转 转"))
        self.assertTrue(listener.is_probable_asr_hallucination_text("谢谢观看 下期再见"))
        self.assertFalse(listener.is_probable_asr_hallucination_text("打开浏览器搜索天气"))

    def test_short_non_command_text_is_noise(self):
        self.assertTrue(listener._is_too_short_non_command_text("额好"))
        self.assertFalse(listener._is_too_short_non_command_text("打开"))
        self.assertFalse(listener._is_too_short_non_command_text("搜索天气"))

    def test_min_command_rms_can_be_configured(self):
        with patch.dict(os.environ, {"SPIRITKIN_ASR_MIN_RMS": "550"}, clear=False):
            self.assertEqual(listener._min_command_rms(), 550)

    def test_asr_language_supports_cantonese_aliases(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(listener._asr_language())

        with patch.dict(os.environ, {"SPIRITKIN_ASR_LANGUAGE": "zh-HK"}, clear=False):
            self.assertEqual(listener._asr_language(), "yue")
            self.assertIn("粵語", listener._asr_initial_prompt())

        with patch.dict(os.environ, {"SPIRITKIN_ASR_LANGUAGE": "auto"}, clear=False):
            self.assertIsNone(listener._asr_language())
            self.assertIn("Mandarin or Cantonese", listener._asr_initial_prompt())

    def test_resolve_microphone_device_index_prefers_explicit_index(self):
        with patch.dict(os.environ, {"SPIRITKIN_MIC_INDEX": "3"}, clear=True):
            index, metadata = listener.resolve_microphone_device_index()

        self.assertEqual(index, 3)
        self.assertEqual(metadata["selection"], "explicit_index")

    def test_resolve_microphone_device_index_keeps_legacy_explicit_index_name(self):
        with patch.dict(os.environ, {"SPIRITKIN_MIC_DEVICE_INDEX": "4"}, clear=True):
            index, metadata = listener.resolve_microphone_device_index()

        self.assertEqual(index, 4)
        self.assertEqual(metadata["selection"], "explicit_index")

    def test_resolve_microphone_device_index_skips_loopback_devices_by_default(self):
        devices = [
            {"index": 0, "name": "Stereo Mix (Realtek Audio)"},
            {"index": 1, "name": "Microphone Array (Realtek Audio)"},
        ]

        with patch.dict(os.environ, {}, clear=True), patch.object(listener, "list_microphone_devices", return_value=devices):
            index, metadata = listener.resolve_microphone_device_index()

        self.assertEqual(index, 1)
        self.assertIn(metadata["selection"], {"auto_non_loopback", "auto_preferred_mic"})

    def test_resolve_microphone_device_index_can_use_allowlist(self):
        devices = [
            {"index": 0, "name": "Microsoft Sound Mapper - Input"},
            {"index": 2, "name": "USB Podcast Mic"},
        ]

        with patch.dict(os.environ, {"SPIRITKIN_MIC_ALLOWLIST": "podcast"}, clear=True), patch.object(listener, "list_microphone_devices", return_value=devices):
            index, metadata = listener.resolve_microphone_device_index()

        self.assertEqual(index, 2)
        self.assertEqual(metadata["selection"], "allowlist")


if __name__ == "__main__":
    unittest.main()
