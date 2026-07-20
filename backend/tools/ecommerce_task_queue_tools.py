from __future__ import annotations

import importlib
from typing import Any

from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec

queue = importlib.import_module("backend.orchestrator.ecommerce_task_queue")


def _bool_arg(arguments: dict[str, Any], name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _list_arg(arguments: dict[str, Any], name: str) -> list[str]:
    value = arguments.get(name)
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", "\n").splitlines() if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class EcommerceTaskQueueTool(BaseTool):
    def __init__(self, spec: ToolSpec):
        self.spec = spec

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(False, f"不支持的工具: {call.name}", error_code="tool_not_supported")
        arguments = dict(call.arguments or {})
        state_dir = arguments.get("state_dir") or None
        project_root = arguments.get("project_root") or None
        try:
            if self.spec.operation == "status":
                data = queue.status(state_dir=state_dir, project_root=project_root)
                return ToolResult(True, f"电商任务队列共有 {data['task_count']} 个任务", data=data)
            if self.spec.operation == "ingest_mobile_links":
                data = queue.ingest_mobile_links(
                    links_jsonl=arguments.get("links_jsonl") or "state/mobile-links/links.jsonl",
                    latest_link=arguments.get("latest_link") or "state/mobile-links/latest-link.txt",
                    include_latest=_bool_arg(arguments, "include_latest"),
                    include_test_links=_bool_arg(arguments, "include_test_links"),
                    state_dir=state_dir,
                    project_root=project_root,
                )
                return ToolResult(True, f"导入手机链接：新增 {len(data['created'])}，更新 {len(data['updated'])}，忽略 {data['ignored']}", data=data)
            if self.spec.operation == "enqueue_image":
                image = str(arguments.get("image") or "").strip()
                if not image:
                    return ToolResult(False, "缺少 image 参数", error_code="missing_params", metadata={"missing_param": "image"})
                data = queue.enqueue_image_task(
                    image=image,
                    source=str(arguments.get("source") or "manual"),
                    title=str(arguments.get("title") or ""),
                    task_id=str(arguments.get("task_id") or ""),
                    state_dir=state_dir,
                    project_root=project_root,
                )
                return ToolResult(True, f"图片任务 {'已创建' if data['created'] else '已存在'}: {data['task']['id']}", data=data)
            if self.spec.operation == "attach_probe":
                task_id = str(arguments.get("task_id") or "").strip()
                probe_result = str(arguments.get("probe_result") or "").strip()
                if not task_id or not probe_result:
                    return ToolResult(False, "缺少 task_id 或 probe_result 参数", error_code="missing_params")
                data = queue.attach_probe_artifacts(
                    task_id=task_id,
                    probe_result=probe_result,
                    screenshots=_list_arg(arguments, "screenshots"),
                    keep_screenshots=_bool_arg(arguments, "keep_screenshots"),
                    ttl_hours=int(arguments.get("ttl_hours") or queue.DEFAULT_TEMP_TTL_HOURS),
                    state_dir=state_dir,
                    project_root=project_root,
                )
                return ToolResult(True, f"已挂载 {len(data['artifacts'])} 个 probe 产物", data=data)
            if self.spec.operation == "attach_productdata":
                task_id = str(arguments.get("task_id") or "").strip()
                product_data_json = str(arguments.get("product_data_json") or "").strip()
                if not task_id or not product_data_json:
                    return ToolResult(False, "缺少 task_id 或 product_data_json 参数", error_code="missing_params")
                data = queue.attach_productdata_artifact(
                    task_id=task_id,
                    product_data_json=product_data_json,
                    control_plane_artifact_id=str(arguments.get("control_plane_artifact_id") or ""),
                    state_dir=state_dir,
                    project_root=project_root,
                )
                listing_gate = data.get("validation", {}).get("listingGate", {}) if isinstance(data.get("validation"), dict) else {}
                status = data.get("task", {}).get("status", "unknown") if isinstance(data.get("task"), dict) else "unknown"
                return ToolResult(True, f"浏览器扩展 productData 已挂载，任务状态: {status}", data=data, metadata={"listingGateOk": bool(listing_gate.get("ok"))})
            if self.spec.operation == "cleanup_temp":
                data = queue.cleanup_temporary_artifacts(
                    older_than_hours=int(arguments.get("older_than_hours") or queue.DEFAULT_TEMP_TTL_HOURS),
                    dry_run=_bool_arg(arguments, "dry_run"),
                    state_dir=state_dir,
                    project_root=project_root,
                )
                return ToolResult(True, f"临时产物清理：命中 {len(data['deleted'])} 个", data=data)
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}", error_code="tool_exception", metadata={"exception_type": type(exc).__name__})
        return ToolResult(False, f"未实现的电商队列操作: {self.spec.operation}", error_code="operation_not_supported")


def get_ecommerce_task_queue_tools() -> list[BaseTool]:
    return [
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.status",
                description="查看本项目电商 RPA 任务队列状态。",
                target="ecommerce_task_queue",
                operation="status",
                risk_level="low",
                read_only=True,
                schema={"state_dir": "string", "project_root": "string"},
            )
        ),
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.ingest_mobile_links",
                description="把手机桥接收到的拼多多链接导入电商任务队列，默认跳过测试链接。",
                target="ecommerce_task_queue",
                operation="ingest_mobile_links",
                risk_level="low",
                schema={"links_jsonl": "string", "latest_link": "string", "include_latest": "boolean", "include_test_links": "boolean", "state_dir": "string", "project_root": "string"},
            )
        ),
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.enqueue_image",
                description="把本地商品图片登记为电商 RPA 上传/拍照搜索任务。",
                target="ecommerce_task_queue",
                operation="enqueue_image",
                risk_level="medium",
                schema={"image": "string", "source": "string", "title": "string", "task_id": "string", "state_dir": "string", "project_root": "string"},
            )
        ),
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.attach_probe",
                description="把 probe/OCR JSON 和截图挂到电商 RPA 任务产物生命周期。",
                target="ecommerce_task_queue",
                operation="attach_probe",
                risk_level="medium",
                schema={"task_id": "string", "probe_result": "string", "screenshots": "array", "keep_screenshots": "boolean", "ttl_hours": "integer", "state_dir": "string", "project_root": "string"},
            )
        ),
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.attach_productdata",
                description="把浏览器扩展生成的 productData JSON 挂到电商任务并更新完整性门禁。",
                target="ecommerce_task_queue",
                operation="attach_productdata",
                risk_level="medium",
                schema={
                    "task_id": "string",
                    "product_data_json": "string",
                    "control_plane_artifact_id": "string",
                    "state_dir": "string",
                    "project_root": "string",
                },
            )
        ),
        EcommerceTaskQueueTool(
            ToolSpec(
                name="ecommerce.task_queue.cleanup_temp",
                description="清理电商 RPA 任务中已过期的临时 OCR 截图产物。",
                target="ecommerce_task_queue",
                operation="cleanup_temp",
                risk_level="medium",
                schema={"older_than_hours": "integer", "dry_run": "boolean", "state_dir": "string", "project_root": "string"},
            )
        ),
    ]
