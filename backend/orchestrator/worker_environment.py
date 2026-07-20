from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

WORKER_ENVIRONMENT_SCHEMA_VERSION = "spiritkin.worker_environment.v1"


# Status vocabulary shared by every optional worker validation report.
#   available     - a real execution backend is configured and the worker is (or can be) registered.
#   preview_only  - the worker runs but only against an in-memory / simulated backend, not real hardware.
#   not_configured- an optional backend is not configured, so the worker is intentionally absent.
#   degraded      - configured but missing a signal needed for full operation.
STATUS_AVAILABLE = "available"
STATUS_PREVIEW_ONLY = "preview_only"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_DEGRADED = "degraded"


@dataclass(frozen=True)
class WorkerEnvironmentReport:
    """Operator-visible reason a worker is or is not usable.

    This exists so an unconfigured optional worker surfaces an explicit
    "why it is unavailable" record instead of silently disappearing from the
    executor list.
    """

    worker_id: str
    label: str
    status: str
    registered: bool
    reason: str
    remediation: str = ""
    env_signals: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "label": self.label,
            "status": self.status,
            "registered": self.registered,
            "reason": self.reason,
            "remediation": self.remediation,
            "env_signals": dict(self.env_signals or {}),
            "metadata": dict(self.metadata or {}),
        }


