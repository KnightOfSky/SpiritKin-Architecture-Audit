from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.agent_management import load_agent_management_state
from backend.knowledge.loader import SUPPORTED_TEXT_EXTENSIONS, ingest_directory
from backend.knowledge.store import InMemoryKnowledgeStore
from backend.knowledge.vault_connector import ObsidianVaultConnector
from backend.state_store import resolve_state_path

DEFAULT_KNOWLEDGE_ROOT = Path("state/knowledge_bases")
KNOWLEDGE_BASE_SCHEMA_VERSION = "spiritkin.knowledge_base.v1"
KNOWLEDGE_SOURCE_SCHEMA_VERSION = "spiritkin.knowledge_sources.v1"
KNOWLEDGE_JOB_SCHEMA_VERSION = "spiritkin.knowledge_jobs.v1"
DEFAULT_KNOWLEDGE_SOURCE_REGISTRY_PATH = "state/knowledge_bases/sources.json"
DEFAULT_KNOWLEDGE_JOB_HISTORY_PATH = "state/knowledge_bases/jobs.json"
KNOWLEDGE_JOB_HISTORY_LIMIT = 200


@dataclass(frozen=True)
class KnowledgeBaseIndexReport:
    knowledge_base_id: str
    path: str
    document_count: int
    chunk_count: int
    indexed_files: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    updated_at: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "knowledge_base_id": self.knowledge_base_id,
            "path": self.path,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "indexed_files": list(self.indexed_files),
            "skipped": list(self.skipped),
            "updated_at": self.updated_at,
        }


def resolve_knowledge_base_path(raw_path: str | Path, *, root: str | Path = DEFAULT_KNOWLEDGE_ROOT) -> Path:
    root_path = Path(root).resolve()
    raw = str(raw_path or "").strip()
    target = Path(raw) if raw else root_path / "custom"
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()
    allowed_roots = {root_path, (Path.cwd() / DEFAULT_KNOWLEDGE_ROOT).resolve()}
    if not any(target == allowed or allowed in target.parents for allowed in allowed_roots):
        raise ValueError("knowledge base path must stay under state/knowledge_bases")
    return target


def build_knowledge_base_snapshot() -> dict[str, Any]:
    state = load_agent_management_state()
    records = []
    for kb in state.knowledge_bases:
        path = resolve_knowledge_base_path(kb.path)
        records.append(
            {
                **kb.snapshot(),
                "resolved_path": str(path),
                "exists": path.exists(),
                "file_count": _count_text_files(path) if path.exists() else 0,
                "last_index": _load_last_index(path),
            }
        )
    return {
        "schema_version": KNOWLEDGE_BASE_SCHEMA_VERSION,
        "knowledge_bases": records,
        "count": len(records),
        "supported_extensions": sorted(SUPPORTED_TEXT_EXTENSIONS),
        "external_sources": build_knowledge_source_snapshot()["sources"],
        "job_history": build_knowledge_job_history(),
    }


def handle_knowledge_base_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "list"}:
        return {"ok": True, "knowledge_base": build_knowledge_base_snapshot()}
    if action == "index":
        kb_id, path = _resolve_target_knowledge_base(payload)
        report = index_knowledge_base(kb_id, path)
        return {"ok": True, "knowledge_base": build_knowledge_base_snapshot(), "index": report.snapshot()}
    if action == "import_files":
        kb_id, path = _resolve_target_knowledge_base(payload)
        imported = import_knowledge_base_files(path, payload.get("files") or [])
        report = index_knowledge_base(kb_id, path)
        return {
            "ok": True,
            "knowledge_base": build_knowledge_base_snapshot(),
            "import": imported,
            "index": report.snapshot(),
        }
    if action in {"index_all", "rebuild_all", "index_unindexed"}:
        return index_all_knowledge_bases(only_unindexed=action == "index_unindexed")
    if action in {"register_source", "save_source", "upsert_source"}:
        return save_knowledge_source(payload)
    if action in {"delete_source", "remove_source"}:
        return delete_knowledge_source(str(payload.get("source_id") or payload.get("id") or ""))
    if action in {"sync_source", "reindex_source"}:
        source_id = str(payload.get("source_id") or payload.get("id") or "")
        return sync_knowledge_source(source_id, index_after=bool(payload.get("index_after", True)))
    raise ValueError(f"unsupported knowledge base action: {action}")


