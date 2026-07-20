from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AndroidPushNotification:
    title: str
    body: str
    priority: str = "normal"
    target_device_id: str = ""
    action_request: dict[str, Any] | None = None


class AndroidPushQueue:
    def __init__(self):
        self._queues: dict[str, list[AndroidPushNotification]] = {}

    def push(self, notification: AndroidPushNotification) -> None:
        device_id = notification.target_device_id or "default"
        self._queues.setdefault(device_id, []).append(notification)

    def drain(self, device_id: str) -> list[dict[str, Any]]:
        notifications = self._queues.pop(device_id, [])
        return [
            {
                "title": n.title,
                "body": n.body,
                "priority": n.priority,
                "action_request": n.action_request,
            }
            for n in notifications
        ]

    def pending_count(self, device_id: str) -> int:
        return len(self._queues.get(device_id, []))

    def clear(self) -> None:
        self._queues.clear()
