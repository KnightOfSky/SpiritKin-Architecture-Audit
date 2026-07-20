from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.state_store import locked_state_path
from backend.world.state import WorldStateStore

OBSERVATION_SCHEMA_VERSION = "spiritkin.observation.v1"
DEFAULT_OBSERVATION_LOG = "state/world/observations.jsonl"
PROVIDER_TYPES = frozenset(
    {
        "arkit",
        "android_camera",
        "browser_dom",
        "desktop_capture",
        "remote_camera",
        "screen_ocr",
        "usb_camera",
        "robot_camera",
        "custom",
    }
)
TRACKING_STATUSES = frozenset({"normal", "limited", "unavailable", "not_applicable"})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_FORBIDDEN_RAW_KEYS = re.compile(
    r"(?:captured_?image|image_?data|pixel_?buffer|depth_?map|point_?cloud|video|base64|bytes|file_?path|local_?path|raw_?data|mesh_?data|texture)",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = re.compile(r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key)", re.IGNORECASE)
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "observation_id",
        "workspace_id",
        "host_id",
        "provider_id",
        "provider_type",
        "session_id",
        "sequence",
        "observed_at",
        "tracking",
        "camera_pose",
        "location",
        "depth",
        "objects",
        "planes",
        "relations",
        "environment",
        "confidence",
        "observation_retention_seconds",
    }
)


def _bounded_text(value: Any, field: str, *, limit: int = 160, required: bool = False) -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise ValueError(f"{field} is required")
    return text


def _required_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError(f"invalid {field}")
    return text


def _optional_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _required_id(text, field)


def _number(value: Any, field: str, *, minimum: float = -1_000_000.0, maximum: float = 1_000_000.0) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{field} is out of range")
    return round(number, 6)


def _optional_number(value: Any, field: str, **bounds: float) -> float | None:
    if value in (None, ""):
        return None
    return _number(value, field, **bounds)


def _confidence(value: Any, field: str = "confidence") -> float:
    return _number(1.0 if value in (None, "") else value, field, minimum=0.0, maximum=1.0)


def _timestamp(value: Any, field: str = "observed_at") -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC).isoformat(timespec="milliseconds")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    now = datetime.now(UTC).timestamp()
    timestamp = parsed.astimezone(UTC).timestamp()
    if timestamp > now + 300 or timestamp < now - (7 * 24 * 3600):
        raise ValueError(f"{field} is outside the accepted window")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds")


def _vector(value: Any, field: str, *, size: int = 3) -> dict[str, float]:
    keys = ("x", "y", "z", "w")[:size]
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return {key: _number(value.get(key), f"{field}.{key}") for key in keys}


