import unittest
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.runtime import SpiritKinRuntime
from backend.expression.performance import PerformanceController
from backend.perception.audio.realtime_session import RealtimeDuplexSession, RealtimeSessionConfig


class FakeAgent:
    def __init__(self):
        self.calls = []

    def process(self, user_input, visual_context="", channel=None, input_metadata=None):
        self.calls.append((user_input, visual_context, channel, input_metadata or {}))
        return AgentReply(text=f"已处理：{user_input}", spoken_text=f"好的，{user_input}", emotion="happy")


class FakeSpeech:
    def __init__(self):
        self.active = False
        self.spoken = []
        self.stop_calls = 0

    def is_speaking(self):
        return self.active

    def stop(self):
        self.stop_calls += 1
        was_active = self.active
        self.active = False
        return was_active

    def speak(self, text, *, block=False):
        self.spoken.append((text, block))
        self.active = True
        return None


class RealtimeDuplexSessionTests(unittest.TestCase):
    def _session(self, listener):
        events = []
        agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=agent, hotword="Spirit")
        speech = FakeSpeech()
        session = RealtimeDuplexSession(
            runtime,
            listener=listener,
            speech_controller=speech,
            performance=PerformanceController(events.append),
            config=RealtimeSessionConfig(max_turns=1, idle_timeouts=2, phrase_time_limit=3),
        )
        return session, agent, speech, events

    def test_run_processes_cleaned_hotword_voice_turn(self):
        queue = [{"text": ""}, {"text": "Spirit 打开浏览器", "elapsed": 0.1}]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, speech, events = self._session(listener)
        turns = session.run()

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].cleaned_text, "打开浏览器")
        self.assertEqual(agent.calls[0][0], "打开浏览器")
        self.assertEqual(agent.calls[0][2], "voice")
        self.assertEqual(agent.calls[0][3]["raw_voice_text"], "Spirit 打开浏览器")
        self.assertEqual(speech.spoken[0][0], "好的，打开浏览器")
        phases = [event["payload"].get("phase") for event in events if event["type"] == "performance.state"]
        self.assertIn("listening", phases)
        self.assertIn("thinking", phases)
        self.assertIn("speaking", phases)

    def test_realtime_voice_applies_asr_correction_before_agent(self):
        session, agent, _speech, _events = self._session(lambda **_: {"text": ""})

        turn = session.process_text("Spirit s现在能听到我所谓。。")

        self.assertIsNotNone(turn)
        self.assertEqual(turn.cleaned_text, "现在能听到我说话。")
        self.assertEqual(agent.calls[0][0], "现在能听到我说话。")
        self.assertEqual(agent.calls[0][3]["raw_voice_text"], "Spirit s现在能听到我所谓。。")
        self.assertEqual(agent.calls[0][3]["asr_corrected_text"], "现在能听到我说话。")

    def test_run_requires_hotword_before_accepting_voice_turn(self):
        queue = [{"text": "打开浏览器"}, {"text": "Spirit 打开浏览器"}]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, _speech, events = self._session(listener)
        turns = session.run()

        self.assertEqual(len(turns), 1)
        self.assertEqual(agent.calls[0][0], "打开浏览器")
        wake_required = [
            event
            for event in events
            if event["type"] == "performance.state" and event["payload"].get("metadata", {}).get("reason") == "wake_required"
        ]
        self.assertTrue(wake_required)
        wake_avatar_events = [
            event
            for event in events
            if event["type"] == "avatar.state" and event["payload"].get("metadata", {}).get("reason") == "wake_required"
        ]
        self.assertTrue(wake_avatar_events)
        self.assertEqual(wake_avatar_events[0]["payload"].get("message"), "")

    def test_performance_avatar_state_never_surfaces_status_text(self):
        session, _agent, _speech, events = self._session(lambda **_: {"text": ""})

        metrics = session.listen_once()
        session.performance.emit("thinking", "我听到了，正在理解。")

        self.assertEqual(metrics["text"], "")
        avatar_events = [
            event
            for event in events
            if event["type"] == "avatar.state" and event["payload"].get("performance_phase") in {"listening", "thinking"}
        ]
        self.assertTrue(avatar_events)
        self.assertTrue(all(event["payload"].get("message") == "" for event in avatar_events))

    def test_wake_window_accepts_followup_without_repeating_hotword(self):
        queue = [
            {"text": "Spirit 打开浏览器"},
            {"text": "打开飞书"},
        ]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, speech, _events = self._session(listener)

        def speak_without_staying_active(text, *, block=False):
            speech.spoken.append((text, block))
            speech.active = False

        speech.speak = speak_without_staying_active
        turns = session.run(max_turns=2)

        self.assertEqual([turn.cleaned_text for turn in turns], ["打开浏览器", "打开飞书"])
        self.assertEqual([call[0] for call in agent.calls], ["打开浏览器", "打开飞书"])

    def test_strict_hotword_requires_prefix_for_each_turn(self):
        queue = [
            {"text": "Spirit 打开浏览器"},
            {"text": "打开飞书"},
            {"text": "Spirit 打开微信"},
        ]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, speech, events = self._session(listener)
        session.config = RealtimeSessionConfig(max_turns=2, idle_timeouts=2, phrase_time_limit=3, strict_hotword=True)

        def speak_without_staying_active(text, *, block=False):
            speech.spoken.append((text, block))
            speech.active = False

        speech.speak = speak_without_staying_active
        turns = session.run(max_turns=2)

        self.assertEqual([turn.cleaned_text for turn in turns], ["打开浏览器", "打开微信"])
        self.assertEqual([call[0] for call in agent.calls], ["打开浏览器", "打开微信"])
        wake_required = [
            event
            for event in events
            if event["type"] == "performance.state" and event["payload"].get("metadata", {}).get("reason") == "wake_required"
        ]
        self.assertTrue(wake_required)

    def test_barge_in_stops_previous_speech_before_processing_new_text(self):
        session, agent, speech, events = self._session(lambda **_: {"text": ""})

        session.process_text("第一句")
        self.assertTrue(speech.is_speaking())
        session.process_text("插话")

        self.assertEqual(agent.calls[-1][0], "插话")
        self.assertGreaterEqual(speech.stop_calls, 1)
        interrupted = [event for event in events if event["type"] == "performance.state" and event["payload"]["phase"] == "interrupted"]
        self.assertTrue(interrupted)

    def test_request_interrupt_queues_text_for_next_turn(self):
        session, agent, speech, _ = self._session(lambda **_: {"text": ""})
        speech.active = True

        self.assertTrue(session.request_interrupt("改成打开飞书"))
        turns = session.run(max_turns=1)

        self.assertEqual(turns[0].cleaned_text, "改成打开飞书")
        self.assertEqual(agent.calls[0][0], "改成打开飞书")

    def test_listen_once_suppresses_microphone_while_assistant_is_speaking(self):
        def listener(**kwargs):
            raise AssertionError("listener should not run while playback echo is suppressed")

        session, _agent, speech, events = self._session(listener)
        speech.active = True

        metrics = session.listen_once()

        self.assertEqual(metrics["error"], "playback_echo_suppressed")
        self.assertTrue(metrics["suppressed_playback"])
        suppressed = [event for event in events if event["type"] == "performance.state" and event["payload"].get("metadata", {}).get("reason") == "playback_echo_suppressed"]
        self.assertTrue(suppressed)

    def test_unlimited_live_session_does_not_exit_on_idle_timeouts(self):
        calls = 0
        holder = {}

        def listener(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                holder["session"].stop()
            return {"text": ""}

        events = []
        runtime = SpiritKinRuntime(agent=FakeAgent(), hotword="Spirit")
        session = RealtimeDuplexSession(
            runtime,
            listener=listener,
            speech_controller=FakeSpeech(),
            performance=PerformanceController(events.append),
            config=RealtimeSessionConfig(max_turns=0, idle_timeouts=0, speak_responses=False),
        )
        holder["session"] = session

        turns = session.run()

        self.assertEqual(turns, [])
        self.assertEqual(calls, 3)
        phases = [event["payload"].get("phase") for event in events if event["type"] == "performance.state"]
        self.assertIn("attentive_wait", phases)

    def test_run_ignores_probable_asr_hallucination_text(self):
        queue = [
            {"text": "请不吝点赞 订阅 转 转 转 转 转 转 转 转"},
            {"text": "Spirit 打开浏览器"},
        ]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, _speech, events = self._session(listener)
        turns = session.run()

        self.assertEqual(len(turns), 1)
        self.assertEqual(agent.calls[0][0], "打开浏览器")
        ignored = [event for event in events if event["type"] == "performance.state" and event["payload"].get("metadata", {}).get("reason") == "asr_hallucination"]
        self.assertTrue(ignored)

    def test_run_ignores_text_matching_recent_playback_echo(self):
        queue = [
            {"text": "Spirit 打开浏览器"},
            {"text": "好的打开浏览器"},
            {"text": "Spirit 打开飞书"},
        ]

        def listener(**kwargs):
            return queue.pop(0) if queue else {"text": ""}

        session, agent, speech, events = self._session(listener)
        def speak_without_staying_active(text, *, block=False):
            speech.spoken.append((text, block))
            speech.active = False

        speech.speak = speak_without_staying_active
        turns = session.run(max_turns=2)

        self.assertEqual([turn.cleaned_text for turn in turns], ["打开浏览器", "打开飞书"])
        self.assertEqual([call[0] for call in agent.calls], ["打开浏览器", "打开飞书"])
        ignored = [event for event in events if event["type"] == "performance.state" and event["payload"].get("metadata", {}).get("reason") == "playback_echo_text_suppressed"]
        self.assertTrue(ignored)

    def test_safe_print_does_not_crash_on_gbk_encoded_stdout(self):
        import io

        from backend.perception.audio.realtime_session import _configure_utf8_stdio, _safe_print

        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")

        with patch("sys.stdout", stream):
            _safe_print("bad char: \ufffd")
            _configure_utf8_stdio()

    def test_start_realtime_session_accepts_language_and_tts_overrides(self):
        from backend.perception.audio import realtime_session

        created = {}

        class FakeSession:
            def __init__(self, runtime, config=None, **kwargs):
                created["config"] = config
                created["kwargs"] = kwargs

            def run(self, *, visual_context=""):
                created["visual_context"] = visual_context
                return []

            def stop(self):
                created["stopped"] = True

        with patch("backend.perception.audio.realtime_session.SpiritKinRuntime", return_value=SpiritKinRuntime(agent=FakeAgent(), hotword="Spirit")), \
             patch("backend.perception.audio.listener.get_whisper_model", return_value=object()), \
             patch("backend.perception.audio.listener.resolve_microphone_device_index", return_value=(1, {"name": "Mic"})), \
             patch("backend.perception.audio.realtime_session.RealtimeDuplexSession", FakeSession), \
             patch("backend.expression.edge_tts.EdgeTTSProvider.is_available", return_value=False), \
             patch.dict("os.environ", {}, clear=True):
            realtime_session.start_realtime_session(
                max_turns=0,
                idle_timeouts=0,
                speak_responses=False,
                asr_language="zh-HK",
                tts_voice="zh-HK-HiuMaanNeural",
            )

        self.assertEqual(created["config"].max_turns, 0)
        self.assertTrue(created["stopped"])


if __name__ == "__main__":
    unittest.main()
