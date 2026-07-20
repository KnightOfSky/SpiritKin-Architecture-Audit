import json
import logging
import unittest
from unittest.mock import patch

from backend.app.realtime_bridge import RealtimeEventHub
from backend.runtime import RuntimeEventBus


class RealtimeEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_publishes_received_events_to_runtime_event_bus(self):
        bus = RuntimeEventBus()
        delivered = []
        bus.subscribe("assistant.*", delivered.append)
        hub = RealtimeEventHub(event_bus=bus)

        await hub.publish(
            {
                "type": "assistant.message",
                "payload": {"text": "hello", "workspace_id": "tenant-a", "request_id": "request-a"},
            }
        )

        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].workspace_id, "tenant-a")
        self.assertEqual(delivered[0].correlation_id, "request-a")

    def test_handshake_noise_filter_only_drops_invalid_probe_message(self):
        from backend.app.realtime_bridge import _HandshakeNoiseFilter

        noise = logging.LogRecord("websockets.server", logging.ERROR, __file__, 1, "opening handshake failed", (), None)
        failure = logging.LogRecord("websockets.server", logging.ERROR, __file__, 1, "server crashed", (), None)
        self.assertFalse(_HandshakeNoiseFilter().filter(noise))
        self.assertTrue(_HandshakeNoiseFilter().filter(failure))

    async def test_hub_keeps_recent_events_in_snapshot(self):
        hub = RealtimeEventHub(history_limit=2)

        await hub.publish({"type": "assistant.message", "schema_version": "v1", "payload": {"text": "hello"}})
        await hub.publish({"type": "device.openclaw_state_updated", "schema_version": "v1", "payload": {"state": {"state": "idle"}}})
        await hub.publish({"type": "avatar.state", "schema_version": "v1", "payload": {"emotion": "happy"}})

        snapshot = hub.build_snapshot_event()

        self.assertEqual(snapshot["type"], "runtime.snapshot")
        self.assertEqual([event["type"] for event in snapshot["payload"]["events"]], [
            "device.openclaw_state_updated",
            "avatar.state",
        ])
        self.assertEqual(snapshot["payload"]["aggregated_state"]["schema_version"], "spiritkin.aggregated_runtime_state.v1")

    async def test_snapshot_carries_latest_avatar_state_per_session(self):
        hub = RealtimeEventHub(history_limit=2)

        await hub.publish({
            "type": "avatar.state",
            "schema_version": "v1",
            "payload": {"emotion": "happy", "action": "wave", "speaking": True, "session_id": "s1"},
        })
        await hub.publish({
            "type": "avatar.state",
            "schema_version": "v1",
            "payload": {"emotion": "thinking", "action": "idle", "session_id": "s1"},
        })
        await hub.publish({
            "type": "avatar.state",
            "schema_version": "v1",
            "payload": {"emotion": "alert", "action": "nod", "session_id": "s2"},
        })

        avatar_states = hub.build_snapshot_event()["payload"]["avatar_states"]

        # Newest per session wins; sessions never bleed into one another.
        self.assertEqual(avatar_states["s1"]["emotion"], "thinking")
        self.assertEqual(avatar_states["s1"]["action"], "idle")
        self.assertEqual(avatar_states["s2"]["emotion"], "alert")

    async def test_avatar_state_snapshot_survives_event_history_eviction(self):
        hub = RealtimeEventHub(history_limit=1)

        await hub.publish({
            "type": "avatar.state",
            "schema_version": "v1",
            "payload": {"emotion": "happy", "session_id": "s1"},
        })
        # This pushes the avatar.state off the recent-events tail (limit=1).
        await hub.publish({"type": "assistant.message", "schema_version": "v1", "payload": {"text": "hi"}})

        snapshot = hub.build_snapshot_event()

        self.assertEqual([e["type"] for e in snapshot["payload"]["events"]], ["assistant.message"])
        # But the authoritative avatar state is still available for cold-start alignment.
        self.assertEqual(snapshot["payload"]["avatar_states"]["s1"]["emotion"], "happy")

    def test_hub_decode_message_requires_typed_json_object(self):
        self.assertIsNone(RealtimeEventHub.decode_message("not-json"))
        self.assertIsNone(RealtimeEventHub.decode_message("[]"))
        self.assertIsNone(RealtimeEventHub.decode_message('{"payload": {}}'))
        self.assertEqual(
            RealtimeEventHub.decode_message('{"type": "assistant.message", "payload": {"text": "ok"}}')["type"],
            "assistant.message",
        )

    def test_bridge_auth_allows_only_matching_runtime_auth_token(self):
        from backend.app import realtime_bridge

        with patch.dict("os.environ", {"SPIRITKIN_BRIDGE_AUTH": "1", "SPIRITKIN_DESKTOP_TOKEN": "secret"}, clear=False):
            self.assertTrue(realtime_bridge.bridge_auth_allowed({"type": "runtime.auth", "token": "secret"}))
            self.assertTrue(realtime_bridge.bridge_auth_allowed({"type": "auth", "authorization": "Bearer secret"}))
            self.assertFalse(realtime_bridge.bridge_auth_allowed({"type": "runtime.auth", "token": "wrong"}))
            self.assertFalse(realtime_bridge.bridge_auth_allowed({"type": "assistant.message", "token": "secret"}))

    def test_bridge_auth_accepts_mobile_token_fallback(self):
        from backend.app import realtime_bridge

        env = {
            "SPIRITKIN_BRIDGE_AUTH": "1",
            "SPIRITKIN_DESKTOP_TOKEN": "",
            "SPIRITKIN_API_TOKEN": "",
            "SPIRITKIN_MOBILE_TOKEN": "mobile-secret",
        }
        with patch.dict("os.environ", env, clear=False):
            self.assertTrue(realtime_bridge.bridge_auth_allowed({"type": "runtime.auth", "token": "mobile-secret"}))

    def test_local_browser_trust_requires_loopback_peer_and_origin(self):
        from backend.app.realtime_bridge import bridge_local_browser_allowed

        class Request:
            headers = {"Origin": "http://127.0.0.1:8792"}

        websocket = type("WebSocket", (), {"remote_address": ("127.0.0.1", 50000), "request": Request()})()
        self.assertTrue(bridge_local_browser_allowed(websocket))
        Request.headers = {"Origin": "https://example.com"}
        self.assertFalse(bridge_local_browser_allowed(websocket))
        Request.headers = {"Origin": "http://localhost:8792"}
        websocket.remote_address = ("192.0.2.2", 50000)
        self.assertFalse(bridge_local_browser_allowed(websocket))

    async def test_handle_connection_closes_when_auth_frame_is_missing_or_wrong(self):
        class FakeWebSocket:
            def __init__(self):
                self.closed = None

            async def recv(self):
                return '{"type":"runtime.auth","token":"wrong"}'

            async def close(self, code=None, reason=""):
                self.closed = (code, reason)

        hub = RealtimeEventHub()
        websocket = FakeWebSocket()
        with patch.dict("os.environ", {"SPIRITKIN_BRIDGE_AUTH": "1", "SPIRITKIN_DESKTOP_TOKEN": "secret"}, clear=False):
            await hub.handle_connection(websocket)

        self.assertEqual(websocket.closed, (1008, "bridge auth required"))

    async def test_handle_connection_ignores_auth_frames_after_subscribe(self):
        class FakeWebSocket:
            def __init__(self):
                self.sent: list[dict] = []

            async def recv(self):
                return '{"type":"runtime.auth","token":"secret"}'

            async def send(self, message):
                self.sent.append(json.loads(message))

            def __aiter__(self):
                self._messages = iter(
                    [
                        '{"type":"runtime.auth","token":"secret"}',
                        '{"type":"assistant.message","payload":{"text":"ok"}}',
                    ]
                )
                return self

            async def __anext__(self):
                try:
                    return next(self._messages)
                except StopIteration:
                    raise StopAsyncIteration from None

        hub = RealtimeEventHub()
        websocket = FakeWebSocket()
        with patch.dict("os.environ", {"SPIRITKIN_BRIDGE_AUTH": "1", "SPIRITKIN_DESKTOP_TOKEN": "secret"}, clear=False):
            await hub.handle_connection(websocket)

        event_types = [event.get("type") for event in hub.build_snapshot_event()["payload"]["events"]]
        self.assertEqual(event_types, ["assistant.message"])


if __name__ == "__main__":
    unittest.main()