def _pose(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    position = _vector(value.get("position"), "camera_pose.position")
    orientation = _vector(value.get("orientation"), "camera_pose.orientation", size=4)
    magnitude = math.sqrt(sum(component * component for component in orientation.values()))
    if magnitude < 0.000001:
        raise ValueError("camera_pose.orientation must be a non-zero quaternion")
    orientation = {key: round(component / magnitude, 6) for key, component in orientation.items()}
    return {"position": position, "orientation": orientation}


def _safe_attributes(value: Any, field: str) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or len(value) > 24:
        raise ValueError(f"{field} must be a bounded object")
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = _bounded_text(key, f"{field}.key", limit=64, required=True)
        if _FORBIDDEN_RAW_KEYS.search(name) or _SENSITIVE_KEYS.search(name):
            raise ValueError(f"{field}.{name} is not allowed")
        if isinstance(item, bool) or item is None:
            result[name] = item
        elif isinstance(item, (int, float)):
            result[name] = _number(item, f"{field}.{name}")
        elif isinstance(item, str):
            result[name] = _bounded_text(item, f"{field}.{name}", limit=240)
        else:
            raise ValueError(f"{field}.{name} must be scalar")
    return result


def _spatial_item(value: Any, field: str, index: int, *, plane: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field}[{index}] must be an object")
    item: dict[str, Any] = {
        "entity_id": _optional_id(value.get("entity_id") or value.get("object_id") or value.get("anchor_id"), f"{field}[{index}].entity_id"),
        "kind": _bounded_text(value.get("kind") or ("plane" if plane else "object"), f"{field}[{index}].kind", limit=80, required=True).lower(),
        "label": _bounded_text(value.get("label") or value.get("classification"), f"{field}[{index}].label", limit=120),
        "confidence": _confidence(value.get("confidence"), f"{field}[{index}].confidence"),
        "attributes": _safe_attributes(value.get("attributes"), f"{field}[{index}].attributes"),
    }
    if isinstance(value.get("position") or value.get("center"), dict):
        item["position"] = _vector(value.get("position") or value.get("center"), f"{field}[{index}].position")
    if isinstance(value.get("extent") or value.get("size"), dict):
        extent = _vector(value.get("extent") or value.get("size"), f"{field}[{index}].extent")
        if any(component < 0 for component in extent.values()):
            raise ValueError(f"{field}[{index}].extent cannot be negative")
        item["extent"] = extent
    if plane:
        item["alignment"] = _bounded_text(value.get("alignment") or "unknown", f"{field}[{index}].alignment", limit=40).lower()
    return item


def _relations(value: Any) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("relations must be a bounded array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"relations[{index}] must be an object")
        result.append(
            {
                "subject_id": _required_id(item.get("subject_id"), f"relations[{index}].subject_id"),
                "predicate": _bounded_text(item.get("predicate"), f"relations[{index}].predicate", limit=60, required=True).lower(),
                "object_id": _required_id(item.get("object_id"), f"relations[{index}].object_id"),
                "confidence": _confidence(item.get("confidence"), f"relations[{index}].confidence"),
            }
        )
    return result


def _reject_raw_payload(value: Any, path: str = "observation") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if _FORBIDDEN_RAW_KEYS.search(key_text):
                raise ValueError(f"raw sensor payload is not allowed: {path}.{key_text}")
            if _SENSITIVE_KEYS.search(key_text):
                raise ValueError(f"sensitive payload is not allowed: {path}.{key_text}")
            _reject_raw_payload(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_raw_payload(item, f"{path}[{index}]")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"binary sensor payload is not allowed: {path}")


def normalize_observation(
    payload: dict[str, Any],
    *,
    workspace_id: str = "",
    host_id: str = "",
    provider_id: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("observation must be an object")
    _reject_raw_payload(payload)
    unknown_keys = sorted(str(key) for key in payload if str(key) not in _ALLOWED_TOP_LEVEL_KEYS)
    if unknown_keys:
        raise ValueError(f"unsupported observation fields: {', '.join(unknown_keys[:8])}")
    resolved_workspace = _required_id(workspace_id or payload.get("workspace_id"), "workspace_id")
    resolved_host = _required_id(host_id or payload.get("host_id"), "host_id")
    resolved_provider = _required_id(provider_id or payload.get("provider_id"), "provider_id")
    provider_type = str(payload.get("provider_type") or "custom").strip().lower()
    if provider_type not in PROVIDER_TYPES:
        raise ValueError(f"unsupported provider_type: {provider_type}")
    tracking = payload.get("tracking") if isinstance(payload.get("tracking"), dict) else {}
    tracking_status = str(tracking.get("status") or "not_applicable").strip().lower()
    if tracking_status not in TRACKING_STATUSES:
        raise ValueError(f"unsupported tracking status: {tracking_status}")
    objects = payload.get("objects") or []
    planes = payload.get("planes") or []
    if not isinstance(objects, list) or len(objects) > 256:
        raise ValueError("objects must be a bounded array")
    if not isinstance(planes, list) or len(planes) > 128:
        raise ValueError("planes must be a bounded array")
    depth = payload.get("depth") if isinstance(payload.get("depth"), dict) else {}
    depth_summary = {
        "available": bool(depth.get("available")),
        "min_m": _optional_number(depth.get("min_m"), "depth.min_m", minimum=0.0, maximum=1000.0),
        "max_m": _optional_number(depth.get("max_m"), "depth.max_m", minimum=0.0, maximum=1000.0),
        "mean_m": _optional_number(depth.get("mean_m"), "depth.mean_m", minimum=0.0, maximum=1000.0),
        "confidence": _confidence(depth.get("confidence"), "depth.confidence"),
    }
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    location_summary: dict[str, Any] = {}
    if location:
        location_summary = {
            "latitude": round(_number(location.get("latitude"), "location.latitude", minimum=-90.0, maximum=90.0), 4),
            "longitude": round(_number(location.get("longitude"), "location.longitude", minimum=-180.0, maximum=180.0), 4),
            "altitude_m": _optional_number(location.get("altitude_m"), "location.altitude_m"),
            "horizontal_accuracy_m": _optional_number(location.get("horizontal_accuracy_m"), "location.horizontal_accuracy_m", minimum=0.0, maximum=100_000.0),
            "coordinate_precision_decimals": 4,
        }
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observation_id": _optional_id(payload.get("observation_id"), "observation_id") or f"observation-{uuid4().hex}",
        "workspace_id": resolved_workspace,
        "host_id": resolved_host,
        "provider_id": resolved_provider,
        "provider_type": provider_type,
        "session_id": _optional_id(payload.get("session_id"), "session_id"),
        "sequence": max(0, int(payload.get("sequence") or 0)),
        "observed_at": _timestamp(payload.get("observed_at")),
        "tracking": {
            "status": tracking_status,
            "reason": _bounded_text(tracking.get("reason"), "tracking.reason", limit=120),
            "world_mapping_status": _bounded_text(tracking.get("world_mapping_status"), "tracking.world_mapping_status", limit=40),
        },
        "camera_pose": _pose(payload.get("camera_pose")),
        "location": location_summary,
        "depth": depth_summary,
        "objects": [_spatial_item(item, "objects", index) for index, item in enumerate(objects)],
        "planes": [_spatial_item(item, "planes", index, plane=True) for index, item in enumerate(planes)],
        "relations": _relations(payload.get("relations")),
        "environment": _safe_attributes(payload.get("environment"), "environment"),
        "confidence": _confidence(payload.get("confidence")),
        "retention": {
            "raw_sensor_data_accepted": False,
            "raw_reference_ttl_seconds": 0,
            "observation_retention_seconds": max(3600, min(30 * 24 * 3600, int(payload.get("observation_retention_seconds") or 7 * 24 * 3600))),
            "world_state_long_term": True,
        },
        "received_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }


class ObservationStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_OBSERVATION_LOG,
        *,
        max_events: int = 50_000,
        retention_seconds: int = 7 * 24 * 3600,
    ):
        self.path = Path(path).resolve()
        self.max_events = max(100, min(200_000, int(max_events)))
        self.retention_seconds = max(3600, min(30 * 24 * 3600, int(retention_seconds)))

    def append(self, observation: dict[str, Any]) -> dict[str, Any]:
        if observation.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported observation schema")
        with locked_state_path(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            if self.path.stat().st_size > 32 * 1024 * 1024:
                self._compact_locked()
        return dict(observation)

    def list(self, *, workspace_id: str, provider_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        rows = self._rows()
        selected = [
            row
            for row in rows
            if str(row.get("workspace_id") or "") == workspace_id
            and (not provider_id or str(row.get("provider_id") or "") == provider_id)
        ]
        return selected[-max(1, min(1000, int(limit))):]

    def cleanup(self) -> dict[str, Any]:
        with locked_state_path(self.path):
            before = len(self._rows_unlocked())
            kept = self._compact_locked()
        return {"removed": max(0, before - kept), "retained": kept, "raw_assets_removed": 0}

    def snapshot(self, *, workspace_id: str, limit: int = 20) -> dict[str, Any]:
        observations = self.list(workspace_id=workspace_id, limit=limit)
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "count": len(observations),
            "recent": observations,
            "policy": {
                "raw_sensor_data_accepted": False,
                "observation_retention_seconds": self.retention_seconds,
                "max_events": self.max_events,
            },
        }

    def _rows(self) -> list[dict[str, Any]]:
        with locked_state_path(self.path):
            return self._rows_unlocked()

    def _rows_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-self.max_events :]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("schema_version") == OBSERVATION_SCHEMA_VERSION:
                rows.append(item)
        return rows

    def _compact_locked(self) -> int:
        cutoff = time.time() - self.retention_seconds
        kept = []
        for item in self._rows_unlocked():
            try:
                observed = datetime.fromisoformat(str(item.get("observed_at") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if observed >= cutoff:
                kept.append(item)
        kept = kept[-self.max_events :]
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in kept), encoding="utf-8")
        os.replace(temporary, self.path)
        return len(kept)


class ObservationRuntime:
    def __init__(self, *, observation_store: ObservationStore | None = None, world_store: WorldStateStore | None = None):
        self.observations = observation_store or ObservationStore()
        self.world = world_store or WorldStateStore()

    def ingest(
        self,
        payload: dict[str, Any],
        *,
        workspace_id: str = "",
        host_id: str = "",
        provider_id: str = "",
    ) -> dict[str, Any]:
        observation = normalize_observation(
            payload,
            workspace_id=workspace_id,
            host_id=host_id,
            provider_id=provider_id,
        )
        self.observations.append(observation)
        world_update = self.world.apply_observation(observation)
        return {
            "ok": True,
            "observation": observation,
            "world_update": world_update,
            "raw_sensor_data_stored": False,
        }
