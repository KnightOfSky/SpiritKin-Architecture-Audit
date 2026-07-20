from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.executors.command_preflight import check_executable

_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class BrowserWorkerExecutor(BaseExecutor):
    """Process-backed browser worker using a JSON-over-stdin protocol.

    This executor is intentionally not a browser automation implementation.
    It is the governed bridge to a production browser/Playwright worker
    process, keeping Browser worker scheduling separate from local_pc desktop
    shortcuts.
    """

    name = "browser_worker"
    supported_targets = ("browser", "playwright")
    supported_operations = ("browser.health_check", "browser_open_url", "browser_search")

    def __init__(
        self,
        *,
        browser_command: list[str] | tuple[str, ...] | str | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
        default_timeout_seconds: float | None = None,
        max_timeout_seconds: float | None = None,
    ):
        self.workspace_root = Path(workspace_root or os.getenv("SPIRITKIN_WORKSPACE_ROOT") or _DEFAULT_WORKSPACE_ROOT).resolve()
        self.browser_command = _coerce_command(browser_command if browser_command is not None else os.getenv("SPIRITKIN_BROWSER_WORKER_COMMAND"))
        self.default_timeout_seconds = _coerce_float(
            default_timeout_seconds,
            env_name="SPIRITKIN_BROWSER_WORKER_TIMEOUT_SECONDS",
            fallback=20.0,
        )
        self.max_timeout_seconds = _coerce_float(
            max_timeout_seconds,
            env_name="SPIRITKIN_BROWSER_WORKER_MAX_TIMEOUT_SECONDS",
            fallback=120.0,
        )

    @classmethod
    def from_environment(cls, **kwargs) -> BrowserWorkerExecutor | None:
        if not str(os.getenv("SPIRITKIN_BROWSER_WORKER_COMMAND") or "").strip():
            return None
        return cls(**kwargs)

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in self.supported_targets and request.operation in self.supported_operations

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"Unsupported Browser worker request: {request.target}.{request.operation}",
                error_code="browser_worker_unsupported_request",
                metadata={"target": request.target, "operation": request.operation},
            )
        if not self.browser_command:
            return ExecutionResult(
                success=False,
                message="Browser worker process command is not configured",
                error_code="browser_worker_process_not_configured",
                metadata=self._base_metadata(),
            )

        params = dict(request.params or {})
        timeout_seconds = self._timeout_seconds(params.get("timeout_seconds", params.get("timeout")))
        payload = {
            "schema_version": "spiritkin.browser_worker.request.v1",
            "target": request.target,
            "operation": request.operation,
            "params": params,
            "workspace_root": str(self.workspace_root),
        }
        preflight = check_executable(
            self.browser_command[0],
            install_suggestion="Install the configured browser worker runtime or update SPIRITKIN_BROWSER_WORKER_COMMAND.",
        )
        if not preflight.available:
            return ExecutionResult(
                success=False,
                message="Browser worker process executable is not available",
                error_code="browser_worker_not_available",
                metadata={**self._base_metadata(timeout_seconds=timeout_seconds), "failure_context": preflight.failure_context()},
            )
        try:
            completed = subprocess.run(
                self.browser_command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                cwd=str(self.workspace_root),
                shell=False,
                check=False,
                **_windows_hidden_startupinfo(),
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                message="Browser worker process executable is not available",
                error_code="browser_worker_not_available",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                message=f"Browser worker timed out after {timeout_seconds:g}s",
                data={
                    "stdout": _decode_timeout_output(exc.stdout),
                    "stderr": _decode_timeout_output(exc.stderr),
                    "timeout_seconds": timeout_seconds,
                },
                error_code="browser_worker_timeout",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        except OSError as exc:
            return ExecutionResult(
                success=False,
                message=f"Browser worker failed to start: {exc}",
                error_code="browser_worker_start_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        response = _parse_worker_response(completed.stdout)
        data = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "response": response,
        }
        if completed.returncode != 0:
            return ExecutionResult(
                success=False,
                message=f"Browser worker exited with code {completed.returncode}",
                data=data,
                error_code="browser_worker_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        if response is None:
            return ExecutionResult(
                success=False,
                message="Browser worker returned invalid JSON",
                data=data,
                error_code="browser_worker_invalid_response",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        success = bool(response.get("success", response.get("ok", False)))
        message = str(response.get("message") or ("Browser worker completed" if success else "Browser worker failed"))
        return ExecutionResult(
            success=success,
            message=message,
            data=response.get("data", data),
            error_code="" if success else str(response.get("error_code") or "browser_worker_failed"),
            metadata={
                **self._base_metadata(timeout_seconds=timeout_seconds),
                "worker_response_metadata": dict(response.get("metadata") or {}) if isinstance(response.get("metadata"), dict) else {},
            },
        )

    def _timeout_seconds(self, value: Any) -> float:
        timeout = self.default_timeout_seconds
        if value is not None:
            try:
                timeout = float(value)
            except (TypeError, ValueError):
                timeout = self.default_timeout_seconds
        if timeout <= 0:
            timeout = self.default_timeout_seconds
        return min(timeout, self.max_timeout_seconds)

    def _base_metadata(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        metadata = {
            "executor": self.name,
            "workspace_root": str(self.workspace_root),
            "worker_maturity": "process_backed" if self.browser_command else "not_configured",
            "process_protocol": "json_stdin_stdout",
        }
        if self.browser_command:
            metadata["browser_command"] = self.browser_command[0]
        if timeout_seconds is not None:
            metadata["timeout_seconds"] = timeout_seconds
        return metadata


def _coerce_command(value: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                return [str(item) for item in payload if str(item).strip()]
        if os.name == "nt":
            return _split_windows_command(raw)
        return shlex.split(raw, posix=True)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _split_windows_command(raw: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in raw:
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char.isspace() and not in_quotes:
            if current:
                parts.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _coerce_float(value: float | None, *, env_name: str, fallback: float) -> float:
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback
    env_value = os.getenv(env_name)
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            return fallback
    return fallback


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_worker_response(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    candidate = text.splitlines()[-1].strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _windows_hidden_startupinfo() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startupinfo}
