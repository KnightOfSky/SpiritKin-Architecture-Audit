"""跨端实时契约的单一事实来源：事件类型名 + 服务默认端口。

前端 `frontend/js/realtime_contract.js` 与桌面端
`desktop/SpiritKinDesktop/Features/Runtime/RealtimeContract.g.cs`
均由本模块生成，改动后运行 `python scripts/generate_realtime_contract.py`
重新生成；单测会在生成物与本模块漂移时失败。
"""

from __future__ import annotations

from backend.app.service_ports import PORT_SPECS

CONTRACT_SCHEMA_VERSION = "spiritkin.realtime_contract.v1"

ASSISTANT_MESSAGE = "assistant.message"
ASSISTANT_DELTA = "assistant.delta"
ASSISTANT_CONFIRMATION_REQUESTED = "assistant.confirmation_requested"
ASSISTANT_EXECUTION_UPDATED = "assistant.execution_updated"
ASSISTANT_TASK_UPDATED = "assistant.task_updated"
ASSISTANT_PROJECT_UPDATED = "assistant.project_updated"
ASSISTANT_WORK_UPDATED = "assistant.work_updated"
RUNTIME_SNAPSHOT = "runtime.snapshot"
RUNTIME_SUBSCRIBE = "runtime.subscribe"
RUNTIME_CAPABILITIES = "runtime.capabilities"
RUNTIME_AGGREGATED_STATE = "runtime.aggregated_state"
DESKTOP_STATE_UPDATED = "desktop.state_updated"
DESKTOP_COLLABORATION_UPDATED = "desktop.collaboration_updated"
AVATAR_STATE = "avatar.state"
AVATAR_MOTION = "avatar.motion"
AVATAR_ACTION = "avatar.action"
COLLABORATION_MESSAGE = "collaboration.message"
MEMORY_UPDATED = "memory.updated"
MODEL_INTERACTION = "model.interaction"
PERSONALITY_UPDATED = "personality.updated"
PRESENCE_UPDATED = "presence.updated"
RELATIONSHIP_UPDATED = "relationship.updated"
PROACTIVE_SUGGESTED = "proactive.suggested"
PROACTIVE_SUPPRESSED = "proactive.suppressed"
PROACTIVE_FEEDBACK = "proactive.feedback"
OPENING_BUBBLE_PRESENT = "opening_bubble.present"
SCHEDULER_INTENT_DUE = "scheduler.intent_due"
SCHEDULER_INTENT_SUPPRESSED = "scheduler.intent_suppressed"
VOICE_CALL_STATE = "voice.call.state"
VOICE_CALL_TRANSCRIPT = "voice.call.transcript"
ASR_SPEECH_STARTED = "asr.speech_started"
ASR_PARTIAL = "asr.partial"
ASR_FINAL = "asr.final"
SPEECH_STARTED = "speech.started"
SPEECH_ENDED = "speech.ended"
SPEECH_INTERRUPTED = "speech.interrupted"
SPEECH_PHONEME = "speech.phoneme"
SPEECH_VISEME = "speech.viseme"
DEVICE_OPENCLAW_STATE_UPDATED = "device.openclaw_state_updated"

SHARED_EVENT_TYPES: tuple[str, ...] = (
    ASSISTANT_MESSAGE,
    ASSISTANT_DELTA,
    ASSISTANT_CONFIRMATION_REQUESTED,
    ASSISTANT_EXECUTION_UPDATED,
    ASSISTANT_TASK_UPDATED,
    ASSISTANT_PROJECT_UPDATED,
    ASSISTANT_WORK_UPDATED,
    RUNTIME_SNAPSHOT,
    RUNTIME_SUBSCRIBE,
    RUNTIME_CAPABILITIES,
    RUNTIME_AGGREGATED_STATE,
    DESKTOP_STATE_UPDATED,
    DESKTOP_COLLABORATION_UPDATED,
    AVATAR_STATE,
    AVATAR_MOTION,
    AVATAR_ACTION,
    COLLABORATION_MESSAGE,
    MEMORY_UPDATED,
    MODEL_INTERACTION,
    PERSONALITY_UPDATED,
    PRESENCE_UPDATED,
    RELATIONSHIP_UPDATED,
    PROACTIVE_SUGGESTED,
    PROACTIVE_SUPPRESSED,
    PROACTIVE_FEEDBACK,
    OPENING_BUBBLE_PRESENT,
    SCHEDULER_INTENT_DUE,
    SCHEDULER_INTENT_SUPPRESSED,
    VOICE_CALL_STATE,
    VOICE_CALL_TRANSCRIPT,
    ASR_SPEECH_STARTED,
    ASR_PARTIAL,
    ASR_FINAL,
    SPEECH_STARTED,
    SPEECH_ENDED,
    SPEECH_INTERRUPTED,
    SPEECH_PHONEME,
    SPEECH_VISEME,
    DEVICE_OPENCLAW_STATE_UPDATED,
)

