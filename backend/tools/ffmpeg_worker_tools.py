from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_ffmpeg_worker_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="ffmpeg.probe",
                description="Probe a workspace media file with ffprobe.",
                target="ffmpeg",
                operation="ffmpeg.probe",
                risk_level="low",
                read_only=True,
                schema={"input_path": "str", "timeout_seconds": "number"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="ffmpeg.transcode",
                description="Transcode a workspace media file with ffmpeg.",
                target="ffmpeg",
                operation="ffmpeg.transcode",
                risk_level="medium",
                read_only=False,
                schema={
                    "input_path": "str",
                    "output_path": "str",
                    "args": "list[str]",
                    "overwrite": "bool",
                    "timeout_seconds": "number",
                },
            )
        ),
    ]
