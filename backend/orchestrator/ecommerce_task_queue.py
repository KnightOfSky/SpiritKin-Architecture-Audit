from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STATE_VERSION = 1
DEFAULT_TEMP_TTL_HOURS = 24
DEFAULT_STATE_DIR = "state/ecommerce_tasks"
QUEUE_FILE_NAME = "queue.json"
EVENTS_FILE_NAME = "events.jsonl"
ARTIFACT_DIR_NAME = "artifacts"
TEST_LINK_MARKERS = ("test", "smoke", "localtest", "self-test")

STATUS_LINK_RECEIVED = "link_received"
STATUS_IMAGE_QUEUED = "image_queued"
STATUS_PROBE_CAPTURED = "probe_captured"
STATUS_PRODUCTDATA_READY = "productdata_ready"
STATUS_PRODUCTDATA_READY_WITH_GAPS = "productdata_ready_with_gaps"
STATUS_WORKFLOW_COMPLETE = "workflow_complete"
STATUS_WORKFLOW_BLOCKED = "workflow_blocked"
STATUS_WORKFLOW_REVIEW = "workflow_review"
STATUS_WORKFLOW_WAITING = "workflow_waiting"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sha12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def safe_name(value: str, fallback: str = "artifact") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    cleaned = cleaned.strip("._")
    return cleaned[:80] or fallback


def resolve_project_root(project_root: str | Path | None = None) -> Path:
    return (Path(project_root) if project_root else Path.cwd()).resolve()