FRONTEND_CONTRACT_PATH = "frontend/js/realtime_contract.js"
DESKTOP_CONTRACT_PATH = "desktop/SpiritKinDesktop/Features/Runtime/RealtimeContract.g.cs"

_GENERATED_NOTICE = "GENERATED FILE - do not edit by hand. Source: backend/app/realtime_contract.py; regenerate with scripts/generate_realtime_contract.py."


def _words(event_type: str) -> list[str]:
    return [part for chunk in event_type.split(".") for part in chunk.split("_") if part]


def _pascal(event_type: str) -> str:
    return "".join(word.capitalize() for word in _words(event_type))


def _camel(event_type: str) -> str:
    pascal = _pascal(event_type)
    return pascal[:1].lower() + pascal[1:]


def default_ports() -> dict[str, int]:
    return {spec.service_id: spec.default_port for spec in PORT_SPECS}


def port_env_vars() -> dict[str, str]:
    return {spec.service_id: spec.env_var for spec in PORT_SPECS}


def render_frontend_contract() -> str:
    lines = [
        f"// {_GENERATED_NOTICE}",
        "(function (root) {",
        '  "use strict";',
        "  const contract = Object.freeze({",
        f'    schema: "{CONTRACT_SCHEMA_VERSION}",',
        "    events: Object.freeze({",
    ]
    lines.extend(f'      {_camel(event)}: "{event}",' for event in SHARED_EVENT_TYPES)
    lines.append("    }),")
    lines.append("    knownEventTypes: Object.freeze([")
    lines.extend(f'      "{event}",' for event in SHARED_EVENT_TYPES)
    lines.append("    ]),")
    lines.append("    defaultPorts: Object.freeze({")
    lines.extend(f"      {service_id}: {port}," for service_id, port in default_ports().items())
    lines.append("    }),")
    lines.append("    portEnvVars: Object.freeze({")
    lines.extend(f'      {service_id}: "{env_var}",' for service_id, env_var in port_env_vars().items())
    lines.append("    }),")
    lines.append("  });")
    lines.append('  root.SPIRITKIN_CONTRACT = contract;')
    lines.append('})(typeof window !== "undefined" ? window : globalThis);')
    return "\n".join(lines) + "\n"


def render_desktop_contract() -> str:
    lines = [
        "// <auto-generated>",
        f"// {_GENERATED_NOTICE}",
        "// </auto-generated>",
        "",
        "namespace SpiritKinDesktop;",
        "",
        "internal static class RealtimeContract",
        "{",
        f'    public const string SchemaVersion = "{CONTRACT_SCHEMA_VERSION}";',
        "",
        "    internal static class Events",
        "    {",
    ]
    lines.extend(f'        public const string {_pascal(event)} = "{event}";' for event in SHARED_EVENT_TYPES)
    lines.append("    }")
    lines.append("")
    lines.append("    internal static class DefaultPorts")
    lines.append("    {")
    lines.extend(f"        public const int {_pascal(service_id)} = {port};" for service_id, port in default_ports().items())
    lines.append("    }")
    lines.append("")
    lines.append("    internal static class PortEnvVars")
    lines.append("    {")
    lines.extend(f'        public const string {_pascal(service_id)} = "{env_var}";' for service_id, env_var in port_env_vars().items())
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"
