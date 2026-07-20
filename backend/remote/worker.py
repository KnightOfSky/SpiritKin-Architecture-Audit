from __future__ import annotations

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from backend.devices.registry import get_device_backend
from backend.executors import ExecutorRemoteNodeClient, LocalPCExecutor
from backend.executors.remote_protocol import (
    RemoteExecutionPayload,
    remote_execution_payload_to_dict,
    remote_execution_response_to_dict,
    remote_node_heartbeat_to_dict,
)
from backend.remote.package_security import verify_remote_package_signature
from backend.security.http import add_cors_headers, is_local_request, localhost_auth_bypass_enabled, token_matches
from backend.security.safety_control import build_safety_snapshot, evaluate_execution_safety

DEFAULT_REMOTE_WORKER_HOST = os.getenv("SPIRITKIN_REMOTE_WORKER_HOST", "127.0.0.1")
DEFAULT_REMOTE_WORKER_PORT = int(os.getenv("SPIRITKIN_REMOTE_WORKER_PORT", "8790"))
REMOTE_AUTH_HEADER = "X-SpiritKin-Remote-Token"
DEFAULT_REMOTE_PACKAGE_DIR = os.getenv("SPIRITKIN_REMOTE_PACKAGE_DIR", "state/remote_worker/packages")


class RemoteWorker:
    def __init__(self, *, node_id: str, auth_token: str = "", aliases: set[str] | None = None, metadata: dict[str, Any] | None = None, executors=None):
        self.node_id = node_id.strip() or "worker-node"
        self.auth_token = auth_token.strip()
        self.aliases = set(aliases or set())
        self.metadata = dict(metadata or {})
        self.client = ExecutorRemoteNodeClient(list(executors or []))
        self.package_dir = self._resolve_package_dir()

    def build_heartbeat_payload(self) -> dict[str, Any]:
        heartbeat = self.client.heartbeat(self.node_id, aliases=self.aliases, metadata=self.metadata)
        payload = remote_node_heartbeat_to_dict(heartbeat)
        payload["safety"] = build_safety_snapshot()
        return payload

    def execute_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = RemoteExecutionPayload(
            node_id=str(payload.get("node_id") or self.node_id),
            target=str(payload.get("target") or "remote"),
            operation=str(payload.get("operation") or ""),
            params=dict(payload.get("params") or {}),
        )
        safety = evaluate_execution_safety(
            target=f"remote_worker:{request_payload.target}",
            operation=request_payload.operation,
            actor=str(payload.get("actor") or self.node_id),
        )
        if not safety.allowed:
            return self._attach_remote_trajectory(
                "execute",
                payload,
                {
                    "ok": False,
                    "node_id": self.node_id,
                    "success": False,
                    "message": safety.message,
                    "error_code": safety.error_code,
                    "safety": safety.snapshot(),
                },
            )
        response = self.client.execute(request_payload)
        result = remote_execution_response_to_dict(response)
        result.setdefault("ok", bool(result.get("success")))
        result.setdefault("node_id", self.node_id)
        return self._attach_remote_trajectory("execute", payload, result)

    def import_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        package = self._extract_package(payload)
        signature = verify_remote_package_signature(
            package,
            require_signature=bool(payload.get("require_signature", True)),
            signing_key=str(payload.get("signing_key") or self.auth_token or ""),
        )
        package_id = self._safe_package_id(str(package.get("export_id") or payload.get("package_id") or f"remote-package-{int(time.time())}"))
        package_path = self.package_dir / f"{package_id}.json"
        previous_active = self._active_package_snapshot()
        record = {
            **package,
            "status": "staged",
            "imported_at": time.time(),
            "remote_node_id": self.node_id,
            "source_package_path": str(payload.get("source_package_path") or ""),
            "signature_verification": signature,
            "activation": {
                "status": "staged",
                "previous_active_package_id": previous_active.get("package_id", ""),
                "previous_active_package_path": previous_active.get("package_path", ""),
            },
        }
        package_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "package_id": package_id,
            "package_path": str(package_path),
            "package": record,
        }

    def execute_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        safety = evaluate_execution_safety(
            target="remote_worker",
            operation="execute_package",
            actor=str(payload.get("actor") or self.node_id),
        )
        if not safety.allowed:
            return self._attach_remote_trajectory(
                "execute_package",
                payload,
                {
                    "ok": False,
                    "status": "blocked_by_safety",
                    "message": safety.message,
                    "error_code": safety.error_code,
                    "safety": safety.snapshot(),
                },
            )
        import_result = self.import_package(payload)
        package = dict(import_result["package"])
        signature = dict(package.get("signature_verification") or {})
        if not bool(signature.get("verified")):
            return self._attach_remote_trajectory(
                "execute_package",
                payload,
                {
                    "ok": False,
                    "status": "imported_verification_pending",
                    "message": "remote package signature verification required",
                    "error_code": "remote_package_signature_required",
                    "signature_verification": signature,
                },
            )
        commands = [str(item) for item in package.get("verification_commands") or [] if str(item).strip()]
        allow_commands = os.getenv("SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS", "").strip().lower() in {"1", "true", "yes", "on"}
        command_results: list[dict[str, Any]] = []
        if bool(payload.get("run_verification", True)) and commands:
            if allow_commands:
                for command in commands[:8]:
                    command_results.append(self._run_verification_command(command))
            else:
                command_results.append(
                    {
                        "command": "",
                        "success": False,
                        "skipped": True,
                        "message": "remote package command execution is disabled; set SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS=1 on the worker to enable it",
                    }
                )
        success = all(bool(item.get("success")) for item in command_results if not item.get("skipped")) if command_results else True
        if any(item.get("skipped") for item in command_results):
            success = False
        result = {
            "ok": success,
            "package_id": import_result["package_id"],
            "package_path": import_result["package_path"],
            "status": "active" if success else "staged_verification_pending",
            "verification": command_results,
            "signature_verification": signature,
        }
        if success:
            result["activation"] = self._activate_package(Path(str(import_result["package_path"])), package_id=str(import_result["package_id"]))
        else:
            result["activation"] = {"status": "staged", "active": False}
        self._update_package_status(Path(str(import_result["package_path"])), result)
        return self._attach_remote_trajectory("execute_package", payload, result)

    def rollback_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        safety = evaluate_execution_safety(
            target="remote_worker",
            operation="rollback_package",
            actor=str(payload.get("actor") or self.node_id),
        )
        if not safety.allowed:
            return self._attach_remote_trajectory(
                "rollback_package",
                payload,
                {
                    "ok": False,
                    "status": "blocked_by_safety",
                    "message": safety.message,
                    "error_code": safety.error_code,
                    "safety": safety.snapshot(),
                },
            )
        active = self._active_package_snapshot()
        if not active:
            return self._attach_remote_trajectory(
                "rollback_package",
                payload,
                {
                    "ok": False,
                    "status": "rollback_unavailable",
                    "message": "no active remote package pointer found",
                    "error_code": "remote_package_active_pointer_missing",
                },
            )
        requested_package_id = str(payload.get("package_id") or "").strip()
        active_package_id = str(active.get("package_id") or "").strip()
        if requested_package_id and requested_package_id != active_package_id:
            return self._attach_remote_trajectory(
                "rollback_package",
                payload,
                {
                    "ok": False,
                    "status": "rollback_rejected",
                    "message": "requested package_id does not match the active remote package",
                    "error_code": "remote_package_active_mismatch",
                    "active_package_id": active_package_id,
                    "requested_package_id": requested_package_id,
                },
            )
        previous_package_id = str(active.get("previous_active_package_id") or "").strip()
        previous_package_path = self._resolve_stored_package_path(str(active.get("previous_active_package_path") or ""))
        if previous_package_path is None and previous_package_id:
            previous_package_path = self.package_dir / f"{self._safe_package_id(previous_package_id)}.json"
        if previous_package_path is None:
            return self._attach_remote_trajectory(
                "rollback_package",
                payload,
                {
                    "ok": False,
                    "status": "rollback_unavailable",
                    "message": "active package has no previous package pointer",
                    "error_code": "remote_package_previous_pointer_missing",
                    "active_package_id": active_package_id,
                },
            )
        if not previous_package_path.exists():
            return self._attach_remote_trajectory(
                "rollback_package",
                payload,
                {
                    "ok": False,
                    "status": "rollback_unavailable",
                    "message": "previous remote package file is missing",
                    "error_code": "remote_package_previous_file_missing",
                    "active_package_id": active_package_id,
                    "previous_package_id": previous_package_id,
                    "previous_package_path": str(previous_package_path),
                },
            )
        previous_package = json.loads(previous_package_path.read_text(encoding="utf-8"))
        if not isinstance(previous_package, dict):
            raise ValueError("previous remote package must be a JSON object")
        signature = verify_remote_package_signature(
            previous_package,
            require_signature=bool(payload.get("require_signature", True)),
            signing_key=str(payload.get("signing_key") or self.auth_token or ""),
        )
        resolved_previous_id = self._safe_package_id(str(previous_package.get("export_id") or previous_package_id or previous_package_path.stem))
        activation = self._activate_package(previous_package_path, package_id=resolved_previous_id)
        result = {
            "ok": True,
            "status": "rolled_back",
            "from_package_id": active_package_id,
            "to_package_id": resolved_previous_id,
            "previous_package_path": str(previous_package_path),
            "active_package": activation,
            "signature_verification": signature,
        }
        self._update_package_status(
            previous_package_path,
            {
                "status": "active",
                "verification": previous_package.get("verification", []),
                "signature_verification": signature,
                "activation": activation,
            },
        )
        active_package_path = self._resolve_stored_package_path(str(active.get("package_path") or ""))
        if active_package_path is not None and active_package_path.exists() and active_package_path.resolve() != previous_package_path.resolve():
            self._update_package_status(
                active_package_path,
                {
                    "status": "rolled_back",
                    "verification": [],
                    "activation": {
                        "status": "rolled_back",
                        "active": False,
                        "rolled_back_at": time.time(),
                        "rollback_to_package_id": resolved_previous_id,
                        "rollback_to_package_path": str(previous_package_path),
                    },
                },
            )
        return self._attach_remote_trajectory("rollback_package", payload, result)

    def _resolve_package_dir(self) -> Path:
        raw = os.getenv("SPIRITKIN_REMOTE_PACKAGE_DIR", DEFAULT_REMOTE_PACKAGE_DIR)
        target = Path(raw)
        if not target.is_absolute():
            target = Path.cwd() / target
        target.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    @staticmethod
    def _extract_package(payload: dict[str, Any]) -> dict[str, Any]:
        package = payload.get("package")
        if not isinstance(package, dict):
            raise ValueError("missing package object")
        return dict(package)

    @staticmethod
    def _safe_package_id(raw: str) -> str:
        return "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"}) or f"remote-package-{int(time.time())}"

    def _run_verification_command(self, command: str) -> dict[str, Any]:
        # Trust boundary: `command` comes from a package whose signature was verified
        # upstream, and execution is disabled unless the operator explicitly sets
        # SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS=1 on this worker. shell=True is
        # required for full command lines; do NOT call this with unverified input.
        started_at = time.time()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(Path.cwd()),
                capture_output=True,
                text=True,
                timeout=float(os.getenv("SPIRITKIN_REMOTE_PACKAGE_COMMAND_TIMEOUT", "120")),
            )
        except Exception as exc:
            return {
                "command": command,
                "success": False,
                "message": str(exc),
                "duration_seconds": round(time.time() - started_at, 3),
            }
        return {
            "command": command,
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "")[-4000:],
            "stderr": (completed.stderr or "")[-4000:],
            "duration_seconds": round(time.time() - started_at, 3),
        }

    @staticmethod
    def _active_pointer_path(package_dir: Path) -> Path:
        return package_dir / "active-package.json"

    def _active_package_snapshot(self) -> dict[str, Any]:
        pointer = self._active_pointer_path(self.package_dir)
        if not pointer.exists():
            return {}
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _activate_package(self, package_path: Path, *, package_id: str) -> dict[str, Any]:
        previous_active = self._active_package_snapshot()
        activation = {
            "status": "active",
            "active": True,
            "package_id": package_id,
            "package_path": str(package_path),
            "activated_at": time.time(),
            "previous_active_package_id": previous_active.get("package_id", ""),
            "previous_active_package_path": previous_active.get("package_path", ""),
        }
        self._active_pointer_path(self.package_dir).write_text(json.dumps(activation, ensure_ascii=False, indent=2), encoding="utf-8")
        return activation

    def _resolve_stored_package_path(self, raw: str) -> Path | None:
        value = raw.strip()
        if not value:
            return None
        target = Path(value)
        if not target.is_absolute():
            target = self.package_dir / target
        return target

    @staticmethod
    def _update_package_status(package_path: Path, result: dict[str, Any]) -> None:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
            if isinstance(package, dict):
                package["status"] = result.get("status")
                package["executed_at"] = time.time()
                package["verification"] = result.get("verification", [])
                if "signature_verification" in result:
                    package["signature_verification"] = result.get("signature_verification")
                if "activation" in result:
                    package["activation"] = result.get("activation")
                package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _attach_remote_trajectory(self, action: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(result or {})
        try:
            from backend.orchestrator.runtime_trajectory_log import (
                append_runtime_trajectory,
                trajectory_from_remote_worker_result,
                trajectory_logging_enabled,
            )
        except Exception as exc:
            enriched["trajectory_log_error"] = str(exc)
            return enriched
        if not trajectory_logging_enabled():
            return enriched
        try:
            trajectory = append_runtime_trajectory(
                trajectory_from_remote_worker_result(
                    node_id=self.node_id,
                    action=action,
                    payload=payload,
                    result=enriched,
                )
            )
            metadata = trajectory.get("metadata") if isinstance(trajectory.get("metadata"), dict) else {}
            enriched["trajectory_record"] = {
                "trajectory_id": trajectory.get("trajectory_id", ""),
                "source": metadata.get("source", "remote.worker_result"),
                "overall_success": bool(trajectory.get("overall_success", False)),
                "bottleneck_stage": trajectory.get("bottleneck_stage", ""),
            }
        except Exception as exc:
            enriched["trajectory_log_error"] = str(exc)
        return enriched


def build_default_remote_worker(*, node_id: str | None = None, auth_token: str | None = None) -> RemoteWorker:
    worker_node_id = node_id or os.getenv("SPIRITKIN_REMOTE_NODE_ID", "local-worker")
    token = auth_token if auth_token is not None else os.getenv("SPIRITKIN_REMOTE_TOKEN", "")
    aliases = {alias.strip() for alias in os.getenv("SPIRITKIN_REMOTE_ALIASES", "").split(",") if alias.strip()}
    metadata = {"transport": "http", "worker_version": "minimal-v1"}
    metadata["hostname"] = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or worker_node_id
    executors = [LocalPCExecutor(device_backend=get_device_backend("local_pc"), device_name="local_pc")]
    return RemoteWorker(node_id=worker_node_id, auth_token=token, aliases=aliases, metadata=metadata, executors=executors)


class RemoteWorkerHandler(BaseHTTPRequestHandler):
    worker: RemoteWorker | None = None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {REMOTE_AUTH_HEADER}", env_key="SPIRITKIN_REMOTE_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        worker = self.worker
        expected = worker.auth_token.strip() if worker is not None else ""
        if not expected:
            client_ip = str(self.client_address[0]) if getattr(self, "client_address", None) else ""
            return localhost_auth_bypass_enabled() and is_local_request(self.headers, client_ip=client_ip)
        return token_matches(self.headers, expected_token=expected, header_name=REMOTE_AUTH_HEADER)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"ok": True, "service": "spiritkin-remote-worker", "node_id": self.worker.node_id})
            return
        if self.path.rstrip("/") == "/heartbeat":
            self._send_json(200, {"ok": True, "heartbeat": self.worker.build_heartbeat_payload()})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path not in {"/execute", "/remote-package/import", "/remote-package/execute", "/remote-package/rollback"}:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "json body must be object"})
            return
        try:
            if path == "/remote-package/import":
                response = self.worker.import_package(payload)
                self._send_json(200, response)
                return
            if path == "/remote-package/execute":
                response = self.worker.execute_package(payload)
                self._send_json(200, response)
                return
            if path == "/remote-package/rollback":
                response = self.worker.rollback_package(payload)
                self._send_json(200, response)
                return
            response = self.worker.execute_payload(payload)
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": f"remote worker action failed: {type(exc).__name__}", "detail": str(exc)})
            return
        self._send_json(200, {"ok": True, "request": remote_execution_payload_to_dict(RemoteExecutionPayload(node_id=str(payload.get('node_id') or self.worker.node_id), target=str(payload.get('target') or 'remote'), operation=str(payload.get('operation') or ''), params=dict(payload.get('params') or {}))), "response": response})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[remote-worker] {self.address_string()} - {format % args}")


def serve_remote_worker(host: str = DEFAULT_REMOTE_WORKER_HOST, port: int = DEFAULT_REMOTE_WORKER_PORT, *, worker: RemoteWorker | None = None) -> None:
    RemoteWorkerHandler.worker = worker or build_default_remote_worker()
    server = ThreadingHTTPServer((host, port), RemoteWorkerHandler)
    print(f"[worker] Remote worker started at http://{host}:{port} node_id={RemoteWorkerHandler.worker.node_id}")
    if RemoteWorkerHandler.worker.auth_token:
        print(f"[worker] Token required via {REMOTE_AUTH_HEADER}")
    server.serve_forever()


def main() -> None:
    serve_remote_worker()


if __name__ == "__main__":
    main()
