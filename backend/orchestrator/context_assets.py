from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.executors.base import ExecutionRequest
from backend.orchestrator.perception_context import (
    build_perception_request,
    merge_visual_context,
    perception_context_requested,
    summarize_perception_data,
)
from backend.orchestrator.prompt_context import format_inventory_hardware, format_inventory_software


@dataclass(frozen=True)
class ContextAssetServices:
    evaluate_policy: Callable[[ExecutionRequest], Any]
    requires_confirmation: Callable[[ExecutionRequest], bool]
    worker_pool: Any


class ContextAssetStore:
    def __init__(self, services: ContextAssetServices):
        self._services = services
        self.inventory: dict[str, object] = {
            "software": [],
            "cli_tools": [],
            "hardware": [],
            "devices": {},
        }

    def initialize_local_inventory(self, backend: Any) -> None:
        try:
            if hasattr(backend, "list_installed_apps"):
                self.inventory["software"] = backend.list_installed_apps(limit=200)
            if hasattr(backend, "list_cli_tools"):
                self.inventory["cli_tools"] = backend.list_cli_tools(limit=80)
        except Exception:
            return

    def enrich_perception(
        self,
        *,
        user_input: str,
        visual_context: str,
        metadata: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        metadata = dict(metadata or {})
        if not perception_context_requested(metadata):
            return visual_context, metadata
        request = build_perception_request(user_input, metadata)
        record: dict[str, object] = {
            "requested": True,
            "target": request.target,
            "operation": request.operation,
            "params": dict(request.params or {}),
            "read_only": True,
        }
        policy_decision = self._services.evaluate_policy(request)
        if policy_decision is not None and not getattr(policy_decision, "allowed", True):
            record.update(
                {
                    "success": False,
                    "error_code": "policy_denied",
                    "message": f"安全策略已拦截感知上下文：{policy_decision.reason}",
                    "policy_decision": {
                        "allowed": policy_decision.allowed,
                        "require_confirmation": policy_decision.require_confirmation,
                        "reason": policy_decision.reason,
                        "matched_rule_id": policy_decision.matched_rule_id,
                    },
                }
            )
            return visual_context, {**metadata, "perception_context": record}
        if self._services.requires_confirmation(request):
            record.update(
                {
                    "success": False,
                    "error_code": "permission_required",
                    "message": "感知上下文需要用户确认，已跳过自动注入。",
                }
            )
            return visual_context, {**metadata, "perception_context": record}
        try:
            worker_execution = self._services.worker_pool.execute(
                request,
                actor=str(metadata.get("actor") or "agent_cluster"),
                metadata={"user_input": user_input, "purpose": "perception_context"},
            )
            result = worker_execution.result
            record.update(
                {
                    "success": bool(result.success),
                    "message": result.message,
                    "error_code": result.error_code,
                    "metadata": dict(result.metadata or {}),
                    "worker": worker_execution.worker.snapshot() if worker_execution.worker is not None else None,
                    "worker_audit": worker_execution.audit_event.snapshot(),
                }
            )
            if result.success:
                summary = summarize_perception_data(result.data, operation=request.operation)
                record["summary"] = summary
                return merge_visual_context(visual_context, summary), {**metadata, "perception_context": record}
            return visual_context, {**metadata, "perception_context": record}
        except Exception as exc:
            record.update({"success": False, "error_code": "perception_context_exception", "message": str(exc)})
            return visual_context, {**metadata, "perception_context": record}

    def remember_inventory(
        self,
        request: ExecutionRequest,
        data: Any,
        metadata: dict | None = None,
    ) -> dict[str, object] | None:
        if request.operation == "list_installed_apps" and isinstance(data, list):
            apps = [item for item in data if isinstance(item, dict)]
            self.inventory["software"] = apps[:80]
            return self.remember_scoped("software", apps, request=request, metadata=metadata)
        if request.operation == "list_hardware_devices" and isinstance(data, list):
            devices = [item for item in data if isinstance(item, dict)]
            self.inventory["hardware"] = devices[:80]
            return self.remember_scoped("hardware", devices, request=request, metadata=metadata)
        return None

    def build_inventory_context(self) -> str:
        parts: list[str] = []
        devices = self.inventory.get("devices")
        if isinstance(devices, dict) and devices:
            for scope_id, record in list(devices.items())[:6]:
                if not isinstance(record, dict):
                    continue
                label = str(record.get("label") or scope_id).strip()
                device_parts = []
                software_names = format_inventory_software(record.get("software", []), limit=20)
                hardware_names = format_inventory_hardware(record.get("hardware", []), limit=12)
                if software_names:
                    device_parts.append("软件=" + "、".join(software_names))
                if hardware_names:
                    device_parts.append("硬件=" + "、".join(hardware_names))
                if device_parts:
                    parts.append(f"[{label}] " + "；".join(device_parts))

        software_names = format_inventory_software(self.inventory.get("software", []), limit=30)
        if software_names:
            parts.append("最近扫描到的软件：" + "、".join(software_names))
        hardware_names = format_inventory_hardware(self.inventory.get("hardware", []), limit=20)
        if hardware_names:
            parts.append("最近扫描到的硬件：" + "、".join(hardware_names))
        return "设备/软件库存上下文：\n" + "\n".join(parts) if parts else ""

    @staticmethod
    def inventory_scope(request: ExecutionRequest, metadata: dict | None = None) -> tuple[str, dict[str, str]]:
        metadata = dict(metadata or {})
        node_id = str(metadata.get("node_id") or request.params.get("node_id") or "").strip()
        remote_target = str(metadata.get("remote_target") or request.params.get("remote_target") or "").strip()
        if node_id:
            scope_id = f"remote:{node_id}:{remote_target or request.target}"
            return scope_id, {"node_id": node_id, "remote_target": remote_target or request.target, "label": node_id}
        target = request.target or "local_pc"
        return target, {"target": target, "label": target}

    def remember_scoped(
        self,
        kind: str,
        items: list[dict],
        *,
        request: ExecutionRequest,
        metadata: dict | None = None,
    ) -> dict[str, object]:
        scope_id, scope_metadata = self.inventory_scope(request, metadata)
        devices = self.inventory.setdefault("devices", {})
        if not isinstance(devices, dict):
            devices = {}
            self.inventory["devices"] = devices
        record = dict(devices.get(scope_id) or {})
        record.update(scope_metadata)
        record.setdefault("software", [])
        record.setdefault("hardware", [])
        record[kind] = items[:80]
        devices[scope_id] = record
        update: dict[str, object] = {"kind": kind, "count": len(items), "scope": scope_id}
        update.update({key: value for key, value in scope_metadata.items() if value})
        return update
