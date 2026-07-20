from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.orchestrator.execution_finalizer import ExecutionFinalizer
from backend.orchestrator.workflow_graph import (
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_SUCCEEDED,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeRun,
    WorkflowRun,
)
from backend.orchestrator.workflow_runtime_contracts import (
    WORKFLOW_RUNTIME_CONTRACT_VERSION,
    workflow_run_context_patches,
    workflow_run_execution_summary,
)
from backend.orchestrator.workflow_task_finalizer import CollaborationTaskFinalizerPort, sync_workflow_verdict_to_task

DEFAULT_WORKFLOW_STATE_DIR = "state/workflows"
DEFINITIONS_FILE_NAME = "definitions.json"
RUNS_FILE_NAME = "runs.json"
DEFINITION_VERSIONS_FILE_NAME = "definition_versions.json"
AUDIT_FILE_NAME = "audit.jsonl"
RUNTIME_CONTEXT_FILE_NAME = "runtime_context.jsonl"
FINALIZER_VERDICTS_FILE_NAME = "finalizer_verdicts.jsonl"
AUDIT_RETENTION_EVENTS = 5000
AUDIT_MAX_BYTES = 8 * 1024 * 1024
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def locked_path(path: Path):
    lock = _lock_for_path(path)
    with lock:
        lock_handle = None
        file_locked = False
        try:
            lock_path = path.with_suffix(path.suffix + ".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = lock_path.open("a+b")
            if lock_path.stat().st_size == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            lock_handle.seek(0)
            _lock_file(lock_handle)
            file_locked = True
            yield
        finally:
            if lock_handle is not None:
                try:
                    if file_locked:
                        lock_handle.seek(0)
                        _unlock_file(lock_handle)
                finally:
                    lock_handle.close()


def _lock_file(handle: Any) -> None:
    if sys.platform.startswith("win"):
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except ImportError:
        return


def _unlock_file(handle: Any) -> None:
    if sys.platform.startswith("win"):
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        return


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_project_root(project_root: str | Path | None = None) -> Path:
    return (Path(project_root) if project_root else Path.cwd()).resolve()


def resolve_state_dir(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    path = Path(state_dir or DEFAULT_WORKFLOW_STATE_DIR)
    if not path.is_absolute():
        path = resolve_project_root(project_root) / path
    return path.resolve()


def definitions_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / DEFINITIONS_FILE_NAME


def runs_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / RUNS_FILE_NAME


def definition_versions_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / DEFINITION_VERSIONS_FILE_NAME


def audit_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / AUDIT_FILE_NAME


def runtime_context_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / RUNTIME_CONTEXT_FILE_NAME


def finalizer_verdicts_path(state_dir: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    return resolve_state_dir(state_dir, project_root=project_root) / FINALIZER_VERDICTS_FILE_NAME


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def workflow_node_definition_from_dict(snapshot: dict[str, Any]) -> WorkflowNodeDefinition:
    return WorkflowNodeDefinition(
        node_id=str(snapshot.get("node_id") or ""),
        node_type=str(snapshot.get("node_type") or ""),
        label=str(snapshot.get("label") or ""),
        tool_name=str(snapshot.get("tool_name") or ""),
        skill_name=str(snapshot.get("skill_name") or ""),
        assigned_agent=str(snapshot.get("assigned_agent") or ""),
        arguments=dict(snapshot.get("arguments") or {}),
        depends_on=tuple(str(item) for item in snapshot.get("depends_on") or []),
        review_gate=str(snapshot.get("review_gate") or ""),
        retry_policy=dict(snapshot.get("retry_policy") or {}),
        metadata=dict(snapshot.get("metadata") or {}),
    )


def workflow_definition_from_dict(snapshot: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=str(snapshot.get("name") or ""),
        version=str(snapshot.get("version") or "0.1.0"),
        description=str(snapshot.get("description") or ""),
        nodes=tuple(
            workflow_node_definition_from_dict(node)
            for node in snapshot.get("nodes") or []
            if isinstance(node, dict)
        ),
        metadata=dict(snapshot.get("metadata") or {}),
    )


def workflow_node_run_from_dict(snapshot: dict[str, Any]) -> WorkflowNodeRun:
    return WorkflowNodeRun(
        node_id=str(snapshot.get("node_id") or ""),
        status=str(snapshot.get("status") or "pending"),
        attempts=int(snapshot.get("attempts") or 0),
        started_at=str(snapshot.get("started_at") or ""),
        finished_at=str(snapshot.get("finished_at") or ""),
        outputs=dict(snapshot.get("outputs") or {}),
        error=str(snapshot.get("error") or ""),
        assigned_agent=str(snapshot.get("assigned_agent") or ""),
    )


def workflow_run_from_dict(snapshot: dict[str, Any]) -> WorkflowRun:
    nodes_snapshot = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), dict) else {}
    return WorkflowRun(
        run_id=str(snapshot.get("run_id") or ""),
        workflow_name=str(snapshot.get("workflow_name") or ""),
        workflow_version=str(snapshot.get("workflow_version") or ""),
        status=str(snapshot.get("status") or "pending"),
        inputs=dict(snapshot.get("inputs") or {}),
        nodes={
            str(node_id): workflow_node_run_from_dict(node)
            for node_id, node in nodes_snapshot.items()
            if isinstance(node, dict)
        },
        artifacts=list(snapshot.get("artifacts") or []),
        events=list(snapshot.get("events") or []),
        created_at=str(snapshot.get("created_at") or ""),
        updated_at=str(snapshot.get("updated_at") or ""),
    )


class JsonWorkflowStore:
    def __init__(
        self,
        state_dir: str | Path | None = None,
        *,
        project_root: str | Path | None = None,
        collaboration_task_port: CollaborationTaskFinalizerPort | None = None,
    ):
        self.project_root = resolve_project_root(project_root)
        self.state_dir = resolve_state_dir(state_dir, project_root=project_root)
        self._definitions_path = definitions_path(self.state_dir)
        self._runs_path = runs_path(self.state_dir)
        self._definition_versions_path = definition_versions_path(self.state_dir)
        self._audit_path = audit_path(self.state_dir)
        self._runtime_context_path = runtime_context_path(self.state_dir)
        self._finalizer_verdicts_path = finalizer_verdicts_path(self.state_dir)
        self._collaboration_task_port = collaboration_task_port

    def save_definition(self, definition: WorkflowDefinition, *, actor: str = "", reason: str = "", record_history: bool = True) -> None:
        definition_snapshot = definition.snapshot()
        changed = True
        with locked_path(self._definitions_path):
            data = load_json_object(self._definitions_path)
            definitions = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
            changed = definitions.get(definition.name) != definition_snapshot
            if changed:
                definitions[definition.name] = definition_snapshot
                write_json_object(self._definitions_path, {"definitions": definitions})
        # The auto-advance daemon periodically materializes definitions. Avoid
        # turning an unchanged heartbeat into an unbounded audit stream.
        if not changed:
            return
        if record_history:
            self.record_definition_version(definition, actor=actor, action="save_definition", reason=reason)
        self.record_audit(
            "definition_saved",
            workflow_name=definition.name,
            actor=actor,
            message=reason or f"Saved workflow definition {definition.name}",
            payload={"version": definition.version, "node_count": len(definition.nodes)},
        )

    def delete_definition(self, name: str, *, actor: str = "") -> bool:
        with locked_path(self._definitions_path):
            data = load_json_object(self._definitions_path)
            definitions = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
            if name not in definitions:
                return False
            snapshot = definitions.get(name)
            del definitions[name]
            write_json_object(self._definitions_path, {"definitions": definitions})
        self.record_audit(
            "definition_deleted",
            workflow_name=name,
            actor=actor,
            message=f"Deleted workflow definition {name}",
            payload={"definition": snapshot if isinstance(snapshot, dict) else {}},
        )
        return True

    def load_definition(self, name: str) -> WorkflowDefinition | None:
        with locked_path(self._definitions_path):
            data = load_json_object(self._definitions_path)
        definitions = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
        snapshot = definitions.get(name)
        if not isinstance(snapshot, dict):
            return None
        return workflow_definition_from_dict(snapshot)

    def list_definitions(self) -> list[WorkflowDefinition]:
        with locked_path(self._definitions_path):
            data = load_json_object(self._definitions_path)
        definitions = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
        return [
            workflow_definition_from_dict(snapshot)
            for snapshot in definitions.values()
            if isinstance(snapshot, dict)
        ]

    def record_definition_version(self, definition: WorkflowDefinition, *, actor: str = "", action: str = "", reason: str = "") -> dict[str, Any]:
        with locked_path(self._definition_versions_path):
            data = load_json_object(self._definition_versions_path)
            versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
            workflow_versions = versions.get(definition.name) if isinstance(versions.get(definition.name), list) else []
            entry = {
                "version_id": f"{utc_now().replace(':', '').replace('+', 'Z')}-{uuid4().hex[:8]}",
                "saved_at": utc_now(),
                "workflow_name": definition.name,
                "definition_version": definition.version,
                "actor": actor or "system",
                "action": action or "save_definition",
                "reason": reason,
                "node_count": len(definition.nodes),
                "definition": definition.snapshot(),
            }
            workflow_versions.append(entry)
            versions[definition.name] = workflow_versions[-50:]
            write_json_object(self._definition_versions_path, {"versions": versions})
        return entry

    def list_definition_versions(self, name: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with locked_path(self._definition_versions_path):
            data = load_json_object(self._definition_versions_path)
        versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
        workflow_versions = versions.get(name) if isinstance(versions.get(name), list) else []
        return [dict(item) for item in reversed(workflow_versions[-limit:]) if isinstance(item, dict)]

    def rollback_definition(self, name: str, version_id: str, *, actor: str = "") -> WorkflowDefinition | None:
        with locked_path(self._definition_versions_path):
            data = load_json_object(self._definition_versions_path)
        versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
        workflow_versions = versions.get(name) if isinstance(versions.get(name), list) else []
        selected = next(
            (item for item in workflow_versions if isinstance(item, dict) and str(item.get("version_id") or "") == version_id),
            None,
        )
        if not isinstance(selected, dict) or not isinstance(selected.get("definition"), dict):
            return None
        definition = workflow_definition_from_dict(selected["definition"])
        self.save_definition(
            definition,
            actor=actor,
            reason=f"Rolled back to workflow definition version {version_id}",
            record_history=True,
        )
        self.record_audit(
            "definition_rolled_back",
            workflow_name=name,
            actor=actor,
            message=f"Rolled back workflow definition {name} to {version_id}",
            payload={"version_id": version_id, "definition_version": definition.version},
        )
        return definition

    def record_audit(self, action: str, *, workflow_name: str = "", actor: str = "", message: str = "", payload: dict[str, Any] | None = None) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": utc_now(),
            "action": action,
            "workflow_name": workflow_name,
            "actor": actor or "system",
            "message": message,
            "payload": payload or {},
        }
        with locked_path(self._audit_path):
            with self._audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if self._audit_path.stat().st_size > AUDIT_MAX_BYTES:
                _trim_jsonl_tail(self._audit_path, keep=AUDIT_RETENTION_EVENTS)

    def list_audit_events(self, *, workflow_name: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if not self._audit_path.exists():
            return []
        try:
            with locked_path(self._audit_path):
                lines = self._audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if workflow_name and str(event.get("workflow_name") or "") != workflow_name:
                continue
            events.append(event)
            if len(events) >= limit:
                break
        return events

    def save_run(self, run: WorkflowRun) -> None:
        with locked_path(self._runs_path):
            data = load_json_object(self._runs_path)
            runs = data.get("runs") if isinstance(data.get("runs"), dict) else {}
            runs[run.run_id] = run.snapshot()
            write_json_object(self._runs_path, {"runs": runs})
        self._record_runtime_contracts(run)

    def load_run(self, run_id: str) -> WorkflowRun | None:
        with locked_path(self._runs_path):
            data = load_json_object(self._runs_path)
        runs = data.get("runs") if isinstance(data.get("runs"), dict) else {}
        snapshot = runs.get(run_id)
        if not isinstance(snapshot, dict):
            return None
        return workflow_run_from_dict(snapshot)

    def list_runs(self, *, workflow_name: str = "") -> list[WorkflowRun]:
        with locked_path(self._runs_path):
            data = load_json_object(self._runs_path)
        runs = data.get("runs") if isinstance(data.get("runs"), dict) else {}
        loaded = [
            workflow_run_from_dict(snapshot)
            for snapshot in runs.values()
            if isinstance(snapshot, dict)
        ]
        if workflow_name:
            loaded = [run for run in loaded if run.workflow_name == workflow_name]
        return sorted(loaded, key=lambda run: run.updated_at or run.created_at, reverse=True)

    def archive_run(self, run_id: str, *, actor: str = "", reason: str = "") -> WorkflowRun | None:
        run = self.load_run(run_id)
        if run is None:
            return None
        archived = replace(run, status="archived", updated_at=utc_now())
        self.save_run(archived)
        self.record_audit(
            "run_archived",
            workflow_name=archived.workflow_name,
            actor=actor,
            message=reason or f"Archived workflow run {run_id}",
            payload={"run_id": run_id, "previous_status": run.status},
        )
        return archived

    def delete_run(self, run_id: str, *, actor: str = "", reason: str = "") -> dict[str, Any]:
        with locked_path(self._runs_path):
            data = load_json_object(self._runs_path)
            runs = data.get("runs") if isinstance(data.get("runs"), dict) else {}
            snapshot = runs.get(run_id)
            if not isinstance(snapshot, dict):
                return {"deleted": False, "run_id": run_id}
            del runs[run_id]
            write_json_object(self._runs_path, {"runs": runs})
        workflow_name = str(snapshot.get("workflow_name") or "")
        self.record_audit(
            "run_deleted",
            workflow_name=workflow_name,
            actor=actor,
            message=reason or f"Deleted workflow run {run_id}",
            payload={"run_id": run_id, "status": str(snapshot.get("status") or ""), "workflow_name": workflow_name},
        )
        return {"deleted": True, "run_id": run_id, "workflow_name": workflow_name, "status": str(snapshot.get("status") or "")}

    def cleanup_runs(self, *, workflow_name: str = "", keep_recent: int = 30, include_archived: bool = True, actor: str = "", reason: str = "") -> dict[str, Any]:
        protected_statuses = {"pending", "running", "waiting", "waiting_review"}
        runs = self.list_runs(workflow_name=workflow_name)
        retained_recent = {run.run_id for run in runs[: max(0, keep_recent)]}
        removed: list[dict[str, Any]] = []
        for run in runs:
            if run.run_id in retained_recent:
                continue
            if run.status in protected_statuses:
                continue
            if run.status == "archived" and not include_archived:
                continue
            result = self.delete_run(run.run_id, actor=actor, reason=reason or "Workflow run cleanup")
            if result.get("deleted"):
                removed.append(result)
        self.record_audit(
            "runs_cleaned",
            workflow_name=workflow_name,
            actor=actor,
            message=reason or f"Cleaned {len(removed)} workflow run(s)",
            payload={"workflow_name": workflow_name, "keep_recent": keep_recent, "removed": removed},
        )
        return {"removed": len(removed), "removed_runs": removed, "kept_recent": max(0, keep_recent), "workflow_name": workflow_name}

    def list_runtime_context_patches(self, *, run_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        events = _read_jsonl_objects(self._runtime_context_path)
        if run_id:
            events = [event for event in events if str(event.get("run_id") or "") == run_id]
        return events[-max(1, limit):]

    def list_finalizer_verdicts(self, *, run_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        verdicts = _read_jsonl_objects(self._finalizer_verdicts_path)
        if run_id:
            verdicts = [item for item in verdicts if str(item.get("run_id") or "") == run_id]
        return verdicts[-max(1, limit):]

    def _record_runtime_contracts(self, run: WorkflowRun) -> None:
        definition = self.load_definition(run.workflow_name)
        if definition is None:
            return
        try:
            context_id = f"workflow:{run.run_id}"
            context_record = {
                "schema_version": WORKFLOW_RUNTIME_CONTRACT_VERSION,
                "at": utc_now(),
                "run_id": run.run_id,
                "workflow_name": run.workflow_name,
                "context_id": context_id,
                "patches": [patch.snapshot() for patch in workflow_run_context_patches(definition, run, context_id=context_id)],
            }
            _append_jsonl_object(self._runtime_context_path, context_record)
            if run.status in {RUN_SUCCEEDED, RUN_FAILED, RUN_BLOCKED}:
                summary = workflow_run_execution_summary(definition, run)
                verdict = ExecutionFinalizer().finalize(summary)
                _append_jsonl_object(
                    self._finalizer_verdicts_path,
                    {
                        "schema_version": WORKFLOW_RUNTIME_CONTRACT_VERSION,
                        "at": utc_now(),
                        "run_id": run.run_id,
                        "workflow_name": run.workflow_name,
                        "context_id": context_id,
                        "execution_summary": {
                            "task_id": summary.task_id,
                            "status": summary.status,
                            "success": summary.success,
                            "success_criteria": list(summary.success_criteria),
                            "metadata": dict(summary.metadata or {}),
                        },
                        "verdict": verdict.snapshot(),
                    },
                )
                sync_result = sync_workflow_verdict_to_task(
                    run,
                    verdict,
                    project_root=self.project_root,
                    collaboration_port=self._collaboration_task_port,
                )
                self.record_audit(
                    "workflow_finalizer_task_sync",
                    workflow_name=run.workflow_name,
                    actor="workflow_store",
                    message=sync_result.message,
                    payload=sync_result.snapshot(),
                )
        except Exception as exc:
            self.record_audit(
                "runtime_contract_record_failed",
                workflow_name=run.workflow_name,
                actor="workflow_store",
                message=f"Failed to record workflow runtime contracts for {run.run_id}",
                payload={"run_id": run.run_id, "error": f"{type(exc).__name__}: {exc}"},
            )


def _append_jsonl_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_path(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _trim_jsonl_tail(path: Path, *, keep: int) -> None:
    """Bound an append-only runtime log while keeping the newest evidence."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        retained = lines[-max(1, keep):]
        temporary = path.with_suffix(path.suffix + ".trim.tmp")
        temporary.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Logging must never break workflow execution when a file is locked by
        # an external inspector or antivirus process.
        return


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with locked_path(path):
            lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows
