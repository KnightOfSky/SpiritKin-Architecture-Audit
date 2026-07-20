from __future__ import annotations

from backend.action.atomic_operations import list_default_atomic_operations
from backend.tools.base import ExecutionTool, ToolSpec


def get_desktop_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name=operation.name,
                description=operation.description,
                target="local_pc",
                operation=operation.operation,
                risk_level=operation.risk_level,
                read_only=operation.read_only,
                schema=operation.params_schema,
            )
        )
        for operation in list_default_atomic_operations()
    ]