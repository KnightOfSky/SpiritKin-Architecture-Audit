from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.executors.command_preflight import check_executable

_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class GitWorkerExecutor(BaseExecutor):
    """Governed local Git executor for workspace-contained repositories."""

    name = "git_worker"
    supported_targets = ("git", "repository")
    supported_operations = ("git.status", "git.diff", "git.commit")

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str] | None = None,
        git_executable: str | None = None,
        default_timeout_seconds: float = 30.0,
        max_timeout_seconds: float = 120.0,
    ):
        self.workspace_root = Path(workspace_root or os.getenv("SPIRITKIN_WORKSPACE_ROOT") or _DEFAULT_WORKSPACE_ROOT).resolve()
        self.git_executable = git_executable or os.getenv("SPIRITKIN_GIT_WORKER_EXECUTABLE") or "git"
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_timeout_seconds = float(max_timeout_seconds)

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in self.supported_targets and request.operation in self.supported_operations

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"Unsupported Git worker request: {request.target}.{request.operation}",
                error_code="git_worker_unsupported_request",
                metadata={"target": request.target, "operation": request.operation},
            )

        params = dict(request.params or {})
        try:
            repo_path = self._resolve_directory(params.get("repo_path") or params.get("cwd") or self.workspace_root)
            timeout_seconds = self._timeout_seconds(params.get("timeout_seconds", params.get("timeout")))
            command = self._build_command(request.operation, params)
        except ValueError as exc:
            return ExecutionResult(
                success=False,
                message=str(exc),
                error_code=_error_code_from_value_error(str(exc)),
                metadata=self._base_metadata(),
            )

        return self._run_git(repo_path, command, timeout_seconds)

    def _build_command(self, operation: str, params: dict[str, Any]) -> list[str]:
        normalized = operation.strip().lower()
        if normalized == "git.status":
            command = ["status", "--porcelain=v1", "-b"]
            if bool(params.get("short")):
                command = ["status", "--short", "--branch"]
            return command
        if normalized == "git.diff":
            command = ["diff"]
            if bool(params.get("staged") or params.get("cached")):
                command.append("--staged")
            pathspecs = _coerce_string_list(params.get("paths") or params.get("pathspecs"))
            if pathspecs:
                command.extend(["--", *pathspecs])
            return command
        if normalized == "git.commit":
            message = str(params.get("message") or "").strip()
            if not message:
                raise ValueError("message is required for git.commit")
            command = ["commit", "-m", message]
            if bool(params.get("allow_empty")):
                command.append("--allow-empty")
            return command
        raise ValueError(f"unsupported git operation: {operation}")

    def _run_git(self, repo_path: Path, git_args: list[str], timeout_seconds: float) -> ExecutionResult:
        command = [self.git_executable, "-C", str(repo_path), *git_args]
        preflight = check_executable(
            self.git_executable,
            install_suggestion="Install Git or configure SPIRITKIN_GIT_WORKER_EXECUTABLE.",
        )
        if not preflight.available:
            return ExecutionResult(
                success=False,
                message="Git executable is not available",
                error_code="git_worker_not_available",
                metadata={**self._base_metadata(timeout_seconds=timeout_seconds), "failure_context": preflight.failure_context()},
            )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                message="Git executable is not available",
                error_code="git_worker_not_available",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                message=f"Git worker timed out after {timeout_seconds:g}s",
                data={"stdout": _decode_timeout_output(exc.stdout), "stderr": _decode_timeout_output(exc.stderr)},
                error_code="git_worker_timeout",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        data = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "repo_path": str(repo_path),
            "git_args": git_args,
        }
        if completed.returncode != 0:
            return ExecutionResult(
                success=False,
                message=f"Git worker exited with code {completed.returncode}",
                data=data,
                error_code="git_worker_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        return ExecutionResult(
            success=True,
            message="Git worker completed",
            data=data,
            metadata=self._base_metadata(timeout_seconds=timeout_seconds),
        )

    def _resolve_directory(self, value: Any) -> Path:
        raw = Path(str(value))
        candidate = raw if raw.is_absolute() else self.workspace_root / raw
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, self.workspace_root):
            raise ValueError(f"path is outside workspace: {resolved}")
        if not resolved.exists():
            raise ValueError(f"repo_path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"repo_path is not a directory: {resolved}")
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
            "git_executable": self.git_executable,
            "worker_maturity": "real",
        }
        if timeout_seconds is not None:
            metadata["timeout_seconds"] = timeout_seconds
        return metadata


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("paths must be a list")
    return [str(item) for item in value if str(item).strip()]


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _error_code_from_value_error(message: str) -> str:
    normalized = message.lower()
    if "outside workspace" in normalized:
        return "git_worker_path_outside_workspace"
    if "message is required" in normalized:
        return "git_worker_missing_message"
    if "repo_path" in normalized:
        return "git_worker_invalid_repo"
    if "paths" in normalized:
        return "git_worker_invalid_paths"
    return "git_worker_invalid_request"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
