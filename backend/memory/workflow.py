from __future__ import annotations

import gzip
import json
import sqlite3
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.executors.base import ExecutionRequest, ExecutionResult


@dataclass(frozen=True)
class WorkflowRecord:
    workflow_id: str
    user_input: str
    target: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    message: str = ""
    error_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    archived: bool = False

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> WorkflowRecord:
        return cls(
            workflow_id=str(snapshot.get("workflow_id") or ""),
            user_input=str(snapshot.get("user_input") or ""),
            target=str(snapshot.get("target") or ""),
            operation=str(snapshot.get("operation") or ""),
            params=dict(snapshot.get("params") or {}),
            success=bool(snapshot.get("success")),
            message=str(snapshot.get("message") or ""),
            error_code=str(snapshot.get("error_code") or ""),
            metadata=dict(snapshot.get("metadata") or {}),
            timestamp=float(snapshot.get("timestamp") or time.time()),
            archived=bool(snapshot.get("archived")),
        )


def _normalized_limit(limit: int) -> int:
    return max(1, int(limit))


def _record_device(record: WorkflowRecord) -> str:
    metadata = dict(record.metadata or {})
    execution_metadata = dict(metadata.get("execution_metadata") or {})
    params = dict(record.params or {})
    return str(
        metadata.get("device")
        or execution_metadata.get("device")
        or execution_metadata.get("node_id")
        or params.get("device")
        or params.get("node_id")
        or ""
    )


def _write_jsonl_gzip(path: str | Path, records: list[WorkflowRecord]) -> None:
    archive_path = Path(path).expanduser()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "at", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")


def _stats_for_records(records: list[WorkflowRecord]) -> dict[str, Any]:
    success_count = sum(1 for record in records if record.success)
    return {
        "total": len(records),
        "success": success_count,
        "failure": len(records) - success_count,
        "success_rate": (success_count / len(records)) if records else 0.0,
        "by_operation": dict(Counter(record.operation for record in records)),
        "by_target": dict(Counter(record.target for record in records)),
        "by_device": dict(Counter(_record_device(record) or "unknown" for record in records)),
    }


def _skill_candidates_for_records(records: list[WorkflowRecord], *, min_successes: int = 2, limit: int = 10) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if record.archived:
            continue
        key = (record.target, record.operation)
        entry = grouped.setdefault(
            key,
            {
                "target": record.target,
                "operation": record.operation,
                "success_count": 0,
                "total_count": 0,
                "last_seen": 0.0,
                "example_params": {},
            },
        )
        entry["total_count"] += 1
        entry["last_seen"] = max(float(entry["last_seen"]), record.timestamp)
        if record.success:
            entry["success_count"] += 1
            entry["example_params"] = dict(record.params or {})

    candidates = []
    for entry in grouped.values():
        if int(entry["success_count"]) < max(1, int(min_successes)):
            continue
        total_count = max(1, int(entry["total_count"]))
        candidates.append({**entry, "success_rate": int(entry["success_count"]) / total_count})
    candidates.sort(key=lambda item: (int(item["success_count"]), float(item["last_seen"])), reverse=True)
    return candidates[:_normalized_limit(limit)]


