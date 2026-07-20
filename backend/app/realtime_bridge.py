from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

from backend.app.runtime import EVENT_SCHEMA_VERSION
from backend.app.runtime_state import build_aggregated_runtime_state_snapshot
from backend.runtime import RuntimeBusEvent, RuntimeEventBus

DEFAULT_EVENTS_HOST = os.getenv("SPIRITKIN_EVENTS_BIND_HOST") or os.getenv("SPIRITKIN_EVENTS_HOST", "127.0.0.1")
DEFAULT_EVENTS_PORT = int(os.getenv("SPIRITKIN_EVENTS_PORT", "8765"))


class _HandshakeNoiseFilter(logging.Filter):
    """Drop malformed-probe tracebacks without hiding bridge failures."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "opening handshake failed"


BRIDGE_LOGGER = logging.getLogger("spiritkin.realtime_bridge")
BRIDGE_LOGGER.addFilter(_HandshakeNoiseFilter())


def bridge_auth_enabled() -> bool:
    raw = str(os.getenv("SPIRITKIN_BRIDGE_AUTH", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def expected_bridge_token() -> str:
    return str(os.getenv("SPIRITKIN_DESKTOP_TOKEN") or os.getenv("SPIRITKIN_API_TOKEN") or os.getenv("SPIRITKIN_MOBILE_TOKEN") or "").strip()


def bridge_auth_token(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    token = payload.get("token") or payload.get("access_token")
    auth = str(payload.get("authorization") or payload.get("Authorization") or "").strip()
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    return str(token or "").strip()


def is_bridge_auth_message(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("type") or "").strip().lower() in {"auth", "runtime.auth", "spiritkin.auth"}


def bridge_auth_allowed(payload: dict[str, Any] | None) -> bool:
    if not bridge_auth_enabled():
        return True
    expected = expected_bridge_token()
    if not expected:
        return False
    return is_bridge_auth_message(payload) and bridge_auth_token(payload) == expected


def bridge_local_browser_allowed(websocket: Any) -> bool:
    remote = getattr(websocket, "remote_address", None)
    remote_host = str(remote[0] if isinstance(remote, tuple) and remote else "").strip()
    try:
        if not ipaddress.ip_address(remote_host).is_loopback:
            return False
    except ValueError:
        return False

    request = getattr(websocket, "request", None)
    headers = getattr(request, "headers", None) or getattr(websocket, "request_headers", None)
    origin = str(headers.get("Origin") if headers is not None else "").strip()
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


class RealtimeEventHub:
    """最小事件桥：接收 runtime/avatar 发送的事件，并广播给前端订阅者。"""

    def __init__(self, history_limit: int = 60, *, event_bus: RuntimeEventBus | None = None):
        self.history_limit = max(1, int(history_limit))
        self._recent_events: list[dict[str, object]] = []
        self._subscribers: set[object] = set()
        # Latest authoritative avatar state per session_id. The event *stream* is
        # ephemeral (a late-joining client only replays the last N events), so a
        # client that connects mid-conversation cannot otherwise know the current
        # expression/action another end is showing. We keep the newest avatar.state
        # per session and hand it to new connections in the snapshot for cold-start
        # alignment. Scoped by session so one session never drags another's avatar.
        self._avatar_state_by_session: dict[str, dict[str, object]] = {}
        self._event_bus = event_bus or RuntimeEventBus()

    @property
    def recent_events(self) -> list[dict[str, object]]:
        return list(self._recent_events)

    @property
    def avatar_states(self) -> dict[str, dict[str, object]]:
        return {key: dict(value) for key, value in self._avatar_state_by_session.items()}

    @staticmethod
    def decode_message(message: str) -> dict[str, object] | None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and payload.get("type"):
            return payload
        return None

    @staticmethod
    def _clone_event(event: dict[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(event, ensure_ascii=False, default=str))

    def record_event(self, event: dict[str, object]) -> dict[str, object]:
        stored = self._clone_event(event)
        self._recent_events.append(stored)
        self._recent_events = self._recent_events[-self.history_limit :]
        self._remember_avatar_state(stored)
        return stored

    def _remember_avatar_state(self, event: dict[str, object]) -> None:
        if event.get("type") != "avatar.state":
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        session_id = str(payload.get("session_id") or "").strip()
        snapshot = {
            "emotion": payload.get("emotion"),
            "action": payload.get("action"),
            "speaking": bool(payload.get("speaking")),
            "response_kind": payload.get("response_kind"),
            "performance_phase": payload.get("performance_phase"),
            "session_id": session_id,
            "request_id": str(payload.get("request_id") or ""),
        }
        self._avatar_state_by_session[session_id] = snapshot

    def build_snapshot_event(self) -> dict[str, object]:
        events = self.recent_events
        return {
            "type": "runtime.snapshot",
            "schema_version": EVENT_SCHEMA_VERSION,
            "payload": {
                "events": events,
                "aggregated_state": build_aggregated_runtime_state_snapshot(events),
                "avatar_states": self.avatar_states,
            },
        }

    @staticmethod
    async def _send_json(websocket, payload: dict[str, object]) -> None:
        await websocket.send(json.dumps(payload, ensure_ascii=False, default=str))

    async def publish(self, event: dict[str, object], *, exclude=None) -> dict[str, object]:
        stored = self.record_event(event)
        payload = stored.get("payload") if isinstance(stored.get("payload"), dict) else {}
        self._event_bus.publish(
            RuntimeBusEvent(
                topic=str(stored.get("type") or "runtime.unknown"),
                payload=dict(payload),
                source=str(stored.get("source") or "realtime_bridge"),
                workspace_id=str(payload.get("workspace_id") or ""),
                correlation_id=str(payload.get("request_id") or payload.get("correlation_id") or ""),
            )
        )
        stale_connections = []
        for websocket in tuple(self._subscribers):
            if websocket is exclude:
                continue
            try:
                # 广播是串行循环：单个不消费消息的慢客户端会把整个循环卡死，
                # 所有其他客户端表现为"实时断连"。发送限时，超时按失联剔除。
                await asyncio.wait_for(self._send_json(websocket, stored), timeout=2.0)
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self._subscribers.discard(websocket)
            try:
                await asyncio.wait_for(websocket.close(), timeout=1.0)
            except Exception:
                pass
        return stored

    async def handle_connection(self, websocket, *_) -> None:
        if bridge_auth_enabled():
            try:
                first_message = await asyncio.wait_for(websocket.recv(), timeout=3.0)
            except TimeoutError:
                await websocket.close(code=1008, reason="bridge auth required")
                return
            auth_event = self.decode_message(first_message)
            if not bridge_auth_allowed(auth_event) and not bridge_local_browser_allowed(websocket):
                await websocket.close(code=1008, reason="bridge auth required")
                return
        self._subscribers.add(websocket)
        await self._send_json(websocket, self.build_snapshot_event())
        try:
            async for message in websocket:
                event = self.decode_message(message)
                if event is None or event.get("type") in {"runtime.snapshot", "runtime.subscribe"} or is_bridge_auth_message(event):
                    continue
                await self.publish(event, exclude=websocket)
        finally:
            self._subscribers.discard(websocket)


async def serve_event_bridge(
    host: str = DEFAULT_EVENTS_HOST,
    port: int = DEFAULT_EVENTS_PORT,
    *,
    history_limit: int = 60,
) -> None:
    try:
        import websockets
    except Exception as exc:
        raise RuntimeError("实时事件桥需要安装 websockets>=12.0") from exc

    hub = RealtimeEventHub(history_limit=history_limit)
    async with websockets.serve(hub.handle_connection, host, port, logger=BRIDGE_LOGGER):
        print(f"[bridge] Realtime event bridge started at ws://{host}:{port}")
        await asyncio.Future()


def main() -> None:
    asyncio.run(serve_event_bridge())


if __name__ == "__main__":
    main()
