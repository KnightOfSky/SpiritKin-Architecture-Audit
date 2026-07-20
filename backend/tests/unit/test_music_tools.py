from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.security.safety_control import SafetyDecision
from backend.security.tool_authz import ToolAuthzRegistry
from backend.tools.base import ToolCall
from backend.tools.music_tools import MusicCommandQueue, get_music_tools, validate_music_path
from backend.tools.registry import ToolRegistry


class MusicToolTests(unittest.TestCase):
    def test_queue_preserves_command_order(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.jsonl"
            queue = MusicCommandQueue(path)

            first = queue.enqueue("play", {"paths": ["one.mp3"]})
            second = queue.enqueue("pause")
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([item["command_id"] for item in records], [first["command_id"], second["command_id"]])
        self.assertEqual([item["action"] for item in records], ["play", "pause"])

    def test_local_play_resolves_only_files_inside_music_roots(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            song = root / "focus.mp3"
            song.write_bytes(b"fake")
            queue = MusicCommandQueue(Path(tmp) / "commands.jsonl")
            play = next(tool for tool in get_music_tools(queue=queue, music_roots=(root.resolve(),)) if tool.spec.name == "music.play")

            result = play.invoke(ToolCall("music.play", {"path": str(song)}))

        self.assertTrue(result.success)
        self.assertEqual(result.data["arguments"]["paths"], [str(song.resolve())])

    def test_directory_escape_is_denied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            outside = Path(tmp) / "outside.mp3"
            outside.write_bytes(b"fake")

            with self.assertRaises(PermissionError):
                validate_music_path(outside, roots=(root.resolve(),))

    def test_unsupported_local_file_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "notes.txt"
            bad.write_text("not audio", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported"):
                validate_music_path(bad, roots=(root.resolve(),))

    def test_remote_url_is_disabled_by_default(self):
        with TemporaryDirectory() as tmp:
            remote = next(
                tool
                for tool in get_music_tools(queue=MusicCommandQueue(Path(tmp) / "commands.jsonl"))
                if tool.spec.name == "music.play_url"
            )
            with patch.dict(os.environ, {"SPIRITKIN_MUSIC_REMOTE_URLS": "0"}, clear=False):
                result = remote.invoke(ToolCall("music.play_url", {"url": "https://example.com/audio.mp3"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "music_remote_disabled")

    def test_remote_url_requires_network_authorization(self):
        with TemporaryDirectory() as tmp:
            authz = ToolAuthzRegistry(Path(tmp) / "authz.json")
            queue = MusicCommandQueue(Path(tmp) / "commands.jsonl")
            registry = ToolRegistry(authz_registry=authz)
            registry.register_many(get_music_tools(queue=queue, music_roots=(Path(tmp).resolve(),)))

            blocked = registry.invoke(ToolCall(
                "music.play_url",
                {"url": "https://example.com/audio.mp3", "authz_enforce_confirmation": True},
            ))
            with patch.dict(os.environ, {"SPIRITKIN_MUSIC_REMOTE_URLS": "1"}, clear=False), \
                 patch("backend.tools.registry.evaluate_execution_safety", return_value=SafetyDecision(True)):
                allowed = registry.invoke(ToolCall(
                    "music.play_url",
                    {"url": "https://example.com/audio.mp3", "authz_confirmed": True},
                ))

        self.assertFalse(blocked.success)
        self.assertEqual(blocked.error_code, "tool_confirmation_required")
        self.assertTrue(allowed.success)

    def test_volume_boundaries_are_enforced(self):
        with TemporaryDirectory() as tmp:
            volume = next(
                tool
                for tool in get_music_tools(queue=MusicCommandQueue(Path(tmp) / "commands.jsonl"))
                if tool.spec.name == "music.volume"
            )

            low = volume.invoke(ToolCall("music.volume", {"volume": -0.1}))
            high = volume.invoke(ToolCall("music.volume", {"volume": 1.1}))
            valid = volume.invoke(ToolCall("music.volume", {"volume": 0.4}))

        self.assertFalse(low.success)
        self.assertFalse(high.success)
        self.assertTrue(valid.success)


if __name__ == "__main__":
    unittest.main()
