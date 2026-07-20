from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    event_type: str
    actor: str = "runtime"
    channel: str = ""
    target: str = ""
    operation: str = ""
    risk_level: str = ""
    success: bool | None = None
    message: str = ""
    policy_decision_id: str = ""
    capability_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> AuditRecord:
        return cls(
            audit_id=str(snapshot.get("audit_id") or ""),
            event_type=str(snapshot.get("event_type") or ""),
            actor=str(snapshot.get("actor") or "runtime"),
            channel=str(snapshot.get("channel") or ""),
            target=str(snapshot.get("target") or ""),
            operation=str(snapshot.get("operation") or ""),
            risk_level=str(snapshot.get("risk_level") or ""),
            success=snapshot.get("success") if isinstance(snapshot.get("success"), bool) else None,
            message=str(snapshot.get("message") or ""),
            policy_decision_id=str(snapshot.get("policy_decision_id") or ""),
            capability_id=str(snapshot.get("capability_id") or ""),
            metadata=dict(snapshot.get("metadata") or {}),
            timestamp=float(snapshot.get("timestamp") or time.time()),
        )


class InMemoryAuditLog:
    def __init__(self, limit: int = 300):
        self.limit = max(1, int(limit))
        self._records: list[AuditRecord] = []
        self._counter = 0

    def record(self, event_type: str, **kwargs: Any) -> AuditRecord:
        self._counter += 1
        record = AuditRecord(audit_id=f"audit-{self._counter:06d}", event_type=event_type, **kwargs)
        self._records.append(record)
        self._records = self._records[-self.limit :]
        return record

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [record.snapshot() for record in self._records[-max(1, int(limit)) :]]

    def query(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        target: str | None = None,
        operation: str | None = None,
        success: bool | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[AuditRecord] = []
        for record in self._records:
            if event_type is not None and record.event_type != event_type:
                continue
            if actor is not None and record.actor != actor:
                continue
            if channel is not None and record.channel != channel:
                continue
            if target is not None and record.target != target:
                continue
            if operation is not None and record.operation != operation:
                continue
            if success is not None and record.success is not success:
                continue
            if since is not None and record.timestamp < since:
                continue
            if until is not None and record.timestamp > until:
                continue
            results.append(record)
        results.sort(key=lambda item: item.timestamp, reverse=True)
        return [record.snapshot() for record in results[: max(1, int(limit))]]

    def export_jsonl(self, path: str | Path, *, records: list[dict[str, Any]] | None = None, redact_metadata_keys: tuple[str, ...] = ("token", "secret", "password")) -> int:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        source = records if records is not None else [record.snapshot() for record in self._records]
        with target.open("w", encoding="utf-8") as fp:
            for record in source:
                sanitized = dict(record)
                metadata = dict(sanitized.get("metadata") or {})
                for key in list(metadata.keys()):
                    if any(term in key.lower() for term in redact_metadata_keys):
                        metadata[key] = "<redacted>"
                sanitized["metadata"] = metadata
                fp.write(json.dumps(sanitized, ensure_ascii=False) + "\n")
        return len(source)

    def summary(self, limit: int = 12) -> dict[str, Any]:
        records = self._records
        return {
            "total": len(records),
            "high_risk_count": sum(1 for record in records if record.risk_level == "high"),
            "remote_count": sum(1 for record in records if record.target.startswith("remote") or bool(record.metadata.get("node_id"))),
            "mobile_count": sum(1 for record in records if record.channel == "mobile" or record.actor == "command_gateway"),
            "failure_count": sum(1 for record in records if record.success is False),
            "rate_limit_violations": sum(1 for record in records if record.event_type == "rate_limit_violation"),
            "recent": self.recent(limit=limit),
        }


class JsonlAuditLog(InMemoryAuditLog):
    def __init__(self, path: str | Path, limit: int = 300):
        super().__init__(limit=limit)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load_existing_records()

    def _load_existing_records(self) -> None:
        if not self.path.exists() or not self.path.is_file():
            return
        records: list[AuditRecord] = []
        max_counter = 0
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    record = AuditRecord.from_snapshot(payload)
                    records.append(record)
                    if record.audit_id.startswith("audit-"):
                        max_counter = max(max_counter, int(record.audit_id.split("-", 1)[1]))
            except Exception:
                continue
        self._records = records[-self.limit :]
        self._counter = max(max_counter, len(records))

    def record(self, event_type: str, **kwargs: Any) -> AuditRecord:
        record = super().record(event_type, **kwargs)
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")
        return record


def build_audit_log(path: str | Path | None = None, *, limit: int = 300) -> InMemoryAuditLog:
    normalized = str(path or "").strip()
    if not normalized:
        return InMemoryAuditLog(limit=limit)
    return JsonlAuditLog(normalized, limit=limit)