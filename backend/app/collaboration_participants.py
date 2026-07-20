from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from backend.app.agent_management import load_agent_management_state
from backend.app.learning_workflow import discover_model_providers, load_assist_models
from backend.orchestrator.worker_pool import planned_worker_seed_descriptors

COLLABORATION_PARTICIPANT_SCHEMA_VERSION = "spiritkin.collaboration_participant.v1"

# Snapshot builds resolve dozens of agent ids, each rebuilding the registry from
# config/state files; a short TTL keeps the gateway responsive under polling.
_REGISTRY_CACHE_TTL_SECONDS = 2.0
_registry_cache: tuple[float, dict[str, Any]] | None = None


@dataclass(frozen=True)
class CollaborationParticipant:
    participant_id: str
    label: str
    kind: str
    status: str
    capabilities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    can_chat: bool = False
    can_execute: bool = False
    requires_review: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": COLLABORATION_PARTICIPANT_SCHEMA_VERSION,
            "participant_id": self.participant_id,
            "label": self.label,
            "kind": self.kind,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "aliases": list(self.aliases),
            "mention": f"@{self.aliases[0] if self.aliases else self.participant_id}",
            "can_chat": self.can_chat,
            "can_execute": self.can_execute,
            "requires_review": self.requires_review,
            "metadata": dict(self.metadata or {}),
        }


def build_collaboration_participant_registry() -> dict[str, Any]:
    global _registry_cache
    now = time.monotonic()
    if _registry_cache is not None and now - _registry_cache[0] < _REGISTRY_CACHE_TTL_SECONDS:
        return _registry_cache[1]
    registry = _build_collaboration_participant_registry_uncached()
    _registry_cache = (now, registry)
    return registry


