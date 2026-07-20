from __future__ import annotations

from backend.mobile.ios_world import build_ios_world_snapshot, ingest_ios_observation


def test_ios_observation_binding_overrides_client_workspace_host_and_provider(tmp_path):
    result = ingest_ios_observation(
        {
            "workspace_id": "attacker",
            "host_id": "desktop:attacker",
            "provider_id": "custom:attacker",
            "provider_type": "arkit",
            "tracking": {"status": "normal"},
            "objects": [],
            "planes": [],
            "confidence": 1.0,
        },
        workspace_id="tenant-a",
        actor_id="phone-a",
        project_root=tmp_path,
    )

    observation = result["observation"]
    assert observation["workspace_id"] == "tenant-a"
    assert observation["host_id"] == "ios:phone-a"
    assert observation["provider_id"] == "arkit:phone-a"
    assert result["raw_sensor_data_stored"] is False

    snapshot = build_ios_world_snapshot(workspace_id="tenant-a", project_root=tmp_path)
    assert snapshot["observations"]["count"] == 1
    assert snapshot["provider_contract"]["raw_sensor_data_accepted"] is False
