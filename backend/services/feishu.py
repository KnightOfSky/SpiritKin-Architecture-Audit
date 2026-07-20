from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    base_url: str = "https://open.feishu.cn"
    dry_run: bool = True
    default_receive_id_type: str = "user_id"
    contacts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeishuSendResult:
    dry_run: bool
    recipient: str
    receive_id: str
    receive_id_type: str
    text: str
    message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_contacts(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in data.items() if str(key).strip() and str(value).strip()}


def load_feishu_config(environ: Mapping[str, str] | None = None) -> FeishuConfig:
    env = os.environ if environ is None else environ
    app_id = str(env.get("SPIRIT_FEISHU_APP_ID", "")).strip()
    app_secret = str(env.get("SPIRIT_FEISHU_APP_SECRET", "")).strip()
    explicit_dry_run = env.get("SPIRIT_FEISHU_DRY_RUN")
    dry_run = _truthy(explicit_dry_run) if explicit_dry_run is not None else not (app_id and app_secret)
    return FeishuConfig(
        app_id=app_id,
        app_secret=app_secret,
        base_url=str(env.get("SPIRIT_FEISHU_BASE_URL", "https://open.feishu.cn")).rstrip("/"),
        dry_run=dry_run,
        default_receive_id_type=str(env.get("SPIRIT_FEISHU_RECEIVE_ID_TYPE", "user_id")).strip() or "user_id",
        contacts=_load_contacts(env.get("SPIRIT_FEISHU_CONTACTS_JSON")),
    )


class FeishuClient:
    def __init__(self, config: FeishuConfig | None = None):
        self.config = config or load_feishu_config()
        self._tenant_access_token: str | None = None

    def resolve_contact(self, recipient: str) -> tuple[str, str]:
        cleaned = recipient.strip().strip("“”\"'‘’")
        mapped = self.config.contacts.get(cleaned, cleaned)
        for prefix, receive_id_type in (("open_id:", "open_id"), ("user_id:", "user_id"), ("email:", "email")):
            if mapped.lower().startswith(prefix):
                return mapped[len(prefix) :], receive_id_type
        return mapped, self.config.default_receive_id_type

    def send_text_message(self, recipient: str, text: str) -> FeishuSendResult:
        message = text.strip()
        if not message:
            raise ValueError("飞书消息内容不能为空")
        receive_id, receive_id_type = self.resolve_contact(recipient)
        if not receive_id:
            raise ValueError("飞书接收人不能为空")

        if self.config.dry_run:
            return FeishuSendResult(True, recipient, receive_id, receive_id_type, message, message_id="dry-run")

        token = self._get_tenant_access_token()
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": message}, ensure_ascii=False),
        }
        response = self._post_json(
            f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if int(response.get("code", -1)) != 0:
            raise RuntimeError(f"飞书发送失败: {response.get('msg') or response}")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        return FeishuSendResult(False, recipient, receive_id, receive_id_type, message, message_id=str(data.get("message_id", "")), raw=response)

    def _get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        if not self.config.app_id or not self.config.app_secret:
            raise RuntimeError("缺少飞书应用凭据，请配置 SPIRIT_FEISHU_APP_ID / SPIRIT_FEISHU_APP_SECRET")
        response = self._post_json(
            "/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.config.app_id, "app_secret": self.config.app_secret},
        )
        if int(response.get("code", -1)) != 0:
            raise RuntimeError(f"获取飞书 token 失败: {response.get('msg') or response}")
        self._tenant_access_token = str(response.get("tenant_access_token", ""))
        return self._tenant_access_token

    def _post_json(self, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8", **dict(headers or {})},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (OSError, TimeoutError) as exc:
                last_error = exc
                if attempt:
                    raise
        raise RuntimeError(f"飞书请求失败: {last_error}")
