from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_browser_worker_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="browser.worker_health",
                description="Check the configured process-backed Browser worker.",
                target="browser",
                operation="browser.health_check",
                risk_level="low",
                read_only=True,
                schema={
                    "type": "object",
                    "properties": {"timeout_seconds": {"type": "number", "minimum": 0.1}},
                    "additionalProperties": True,
                },
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="browser.worker_open_url",
                description="Open a URL through the process-backed Browser worker.",
                target="browser",
                operation="browser_open_url",
                risk_level="medium",
                read_only=False,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "timeout_seconds": {"type": "number", "minimum": 0.1},
                    },
                    "required": ["url"],
                    "additionalProperties": True,
                },
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="browser.worker_search",
                description="Run a browser search through the process-backed Browser worker.",
                target="browser",
                operation="browser_search",
                risk_level="medium",
                read_only=False,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "timeout_seconds": {"type": "number", "minimum": 0.1},
                    },
                    "required": ["query"],
                    "additionalProperties": True,
                },
            )
        ),
    ]