def _build_collaboration_participant_registry_uncached() -> dict[str, Any]:
    participants: dict[str, CollaborationParticipant] = {}
    alias_owner: dict[str, str] = {}

    def add(participant: CollaborationParticipant) -> None:
        normalized_id = normalize_participant_id(participant.participant_id)
        if not normalized_id:
            return
        aliases = _unique_aliases((participant.participant_id, participant.label, *participant.aliases))
        item = CollaborationParticipant(
            participant_id=normalized_id,
            label=participant.label or normalized_id,
            kind=participant.kind,
            status=participant.status,
            capabilities=tuple(dict.fromkeys(str(value) for value in participant.capabilities if str(value).strip())),
            aliases=aliases,
            can_chat=participant.can_chat,
            can_execute=participant.can_execute,
            requires_review=participant.requires_review,
            metadata=dict(participant.metadata or {}),
        )
        participants[normalized_id] = item
        for alias in aliases:
            alias_key = normalize_participant_alias(alias)
            if alias_key and alias_key not in alias_owner:
                alias_owner[alias_key] = normalized_id

    state = load_agent_management_state()
    for agent in state.agents:
        if not bool(getattr(agent, "enabled", False)):
            continue
        agent_id = str(getattr(agent, "agent_id", "") or "")
        add(
            CollaborationParticipant(
                participant_id=agent_id,
                label=str(getattr(agent, "label", "") or agent_id),
                kind="local_agent",
                status="ready",
                capabilities=tuple(getattr(agent, "capabilities", ()) or ()),
                aliases=_agent_aliases(agent_id, str(getattr(agent, "label", "") or "")),
                can_chat=True,
                can_execute=False,
                requires_review=False,
                metadata={
                    "domain": str(getattr(agent, "domain", "") or ""),
                    "adapter": str(getattr(agent, "adapter", "") or ""),
                    "provider": str(getattr(agent, "provider", "") or ""),
                    "model": str(getattr(agent, "model", "") or ""),
                    "allowed_assistant_ids": list(getattr(agent, "allowed_assistant_ids", ()) or ()),
                },
            )
        )

    for assistant in state.external_assistants:
        assistant_id = str(getattr(assistant, "assistant_id", "") or "")
        command = str(getattr(assistant, "command", "") or "")
        kind = "model_api" if str(getattr(assistant, "kind", "") or "").lower() == "api" else "external_cli"
        configured = bool(command) if kind == "external_cli" else bool(getattr(assistant, "enabled", False))
        add(
            CollaborationParticipant(
                participant_id=_assistant_participant_id(assistant_id),
                label=str(getattr(assistant, "label", "") or assistant_id),
                kind=kind,
                status="ready" if bool(getattr(assistant, "enabled", False)) and configured else "not_configured",
                capabilities=("dialogue", "code_review") if kind == "external_cli" else ("dialogue", "review"),
                aliases=_assistant_aliases(assistant_id, str(getattr(assistant, "label", "") or "")),
                can_chat=bool(getattr(assistant, "enabled", False)),
                can_execute=False,
                requires_review=bool(getattr(assistant, "review_only", True)),
                metadata={
                    "assistant_id": assistant_id,
                    "command": command,
                    "source": "external_assistants",
                },
            )
        )

    for model in load_assist_models():
        model_id = str(getattr(model, "model_id", "") or "")
        add(
            CollaborationParticipant(
                participant_id=f"model_{model_id}",
                label=str(getattr(model, "display_name", "") or model_id),
                kind="model_api",
                status="ready" if bool(getattr(model, "configured", False)) else "not_configured",
                capabilities=("dialogue", "review", str(getattr(model, "role", "") or "model")),
                aliases=_model_aliases(
                    model_id,
                    str(getattr(model, "display_name", "") or ""),
                    str(getattr(model, "provider", "") or ""),
                    str(getattr(model, "model", "") or ""),
                ),
                can_chat=bool(getattr(model, "enabled", False)) and bool(getattr(model, "configured", False)),
                can_execute=False,
                requires_review=True,
                metadata={
                    "model_id": model_id,
                    "provider": str(getattr(model, "provider", "") or ""),
                    "model": str(getattr(model, "model", "") or ""),
                    "source": "assist_models",
                },
            )
        )

    for provider in discover_model_providers():
        provider_id = str(getattr(provider, "provider", "") or "")
        add(
            CollaborationParticipant(
                participant_id=f"provider_{provider_id}",
                label=str(getattr(provider, "display_name", "") or provider_id),
                kind="model_api",
                status="ready" if bool(getattr(provider, "configured", False)) else "not_configured",
                capabilities=("dialogue", "review"),
                aliases=_provider_aliases(
                    provider_id,
                    str(getattr(provider, "display_name", "") or ""),
                    str(getattr(provider, "model", "") or ""),
                ),
                can_chat=bool(getattr(provider, "configured", False)),
                can_execute=False,
                requires_review=True,
                metadata={
                    "provider": provider_id,
                    "model": str(getattr(provider, "model", "") or ""),
                    "endpoint": str(getattr(provider, "endpoint", "") or ""),
                    "source": str(getattr(provider, "source", "") or "providers"),
                },
            )
        )

    for worker in planned_worker_seed_descriptors():
        add(
            CollaborationParticipant(
                participant_id=str(worker.worker_id),
                label=str(worker.label or worker.worker_id),
                kind="worker",
                status=str(worker.health_status or "planned"),
                capabilities=tuple(worker.capabilities or worker.capability_namespaces or ()),
                aliases=_worker_aliases(str(worker.worker_id), str(worker.label or "")),
                can_chat=bool((worker.metadata or {}).get("can_chat")),
                can_execute=str(worker.health_status or "").lower() in {"ready", "online"},
                requires_review=True,
                metadata=worker.snapshot(),
            )
        )

    snapshots = [item.snapshot() for item in sorted(participants.values(), key=lambda item: (item.kind, item.label.lower(), item.participant_id))]
    return {
        "schema_version": COLLABORATION_PARTICIPANT_SCHEMA_VERSION,
        "generated_at": time.time(),
        "participants": snapshots,
        "aliases": dict(sorted(alias_owner.items())),
    }


def normalize_participant_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_participant_alias(value: Any) -> str:
    return re.sub(r"[\s_\-·.]+", "", str(value or "").strip().lower())


def resolve_collaboration_participant(value: Any, registry: dict[str, Any] | None = None) -> str:
    raw = str(value or "").strip().lstrip("@")
    if not raw:
        return ""
    registry = registry or build_collaboration_participant_registry()
    aliases = registry.get("aliases") if isinstance(registry.get("aliases"), dict) else {}
    key = normalize_participant_alias(raw)
    if key in aliases:
        return str(aliases[key])
    normalized = normalize_participant_id(raw)
    participants = registry.get("participants") if isinstance(registry.get("participants"), list) else []
    ids = {str(item.get("participant_id") or "") for item in participants if isinstance(item, dict)}
    return normalized if normalized in ids else ""