def as_path(value: str | Path, *, project_root: str | Path | None = None) -> Path:
    root = resolve_project_root(project_root)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def display_path(path: str | Path, *, project_root: str | Path | None = None) -> str:
    root = resolve_project_root(project_root)
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolve_state_dir(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return as_path(state_dir or DEFAULT_STATE_DIR, project_root=project_root)


def queue_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / QUEUE_FILE_NAME


def events_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / EVENTS_FILE_NAME


def artifacts_dir(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / ARTIFACT_DIR_NAME


def load_queue(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> dict[str, Any]:
    path = queue_path(state_dir, project_root=project_root)
    if not path.exists():
        return {"version": STATE_VERSION, "updated_at": utc_now(), "tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    return {
        "version": int(data.get("version") or STATE_VERSION),
        "updated_at": str(data.get("updated_at") or utc_now()),
        "tasks": [task for task in tasks if isinstance(task, dict)],
    }


def save_queue(queue: dict[str, Any], state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> None:
    state = resolve_state_dir(state_dir, project_root=project_root)
    state.mkdir(parents=True, exist_ok=True)
    queue["updated_at"] = utc_now()
    tmp = queue_path(state, project_root=project_root).with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(queue_path(state, project_root=project_root))


def append_event(
    event: dict[str, Any],
    state_dir: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
) -> None:
    state = resolve_state_dir(state_dir, project_root=project_root)
    state.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_id": f"evt_{int(time.time() * 1000)}_{sha12(json.dumps(event, ensure_ascii=False, sort_keys=True))}",
        "at": utc_now(),
        **event,
    }
    with events_path(state, project_root=project_root).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def task_by_id(queue: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return next((task for task in queue.get("tasks", []) if task.get("id") == task_id), None)


def task_by_input(queue: dict[str, Any], key: str, value: str) -> dict[str, Any] | None:
    for task in queue.get("tasks", []):
        inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
        if inputs.get(key) == value:
            return task
    return None


def add_history(task: dict[str, Any], event_type: str, payload: dict[str, Any] | None = None) -> None:
    history = task.setdefault("history", [])
    history.append({"at": utc_now(), "type": event_type, "payload": payload or {}})
    if len(history) > 50:
        del history[:-50]
    task["updated_at"] = utc_now()


def new_task(task_id: str, task_type: str, status: str, source: str, inputs: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": task_id,
        "type": task_type,
        "status": status,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "inputs": inputs,
        "artifacts": [],
        "history": [{"at": now, "type": "created", "payload": {"status": status, "source": source}}],
        "checks": {},
    }


def classify_link(link: str) -> str:
    if "yangkeduo.com" in link or "pinduoduo.com" in link:
        return "pdd_web_link"
    return "unknown"


def is_test_link_event(row: dict[str, Any], link: str) -> bool:
    source = str(row.get("source") or "").lower()
    value = link.lower()
    return any(marker in source or marker in value for marker in TEST_LINK_MARKERS)


def extract_pdd_link(value: object) -> str:
    import re

    if value is None:
        return ""
    text = str(value).strip()
    match = re.search(r"https?://[^\s\"'<>]*\b(?:yangkeduo|pinduoduo)\.com/[^\s\"'<>]*", text)
    return match.group(0) if match else ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def ingest_mobile_links(
    *,
    links_jsonl: str | Path = "state/mobile-links/links.jsonl",
    latest_link: str | Path = "state/mobile-links/latest-link.txt",
    include_latest: bool = False,
    include_test_links: bool = False,
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    queue = load_queue(state_dir, project_root=project_root)
    rows = read_jsonl(as_path(links_jsonl, project_root=project_root))
    if include_latest:
        latest = as_path(latest_link, project_root=project_root)
        if latest.exists():
            rows.append({"link": latest.read_text(encoding="utf-8").strip(), "source": "latest-link.txt"})

    created: list[str] = []
    updated: list[str] = []
    ignored = 0
    for row in rows:
        link = extract_pdd_link(row.get("link") or row.get("text") or "")
        if not link:
            ignored += 1
            continue
        if not include_test_links and is_test_link_event(row, link):
            ignored += 1
            append_event(
                {"type": "mobile_link_ignored", "task_id": None, "payload": {"reason": "test_link", "link": link, "receiver_event": row}},
                state_dir,
                project_root=project_root,
            )
            continue
        event_payload = {"link": link, "link_type": classify_link(link), "receiver_event": row}
        existing = task_by_input(queue, "link", link)
        if existing:
            add_history(existing, "mobile_link_seen", event_payload)
            existing["status"] = existing.get("status") or STATUS_LINK_RECEIVED
            updated.append(str(existing["id"]))
            append_event({"type": "mobile_link_seen", "task_id": existing["id"], "payload": event_payload}, state_dir, project_root=project_root)
            continue
        task_id = f"link_{sha12(link)}"
        task = new_task(
            task_id,
            "pdd_product_link",
            STATUS_LINK_RECEIVED,
            "mobile-link-bridge",
            {"link": link, "link_type": classify_link(link), "received_at": row.get("receivedAt") or row.get("received_at") or utc_now()},
        )
        add_history(task, "mobile_link_ingested", event_payload)
        queue["tasks"].append(task)
        created.append(task_id)
        append_event({"type": "mobile_link_ingested", "task_id": task_id, "payload": event_payload}, state_dir, project_root=project_root)

    save_queue(queue, state_dir, project_root=project_root)
    return {"created": created, "updated": updated, "ignored": ignored, "task_count": len(queue["tasks"])}


def artifact_target(state_dir: Path, task_id: str, stage: str, source: Path) -> Path:
    target_dir = state_dir / ARTIFACT_DIR_NAME / safe_name(task_id, "task") / safe_name(stage, "stage")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name(source.name, "artifact")
    if not target.exists():
        return target
    stem = target.stem or "artifact"
    suffix = target.suffix
    for index in range(2, 1000):
        candidate = target_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    return target_dir / f"{stem}_{int(time.time())}{suffix}"


def copy_artifact(
    task: dict[str, Any],
    source_path: str | Path,
    *,
    kind: str,
    stage: str,
    temporary: bool = False,
    ttl_hours: int = DEFAULT_TEMP_TTL_HOURS,
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir, project_root=project_root)
    source = as_path(source_path, project_root=project_root)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(str(source))
    target = artifact_target(state, str(task["id"]), stage, source)
    shutil.copy2(source, target)
    artifact = {
        "kind": kind,
        "path": display_path(target, project_root=project_root),
        "source_path": display_path(source, project_root=project_root),
        "created_at": utc_now(),
        "temporary": bool(temporary),
    }
    if temporary:
        artifact["expires_at"] = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat()
        artifact["ttl_hours"] = ttl_hours
    task.setdefault("artifacts", []).append(artifact)
    return artifact


def record_existing_artifact(
    task: dict[str, Any],
    path: str | Path,
    *,
    kind: str,
    temporary: bool = False,
    ttl_hours: int = DEFAULT_TEMP_TTL_HOURS,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved = as_path(path, project_root=project_root)
    artifact = {
        "kind": kind,
        "path": display_path(resolved, project_root=project_root),
        "created_at": utc_now(),
        "temporary": bool(temporary),
    }
    if temporary:
        artifact["expires_at"] = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat()
        artifact["ttl_hours"] = ttl_hours
    task.setdefault("artifacts", []).append(artifact)
    return artifact


def enqueue_image_task(
    *,
    image: str | Path,
    source: str = "manual",
    title: str = "",
    task_id: str = "",
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    image_path = as_path(image, project_root=project_root)
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    signature = f"{image_path}|{image_path.stat().st_size}|{int(image_path.stat().st_mtime)}"
    resolved_task_id = task_id or f"image_{sha12(signature)}"
    queue = load_queue(state_dir, project_root=project_root)
    existing = task_by_id(queue, resolved_task_id)
    if existing:
        add_history(existing, "image_enqueue_seen", {"image": display_path(image_path, project_root=project_root)})
        save_queue(queue, state_dir, project_root=project_root)
        return {"task": existing, "created": False}

    task = new_task(
        resolved_task_id,
        "source_image_upload",
        STATUS_IMAGE_QUEUED,
        source,
        {"original_image_path": display_path(image_path, project_root=project_root), "title": title},
    )
    artifact = copy_artifact(task, image_path, kind="source_image", stage="input", state_dir=state_dir, project_root=project_root)
    task["inputs"]["source_image_path"] = artifact["path"]
    add_history(task, "source_image_enqueued", {"artifact": artifact})
    queue["tasks"].append(task)
    save_queue(queue, state_dir, project_root=project_root)
    append_event({"type": "source_image_enqueued", "task_id": resolved_task_id, "payload": {"artifact": artifact}}, state_dir, project_root=project_root)
    return {"task": task, "created": True}


def attach_probe_artifacts(
    *,
    task_id: str,
    probe_result: str | Path,
    screenshots: list[str | Path] | None = None,
    keep_screenshots: bool = False,
    ttl_hours: int = DEFAULT_TEMP_TTL_HOURS,
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    queue = load_queue(state_dir, project_root=project_root)
    task = task_by_id(queue, task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    artifacts = [
        copy_artifact(task, probe_result, kind="probe_result_json", stage="probe", state_dir=state_dir, project_root=project_root)
    ]
    for screenshot in screenshots or []:
        artifacts.append(
            copy_artifact(
                task,
                screenshot,
                kind="ocr_screenshot",
                stage="probe",
                temporary=not keep_screenshots,
                ttl_hours=ttl_hours,
                state_dir=state_dir,
                project_root=project_root,
            )
        )
    task["status"] = STATUS_PROBE_CAPTURED
    add_history(task, "probe_artifacts_attached", {"artifacts": artifacts})
    save_queue(queue, state_dir, project_root=project_root)
    append_event({"type": "probe_artifacts_attached", "task_id": task_id, "payload": {"artifacts": artifacts}}, state_dir, project_root=project_root)
    return {"task": task, "artifacts": artifacts}


def attach_productdata_artifact(
    *,
    task_id: str,
    product_data_json: str | Path,
    control_plane_artifact_id: str = "",
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    queue = load_queue(state_dir, project_root=project_root)
    task = task_by_id(queue, task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    source = as_path(product_data_json, project_root=project_root)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(str(source))
    product = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(product, dict):
        raise ValueError("browser extension productData must be a JSON object")
    listing_gate = product.get("listingGate") if isinstance(product.get("listingGate"), dict) else {}
    if not listing_gate:
        listing_gate = {"ok": False, "missing": ["listingGate"], "checks": {}}
    existing = next(
        (
            item
            for item in task.get("artifacts") or []
            if isinstance(item, dict)
            and control_plane_artifact_id
            and item.get("control_plane_artifact_id") == control_plane_artifact_id
        ),
        None,
    )
    artifact = existing or copy_artifact(
        task,
        source,
        kind="browser_extension_productdata_json",
        stage="productdata",
        state_dir=state_dir,
        project_root=project_root,
    )
    artifact["control_plane_artifact_id"] = control_plane_artifact_id
    artifact["source"] = "browser_extension"
    task["checks"] = {"listingGate": listing_gate}
    task["status"] = STATUS_PRODUCTDATA_READY if listing_gate.get("ok") else STATUS_PRODUCTDATA_READY_WITH_GAPS
    add_history(
        task,
        "browser_extension_productdata_attached",
        {"artifact": artifact, "listingGateOk": bool(listing_gate.get("ok"))},
    )
    save_queue(queue, state_dir, project_root=project_root)
    append_event(
        {
            "type": "browser_extension_productdata_attached",
            "task_id": task_id,
            "payload": {"artifact": artifact, "listingGateOk": bool(listing_gate.get("ok"))},
        },
        state_dir,
        project_root=project_root,
    )
    return {"task": task, "artifact": artifact, "validation": {"listingGate": listing_gate}}


def assert_under_artifacts(path: Path, state_dir: Path) -> None:
    resolved = path.resolve()
    allowed_root = (state_dir / ARTIFACT_DIR_NAME).resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"refusing to clean artifact outside {allowed_root}: {resolved}")


def cleanup_temporary_artifacts(
    *,
    older_than_hours: int = DEFAULT_TEMP_TTL_HOURS,
    dry_run: bool = False,
    state_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir, project_root=project_root)
    queue = load_queue(state, project_root=project_root)
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    deleted: list[str] = []
    skipped: list[str] = []
    for task in queue.get("tasks", []):
        task_deleted: list[str] = []
        for artifact in task.get("artifacts") or []:
            if not isinstance(artifact, dict) or not artifact.get("temporary") or artifact.get("deleted_at"):
                continue
            created_at = parse_time(str(artifact.get("created_at") or ""))
            expires_at = parse_time(str(artifact.get("expires_at") or "")) if artifact.get("expires_at") else None
            expired = created_at <= cutoff or (expires_at is not None and expires_at <= datetime.now(UTC))
            if not expired:
                skipped.append(str(artifact.get("path") or ""))
                continue
            path = as_path(str(artifact.get("path") or ""), project_root=project_root)
            assert_under_artifacts(path, state)
            if path.exists() and path.is_file():
                deleted_path = display_path(path, project_root=project_root)
                if not dry_run:
                    path.unlink()
                    artifact["deleted_at"] = utc_now()
                deleted.append(deleted_path)
                task_deleted.append(deleted_path)
            else:
                if not dry_run:
                    artifact["deleted_at"] = artifact.get("deleted_at") or utc_now()
                skipped.append(display_path(path, project_root=project_root))
        if task_deleted:
            add_history(task, "temporary_artifact_cleanup", {"deleted_count": len(task_deleted), "dry_run": dry_run})
    if not dry_run:
        save_queue(queue, state, project_root=project_root)
    append_event({"type": "temporary_artifact_cleanup", "task_id": None, "payload": {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}}, state, project_root=project_root)
    return {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}


def status(*, state_dir: str | Path | None = None, project_root: str | Path | None = None) -> dict[str, Any]:
    queue = load_queue(state_dir, project_root=project_root)
    tasks = queue.get("tasks") or []
    return {
        "state_dir": str(resolve_state_dir(state_dir, project_root=project_root)),
        "task_count": len(tasks),
        "status_counts": dict(sorted(Counter(str(task.get("status") or "unknown") for task in tasks).items())),
        "type_counts": dict(sorted(Counter(str(task.get("type") or "unknown") for task in tasks).items())),
        "updated_at": queue.get("updated_at"),
    }
