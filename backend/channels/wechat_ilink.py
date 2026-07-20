from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_LONG_POLL_TIMEOUT = 35.0
DEFAULT_REQUEST_TIMEOUT = 45.0
CHANNEL_VERSION = "2.0.0"


class ILinkError(RuntimeError):
    """Base error for iLink protocol failures."""


class ILinkAuthError(ILinkError):
    """The token is missing, rejected, or no longer valid."""


class ILinkSessionExpired(ILinkError):
    """The iLink cursor/session expired and requires re-authentication."""


@dataclass(frozen=True)
class ILinkConfig:
    enabled: bool = False
    bot_token: str = ""
    bot_id: str = ""
    user_id: str = ""
    base_url: str = DEFAULT_BASE_URL
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    max_retry_delay: float = 20.0
    credentials_path: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ILinkConfig:
        env = os.environ if environ is None else environ
        credential_path = str(env.get("SPIRITKIN_WECHAT_ILINK_CREDENTIALS") or "").strip()
        stored: dict[str, Any] = {}
        if credential_path:
            try:
                raw = Path(credential_path).expanduser().read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    stored = parsed
            except (OSError, json.JSONDecodeError):
                stored = {}

        def value(name: str, *aliases: str, default: str = "") -> str:
            for key in (name, *aliases):
                candidate = str(env.get(key) or "").strip()
                if candidate:
                    return candidate
            for key in (name, *aliases):
                candidate = str(stored.get(key) or "").strip()
                if candidate:
                    return candidate
            return default

        def number(name: str, default: float) -> float:
            try:
                return max(1.0, float(env.get(name) or default))
            except (TypeError, ValueError):
                return default

        enabled = str(env.get("SPIRITKIN_WECHAT_ILINK_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            enabled=enabled,
            bot_token=value("SPIRITKIN_WECHAT_ILINK_BOT_TOKEN", "bot_token", "botToken"),
            bot_id=value("SPIRITKIN_WECHAT_ILINK_BOT_ID", "ilink_bot_id", "ilinkBotId"),
            user_id=value("SPIRITKIN_WECHAT_ILINK_USER_ID", "ilink_user_id", "ilinkUserId"),
            base_url=value("SPIRITKIN_WECHAT_ILINK_BASE_URL", "base_url", "baseUrl", default=DEFAULT_BASE_URL).rstrip("/"),
            long_poll_timeout=min(60.0, number("SPIRITKIN_WECHAT_ILINK_LONG_POLL_SECONDS", DEFAULT_LONG_POLL_TIMEOUT)),
            request_timeout=min(90.0, number("SPIRITKIN_WECHAT_ILINK_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT)),
            max_retry_delay=min(120.0, number("SPIRITKIN_WECHAT_ILINK_MAX_RETRY_DELAY_SECONDS", 20.0)),
            credentials_path=Path(credential_path).expanduser() if credential_path else None,
        )

    def validate(self) -> str | None:
        if not self.enabled:
            return "微信 iLink 未启用。"
        if not self.bot_token:
            return "缺少微信 iLink Bot Token。"
        if not self.bot_id:
            return "缺少微信 iLink Bot ID。"
        if not self.base_url.startswith(("http://", "https://")):
            return "微信 iLink Base URL 必须是 HTTP(S) 地址。"
        return None


@dataclass(frozen=True)
class ILinkIncomingMessage:
    message_id: str
    from_user_id: str
    to_user_id: str
    text: str
    context_token: str
    create_time_ms: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)


Transport = Callable[[str, str, dict[str, str], dict[str, Any], float], Mapping[str, Any]]


def _default_transport(method: str, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> Mapping[str, Any]:
    response = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
    if response.status_code in {401, 403}:
        raise ILinkAuthError(f"iLink HTTP {response.status_code}: token rejected")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, Mapping):
        raise ILinkError("iLink returned a non-object JSON response")
    return data


class ILinkProtocolClient:
    """Small, dependency-light client for the real iLink Bot HTTP protocol."""

    def __init__(
        self,
        config: ILinkConfig,
        *,
        transport: Transport | None = None,
        uin_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._uin_factory = uin_factory or random_wechat_uin

    def get_updates(self, sync_buf: str = "") -> tuple[list[ILinkIncomingMessage], str]:
        data = self._request(
            "/ilink/bot/getupdates",
            {"get_updates_buf": sync_buf, "base_info": {"channel_version": CHANNEL_VERSION}},
            timeout=max(self.config.request_timeout, self.config.long_poll_timeout + 5.0),
        )
        self._raise_protocol_error(data, operation="getupdates")
        messages: list[ILinkIncomingMessage] = []
        raw_messages = data.get("msgs") or data.get("messages") or []
        if isinstance(raw_messages, list):
            for raw in raw_messages:
                if not isinstance(raw, Mapping):
                    continue
                if int(raw.get("message_type") or 1) != 1:
                    continue
                message = self._parse_message(raw)
                if message.text or message.raw.get("item_list"):
                    messages.append(message)
        next_buf = str(data.get("get_updates_buf") or data.get("sync_buf") or sync_buf)
        return messages, next_buf

    def send_text(self, to_user_id: str, text: str, context_token: str) -> Mapping[str, Any]:
        if not str(to_user_id or "").strip():
            raise ValueError("iLink recipient is required")
        if not str(text or "").strip():
            return {"ret": 0, "skipped": True}
        data = self._request(
            "/ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": str(to_user_id),
                    "client_id": str(uuid.uuid4()),
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": str(context_token or ""),
                    "item_list": [{"type": 1, "text_item": {"text": str(text)}}],
                },
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
        )
        self._raise_protocol_error(data, operation="sendmessage")
        return data

    def _request(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> Mapping[str, Any]:
        return self._transport(
            "POST",
            f"{self.config.base_url}{path}",
            {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {self.config.bot_token}",
                "X-WECHAT-UIN": self._uin_factory(),
            },
            payload,
            float(timeout or self.config.request_timeout),
        )

    @staticmethod
    def _raise_protocol_error(data: Mapping[str, Any], *, operation: str) -> None:
        ret = data.get("ret")
        if ret == -14:
            raise ILinkSessionExpired(f"iLink {operation} session expired")
        if ret in {401, 403, -401, -403}:
            raise ILinkAuthError(f"iLink {operation} token rejected: ret={ret}")
        if ret not in (None, 0):
            raise ILinkError(f"iLink {operation} failed: ret={ret} {data.get('errmsg') or data.get('error') or ''}".strip())

    @staticmethod
    def _parse_message(raw: Mapping[str, Any]) -> ILinkIncomingMessage:
        items = raw.get("item_list") or raw.get("items") or []
        texts: list[str] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                text_item = item.get("text_item")
                if isinstance(text_item, Mapping) and text_item.get("text"):
                    texts.append(str(text_item.get("text")))
        fallback = raw.get("text") or raw.get("content") or ""
        return ILinkIncomingMessage(
            message_id=str(raw.get("message_id") or raw.get("msg_id") or ""),
            from_user_id=str(raw.get("from_user_id") or raw.get("sender_id") or ""),
            to_user_id=str(raw.get("to_user_id") or ""),
            text="".join(texts) or str(fallback),
            context_token=str(raw.get("context_token") or ""),
            create_time_ms=int(raw.get("create_time_ms") or 0),
            raw=dict(raw),
        )


@dataclass
class ChannelStatus:
    enabled: bool = False
    phase: str = "offline"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "phase": self.phase,
            "message": self.message,
            "detail": dict(self.detail),
        }


class WeChatILinkChannel:
    """Bidirectional iLink channel with a stoppable long-poll worker."""

    def __init__(
        self,
        config: ILinkConfig,
        *,
        client: ILinkProtocolClient | None = None,
        on_message: Callable[[ILinkIncomingMessage], Any] | None = None,
        status_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.client = client or ILinkProtocolClient(config)
        self.on_message = on_message
        self.status_sink = status_sink
        self.status = ChannelStatus(enabled=config.enabled)
        self.sync_buf = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_context_tokens: dict[str, str] = {}

    def start(self) -> bool:
        error = self.config.validate()
        if error:
            self._set_status("config_missing", error)
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._set_status("starting", "正在连接微信 iLink…")
        self._thread = threading.Thread(target=self._run, name="spiritkin-wechat-ilink", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.1, timeout))
        self._thread = None
        self._set_status("offline", "微信 iLink 已停止")

    def run_once(self) -> int:
        messages, next_buf = self.client.get_updates(self.sync_buf)
        self.sync_buf = next_buf
        delivered = 0
        for message in messages:
            if not message.from_user_id or not message.text.strip():
                continue
            self._last_context_tokens[message.from_user_id] = message.context_token
            delivered += 1
            reply = self.on_message(message) if self.on_message is not None else None
            text = _reply_text(reply)
            if text:
                self.client.send_text(message.from_user_id, text, message.context_token)
        return delivered

    def send_text(self, to_user_id: str, text: str, *, context_token: str = "") -> Mapping[str, Any]:
        token = context_token or self._last_context_tokens.get(str(to_user_id), "")
        return self.client.send_text(str(to_user_id), text, token)

    def _run(self) -> None:
        retry_delay = 1.0
        self._set_status("running", "微信 iLink 已连接", detail={"bot_id": self.config.bot_id})
        while not self._stop_event.is_set():
            try:
                self.run_once()
                retry_delay = 1.0
            except ILinkSessionExpired as exc:
                self._set_status("error", f"微信 iLink 会话已过期：{exc}")
                return
            except ILinkAuthError as exc:
                self._set_status("error", f"微信 iLink 认证失败：{exc}")
                return
            except Exception as exc:  # network failures must not kill the desktop host
                self._set_status("error", f"微信 iLink 暂时不可用：{exc}")
                self._stop_event.wait(min(retry_delay, self.config.max_retry_delay))
                retry_delay = min(self.config.max_retry_delay, retry_delay * 2.0)
        self._set_status("offline", "微信 iLink 已停止")

    def _set_status(self, phase: str, message: str, *, detail: Mapping[str, Any] | None = None) -> None:
        self.status = ChannelStatus(
            enabled=self.config.enabled,
            phase=phase,
            message=message,
            detail=dict(detail or self.status.detail),
        )
        if self.status_sink is not None:
            try:
                self.status_sink(self.status.snapshot())
            except Exception:
                pass


def build_ilink_channel_from_env(*, on_message: Callable[[ILinkIncomingMessage], Any] | None = None) -> WeChatILinkChannel:
    config = ILinkConfig.from_env()
    return WeChatILinkChannel(config, on_message=on_message)


def random_wechat_uin() -> str:
    value = str(secrets.randbits(32)).encode("ascii")
    return base64.b64encode(value).decode("ascii")


def _reply_text(reply: Any) -> str:
    if reply is None:
        return ""
    if isinstance(reply, str):
        return reply.strip()
    if isinstance(reply, Mapping):
        return str(reply.get("spoken_text") or reply.get("text") or "").strip()
    return str(getattr(reply, "spoken_text", "") or getattr(reply, "text", "") or "").strip()