def collaboration_participants_mentioned_in_text(
    value: Any,
    registry: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Resolve explicit @ aliases and configured model names in message order."""

    text = str(value or "")
    if not text.strip():
        return ()
    registry = registry or build_collaboration_participant_registry()
    participants = registry.get("participants") if isinstance(registry.get("participants"), list) else []
    matches: list[tuple[int, int, str]] = []

    def match_position(alias: str, *, at_mention: bool) -> int:
        raw = str(alias or "").strip().lstrip("@")
        if not raw:
            return -1
        if at_mention:
            pattern = rf"(?<![\w./-])@{re.escape(raw)}(?=$|[\s,，。！？!?;；:：])"
        elif any("\u4e00" <= ch <= "\u9fff" for ch in raw):
            return text.casefold().find(raw.casefold())
        else:
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(raw)}(?![A-Za-z0-9_])"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.start() if match else -1

    for item in participants:
        if not isinstance(item, dict):
            continue
        participant_id = str(item.get("participant_id") or "").strip()
        if not participant_id:
            continue
        aliases = [str(alias) for alias in item.get("aliases") or () if str(alias).strip()]
        for alias in aliases:
            position = match_position(alias, at_mention=True)
            if position >= 0:
                matches.append((position, -len(alias), participant_id))

        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        model_names = [str(metadata.get("model") or "").strip()]
        if str(item.get("kind") or "").strip().lower() == "model_api":
            model_names.append(str(item.get("label") or "").strip())
        for model_name in model_names:
            if len(normalize_participant_alias(model_name)) < 3:
                continue
            position = match_position(model_name, at_mention=False)
            if position >= 0:
                owner = resolve_collaboration_participant(model_name, registry)
                if owner == participant_id:
                    matches.append((position, -len(model_name), participant_id))

    resolved: list[str] = []
    for _, _, participant_id in sorted(matches):
        if participant_id not in resolved:
            resolved.append(participant_id)
    return tuple(resolved)


def _unique_aliases(values: tuple[str, ...]) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in values:
        text = str(value or "").strip().lstrip("@")
        if not text:
            continue
        safe = re.sub(r"\s+", "", text)
        if safe and safe not in aliases:
            aliases.append(safe)
    return tuple(aliases)


def _agent_aliases(agent_id: str, label: str) -> tuple[str, ...]:
    aliases = [agent_id, label]
    mapping = {
        "main_text": ("Spirit", "主Agent", "主模型"),
        "programming": ("编程Agent", "编程"),
        "vision_model": ("视觉Agent", "视觉"),
        "game_development": ("游戏Agent", "游戏开发"),
        "ecommerce": ("电商Agent", "电商"),
        "skill_runner": ("Skill执行器",),
    }
    aliases.extend(mapping.get(agent_id, ()))
    return tuple(aliases)


def _assistant_participant_id(assistant_id: str) -> str:
    key = normalize_participant_id(assistant_id)
    if key == "codex_cli":
        return "codex"
    return key


def _assistant_aliases(assistant_id: str, label: str) -> tuple[str, ...]:
    key = normalize_participant_id(assistant_id)
    aliases = [assistant_id, label]
    if key == "codex_cli":
        aliases.extend(["Codex", "Code", "CodexCLI"])
    elif key == "claude_code":
        aliases.extend(["ClaudeCode", "Claude", "CC"])
    elif key == "cloud_model":
        aliases.extend(["GPT", "OpenAI", "云端模型"])
    return tuple(aliases)


def _model_aliases(model_id: str, label: str, provider: str, model: str = "") -> tuple[str, ...]:
    aliases = [model_id, label, model, provider]
    if provider:
        aliases.append(provider.replace("_", ""))
    return tuple(aliases)


def _provider_aliases(provider: str, label: str, model: str = "") -> tuple[str, ...]:
    aliases = [provider, label, model]
    key = normalize_participant_id(provider)
    if key in {"openai_compatible", "cloud_openai_compatible"}:
        aliases.extend(["GPT", "OpenAI"])
    elif key == "lmstudio":
        aliases.append("LMStudio")
    elif key == "llamacpp":
        aliases.extend(["llama.cpp", "llamacpp"])
    return tuple(aliases)


def _worker_aliases(worker_id: str, label: str) -> tuple[str, ...]:
    aliases = [worker_id, label]
    if "local_pc" in worker_id:
        aliases.extend(["本机", "电脑", "localpc"])
    return tuple(aliases)
