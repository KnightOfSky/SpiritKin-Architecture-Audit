from __future__ import annotations

import os

from backend.knowledge.base import BaseKnowledgeRetriever
from backend.security.safety_control import evaluate_execution_safety
from backend.security.tool_authz import ToolAuthzRegistry
from backend.tools.android_tools import get_android_tools
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec
from backend.tools.browser_worker_tools import get_browser_worker_tools
from backend.tools.desktop_tools import get_desktop_tools
from backend.tools.ecommerce_task_queue_tools import get_ecommerce_task_queue_tools
from backend.tools.email_tools import get_email_tools
from backend.tools.feishu_tools import get_feishu_tools
from backend.tools.ffmpeg_worker_tools import get_ffmpeg_worker_tools
from backend.tools.game_automation_tools import get_game_automation_tools
from backend.tools.git_worker_tools import get_git_worker_tools
from backend.tools.kb_write_tools import get_kb_write_tools
from backend.tools.knowledge_tools import KnowledgeSearchTool
from backend.tools.manifest_loader import discover_manifest_tools
from backend.tools.mcp_adapter import build_mcp_adapter_from_config
from backend.tools.music_tools import get_music_tools
from backend.tools.openclaw_tools import get_openclaw_tools
from backend.tools.python_worker_tools import get_python_worker_tools
from backend.tools.service_rag_worker_tools import get_service_rag_worker_tools
from backend.tools.web_search_tools import WebSearchTool
from backend.tools.workflow_graph_tools import get_workflow_graph_tools


class ToolRegistry:
    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        *,
        authz_registry: ToolAuthzRegistry | None = None,
        bootstrap_legacy_tools: bool = False,
    ):
        self._tools: dict[str, BaseTool] = {}
        self._authz_registry = authz_registry or ToolAuthzRegistry()
        self._manifest_discovery: dict[str, object] = {
            "root_precedence": [],
            "loaded_files": [],
            "errors": [],
            "conflicts": [],
        }
        self._bootstrap_legacy_tools = bool(bootstrap_legacy_tools or tools)
        if tools:
            self.register_many(tools)
        self._bootstrap_legacy_tools = bool(bootstrap_legacy_tools)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.spec.name] = tool
        self._authz_registry.ensure_tool(tool.spec, legacy_import=self._bootstrap_legacy_tools)

    def register_many(self, tools: list[BaseTool]) -> None:
        for tool in tools:
            self._tools[tool.spec.name] = tool
        self._authz_registry.ensure_tools(
            [tool.spec for tool in tools],
            legacy_import=self._bootstrap_legacy_tools,
        )

    def get(self, tool_name: str) -> BaseTool | None:
        return self._tools.get(tool_name)

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def finish_legacy_import(self) -> None:
        self._bootstrap_legacy_tools = False

    def set_manifest_discovery(self, discovery, *, registration_conflicts: list[dict[str, str]] | None = None) -> None:
        self._manifest_discovery = {
            "root_precedence": list(getattr(discovery, "root_precedence", ()) or ()),
            "loaded_files": list(getattr(discovery, "loaded_files", ()) or ()),
            "errors": [dict(item) for item in getattr(discovery, "errors", ()) or ()],
            "conflicts": [
                *[dict(item) for item in getattr(discovery, "conflicts", ()) or ()],
                *[dict(item) for item in registration_conflicts or []],
            ],
        }

    def manifest_discovery_snapshot(self) -> dict[str, object]:
        return {
            **self._manifest_discovery,
            "loaded_count": len(self._manifest_discovery["loaded_files"]),
            "error_count": len(self._manifest_discovery["errors"]),
            "conflict_count": len(self._manifest_discovery["conflicts"]),
        }

    def invoke(self, call: ToolCall) -> ToolResult:
        tool = self.get(call.name)
        if tool is None:
            return ToolResult(
                success=False,
                message=f"未注册工具: {call.name}",
                error_code="tool_not_registered",
                metadata={"tool_name": call.name},
            )
        authz = self._authz_registry.evaluate(
            tool.spec.name,
            dict(call.arguments or {}),
            fallback_risk="safe",
        )
        enforce_confirmation = bool((call.arguments or {}).get("authz_enforce_confirmation"))
        if not authz.allowed and (authz.reason != "tool_confirmation_required" or enforce_confirmation):
            return ToolResult(
                success=False,
                message=(f"工具已被授权策略禁用: {call.name}" if authz.reason == "tool_disabled_by_operator" else f"工具调用需要确认: {call.name}"),
                error_code=authz.reason,
                metadata={"tool_name": call.name, "tool_authz": authz.snapshot()},
            )
        safety = evaluate_execution_safety(
            target=tool.spec.target,
            operation=tool.spec.operation,
            actor=str((call.arguments or {}).get("actor") or ""),
            read_only=tool.spec.read_only,
            dry_run=bool((call.arguments or {}).get("dry_run")),
        )
        if not safety.allowed:
            return ToolResult(
                success=False,
                message=safety.message,
                error_code=safety.error_code,
                metadata={"tool_name": call.name, "safety": safety.snapshot()},
            )
        result = tool.invoke(call)
        result.metadata = {
            **dict(result.metadata or {}),
            "tool_authz": authz.snapshot(),
            **({"tool_authz_confirmation_deferred": True} if authz.reason == "tool_confirmation_required" else {}),
        }
        return result


