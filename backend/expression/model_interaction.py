from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MODEL_INTERACTION_SCHEMA_VERSION = "spiritkin.model_interaction.v1"
MODEL_INTERACTION_EVENT_TYPE = "model.interaction"


PHASE_TO_REACTION = {
    "silence_idle": ("neutral", "idle", False),
    "listening": ("listening", "listen", False),
    "attentive_wait": ("listening", "wait", False),
    "thinking": ("thinking", "think", False),
    "acting": ("thinking", "execute_task", False),
    "speaking": ("speaking", "speak", True),
    "interrupted": ("alert", "interrupted", False),
    "waiting_confirmation": ("waiting", "await_confirmation", False),
    "error": ("error", "shake", False),
}


ACTION_ALIASES = {
    "": "",
    "none": "",
    "idle": "",
    "listen": "",
    "speak": "",
    "wave_hand": "wave",
    "wave": "wave",
    "hello": "wave",
    "greet": "wave",
    "nod": "nod",
    "yes": "nod",
    "confirm": "nod",
    "await_confirmation": "nod",
    "execute_task": "nod",
    "execution_result": "nod",
    "plan_development": "nod",
    "write_plan": "nod",
    "queue_task": "nod",
    "tap_chin": "nod",
    "think": "nod",
    "thinking": "nod",
    "shake": "shake",
    "no": "shake",
    "deny": "shake",
    "negative": "shake",
    "cancel_execution": "shake",
    "error": "shake",
    "failed": "shake",
    "tilt_head": "shake",
    "scan_screen": "walk_forward",
    "walk": "walk_forward",
    "walk_forward": "walk_forward",
    "walk_back": "walk_back",
    "walk_backward": "walk_back",
    "walk_left": "walk_left",
    "walk_right": "walk_right",
    "glance_clock": "walk_right",
}


def normalize_motion_action(action: object) -> str:
    key = str(action or "").strip().lower().replace("-", "_")
    return ACTION_ALIASES.get(key, key if key in {"nod", "shake", "wave", "walk_forward", "walk_back", "walk_left", "walk_right"} else "")


def _scope_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    client_metadata = data.get("client_metadata") if isinstance(data.get("client_metadata"), dict) else {}
    return {
        "session_id": str(payload.get("session_id") or data.get("session_id") or metadata.get("session_id") or client_metadata.get("session_id") or ""),
        "request_id": str(payload.get("request_id") or data.get("request_id") or metadata.get("request_id") or client_metadata.get("request_id") or ""),
    }


@dataclass(frozen=True)
class ModelInteraction:
    """Stable model-interaction envelope for the current 3D avatar model.

    This protocol is intentionally model-agnostic. The current frontend maps it
    to the Bangboo GLB screen, procedural motions, audio meter, and subtitles.
    """

    source_event_type: str
    phase: str = ""
    emotion: str = "neutral"
    action: str = ""
    speaking: bool = False
    text: str = ""
    spoken_text: str = ""
    screen_text: str = ""
    subtitle_text: str = ""
    response_kind: str = ""
    agent_name: str = ""
    session_id: str = ""
    request_id: str = ""
    speech_id: str = ""
    audio_level: float | None = None
    mouth_shape: str = ""
    timestamp_ms: int = 0
    duration_ms: int = 0
    strength: float | None = None
    interruptible: bool = True
    input_channel: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": MODEL_INTERACTION_SCHEMA_VERSION,
            "source_event_type": self.source_event_type,
            "phase": self.phase,
            "emotion": self.emotion,
            "action": self.action,
            "speaking": self.speaking,
            "text": self.text,
            "spoken_text": self.spoken_text,
            "screen_text": self.screen_text,
            "subtitle_text": self.subtitle_text,
            "response_kind": self.response_kind,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "speech_id": self.speech_id,
            "interruptible": self.interruptible,
            "input_channel": self.input_channel,
            "capabilities": dict(self.capabilities or {}),
            "metadata": dict(self.metadata or {}),
            "timestamp": time.time(),
        }
        if self.audio_level is not None:
            payload["audio_level"] = self.audio_level
        if self.mouth_shape:
            payload["mouth_shape"] = self.mouth_shape
        if self.timestamp_ms:
            payload["timestamp_ms"] = self.timestamp_ms
        if self.duration_ms:
            payload["duration_ms"] = self.duration_ms
        if self.strength is not None:
            payload["strength"] = self.strength
        return payload

    def to_event(self) -> dict[str, Any]:
        return {
            "type": MODEL_INTERACTION_EVENT_TYPE,
            "schema_version": "v1",
            "payload": self.to_payload(),
        }


