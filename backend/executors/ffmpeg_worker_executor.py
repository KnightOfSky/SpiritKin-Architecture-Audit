from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.executors.command_preflight import check_executable

_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class FFmpegWorkerExecutor(BaseExecutor):
    """Governed local FFmpeg executor for workspace media files."""

    name = "ffmpeg_worker"
    supported_targets = ("ffmpeg", "media")
    supported_operations = ("ffmpeg.probe", "ffmpeg.transcode")

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str] | None = None,
        ffmpeg_executable: str | None = None,
        ffprobe_executable: str | None = None,
        default_timeout_seconds: float = 60.0,
        max_timeout_seconds: float = 600.0,
    ):
        self.workspace_root = Path(workspace_root or os.getenv("SPIRITKIN_WORKSPACE_ROOT") or _DEFAULT_WORKSPACE_ROOT).resolve()
        self.ffmpeg_executable = ffmpeg_executable or os.getenv("SPIRITKIN_FFMPEG_WORKER_EXECUTABLE") or "ffmpeg"
        self.ffprobe_executable = ffprobe_executable or os.getenv("SPIRITKIN_FFPROBE_WORKER_EXECUTABLE") or "ffprobe"
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_timeout_seconds = float(max_timeout_seconds)

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in self.supported_targets and request.operation in self.supported_operations

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"Unsupported FFmpeg worker request: {request.target}.{request.operation}",
                error_code="ffmpeg_worker_unsupported_request",
                metadata={"target": request.target, "operation": request.operation},
            )

        params = dict(request.params or {})
        timeout_seconds = self._timeout_seconds(params.get("timeout_seconds", params.get("timeout")))
        try:
            if request.operation == "ffmpeg.probe":
                command, command_ref = self._probe_command(params)
            else:
                command, command_ref = self._transcode_command(params)
        except ValueError as exc:
            return ExecutionResult(
                success=False,
                message=str(exc),
                error_code=_error_code_from_value_error(str(exc)),
                metadata=self._base_metadata(),
            )

        return self._run(command, command_ref, timeout_seconds)

    def _probe_command(self, params: dict[str, Any]) -> tuple[list[str], str]:
        input_path = self._resolve_existing_file(params.get("input_path") or params.get("path"))
        return (
            [
                self.ffprobe_executable,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(input_path),
            ],
            str(input_path),
        )

    def _transcode_command(self, params: dict[str, Any]) -> tuple[list[str], str]:
        input_path = self._resolve_existing_file(params.get("input_path") or params.get("source_path") or params.get("path"))
        output_path = self._resolve_output_file(params.get("output_path") or params.get("target_path"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        overwrite = bool(params.get("overwrite", False))
        args = _coerce_string_list(params.get("args") or params.get("ffmpeg_args"))
        command = [self.ffmpeg_executable]
        command.append("-y" if overwrite else "-n")
        command.extend(["-i", str(input_path)])
        command.extend(args)
        command.append(str(output_path))
        return command, f"{input_path} -> {output_path}"

    def _run(self, command: list[str], command_ref: str, timeout_seconds: float) -> ExecutionResult:
        preflight = check_executable(
            command[0],
            install_suggestion="Install FFmpeg/FFprobe or configure the SpiritKin FFmpeg worker executable paths.",
        )
        if not preflight.available:
            return ExecutionResult(
                success=False,
                message=f"FFmpeg executable is not available: {command[0]}",
                error_code="ffmpeg_worker_not_available",
                metadata={**self._base_metadata(timeout_seconds=timeout_seconds), "failure_context": preflight.failure_context()},
            )
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace_root),
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
                message="FFmpeg executable is not available",
                error_code="ffmpeg_worker_not_available",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                message=f"FFmpeg worker timed out after {timeout_seconds:g}s",
                data={"stdout": _decode_timeout_output(exc.stdout), "stderr": _decode_timeout_output(exc.stderr), "command_ref": command_ref},
                error_code="ffmpeg_worker_timeout",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )

        data: dict[str, Any] = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command_ref": command_ref,
        }
        if command and Path(command[0]).name.lower().startswith("ffprobe") and completed.stdout.strip():
            try:
                data["probe"] = json.loads(completed.stdout)
            except json.JSONDecodeError:
                pass
        if completed.returncode != 0:
            return ExecutionResult(
                success=False,
                message=f"FFmpeg worker exited with code {completed.returncode}",
                data=data,
                error_code="ffmpeg_worker_failed",
                metadata=self._base_metadata(timeout_seconds=timeout_seconds),
            )
        return ExecutionResult(
            success=True,
            message="FFmpeg worker completed",
            data=data,
            metadata=self._base_metadata(timeout_seconds=timeout_seconds),
        )

    def _resolve_existing_file(self, value: Any) -> Path:
        if not value:
            raise ValueError("input_path is required")
        resolved = self._resolve_path(value)
        if not resolved.exists():
            raise ValueError(f"input_path does not exist: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"input_path is not a file: {resolved}")
        return resolved

    def _resolve_output_file(self, value: Any) -> Path:
        if not value:
            raise ValueError("output_path is required")
        resolved = self._resolve_path(value)
        if resolved.exists() and resolved.is_dir():
            raise ValueError(f"output_path is a directory: {resolved}")
        return resolved

    def _resolve_path(self, value: Any) -> Path:
        raw = Path(str(value))
        candidate = raw if raw.is_absolute() else self.workspace_root / raw
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, self.workspace_root):
            raise ValueError(f"path is outside workspace: {resolved}")
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
            "ffmpeg_executable": self.ffmpeg_executable,
            "ffprobe_executable": self.ffprobe_executable,
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
        raise ValueError("args must be a list")
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
        return "ffmpeg_worker_path_outside_workspace"
    if "input_path" in normalized:
        return "ffmpeg_worker_invalid_input"
    if "output_path" in normalized:
        return "ffmpeg_worker_invalid_output"
    if "args" in normalized:
        return "ffmpeg_worker_invalid_args"
    return "ffmpeg_worker_invalid_request"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
