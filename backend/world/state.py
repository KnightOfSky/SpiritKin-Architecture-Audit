from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.state_store import StateCorruptionError, locked_state_path, read_json_state

WORLD_STATE_SCHEMA_VERSION = "spiritkin.world_state.v1"
DEFAULT_WORLD_STATE_PATH = "state/world/world_state.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _entity_key(item: dict[str, Any], observation: dict[str, Any]) -> str:
    stable_id = str(item.get("entity_id") or "").strip()
    if stable_id:
        identity = f"{observation['provider_id']}:{stable_id}"
    else:
        position = item.get("position") if isinstance(item.get("position"), dict) else {}
        cell = ":".join(str(round(float(position.get(axis) or 0) * 4) / 4) for axis in ("x", "y", "z"))
        identity = f"{item.get('kind')}:{item.get('label')}:{cell}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"world-entity-{digest}"


class WorldStateStore:
    def __init__(self, path: str | Path = DEFAULT_WORLD_STATE_PATH, *, stale_after_seconds: int = 300):
        self.path = Path(path).resolve()
        self.stale_after_seconds = max(5, min(24 * 3600, int(stale_after_seconds)))

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"schema_version": WORLD_STATE_SCHEMA_VERSION, "workspaces": {}}

    def _load(self) -> dict[str, Any]:
        state = read_json_state(self.path, self._empty_state(), strict=True)
        if state.get("schema_version") != WORLD_STATE_SCHEMA_VERSION or not isinstance(state.get("workspaces"), dict):
            raise StateCorruptionError(self.path, f"unsupported world state schema: {state.get('schema_version')!r}")
        for workspace_id, workspace in state["workspaces"].items():
            if not isinstance(workspace, dict):
                raise StateCorruptionError(self.path, f"world workspace record {workspace_id!r} must be an object")
            for field in ("entities", "relations", "provider_states"):
                records = workspace.get(field)
                if not isinstance(records, dict):
                    raise StateCorruptionError(self.path, f"world workspace {workspace_id!r} field {field!r} must be an object")
                if any(not isinstance(record, dict) for record in records.values()):
                    raise StateCorruptionError(self.path, f"world workspace {workspace_id!r} field {field!r} must contain only objects")
        return state

    def apply_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(observation.get("workspace_id") or "")
        if not workspace_id:
            raise ValueError("observation workspace_id is required")
        observed_at = str(observation.get("observed_at") or "")
        with locked_state_path(self.path):
            state = self._load()
            workspace = state["workspaces"].get(workspace_id)
            if not isinstance(workspace, dict):
                workspace = {"workspace_id": workspace_id, "entities": {}, "relations": {}, "provider_states": {}}
            entities = workspace.get("entities") if isinstance(workspace.get("entities"), dict) else {}
            mapped_ids: dict[str, str] = {}
            updated_entity_ids: list[str] = []
            for item in [*(observation.get("planes") or []), *(observation.get("objects") or [])]:
                if not isinstance(item, dict):
                    continue
                entity_id = _entity_key(item, observation)
                source_id = str(item.get("entity_id") or "")
                if source_id:
                    mapped_ids[source_id] = entity_id
                current = entities.get(entity_id) if isinstance(entities.get(entity_id), dict) else {}
                providers = list(current.get("provider_ids") or [])
                if observation["provider_id"] not in providers:
                    providers.append(observation["provider_id"])
                confidence = float(item.get("confidence") or 0)
                prior_confidence = float(current.get("confidence") or 0)
                prefer_new = confidence >= prior_confidence or _timestamp(observed_at) >= _timestamp(str(current.get("last_observed_at") or ""))
                entity = {
                    **current,
                    "entity_id": entity_id,
                    "workspace_id": workspace_id,
                    "source_entity_id": source_id,
                    "kind": str(item.get("kind") or current.get("kind") or "object"),
                    "label": str(item.get("label") or current.get("label") or ""),
                    "confidence": max(confidence, prior_confidence),
                    "position": dict(item.get("position") or current.get("position") or {}) if prefer_new else dict(current.get("position") or {}),
                    "extent": dict(item.get("extent") or current.get("extent") or {}) if prefer_new else dict(current.get("extent") or {}),
                    "attributes": {**dict(current.get("attributes") or {}), **dict(item.get("attributes") or {})},
                    "provider_ids": providers[-16:],
                    "first_observed_at": str(current.get("first_observed_at") or observed_at),
                    "last_observed_at": observed_at,
                    "observation_count": int(current.get("observation_count") or 0) + 1,
                    "status": "current",
                }
                if item.get("alignment"):
                    entity["alignment"] = str(item["alignment"])
                entities[entity_id] = entity
                updated_entity_ids.append(entity_id)
            relations = workspace.get("relations") if isinstance(workspace.get("relations"), dict) else {}
            updated_relation_ids: list[str] = []
            for relation in observation.get("relations") or []:
                if not isinstance(relation, dict):
                    continue
                subject = mapped_ids.get(str(relation.get("subject_id") or ""))
                object_id = mapped_ids.get(str(relation.get("object_id") or ""))
                if not subject or not object_id:
                    continue
                identity = f"{subject}:{relation.get('predicate')}:{object_id}"
                relation_id = f"world-relation-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
                relations[relation_id] = {
                    "relation_id": relation_id,
                    "workspace_id": workspace_id,
                    "subject_id": subject,
                    "predicate": str(relation.get("predicate") or "related_to"),
                    "object_id": object_id,
                    "confidence": float(relation.get("confidence") or 0),
                    "last_observed_at": observed_at,
                    "provider_id": observation["provider_id"],
                    "status": "current",
                }
                updated_relation_ids.append(relation_id)
            provider_states = workspace.get("provider_states") if isinstance(workspace.get("provider_states"), dict) else {}
            provider_states[observation["provider_id"]] = {
                "provider_id": observation["provider_id"],
                "provider_type": observation["provider_type"],
                "host_id": observation["host_id"],
                "tracking": dict(observation.get("tracking") or {}),
                "camera_pose": dict(observation.get("camera_pose") or {}),
                "location": dict(observation.get("location") or {}),
                "depth": dict(observation.get("depth") or {}),
                "last_observation_id": observation["observation_id"],
                "last_observed_at": observed_at,
            }
            workspace.update(
                {
                    "entities": entities,
                    "relations": relations,
                    "provider_states": provider_states,
                    "updated_at": observed_at,
                    "last_observation_id": observation["observation_id"],
                }
            )
            state["workspaces"][workspace_id] = workspace
            _write_json_atomic(self.path, state)
        return {
            "schema_version": WORLD_STATE_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "observation_id": observation["observation_id"],
            "updated_entity_ids": updated_entity_ids,
            "updated_relation_ids": updated_relation_ids,
            "entity_count": len(entities),
            "relation_count": len(relations),
        }

    def snapshot(self, *, workspace_id: str, include_stale: bool = True) -> dict[str, Any]:
        with locked_state_path(self.path):
            state = self._load()
        workspace = state["workspaces"].get(workspace_id)
        if not isinstance(workspace, dict):
            workspace = {"workspace_id": workspace_id, "entities": {}, "relations": {}, "provider_states": {}}
        now = datetime.now(UTC).timestamp()
        entities = []
        for item in workspace.get("entities", {}).values():
            if not isinstance(item, dict):
                continue
            copy = dict(item)
            copy["status"] = "stale" if now - _timestamp(str(copy.get("last_observed_at") or "")) > self.stale_after_seconds else "current"
            if include_stale or copy["status"] == "current":
                entities.append(copy)
        relations = []
        for item in workspace.get("relations", {}).values():
            if not isinstance(item, dict):
                continue
            copy = dict(item)
            copy["status"] = "stale" if now - _timestamp(str(copy.get("last_observed_at") or "")) > self.stale_after_seconds else "current"
            if include_stale or copy["status"] == "current":
                relations.append(copy)
        entities.sort(key=lambda item: str(item.get("last_observed_at") or ""), reverse=True)
        relations.sort(key=lambda item: str(item.get("last_observed_at") or ""), reverse=True)
        return {
            "schema_version": WORLD_STATE_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "updated_at": str(workspace.get("updated_at") or ""),
            "last_observation_id": str(workspace.get("last_observation_id") or ""),
            "entity_count": len(entities),
            "current_entity_count": sum(1 for item in entities if item["status"] == "current"),
            "relation_count": len(relations),
            "entities": entities,
            "relations": relations,
            "provider_states": list((workspace.get("provider_states") or {}).values()),
            "policy": {
                "raw_sensor_data_stored": False,
                "world_state_long_term": True,
                "stale_after_seconds": self.stale_after_seconds,
            },
        }
