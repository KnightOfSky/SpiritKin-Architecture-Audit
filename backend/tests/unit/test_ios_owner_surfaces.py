from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.mobile.ios_channels import build_ios_channels_snapshot, persist_wechat_ilink_status
from backend.mobile.ios_music import build_ios_music_snapshot, handle_ios_music_action
from backend.mobile.ios_resources import build_ios_resources_snapshot, handle_ios_resource_action


class _ArtifactStore:
    def __init__(self, path: Path, mime_type: str = "audio/mpeg"):
        self.path = path
        self.mime_type = mime_type

    def artifact_file(self, artifact_id: str, *, file_index: int, workspace_id: str):
        assert artifact_id == "art-audio"
        assert file_index == 0
        assert workspace_id == "tenant-a"
        return {"path": self.path, "mime_type": self.mime_type}


def test_ios_music_snapshot_and_artifact_command_are_real_files(tmp_path):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "status": "playing",
                "queue": [{"source": "focus.mp3", "title": "focus", "is_remote": False}],
                "current_index": 0,
                "current_track": {"source": "focus.mp3", "title": "focus", "is_remote": False},
                "volume": 0.6,
            }
        ),
        encoding="utf-8",
    )
    audio = tmp_path / "focus.mp3"
    audio.write_bytes(b"audio")
    command_path = tmp_path / "commands.jsonl"

    snapshot = build_ios_music_snapshot(status_path=status_path)
    result = handle_ios_music_action(
        _ArtifactStore(audio),
        {"action": "play_artifact", "artifact_id": "art-audio", "file_index": 0},
        workspace_id="tenant-a",
        command_path=command_path,
        status_path=status_path,
    )
    command = json.loads(command_path.read_text(encoding="utf-8"))

    assert snapshot["controller_online"] is True
    assert snapshot["current_track"]["title"] == "focus"
    assert "source" not in snapshot["current_track"]
    assert "status_path" not in snapshot
    assert result["command"]["action"] == "play"
    assert command["arguments"]["paths"] == [str(audio.resolve())]


def test_ios_music_rejects_non_audio_artifact(tmp_path):
    document = tmp_path / "notes.txt"
    document.write_text("not music", encoding="utf-8")

    with pytest.raises(ValueError, match="supported audio"):
        handle_ios_music_action(
            _ArtifactStore(document, "text/plain"),
            {"action": "play_artifact", "artifact_id": "art-audio"},
            workspace_id="tenant-a",
            command_path=tmp_path / "commands.jsonl",
        )


def test_ios_channel_snapshot_masks_identity_and_never_returns_token(tmp_path, monkeypatch):
    status_path = tmp_path / "wechat-status.json"
    monkeypatch.setenv("SPIRITKIN_WECHAT_ILINK_ENABLED", "1")
    monkeypatch.setenv("SPIRITKIN_WECHAT_ILINK_BOT_TOKEN", "super-secret-token")
    monkeypatch.setenv("SPIRITKIN_WECHAT_ILINK_BOT_ID", "bot-123456789")
    monkeypatch.setenv("SPIRITKIN_WECHAT_ILINK_USER_ID", "user-123456789")
    persist_wechat_ilink_status({"phase": "running", "message": "connected", "detail": {"token": "leak", "polls": 2}}, status_path=status_path)

    snapshot = build_ios_channels_snapshot(status_path=status_path)["wechat_ilink"]
    serialized = json.dumps(snapshot)

    assert snapshot["phase"] == "running"
    assert snapshot["bot_id"] == "bot...789"
    assert snapshot["secret_exposed"] is False
    assert "super-secret-token" not in serialized
    assert "leak" not in status_path.read_text(encoding="utf-8")


def test_commerce_resources_are_workspace_scoped_and_products_require_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_RESOURCE_REGISTRY_PATH", str(tmp_path / "resources.json"))
    store = {
        "resource_id": "commerce_store:main",
        "label": "Main shop",
        "resource_type": "commerce_store",
        "platform": "pdd",
        "credential_ref": "keychain:commerce/pdd/main",
    }
    handle_ios_resource_action({"action": "create", "resource": store}, workspace_id="tenant-a")

    with pytest.raises(ValueError, match="store_resource_id"):
        handle_ios_resource_action(
            {
                "action": "create",
                "resource": {
                    "resource_id": "commerce_product:orphan",
                    "label": "Orphan",
                    "resource_type": "commerce_product",
                },
            },
            workspace_id="tenant-a",
        )

    snapshot_a = build_ios_resources_snapshot(workspace_id="tenant-a")
    snapshot_b = build_ios_resources_snapshot(workspace_id="tenant-b")
    resource = snapshot_a["resource_registry"]["resources"][0]

    assert resource["editable"] is True
    assert resource["metadata"]["workspace_id"] == "tenant-a"
    assert "ecommerce" in resource["tags"]
    assert snapshot_b["resource_count"] == 0

    product = {
        "resource_id": "commerce_product:sku-1",
        "label": "SKU 1",
        "resource_type": "commerce_product",
        "metadata": {"store_resource_id": "commerce_store:main"},
    }
    handle_ios_resource_action({"action": "create", "resource": product}, workspace_id="tenant-a")
    with pytest.raises(ValueError, match="products reference"):
        handle_ios_resource_action(
            {"action": "delete", "resource_id": "commerce_store:main"},
            workspace_id="tenant-a",
        )
