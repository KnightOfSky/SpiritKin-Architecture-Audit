from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UPLOAD_ROOT = Path("state/uploads")
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".log", ".py", ".yaml", ".yml", ".json", ".jsonl", ".csv"}
MAX_INLINE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class FileIngestReport:
    upload_id: str
    root: str
    attachments: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    skipped: list[dict[str, str]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "root": self.root,
            "attachments": list(self.attachments),
            "documents": list(self.documents),
            "skipped": list(self.skipped),
        }


def ingest_uploaded_files(
    files: list[dict[str, Any]],
    *,
    upload_root: str | Path = UPLOAD_ROOT,
    purpose: str = "user_upload",
) -> FileIngestReport:
    upload_id = f"upl_{int(time.time())}_{uuid.uuid4().hex[:10]}"
    root = Path(upload_root).resolve() / upload_id
    root.mkdir(parents=True, exist_ok=True)
    attachments: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            skipped.append({"path": f"file-{index}", "reason": "invalid_file_payload"})
            continue
        raw_name = str(item.get("path") or item.get("name") or f"file-{index}")
        safe_path = _safe_relative_path(raw_name)
        if not safe_path:
            skipped.append({"path": raw_name, "reason": "invalid_path"})
            continue

        text = item.get("text")
        content_base64 = item.get("content_base64")
        try:
            if text is not None:
                data = str(text).encode("utf-8")
            elif content_base64:
                data = base64.b64decode(str(content_base64), validate=True)
            else:
                skipped.append({"path": raw_name, "reason": "missing_content"})
                continue
        except Exception:
            skipped.append({"path": raw_name, "reason": "decode_failed"})
            continue

        if len(data) > MAX_INLINE_BYTES:
            skipped.append({"path": raw_name, "reason": "too_large"})
            continue

        target = (root / safe_path).resolve()
        if root not in target.parents:
            skipped.append({"path": raw_name, "reason": "path_escape"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        file_id = f"file_{hashlib.sha256((upload_id + ':' + safe_path).encode('utf-8')).hexdigest()[:16]}"
        attachment = {
            "file_id": file_id,
            "name": Path(safe_path).name,
            "mime_type": str(item.get("mime_type") or item.get("type") or _guess_mime_type(target)),
            "uri": str(target),
            "size_bytes": len(data),
            "purpose": purpose,
            "relative_path": safe_path,
        }
        attachments.append(attachment)
        if target.suffix.lower() in TEXT_SUFFIXES:
            documents.append(
                {
                    "path": safe_path,
                    "text": data.decode("utf-8", errors="replace"),
                    "metadata": {
                        "file_id": file_id,
                        "upload_id": upload_id,
                        "mime_type": attachment["mime_type"],
                        "size_bytes": len(data),
                    },
                }
            )

    metadata = {"upload_id": upload_id, "attachments": attachments, "skipped": skipped, "created_at": time.time()}
    (root / "upload_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return FileIngestReport(upload_id=upload_id, root=str(root), attachments=attachments, documents=documents, skipped=skipped)


def _safe_relative_path(raw_path: str) -> str:
    parts: list[str] = []
    for part in str(raw_path).replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", part).strip(" .")
        if safe:
            parts.append(safe[:120])
    return "/".join(parts[-8:])


def _guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".rst", ".log", ".py", ".yaml", ".yml", ".json", ".jsonl", ".csv"}:
        return "text/plain"
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix in {".pdf"}:
        return "application/pdf"
    return "application/octet-stream"
