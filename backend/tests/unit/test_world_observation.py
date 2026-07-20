from __future__ import annotations

import json

import pytest

from backend.state_store import StateCorruptionError
from backend.world import ObservationRuntime, ObservationStore, WorldStateStore, normalize_observation


def _payload() -> dict:
    return {
        "provider_type": "arkit",
        "session_id": "scan-1",
        "sequence": 8,
        "tracking": {"status": "normal", "world_mapping_status": "mapped"},
        "camera_pose": {
            "position": {"x": 0.0, "y": 1.5, "z": 0.2},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 2.0},
        },
        "depth": {"available": True, "min_m": 0.4, "max_m": 4.0, "mean_m": 1.2, "confidence": 0.9},
        "location": {"latitude": 31.230416, "longitude": 121.473701, "horizontal_accuracy_m": 35},
        "planes": [
            {
                "anchor_id": "desk-plane",
                "kind": "plane",
                "classification": "table",
                "alignment": "horizontal",
                "center": {"x": 0.0, "y": 0.8, "z": -1.0},
                "extent": {"x": 1.4, "y": 0.02, "z": 0.8},
                "confidence": 0.95,
            }
        ],
        "objects": [
            {
                "object_id": "laptop-1",
                "kind": "device",
                "label": "laptop",
                "position": {"x": 0.1, "y": 0.9, "z": -1.0},
                "confidence": 0.92,
            }
        ],
        "relations": [
            {"subject_id": "laptop-1", "predicate": "on", "object_id": "desk-plane", "confidence": 0.9}
        ],
        "confidence": 0.93,
    }


def test_observation_rejects_raw_images_depth_maps_paths_and_secrets():
    for key, value in (
        ("capturedImage", "base64data"),
        ("depth_map", [[1, 2]]),
        ("point_cloud", [1, 2, 3]),
        ("file_path", "/tmp/frame.jpg"),
        ("api_token", "secret"),
    ):
        payload = _payload()
        payload[key] = value
        with pytest.raises(ValueError):
            normalize_observation(payload, workspace_id="tenant-a", host_id="ios:phone-a", provider_id="arkit:phone-a")

    payload = _payload()
    payload["unmanaged_payload"] = "ignored data must not be persisted"
    with pytest.raises(ValueError, match="unsupported observation fields"):
        normalize_observation(payload, workspace_id="tenant-a", host_id="ios:phone-a", provider_id="arkit:phone-a")


def test_observation_normalizes_pose_and_world_merges_stable_entities(tmp_path):
    runtime = ObservationRuntime(
        observation_store=ObservationStore(tmp_path / "observations.jsonl"),
        world_store=WorldStateStore(tmp_path / "world.json", stale_after_seconds=300),
    )
    first = runtime.ingest(_payload(), workspace_id="tenant-a", host_id="ios:phone-a", provider_id="arkit:phone-a")
    second_payload = _payload()
    second_payload["sequence"] = 9
    second_payload["objects"][0]["position"]["x"] = 0.2
    second = runtime.ingest(second_payload, workspace_id="tenant-a", host_id="ios:phone-a", provider_id="arkit:phone-a")

    assert first["raw_sensor_data_stored"] is False
    assert first["observation"]["camera_pose"]["orientation"]["w"] == 1.0
    assert first["observation"]["location"]["latitude"] == 31.2304
    assert first["observation"]["location"]["longitude"] == 121.4737
    assert second["world_update"]["entity_count"] == 2
    world = runtime.world.snapshot(workspace_id="tenant-a")
    laptop = next(item for item in world["entities"] if item["label"] == "laptop")
    assert laptop["observation_count"] == 2
    assert laptop["position"]["x"] == 0.2
    assert world["relation_count"] == 1
    assert world["policy"]["raw_sensor_data_stored"] is False


def test_observation_store_is_workspace_scoped_and_contains_no_binary_payload(tmp_path):
    store = ObservationStore(tmp_path / "observations.jsonl")
    for workspace_id in ("tenant-a", "tenant-b"):
        observation = normalize_observation(
            _payload(),
            workspace_id=workspace_id,
            host_id=f"ios:{workspace_id}",
            provider_id=f"arkit:{workspace_id}",
        )
        store.append(observation)

    assert len(store.list(workspace_id="tenant-a")) == 1
    assert len(store.list(workspace_id="tenant-b")) == 1
    text = (tmp_path / "observations.jsonl").read_text(encoding="utf-8")
    assert "capturedImage" not in text
    assert "depth_map" not in text
    assert json.loads(text.splitlines()[0])["retention"]["raw_sensor_data_accepted"] is False


def test_corrupt_world_state_fails_closed(tmp_path):
    path = tmp_path / "world.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(StateCorruptionError, match="top-level JSON"):
        WorldStateStore(path).snapshot(workspace_id="tenant-a")

    path.write_text(
        '{"schema_version":"spiritkin.world_state.v1","workspaces":{"tenant-a":[]}}',
        encoding="utf-8",
    )
    with pytest.raises(StateCorruptionError, match="workspace record 'tenant-a' must be an object"):
        WorldStateStore(path).snapshot(workspace_id="tenant-a")
