from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.capability.growth.sandbox_bundle import GrowthSandboxBundleStore
from backend.capability.growth.sandbox_runtime import GrowthSandboxRuntimeProbe, sandbox_execution_policy

SCHEMA_VERSION = "spiritkin.growth_sandbox_execution.v1"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_OUTPUT_EXCERPT = 4000


def _safe_id(value: str, fallback: str = "execution") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return normalized[:72] or fallback


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="strict")).hexdigest()


def _bounded_output(value: str) -> tuple[str, int, bool]:
    encoded = str(value or "").encode("utf-8", errors="replace")
    truncated = len(encoded) > MAX_OUTPUT_BYTES
    bounded = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    cleaned = "".join(character for character in bounded if character in "\n\r\t" or ord(character) >= 32)
    return cleaned[:MAX_OUTPUT_EXCERPT], len(encoded), truncated or len(cleaned) > MAX_OUTPUT_EXCERPT


def _isolation_checks(metadata: dict[str, Any], volume_name: str) -> dict[str, bool]:
    host = metadata.get("HostConfig") if isinstance(metadata.get("HostConfig"), dict) else {}
    config = metadata.get("Config") if isinstance(metadata.get("Config"), dict) else {}
    mounts = [item for item in metadata.get("Mounts") or [] if isinstance(item, dict)]
    workspace_mount = next((item for item in mounts if str(item.get("Destination") or "") == "/workspace"), {})
    security_options = {str(item).lower() for item in host.get("SecurityOpt") or []}
    cap_drop = {str(item).upper() for item in host.get("CapDrop") or []}
    tmpfs = host.get("Tmpfs") if isinstance(host.get("Tmpfs"), dict) else {}
    tmp_options = str(tmpfs.get("/tmp") or "").lower()
    log_config = host.get("LogConfig") if isinstance(host.get("LogConfig"), dict) else {}
    log_options = log_config.get("Config") if isinstance(log_config.get("Config"), dict) else {}
    try:
        memory = int(host.get("Memory") or 0)
        nano_cpus = int(host.get("NanoCpus") or 0)
        pids_limit = int(host.get("PidsLimit") or 0)
    except (TypeError, ValueError):
        memory = nano_cpus = pids_limit = 0
    return {
        "isolation_validation_passed": False,
        "network_disabled": str(host.get("NetworkMode") or "").lower() == "none",
        "host_mounts_used": any(str(item.get("Type") or "").lower() == "bind" for item in mounts),
        "managed_volume_used": str(workspace_mount.get("Type") or "").lower() == "volume"
        and str(workspace_mount.get("Name") or "") == volume_name,
        "managed_volume_read_only_during_execution": workspace_mount.get("RW") is False,
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "non_root_user": str(config.get("User") or "") == "65534:65534",
        "capabilities_dropped": "ALL" in cap_drop,
        "no_new_privileges": "no-new-privileges:true" in security_options,
        "resource_limits_applied": 0 < memory <= 256 * 1024 * 1024
        and 0 < nano_cpus <= 500_000_000
        and 0 < pids_limit <= 64,
        "bounded_tmpfs": "/tmp" in tmpfs
        and all(option in tmp_options for option in ("noexec", "nosuid", "nodev")),
        "bounded_logs": str(log_options.get("max-size") or "").lower() == "64k"
        and str(log_options.get("max-file") or "") == "1",
    }