def _env_present(name: str, environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(str(env.get(name) or "").strip())


def validate_browser_worker_environment(environ: dict[str, str] | None = None) -> WorkerEnvironmentReport:
    env = os.environ if environ is None else environ
    command = str(env.get("SPIRITKIN_BROWSER_WORKER_COMMAND") or "").strip()
    configured = bool(command)
    return WorkerEnvironmentReport(
        worker_id="executor:browser_worker",
        label="Browser Worker",
        status=STATUS_AVAILABLE if configured else STATUS_NOT_CONFIGURED,
        registered=configured,
        reason=(
            "Process-backed browser worker command is configured."
            if configured
            else "No browser worker process command is set, so the executor is not registered."
        ),
        remediation=(
            ""
            if configured
            else "Set SPIRITKIN_BROWSER_WORKER_COMMAND to the browser/Playwright worker process command."
        ),
        env_signals={"SPIRITKIN_BROWSER_WORKER_COMMAND": configured},
        metadata={"worker_type": "browser_worker", "transport": "json_stdin_stdout"},
    )


def validate_openclaw_worker_environment(environ: dict[str, str] | None = None) -> WorkerEnvironmentReport:
    env = os.environ if environ is None else environ
    http_base_url = str(env.get("SPIRITKIN_OPENCLAW_HTTP_BASE_URL") or "").strip()
    has_http = bool(http_base_url)
    return WorkerEnvironmentReport(
        worker_id="executor:openclaw",
        label="OpenClaw Desktop Device Worker",
        status=STATUS_AVAILABLE if has_http else STATUS_PREVIEW_ONLY,
        registered=True,
        reason=(
            "OpenClaw HTTP transport is configured; arm/gripper operations reach a real device endpoint."
            if has_http
            else "OpenClaw falls back to the in-memory client; operations are simulated, not sent to real hardware."
        ),
        remediation=(
            ""
            if has_http
            else "Set SPIRITKIN_OPENCLAW_HTTP_BASE_URL (and optionally SPIRITKIN_OPENCLAW_HTTP_TOKEN) to reach a real OpenClaw device."
        ),
        env_signals={
            "SPIRITKIN_OPENCLAW_HTTP_BASE_URL": has_http,
            "SPIRITKIN_OPENCLAW_HTTP_TOKEN": _env_present("SPIRITKIN_OPENCLAW_HTTP_TOKEN", env),
        },
        metadata={"worker_type": "device_worker", "transport": "http" if has_http else "in_memory"},
    )


def validate_remote_worker_environment(
    *, node_count: int = 0, environ: dict[str, str] | None = None
) -> WorkerEnvironmentReport:
    has_nodes = int(node_count or 0) > 0
    return WorkerEnvironmentReport(
        worker_id="executor:remote",
        label="Generic Remote Worker",
        status=STATUS_AVAILABLE if has_nodes else STATUS_NOT_CONFIGURED,
        registered=has_nodes,
        reason=(
            f"{int(node_count)} remote node(s) registered; remote execution is routable."
            if has_nodes
            else "No remote nodes are registered, so the remote executor is not registered."
        ),
        remediation=(
            ""
            if has_nodes
            else "Register a remote node (heartbeat or explicit RemoteNode) before remote execution can be scheduled."
        ),
        env_signals={"remote_node_count": int(node_count or 0)},
        metadata={"worker_type": "generic_remote_worker"},
    )


def validate_android_worker_environment(
    *, worker_status: str = "", device_count: int = 0, environ: dict[str, str] | None = None
) -> WorkerEnvironmentReport:
    env = os.environ if environ is None else environ
    status_text = str(worker_status or "").strip() or "needs_pairing"
    token_set = _env_present("SPIRITKIN_ANDROID_TOKEN", env)
    if status_text in {"needs_pairing", "endpoint_offline", "offline"}:
        status = STATUS_NOT_CONFIGURED
        reason = f"Android companion worker is not ready (status={status_text}); no paired device is reachable."
        remediation = "Pair an Android companion device and set SPIRITKIN_ANDROID_TOKEN before opening the bridge to a phone network."
    elif status_text in {"needs_attention", "degraded"}:
        status = STATUS_DEGRADED
        reason = f"Android companion worker is degraded (status={status_text}); check pairing/permissions."
        remediation = "Resolve the companion attention items; verify token and device permissions."
    else:
        status = STATUS_AVAILABLE
        reason = f"Android companion worker is ready (status={status_text}, devices={int(device_count)})."
        remediation = ""
    return WorkerEnvironmentReport(
        worker_id="executor:android_device",
        label="Android Device Worker",
        status=status,
        registered=status == STATUS_AVAILABLE,
        reason=reason,
        remediation=remediation,
        env_signals={
            "companion_status": status_text,
            "device_count": int(device_count or 0),
            "SPIRITKIN_ANDROID_TOKEN": token_set,
        },
        metadata={"worker_type": "device_worker", "worker_subtype": "android_device_worker"},
    )


def build_worker_environment_reports(
    *,
    remote_node_count: int = 0,
    android_worker_status: str = "",
    android_device_count: int = 0,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate optional-worker validation reports for operator surfaces.

    Callers pass runtime-derived signals (remote node count, android companion
    status) so the report reflects live state, while env-driven signals are read
    from the environment directly.
    """

    reports = [
        validate_browser_worker_environment(environ),
        validate_openclaw_worker_environment(environ),
        validate_remote_worker_environment(node_count=remote_node_count, environ=environ),
        validate_android_worker_environment(
            worker_status=android_worker_status,
            device_count=android_device_count,
            environ=environ,
        ),
    ]
    status_counts: dict[str, int] = {}
    for report in reports:
        status_counts[report.status] = status_counts.get(report.status, 0) + 1
    return {
        "schema_version": WORKER_ENVIRONMENT_SCHEMA_VERSION,
        "total": len(reports),
        "status_counts": status_counts,
        "reports": [report.snapshot() for report in reports],
    }


def build_worker_environment_snapshot(node_registry: object | None = None) -> dict[str, Any]:
    """Probe live runtime signals, then aggregate the optional-worker reports."""

    remote_node_count = 0
    if node_registry is not None:
        try:
            remote_node_count = len(node_registry.list_nodes())
        except Exception:
            remote_node_count = 0
    android_status = ""
    android_device_count = 0
    try:
        from backend.mobile.android_companion_store import AndroidCompanionStore

        companion = AndroidCompanionStore().snapshot()
        worker = dict(companion.get("worker") or {})
        android_status = str(worker.get("status") or "")
        android_device_count = int(worker.get("device_count") or companion.get("device_count") or 0)
    except Exception:
        android_status = ""
        android_device_count = 0
    return build_worker_environment_reports(
        remote_node_count=remote_node_count,
        android_worker_status=android_status,
        android_device_count=android_device_count,
    )
