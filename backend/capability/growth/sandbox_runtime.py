from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from backend.app.settings import resolve_growth_sandbox_execution
from backend.state_store import resolve_state_path

DEFAULT_RUNTIME_STATE = "state/growth/sandbox_runtime.json"
SCHEMA_VERSION = "spiritkin.growth_sandbox_runtime.v1"
IMMUTABLE_IMAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[a-z0-9._-]+)?@sha256:[0-9a-f]{64}$")


def sandbox_execution_policy(
    environ: dict[str, str] | None = None,
    *,
    config_path: str | os.PathLike[str] = "config/config.yaml",
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    configured = resolve_growth_sandbox_execution(environ=env, config_path=config_path)
    enabled = bool(configured.get("enabled"))
    raw_images = configured.get("images") or []
    images = tuple(
        dict.fromkeys(
            str(item).strip().lower()
            for item in raw_images
            if IMMUTABLE_IMAGE_PATTERN.fullmatch(str(item).strip().lower())
        )
    )
    return {
        "configured": enabled and bool(images),
        "operator_enabled": enabled,
        "approved_images": images,
        "approved_image_count": len(images),
        "probe_command": tuple(
            item
            for item in (configured.get("probe_command") or [])
            if isinstance(item, str) and item and "\x00" not in item and "\n" not in item and "\r" not in item and len(item) <= 240
        )[:24],
        "immutable_image_required": True,
        "automatic_pull": False,
        "network_policy": "none",
        "host_mounts_allowed": False,
        "container_user": "65534:65534",
        "read_only_root": True,
        "resource_limits_required": True,
    }


def _now() -> float:
    return time.time()


def _base_report(
    *,
    status: str,
    reason: str,
    probed_at: float | None = None,
    execution_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution = sandbox_execution_policy()
    probe = dict(execution_probe or {"status": "not_run", "reason": "not_run"})
    return {
        "schema_version": SCHEMA_VERSION,
        "probed_at": probed_at or _now(),
        "provider": "docker",
        "status": status,
        "cli_available": False,
        "daemon_available": False,
        "network_policy": execution["network_policy"],
        "automatic_pull": False,
        "execution_configured": execution["configured"],
        "approved_image_count": execution["approved_image_count"],
        "execution_probe": {
            "status": str(probe.get("status") or "not_run"),
            "reason": str(probe.get("reason") or "")[:160],
            "duration_ms": float(probe.get("duration_ms") or 0),
        },
        "candidate_execution_enabled": status == "ready"
        and execution["configured"]
        and str(probe.get("status") or "") == "passed",
        "isolation_profile": {
            "immutable_image_required": True,
            "host_mounts_allowed": False,
            "read_only_root": True,
            "non_root_user": True,
            "resource_limits_required": True,
        },
        "external_code_execution_requires_explicit_gate": True,
        "probe_scope": "daemon_metadata_and_fixed_trusted_probe",
        "reason": reason,
    }


def _normalize_report(value: dict[str, Any]) -> dict[str, Any]:
    status = str(value.get("status") or "unavailable")
    if status not in {"not_probed", "unavailable", "ready"}:
        status = "unavailable"
    try:
        probed_at = float(value.get("probed_at") or _now())
    except (TypeError, ValueError):
        probed_at = _now()
    report = _base_report(
        status=status,
        reason=str(value.get("reason") or "runtime_state_invalid")[:96],
        probed_at=probed_at,
    )
    report["cli_available"] = bool(value.get("cli_available"))
    report["daemon_available"] = bool(value.get("daemon_available"))
    stored_probe = value.get("execution_probe") if isinstance(value.get("execution_probe"), dict) else {}
    try:
        probe_duration = float(stored_probe.get("duration_ms") or 0)
    except (TypeError, ValueError):
        probe_duration = 0.0
    report["execution_probe"] = {
        "status": str(stored_probe.get("status") or "not_run"),
        "reason": str(stored_probe.get("reason") or "")[:160],
        "duration_ms": max(0.0, probe_duration),
    }
    report["candidate_execution_enabled"] = (
        status == "ready"
        and sandbox_execution_policy()["configured"]
        and report["execution_probe"]["status"] == "passed"
    )
    if status == "ready":
        report["server_version"] = str(value.get("server_version") or "unknown")[:64]
        report["operating_system"] = str(value.get("operating_system") or "unknown")[:96]
        try:
            report["image_count"] = max(0, int(value.get("image_count") or 0))
        except (TypeError, ValueError):
            report["image_count"] = 0
    return report


class GrowthSandboxRuntimeProbe:
    """Probe Docker metadata plus one fixed trusted command in the approved image.

    The trusted probe is not candidate code and is gated by explicit operator
    confirmation at the action boundary. It never pulls images, mounts a
    workspace, or changes Growth stage/activation state.
    """

    def __init__(self, state_path: str | os.PathLike[str] | None = None) -> None:
        self.state_path = resolve_state_path(
            "SPIRITKIN_GROWTH_SANDBOX_RUNTIME_PATH", DEFAULT_RUNTIME_STATE, state_path
        )

    def snapshot(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _base_report(status="not_probed", reason="not_probed")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _base_report(status="unavailable", reason="runtime_state_unreadable")
        if not isinstance(value, dict):
            return _base_report(status="unavailable", reason="runtime_state_invalid")
        return _normalize_report(value)

    def _write_snapshot(self, report: dict[str, Any]) -> dict[str, Any]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="sandbox-runtime-", suffix=".json", dir=self.state_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.state_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return _normalize_report(report)

    def probe(self) -> dict[str, Any]:
        docker = shutil.which("docker")
        if not docker:
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_cli_missing"))

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [docker, "info", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_daemon_timeout"))
        except OSError:
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_daemon_unavailable"))

        if result.returncode != 0:
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_daemon_unavailable"))
        try:
            metadata = json.loads(result.stdout or "")
        except json.JSONDecodeError:
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_daemon_invalid_response"))
        if not isinstance(metadata, dict):
            return self._write_snapshot(_base_report(status="unavailable", reason="docker_daemon_invalid_response"))

        report = _base_report(status="ready", reason="ready")
        report.update(
            {
                "cli_available": True,
                "daemon_available": True,
                "server_version": str(metadata.get("ServerVersion") or "unknown")[:64],
                "operating_system": str(metadata.get("OperatingSystem") or "unknown")[:96],
                "image_count": int(metadata.get("Images") or 0) if str(metadata.get("Images") or "0").isdigit() else 0,
            }
        )
        execution = sandbox_execution_policy()
        if execution["configured"]:
            probe_result = self._probe_execution(docker, execution)
            report["execution_probe"] = probe_result
            report["candidate_execution_enabled"] = probe_result.get("status") == "passed"
        else:
            report["execution_probe"] = {"status": "not_configured", "reason": "execution_policy_not_configured", "duration_ms": 0}
            report["candidate_execution_enabled"] = False
        return self._write_snapshot(report)

    @staticmethod
    def _probe_execution(docker: str, execution: dict[str, Any]) -> dict[str, Any]:
        images = tuple(execution.get("approved_images") or ())
        command = [str(item) for item in execution.get("probe_command") or []]
        if len(images) != 1 or not command:
            return {"status": "unavailable", "reason": "execution_probe_config_incomplete", "duration_ms": 0}
        image = images[0]
        started_at = time.time()
        name = f"spiritkin-runtime-probe-{os.getpid()}-{time.time_ns() % 1000000000}"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        common = [
            docker,
            "create",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "0.50",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--log-opt",
            "max-size=64k",
            "--log-opt",
            "max-file=1",
            image,
            *command,
        ]
        created = False
        try:
            result = subprocess.run(common, capture_output=True, text=True, timeout=8, check=False, creationflags=creationflags)
            if result.returncode != 0:
                return {"status": "unavailable", "reason": "execution_probe_create_failed", "duration_ms": round((time.time() - started_at) * 1000, 2)}
            created = True
            started = subprocess.run([docker, "start", name], capture_output=True, text=True, timeout=8, check=False, creationflags=creationflags)
            if started.returncode != 0:
                return {"status": "unavailable", "reason": "execution_probe_start_failed", "duration_ms": round((time.time() - started_at) * 1000, 2)}
            waited = subprocess.run([docker, "wait", name], capture_output=True, text=True, timeout=10, check=False, creationflags=creationflags)
            if waited.returncode != 0 or (waited.stdout or "").strip() != "0":
                return {"status": "unavailable", "reason": "execution_probe_failed", "duration_ms": round((time.time() - started_at) * 1000, 2)}
            return {"status": "passed", "reason": "trusted_image_probe_passed", "duration_ms": round((time.time() - started_at) * 1000, 2)}
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "unavailable", "reason": "execution_probe_timeout", "duration_ms": round((time.time() - started_at) * 1000, 2)}
        finally:
            if created:
                try:
                    subprocess.run([docker, "rm", "--force", name], capture_output=True, text=True, timeout=8, check=False, creationflags=creationflags)
                except (OSError, subprocess.TimeoutExpired):
                    pass