class InMemoryWorkflowMemory:
    """Minimal procedural memory for work traces that may later become Skills."""

    def __init__(self, limit: int = 200):
        self.limit = max(1, int(limit))
        self._records: list[WorkflowRecord] = []
        self._counter = 0

    def record_execution(self, *, user_input: str, request: ExecutionRequest, result: ExecutionResult) -> WorkflowRecord:
        self._counter += 1
        record = WorkflowRecord(
            workflow_id=f"wf-{self._counter:06d}",
            user_input=user_input,
            target=request.target,
            operation=request.operation,
            params=dict(request.params or {}),
            success=bool(result.success),
            message=result.message,
            error_code=result.error_code,
            metadata={"execution_metadata": dict(result.metadata or {})},
        )
        self._records.append(record)
        self._records = self._records[-self.limit :]
        return record

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        active = [record for record in self._records if not record.archived]
        return [record.snapshot() for record in active[-_normalized_limit(limit) :]]

    def successful_by_operation(self, operation: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.query(operation=operation, success=True, limit=limit)

    def query(
        self,
        *,
        operation: str | None = None,
        target: str | None = None,
        device: str | None = None,
        success: bool | None = None,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        device_filter = (device or "").strip().lower()
        matched: list[WorkflowRecord] = []
        for record in self._records:
            if record.archived and not include_archived:
                continue
            if operation is not None and record.operation != operation:
                continue
            if target is not None and record.target != target:
                continue
            if success is not None and record.success is not success:
                continue
            if device_filter and _record_device(record).lower() != device_filter:
                continue
            matched.append(record)
        return [record.snapshot() for record in matched[-_normalized_limit(limit) :]]

    def stats(self, *, include_archived: bool = False) -> dict[str, Any]:
        records = [record for record in self._records if include_archived or not record.archived]
        return _stats_for_records(records)

    def archive_before(self, cutoff_timestamp: float, *, archive_path: str | Path | None = None) -> int:
        matched = [record for record in self._records if not record.archived and record.timestamp < cutoff_timestamp]
        if archive_path is not None and matched:
            _write_jsonl_gzip(archive_path, matched)
        matched_ids = {record.workflow_id for record in matched}
        self._records = [replace(record, archived=True) if record.workflow_id in matched_ids else record for record in self._records]
        return len(matched)

    def skill_candidates(self, *, min_successes: int = 2, limit: int = 10) -> list[dict[str, Any]]:
        return _skill_candidates_for_records(self._records, min_successes=min_successes, limit=limit)

    def __len__(self) -> int:
        return len([record for record in self._records if not record.archived])


class JsonlWorkflowMemory(InMemoryWorkflowMemory):
    """Append-only workflow memory persisted as JSONL on local disk."""

    def __init__(self, path: str | Path, limit: int = 200):
        super().__init__(limit=limit)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load_existing_records()

    def _load_existing_records(self) -> None:
        if not self.path.exists() or not self.path.is_file():
            return
        max_counter = 0
        records: list[WorkflowRecord] = []
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                record = WorkflowRecord.from_snapshot(payload)
            except Exception:
                continue
            records.append(record)
            if record.workflow_id.startswith("wf-"):
                try:
                    max_counter = max(max_counter, int(record.workflow_id.split("-", 1)[1]))
                except Exception:
                    pass
        self._records = records[-self.limit :]
        self._counter = max(max_counter, len(records))

    def record_execution(self, *, user_input: str, request: ExecutionRequest, result: ExecutionResult) -> WorkflowRecord:
        record = super().record_execution(user_input=user_input, request=request, result=result)
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")
        return record

    def archive_before(self, cutoff_timestamp: float, *, archive_path: str | Path | None = None) -> int:
        count = super().archive_before(cutoff_timestamp, archive_path=archive_path)
        if count:
            with self.path.open("w", encoding="utf-8") as fp:
                for record in self._records:
                    fp.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")
        return count


class SQLiteWorkflowMemory(InMemoryWorkflowMemory):
    """Workflow memory persisted in SQLite with indexed recall fields."""

    def __init__(self, path: str | Path, limit: int = 200):
        super().__init__(limit=limit)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._load_existing_records()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_id TEXT NOT NULL UNIQUE,
                        user_input TEXT NOT NULL,
                        target TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        device TEXT NOT NULL DEFAULT '',
                        params_json TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        message TEXT NOT NULL,
                        error_code TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        archived INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_operation ON workflow_records(operation, success, archived)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_target ON workflow_records(target, archived)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_device ON workflow_records(device, archived)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_timestamp ON workflow_records(timestamp, archived)")

    def _row_to_record(self, row: sqlite3.Row) -> WorkflowRecord:
        return WorkflowRecord(
            workflow_id=str(row["workflow_id"]),
            user_input=str(row["user_input"]),
            target=str(row["target"]),
            operation=str(row["operation"]),
            params=json.loads(row["params_json"] or "{}"),
            success=bool(row["success"]),
            message=str(row["message"] or ""),
            error_code=str(row["error_code"] or ""),
            metadata=json.loads(row["metadata_json"] or "{}"),
            timestamp=float(row["timestamp"]),
            archived=bool(row["archived"]),
        )

    def _load_existing_records(self) -> None:
        max_counter = 0
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM workflow_records ORDER BY timestamp DESC, id DESC LIMIT ?", (self.limit,)).fetchall()
            ids = conn.execute("SELECT workflow_id FROM workflow_records").fetchall()
        for row in ids:
            workflow_id = str(row["workflow_id"])
            if workflow_id.startswith("wf-"):
                try:
                    max_counter = max(max_counter, int(workflow_id.split("-", 1)[1]))
                except Exception:
                    pass
        self._records = list(reversed([self._row_to_record(row) for row in rows]))
        self._counter = max_counter

    def _insert_record(self, record: WorkflowRecord) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO workflow_records
                    (workflow_id, user_input, target, operation, device, params_json, success, message, error_code, metadata_json, timestamp, archived)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.workflow_id,
                        record.user_input,
                        record.target,
                        record.operation,
                        _record_device(record),
                        json.dumps(record.params, ensure_ascii=False),
                        1 if record.success else 0,
                        record.message,
                        record.error_code,
                        json.dumps(record.metadata, ensure_ascii=False),
                        record.timestamp,
                        1 if record.archived else 0,
                    ),
                )

    def record_execution(self, *, user_input: str, request: ExecutionRequest, result: ExecutionResult) -> WorkflowRecord:
        record = super().record_execution(user_input=user_input, request=request, result=result)
        self._insert_record(record)
        return record

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_records WHERE archived = 0 ORDER BY timestamp DESC, id DESC LIMIT ?",
                (_normalized_limit(limit),),
            ).fetchall()
        return [record.snapshot() for record in reversed([self._row_to_record(row) for row in rows])]

    def query(
        self,
        *,
        operation: str | None = None,
        target: str | None = None,
        device: str | None = None,
        success: bool | None = None,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if not include_archived:
            clauses.append("archived = 0")
        if operation is not None:
            clauses.append("operation = ?")
            values.append(operation)
        if target is not None:
            clauses.append("target = ?")
            values.append(target)
        if device is not None:
            clauses.append("device = ?")
            values.append(device)
        if success is not None:
            clauses.append("success = ?")
            values.append(1 if success else 0)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(_normalized_limit(limit))
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT * FROM workflow_records{where} ORDER BY timestamp DESC, id DESC LIMIT ?", values).fetchall()
        return [record.snapshot() for record in reversed([self._row_to_record(row) for row in rows])]

    def stats(self, *, include_archived: bool = False) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM workflow_records" + ("" if include_archived else " WHERE archived = 0")).fetchall()
        records = [self._row_to_record(row) for row in rows]
        return _stats_for_records(records)

    def archive_before(self, cutoff_timestamp: float, *, archive_path: str | Path | None = None) -> int:
        with closing(self._connect()) as conn:
            with conn:
                rows = conn.execute("SELECT * FROM workflow_records WHERE archived = 0 AND timestamp < ?", (float(cutoff_timestamp),)).fetchall()
                records = [self._row_to_record(row) for row in rows]
                if archive_path is not None and records:
                    _write_jsonl_gzip(archive_path, records)
                conn.execute("UPDATE workflow_records SET archived = 1 WHERE archived = 0 AND timestamp < ?", (float(cutoff_timestamp),))
        if records:
            self._load_existing_records()
        return len(records)

    def skill_candidates(self, *, min_successes: int = 2, limit: int = 10) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM workflow_records WHERE archived = 0").fetchall()
        records = [self._row_to_record(row) for row in rows]
        return _skill_candidates_for_records(records, min_successes=min_successes, limit=limit)

    def __len__(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM workflow_records WHERE archived = 0").fetchone()
        return int(row["count"] if row else 0)


def build_workflow_memory(memory_path: str | Path | None = None, *, limit: int = 200) -> InMemoryWorkflowMemory:
    if memory_path is None:
        return InMemoryWorkflowMemory(limit=limit)
    normalized = str(memory_path).strip()
    if not normalized:
        return InMemoryWorkflowMemory(limit=limit)
    suffix = Path(normalized).suffix.lower()
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        return SQLiteWorkflowMemory(normalized, limit=limit)
    return JsonlWorkflowMemory(normalized, limit=limit)