from __future__ import annotations

import asyncio
import json
import os
import time

LIVE2D_WS_URL = os.getenv("SPIRITKIN_LIVE2D_WS_URL", "ws://localhost:8765")
EMOJIS = {
    "idle": "[idle]",
    "listening": "[listen]",
    "thinking": "[think]",
    "speaking": "[speak]",
    "happy": "[happy]",
    "done": "[done]",
    "error": "[ERROR]",
    "waiting": "[wait]",
    "alert": "[ALERT]",
    "confused": "[?]",
    "neutral": "[-]",
}


def show_emotion(emotion: str, message: str = ""):
    tag = EMOJIS.get(emotion, "[?]")
    print(f"{tag} {message or f'state: {emotion}'}")


async def send_to_live2d(data: dict):
    try:
        import websockets

        async with websockets.connect(LIVE2D_WS_URL) as ws:
            token = str(os.getenv("SPIRITKIN_DESKTOP_TOKEN") or os.getenv("SPIRITKIN_API_TOKEN") or os.getenv("SPIRITKIN_MOBILE_TOKEN") or "").strip()
            await ws.send(json.dumps({"type": "runtime.auth", "token": token}))
            await ws.send(json.dumps(data))
    except Exception:
        return


def trigger_emotion(
    emotion: str,
    speaking: bool = False,
    action: str = "idle",
    message: str = "",
    metadata: dict | None = None,
):
    show_emotion(emotion, f"Live2D: {emotion}, speaking={speaking}, action={action}")
    payload = {
        "type": "avatar.state",
        "schema_version": "v1",
        "emotion": emotion,
        "speaking": speaking,
        "action": action,
        "message": message,
        "metadata": dict(metadata or {}),
        "timestamp": time.time(),
    }

    try:
        asyncio.run(send_to_live2d(payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(send_to_live2d(payload))
