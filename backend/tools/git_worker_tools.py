from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_git_worker_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="git.status",
                description="Read Git repository status inside the governed workspace.",
                target="git",
                operation="git.status",
                risk_level="low",
                read_only=True,
                schema={"repo_path": "str", "short": "bool", "timeout_seconds": "number"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="git.diff",
                description="Read Git repository diff inside the governed workspace.",
                target="git",
                operation="git.diff",
                risk_level="low",
                read_only=True,
                schema={"repo_path": "str", "paths": "list[str]", "staged": "bool", "timeout_seconds": "number"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="git.commit",
                description="Create a Git commit inside the governed workspace.",
                target="git",
                operation="git.commit",
                risk_level="high",
                read_only=False,
                schema={"repo_path": "str", "message": "str", "allow_empty": "bool", "timeout_seconds": "number"},
            )
        ),
    ]
