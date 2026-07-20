from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.orchestrator import ecommerce_task_queue

DEFAULT_MOBILE_LINK_DIR = "state/mobile-links"
LINKS_FILE_NAME = "links.jsonl"
LATEST_FILE_NAME = "latest-link.txt"
PDD_WEB_LINK_RE = re.compile(r"https?://[^\s\"'<>]*\b(?:yangkeduo|pinduoduo)\.com/[^\s\"'<>]*")


class MobileLinkError(ValueError):
    def __init__(self, message: str, *, error_code: str = "invalid_mobile_link"):
        super().__init__(message)
        self.error_code = error_code


def extract_pdd_link(text: object) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    match = PDD_WEB_LINK_RE.search(value)
    return match.group(0) if match else ""


def is_supported_pdd_link(link: str) -> bool:
    return bool(extract_pdd_link(link) == link.strip())


def mobile_link_paths(*, project_root: str | Path | None = None, link_dir: str | Path = DEFAULT_MOBILE_LINK_DIR) -> dict[str, Path]:
    root = Path(project_root or Path.cwd()).resolve()
    directory = Path(link_dir)
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    return {
        "directory": directory,
        "links_jsonl": directory / LINKS_FILE_NAME,
        "latest_link": directory / LATEST_FILE_NAME,
    }


def record_mobile_pdd_link(
    payload: dict[str, Any],
    *,
    project_root: str | Path | None = None,
    link_dir: str | Path = DEFAULT_MOBILE_LINK_DIR,
    client: str = "",
    ingest_to_queue: bool = True,
) -> dict[str, Any]:
    link = extract_pdd_link(payload.get("link") or payload.get("text") or payload.get("share_text") or payload.get("raw_text") or "")
    if not link or not is_supported_pdd_link(link):
        raise MobileLinkError("missing pdd link", error_code="missing_pdd_link")

    paths = mobile_link_paths(project_root=project_root, link_dir=link_dir)
    paths["directory"].mkdir(parents=True, exist_ok=True)
    event = {
        "link": link,
        "source": payload.get("source") or "android-bridge",
        "receivedAt": datetime.now(UTC).isoformat(),
        "client": client or payload.get("client") or "",
    }
    device_id = str(payload.get("device_id") or "").strip()
    if device_id:
        event["device_id"] = device_id
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = metadata

    with paths["links_jsonl"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    paths["latest_link"].write_text(link + "\n", encoding="utf-8")

    ingest_result: dict[str, Any] | None = None
    if ingest_to_queue:
        ingest_result = ecommerce_task_queue.ingest_mobile_links(
            links_jsonl=paths["links_jsonl"],
            latest_link=paths["latest_link"],
            include_latest=False,
            include_test_links=bool(payload.get("include_test_links")),
            project_root=project_root,
        )

    return {
        "link": link,
        "link_type": ecommerce_task_queue.classify_link(link),
        "event": event,
        "paths": {key: str(value) for key, value in paths.items()},
        "ingest": ingest_result,
    }
