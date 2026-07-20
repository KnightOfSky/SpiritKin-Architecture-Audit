from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from backend.app.file_uploads import ingest_uploaded_files
from backend.security.sensitive_payload import assert_no_sensitive_payload

DEFAULT_MOBILE_ARTIFACT_ROOT = "state/mobile-artifacts"
MAX_RECENT_ARTIFACTS = 120
DEFAULT_TTL_HOURS = 168


class MobileArtifactStore:
    def __init__(self, root: str | Path | None = None):
        self.root = _artifact_root(root)
        self.index_path = self.root / "index.json"

    def snapshot(self) -> dict[str, Any]:
        data = self._load()
        artifacts = [dict(item) for item in data.get("artifacts", []) if isinstance(item, dict)]
        artifacts.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        total_size = sum(_int(item.get("size_bytes")) for item in artifacts)
        image_count = sum(1 for item in artifacts if str(item.get("mime_type") or "").startswith("image/"))
        expired_count = sum(1 for item in artifacts if _is_expired(item))
        return {
            "root": str(self.root),
            "index_path": str(self.index_path),
            "artifact_count": len(artifacts),
            "image_count": image_count,
            "expired_count": expired_count,
            "total_size_bytes": total_size,
            "recent": artifacts[:MAX_RECENT_ARTIFACTS],
        }

    def ingest(self, payload: dict[str, Any], *, source: str = "mobile", device_id: str = "") -> dict[str, Any]:
        assert_no_sensitive_payload(payload)
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            return {"ok": False, "status": "missing_files", "message": "files must be a non-empty list."}
        purpose = str(payload.get("purpose") or "mobile_artifact").strip() or "mobile_artifact"
        owner_agent_id = str(payload.get("owner_agent_id") or payload.get("owner") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        ttl_hours = _int(payload.get("ttl_hours"), DEFAULT_TTL_HOURS)
        tags = [str(item).strip() for item in payload.get("tags", []) if str(item).strip()] if isinstance(payload.get("tags"), list) else []
        report = ingest_uploaded_files(_normalize_files(files), upload_root=self.root / "uploads", purpose=purpose)
        data = self._load()
        created: list[dict[str, Any]] = []
        now = time.time()
        for attachment in report.attachments:
            artifact = {
                "artifact_id": f"artifact_{attachment['file_id']}",
                "file_id": attachment["file_id"],
                "name": attachment["name"],
                "mime_type": attachment["mime_type"],
                "uri": attachment["uri"],
                "relative_path": attachment["relative_path"],
                "size_bytes": attachment["size_bytes"],
                "purpose": purpose,
                "source": source,
                "device_id": device_id,
                "owner_agent_id": owner_agent_id,
                "task_id": task_id,
                "tags": tags,
                "upload_id": report.upload_id,
                "created_at": now,
                "expires_at": now + max(1, ttl_hours) * 3600,
                "status": "available",
            }
            data.setdefault("artifacts", []).append(artifact)
            created.append(artifact)
        self._save(data)
        return {
            "ok": True,
            "status": "ingested",
            "message": f"已接收 {len(created)} 个移动端 artifact。",
            "upload": report.snapshot(),
            "artifacts": created,
        }

    def artifact_file(self, artifact_id: str) -> dict[str, Any]:
        target = str(artifact_id or "").strip()
        if not target:
            raise KeyError("artifact_id is required")
        data = self._load()
        for item in data.get("artifacts", []):
            if not isinstance(item, dict) or str(item.get("artifact_id") or "") != target:
                continue
            path = Path(str(item.get("uri") or ""))
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(target)
            return {
                "artifact": dict(item),
                "path": path,
                "filename": str(item.get("name") or path.name),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
            }
        raise KeyError(target)

    def cleanup(self, *, expired_only: bool = True, keep_recent: int = 200) -> dict[str, Any]:
        data = self._load()
        artifacts = [dict(item) for item in data.get("artifacts", []) if isinstance(item, dict)]
        artifacts.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        keep_ids = {str(item.get("artifact_id") or "") for item in artifacts[: max(0, keep_recent)]}
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for item in artifacts:
            artifact_id = str(item.get("artifact_id") or "")
            should_remove = _is_expired(item) if expired_only else artifact_id not in keep_ids
            if should_remove:
                removed.append(item)
                _remove_artifact_file(item)
            else:
                kept.append(item)
        data["artifacts"] = sorted(kept, key=lambda item: float(item.get("created_at") or 0))
        self._save(data)
        _remove_empty_upload_dirs(self.root / "uploads")
        return {
            "ok": True,
            "status": "cleaned",
            "removed": len(removed),
            "remaining": len(kept),
            "removed_artifacts": removed[:40],
        }

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", "spiritkin.mobile_artifacts.v1")
        data.setdefault("artifacts", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.index_path)


def _artifact_root(root: str | Path | None = None) -> Path:
    value = root or os.getenv("SPIRITKIN_MOBILE_ARTIFACT_ROOT", DEFAULT_MOBILE_ARTIFACT_ROOT)
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _normalize_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        next_item = dict(item)
        if "content_base64" not in next_item and "base64" in next_item:
            next_item["content_base64"] = next_item.get("base64")
        data_url = str(next_item.get("data_url") or "")
        if "content_base64" not in next_item and data_url.startswith("data:") and "," in data_url:
            header, encoded = data_url.split(",", 1)
            next_item["content_base64"] = encoded
            if "mime_type" not in next_item and ";" in header:
                next_item["mime_type"] = header[5:].split(";", 1)[0]
        normalized.append(next_item)
    return normalized


def _is_expired(item: dict[str, Any]) -> bool:
    expires_at = _float(item.get("expires_at"))
    return expires_at > 0 and expires_at < time.time()


def _remove_artifact_file(item: dict[str, Any]) -> None:
    uri = str(item.get("uri") or "").strip()
    if not uri:
        return
    try:
        path = Path(uri)
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        return


def _remove_empty_upload_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
