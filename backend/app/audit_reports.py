"""Incremental audit aggregation for the Audit/Replay report surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.app.settings import resolve_audit_log_path
from backend.state_store import now_ts, read_json_state, resolve_state_path, write_json_state

SCHEMA_VERSION = "spiritkin.audit_report.v1"
DEFAULT_CURSOR_PATH = "state/audit_reports/cursor.json"
DEFAULT_REPORT_PATH = "state/audit_reports/latest.json"
REPORT_RECORD_LIMIT = 500


def _cursor_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_AUDIT_REPORT_CURSOR_PATH", DEFAULT_CURSOR_PATH, path)


def _report_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_AUDIT_REPORT_PATH", DEFAULT_REPORT_PATH, path)


def _default_cursor() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sources": {}, "updated_at": 0.0}


def _default_report() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": 0.0,
        "total": 0,
        "high_risk_count": 0,
        "failure_count": 0,
        "remote_count": 0,
        "sessions": {},
        "records": [],
        "cursor": {"read_records": 0, "skipped_records": 0, "offset": 0},
    }


def _load_report(path: Path) -> dict[str, Any]:
    report = read_json_state(path, _default_report())
    normalized = _default_report()
    normalized.update(report)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["sessions"] = dict(normalized.get("sessions") or {})
    normalized["records"] = [dict(item) for item in normalized.get("records") or [] if isinstance(item, dict)][-REPORT_RECORD_LIMIT:]
    normalized["cursor"] = dict(normalized.get("cursor") or {})
    return normalized


def _read_appended_jsonl(path: Path, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    if not path.exists() or not path.is_file():
        return [], 0, 0
    size = path.stat().st_size
    start = max(0, int(offset)) if size >= max(0, int(offset)) else 0
    records: list[dict[str, Any]] = []
    skipped = 0
    with path.open("rb") as stream:
        stream.seek(start)
        committed_offset = start
        while True:
            line = stream.readline()
            if not line:
                break
            next_offset = stream.tell()
            if not line.endswith(b"\n"):
                break
            try:
                decoded = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                skipped += 1
                committed_offset = next_offset
                continue
            if isinstance(decoded, dict):
                records.append(decoded)
            else:
                skipped += 1
            committed_offset = next_offset
    return records, committed_offset, skipped


def _session_id(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for key in ("session_id", "conversation_id", "thread_id", "run_id", "workflow_id"):
        value = record.get(key) or metadata.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return "ungrouped"


def _apply_record(report: dict[str, Any], record: dict[str, Any]) -> None:
    session_id = _session_id(record)
    sessions = report["sessions"]
    session = dict(sessions.get(session_id) or {"session_id": session_id, "total": 0, "high_risk_count": 0, "failure_count": 0, "first_timestamp": 0.0, "last_timestamp": 0.0, "event_types": {}})
    timestamp = float(record.get("timestamp") or 0.0)
    risk = str(record.get("risk_level") or "").lower()
    failed = record.get("success") is False
    session["total"] = int(session["total"]) + 1
    session["high_risk_count"] = int(session["high_risk_count"]) + int(risk == "high")
    session["failure_count"] = int(session["failure_count"]) + int(failed)
    session["first_timestamp"] = timestamp if not session["first_timestamp"] else min(float(session["first_timestamp"]), timestamp)
    session["last_timestamp"] = max(float(session["last_timestamp"]), timestamp)
    event_types = dict(session.get("event_types") or {})
    event_type = str(record.get("event_type") or "unknown")
    event_types[event_type] = int(event_types.get(event_type) or 0) + 1
    session["event_types"] = event_types
    sessions[session_id] = session
    report["total"] = int(report["total"]) + 1
    report["high_risk_count"] = int(report["high_risk_count"]) + int(risk == "high")
    report["failure_count"] = int(report["failure_count"]) + int(failed)
    report["remote_count"] = int(report["remote_count"]) + int(str(record.get("target") or "").startswith("remote") or bool((record.get("metadata") or {}).get("node_id")))
    report["records"] = [*report["records"], record][-REPORT_RECORD_LIMIT:]


def generate_audit_report(
    *,
    audit_log_path: str | os.PathLike[str] | None = None,
    cursor_path: str | os.PathLike[str] | None = None,
    report_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    source = Path(audit_log_path or resolve_audit_log_path()).expanduser().resolve()
    cursor_file = _cursor_path(cursor_path)
    report_file = _report_path(report_path)
    cursor_state = read_json_state(cursor_file, _default_cursor())
    sources = dict(cursor_state.get("sources") or {})
    source_key = str(source)
    prior = dict(sources.get(source_key) or {})
    records, offset, skipped = _read_appended_jsonl(source, int(prior.get("offset") or 0))
    report = _load_report(report_file)
    for record in records:
        _apply_record(report, record)
    sources[source_key] = {"offset": offset, "updated_at": now_ts()}
    cursor_state = {"schema_version": SCHEMA_VERSION, "sources": sources, "updated_at": now_ts()}
    report["generated_at"] = now_ts()
    report["audit_log_path"] = source_key
    report["cursor"] = {"read_records": len(records), "skipped_records": skipped, "offset": offset}
    write_json_state(cursor_file, cursor_state)
    write_json_state(report_file, report)
    return {**report, "report_path": str(report_file), "cursor_path": str(cursor_file)}


def build_audit_report_snapshot() -> dict[str, Any]:
    report = _load_report(_report_path())
    report["report_path"] = str(_report_path())
    report["cursor_path"] = str(_cursor_path())
    return report
