from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.state_store import resolve_state_path
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec

DEFAULT_MUSIC_COMMAND_PATH = "state/music/commands.jsonl"
DEFAULT_MUSIC_STATUS_PATH = "state/music/status.json"
SUPPORTED_MUSIC_EXTENSIONS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"}
_QUEUE_LOCK = threading.RLock()


def resolve_music_command_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MUSIC_COMMAND_PATH", DEFAULT_MUSIC_COMMAND_PATH, path)


def resolve_music_status_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MUSIC_STATUS_PATH", DEFAULT_MUSIC_STATUS_PATH, path)


def resolve_music_roots(roots: list[str | os.PathLike[str]] | None = None) -> tuple[Path, ...]:
    if roots is None:
        configured = (os.getenv("SPIRITKIN_MUSIC_ROOTS") or "").strip()
        if configured:
            roots = [part for part in configured.split(os.pathsep) if part.strip()]
        else:
            roots = [Path.cwd(), Path.home() / "Music"]
    resolved: list[Path] = []
    for raw in roots:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        path = candidate.resolve()
        if path not in resolved:
            resolved.append(path)
    return tuple(resolved)


def validate_music_path(value: Any, *, roots: tuple[Path, ...] | None = None) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("music path is required")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve(strict=True)
    allowed_roots = roots or resolve_music_roots()
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise PermissionError("music path is outside the configured music roots")
    if resolved.is_file() and resolved.suffix.lower() not in SUPPORTED_MUSIC_EXTENSIONS:
        raise ValueError(f"unsupported music file type: {resolved.suffix or '<none>'}")
    if not resolved.is_file() and not resolved.is_dir():
        raise ValueError("music path must be a file or directory")
    return resolved


def validate_remote_music_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("remote music URL must use http or https")
    enabled = (os.getenv("SPIRITKIN_MUSIC_REMOTE_URLS") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise PermissionError("remote music URLs are disabled")
    return raw


class MusicCommandQueue:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = resolve_music_command_path(path)

    def enqueue(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        command = {
            "schema_version": "spiritkin.music_command.v1",
            "command_id": f"music_{uuid.uuid4().hex[:16]}",
            "action": str(action or "").strip().lower(),
            "arguments": dict(arguments or {}),
            "created_at": time.time(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _QUEUE_LOCK:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        return command


class MusicControlTool(BaseTool):
    def __init__(
        self,
        spec: ToolSpec,
        action: str,
        *,
        queue: MusicCommandQueue | None = None,
        music_roots: tuple[Path, ...] | None = None,
        remote: bool = False,
    ):
        self.spec = spec
        self.action = action
        self.queue = queue or MusicCommandQueue()
        self.music_roots = music_roots
        self.remote = remote

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(False, f"不支持的工具: {call.name}", error_code="tool_not_supported")
        arguments = dict(call.arguments or {})
        try:
            normalized = self._normalize_arguments(arguments)
            command = self.queue.enqueue(self.action, normalized)
        except FileNotFoundError:
            return ToolResult(False, "音乐文件或目录不存在。", error_code="music_path_not_found")
        except PermissionError as exc:
            return ToolResult(False, str(exc), error_code="music_path_denied" if not self.remote else "music_remote_disabled")
        except (TypeError, ValueError) as exc:
            return ToolResult(False, str(exc), error_code="music_invalid_arguments")
        return ToolResult(
            True,
            "音乐命令已提交。",
            data=command,
            metadata={"music_command_id": command["command_id"], "music_action": self.action},
        )

    def _normalize_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.remote:
            return {"url": validate_remote_music_url(arguments.get("url")), "autoplay": bool(arguments.get("autoplay", True))}
        if self.action in {"play", "queue"}:
            raw_paths = arguments.get("paths")
            if raw_paths is None:
                raw_paths = [arguments.get("path")]
            if not isinstance(raw_paths, list):
                raise ValueError("music paths must be an array")
            paths = [str(validate_music_path(path, roots=self.music_roots)) for path in raw_paths if str(path or "").strip()]
            if not paths:
                raise ValueError("at least one music path is required")
            return {
                "paths": paths,
                "autoplay": bool(arguments.get("autoplay", self.action == "play")),
                "replace": bool(arguments.get("replace", self.action == "play")),
            }
        if self.action == "seek":
            seconds = float(arguments.get("seconds", 0))
            if seconds < 0:
                raise ValueError("seek seconds must be non-negative")
            return {"seconds": seconds}
        if self.action == "volume":
            volume = float(arguments.get("volume", 1))
            if volume < 0 or volume > 1:
                raise ValueError("volume must be between 0 and 1")
            return {"volume": volume}
        if self.action == "loop":
            mode = str(arguments.get("mode") or "off").strip().lower()
            if mode not in {"off", "all", "one"}:
                raise ValueError("loop mode must be off, all, or one")
            return {"mode": mode}
        return {}


def get_music_tools(
    *,
    queue: MusicCommandQueue | None = None,
    music_roots: tuple[Path, ...] | None = None,
) -> list[MusicControlTool]:
    local_specs = (
        ("music.play", "播放本地音乐文件或目录", "play", {"paths": {"type": "array"}, "autoplay": {"type": "boolean"}}),
        ("music.pause", "暂停本地音乐", "pause", {}),
        ("music.resume", "继续本地音乐", "resume", {}),
        ("music.stop", "停止本地音乐", "stop", {}),
        ("music.next", "播放下一首", "next", {}),
        ("music.previous", "播放上一首", "previous", {}),
        ("music.seek", "跳转本地音乐进度", "seek", {"seconds": {"type": "number", "minimum": 0}}),
        ("music.volume", "设置本地音乐音量", "volume", {"volume": {"type": "number", "minimum": 0, "maximum": 1}}),
        ("music.loop", "设置本地音乐循环模式", "loop", {"mode": {"enum": ["off", "all", "one"]}}),
        ("music.queue", "追加本地音乐队列", "queue", {"paths": {"type": "array"}}),
    )
    tools = [
        MusicControlTool(
            ToolSpec(name, description, "desktop_media", f"music_{action}", read_only=True, schema=schema),
            action,
            queue=queue,
            music_roots=music_roots,
        )
        for name, description, action, schema in local_specs
    ]
    tools.append(
        MusicControlTool(
            ToolSpec(
                "music.play_url",
                "播放已授权的远程音乐 URL",
                "network",
                "music_play_url",
                risk_level="medium",
                schema={"url": {"type": "string", "format": "uri"}},
            ),
            "play_url",
            queue=queue,
            remote=True,
        )
    )
    return tools


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