class GrowthDockerSandboxExecutor:
    """Executes immutable candidate bundles in a constrained, local-only Docker container."""

    def __init__(
        self,
        artifact_root: str | os.PathLike[str],
        runtime_probe: GrowthSandboxRuntimeProbe,
        bundle_store: GrowthSandboxBundleStore,
    ) -> None:
        artifact_root_path = Path(artifact_root).resolve()
        self.root = (artifact_root_path.parent / "sandbox-executions").resolve()
        self.runtime_probe = runtime_probe
        self.bundle_store = bundle_store

    def execute(
        self,
        candidate: dict[str, Any],
        artifact: dict[str, Any],
        payload: dict[str, Any],
        *,
        executed_by: str,
    ) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        sandbox_plan = dict(artifact.get("sandbox_plan") or {})
        bundle_summary = dict(sandbox_plan.get("bundle") or {})
        bundle_id = str(payload.get("bundle_id") or bundle_summary.get("bundle_id") or "").strip()
        if not candidate_id or not artifact_id or not bundle_id or not executed_by:
            raise ValueError("candidate_id, artifact_id, bundle_id and executed_by are required")
        if str(artifact.get("candidate_id") or "") != candidate_id:
            raise PermissionError("Builder artifact belongs to another candidate")
        if str(artifact.get("workspace_id") or "") != str(candidate.get("workspace_id") or ""):
            raise PermissionError("Builder artifact belongs to another workspace")
        verification = dict(artifact.get("verification_plan") or {})
        if str(verification.get("execution_status") or "") != "passed":
            raise PermissionError("static Builder verification must pass before container execution")
        if payload.get("execution_ack") != "run_untrusted_code_in_isolated_container":
            raise PermissionError("explicit isolated execution acknowledgement is required")

        runtime = self.runtime_probe.snapshot()
        if runtime.get("status") != "ready" or runtime.get("candidate_execution_enabled") is not True:
            raise RuntimeError(f"sandbox candidate execution is unavailable: {runtime.get('reason') or 'not_configured'}")
        policy = sandbox_execution_policy()
        approved_images = tuple(policy.get("approved_images") or ())
        requested_image = str(payload.get("image") or "").strip().lower()
        image = requested_image or (approved_images[0] if len(approved_images) == 1 else "")
        if not image or image not in approved_images:
            raise PermissionError("sandbox image must be selected from the operator-approved immutable image list")

        manifest = self.bundle_store.load(candidate_id, bundle_id)
        if str(manifest.get("artifact_id") or "") != artifact_id:
            raise PermissionError("sandbox bundle belongs to another Builder artifact")
        files_root = self.bundle_store.verify_files(manifest)
        command = [str(item) for item in manifest.get("command") or []]
        expected_exit_codes = {int(value) for value in manifest.get("expected_exit_codes") or [0]}
        timeout_seconds = max(1, min(30, int(manifest.get("timeout_seconds") or 15)))

        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError("docker CLI is unavailable")
        image_inspect = self._run(
            [docker, "image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=5
        )
        if image_inspect.returncode != 0:
            raise RuntimeError("approved sandbox image is not present locally; automatic pull is disabled")
        try:
            repo_digests = json.loads(image_inspect.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Docker returned invalid image digest metadata") from exc
        if image not in {str(item).lower() for item in repo_digests if isinstance(item, str)}:
            raise PermissionError("local sandbox image digest does not match the approved immutable reference")

        created_at = time.time()
        execution_seed = {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "bundle_id": bundle_id,
            "image": image,
            "created_at": created_at,
        }
        execution_id = f"execute-{_digest(execution_seed)[:16]}"
        container_name = f"spiritkin-growth-{_safe_id(candidate_id, 'candidate')[:32]}-{execution_id[-8:]}"
        staging_name = f"{container_name}-stage"
        volume_name = f"{container_name}-bundle"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        common_isolation = [
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
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--stop-timeout",
            "1",
            "--log-opt",
            "max-size=64k",
            "--log-opt",
            "max-file=1",
        ]
        staging_create_command = [
            docker,
            "create",
            "--name",
            staging_name,
            *common_isolation,
            "--mount",
            f"type=volume,src={volume_name},dst=/workspace",
            image,
            *command,
        ]
        execution_create_command = [
            docker,
            "create",
            "--name",
            container_name,
            *common_isolation,
            "--mount",
            f"type=volume,src={volume_name},dst=/workspace,readonly",
            image,
            *command,
        ]
        volume_created = False
        staging_created = False
        execution_created = False
        execution_started = False
        exit_code = -1
        stdout = ""
        stderr = ""
        failure_reason = ""
        cleanup_ok = True
        isolation = {
            "isolation_validation_passed": False,
            "network_disabled": False,
            "host_mounts_used": False,
            "managed_volume_used": False,
            "managed_volume_read_only_during_execution": False,
            "read_only_root": False,
            "non_root_user": False,
            "capabilities_dropped": False,
            "no_new_privileges": False,
            "resource_limits_applied": False,
            "bounded_tmpfs": False,
            "bounded_logs": False,
        }
        started_at = time.time()
        try:
            volume_result = subprocess.run(
                [
                    docker,
                    "volume",
                    "create",
                    "--label",
                    f"spiritkin.growth.execution_id={execution_id}",
                    volume_name,
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=creationflags,
            )
            if volume_result.returncode != 0:
                failure_reason = "managed_volume_create_failed"
                stderr = volume_result.stderr
            else:
                volume_created = True
                staging_result = subprocess.run(
                    staging_create_command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    creationflags=creationflags,
                )
                if staging_result.returncode != 0:
                    failure_reason = "staging_container_create_failed"
                    stderr = staging_result.stderr
                else:
                    staging_created = True
                    copy_result = subprocess.run(
                        [docker, "cp", f"{files_root}{os.sep}.", f"{staging_name}:/workspace"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                        creationflags=creationflags,
                    )
                    if copy_result.returncode != 0:
                        failure_reason = "bundle_copy_failed"
                        stderr = copy_result.stderr
                    else:
                        stage_cleanup = subprocess.run(
                            [docker, "rm", "--force", staging_name],
                            capture_output=True,
                            text=True,
                            timeout=8,
                            check=False,
                            creationflags=creationflags,
                        )
                        staging_created = stage_cleanup.returncode != 0
                        if staging_created:
                            failure_reason = "staging_container_cleanup_failed"
                            stderr = stage_cleanup.stderr
                        else:
                            execution_result = subprocess.run(
                                execution_create_command,
                                capture_output=True,
                                text=True,
                                timeout=10,
                                check=False,
                                creationflags=creationflags,
                            )
                            if execution_result.returncode != 0:
                                failure_reason = "container_create_failed"
                                stderr = execution_result.stderr
                            else:
                                execution_created = True
                                inspect_result = self._run(
                                    [docker, "container", "inspect", container_name], timeout=5
                                )
                                try:
                                    inspect_payload = json.loads(inspect_result.stdout or "[]")
                                    inspect_metadata = inspect_payload[0] if isinstance(inspect_payload, list) else {}
                                except (IndexError, json.JSONDecodeError):
                                    inspect_metadata = {}
                                isolation = _isolation_checks(inspect_metadata, volume_name)
                                required_checks = [
                                    value
                                    for key, value in isolation.items()
                                    if key not in {"isolation_validation_passed", "host_mounts_used"}
                                ]
                                isolation["isolation_validation_passed"] = (
                                    inspect_result.returncode == 0
                                    and not isolation["host_mounts_used"]
                                    and all(required_checks)
                                )
                                if not isolation["isolation_validation_passed"]:
                                    failure_reason = "container_isolation_validation_failed"
                                    stderr = inspect_result.stderr or "Docker container isolation metadata did not match policy"
                                else:
                                    try:
                                        execution_started = True
                                        start_result = subprocess.run(
                                            [docker, "start", container_name],
                                            capture_output=True,
                                            text=True,
                                            timeout=5,
                                            check=False,
                                            creationflags=creationflags,
                                        )
                                        if start_result.returncode != 0:
                                            failure_reason = "container_start_failed"
                                            stderr = start_result.stderr
                                        else:
                                            wait_result = subprocess.run(
                                                [docker, "wait", container_name],
                                                capture_output=True,
                                                text=True,
                                                timeout=timeout_seconds,
                                                check=False,
                                                creationflags=creationflags,
                                            )
                                            try:
                                                exit_code = int((wait_result.stdout or "").strip())
                                            except ValueError:
                                                failure_reason = "container_exit_code_unavailable"
                                            logs_result = self._run(
                                                [docker, "logs", "--tail", "200", container_name], timeout=5
                                            )
                                            stdout = logs_result.stdout
                                            stderr = logs_result.stderr
                                    except subprocess.TimeoutExpired as exc:
                                        failure_reason = "execution_timeout"
                                        stdout = str(exc.stdout or "")
                                        stderr = str(exc.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            failure_reason = "docker_runtime_error"
            stderr = f"{type(exc).__name__}: {exc}"
        finally:
            for created_name, exists in (
                (staging_name, staging_created),
                (container_name, execution_created),
            ):
                if not exists:
                    continue
                try:
                    cleanup_result = subprocess.run(
                        [docker, "rm", "--force", created_name],
                        capture_output=True,
                        text=True,
                        timeout=8,
                        check=False,
                        creationflags=creationflags,
                    )
                    cleanup_ok = cleanup_ok and cleanup_result.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_ok = False
            if volume_created:
                try:
                    volume_cleanup = subprocess.run(
                        [docker, "volume", "rm", "--force", volume_name],
                        capture_output=True,
                        text=True,
                        timeout=8,
                        check=False,
                        creationflags=creationflags,
                    )
                    cleanup_ok = cleanup_ok and volume_cleanup.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_ok = False

        stdout_excerpt, stdout_bytes, stdout_truncated = _bounded_output(stdout)
        stderr_excerpt, stderr_bytes, stderr_truncated = _bounded_output(stderr)
        passed = not failure_reason and exit_code in expected_exit_codes and cleanup_ok
        if not failure_reason and exit_code not in expected_exit_codes:
            failure_reason = "unexpected_exit_code"
        if not cleanup_ok:
            failure_reason = failure_reason or "container_cleanup_failed"
        report = {
            "schema_version": SCHEMA_VERSION,
            "execution_id": execution_id,
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "bundle_id": bundle_id,
            "workspace_id": str(candidate.get("workspace_id") or ""),
            "kind": str(candidate.get("kind") or ""),
            "status": "passed" if passed else "failed",
            "failure_reason": failure_reason,
            "executed_by": executed_by[:200],
            "created_at": created_at,
            "duration_ms": round((time.time() - started_at) * 1000, 2),
            "image": image,
            "command": command,
            "expected_exit_codes": sorted(expected_exit_codes),
            "exit_code": exit_code,
            "output": {
                "stdout_excerpt": stdout_excerpt,
                "stderr_excerpt": stderr_excerpt,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
            "checks": {
                "image_digest_approved": True,
                "automatic_pull_disabled": True,
                **isolation,
                "container_cleanup_ok": cleanup_ok,
                "bundle_integrity_valid": True,
            },
            "policy": {
                "network_accessed": False,
                "host_code_executed": False,
                "container_code_executed": execution_started,
                "host_dependencies_installed": False,
                "persistent_dependencies_installed": False,
                "candidate_stage_advanced": False,
                "activation_enabled": False,
                "requires_human_review": True,
            },
            "integrity": {},
        }
        report["integrity"] = {
            "algorithm": "sha256",
            "digest": _digest({key: value for key, value in report.items() if key != "integrity"}),
        }
        candidate_root = (self.root / _safe_id(candidate_id, "candidate")).resolve()
        if not candidate_root.is_relative_to(self.root):
            raise PermissionError("unsafe sandbox execution report path")
        candidate_root.mkdir(parents=True, exist_ok=True)
        report_path = candidate_root / f"{execution_id}.json"
        fd, temporary = tempfile.mkstemp(prefix="execution-", suffix=".json", dir=candidate_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, report_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return {**report, "path": str(report_path)}

    @staticmethod
    def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def snapshot(self, candidate_ids: list[str] | None = None) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for candidate_id in candidate_ids or []:
            candidate_root = (self.root / _safe_id(candidate_id, "candidate")).resolve()
            if not candidate_root.is_relative_to(self.root) or not candidate_root.exists():
                continue
            for path in candidate_root.glob("execute-*.json"):
                try:
                    report = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(report, dict):
                    reports.append(report)
        reports.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(reports),
            "recent": [
                {
                    "execution_id": item.get("execution_id"),
                    "candidate_id": item.get("candidate_id"),
                    "bundle_id": item.get("bundle_id"),
                    "workspace_id": item.get("workspace_id"),
                    "status": item.get("status"),
                    "failure_reason": item.get("failure_reason"),
                    "exit_code": item.get("exit_code"),
                    "duration_ms": item.get("duration_ms"),
                    "created_at": item.get("created_at"),
                }
                for item in reports[:20]
            ],
            "policy": {
                "container_only": True,
                "automatic_pull": False,
                "network_enabled": False,
                "host_mounts_allowed": False,
                "automatic_stage_advance": False,
                "automatic_activation": False,
            },
        }
