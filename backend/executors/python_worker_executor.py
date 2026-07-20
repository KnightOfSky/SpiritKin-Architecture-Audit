from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.executors.command_preflight import check_executable

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class PythonWorkerExecutor(BaseExecutor):
    """Governed local Python script executor.

    The first real Python worker intentionally keeps the execution surface narrow:
    scripts must live under the configured workspace root, commands never use a
    shell, and inline code is disabled unless explicitly enabled for development.
    """

    name = "python_worker"
    supported_targets = ("python", "local_runtime")
    supported_operations = ("python.run", "python.execute", "python.install_package")

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str] | None = None,
        python_executable: str | None = None,
        default_timeout_seconds: float | None = None,
        max_timeout_seconds: float | None = None,
        allow_inline: bool | None = None,
    ):
        self.workspace_root = Path(
            workspace_root or os.getenv("SPIRITKIN_WORKSPACE_ROOT") or _DEFAULT_WORKSPACE_ROOT
        ).resolve()
        self.python_executable = python_executable or sys.executable
        self.default_timeout_seconds = _coerce_float(
            default_timeout_seconds,
            env_name="SPIRITKIN_PYTHON_WORKER_TIMEOUT_SECONDS",
            fallback=30.0,
        )
        self.max_timeout_seconds = _coerce_float(
            max_timeout_seconds,
            env_name="SPIRITKIN_PYTHON_WORKER_MAX_TIMEOUT_SECONDS",
            fallback=120.0,
        )
        self.allow_inline = (
            _env_flag("SPIRITKIN_PYTHON_WORKER_ALLOW_INLINE")
            if allow_inline is None
            else bool(allow_inline)
        )

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in self.supported_targets and request.operation in self.supported_operations

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"Unsupported Python worker request: {request.target}.{request.operation}",
                error_code="python_worker_unsupported_request",
                metadata={"target": request.target, "operation": request.operation},
            )

        params = dict(request.params or {})
        timeout_seconds = self._timeout_seconds(params.get("timeout_seconds", params.get("timeout")))
        try:
            command, command_ref = self._build_command(request.operation, params)
            cwd = self._resolve_directory(params.get("cwd") or self.workspace_root)
        except ValueError as exc:
            return ExecutionResult(
                success=False,
                message=str(exc),
                error_code=_error_code_from_value_error(str(exc)),
                metadata=self._base_metadata(),
            )

        env = os.environ.copy()
        env["SPIRITKIN_PYTHON_WORKER"] = "1"
        env.setdefault("PYTHONIOENCODING", "utf-8")

        preflight = check_executable(
            command[0],
            install_suggestion="Configure python_executable or install Python and add it to PATH.",
        )
        if not preflight.available:
            return ExecutionResult(
                success=False,
                message=f"Python executable is not available: {command[0]}",
                error_code="python_worker_start_failed",
                metadata={**self._base_metadata(timeout_seconds=timeout_seconds), "failure_context": preflight.failure_context()},
            )

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                message=f"Python worker timed out after {timeout_seconds:g}s",
                data={
                    "stdout": _decode_timeout_output(exc.stdout),
                    "stderr": _decode_timeout_output(exc.stderr),
                    "timeout_seconds": timeout_seconds,
                    "command_ref": command_ref,
                    "cwd": str(cwd),
                },
                error_code="python_worker_timeout",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        except OSError as exc:
            return ExecutionResult(
                success=False,
                message=f"Python worker failed to start: {exc}",
                error_code="python_worker_start_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        data = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command_ref": command_ref,
            "cwd": str(cwd),
        }
        if completed.returncode != 0:
            return ExecutionResult(
                success=False,
                message=f"Python worker exited with code {completed.returncode}",
                data=data,
                error_code="python_worker_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        return ExecutionResult(
            success=True,
            message="Python worker completed",
            data=data,
            metadata=self._base_metadata(timeout_seconds=timeout_seconds),
        )

    def _build_command(self, operation: str, params: dict[str, Any]) -> tuple[list[str], str]:
        if operation == "python.install_package":
            package = str(params.get("package") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:==[A-Za-z0-9][A-Za-z0-9._+-]*)?", package):
                raise ValueError("package must be a PyPI name with an optional exact version")
            return [self.python_executable, "-m", "pip", "install", package], f"<package:{package}>"
        inline_code = params.get("code")
        if inline_code is not None:
            if not self.allow_inline:
                raise ValueError("inline Python code is disabled for this worker")
            code = str(inline_code)
            if not code.strip():
                raise ValueError("inline Python code is empty")
            return [self.python_executable, "-c", code], "<inline>"

        raw_script = params.get("script_path") or params.get("path") or params.get("script")
        if not raw_script:
            raise ValueError("script_path is required")
        script_path = self._resolve_path(raw_script)
        if not script_path.exists():
            raise ValueError(f"script_path does not exist: {script_path}")
        if not script_path.is_file():
            raise ValueError(f"script_path is not a file: {script_path}")
        if script_path.suffix.lower() != ".py":
            raise ValueError("script_path must point to a .py file")
        args = _coerce_args(params.get("args"))
        return [self.python_executable, str(script_path), *args], str(script_path)

    def _resolve_path(self, value: Any) -> Path:
        raw = Path(str(value))
        candidate = raw if raw.is_absolute() else self.workspace_root / raw
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, self.workspace_root):
            raise ValueError(f"path is outside workspace: {resolved}")
        return resolved

    def _resolve_directory(self, value: Any) -> Path:
        resolved = self._resolve_path(value)
        if not resolved.exists():
            raise ValueError(f"cwd does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"cwd is not a directory: {resolved}")
        return resolved

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
            "python_executable": self.python_executable,
            "worker_maturity": "real",
        }
        if timeout_seconds is not None:
            metadata["timeout_seconds"] = timeout_seconds
        return metadata


def _coerce_args(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("args must be a list")
    return [str(item) for item in value]


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


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in _TRUTHY


def _error_code_from_value_error(message: str) -> str:
    normalized = message.lower()
    if "outside workspace" in normalized:
        return "python_worker_path_outside_workspace"
    if "inline" in normalized:
        return "python_worker_inline_disabled"
    if "cwd" in normalized:
        return "python_worker_invalid_cwd"
    if "args" in normalized:
        return "python_worker_invalid_args"
    if "script_path" in normalized or ".py file" in normalized:
        return "python_worker_invalid_script"
    return "python_worker_invalid_request"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