def build_default_tool_registry(
    knowledge_retriever: BaseKnowledgeRetriever | None = None,
    worker_pool=None,
    *,
    allow_dynamic_mcp_discovery: bool = True,
    workflow_store_factory=None,
) -> ToolRegistry:
    registry = ToolRegistry(bootstrap_legacy_tools=True)
    registry.register_many(get_desktop_tools())
    registry.register_many(get_feishu_tools())
    registry.register_many(get_email_tools())
    registry.register_many(get_openclaw_tools())
    registry.register_many(get_android_tools())
    registry.register_many(get_browser_worker_tools())
    registry.register_many(get_kb_write_tools())
    registry.register_many(get_python_worker_tools())
    registry.register_many(get_git_worker_tools())
    registry.register_many(get_ffmpeg_worker_tools())
    registry.register_many(get_service_rag_worker_tools())
    registry.register_many(get_ecommerce_task_queue_tools())
    registry.register_many(
        get_workflow_graph_tools(
            tool_registry=registry,
            worker_pool=worker_pool,
            workflow_store_factory=workflow_store_factory,
        )
    )
    registry.register(WebSearchTool())

    if knowledge_retriever is not None:
        registry.register(KnowledgeSearchTool(knowledge_retriever))

    registry.finish_legacy_import()
    registry.register_many(get_music_tools())
    registry.register_many(get_game_automation_tools())
    manifest_discovery = discover_manifest_tools()
    registration_conflicts: list[dict[str, str]] = []
    for tool, source_path in zip(manifest_discovery.tools, manifest_discovery.loaded_files, strict=True):
        existing = registry.get(tool.spec.name)
        if existing is not None:
            registration_conflicts.append(
                {
                    "tool_id": tool.spec.name,
                    "winner_path": "builtin_registry",
                    "winner_root": "builtin_registry",
                    "shadowed_path": source_path,
                    "shadowed_root": "manifest",
                    "resolution": "builtin_tool_wins",
                }
            )
            continue
        registry.register(tool)
    registry.set_manifest_discovery(manifest_discovery, registration_conflicts=registration_conflicts)

    try:
        from backend.app.mcp_management import mcp_adapter_config_entries

        mcp_adapter = build_mcp_adapter_from_config(mcp_adapter_config_entries())
        mappings = mcp_adapter.list_mappings()
        if allow_dynamic_mcp_discovery and str(os.environ.get("SPIRITKIN_MCP_DYNAMIC_TOOL_REGISTRATION", "")).strip().lower() in {"1", "true", "yes", "on"}:
            discovered_mappings = mcp_adapter.discover_tool_mappings()
            for mapping in discovered_mappings:
                mcp_adapter.register_mapping(mapping)
            mappings = [*mappings, *discovered_mappings]
        for mapping in mappings:
            tool = mcp_adapter.generate_tool_registry_entry(mapping.mcp_server, mapping.mcp_tool_name)
            if tool is not None:
                registry.register(tool)
    except Exception:
        # MCP registry failures must not prevent core local tools from loading.
        pass

    return registry
