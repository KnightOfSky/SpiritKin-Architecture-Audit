import unittest

from backend.expression.model_interaction import (
    MODEL_INTERACTION_EVENT_TYPE,
    build_performance_interaction,
    build_response_interaction,
    build_speech_interaction,
    normalize_motion_action,
)


class ModelInteractionProtocolTests(unittest.TestCase):
    def test_response_interaction_maps_reply_to_3d_protocol(self):
        event = build_response_interaction(
            {
                "text": "需要确认。",
                "spoken_text": "这个操作需要你确认。",
                "emotion": "waiting",
                "action": "await_confirmation",
                "agent_name": "execution_guard",
                "requires_confirmation": True,
                "response_kind": "confirmation_request",
                "data": {
                    "input_channel": "voice",
                    "session_id": "sess-1",
                    "request_id": "req-1",
                },
            }
        )

        payload = event["payload"]
        self.assertEqual(event["type"], MODEL_INTERACTION_EVENT_TYPE)
        self.assertEqual(payload["protocol"], "spiritkin.model_interaction.v1")
        self.assertEqual(payload["phase"], "waiting_confirmation")
        self.assertEqual(payload["emotion"], "waiting")
        self.assertEqual(payload["action"], "nod")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["input_channel"], "voice")
        self.assertIn("audio_meter", payload["capabilities"]["display_channels"])

    def test_performance_interaction_does_not_surface_status_text(self):
        event = build_performance_interaction("thinking", "我听到了，正在理解。", {"reason": "voice"})

        payload = event["payload"]
        self.assertEqual(payload["phase"], "thinking")
        self.assertEqual(payload["emotion"], "thinking")
        self.assertEqual(payload["screen_text"], "")
        self.assertEqual(payload["subtitle_text"], "")
        self.assertEqual(payload["metadata"]["reason"], "voice")

    def test_speech_phoneme_interaction_carries_mouth_shape(self):
        event = build_speech_interaction(
            "speech.phoneme",
            {
                "speech_id": "speech-1",
                "mouth_shape": "aa",
                "timestamp_ms": 80,
                "duration_ms": 160,
                "source": "speech_controller",
            },
        )

        payload = event["payload"]
        self.assertEqual(payload["phase"], "speaking")
        self.assertTrue(payload["speaking"])
        self.assertEqual(payload["mouth_shape"], "aa")
        self.assertEqual(payload["timestamp_ms"], 80)
        self.assertEqual(payload["duration_ms"], 160)

    def test_motion_aliases_are_current_3d_model_actions(self):
        self.assertEqual(normalize_motion_action("wave_hand"), "wave")
        self.assertEqual(normalize_motion_action("execute_task"), "nod")
        self.assertEqual(normalize_motion_action("cancel_execution"), "shake")
        self.assertEqual(normalize_motion_action("scan_screen"), "walk_forward")


if __name__ == "__main__":
    unittest.main()
