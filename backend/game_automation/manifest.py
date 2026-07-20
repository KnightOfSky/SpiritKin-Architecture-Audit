from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_ADAPTER_ROOT = "config/game_automation/adapters"


@dataclass(frozen=True)
class GameAdapterManifest:
    adapter_id: str
    label: str
    allowed_origins: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    title_pattern: str
    expected_scenes: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    max_actions_per_second: float = 5.0
    requires_focus: bool = True
    risk_level: str = "high"
    stop_conditions: tuple[str, ...] = ("focus_lost", "unknown_scene", "kill_switch", "rate_limit")

    def accepts_url(self, value: str) -> bool:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        origin = f"{parsed.scheme}://{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}"
        origin_allowed = any(_origin_matches(origin, pattern) for pattern in self.allowed_origins)
        path_allowed = any(parsed.path == path or parsed.path.startswith(path.rstrip("/") + "/") for path in self.allowed_paths)
        return origin_allowed and path_allowed

    def accepts_title(self, title: str) -> bool:
        try:
            return re.search(self.title_pattern, str(title or ""), flags=re.IGNORECASE) is not None
        except re.error:
            return False


def resolve_game_adapter_root(root: str | os.PathLike[str] | None = None) -> Path:
    raw = Path(root or os.getenv("SPIRITKIN_GAME_ADAPTER_ROOT") or DEFAULT_ADAPTER_ROOT).expanduser()
    return (raw if raw.is_absolute() else Path.cwd() / raw).resolve()


def load_game_adapter_manifests(root: str | os.PathLike[str] | None = None) -> dict[str, GameAdapterManifest]:
    adapter_root = resolve_game_adapter_root(root)
    if not adapter_root.exists():
        return {}
    manifests: dict[str, GameAdapterManifest] = {}
    for path in sorted(adapter_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = parse_game_adapter_manifest(payload)
        if manifest.adapter_id in manifests:
            raise ValueError(f"duplicate game adapter id: {manifest.adapter_id}")
        manifests[manifest.adapter_id] = manifest
    return manifests


def parse_game_adapter_manifest(payload: dict[str, Any]) -> GameAdapterManifest:
    adapter_id = str(payload.get("id") or "").strip()
    if not adapter_id.startswith("game."):
        raise ValueError("game adapter id must use the game.* namespace")
    allowed_origins = _strings(payload.get("allowed_origins"))
    allowed_paths = _strings(payload.get("allowed_paths"))
    expected_scenes = _strings(payload.get("expected_scenes"))
    allowed_actions = _strings(payload.get("allowed_actions"))
    if not allowed_origins or not allowed_paths or not expected_scenes or not allowed_actions:
        raise ValueError("game adapter requires origins, paths, scenes, and actions")
    forbidden = {"raw_key", "raw_click", "memory_write", "packet_send", "captcha_solve", "purchase"}
    if forbidden.intersection(action.lower() for action in allowed_actions):
        raise ValueError("game adapter contains a forbidden action")
    max_rate = float(payload.get("max_actions_per_second") or 5.0)
    if max_rate <= 0 or max_rate > 10:
        raise ValueError("game adapter action rate must be between 0 and 10 per second")
    title_pattern = str(payload.get("title_pattern") or "").strip()
    if not title_pattern or len(title_pattern) > 160:
        raise ValueError("game adapter title pattern is required and must be short")
    re.compile(title_pattern)
    risk = str(payload.get("risk") or "high").strip().lower()
    if risk not in {"low", "medium", "high"}:
        raise ValueError("game adapter risk must be low, medium, or high")
    return GameAdapterManifest(
        adapter_id=adapter_id,
        label=str(payload.get("label") or adapter_id).strip(),
        allowed_origins=allowed_origins,
        allowed_paths=allowed_paths,
        title_pattern=title_pattern,
        expected_scenes=expected_scenes,
        allowed_actions=allowed_actions,
        max_actions_per_second=max_rate,
        requires_focus=bool(payload.get("requires_focus", True)),
        risk_level=risk,
        stop_conditions=_strings(payload.get("stop_conditions")) or GameAdapterManifest.__dataclass_fields__["stop_conditions"].default,
    )


def configured_game_adapter_allowlist() -> frozenset[str]:
    raw = os.getenv("SPIRITKIN_GAME_ADAPTER_ALLOWLIST") or ""
    return frozenset(item.strip() for item in re.split(r"[,;|]", raw) if item.strip())


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _origin_matches(origin: str, pattern: str) -> bool:
    escaped = re.escape(pattern.rstrip("/"))
    escaped = escaped.replace(r"\*", r"[0-9]{1,5}")
    return re.fullmatch(escaped, origin.rstrip("/"), flags=re.IGNORECASE) is not None
