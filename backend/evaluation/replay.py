from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from backend.executors.base import ExecutionRequest
from backend.tools.base import ToolSpec


@dataclass(frozen=True)
class ReplayRecord:
    workflow_id: str
    user_input: str
    request: ExecutionRequest | None
    expected_success: bool
    replayable: bool
    risk_level: str = "unknown"
    tool_name: str = ""
    issues: tuple[str, ...] = ()
    correlated_audit_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request"] = asdict(self.request) if self.request is not None else None
        return payload


@dataclass(frozen=True)
class ReplayReport:
    total: int
    replayable_count: int
    expected_success_count: int
    expected_failure_count: int
    high_risk_count: int
    records: tuple[ReplayRecord, ...] = ()
    failure_sample_ids: tuple[str, ...] = ()

    @property
    def replayable_rate(self) -> float:
        return self.replayable_count / self.total if self.total else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "replayable_count": self.replayable_count,
            "expected_success_count": self.expected_success_count,
            "expected_failure_count": self.expected_failure_count,
            "high_risk_count": self.high_risk_count,
            "failure_sample_ids": list(self.failure_sample_ids),
            "replayable_rate": self.replayable_rate,
            "records": [record.snapshot() for record in self.records],
        }


def _tool_map(tools: list[ToolSpec] | None) -> dict[tuple[str, str], ToolSpec]:
    return {(tool.target, tool.operation): tool for tool in tools or []}


def _as_record_snapshot(record: Any) -> dict[str, Any]:
    if hasattr(record, "snapshot"):
        return dict(record.snapshot())
    return dict(record or {})


def _build_replay_record(snapshot: dict[str, Any], tools: dict[tuple[str, str], ToolSpec], *, require_known_tool: bool) -> ReplayRecord:
    workflow_id = str(snapshot.get("workflow_id") or "")
    target = str(snapshot.get("target") or "")
    operation = str(snapshot.get("operation") or "")
    params = dict(snapshot.get("params") or {})
    issues: list[str] = []
    tool = tools.get((target, operation))
    if not workflow_id:
        issues.append("missing_workflow_id")
    if not target:
        issues.append("missing_target")
    if not operation:
        issues.append("missing_operation")
    if snapshot.get("archived"):
        issues.append("archived_record")
    if require_known_tool and tool is None:
        issues.append("tool_not_registered")
    request = ExecutionRequest(target=target, operation=operation, params=params) if target and operation else None
    replayable = request is not None and not issues
    return ReplayRecord(
        workflow_id=workflow_id,
        user_input=str(snapshot.get("user_input") or ""),
        request=request,
        expected_success=bool(snapshot.get("success")),
        replayable=replayable,
        risk_level=getattr(tool, "risk_level", "unknown") if tool is not None else "unknown",
        tool_name=getattr(tool, "name", "") if tool is not None else "",
        issues=tuple(issues),
        metadata={
            "source_message": snapshot.get("message") or "",
            "source_error_code": snapshot.get("error_code") or "",
            "source_metadata": dict(snapshot.get("metadata") or {}),
        },
    )


def build_replay_report(
    records: list[Any],
    *,
    tools: list[ToolSpec] | None = None,
    require_known_tool: bool = False,
    failure_samples: Any | None = None,
) -> ReplayReport:
    tool_by_route = _tool_map(tools)
    replay_records = tuple(_build_replay_record(_as_record_snapshot(record), tool_by_route, require_known_tool=require_known_tool) for record in records)
    failure_ids: tuple[str, ...] = ()
    if failure_samples is not None and hasattr(failure_samples, "query"):
        for record in replay_records:
            error_code = record.metadata.get("source_error_code", "")
            if not record.expected_success and error_code:
                matches = failure_samples.query(error_code=error_code, limit=1)
                if matches:
                    failure_ids += (matches[0].sample_id,)
    return ReplayReport(
        total=len(replay_records),
        replayable_count=sum(1 for record in replay_records if record.replayable),
        expected_success_count=sum(1 for record in replay_records if record.expected_success),
        expected_failure_count=sum(1 for record in replay_records if not record.expected_success),
        high_risk_count=sum(1 for record in replay_records if record.risk_level.lower() == "high"),
        records=replay_records,
        failure_sample_ids=failure_ids,
    )


def build_replay_report_with_audit_correlation(
    records: list[Any],
    *,
    tools: list[ToolSpec] | None = None,
    audit_log: Any | None = None,
    require_known_tool: bool = False,
    time_window_seconds: float = 5.0,
) -> ReplayReport:

    tool_by_route = _tool_map(tools)
    replay_records_list: list[ReplayRecord] = []
    for record in records:
        rr = _build_replay_record(_as_record_snapshot(record), tool_by_route, require_known_tool=require_known_tool)
        replay_records_list.append(rr)

    if audit_log is not None and hasattr(audit_log, "recent"):
        audit_records = audit_log.recent(limit=500)
        for i, rr in enumerate(replay_records_list):
            rr_target = getattr(rr.request, "target", "") if rr.request else ""
            rr_op = getattr(rr.request, "operation", "") if rr.request else ""
            for audit in audit_records:
                at = str(audit.get("target") or "")
                ao = str(audit.get("operation") or "")
                if rr_target and at and rr_target == at and rr_op == ao:
                    replay_records_list[i] = ReplayRecord(
                        workflow_id=rr.workflow_id,
                        user_input=rr.user_input,
                        request=rr.request,
                        expected_success=rr.expected_success,
                        replayable=rr.replayable,
                        risk_level=rr.risk_level,
                        tool_name=rr.tool_name,
                        issues=rr.issues,
                        correlated_audit_id=str(audit.get("audit_id") or ""),
                        metadata=rr.metadata,
                    )
                    break

    final_records = tuple(replay_records_list)
    return ReplayReport(
        total=len(final_records),
        replayable_count=sum(1 for r in final_records if r.replayable),
        expected_success_count=sum(1 for r in final_records if r.expected_success),
        expected_failure_count=sum(1 for r in final_records if not r.expected_success),
        high_risk_count=sum(1 for r in final_records if r.risk_level.lower() == "high"),
        records=final_records,
    )