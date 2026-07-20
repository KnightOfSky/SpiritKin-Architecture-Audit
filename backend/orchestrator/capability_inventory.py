from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.tools.base import ToolSpec


@dataclass(frozen=True)
class CapabilityInventory:
    tools: list[dict[str, Any]] = field(default_factory=list)
    executors: list[dict[str, Any]] = field(default_factory=list)
    software: list[dict[str, Any]] = field(default_factory=list)
    cli_tools: list[dict[str, Any]] = field(default_factory=list)
    hardware: list[dict[str, Any]] = field(default_factory=list)
    devices: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "tools": list(self.tools),
            "executors": list(self.executors),
            "software": list(self.software),
            "cli_tools": list(self.cli_tools),
            "hardware": list(self.hardware),
            "devices": dict(self.devices),
            "summary": self.summary,
        }


def build_capability_inventory(
    *,
    tools: list[ToolSpec],
    executors: list[Any] | None = None,
    device_backend=None,
    software_limit: int = 80,
    hardware_limit: int = 80,
) -> CapabilityInventory:
    tool_rows = [
        {
            "name": tool.name,
            "target": tool.target,
            "operation": tool.operation,
            "risk_level": tool.risk_level,
            "read_only": tool.read_only,
            "schema": dict(tool.schema or {}),
        }
        for tool in tools
    ]
    executor_rows = [
        {
            "name": getattr(executor, "name", executor.__class__.__name__),
            "class": executor.__class__.__name__,
        }
        for executor in list(executors or [])
    ]
    software = _scan_backend_list(device_backend, "list_installed_apps", software_limit)
    cli_tools = _scan_cli_tools(device_backend)
    hardware = _scan_backend_list(device_backend, "list_hardware_devices", hardware_limit)
    devices = {"local_pc": {"available": device_backend is not None}}
    summary = _build_summary(tool_rows, executor_rows, software, cli_tools, hardware)
    return CapabilityInventory(tool_rows, executor_rows, software, cli_tools, hardware, devices, summary)


def _scan_backend_list(device_backend, method_name: str, limit: int) -> list[dict[str, Any]]:
    if device_backend is None:
        return []
    method = getattr(device_backend, method_name, None)
    if method is None:
        return []
    try:
        data = method(limit=limit)
    except Exception as exc:
        return [{"error": str(exc), "method": method_name}]
    return [dict(item) for item in data if isinstance(item, dict)]


def _scan_cli_tools(device_backend) -> list[dict[str, Any]]:
    if device_backend is None:
        return []
    method = getattr(device_backend, "list_cli_tools", None)
    if method is None:
        return []
    try:
        data = method()
    except Exception as exc:
        return [{"error": str(exc), "method": "list_cli_tools"}]
    return [dict(item) for item in data if isinstance(item, dict)]


def _build_summary(
    tools: list[dict[str, Any]],
    executors: list[dict[str, Any]],
    software: list[dict[str, Any]],
    cli_tools: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
) -> str:
    launchable = sum(1 for item in software if item.get("can_launch"))
    available_cli = sum(1 for item in cli_tools if item.get("available"))
    high_risk = sum(1 for item in tools if item.get("risk_level") == "high")
    return (
        f"工具 {len(tools)} 个，执行器 {len(executors)} 个，"
        f"软件记录 {len(software)} 条（可启动 {launchable} 个），"
        f"命令行工具 {available_cli}/{len(cli_tools)} 个可用，"
        f"硬件记录 {len(hardware)} 条，高风险工具 {high_risk} 个。"
    )