def index_knowledge_base(knowledge_base_id: str, path: str | Path) -> KnowledgeBaseIndexReport:
    started_at = time.time()
    try:
        target = resolve_knowledge_base_path(path)
        target.mkdir(parents=True, exist_ok=True)
        store = InMemoryKnowledgeStore()
        indexed_files = ingest_directory(store, target)
        report = KnowledgeBaseIndexReport(
            knowledge_base_id=knowledge_base_id,
            path=str(target),
            document_count=len(store.list_documents()),
            chunk_count=len(store.list_chunks()),
            indexed_files=indexed_files,
            updated_at=time.time(),
        )
        (target / ".spiritkin_kb_index.json").write_text(json.dumps(report.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        record_knowledge_job(
            "index",
            "completed",
            target_id=knowledge_base_id,
            target_path=str(target),
            summary=f"Indexed {report.document_count} documents into {report.chunk_count} chunks.",
            details={
                "document_count": report.document_count,
                "chunk_count": report.chunk_count,
                "indexed_file_count": len(indexed_files),
                "indexed_files": indexed_files[:50],
            },
            started_at=started_at,
        )
        return report
    except Exception as exc:
        record_knowledge_job(
            "index",
            "failed",
            target_id=knowledge_base_id,
            target_path=str(path),
            summary="Knowledge base indexing failed.",
            error=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
        )
        raise


def import_knowledge_base_files(path: str | Path, files: list[Any]) -> dict[str, Any]:
    target_root = resolve_knowledge_base_path(path)
    target_root.mkdir(parents=True, exist_ok=True)
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if not isinstance(files, list):
        raise ValueError("files must be a list")
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            skipped.append({"path": f"file-{index}", "reason": "invalid_file_payload"})
            continue
        raw_name = str(item.get("path") or item.get("name") or f"file-{index}.txt")
        safe_name = _safe_relative_path(raw_name)
        if not safe_name:
            skipped.append({"path": raw_name, "reason": "invalid_path"})
            continue
        data = _file_payload_bytes(item)
        if data is None:
            source_path = str(item.get("source_path") or item.get("local_path") or "").strip()
            if not source_path:
                skipped.append({"path": raw_name, "reason": "missing_content"})
                continue
            source = Path(source_path).resolve()
            if not source.exists() or not source.is_file():
                skipped.append({"path": raw_name, "reason": "source_not_found"})
                continue
            safe_name = _safe_relative_path(raw_name or source.name)
            destination = (target_root / safe_name).resolve()
            if target_root not in destination.parents:
                skipped.append({"path": raw_name, "reason": "path_escape"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            size = destination.stat().st_size
        else:
            destination = (target_root / safe_name).resolve()
            if target_root not in destination.parents:
                skipped.append({"path": raw_name, "reason": "path_escape"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            size = len(data)
        imported.append(
            {
                "path": str(destination),
                "relative_path": destination.relative_to(target_root).as_posix(),
                "size_bytes": size,
                "sha256": _sha256_file(destination),
            }
        )
    return {"imported": imported, "skipped": skipped, "count": len(imported)}


def resolve_knowledge_source_registry_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_KNOWLEDGE_SOURCE_REGISTRY_PATH", DEFAULT_KNOWLEDGE_SOURCE_REGISTRY_PATH, path)


def resolve_knowledge_job_history_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_KNOWLEDGE_JOB_HISTORY_PATH", DEFAULT_KNOWLEDGE_JOB_HISTORY_PATH, path)


def load_knowledge_job_history(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    history_path = resolve_knowledge_job_history_path(path)
    if not history_path.exists():
        return _default_job_history()
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_job_history()
    if not isinstance(payload, dict):
        return _default_job_history()
    history = _default_job_history()
    history.update(payload)
    history["schema_version"] = KNOWLEDGE_JOB_SCHEMA_VERSION
    jobs = history.get("jobs")
    history["jobs"] = [_normalize_job(item) for item in jobs if isinstance(item, dict)][-KNOWLEDGE_JOB_HISTORY_LIMIT:] if isinstance(jobs, list) else []
    return history


def save_knowledge_job_history(history: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    history_path = resolve_knowledge_job_history_path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _default_job_history()
    normalized.update(history)
    normalized["schema_version"] = KNOWLEDGE_JOB_SCHEMA_VERSION
    normalized["jobs"] = [_normalize_job(item) for item in normalized.get("jobs") or [] if isinstance(item, dict)][-KNOWLEDGE_JOB_HISTORY_LIMIT:]
    normalized["updated_at"] = time.time()
    history_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def build_knowledge_job_history(*, limit: int = 50) -> dict[str, Any]:
    history = load_knowledge_job_history()
    jobs = [dict(item) for item in history.get("jobs") or [] if isinstance(item, dict)]
    failed = [item for item in jobs if str(item.get("status") or "") == "failed"]
    recent = list(reversed(jobs[-max(1, limit) :]))
    return {
        "schema_version": KNOWLEDGE_JOB_SCHEMA_VERSION,
        "history_path": str(resolve_knowledge_job_history_path()),
        "count": len(jobs),
        "failed_count": len(failed),
        "last_status": str(jobs[-1].get("status") or "") if jobs else "",
        "last_error": str(failed[-1].get("error") or "") if failed else "",
        "jobs": recent,
    }


def record_knowledge_job(
    job_type: str,
    status: str,
    *,
    target_id: str = "",
    target_path: str = "",
    summary: str = "",
    error: str = "",
    details: dict[str, Any] | None = None,
    started_at: float | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    completed_at = time.time()
    start = float(started_at or completed_at)
    normalized_type = _safe_id(job_type)
    normalized_target = _safe_id(target_id) if target_id else "target"
    job = {
        "job_id": f"{int(completed_at * 1000)}-{normalized_type}-{normalized_target[:32]}",
        "job_type": normalized_type,
        "status": _safe_job_status(status),
        "target_id": str(target_id or ""),
        "target_path": str(target_path or ""),
        "summary": str(summary or ""),
        "error": str(error or ""),
        "details": _json_safe_dict(details or {}),
        "actor": str(actor or "system"),
        "started_at": start,
        "completed_at": completed_at,
        "duration_ms": max(0, int((completed_at - start) * 1000)),
    }
    history = load_knowledge_job_history()
    history["jobs"] = [*list(history.get("jobs") or []), job][-KNOWLEDGE_JOB_HISTORY_LIMIT:]
    save_knowledge_job_history(history)
    return job


def load_knowledge_source_registry(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    registry_path = resolve_knowledge_source_registry_path(path)
    if not registry_path.exists():
        return _default_source_registry()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_source_registry()
    if not isinstance(payload, dict):
        return _default_source_registry()
    registry = _default_source_registry()
    registry.update(payload)
    registry["schema_version"] = KNOWLEDGE_SOURCE_SCHEMA_VERSION
    sources = registry.get("sources")
    registry["sources"] = [_normalize_source(item) for item in sources if isinstance(item, dict)] if isinstance(sources, list) else []
    return registry


def save_knowledge_source_registry(registry: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    registry_path = resolve_knowledge_source_registry_path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _default_source_registry()
    normalized.update(registry)
    normalized["schema_version"] = KNOWLEDGE_SOURCE_SCHEMA_VERSION
    normalized["sources"] = [_normalize_source(item) for item in normalized.get("sources") or [] if isinstance(item, dict)]
    normalized["updated_at"] = time.time()
    registry_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def build_knowledge_source_snapshot() -> dict[str, Any]:
    registry = load_knowledge_source_registry()
    sources = []
    for source in registry["sources"]:
        source_path = Path(str(source.get("path") or "")).expanduser()
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        else:
            source_path = source_path.resolve()
        last_sync = dict(source.get("last_sync") or {}) if isinstance(source.get("last_sync"), dict) else {}
        sources.append(
            {
                **source,
                "resolved_path": str(source_path),
                "exists": source_path.exists(),
                "file_count": _count_source_text_files(source_path, source),
                "last_sync": last_sync,
                "status": _source_status(source, source_path, last_sync),
            }
        )
    return {
        "schema_version": KNOWLEDGE_SOURCE_SCHEMA_VERSION,
        "registry_path": str(resolve_knowledge_source_registry_path()),
        "count": len(sources),
        "sources": sources,
    }


def save_knowledge_source(payload: dict[str, Any]) -> dict[str, Any]:
    registry = load_knowledge_source_registry()
    source_id = _safe_id(str(payload.get("source_id") or payload.get("id") or payload.get("label") or payload.get("path") or "knowledge-source"))
    existing = next((item for item in registry["sources"] if item["source_id"] == source_id), None)
    source = _normalize_source({**(existing or {}), **payload, "source_id": source_id})
    if existing is not None:
        existing.update(source)
    else:
        registry["sources"].append(source)
    saved = save_knowledge_source_registry(registry)
    return {"ok": True, "source": source, "knowledge_base": build_knowledge_base_snapshot(), "registry": saved}


def delete_knowledge_source(source_id: str) -> dict[str, Any]:
    normalized = _safe_id(source_id)
    registry = load_knowledge_source_registry()
    before = len(registry["sources"])
    registry["sources"] = [item for item in registry["sources"] if item["source_id"] != normalized]
    save_knowledge_source_registry(registry)
    return {"ok": True, "deleted": before != len(registry["sources"]), "source_id": normalized, "knowledge_base": build_knowledge_base_snapshot()}


def sync_knowledge_source(source_id: str, *, index_after: bool = True) -> dict[str, Any]:
    started_at = time.time()
    normalized = _safe_id(source_id)
    try:
        registry = load_knowledge_source_registry()
        source = next((item for item in registry["sources"] if item["source_id"] == normalized), None)
        if source is None:
            raise ValueError(f"unknown knowledge source: {source_id}")
        kb_id = str(source.get("knowledge_base_id") or "")
        kb_path = _knowledge_base_path_for_id(kb_id)
        target_root = resolve_knowledge_base_path(kb_path)
        target_dir = (target_root / "_external" / normalized).resolve()
        if target_root not in target_dir.parents:
            raise ValueError("knowledge source target escaped knowledge base root")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        sync_report = _copy_source_documents(source, target_dir)
        index_report = index_knowledge_base(kb_id, target_root).snapshot() if index_after else {}
        sync_report["indexed"] = bool(index_report)
        sync_report["index"] = index_report
        source["last_sync"] = sync_report
        source["updated_at"] = time.time()
        save_knowledge_source_registry(registry)
        status = "skipped" if sync_report.get("status") == "disabled" else "completed"
        record_knowledge_job(
            "sync_source",
            status,
            target_id=normalized,
            target_path=str(target_dir),
            summary=f"Synced {sync_report.get('count', 0)} documents from source {normalized}.",
            details={
                "knowledge_base_id": kb_id,
                "copied_count": len(sync_report.get("copied") or []),
                "skipped_count": len(sync_report.get("skipped") or []),
                "skipped": list(sync_report.get("skipped") or [])[:50],
                "indexed": bool(index_report),
            },
            started_at=started_at,
        )
        return {"ok": True, "source": source, "sync": sync_report, "index": index_report, "knowledge_base": build_knowledge_base_snapshot()}
    except Exception as exc:
        record_knowledge_job(
            "sync_source",
            "failed",
            target_id=normalized,
            target_path="",
            summary="Knowledge source sync failed.",
            error=f"{type(exc).__name__}: {exc}",
            details={"index_after": index_after},
            started_at=started_at,
        )
        raise


def index_all_knowledge_bases(*, only_unindexed: bool = False) -> dict[str, Any]:
    started_at = time.time()
    snapshot = build_knowledge_base_snapshot()
    reports = []
    skipped = []
    failed = []
    for kb in snapshot.get("knowledge_bases") or []:
        if not isinstance(kb, dict) or not bool(kb.get("enabled", True)):
            continue
        last_index = dict(kb.get("last_index") or {}) if isinstance(kb.get("last_index"), dict) else {}
        if only_unindexed and last_index.get("updated_at"):
            skipped.append({"knowledge_base_id": kb.get("knowledge_base_id"), "reason": "already_indexed"})
            continue
        path = str(kb.get("path") or kb.get("resolved_path") or "")
        kb_id = str(kb.get("knowledge_base_id") or "kb_custom")
        try:
            report = index_knowledge_base(kb_id, path).snapshot()
            reports.append(report)
        except Exception as exc:
            failed.append({"knowledge_base_id": kb_id, "path": path, "error": f"{type(exc).__name__}: {exc}"})
    status = "failed" if failed else ("skipped" if not reports else "completed")
    record_knowledge_job(
        "index_unindexed" if only_unindexed else "index_all",
        status,
        target_id="all",
        target_path=str(DEFAULT_KNOWLEDGE_ROOT),
        summary=f"Indexed {len(reports)} knowledge bases; skipped {len(skipped)}; failed {len(failed)}.",
        details={"indexed_count": len(reports), "skipped": skipped[:50], "failed": failed[:50]},
        started_at=started_at,
    )
    return {"ok": not failed, "indexed": reports, "skipped": skipped, "failed": failed, "knowledge_base": build_knowledge_base_snapshot()}


def _resolve_target_knowledge_base(payload: dict[str, Any]) -> tuple[str, str]:
    kb_id = str(payload.get("knowledge_base_id") or payload.get("id") or "").strip()
    path = str(payload.get("path") or "").strip()
    if kb_id and not path:
        state = load_agent_management_state()
        match = next((item for item in state.knowledge_bases if item.knowledge_base_id == kb_id), None)
        if match is not None:
            path = match.path
    if not kb_id:
        kb_id = "kb_custom"
    if not path:
        path = f"state/knowledge_bases/custom/{kb_id}"
    return kb_id, path


def _knowledge_base_path_for_id(kb_id: str) -> str:
    state = load_agent_management_state()
    match = next((item for item in state.knowledge_bases if item.knowledge_base_id == kb_id), None)
    if match is None:
        raise ValueError(f"unknown knowledge_base_id: {kb_id}")
    return match.path


def _count_text_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file() and item.suffix.lower() in SUPPORTED_TEXT_EXTENSIONS)


def _load_last_index(path: Path) -> dict[str, Any]:
    index_path = path / ".spiritkin_kb_index.json"
    if not index_path.exists():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_payload_bytes(item: dict[str, Any]) -> bytes | None:
    if "text" in item:
        return str(item.get("text") or "").encode("utf-8")
    content = item.get("content_base64")
    if content:
        try:
            return base64.b64decode(str(content), validate=True)
        except Exception:
            return None
    return None


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_source_registry() -> dict[str, Any]:
    return {"schema_version": KNOWLEDGE_SOURCE_SCHEMA_VERSION, "sources": [], "updated_at": 0.0}


def _default_job_history() -> dict[str, Any]:
    return {"schema_version": KNOWLEDGE_JOB_SCHEMA_VERSION, "jobs": [], "updated_at": 0.0}


def _normalize_job(payload: dict[str, Any]) -> dict[str, Any]:
    completed_at = _float_value(payload.get("completed_at"), time.time())
    started_at = _float_value(payload.get("started_at"), completed_at)
    return {
        "job_id": str(payload.get("job_id") or f"{int(completed_at * 1000)}-knowledge-job"),
        "job_type": _safe_id(str(payload.get("job_type") or "knowledge_job")),
        "status": _safe_job_status(str(payload.get("status") or "completed")),
        "target_id": str(payload.get("target_id") or ""),
        "target_path": str(payload.get("target_path") or ""),
        "summary": str(payload.get("summary") or ""),
        "error": str(payload.get("error") or ""),
        "details": _json_safe_dict(payload.get("details") if isinstance(payload.get("details"), dict) else {}),
        "actor": str(payload.get("actor") or "system"),
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": max(0, int(_float_value(payload.get("duration_ms"), (completed_at - started_at) * 1000))),
    }


def _safe_job_status(status: str) -> str:
    value = str(status or "").strip().lower()
    return value if value in {"queued", "running", "completed", "failed", "skipped"} else "completed"


def _json_safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        safe[str(key)] = _json_safe_value(item)
    return safe


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _float_value(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_source(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or payload.get("source_type") or "folder").strip().lower()
    if kind not in {"folder", "obsidian"}:
        raise ValueError("knowledge source kind must be folder or obsidian")
    path = str(payload.get("path") or payload.get("vault_path") or "").strip()
    if not path:
        raise ValueError("knowledge source path is required")
    source_id = _safe_id(str(payload.get("source_id") or payload.get("id") or payload.get("label") or Path(path).name or "knowledge-source"))
    kb_id = str(payload.get("knowledge_base_id") or "").strip()
    if not kb_id:
        raise ValueError("knowledge source requires knowledge_base_id")
    return {
        "source_id": source_id,
        "label": str(payload.get("label") or source_id),
        "kind": kind,
        "path": path,
        "knowledge_base_id": kb_id,
        "enabled": bool(payload.get("enabled", True)),
        "recursive": bool(payload.get("recursive", True)),
        "ignore_patterns": _string_list(payload.get("ignore_patterns")),
        "tag_filter": _string_list(payload.get("tag_filter")),
        "notes": str(payload.get("notes") or ""),
        "created_at": float(payload.get("created_at") or time.time()),
        "updated_at": time.time(),
        "last_sync": dict(payload.get("last_sync") or {}) if isinstance(payload.get("last_sync"), dict) else {},
    }


def _copy_source_documents(source: dict[str, Any], target_dir: Path) -> dict[str, Any]:
    if not bool(source.get("enabled", True)):
        return {"source_id": source["source_id"], "status": "disabled", "copied": [], "skipped": [], "count": 0, "updated_at": time.time()}
    source_path = Path(str(source.get("path") or "")).expanduser()
    source_path = source_path.resolve() if source_path.is_absolute() else (Path.cwd() / source_path).resolve()
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError(f"knowledge source path not found: {source_path}")
    copied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    ignore_patterns = [str(item) for item in source.get("ignore_patterns") or []]
    tag_filter = {str(item).strip().lstrip("#") for item in source.get("tag_filter") or [] if str(item).strip()}
    if source.get("kind") == "obsidian":
        connector = ObsidianVaultConnector(source_path)
        docs = connector.load_vault(recursive=bool(source.get("recursive", True)))
        backlinks = connector.resolve_backlinks(docs)
        for doc in docs:
            rel_path = str(doc.metadata.get("relative_path") or f"{doc.title}.md")
            if _matches_ignore(rel_path, ignore_patterns):
                skipped.append({"path": rel_path, "reason": "ignored"})
                continue
            tags = {str(item) for item in doc.metadata.get("tags") or []}
            if tag_filter and not tags.intersection(tag_filter):
                skipped.append({"path": rel_path, "reason": "tag_filter"})
                continue
            destination = (target_dir / _safe_relative_path(rel_path)).resolve()
            if target_dir not in destination.parents:
                skipped.append({"path": rel_path, "reason": "path_escape"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            metadata = {
                **dict(doc.metadata or {}),
                "source_type": "obsidian",
                "source_id": source["source_id"],
                "backlinks": backlinks.get(doc.document_id, []),
            }
            destination.write_text(_document_with_metadata(doc.title, doc.content, metadata), encoding="utf-8")
            copied.append({"path": str(destination), "relative_path": destination.relative_to(target_dir).as_posix(), "sha256": _sha256_file(destination)})
    else:
        pattern = "**/*" if bool(source.get("recursive", True)) else "*"
        for path in sorted(source_path.glob(pattern)):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_TEXT_EXTENSIONS:
                continue
            rel_path = path.relative_to(source_path).as_posix()
            if _matches_ignore(rel_path, ignore_patterns):
                skipped.append({"path": rel_path, "reason": "ignored"})
                continue
            destination = (target_dir / _safe_relative_path(rel_path)).resolve()
            if target_dir not in destination.parents:
                skipped.append({"path": rel_path, "reason": "path_escape"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied.append({"path": str(destination), "relative_path": destination.relative_to(target_dir).as_posix(), "sha256": _sha256_file(destination)})
    return {
        "source_id": source["source_id"],
        "status": "synced",
        "target_path": str(target_dir),
        "copied": copied,
        "skipped": skipped,
        "count": len(copied),
        "updated_at": time.time(),
    }


def _document_with_metadata(title: str, content: str, metadata: dict[str, Any]) -> str:
    metadata_text = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return f"---\ntitle: {title}\nspiritkin_metadata: {metadata_text}\n---\n\n{content.strip()}\n"


def _count_source_text_files(path: Path, source: dict[str, Any]) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    pattern = "**/*" if bool(source.get("recursive", True)) else "*"
    ignore_patterns = [str(item) for item in source.get("ignore_patterns") or []]
    return sum(
        1
        for item in path.glob(pattern)
        if item.is_file() and item.suffix.lower() in SUPPORTED_TEXT_EXTENSIONS and not _matches_ignore(item.relative_to(path).as_posix(), ignore_patterns)
    )


def _source_status(source: dict[str, Any], path: Path, last_sync: dict[str, Any]) -> str:
    if not bool(source.get("enabled", True)):
        return "disabled"
    if not path.exists():
        return "missing"
    if not last_sync.get("updated_at"):
        return "unsynced"
    return "synced"


def _matches_ignore(relative_path: str, patterns: list[str]) -> bool:
    text = relative_path.replace("\\", "/")
    for pattern in patterns:
        pattern = pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        if pattern in text or Path(text).match(pattern):
            return True
    return False


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "-", str(value or "").strip().lower()).strip("-._")
    return safe[:80] or f"knowledge-source-{int(time.time())}"