def build_response_interaction(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    response_kind = str(payload.get("response_kind") or data.get("response_kind") or "message")
    action = normalize_motion_action(payload.get("action") or ("execute_task" if response_kind == "execution_result" else ""))
    scope = _scope_from_payload(payload)
    spoken = str(payload.get("spoken_text") or "")
    text = str(payload.get("text") or "")
    reaction = payload.get("avatar_reaction") if isinstance(payload.get("avatar_reaction"), dict) else data.get("avatar_reaction")
    return ModelInteraction(
        source_event_type="assistant.message",
        phase="waiting_confirmation" if payload.get("requires_confirmation") else "speaking",
        emotion=str(payload.get("emotion") or "neutral"),
        action=action,
        speaking=bool(spoken),
        text=text,
        spoken_text=spoken,
        screen_text=str(payload.get("screen_text") or spoken or text),
        subtitle_text=spoken or text,
        response_kind=response_kind,
        agent_name=str(payload.get("agent_name") or ""),
        session_id=scope["session_id"],
        request_id=scope["request_id"],
        input_channel=_input_channel(data),
        capabilities={
            "avatar_model": "spiritkin_3d",
            "motion_actions": ["nod", "shake", "wave", "walk_forward", "walk_back", "walk_left", "walk_right"],
            "display_channels": ["screen", "subtitle", "audio_meter"],
            "protocol_origin": "backend.expression.model_interaction",
        },
        metadata={
            "legacy_response_kind": response_kind,
            "requires_confirmation": bool(payload.get("requires_confirmation")),
            "avatar_reaction": dict(reaction or {}),
        },
    ).to_event()


def _input_channel(data: dict[str, Any]) -> str:
    if data.get("input_channel"):
        return str(data.get("input_channel") or "")
    client_metadata = data.get("client_metadata")
    if isinstance(client_metadata, dict):
        return str(client_metadata.get("channel") or "")
    return ""


def build_performance_interaction(phase: str, message: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    emotion, action, speaking = PHASE_TO_REACTION.get(str(phase or ""), ("neutral", str(phase or ""), False))
    metadata = dict(metadata or {})
    return ModelInteraction(
        source_event_type="performance.state",
        phase=str(phase or ""),
        emotion=emotion,
        action=normalize_motion_action(action),
        speaking=speaking,
        text="",
        spoken_text="",
        screen_text="",
        subtitle_text="",
        session_id=str(metadata.get("session_id") or ""),
        request_id=str(metadata.get("request_id") or ""),
        metadata=metadata,
    ).to_event()


def build_speech_interaction(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == "speech.started":
        phase = "speaking"
        speaking = True
        audio_level = 0.72
    elif event_type == "speech.interrupted":
        phase = "interrupted"
        speaking = False
        audio_level = 0.0
    elif event_type == "speech.ended":
        phase = "attentive_wait" if not payload.get("interrupted") else "interrupted"
        speaking = False
        audio_level = 0.0
    else:
        phase = "speaking"
        speaking = True
        audio_level = 0.74

    scope = _scope_from_payload(payload)
    return ModelInteraction(
        source_event_type=event_type,
        phase=phase,
        emotion="speaking" if speaking else "neutral",
        action="",
        speaking=speaking,
        text=str(payload.get("text") or ""),
        spoken_text=str(payload.get("text") or ""),
        screen_text="",
        subtitle_text="",
        session_id=scope["session_id"],
        request_id=scope["request_id"],
        speech_id=str(payload.get("speech_id") or ""),
        audio_level=audio_level,
        mouth_shape=str(payload.get("mouth_shape") or payload.get("phoneme") or ""),
        timestamp_ms=int(payload.get("timestamp_ms") or 0),
        duration_ms=int(payload.get("duration_ms") or 0),
        metadata={"speech_source": payload.get("source", "")},
    ).to_event()
