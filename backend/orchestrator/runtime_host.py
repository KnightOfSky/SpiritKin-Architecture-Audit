from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from backend.orchestrator.workflow_graph import (
    NODE_BLOCKED,
    NODE_FAILED,
    NODE_PENDING,
    NODE_RUNNING,
    NODE_WAITING,
    NODE_WAITING_REVIEW,
    RUN_RUNNING,
    RUN_WAITING,
    RUN_WAITING_REVIEW,
    WorkflowNodeRun,
    WorkflowRun,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore, workflow_run_from_dict
from backend.state_store import StateCorruptionError, locked_state_path, read_json_state

RUNTIME_HOST_SCHEMA_VERSION = "spiritkin.runtime_host.v1"
RUNTIME_CHECKPOINT_SCHEMA_VERSION = "spiritkin.runtime_checkpoint.v1"
HOST_TYPES = frozenset({"desktop", "ios", "cloud", "remote", "edge"})
HOST_STATUSES = frozenset({"online", "draining", "offline", "revoked"})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_SECRET_KEY_PATTERN = re.compile(r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key)", re.IGNORECASE)


def _utc_now(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, tz=UTC).isoformat(timespec="seconds")


def _bounded_text(value: Any, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _required_id(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid {name}")
    return normalized


def _capabilities(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        normalized = str(item or "").strip().lower()
        if not normalized or len(normalized) > 100:
            continue
        if normalized not in result:
            result.append(normalized)
        if len(result) >= 64:
            break
    return result


def _safe_endpoint_ref(value: Any) -> str:
    raw = str(value or "").strip()[:500]
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError("endpoint_ref must be an http(s) or ws(s) URL")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SECRET_KEY_PATTERN.search(str(key)) else _redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item) for item in value]
    return value


class RuntimeHostRegistry:
    def __init__(self, path: str | Path = "state/runtime/hosts.json", *, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock
        self._checkpoint_validator: Callable[..., dict[str, Any]] | None = None

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_HOST_SCHEMA_VERSION,
            "hosts": {},
            "leases": {},
            "epochs": {},
            "migrations": {},
            "events": [],
        }

    def _load(self) -> dict[str, Any]:
        state = read_json_state(self.path, self._empty_state(), strict=True)
        if state.get("schema_version") != RUNTIME_HOST_SCHEMA_VERSION:
            raise StateCorruptionError(self.path, f"unsupported schema_version: {state.get('schema_version')!r}")
        for key in ("hosts", "leases", "epochs", "migrations"):
            if not isinstance(state.get(key), dict):
                raise StateCorruptionError(self.path, f"runtime host field {key!r} must be an object")
        if not isinstance(state.get("events"), list):
            raise StateCorruptionError(self.path, "runtime host field 'events' must be an array")
        for host_id, host in state["hosts"].items():
            if not isinstance(host, dict):
                raise StateCorruptionError(self.path, f"runtime host record {host_id!r} must be an object")
        for workspace_id, lease in state["leases"].items():
            if not isinstance(lease, dict):
                raise StateCorruptionError(self.path, f"runtime lease record {workspace_id!r} must be an object")
        for workspace_id, epoch in state["epochs"].items():
            if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
                raise StateCorruptionError(self.path, f"runtime epoch {workspace_id!r} must be a non-negative integer")
        for migration_id, migration in state["migrations"].items():
            if not isinstance(migration, dict):
                raise StateCorruptionError(self.path, f"runtime migration record {migration_id!r} must be an object")
        if any(not isinstance(event, dict) for event in state["events"]):
            raise StateCorruptionError(self.path, "runtime host events must contain only objects")
        return state

    def bind_checkpoint_validator(self, validator: Callable[..., dict[str, Any]]) -> None:
        self._checkpoint_validator = validator

    def _validate_migration_checkpoint(
        self,
        *,
        checkpoint_id: str,
        workspace_id: str,
        source_host_id: str,
        source_epoch: int,
    ) -> dict[str, Any]:
        if self._checkpoint_validator is None:
            raise RuntimeError("runtime checkpoint validation is unavailable")
        return self._checkpoint_validator(
            checkpoint_id=checkpoint_id,
            workspace_id=workspace_id,
            source_host_id=source_host_id,
            source_epoch=int(source_epoch),
        )

    def register_host(
        self,
        *,
        host_id: str,
        workspace_id: str,
        host_type: str,
        capabilities: Any = (),
        label: str = "",
        can_execute_workflows: bool = False,
        can_observe: bool = False,
        priority: int = 0,
        heartbeat_ttl_seconds: float = 45.0,
        endpoint_ref: str = "",
        requested_by: str = "system",
    ) -> dict[str, Any]:
        host_id = _required_id(host_id, "host_id")
        workspace_id = _required_id(workspace_id, "workspace_id")
        host_type = str(host_type or "").strip().lower()
        if host_type not in HOST_TYPES:
            raise ValueError(f"unsupported host_type: {host_type}")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            current = state["hosts"].get(host_id)
            if isinstance(current, dict) and str(current.get("workspace_id") or "") != workspace_id:
                raise PermissionError("runtime host cannot move to another workspace")
            registered_at = str((current or {}).get("registered_at") or _utc_now(now))
            host = {
                "host_id": host_id,
                "workspace_id": workspace_id,
                "host_type": host_type,
                "label": _bounded_text(label or host_id, limit=100),
                "status": "online",
                "capabilities": _capabilities(capabilities),
                "can_execute_workflows": bool(can_execute_workflows),
                "can_observe": bool(can_observe),
                "priority": max(-100, min(100, int(priority))),
                "heartbeat_ttl_seconds": max(5.0, min(300.0, float(heartbeat_ttl_seconds))),
                "endpoint_ref": _safe_endpoint_ref(endpoint_ref),
                "registered_at": registered_at,
                "last_seen_at": _utc_now(now),
                "last_seen_timestamp": now,
                "registered_by": _bounded_text((current or {}).get("registered_by") or requested_by, limit=120),
                "updated_by": _bounded_text(requested_by, limit=120),
                "revision": int((current or {}).get("revision") or 0) + 1,
            }
            state["hosts"][host_id] = host
            self._event(state, "host_registered" if current is None else "host_updated", workspace_id, host_id, requested_by)
            _write_json_atomic(self.path, state)
            return self._public_host(host)

    def heartbeat(
        self,
        host_id: str,
        *,
        capabilities: Any | None = None,
        requested_by: str = "system",
        elect_if_needed: bool = True,
    ) -> dict[str, Any]:
        host_id = _required_id(host_id, "host_id")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            host = state["hosts"].get(host_id)
            if not isinstance(host, dict):
                raise KeyError(f"unknown runtime host: {host_id}")
            if host.get("status") == "revoked":
                raise PermissionError("runtime host is revoked")
            host["status"] = "online"
            host["last_seen_at"] = _utc_now(now)
            host["last_seen_timestamp"] = now
            host["updated_by"] = _bounded_text(requested_by, limit=120)
            host["revision"] = int(host.get("revision") or 0) + 1
            if capabilities is not None:
                host["capabilities"] = _capabilities(capabilities)
            state["hosts"][host_id] = host
            lease = state["leases"].get(str(host.get("workspace_id") or ""))
            if self._lease_is_valid(state, lease, now) and str(lease.get("host_id") or "") == host_id:
                lease["renewed_at"] = _utc_now(now)
                lease["lease_expires_at"] = now + float(host.get("heartbeat_ttl_seconds") or 45.0)
                state["leases"][str(host["workspace_id"])] = lease
            elif elect_if_needed and bool(host.get("can_execute_workflows")):
                self._elect_locked(state, str(host["workspace_id"]), now, requested_by=requested_by)
            self._event(state, "host_heartbeat", str(host["workspace_id"]), host_id, requested_by)
            _write_json_atomic(self.path, state)
            lease_result = state["leases"].get(str(host["workspace_id"]))
            return {
                "host": self._public_host(host),
                "lease": dict(lease_result)
                if isinstance(lease_result, dict) and str(lease_result.get("host_id") or "") == host_id
                else self._public_lease(lease_result, state, now),
            }

    def set_host_status(self, host_id: str, status: str, *, requested_by: str = "system") -> dict[str, Any]:
        host_id = _required_id(host_id, "host_id")
        status = str(status or "").strip().lower()
        if status not in HOST_STATUSES:
            raise ValueError(f"unsupported host status: {status}")
        with locked_state_path(self.path):
            state = self._load()
            host = state["hosts"].get(host_id)
            if not isinstance(host, dict):
                raise KeyError(f"unknown runtime host: {host_id}")
            host["status"] = status
            host["updated_by"] = _bounded_text(requested_by, limit=120)
            host["revision"] = int(host.get("revision") or 0) + 1
            state["hosts"][host_id] = host
            self._event(state, f"host_{status}", str(host["workspace_id"]), host_id, requested_by)
            _write_json_atomic(self.path, state)
            return self._public_host(host)

    def elect(
        self,
        workspace_id: str,
        *,
        requested_by: str = "system",
        reveal_fencing_token: bool = False,
    ) -> dict[str, Any]:
        workspace_id = _required_id(workspace_id, "workspace_id")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            lease = self._elect_locked(state, workspace_id, now, requested_by=requested_by)
            _write_json_atomic(self.path, state)
            return dict(lease) if reveal_fencing_token else self._public_lease(lease, state, now)

    def _elect_locked(self, state: dict[str, Any], workspace_id: str, now: float, *, requested_by: str) -> dict[str, Any]:
        current = state["leases"].get(workspace_id)
        if self._lease_is_valid(state, current, now):
            return current
        candidates = [
            host
            for host in state["hosts"].values()
            if isinstance(host, dict)
            and str(host.get("workspace_id") or "") == workspace_id
            and bool(host.get("can_execute_workflows"))
            and self._host_is_eligible(host, now)
        ]
        if not candidates:
            raise RuntimeError(f"no eligible runtime host for workspace: {workspace_id}")
        type_rank = {"cloud": 5, "remote": 4, "desktop": 3, "edge": 2, "ios": 1}
        candidates.sort(
            key=lambda host: (
                -int(host.get("priority") or 0),
                -type_rank.get(str(host.get("host_type") or ""), 0),
                str(host.get("host_id") or ""),
            )
        )
        selected = candidates[0]
        epoch = int(state["epochs"].get(workspace_id) or 0) + 1
        state["epochs"][workspace_id] = epoch
        lease = {
            "lease_id": f"lease-{uuid4().hex}",
            "workspace_id": workspace_id,
            "host_id": selected["host_id"],
            "epoch": epoch,
            "fencing_token": uuid4().hex,
            "acquired_at": _utc_now(now),
            "renewed_at": _utc_now(now),
            "lease_expires_at": now + float(selected.get("heartbeat_ttl_seconds") or 45.0),
            "status": "active",
        }
        state["leases"][workspace_id] = lease
        self._event(state, "host_elected", workspace_id, str(selected["host_id"]), requested_by, {"epoch": epoch})
        return lease

    @contextmanager
    def authorized_lease(
        self,
        *,
        workspace_id: str,
        host_id: str,
        fencing_token: str,
        epoch: int,
    ) -> Iterator[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "workspace_id")
        host_id = _required_id(host_id, "host_id")
        with locked_state_path(self.path):
            state = self._load()
            lease = state["leases"].get(workspace_id)
            if not self._lease_is_valid(state, lease, float(self.clock())):
                raise PermissionError("runtime host lease is absent or expired")
            if (
                str(lease.get("host_id") or "") != host_id
                or str(lease.get("fencing_token") or "") != str(fencing_token or "")
                or int(lease.get("epoch") or 0) != int(epoch)
            ):
                raise PermissionError("runtime host fencing token is stale")
            yield dict(lease)

    def prepare_migration(
        self,
        *,
        workspace_id: str,
        checkpoint_id: str,
        source_host_id: str,
        target_host_id: str,
        fencing_token: str,
        epoch: int,
        requested_by: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("runtime migration requires explicit confirmation")
        workspace_id = _required_id(workspace_id, "workspace_id")
        checkpoint_id = _required_id(checkpoint_id, "checkpoint_id")
        source_host_id = _required_id(source_host_id, "source_host_id")
        target_host_id = _required_id(target_host_id, "target_host_id")
        if source_host_id == target_host_id:
            raise ValueError("migration target must differ from source")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            lease = state["leases"].get(workspace_id)
            if not self._lease_matches(lease, source_host_id, fencing_token, epoch) or not self._lease_is_valid(state, lease, now):
                raise PermissionError("source runtime host does not own the active lease")
            target = state["hosts"].get(target_host_id)
            if not isinstance(target, dict) or str(target.get("workspace_id") or "") != workspace_id:
                raise KeyError(f"unknown target runtime host: {target_host_id}")
            if not bool(target.get("can_execute_workflows")) or not self._host_is_eligible(target, now):
                raise RuntimeError("target runtime host is not eligible")
            self._validate_migration_checkpoint(
                checkpoint_id=checkpoint_id,
                workspace_id=workspace_id,
                source_host_id=source_host_id,
                source_epoch=int(epoch),
            )
            migration = {
                "migration_id": f"migration-{uuid4().hex}",
                "workspace_id": workspace_id,
                "checkpoint_id": checkpoint_id,
                "source_host_id": source_host_id,
                "target_host_id": target_host_id,
                "source_epoch": int(epoch),
                "status": "prepared",
                "prepared_at": _utc_now(now),
                "expires_at": now + 120.0,
                "requested_by": _bounded_text(requested_by, limit=120),
            }
            state["migrations"][migration["migration_id"]] = migration
            self._event(state, "migration_prepared", workspace_id, source_host_id, requested_by, {"migration_id": migration["migration_id"]})
            _write_json_atomic(self.path, state)
            return dict(migration)

    def request_migration(
        self,
        *,
        workspace_id: str,
        checkpoint_id: str,
        target_host_id: str,
        requested_by: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        """Create a handoff request for a workspace-scoped controller.

        The controller never receives the current execution host's fencing token;
        the registry validates the active lease inside the same locked transaction.
        """

        if not confirmed:
            raise PermissionError("runtime migration requires explicit confirmation")
        workspace_id = _required_id(workspace_id, "workspace_id")
        checkpoint_id = _required_id(checkpoint_id, "checkpoint_id")
        target_host_id = _required_id(target_host_id, "target_host_id")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            lease = state["leases"].get(workspace_id)
            if not self._lease_is_valid(state, lease, now):
                raise RuntimeError("workspace has no active runtime host lease")
            source_host_id = str(lease.get("host_id") or "")
            if source_host_id == target_host_id:
                raise ValueError("migration target must differ from source")
            target = state["hosts"].get(target_host_id)
            if not isinstance(target, dict) or str(target.get("workspace_id") or "") != workspace_id:
                raise KeyError(f"unknown target runtime host: {target_host_id}")
            if not bool(target.get("can_execute_workflows")) or not self._host_is_eligible(target, now):
                raise RuntimeError("target runtime host is not eligible")
            source_epoch = int(lease.get("epoch") or 0)
            self._validate_migration_checkpoint(
                checkpoint_id=checkpoint_id,
                workspace_id=workspace_id,
                source_host_id=source_host_id,
                source_epoch=source_epoch,
            )
            migration = {
                "migration_id": f"migration-{uuid4().hex}",
                "workspace_id": workspace_id,
                "checkpoint_id": checkpoint_id,
                "source_host_id": source_host_id,
                "target_host_id": target_host_id,
                "source_epoch": source_epoch,
                "status": "prepared",
                "prepared_at": _utc_now(now),
                "expires_at": now + 120.0,
                "requested_by": _bounded_text(requested_by, limit=120),
                "controller_requested": True,
            }
            state["migrations"][migration["migration_id"]] = migration
            self._event(state, "migration_requested", workspace_id, source_host_id, requested_by, {"migration_id": migration["migration_id"]})
            _write_json_atomic(self.path, state)
            return dict(migration)

    def claim_migration(
        self,
        migration_id: str,
        *,
        target_host_id: str,
        requested_by: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("runtime migration claim requires explicit confirmation")
        migration_id = _required_id(migration_id, "migration_id")
        target_host_id = _required_id(target_host_id, "target_host_id")
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
            migration = state["migrations"].get(migration_id)
            if not isinstance(migration, dict):
                raise KeyError(f"unknown runtime migration: {migration_id}")
            if migration.get("status") == "claimed" and str(migration.get("target_host_id") or "") == target_host_id:
                workspace_id = str(migration["workspace_id"])
                lease = state["leases"].get(workspace_id)
                if (
                    not self._lease_is_valid(state, lease, now)
                    or str(lease.get("host_id") or "") != target_host_id
                    or int(lease.get("epoch") or 0) != int(migration.get("target_epoch") or 0)
                    or str(lease.get("migration_id") or "") != migration_id
                ):
                    raise PermissionError("runtime migration claim is stale")
                return {"migration": dict(migration), "lease": dict(lease)}
            if migration.get("status") != "prepared" or float(migration.get("expires_at") or 0) <= now:
                raise PermissionError("runtime migration is no longer claimable")
            if str(migration.get("target_host_id") or "") != target_host_id:
                raise PermissionError("runtime migration belongs to another target host")
            target = state["hosts"].get(target_host_id)
            if not isinstance(target, dict) or not bool(target.get("can_execute_workflows")) or not self._host_is_eligible(target, now):
                raise RuntimeError("target runtime host is not eligible")
            workspace_id = str(migration["workspace_id"])
            source_lease = state["leases"].get(workspace_id)
            if (
                not self._lease_is_valid(state, source_lease, now)
                or str(source_lease.get("host_id") or "") != str(migration.get("source_host_id") or "")
                or int(source_lease.get("epoch") or 0) != int(migration.get("source_epoch") or 0)
            ):
                raise PermissionError("runtime migration source lease is stale")
            self._validate_migration_checkpoint(
                checkpoint_id=str(migration.get("checkpoint_id") or ""),
                workspace_id=workspace_id,
                source_host_id=str(migration.get("source_host_id") or ""),
                source_epoch=int(migration.get("source_epoch") or 0),
            )
            epoch = int(state["epochs"].get(workspace_id) or 0) + 1
            state["epochs"][workspace_id] = epoch
            lease = {
                "lease_id": f"lease-{uuid4().hex}",
                "workspace_id": workspace_id,
                "host_id": target_host_id,
                "epoch": epoch,
                "fencing_token": uuid4().hex,
                "acquired_at": _utc_now(now),
                "renewed_at": _utc_now(now),
                "lease_expires_at": now + float(target.get("heartbeat_ttl_seconds") or 45.0),
                "status": "active",
                "migration_id": migration_id,
            }
            state["leases"][workspace_id] = lease
            migration["status"] = "claimed"
            migration["claimed_at"] = _utc_now(now)
            migration["target_epoch"] = epoch
            migration["claimed_by"] = _bounded_text(requested_by, limit=120)
            state["migrations"][migration_id] = migration
            self._event(state, "migration_claimed", workspace_id, target_host_id, requested_by, {"migration_id": migration_id, "epoch": epoch})
            _write_json_atomic(self.path, state)
            return {"migration": dict(migration), "lease": dict(lease)}

    def snapshot(self, *, workspace_id: str = "", include_private: bool = False) -> dict[str, Any]:
        now = float(self.clock())
        with locked_state_path(self.path):
            state = self._load()
        hosts = []
        for host in state["hosts"].values():
            if not isinstance(host, dict) or (workspace_id and str(host.get("workspace_id") or "") != workspace_id):
                continue
            item = dict(host) if include_private else self._public_host(host)
            item["effective_status"] = "online" if self._host_is_eligible(host, now, require_execution=False) else str(host.get("status") or "offline")
            item["execution"] = self._execution_status(str(host.get("host_id") or ""), str(host.get("workspace_id") or ""))
            hosts.append(item)
        hosts.sort(key=lambda item: (str(item.get("workspace_id") or ""), -int(item.get("priority") or 0), str(item.get("host_id") or "")))
        leases = [
            self._public_lease(lease, state, now)
            for key, lease in state["leases"].items()
            if isinstance(lease, dict) and (not workspace_id or key == workspace_id)
        ]
        migrations = [
            dict(item)
            for item in state["migrations"].values()
            if isinstance(item, dict) and (not workspace_id or str(item.get("workspace_id") or "") == workspace_id)
        ]
        return {
            "schema_version": RUNTIME_HOST_SCHEMA_VERSION,
            "generated_at": _utc_now(now),
            "workspace_id": workspace_id,
            "host_count": len(hosts),
            "online_count": sum(1 for item in hosts if item.get("effective_status") == "online"),
            "hosts": hosts,
            "leases": leases,
            "migrations": sorted(migrations, key=lambda item: str(item.get("prepared_at") or ""), reverse=True)[:100],
            "events": [
                dict(item)
                for item in state["events"][-100:]
                if isinstance(item, dict) and (not workspace_id or str(item.get("workspace_id") or "") == workspace_id)
            ],
        }

    @staticmethod
    def _lease_matches(lease: Any, host_id: str, fencing_token: str, epoch: int) -> bool:
        return bool(
            isinstance(lease, dict)
            and str(lease.get("host_id") or "") == host_id
            and str(lease.get("fencing_token") or "") == str(fencing_token or "")
            and int(lease.get("epoch") or 0) == int(epoch)
        )

    def _lease_is_valid(self, state: dict[str, Any], lease: Any, now: float) -> bool:
        if not isinstance(lease, dict) or str(lease.get("status") or "") != "active":
            return False
        if float(lease.get("lease_expires_at") or 0) <= now:
            return False
        host = state["hosts"].get(str(lease.get("host_id") or ""))
        return isinstance(host, dict) and bool(host.get("can_execute_workflows")) and self._host_is_eligible(host, now)

    @staticmethod
    def _host_is_eligible(host: dict[str, Any], now: float, *, require_execution: bool = True) -> bool:
        if str(host.get("status") or "") != "online":
            return False
        if require_execution and not bool(host.get("can_execute_workflows")):
            return False
        ttl = max(5.0, float(host.get("heartbeat_ttl_seconds") or 45.0))
        return now - float(host.get("last_seen_timestamp") or 0) <= ttl

    @staticmethod
    def _public_host(host: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in host.items() if key not in {"endpoint_ref", "registered_by", "updated_by"}}

    def _execution_status(self, host_id: str, workspace_id: str) -> dict[str, Any]:
        status_path = self.path.parent / "host-status" / f"{host_id.replace(':', '_')}.json"
        payload = read_json_state(status_path, {})
        if (
            payload.get("schema_version") != "spiritkin.runtime_host_execution.v1"
            or str(payload.get("host_id") or "") != host_id
            or str(payload.get("workspace_id") or "") != workspace_id
        ):
            return {"status": "not_reported", "epoch": 0, "updated_at": ""}
        latest = payload.get("last_execution") if isinstance(payload.get("last_execution"), dict) else {}
        return {
            "status": str(payload.get("status") or latest.get("status") or "not_reported"),
            "epoch": int(payload.get("epoch") or latest.get("epoch") or 0),
            "run_count": int(latest.get("run_count") or 0),
            "advanced_steps": int(latest.get("advanced_steps") or 0),
            "error_code": _bounded_text(latest.get("error_code"), limit=80),
            "updated_at": str(payload.get("updated_at") or ""),
        }

    def _public_lease(self, lease: Any, state: dict[str, Any], now: float) -> dict[str, Any]:
        if not isinstance(lease, dict):
            return {}
        return {
            key: value
            for key, value in lease.items()
            if key != "fencing_token"
        } | {"effective_status": "active" if self._lease_is_valid(state, lease, now) else "expired"}

    @staticmethod
    def _event(
        state: dict[str, Any],
        action: str,
        workspace_id: str,
        host_id: str,
        actor: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        state["events"].append(
            {
                "event_id": f"runtime-event-{uuid4().hex}",
                "at": _utc_now(),
                "action": action,
                "workspace_id": workspace_id,
                "host_id": host_id,
                "actor": _bounded_text(actor, limit=120),
                "detail": dict(detail or {}),
            }
        )
        state["events"] = state["events"][-1000:]


class RuntimeCheckpointStore:
    def __init__(
        self,
        registry: RuntimeHostRegistry,
        *,
        path: str | Path = "state/runtime/checkpoints.json",
        workflow_store: JsonWorkflowStore | None = None,
        clock=time.time,
    ):
        self.registry = registry
        self.path = Path(path).resolve()
        self.workflow_store = workflow_store or JsonWorkflowStore()
        self.clock = clock
        self.registry.bind_checkpoint_validator(self.validate_for_migration)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_CHECKPOINT_SCHEMA_VERSION,
            "checkpoints": {},
            "latest_by_run": {},
            "resumes": {},
        }

    def _load(self) -> dict[str, Any]:
        state = read_json_state(self.path, self._empty_state(), strict=True)
        if state.get("schema_version") != RUNTIME_CHECKPOINT_SCHEMA_VERSION:
            raise StateCorruptionError(self.path, f"unsupported schema_version: {state.get('schema_version')!r}")
        for key in ("checkpoints", "latest_by_run", "resumes"):
            if not isinstance(state.get(key), dict):
                raise StateCorruptionError(self.path, f"runtime checkpoint field {key!r} must be an object")
        for checkpoint_id, checkpoint in state["checkpoints"].items():
            if not isinstance(checkpoint, dict):
                raise StateCorruptionError(self.path, f"runtime checkpoint record {checkpoint_id!r} must be an object")
        for run_id, checkpoint_id in state["latest_by_run"].items():
            if not isinstance(checkpoint_id, str):
                raise StateCorruptionError(self.path, f"latest checkpoint reference {run_id!r} must be a string")
        for resume_id, resume in state["resumes"].items():
            if not isinstance(resume, dict):
                raise StateCorruptionError(self.path, f"runtime resume record {resume_id!r} must be an object")
        return state

    def validate_for_migration(
        self,
        *,
        checkpoint_id: str,
        workspace_id: str,
        source_host_id: str,
        source_epoch: int,
    ) -> dict[str, Any]:
        checkpoint_id = _required_id(checkpoint_id, "checkpoint_id")
        with locked_state_path(self.path):
            state = self._load()
            checkpoint = state["checkpoints"].get(checkpoint_id)
            if not isinstance(checkpoint, dict):
                raise KeyError(f"unknown runtime checkpoint: {checkpoint_id}")
            if self._checksum(checkpoint) != str(checkpoint.get("checksum") or ""):
                raise ValueError("runtime checkpoint integrity validation failed")
            if str(checkpoint.get("workspace_id") or "") != workspace_id:
                raise PermissionError("runtime checkpoint belongs to another workspace")
            if str(checkpoint.get("source_host_id") or "") != source_host_id:
                raise PermissionError("runtime checkpoint belongs to another source host")
            if int(checkpoint.get("source_epoch") or 0) != int(source_epoch):
                raise PermissionError("runtime checkpoint source epoch is stale")
            if str(checkpoint.get("status") or "") != "active":
                raise PermissionError("runtime checkpoint is not active")
            run_id = str(checkpoint.get("run_id") or "")
            if str(state["latest_by_run"].get(run_id) or "") != checkpoint_id:
                raise PermissionError("runtime checkpoint is not the latest for its run")
            snapshot = checkpoint.get("run_snapshot")
            if not isinstance(snapshot, dict):
                raise ValueError("runtime checkpoint is missing its workflow snapshot")
            current = self.workflow_store.load_run(run_id)
            if current is not None:
                if str(current.inputs.get("workspace_id") or "").strip() != workspace_id:
                    raise PermissionError("workflow run belongs to another workspace")
                if str(current.updated_at or "") > str(snapshot.get("updated_at") or ""):
                    raise PermissionError("runtime checkpoint is stale compared with the stored workflow run")
            if bool(checkpoint.get("definition_present")):
                definition = self.workflow_store.load_definition(str(checkpoint.get("workflow_name") or ""))
                if definition is None or self._definition_digest(definition.snapshot()) != str(checkpoint.get("definition_digest") or ""):
                    raise PermissionError("workflow definition changed since the runtime checkpoint")
            return self._public_checkpoint(checkpoint)

    def create_checkpoint(
        self,
        run_id: str,
        *,
        workspace_id: str,
        host_id: str,
        fencing_token: str,
        epoch: int,
        reason: str = "periodic",
        requested_by: str = "system",
    ) -> dict[str, Any]:
        run_id = _required_id(run_id, "run_id")
        workspace_id = _required_id(workspace_id, "workspace_id")
        host_id = _required_id(host_id, "host_id")
        with self.registry.authorized_lease(
            workspace_id=workspace_id,
            host_id=host_id,
            fencing_token=fencing_token,
            epoch=epoch,
        ):
            run = self.workflow_store.load_run(run_id)
            if run is None:
                raise KeyError(f"unknown workflow run: {run_id}")
            if str(run.inputs.get("workspace_id") or "").strip() != workspace_id:
                raise PermissionError("workflow run belongs to another workspace")
            definition = self.workflow_store.load_definition(run.workflow_name)
            now = float(self.clock())
            with locked_state_path(self.path):
                state = self._load()
                previous_id = str(state["latest_by_run"].get(run_id) or "")
                previous = state["checkpoints"].get(previous_id)
                sequence = int((previous or {}).get("sequence") or 0) + 1
                if isinstance(previous, dict) and previous.get("status") == "active":
                    previous["status"] = "superseded"
                    state["checkpoints"][previous_id] = previous
                run_snapshot = _redact_secrets(run.snapshot())
                checkpoint = {
                    "checkpoint_id": f"checkpoint-{uuid4().hex}",
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "workflow_name": run.workflow_name,
                    "workflow_version": run.workflow_version,
                    "run_updated_at": run.updated_at,
                    "definition_present": definition is not None,
                    "definition_digest": self._definition_digest(definition.snapshot() if definition else {}),
                    "sequence": sequence,
                    "source_host_id": host_id,
                    "source_epoch": int(epoch),
                    "status": "active",
                    "reason": _bounded_text(reason, limit=160),
                    "created_at": _utc_now(now),
                    "created_timestamp": now,
                    "created_by": _bounded_text(requested_by, limit=120),
                    "run_snapshot": run_snapshot,
                    "queue": [node_id for node_id, node in run.nodes.items() if node.status == NODE_PENDING],
                    "pending_skills": self._pending_nodes(run, "skill"),
                    "pending_workers": self._pending_nodes(run, "worker"),
                    "inflight_nodes": [node_id for node_id, node in run.nodes.items() if node.status == NODE_RUNNING],
                    "context_ref": f"workflow:{run_id}",
                    "resume_policy": "reconcile_inflight_then_continue",
                }
                checkpoint["checksum"] = self._checksum(checkpoint)
                checkpoint_id = str(checkpoint["checkpoint_id"])
                state["checkpoints"][checkpoint_id] = checkpoint
                state["latest_by_run"][run_id] = checkpoint_id
                _write_json_atomic(self.path, state)
                return self._public_checkpoint(checkpoint)

    def resume_checkpoint(
        self,
        checkpoint_id: str,
        *,
        target_host_id: str,
        fencing_token: str,
        epoch: int,
        requested_by: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("workflow resume requires explicit confirmation")
        checkpoint_id = _required_id(checkpoint_id, "checkpoint_id")
        target_host_id = _required_id(target_host_id, "target_host_id")
        with locked_state_path(self.path):
            state = self._load()
            checkpoint = state["checkpoints"].get(checkpoint_id)
            if not isinstance(checkpoint, dict):
                raise KeyError(f"unknown runtime checkpoint: {checkpoint_id}")
            workspace_id = str(checkpoint.get("workspace_id") or "")
        with self.registry.authorized_lease(
            workspace_id=workspace_id,
            host_id=target_host_id,
            fencing_token=fencing_token,
            epoch=epoch,
        ):
            with locked_state_path(self.path):
                state = self._load()
                checkpoint = state["checkpoints"].get(checkpoint_id)
                if not isinstance(checkpoint, dict) or self._checksum(checkpoint) != str(checkpoint.get("checksum") or ""):
                    raise ValueError("runtime checkpoint integrity validation failed")
                resume_key = f"{checkpoint_id}:{target_host_id}:{int(epoch)}"
                prior = state["resumes"].get(resume_key)
                if isinstance(prior, dict):
                    return dict(prior)
                current = self.workflow_store.load_run(str(checkpoint["run_id"]))
                snapshot = checkpoint.get("run_snapshot")
                if not isinstance(snapshot, dict):
                    raise ValueError("runtime checkpoint is missing its workflow snapshot")
                if bool(checkpoint.get("definition_present")):
                    definition = self.workflow_store.load_definition(str(checkpoint.get("workflow_name") or ""))
                    if definition is None or self._definition_digest(definition.snapshot()) != str(checkpoint.get("definition_digest") or ""):
                        raise PermissionError("workflow definition changed since the runtime checkpoint")
                if current is not None and str(current.updated_at or "") > str(snapshot.get("updated_at") or ""):
                    raise PermissionError("runtime checkpoint is stale compared with the stored workflow run")
                restored = self._restore_run(snapshot, checkpoint, target_host_id, int(epoch))
                self.workflow_store.save_run(restored)
                checkpoint["status"] = "resumed"
                checkpoint["resumed_at"] = _utc_now(float(self.clock()))
                checkpoint["resumed_on_host_id"] = target_host_id
                checkpoint["resumed_epoch"] = int(epoch)
                state["checkpoints"][checkpoint_id] = checkpoint
                result = {
                    "ok": True,
                    "resume_id": f"resume-{uuid4().hex}",
                    "checkpoint_id": checkpoint_id,
                    "run_id": restored.run_id,
                    "workspace_id": workspace_id,
                    "target_host_id": target_host_id,
                    "target_epoch": int(epoch),
                    "status": restored.status,
                    "reconciliation_required": any(node.status == NODE_WAITING_REVIEW and node.error == "runtime_resume_reconcile_inflight" for node in restored.nodes.values()),
                    "resumed_at": checkpoint["resumed_at"],
                    "requested_by": _bounded_text(requested_by, limit=120),
                    "restart": False,
                }
                state["resumes"][resume_key] = result
                _write_json_atomic(self.path, state)
                return dict(result)

    def snapshot(self, *, workspace_id: str = "", run_id: str = "") -> dict[str, Any]:
        with locked_state_path(self.path):
            state = self._load()
        checkpoints = [
            self._public_checkpoint(item)
            for item in state["checkpoints"].values()
            if isinstance(item, dict)
            and (not workspace_id or str(item.get("workspace_id") or "") == workspace_id)
            and (not run_id or str(item.get("run_id") or "") == run_id)
        ]
        checkpoints.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("sequence") or 0)), reverse=True)
        return {
            "schema_version": RUNTIME_CHECKPOINT_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "count": len(checkpoints),
            "checkpoints": checkpoints[:200],
        }

    @staticmethod
    def _definition_digest(snapshot: dict[str, Any]) -> str:
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _checksum(checkpoint: dict[str, Any]) -> str:
        clean = {key: value for key, value in checkpoint.items() if key == "run_snapshot" or key in {"checkpoint_id", "workspace_id", "run_id", "workflow_name", "workflow_version", "run_updated_at", "definition_present", "definition_digest", "sequence", "source_host_id", "source_epoch", "created_at"}}
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _pending_nodes(run: WorkflowRun, category: str) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for node_id, node in run.nodes.items():
            outputs = node.outputs if isinstance(node.outputs, dict) else {}
            if node.status not in {NODE_RUNNING, NODE_WAITING}:
                continue
            if category == "skill" and not outputs.get("skill_name"):
                continue
            if category == "worker" and not (outputs.get("worker_binding") or outputs.get("worker_schedule") or outputs.get("device_id")):
                continue
            pending.append({"node_id": node_id, "status": node.status, "outputs": _redact_secrets(outputs)})
        return pending

    @staticmethod
    def _restore_run(snapshot: dict[str, Any], checkpoint: dict[str, Any], target_host_id: str, epoch: int) -> WorkflowRun:
        original = workflow_run_from_dict(snapshot)
        nodes: dict[str, WorkflowNodeRun] = {}
        reconciliation_required = False
        for node_id, node in original.nodes.items():
            if node.status == NODE_RUNNING:
                reconciliation_required = True
                nodes[node_id] = replace(
                    node,
                    status=NODE_WAITING_REVIEW,
                    error="runtime_resume_reconcile_inflight",
                    outputs={
                        **dict(node.outputs or {}),
                        "runtime_resume": {
                            "checkpoint_id": checkpoint["checkpoint_id"],
                            "source_host_id": checkpoint["source_host_id"],
                            "target_host_id": target_host_id,
                            "target_epoch": epoch,
                            "strategy": "reconcile_inflight",
                        },
                    },
                )
            else:
                nodes[node_id] = node
        if reconciliation_required or any(node.status == NODE_WAITING_REVIEW for node in nodes.values()):
            status = RUN_WAITING_REVIEW
        elif any(node.status == NODE_WAITING for node in nodes.values()):
            status = RUN_WAITING
        elif any(node.status in {NODE_PENDING, NODE_RUNNING} for node in nodes.values()):
            status = RUN_RUNNING
        elif any(node.status in {NODE_FAILED, NODE_BLOCKED} for node in nodes.values()):
            status = original.status
        else:
            status = original.status
        event = {
            "type": "runtime_checkpoint_resumed",
            "at": _utc_now(),
            "payload": {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "source_host_id": checkpoint["source_host_id"],
                "target_host_id": target_host_id,
                "target_epoch": epoch,
                "restart": False,
                "reconciliation_required": reconciliation_required,
            },
        }
        return replace(original, status=status, nodes=nodes, events=[*original.events, event], updated_at=_utc_now())

    @staticmethod
    def _public_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in checkpoint.items() if key not in {"run_snapshot", "created_timestamp", "created_by"}}


class RuntimeHostWorkflowStore(JsonWorkflowStore):
    """Fence Workflow mutations behind the active Runtime Host lease."""

    def __init__(
        self,
        base_store: JsonWorkflowStore,
        *,
        registry: RuntimeHostRegistry,
        checkpoints: RuntimeCheckpointStore,
        workspace_id: str,
        host_id: str,
        lease_supplier: Callable[[], dict[str, Any]],
    ) -> None:
        super().__init__(base_store.state_dir, project_root=base_store.project_root)
        self.registry = registry
        self.checkpoints = checkpoints
        self.workspace_id = _required_id(workspace_id, "workspace_id")
        self.host_id = _required_id(host_id, "host_id")
        self.lease_supplier = lease_supplier

    def _lease(self) -> dict[str, Any]:
        lease = dict(self.lease_supplier() or {})
        if str(lease.get("host_id") or "") != self.host_id or not lease.get("fencing_token"):
            raise PermissionError("runtime host does not own the workflow execution lease")
        return lease

    def _assert_run_workspace(self, run: WorkflowRun) -> None:
        run_workspace = str(run.inputs.get("workspace_id") or "").strip()
        if not run_workspace:
            raise PermissionError("runtime-hosted workflow run requires workspace_id")
        if run_workspace != self.workspace_id:
            raise PermissionError("runtime host cannot mutate another workspace workflow run")

    @contextmanager
    def _authorized(self) -> Iterator[dict[str, Any]]:
        lease = self._lease()
        with self.registry.authorized_lease(
            workspace_id=self.workspace_id,
            host_id=self.host_id,
            fencing_token=str(lease.get("fencing_token") or ""),
            epoch=int(lease.get("epoch") or 0),
        ):
            yield lease

    def save_definition(self, definition, *, actor: str = "", reason: str = "", record_history: bool = True) -> None:
        with self._authorized():
            super().save_definition(definition, actor=actor, reason=reason, record_history=record_history)

    def save_run(self, run: WorkflowRun) -> None:
        self._assert_run_workspace(run)
        with self._authorized() as lease:
            super().save_run(run)
        self.checkpoints.create_checkpoint(
            run.run_id,
            workspace_id=self.workspace_id,
            host_id=self.host_id,
            fencing_token=str(lease.get("fencing_token") or ""),
            epoch=int(lease.get("epoch") or 0),
            reason="runtime_host_run_commit",
            requested_by=self.host_id,
        )

    def list_runs(self, *, workflow_name: str = "") -> list[WorkflowRun]:
        return [
            run
            for run in super().list_runs(workflow_name=workflow_name)
            if str(run.inputs.get("workspace_id") or "").strip() == self.workspace_id
        ]

    def load_run(self, run_id: str) -> WorkflowRun | None:
        run = super().load_run(run_id)
        if run is None:
            return None
        return run if str(run.inputs.get("workspace_id") or "").strip() == self.workspace_id else None


class RuntimeHostHeartbeatService:
    """Keep one executable host registered and recover durable runs after election."""

    def __init__(
        self,
        registry: RuntimeHostRegistry,
        checkpoints: RuntimeCheckpointStore,
        *,
        host_id: str = "",
        workspace_id: str,
        host_type: str = "desktop",
        capabilities: Any = (),
        priority: int = 50,
        heartbeat_interval_seconds: float = 10.0,
        heartbeat_ttl_seconds: float = 45.0,
    ):
        hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-") or "local"
        self.host_id = _required_id(host_id or f"{host_type}:{hostname}", "host_id")
        self.workspace_id = _required_id(workspace_id, "workspace_id")
        self.host_type = str(host_type or "desktop").strip().lower()
        self.capabilities = _capabilities(capabilities) or ["workflow.execute", "checkpoint.resume"]
        self.priority = int(priority)
        self.heartbeat_interval_seconds = max(2.0, min(60.0, float(heartbeat_interval_seconds)))
        self.heartbeat_ttl_seconds = max(self.heartbeat_interval_seconds * 2, float(heartbeat_ttl_seconds))
        self.registry = registry
        self.checkpoints = checkpoints
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_recovered_epoch = 0
        self._lease_lock = threading.RLock()
        self._private_lease: dict[str, Any] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"runtime-host-{self.host_id}", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def tick(self) -> dict[str, Any]:
        current = next(
            (item for item in self.registry.snapshot(workspace_id=self.workspace_id)["hosts"] if item.get("host_id") == self.host_id),
            None,
        )
        if current is None:
            self.registry.register_host(
                host_id=self.host_id,
                workspace_id=self.workspace_id,
                host_type=self.host_type,
                label=self.host_id,
                capabilities=self.capabilities,
                can_execute_workflows=True,
                priority=self.priority,
                heartbeat_ttl_seconds=self.heartbeat_ttl_seconds,
                requested_by=self.host_id,
            )
        heartbeat = self.registry.heartbeat(
            self.host_id,
            capabilities=self.capabilities,
            requested_by=self.host_id,
        )
        lease = heartbeat.get("lease") if isinstance(heartbeat.get("lease"), dict) else {}
        if str(lease.get("host_id") or "") == self.host_id and lease.get("fencing_token"):
            with self._lease_lock:
                self._private_lease = dict(lease)
            epoch = int(lease.get("epoch") or 0)
            if epoch > self._last_recovered_epoch:
                self._recover(lease)
                self._last_recovered_epoch = epoch
            self._checkpoint_active_runs(lease)
        else:
            with self._lease_lock:
                self._private_lease = {}
        self._claim_prepared_migrations(lease)
        return heartbeat

    def private_lease(self) -> dict[str, Any]:
        with self._lease_lock:
            return dict(self._private_lease)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self.heartbeat_interval_seconds)

    def _claim_prepared_migrations(self, current_lease: dict[str, Any]) -> None:
        snapshot = self.registry.snapshot(workspace_id=self.workspace_id)
        for migration in snapshot.get("migrations") or []:
            if not isinstance(migration, dict):
                continue
            if migration.get("status") not in {"prepared", "claimed"} or migration.get("target_host_id") != self.host_id:
                continue
            try:
                if migration.get("status") == "prepared":
                    claim = self.registry.claim_migration(
                        str(migration.get("migration_id") or ""),
                        target_host_id=self.host_id,
                        requested_by=self.host_id,
                        confirmed=True,
                    )
                    lease = claim.get("lease") if isinstance(claim.get("lease"), dict) else {}
                else:
                    lease = current_lease
                if str(lease.get("host_id") or "") != self.host_id or not lease.get("fencing_token"):
                    continue
                self.checkpoints.resume_checkpoint(
                    str(migration.get("checkpoint_id") or ""),
                    target_host_id=self.host_id,
                    fencing_token=str(lease.get("fencing_token") or ""),
                    epoch=int(lease.get("epoch") or 0),
                    requested_by=self.host_id,
                    confirmed=True,
                )
                self._last_recovered_epoch = max(self._last_recovered_epoch, int(lease.get("epoch") or 0))
            except (KeyError, PermissionError, RuntimeError, ValueError):
                continue

    def _recover(self, lease: dict[str, Any]) -> None:
        snapshot = self.checkpoints.snapshot(workspace_id=self.workspace_id)
        latest_by_run: dict[str, dict[str, Any]] = {}
        for checkpoint in snapshot.get("checkpoints") or []:
            if not isinstance(checkpoint, dict) or checkpoint.get("status") != "active":
                continue
            run_id = str(checkpoint.get("run_id") or "")
            if run_id and run_id not in latest_by_run:
                latest_by_run[run_id] = checkpoint
        for checkpoint in latest_by_run.values():
            if str(checkpoint.get("source_host_id") or "") == self.host_id:
                continue
            try:
                self.checkpoints.resume_checkpoint(
                    str(checkpoint.get("checkpoint_id") or ""),
                    target_host_id=self.host_id,
                    fencing_token=str(lease.get("fencing_token") or ""),
                    epoch=int(lease.get("epoch") or 0),
                    requested_by=self.host_id,
                    confirmed=True,
                )
            except (KeyError, PermissionError, RuntimeError, ValueError):
                continue

    def _checkpoint_active_runs(self, lease: dict[str, Any]) -> None:
        active_statuses = {"pending", "running", "waiting", "waiting_review", "blocked"}
        latest = {
            str(item.get("run_id") or ""): item
            for item in self.checkpoints.snapshot(workspace_id=self.workspace_id).get("checkpoints") or []
            if isinstance(item, dict) and item.get("status") == "active"
        }
        for run in self.checkpoints.workflow_store.list_runs():
            if run.status not in active_statuses:
                continue
            run_workspace = str(run.inputs.get("workspace_id") or "").strip()
            if run_workspace != self.workspace_id:
                continue
            checkpoint = latest.get(run.run_id)
            if checkpoint and str(checkpoint.get("run_updated_at") or "") == str(run.updated_at or ""):
                continue
            try:
                self.checkpoints.create_checkpoint(
                    run.run_id,
                    workspace_id=self.workspace_id,
                    host_id=self.host_id,
                    fencing_token=str(lease.get("fencing_token") or ""),
                    epoch=int(lease.get("epoch") or 0),
                    reason="runtime_host_heartbeat",
                    requested_by=self.host_id,
                )
            except (KeyError, PermissionError, RuntimeError, ValueError):
                continue


class RuntimeWorkflowHostService:
    """Run Workflow Graph nodes only while this process owns the Host lease."""

    def __init__(
        self,
        heartbeat: RuntimeHostHeartbeatService,
        *,
        execution_interval_seconds: float = 2.0,
        max_runs: int = 20,
        max_steps_per_run: int = 10,
        status_path: str | Path | None = None,
    ) -> None:
        self.heartbeat = heartbeat
        self.execution_interval_seconds = max(1.0, min(60.0, float(execution_interval_seconds)))
        self.max_runs = max(1, min(100, int(max_runs)))
        self.max_steps_per_run = max(1, min(100, int(max_steps_per_run)))
        default_status_path = heartbeat.registry.path.parent / "host-status" / f"{heartbeat.host_id.replace(':', '_')}.json"
        self.status_path = Path(status_path or default_status_path).resolve()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tool_registry = None
        self._last_result: dict[str, Any] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.heartbeat.start()
        self._thread = threading.Thread(target=self._run, name=f"runtime-workflow-{self.heartbeat.host_id}", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))
        self.heartbeat.stop(timeout=timeout)

    def execute_once(self) -> dict[str, Any]:
        lease = self.heartbeat.private_lease()
        if not lease:
            self.heartbeat.tick()
            lease = self.heartbeat.private_lease()
        if str(lease.get("host_id") or "") != self.heartbeat.host_id or not lease.get("fencing_token"):
            self._last_result = {"ok": True, "status": "standby", "host_id": self.heartbeat.host_id, "advanced_steps": 0}
            self._write_status()
            return dict(self._last_result)

        if self._tool_registry is None:
            from backend.tools.registry import build_default_tool_registry

            guarded_store = RuntimeHostWorkflowStore(
                self.heartbeat.checkpoints.workflow_store,
                registry=self.heartbeat.registry,
                checkpoints=self.heartbeat.checkpoints,
                workspace_id=self.heartbeat.workspace_id,
                host_id=self.heartbeat.host_id,
                lease_supplier=self.heartbeat.private_lease,
            )
            self._tool_registry = build_default_tool_registry(
                allow_dynamic_mcp_discovery=False,
                workflow_store_factory=lambda _arguments: guarded_store,
            )

        from backend.tools.base import ToolCall

        result = self._tool_registry.invoke(
            ToolCall(
                "workflow.graph.auto_advance_runs",
                {
                    "actor": self.heartbeat.host_id,
                    "max_runs": self.max_runs,
                    "max_steps_per_run": self.max_steps_per_run,
                },
            )
        )
        summary = dict((result.data or {}).get("auto_advance") or {}) if isinstance(result.data, dict) else {}
        self._last_result = {
            "ok": bool(result.success),
            "status": "active" if result.success else "error",
            "host_id": self.heartbeat.host_id,
            "workspace_id": self.heartbeat.workspace_id,
            "epoch": int(lease.get("epoch") or 0),
            "run_count": int(summary.get("run_count") or 0),
            "advanced_steps": int(summary.get("advanced_steps") or 0),
            "error_code": str(result.error_code or ""),
            "message": str(result.message or ""),
        }
        self._write_status()
        return dict(self._last_result)

    def snapshot(self) -> dict[str, Any]:
        lease = self.heartbeat.private_lease()
        return {
            "host_id": self.heartbeat.host_id,
            "workspace_id": self.heartbeat.workspace_id,
            "status": "active" if lease else "standby",
            "epoch": int(lease.get("epoch") or 0),
            "last_execution": dict(self._last_result),
        }

    def _write_status(self) -> None:
        _write_json_atomic(
            self.status_path,
            {
                "schema_version": "spiritkin.runtime_host_execution.v1",
                "updated_at": _utc_now(),
                **self.snapshot(),
            },
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.execute_once()
            except (KeyError, PermissionError, RuntimeError, ValueError):
                self._last_result = {
                    "ok": False,
                    "status": "fenced",
                    "host_id": self.heartbeat.host_id,
                    "advanced_steps": 0,
                }
                self._write_status()
            except Exception as exc:
                self._last_result = {
                    "ok": False,
                    "status": "error",
                    "host_id": self.heartbeat.host_id,
                    "advanced_steps": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                self._write_status()
            self._stop.wait(self.execution_interval_seconds)
