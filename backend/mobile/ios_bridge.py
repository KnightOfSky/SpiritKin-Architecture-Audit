from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from backend.executors.base import ExecutionRequest

IOS_ALLOWED_ACTIONS = {
    "ask_spirit": ("ios_device", "shortcut_query"),
    "read_clipboard": ("ios_device", "clipboard.read"),
    "write_clipboard": ("ios_device", "clipboard.write"),
    "capture_screen": ("ios_device", "screen.capture"),
    "send_notification": ("ios_device", "notification.send"),
    "check_battery": ("ios_device", "device.battery"),
}


@dataclass(frozen=True)
class iOSShortcutPayload:
    shortcut_name: str
    input_text: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    output_type: str = "json"
    device_name: str = ""
    ios_version: str = ""


@dataclass(frozen=True)
class iOSAppIntentPayload:
    intent_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_unlock: bool = True


@dataclass(frozen=True)
class iOSDeviceInfo:
    device_name: str
    ios_version: str = ""
    shortcut_count: int = 0
    app_intent_count: int = 0


class iOSCommandTranslator:

    @staticmethod
    def shortcut_to_execution_request(payload: iOSShortcutPayload) -> ExecutionRequest:
        action = str(payload.parameters.get("action") or "ask_spirit").strip() or "ask_spirit"
        target, operation = IOS_ALLOWED_ACTIONS.get(action, IOS_ALLOWED_ACTIONS["ask_spirit"])
        return ExecutionRequest(
            target=target,
            operation=operation,
            params={
                "text": payload.input_text,
                "shortcut_name": payload.shortcut_name,
                "parameters": payload.parameters,
                "device_name": payload.device_name,
            },
        )

    @staticmethod
    def app_intent_to_execution_request(payload: iOSAppIntentPayload) -> ExecutionRequest:
        action = str(payload.parameters.get("action") or payload.intent_name).strip()
        if action not in IOS_ALLOWED_ACTIONS:
            return ExecutionRequest("ios_device", "clarify", {"reason": "unsupported_ios_action", "intent_name": payload.intent_name})
        target, operation = IOS_ALLOWED_ACTIONS[action]
        return ExecutionRequest(target, operation, dict(payload.parameters))

    @staticmethod
    def reply_to_shortcut_output(reply: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": reply.get("text", ""),
            "emotion": reply.get("emotion", "neutral"),
            "success": reply.get("success", True),
        }


def generate_shortcut_schema(shortcut_name: str) -> dict[str, Any]:
    return {
        "name": shortcut_name,
        "input_fields": ["text"],
        "output_type": "json",
        "url": "/ios/shortcut",
        "method": "POST",
    }


def generate_app_intent_schema(intent_name: str) -> dict[str, Any]:
    return {
        "name": intent_name,
        "input_fields": ["parameters"],
        "output_type": "json",
        "url": "/ios/intent",
        "method": "POST",
        "allowed_actions": sorted(IOS_ALLOWED_ACTIONS.keys()),
    }


def build_shortcut_url_scheme(shortcut_name: str, base_url: str, token: str) -> str:
    query = urlencode({"shortcut_name": shortcut_name, "token_hint": "set-header" if token else "none"})
    return f"{base_url.rstrip('/')}/ios/shortcut?{query}"


def validate_ios_action(action: str) -> tuple[bool, str]:
    if action in IOS_ALLOWED_ACTIONS:
        return True, "allowed"
    return False, f"unsupported_ios_action: {action}"
