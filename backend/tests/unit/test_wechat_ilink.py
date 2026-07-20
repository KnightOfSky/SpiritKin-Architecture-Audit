from __future__ import annotations

import unittest

from backend.channels.wechat_ilink import (
    ILinkAuthError,
    ILinkConfig,
    ILinkProtocolClient,
    ILinkSessionExpired,
    WeChatILinkChannel,
)


class ILinkTests(unittest.TestCase):
    def _config(self) -> ILinkConfig:
        return ILinkConfig(enabled=True, bot_token="token-1", bot_id="bot-1", base_url="https://ilink.test")

    def test_get_updates_parses_snake_case_text_and_cursor(self):
        calls = []

        def transport(method, url, headers, payload, timeout):
            calls.append((method, url, headers, payload, timeout))
            return {
                "msgs": [
                    {
                        "message_id": 7,
                        "from_user_id": "user-1",
                        "to_user_id": "bot-1",
                        "message_type": 1,
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                    },
                    {"message_id": 8, "message_type": 2, "item_list": [{"type": 1, "text_item": {"text": "echo"}}]},
                ],
                "get_updates_buf": "cursor-2",
            }

        client = ILinkProtocolClient(self._config(), transport=transport, uin_factory=lambda: "uin-test")
        messages, cursor = client.get_updates("cursor-1")

        self.assertEqual(cursor, "cursor-2")
        self.assertEqual(messages[0].text, "你好")
        self.assertEqual(messages[0].context_token, "ctx-1")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer token-1")
        self.assertEqual(calls[0][2]["X-WECHAT-UIN"], "uin-test")
        self.assertEqual(calls[0][3]["get_updates_buf"], "cursor-1")

    def test_send_text_uses_bot_message_shape(self):
        captured = {}

        def transport(method, url, headers, payload, timeout):
            captured.update(payload)
            return {"ret": 0, "message_id": "out-1"}

        client = ILinkProtocolClient(self._config(), transport=transport, uin_factory=lambda: "uin-test")
        client.send_text("user-1", "收到", "ctx-1")
        message = captured["msg"]

        self.assertEqual(message["message_type"], 2)
        self.assertEqual(message["message_state"], 2)
        self.assertEqual(message["to_user_id"], "user-1")
        self.assertEqual(message["context_token"], "ctx-1")
        self.assertEqual(message["item_list"][0]["text_item"]["text"], "收到")

    def test_auth_and_expiry_are_distinct_errors(self):
        auth_client = ILinkProtocolClient(self._config(), transport=lambda *_: (_ for _ in ()).throw(ILinkAuthError("401")))
        with self.assertRaises(ILinkAuthError):
            auth_client.get_updates()

        expired_client = ILinkProtocolClient(self._config(), transport=lambda *_: {"ret": -14})
        with self.assertRaises(ILinkSessionExpired):
            expired_client.get_updates()

    def test_channel_run_once_delivers_reply_and_keeps_cursor(self):
        sent = []

        def transport(method, url, headers, payload, timeout):
            if url.endswith("getupdates"):
                return {
                    "msgs": [{
                        "message_id": 1,
                        "from_user_id": "user-1",
                        "message_type": 1,
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
                    }],
                    "get_updates_buf": "cursor-1",
                }
            sent.append(payload)
            return {"ret": 0}

        client = ILinkProtocolClient(self._config(), transport=transport, uin_factory=lambda: "uin-test")
        channel = WeChatILinkChannel(self._config(), client=client, on_message=lambda msg: "pong")

        self.assertEqual(channel.run_once(), 1)
        self.assertEqual(channel.sync_buf, "cursor-1")
        self.assertEqual(sent[0]["msg"]["item_list"][0]["text_item"]["text"], "pong")

    def test_disabled_channel_does_not_start(self):
        channel = WeChatILinkChannel(ILinkConfig(enabled=False))
        self.assertFalse(channel.start())
        self.assertEqual(channel.status.phase, "config_missing")


if __name__ == "__main__":
    unittest.main()
