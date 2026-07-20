from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_python_worker_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="python.run_script",
                description="Run a Python script under the governed local workspace runtime.",
                target="python",
                operation="python.run",
                risk_level="medium",
                read_only=False,
                schema={
                    "type": "object",
                    "properties": {
                        "script_path": {
                            "type": "string",
                            "description": "Workspace-relative or workspace-contained absolute .py script path.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string", "description": "Optional workspace-contained working directory."},
                        "timeout_seconds": {"type": "number", "minimum": 0.1},
                    },
                    "required": ["script_path"],
                    "additionalProperties": True,
                },
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="python.install_package",
                description="Install one exact PyPI package through the governed Python runtime.",
                target="python",
                operation="python.install_package",
                risk_level="high",
                authz_risk="shell",
                read_only=False,
                schema={
                    "type": "object",
                    "properties": {
                        "package": {
                            "type": "string",
                            "description": "PyPI package name, optionally pinned with ==version; URLs and shell fragments are rejected.",
                        },
                        "timeout_seconds": {"type": "number", "minimum": 0.1},
                    },
                    "required": ["package"],
                    "additionalProperties": False,
                },
            )
        ),
    ]
