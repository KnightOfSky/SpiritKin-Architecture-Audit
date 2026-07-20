"""Local ecommerce RPA task queue and artifact lifecycle manager.

This module is the project-owned boundary around phone-shared links, product
image upload jobs, OCR/probe outputs, and productData adapter outputs. It may
use AutoProcess-derived field contracts as documentation, but it does not import
or execute AutoProcess runtime code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "state" / "ecommerce_tasks"
DEFAULT_MOBILE_LINKS = ROOT / "state" / "mobile-links" / "links.jsonl"
DEFAULT_LATEST_LINK = ROOT / "state" / "mobile-links" / "latest-link.txt"

QUEUE_FILE_NAME = "queue.json"
EVENTS_FILE_NAME = "events.jsonl"
ARTIFACT_DIR_NAME = "artifacts"
SKILL_CANDIDATE_FILE_NAME = "skill_candidates.jsonl"
EVOLUTION_PROPOSAL_FILE_NAME = "evolution_proposals.jsonl"
EVOLUTION_DECISION_FILE_NAME = "evolution_decisions.jsonl"

STATE_VERSION = 1
DEFAULT_TEMP_TTL_HOURS = 24
TEST_LINK_MARKERS = ("test", "smoke", "localtest", "self-test")
EVOLUTION_SOURCE_TYPES = {
    "paper",
    "video",
    "training_package",
    "skill_candidate",
    "workflow_candidate",
    "knowledge_entry",
    "prompt_update",
    "model_eval",
}
EVOLUTION_DECISIONS = {"approved", "rejected", "needs_changes", "superseded"}
EVOLUTION_RISK_LEVELS = {"low", "medium", "high"}
EVOLUTION_REVIEW_GATES = {
    "paper": "knowledge_review",
    "video": "knowledge_review",
    "training_package": "cloud_eval_review",
    "skill_candidate": "core_review",
    "workflow_candidate": "core_review",
    "knowledge_entry": "knowledge_review",
    "prompt_update": "prompt_review",
    "model_eval": "cloud_eval_review",
}
EVOLUTION_REQUIRED_REVIEWS = {
    "paper": ["human"],
    "video": ["human"],
    "training_package": ["cloud_evaluator", "human"],
    "skill_candidate": ["core_review", "human"],
    "workflow_candidate": ["core_review", "human"],
    "knowledge_entry": ["human"],
    "prompt_update": ["human"],
    "model_eval": ["cloud_evaluator"],
}

STATUS_LINK_RECEIVED = "link_received"
STATUS_IMAGE_QUEUED = "image_queued"
STATUS_PROBE_CAPTURED = "probe_captured"
STATUS_PRODUCTDATA_READY = "productdata_ready"
STATUS_PRODUCTDATA_READY_WITH_GAPS = "productdata_ready_with_gaps"


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


def as_path(value: str | Path, *, root: Path = ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def display_path(path: str | Path, *, root: Path = ROOT) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def queue_path(state_dir: Path) -> Path:
    return state_dir / QUEUE_FILE_NAME


def events_path(state_dir: Path) -> Path:
    return state_dir / EVENTS_FILE_NAME


def artifacts_dir(state_dir: Path) -> Path:
    return state_dir / ARTIFACT_DIR_NAME


def evolution_proposals_path(state_dir: Path) -> Path:
    return state_dir / EVOLUTION_PROPOSAL_FILE_NAME


def evolution_decisions_path(state_dir: Path) -> Path:
    return state_dir / EVOLUTION_DECISION_FILE_NAME


def load_queue(state_dir: Path) -> dict[str, Any]:
    path = queue_path(state_dir)
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


def save_queue(state_dir: Path, queue: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    queue["updated_at"] = utc_now()
    tmp = queue_path(state_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(queue_path(state_dir))


def append_event(state_dir: Path, event: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_id": f"evt_{int(time.time() * 1000)}_{sha12(json.dumps(event, ensure_ascii=False, sort_keys=True))}",
        "at": utc_now(),
        **event,
    }
    with events_path(state_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def task_by_id(queue: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in queue.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


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


def artifact_target(state_dir: Path, task_id: str, stage: str, source: Path) -> Path:
    target_dir = artifacts_dir(state_dir) / safe_name(task_id, "task") / safe_name(stage, "stage")
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
    state_dir: Path,
    task: dict[str, Any],
    source_path: str | Path,
    *,
    kind: str,
    stage: str,
    temporary: bool = False,
    ttl_hours: int = DEFAULT_TEMP_TTL_HOURS,
    root: Path = ROOT,
) -> dict[str, Any]:
    source = as_path(source_path, root=root)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(str(source))
    target = artifact_target(state_dir, str(task["id"]), stage, source)
    shutil.copy2(source, target)
    created_at = utc_now()
    artifact = {
        "kind": kind,
        "path": display_path(target, root=root),
        "source_path": display_path(source, root=root),
        "created_at": created_at,
        "temporary": bool(temporary),
    }
    if temporary:
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
        artifact["expires_at"] = expires_at.isoformat()
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
    root: Path = ROOT,
) -> dict[str, Any]:
    resolved = as_path(path, root=root)
    created_at = utc_now()
    artifact = {
        "kind": kind,
        "path": display_path(resolved, root=root),
        "created_at": created_at,
        "temporary": bool(temporary),
    }
    if temporary:
        artifact["expires_at"] = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat()
        artifact["ttl_hours"] = ttl_hours
    task.setdefault("artifacts", []).append(artifact)
    return artifact


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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_choice(value: str, allowed: set[str], *, field: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"invalid {field}: {value}; expected one of: {choices}")
    return normalized


def build_evolution_proposal(
    *,
    source_type: str,
    title: str,
    summary: str,
    source_ref: str = "",
    target_area: str = "general",
    risk_level: str = "medium",
    submitted_by: str = "local_assistant",
    evidence: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_gate: str = "",
) -> dict[str, Any]:
    normalized_source_type = normalize_choice(source_type, EVOLUTION_SOURCE_TYPES, field="source_type")
    normalized_risk = normalize_choice(risk_level, EVOLUTION_RISK_LEVELS, field="risk_level")
    clean_title = str(title or "").strip()
    clean_summary = str(summary or "").strip()
    if not clean_title:
        raise ValueError("title is required")
    if not clean_summary:
        raise ValueError("summary is required")
    clean_source_ref = str(source_ref or "").strip()
    source_seed = json.dumps(
        {
            "source_type": normalized_source_type,
            "source_ref": clean_source_ref,
            "title": clean_title,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    now = utc_now()
    proposal_id = f"evo_{normalized_source_type}_{sha12(source_seed)}"
    clean_evidence = [str(item).strip() for item in (evidence or []) if str(item).strip()]
    clean_metadata = dict(metadata or {})
    clean_metadata.setdefault("activation_state", "inactive_candidate")
    clean_metadata.setdefault("live_code_change_allowed", False)
    return {
        "proposal_id": proposal_id,
        "created_at": now,
        "status": "pending_review",
        "source_type": normalized_source_type,
        "source_ref": clean_source_ref,
        "source_hash": hashlib.sha256(source_seed.encode("utf-8")).hexdigest(),
        "title": clean_title,
        "summary": clean_summary,
        "target_area": str(target_area or "general").strip() or "general",
        "risk_level": normalized_risk,
        "review_gate": str(review_gate or EVOLUTION_REVIEW_GATES[normalized_source_type]),
        "required_reviews": list(EVOLUTION_REQUIRED_REVIEWS[normalized_source_type]),
        "submitted_by": str(submitted_by or "local_assistant").strip() or "local_assistant",
        "evidence": clean_evidence,
        "metadata": clean_metadata,
    }


def load_evolution_proposals(state_dir: Path) -> list[dict[str, Any]]:
    return read_jsonl(evolution_proposals_path(state_dir))


def load_evolution_decisions(state_dir: Path) -> list[dict[str, Any]]:
    return read_jsonl(evolution_decisions_path(state_dir))


def evolution_proposal_by_id(state_dir: Path, proposal_id: str) -> dict[str, Any] | None:
    for proposal in load_evolution_proposals(state_dir):
        if proposal.get("proposal_id") == proposal_id:
            return proposal
    return None


def enqueue_evolution_proposal(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    source_type: str,
    title: str,
    summary: str,
    source_ref: str = "",
    target_area: str = "general",
    risk_level: str = "medium",
    submitted_by: str = "local_assistant",
    evidence: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_gate: str = "",
) -> dict[str, Any]:
    proposal = build_evolution_proposal(
        source_type=source_type,
        title=title,
        summary=summary,
        source_ref=source_ref,
        target_area=target_area,
        risk_level=risk_level,
        submitted_by=submitted_by,
        evidence=evidence,
        metadata=metadata,
        review_gate=review_gate,
    )
    existing = evolution_proposal_by_id(state_dir, str(proposal["proposal_id"]))
    if existing:
        append_event(
            state_dir,
            {
                "type": "evolution_proposal_seen",
                "task_id": None,
                "payload": {"proposal_id": existing["proposal_id"], "source_type": existing.get("source_type")},
            },
        )
        return {"proposal": existing, "created": False}
    append_jsonl(evolution_proposals_path(state_dir), proposal)
    append_event(
        state_dir,
        {
            "type": "evolution_proposal_queued",
            "task_id": None,
            "payload": {"proposal_id": proposal["proposal_id"], "source_type": proposal["source_type"]},
        },
    )
    return {"proposal": proposal, "created": True}


def decide_evolution_proposal(
    proposal_id: str,
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    decision: str,
    reviewer: str,
    rationale: str,
    conditions: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    proposal = evolution_proposal_by_id(state_dir, proposal_id)
    if not proposal:
        raise KeyError(f"unknown evolution proposal: {proposal_id}")
    normalized_decision = normalize_choice(decision, EVOLUTION_DECISIONS, field="decision")
    clean_reviewer = str(reviewer or "").strip()
    clean_rationale = str(rationale or "").strip()
    if not clean_reviewer:
        raise ValueError("reviewer is required")
    if not clean_rationale:
        raise ValueError("rationale is required")
    row = {
        "decision_id": f"evod_{sha12(f'{proposal_id}|{normalized_decision}|{clean_reviewer}|{utc_now()}')}",
        "proposal_id": proposal_id,
        "at": utc_now(),
        "decision": normalized_decision,
        "status": normalized_decision,
        "reviewer": clean_reviewer,
        "rationale": clean_rationale,
        "conditions": [str(item).strip() for item in (conditions or []) if str(item).strip()],
        "evidence": [str(item).strip() for item in (evidence or []) if str(item).strip()],
    }
    append_jsonl(evolution_decisions_path(state_dir), row)
    append_event(
        state_dir,
        {
            "type": "evolution_decision_recorded",
            "task_id": None,
            "payload": {
                "proposal_id": proposal_id,
                "decision": normalized_decision,
                "reviewer": clean_reviewer,
            },
        },
    )
    return {"proposal_id": proposal_id, "decision": row}


def build_evolution_queue(state_dir: Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    proposals_by_id: dict[str, dict[str, Any]] = {}
    for proposal in load_evolution_proposals(state_dir):
        proposal_id = str(proposal.get("proposal_id") or "")
        if not proposal_id or proposal_id in proposals_by_id:
            continue
        snapshot = dict(proposal)
        snapshot["status"] = "pending_review"
        snapshot["last_decision"] = None
        proposals_by_id[proposal_id] = snapshot

    for decision in load_evolution_decisions(state_dir):
        proposal_id = str(decision.get("proposal_id") or "")
        if proposal_id not in proposals_by_id:
            continue
        status = str(decision.get("status") or decision.get("decision") or "").strip().lower()
        if status not in EVOLUTION_DECISIONS:
            continue
        proposals_by_id[proposal_id]["status"] = status
        proposals_by_id[proposal_id]["updated_at"] = decision.get("at")
        proposals_by_id[proposal_id]["last_decision"] = decision

    proposals = sorted(
        proposals_by_id.values(),
        key=lambda item: str(item.get("created_at") or ""),
    )
    status_counts = Counter(str(item.get("status") or "unknown") for item in proposals)
    source_counts = Counter(str(item.get("source_type") or "unknown") for item in proposals)
    pending = [item for item in proposals if item.get("status") == "pending_review"]
    return {
        "proposal_count": len(proposals),
        "pending_count": len(pending),
        "status_counts": dict(sorted(status_counts.items())),
        "source_type_counts": dict(sorted(source_counts.items())),
        "proposals": proposals,
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
    try:
        from scripts.mobile_link_receiver import extract_pdd_link as receiver_extract
    except ModuleNotFoundError:
        from mobile_link_receiver import extract_pdd_link as receiver_extract

    return receiver_extract(value)


def ensure_mobile_link_task(
    link: str,
    *,
    receiver_event: dict[str, Any] | None = None,
    state_dir: Path = DEFAULT_STATE_DIR,
) -> dict[str, Any]:
    normalized = extract_pdd_link(link)
    if not normalized or normalized != str(link or "").strip():
        raise ValueError("a PDD web link is required")
    queue = load_queue(state_dir)
    event = dict(receiver_event or {})
    event_payload = {"link": normalized, "link_type": "pdd_web_link", "receiver_event": event}
    existing = task_by_input(queue, "link", normalized)
    if existing:
        add_history(existing, "mobile_link_seen", event_payload)
        save_queue(state_dir, queue)
        append_event(state_dir, {"type": "mobile_link_seen", "task_id": existing["id"], "payload": event_payload})
        return {"task": existing, "created": False}
    task_id = f"link_{sha12(normalized)}"
    task = new_task(
        task_id,
        "pdd_product_link",
        STATUS_LINK_RECEIVED,
        "mobile-link-bridge",
        {
            "link": normalized,
            "link_type": "pdd_web_link",
            "received_at": event.get("receivedAt") or event.get("received_at") or utc_now(),
        },
    )
    add_history(task, "mobile_link_ingested", event_payload)
    queue["tasks"].append(task)
    save_queue(state_dir, queue)
    append_event(state_dir, {"type": "mobile_link_ingested", "task_id": task_id, "payload": event_payload})
    return {"task": task, "created": True}


def ingest_mobile_links(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    links_path: Path = DEFAULT_MOBILE_LINKS,
    latest_path: Path = DEFAULT_LATEST_LINK,
    include_latest: bool = False,
    include_test_links: bool = False,
    root: Path = ROOT,
) -> dict[str, Any]:
    queue = load_queue(state_dir)
    rows = read_jsonl(as_path(links_path, root=root))
    if include_latest:
        latest = as_path(latest_path, root=root)
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
                state_dir,
                {
                    "type": "mobile_link_ignored",
                    "task_id": None,
                    "payload": {"reason": "test_link", "link": link, "receiver_event": row},
                },
            )
            continue
        existing = task_by_input(queue, "link", link)
        event_payload = {
            "link": link,
            "link_type": classify_link(link),
            "receiver_event": row,
        }
        if existing:
            add_history(existing, "mobile_link_seen", event_payload)
            existing["status"] = existing.get("status") or STATUS_LINK_RECEIVED
            updated.append(str(existing["id"]))
            append_event(state_dir, {"type": "mobile_link_seen", "task_id": existing["id"], "payload": event_payload})
            continue
        task_id = f"link_{sha12(link)}"
        task = new_task(
            task_id,
            "pdd_product_link",
            STATUS_LINK_RECEIVED,
            "mobile-link-bridge",
            {
                "link": link,
                "link_type": classify_link(link),
                "received_at": row.get("receivedAt") or row.get("received_at") or utc_now(),
            },
        )
        add_history(task, "mobile_link_ingested", event_payload)
        queue["tasks"].append(task)
        created.append(task_id)
        append_event(state_dir, {"type": "mobile_link_ingested", "task_id": task_id, "payload": event_payload})

    save_queue(state_dir, queue)
    return {"created": created, "updated": updated, "ignored": ignored, "task_count": len(queue["tasks"])}


def enqueue_image_task(
    image_path: str | Path,
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    source: str = "manual",
    title: str = "",
    task_id: str = "",
    root: Path = ROOT,
) -> dict[str, Any]:
    image = as_path(image_path, root=root)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(str(image))
    signature = f"{image}|{image.stat().st_size}|{int(image.stat().st_mtime)}"
    resolved_task_id = task_id or f"image_{sha12(signature)}"
    queue = load_queue(state_dir)
    existing = task_by_id(queue, resolved_task_id)
    if existing:
        add_history(existing, "image_enqueue_seen", {"image": display_path(image, root=root)})
        save_queue(state_dir, queue)
        append_event(state_dir, {"type": "image_enqueue_seen", "task_id": resolved_task_id, "payload": {"image": display_path(image, root=root)}})
        return {"task": existing, "created": False}

    task = new_task(
        resolved_task_id,
        "source_image_upload",
        STATUS_IMAGE_QUEUED,
        source,
        {"original_image_path": display_path(image, root=root), "title": title},
    )
    artifact = copy_artifact(
        state_dir,
        task,
        image,
        kind="source_image",
        stage="input",
        temporary=False,
        root=root,
    )
    task["inputs"]["source_image_path"] = artifact["path"]
    add_history(task, "source_image_enqueued", {"artifact": artifact})
    queue["tasks"].append(task)
    save_queue(state_dir, queue)
    append_event(state_dir, {"type": "source_image_enqueued", "task_id": resolved_task_id, "payload": {"artifact": artifact}})
    return {"task": task, "created": True}


def attach_probe_artifacts(
    task_id: str,
    *,
    probe_result: str | Path,
    screenshots: list[str | Path] | None = None,
    state_dir: Path = DEFAULT_STATE_DIR,
    temporary_screenshots: bool = True,
    ttl_hours: int = DEFAULT_TEMP_TTL_HOURS,
    root: Path = ROOT,
) -> dict[str, Any]:
    queue = load_queue(state_dir)
    task = task_by_id(queue, task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    artifacts: list[dict[str, Any]] = []
    artifacts.append(
        copy_artifact(
            state_dir,
            task,
            probe_result,
            kind="probe_result_json",
            stage="probe",
            temporary=False,
            root=root,
        )
    )
    for screenshot in screenshots or []:
        artifacts.append(
            copy_artifact(
                state_dir,
                task,
                screenshot,
                kind="ocr_screenshot",
                stage="probe",
                temporary=temporary_screenshots,
                ttl_hours=ttl_hours,
                root=root,
            )
        )
    task["status"] = STATUS_PROBE_CAPTURED
    add_history(task, "probe_artifacts_attached", {"artifacts": artifacts})
    save_queue(state_dir, queue)
    append_event(state_dir, {"type": "probe_artifacts_attached", "task_id": task_id, "payload": {"artifacts": artifacts}})
    return {"task": task, "artifacts": artifacts}


def attach_productdata_artifact(
    task_id: str,
    *,
    product_data_json: str | Path,
    control_plane_artifact_id: str = "",
    state_dir: Path = DEFAULT_STATE_DIR,
    root: Path = ROOT,
) -> dict[str, Any]:
    queue = load_queue(state_dir)
    task = task_by_id(queue, task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    source = as_path(product_data_json, root=root)
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
        state_dir,
        task,
        source,
        kind="browser_extension_productdata_json",
        stage="productdata",
        root=root,
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
    save_queue(state_dir, queue)
    append_event(
        state_dir,
        {
            "type": "browser_extension_productdata_attached",
            "task_id": task_id,
            "payload": {"artifact": artifact, "listingGateOk": bool(listing_gate.get("ok"))},
        },
    )
    return {"task": task, "artifact": artifact, "validation": {"listingGate": listing_gate}}


def _artifact_file_path(artifact: dict[str, Any], *, root: Path) -> Path:
    raw = str(artifact.get("path") or "")
    if not raw:
        raise ValueError("artifact path is empty")
    return as_path(raw, root=root)


def assert_under_artifacts(path: Path, state_dir: Path) -> None:
    resolved = path.resolve()
    allowed_root = artifacts_dir(state_dir).resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"refusing to clean artifact outside {allowed_root}: {resolved}")


def cleanup_temporary_artifacts(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    older_than_hours: int = DEFAULT_TEMP_TTL_HOURS,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict[str, Any]:
    queue = load_queue(state_dir)
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
            path = _artifact_file_path(artifact, root=root)
            assert_under_artifacts(path, state_dir)
            if path.exists() and path.is_file():
                if not dry_run:
                    path.unlink()
                    artifact["deleted_at"] = utc_now()
                deleted_path = display_path(path, root=root)
                deleted.append(deleted_path)
                task_deleted.append(deleted_path)
            else:
                artifact["deleted_at"] = artifact.get("deleted_at") or utc_now()
                skipped.append(display_path(path, root=root))
        if task_deleted:
            add_history(task, "temporary_artifact_cleanup", {"deleted_count": len(task_deleted), "dry_run": dry_run})
    if not dry_run:
        _remove_empty_artifact_dirs(state_dir)
        save_queue(state_dir, queue)
    append_event(state_dir, {"type": "temporary_artifact_cleanup", "task_id": None, "payload": {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}})
    return {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}


def _remove_empty_artifact_dirs(state_dir: Path) -> None:
    root = artifacts_dir(state_dir)
    if not root.exists():
        return
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def build_status(state_dir: Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    queue = load_queue(state_dir)
    tasks = queue.get("tasks") or []
    evolution = build_evolution_queue(state_dir)
    status_counts = Counter(str(task.get("status") or "unknown") for task in tasks)
    type_counts = Counter(str(task.get("type") or "unknown") for task in tasks)
    return {
        "state_dir": str(state_dir.resolve()),
        "task_count": len(tasks),
        "status_counts": dict(sorted(status_counts.items())),
        "type_counts": dict(sorted(type_counts.items())),
        "evolution_proposal_count": evolution["proposal_count"],
        "evolution_pending_count": evolution["pending_count"],
        "evolution_status_counts": evolution["status_counts"],
        "updated_at": queue.get("updated_at"),
    }


def build_skill_candidates() -> list[dict[str, Any]]:
    base_metadata = {
        "status": "candidate",
        "promotion_status": "candidate",
        "owner_agent_id": "ecommerce",
        "owner_domain": "ecommerce",
        "workspace_path": "state/agents/ecommerce/workspace/domain_skills/ecommerce_rpa",
        "source_type": "autoprocess_contract_migration",
        "review_gate": "core_review",
        "managed_scope": "agent",
        "runtime_dependency_policy": "project_local_only",
        "forbidden_runtime_dependencies": ["E:/AutoProcessAP", "AutoProcess runtime services"],
    }
    return [
        {
            "name": "ecommerce.pdd_mobile_link_intake.workflow",
            "description": "把 Android 手机分享回来的拼多多链接写入本项目电商 RPA 任务队列。",
            "trigger_intents": ["拼多多链接入队", "手机链接入队", "pdd link intake", "电商任务队列"],
            "input_schema": {"links_jsonl": "str", "include_latest": "bool"},
            "preconditions": ["mobile-link-bridge receiver 已运行或已有 state/mobile-links/links.jsonl"],
            "steps": [
                {
                    "tool_name": "local.script.run",
                    "arguments": {
                    "command": "python scripts/ecommerce_task_queue.py ingest-mobile-links --include-latest={{include_latest}}"
                    },
                    "description": "调用本项目队列脚本导入手机链接。",
                    "optional": False,
                }
            ],
            "tool_allowlist": ["local.script.run", "file.read", "file.write"],
            "risk_level": "low",
            "confirmation_policy": "risk_based",
            "rollback_strategy": "删除候选任务或追加 cancelled 事件，不删除原始手机链接日志。",
            "success_criteria": ["同一链接只生成一个任务", "保留 receivedAt/client/source 审计信息", "默认跳过 smoke/test/local-self-test 链接"],
            "memory_policy": "record_summary",
            "eval_cases": ["重复导入同一 links.jsonl 时任务数量不增加"],
            "version": "0.1.0",
            "usage_count": 0,
            "metadata": {**base_metadata, "module_id": "pdd_mobile_link_intake"},
        },
        {
            "name": "ecommerce.browser_extension_productdata.workflow",
            "description": "把登录态浏览器扩展生成的 productData Artifact 接入电商任务并执行上架完整性门禁。",
            "trigger_intents": ["浏览器商品数据接入", "productData Artifact", "拼多多扩展抓取结果", "上架数据门禁"],
            "input_schema": {
                "task_id": "str",
                "product_data_json": "str",
                "control_plane_artifact_id": "str",
            },
            "preconditions": ["浏览器扩展已在登录态 PDD 页面完成 rawData 抓取"],
            "steps": [
                {
                    "tool_name": "ecommerce.task_queue.attach_productdata",
                    "arguments": {
                        "task_id": "{{task_id}}",
                        "product_data_json": "{{product_data_json}}",
                        "control_plane_artifact_id": "{{control_plane_artifact_id}}",
                    },
                    "description": "挂载扩展 productData Artifact，并同步 listing gate。",
                    "optional": False,
                }
            ],
            "tool_allowlist": ["ecommerce.task_queue.attach_productdata"],
            "risk_level": "medium",
            "confirmation_policy": "risk_based",
            "rollback_strategy": "保留扩展 Artifact 和门禁失败原因，禁止直接发布。",
            "success_criteria": ["productData JSON 进入任务 artifact 生命周期", "listingGate 有结构化结果", "不完整数据不能进入发布阶段"],
            "memory_policy": "record_summary",
            "eval_cases": ["缺少 listingGate 时任务必须进入 productdata_ready_with_gaps"],
            "version": "0.2.0",
            "usage_count": 0,
            "metadata": {**base_metadata, "module_id": "browser_extension_productdata"},
        },
        {
            "name": "ecommerce.ocr_artifact_cleanup.workflow",
            "description": "清理本项目电商任务中的临时 OCR 截图，同时保留 append-only 事件和非临时产物。",
            "trigger_intents": ["清理 OCR 截图", "清理电商临时产物", "ocr cleanup", "产物生命周期"],
            "input_schema": {"older_than_hours": "int", "dry_run": "bool"},
            "preconditions": ["只清理 state/ecommerce_tasks/artifacts 内被标记 temporary=true 的文件"],
            "steps": [
                {
                    "tool_name": "local.script.run",
                    "arguments": {
                        "command": "python scripts/ecommerce_task_queue.py cleanup-temp --older-than-hours={{older_than_hours}} --dry-run={{dry_run}}"
                    },
                    "description": "执行临时 OCR artifact 清理。",
                    "optional": False,
                }
            ],
            "tool_allowlist": ["local.script.run", "file.read", "file.write"],
            "risk_level": "medium",
            "confirmation_policy": "always",
            "rollback_strategy": "dry-run 先列出文件；正式清理后 queue.json 标记 deleted_at，events.jsonl 留痕。",
            "success_criteria": ["不删除 artifacts 根目录外文件", "非 temporary 产物不被删除", "清理事件可审计"],
            "memory_policy": "record_summary",
            "eval_cases": ["外部路径 artifact 被拒绝清理", "dry-run 不删除文件"],
            "version": "0.1.0",
            "usage_count": 0,
            "metadata": {**base_metadata, "module_id": "ocr_artifact_cleanup"},
        },
    ]


def export_skill_candidates(*, state_dir: Path = DEFAULT_STATE_DIR, output: Path | None = None) -> dict[str, Any]:
    target = output or (state_dir / SKILL_CANDIDATE_FILE_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = build_skill_candidates()
    target.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates), encoding="utf-8")
    return {"output": str(target.resolve()), "count": len(candidates), "names": [item["name"] for item in candidates]}


def enqueue_skill_candidates_for_evolution(*, state_dir: Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    created: list[str] = []
    existing: list[str] = []
    proposals: list[dict[str, Any]] = []
    for candidate in build_skill_candidates():
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        evidence = [str(item) for item in candidate.get("eval_cases") or []]
        evidence.extend(str(item) for item in candidate.get("success_criteria") or [])
        result = enqueue_evolution_proposal(
            state_dir=state_dir,
            source_type="skill_candidate",
            title=str(candidate.get("name") or "unnamed skill candidate"),
            summary=str(candidate.get("description") or ""),
            source_ref=str(candidate.get("name") or ""),
            target_area=str(metadata.get("owner_domain") or "ecommerce"),
            risk_level=str(candidate.get("risk_level") or "medium"),
            submitted_by="skill_candidate_export",
            evidence=evidence,
            metadata={
                "skill_name": candidate.get("name"),
                "skill_version": candidate.get("version"),
                "review_gate": metadata.get("review_gate") or "core_review",
                "promotion_status": "candidate",
                "activation_state": "inactive_candidate",
                "tool_allowlist": candidate.get("tool_allowlist") or [],
                "runtime_dependency_policy": metadata.get("runtime_dependency_policy"),
                "forbidden_runtime_dependencies": metadata.get("forbidden_runtime_dependencies") or [],
                "live_code_change_allowed": False,
            },
            review_gate=str(metadata.get("review_gate") or "core_review"),
        )
        proposal_id = str(result["proposal"]["proposal_id"])
        proposals.append(result["proposal"])
        if result["created"]:
            created.append(proposal_id)
        else:
            existing.append(proposal_id)
    return {"created": created, "existing": existing, "count": len(proposals), "proposals": proposals}


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Task queue state directory.")


def parse_bool_arg(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def parse_json_object_arg(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("metadata JSON must be an object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local ecommerce RPA tasks and artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show queue status.")
    add_common_args(status)
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    evolution = sub.add_parser("list-evolution", help="Show governed evolution proposals and decisions.")
    add_common_args(evolution)
    evolution.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    evolution.add_argument("--pending-only", action="store_true", help="Only show pending proposals in text output.")

    ingest = sub.add_parser("ingest-mobile-links", help="Import phone-shared PDD links into the task queue.")
    add_common_args(ingest)
    ingest.add_argument("--links-jsonl", default=str(DEFAULT_MOBILE_LINKS))
    ingest.add_argument("--latest-link", default=str(DEFAULT_LATEST_LINK))
    ingest.add_argument("--include-latest", nargs="?", const=True, default=False, type=parse_bool_arg)
    ingest.add_argument("--include-test-links", nargs="?", const=True, default=False, type=parse_bool_arg)

    image = sub.add_parser("enqueue-image", help="Create a product image upload/search task.")
    add_common_args(image)
    image.add_argument("--image", required=True)
    image.add_argument("--source", default="manual")
    image.add_argument("--title", default="")
    image.add_argument("--task-id", default="")

    probe = sub.add_parser("attach-probe", help="Attach probe OCR JSON and screenshots to a task.")
    add_common_args(probe)
    probe.add_argument("--task-id", required=True)
    probe.add_argument("--probe-result", required=True)
    probe.add_argument("--screenshot", action="append", default=[])
    probe.add_argument("--keep-screenshots", action="store_true", help="Do not mark screenshots as temporary.")
    probe.add_argument("--ttl-hours", type=int, default=DEFAULT_TEMP_TTL_HOURS)

    productdata = sub.add_parser("attach-productdata", help="Attach browser-extension productData JSON to a task.")
    add_common_args(productdata)
    productdata.add_argument("--task-id", required=True)
    productdata.add_argument("--product-data-json", required=True)
    productdata.add_argument("--control-plane-artifact-id", default="")

    cleanup = sub.add_parser("cleanup-temp", help="Clean expired temporary OCR artifacts.")
    add_common_args(cleanup)
    cleanup.add_argument("--older-than-hours", type=int, default=DEFAULT_TEMP_TTL_HOURS)
    cleanup.add_argument("--dry-run", nargs="?", const=True, default=False, type=parse_bool_arg)

    export = sub.add_parser("export-skill-candidates", help="Write SkillSpec-shaped ecommerce RPA candidates.")
    add_common_args(export)
    export.add_argument("--output")

    queue_skills = sub.add_parser("queue-skill-candidates", help="Queue exported Skill candidates for governed evolution review.")
    add_common_args(queue_skills)

    propose = sub.add_parser("propose-evolution", help="Append a governed evolution proposal.")
    add_common_args(propose)
    propose.add_argument("--source-type", required=True, choices=sorted(EVOLUTION_SOURCE_TYPES))
    propose.add_argument("--title", required=True)
    propose.add_argument("--summary", required=True)
    propose.add_argument("--source-ref", default="")
    propose.add_argument("--target-area", default="general")
    propose.add_argument("--risk-level", default="medium", choices=sorted(EVOLUTION_RISK_LEVELS))
    propose.add_argument("--submitted-by", default="local_assistant")
    propose.add_argument("--evidence", action="append", default=[])
    propose.add_argument("--metadata-json", default="{}")
    propose.add_argument("--review-gate", default="")

    decide = sub.add_parser("decide-evolution", help="Append a decision for a governed evolution proposal.")
    add_common_args(decide)
    decide.add_argument("--proposal-id", required=True)
    decide.add_argument("--decision", required=True, choices=sorted(EVOLUTION_DECISIONS))
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--rationale", required=True)
    decide.add_argument("--condition", action="append", default=[])
    decide.add_argument("--evidence", action="append", default=[])

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    state_dir = as_path(args.state_dir)

    if args.command == "status":
        result = build_status(state_dir)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"state_dir: {result['state_dir']}")
            print(f"task_count: {result['task_count']}")
            print("status_counts:")
            for status, count in result["status_counts"].items():
                print(f"  {status}: {count}")
            print(f"evolution_proposal_count: {result['evolution_proposal_count']}")
            print(f"evolution_pending_count: {result['evolution_pending_count']}")
        return 0
    if args.command == "list-evolution":
        result = build_evolution_queue(state_dir)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"proposal_count: {result['proposal_count']}")
            print(f"pending_count: {result['pending_count']}")
            proposals = result["proposals"]
            if args.pending_only:
                proposals = [item for item in proposals if item.get("status") == "pending_review"]
            for proposal in proposals:
                print(
                    f"- {proposal['proposal_id']} [{proposal.get('status')}] "
                    f"{proposal.get('source_type')}: {proposal.get('title')}"
                )
                print(f"  gate: {proposal.get('review_gate')} risk: {proposal.get('risk_level')}")
        return 0
    if args.command == "ingest-mobile-links":
        result = ingest_mobile_links(
            state_dir=state_dir,
            links_path=as_path(args.links_jsonl),
            latest_path=as_path(args.latest_link),
            include_latest=bool(args.include_latest),
            include_test_links=bool(args.include_test_links),
        )
    elif args.command == "enqueue-image":
        result = enqueue_image_task(
            args.image,
            state_dir=state_dir,
            source=args.source,
            title=args.title,
            task_id=args.task_id,
        )
    elif args.command == "attach-probe":
        result = attach_probe_artifacts(
            args.task_id,
            probe_result=args.probe_result,
            screenshots=list(args.screenshot or []),
            state_dir=state_dir,
            temporary_screenshots=not bool(args.keep_screenshots),
            ttl_hours=int(args.ttl_hours),
        )
    elif args.command == "attach-productdata":
        result = attach_productdata_artifact(
            args.task_id,
            product_data_json=args.product_data_json,
            control_plane_artifact_id=args.control_plane_artifact_id,
            state_dir=state_dir,
        )
    elif args.command == "cleanup-temp":
        result = cleanup_temporary_artifacts(
            state_dir=state_dir,
            older_than_hours=int(args.older_than_hours),
            dry_run=bool(args.dry_run),
        )
    elif args.command == "export-skill-candidates":
        result = export_skill_candidates(
            state_dir=state_dir,
            output=as_path(args.output) if args.output else None,
        )
    elif args.command == "queue-skill-candidates":
        result = enqueue_skill_candidates_for_evolution(state_dir=state_dir)
    elif args.command == "propose-evolution":
        result = enqueue_evolution_proposal(
            state_dir=state_dir,
            source_type=args.source_type,
            title=args.title,
            summary=args.summary,
            source_ref=args.source_ref,
            target_area=args.target_area,
            risk_level=args.risk_level,
            submitted_by=args.submitted_by,
            evidence=list(args.evidence or []),
            metadata=parse_json_object_arg(args.metadata_json),
            review_gate=args.review_gate,
        )
    elif args.command == "decide-evolution":
        result = decide_evolution_proposal(
            args.proposal_id,
            state_dir=state_dir,
            decision=args.decision,
            reviewer=args.reviewer,
            rationale=args.rationale,
            conditions=list(args.condition or []),
            evidence=list(args.evidence or []),
        )
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
