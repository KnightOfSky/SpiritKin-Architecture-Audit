from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class FailureRecord:
    stage: str
    actor: str
    message: str
    error_code: str = ""
    user_input: str = ""
    tool_name: str = ""
    execution_target: str = ""
    execution_operation: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class RepairAdvice:
    summary: str
    should_retry: bool = False
    suggested_actions: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    suggested_test_commands: list[str] = field(default_factory=list)
    safe_to_auto_apply: bool = False
    requires_human_review: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairPlan:
    failure: FailureRecord
    summary: str
    suggested_actions: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    suggested_test_commands: list[str] = field(default_factory=list)
    safe_to_auto_apply: bool = False
    requires_human_review: bool = True
    should_retry_after_fix: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DevelopmentPlan:
    request: str
    summary: str
    target_integrations: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    suggested_test_commands: list[str] = field(default_factory=list)
    acceptance_checks: list[str] = field(default_factory=list)
    safe_to_auto_apply: bool = False
    requires_human_review: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseRepairAdvisor(ABC):
    @abstractmethod
    def analyze(self, failure: FailureRecord) -> RepairAdvice | None:
        raise NotImplementedError


class NullRepairAdvisor(BaseRepairAdvisor):
    def analyze(self, failure: FailureRecord) -> RepairAdvice | None:
        return None


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _candidate_files_for_failure(failure: FailureRecord) -> list[str]:
    candidates = []
    if failure.stage == "tool":
        candidates.extend(["backend/tools/registry.py", "backend/orchestrator/agent_cluster.py"])
        if failure.tool_name == "kb.search":
            candidates.append("backend/tools/knowledge_tools.py")

    if failure.stage == "executor":
        candidates.extend(["backend/orchestrator/agent_cluster.py", "backend/orchestrator/planner.py"])
        if failure.execution_target in {"openclaw", "arm"} or failure.actor == "openclaw":
            candidates.append("backend/executors/openclaw_executor.py")
        if failure.execution_target in {"local_pc", "desktop", "pointer", "keyboard"} or failure.actor == "local_pc":
            candidates.append("backend/executors/local_pc_executor.py")
        if failure.error_code.startswith("remote_") or failure.actor == "remote":
            candidates.extend(["backend/executors/remote_executor.py", "backend/executors/node_registry.py"])

    return _dedupe_keep_order(candidates)


def _suggested_tests_for_failure(failure: FailureRecord) -> list[str]:
    commands = []
    if failure.stage == "tool":
        commands.append("python -m unittest backend.tests.unit.test_tooling_and_remote -v")
        if failure.tool_name == "kb.search":
            commands.append("python -m unittest backend.tests.unit.test_agent_cluster -v")

    if failure.stage == "executor":
        commands.append("python -m unittest backend.tests.unit.test_agent_cluster -v")
        if failure.execution_target in {"openclaw", "arm"} or failure.actor == "openclaw":
            commands.append("python -m unittest backend.tests.unit.test_openclaw_executor -v")
        if failure.error_code.startswith("remote_") or failure.actor == "remote":
            commands.append("python -m unittest backend.tests.unit.test_tooling_and_remote -v")

    if not commands:
        commands.append("python -m unittest discover backend/tests/unit -v")
    return _dedupe_keep_order(commands)


def _identify_integration_targets(request: str) -> list[str]:
    normalized = request.lower()
    targets = []
    if "飞书" in request or "feishu" in normalized or "lark" in normalized:
        targets.append("feishu")
    if "抖店" in request or "抖音小店" in request or "douyin" in normalized or "tiktok shop" in normalized:
        targets.append("douyin_shop")
    if "vscode" in normalized or "visual studio code" in normalized or "代码编辑器" in request or "编辑器" in request:
        targets.append("code_editor")
    if not targets:
        targets.append("external_api")
    return _dedupe_keep_order(targets)


def _candidate_files_for_development_request(targets: list[str]) -> list[str]:
    files = [
        "backend/tools/registry.py",
        "backend/orchestrator/planner.py",
        "backend/orchestrator/agent_cluster.py",
        "backend/tests/unit/test_tooling_and_remote.py",
        "backend/tests/unit/test_agent_cluster.py",
    ]
    if "feishu" in targets:
        files.extend([
            "backend/tools/feishu_tools.py",
            "backend/services/feishu_client.py",
            "backend/tests/unit/test_feishu_tools.py",
        ])
    if "douyin_shop" in targets:
        files.extend([
            "backend/tools/douyin_shop_tools.py",
            "backend/services/douyin_shop_client.py",
            "backend/tests/unit/test_douyin_shop_tools.py",
        ])
    if "code_editor" in targets:
        files.extend([
            "backend/tools/editor_tools.py",
            "backend/services/editor_bridge.py",
            "backend/tools/desktop_tools.py",
            "backend/tests/unit/test_editor_tools.py",
        ])
    return _dedupe_keep_order(files)


def _suggested_actions_for_development_request(targets: list[str]) -> list[str]:
    actions = [
        "先确认目标软件的认证方式、权限范围、限流与回调约束",
        "优先走 API / SDK / 扩展桥接，只有缺接口时才退回 GUI + 视觉兜底",
        "在 backend/tools 中定义语义工具，并为写操作标记合适的 risk_level",
        "在 backend/tools/registry.py 注册新工具，并在 planner 中补充意图路由",
        "把外部客户端封装到 backend/services，避免把鉴权和请求细节散落到 orchestrator",
        "为高风险动作接入确认门，确保执行前必须得到人工确认",
        "补最小单测与审核说明，默认只进入人工审核流，不直接自动合入",
    ]
    if "feishu" in targets:
        actions.extend([
            "梳理 tenant_access_token / app 凭证、消息发送、回调签名校验",
            "优先把飞书消息、文档、审批等能力拆成独立工具，避免一个巨型万能接口",
        ])
    if "douyin_shop" in targets:
        actions.extend([
            "梳理抖店 app 授权、签名算法、商品/订单/售后等接口范围",
            "先从只读查询工具起步，再逐步开放发货、改价等高风险写操作",
        ])
    if "code_editor" in targets:
        actions.extend([
            "优先选择编辑器扩展、LSP、CLI 或 RPC 桥接，不建议一开始就全靠屏幕点击",
            "保留 desktop_tools 作为兜底方案，但把 GUI 自动化限制在必须场景",
        ])
    return _dedupe_keep_order(actions)


def _suggested_tests_for_development_request(targets: list[str]) -> list[str]:
    commands = [
        "python -m unittest backend.tests.unit.test_agent_cluster backend.tests.unit.test_tooling_and_remote -v",
    ]
    if "feishu" in targets:
        commands.append("python -m unittest backend.tests.unit.test_feishu_tools -v")
    if "code_editor" in targets:
        commands.append("python -m unittest backend.tests.unit.test_runtime -v")
    return _dedupe_keep_order(commands)


def _acceptance_checks_for_development_request(targets: list[str]) -> list[str]:
    checks = [
        "新增工具已注册，Planner 能稳定路由到目标能力",
        "所有高风险写操作都需要显式确认后才能执行",
        "最小单测通过，且失败路径会产出结构化错误与 repair plan",
        "变更以 diff + 风险说明形式交给人工审核",
    ]
    if "feishu" in targets:
        checks.extend([
            "tenant_access_token 具备刷新与缓存策略，不把凭证逻辑散落在工具层",
            "飞书消息发送、审批发起等写操作默认经过确认门",
            "如果接回调，事件签名校验与幂等处理有最小测试覆盖",
        ])
    if "douyin_shop" in targets:
        checks.append("订单、商品等接口签名校验和限流约束有明确封装")
    if "code_editor" in targets:
        checks.append("优先扩展或桥接通道，GUI 自动化仅作为兜底方案")
    return _dedupe_keep_order(checks)


def _target_specific_metadata(targets: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if "feishu" in targets:
        metadata.update(
            {
                "proposed_first_scope": [
                    "feishu.message.send",
                    "feishu.contact.resolve_user",
                    "feishu.approval.create",
                ],
                "high_risk_operations": [
                    "feishu.message.send",
                    "feishu.approval.create",
                ],
            }
        )
    if "douyin_shop" in targets:
        metadata["suggested_rollout_mode"] = "read_only_first"
    if "code_editor" in targets:
        metadata["preferred_bridge"] = "extension_or_rpc"
    return metadata


def build_development_plan(request: str, available_tool_names: list[str] | None = None) -> DevelopmentPlan:
    targets = _identify_integration_targets(request)
    summary_targets = "、".join(targets)
    return DevelopmentPlan(
        request=request,
        summary=f"建议按 API-first + 人工审核路线为 {summary_targets} 生成接入方案，并保留 GUI/视觉兜底。",
        target_integrations=targets,
        suggested_actions=_suggested_actions_for_development_request(targets),
        candidate_files=_candidate_files_for_development_request(targets),
        suggested_test_commands=_suggested_tests_for_development_request(targets),
        acceptance_checks=_acceptance_checks_for_development_request(targets),
        safe_to_auto_apply=False,
        requires_human_review=True,
        metadata={
            "delivery_mode": "human_reviewed_self_development",
            "api_first": True,
            "fallback_mode": "gui_or_vision_when_needed",
            "existing_tools": list(available_tool_names or []),
            **_target_specific_metadata(targets),
        },
    )


class RuleBasedRepairAdvisor(BaseRepairAdvisor):
    def analyze(self, failure: FailureRecord) -> RepairAdvice | None:
        summary = "建议先做最小范围排查。"
        suggested_actions: list[str] = []

        if failure.error_code == "executor_not_found":
            summary = "没有找到可处理该请求的执行器，先检查 target 路由与执行器挂载。"
            suggested_actions = [
                "检查 Planner 生成的 target / operation 是否正确",
                "检查 AgentCluster 是否已挂载对应 executor",
                "核对 executor.supports() 与请求目标是否一致",
            ]
        elif failure.error_code == "tool_not_registered":
            summary = "工具未注册，先检查 ToolRegistry 与规划出的工具名是否一致。"
            suggested_actions = [
                "核对 ToolCall.name 与 ToolSpec.name",
                "确认 build_default_tool_registry() 是否注册了目标工具",
            ]
        elif failure.error_code == "missing_params":
            missing_param = failure.metadata.get("missing_param", "未知参数")
            summary = f"调用缺少参数 {missing_param}，先检查参数映射与默认值。"
            suggested_actions = [
                "检查 planner / tool 层是否正确传参",
                "补充参数校验与更清晰的错误提示",
            ]
        elif failure.error_code in {"unsupported_operation", "unsupported_target"}:
            summary = "当前能力声明与执行实现不一致，先补齐路由或收窄能力暴露。"
            suggested_actions = [
                "核对 supports()、ToolSpec 和实际 execute() 分支的一致性",
                "必要时补单测覆盖不支持路径",
            ]
        elif failure.error_code == "remote_node_not_found":
            summary = "未找到匹配的远端节点，先检查节点注册信息、别名和 target 映射。"
            suggested_actions = [
                "检查 NodeRegistry 中的 aliases / targets 配置",
                "确认请求 target 与远端节点暴露能力一致",
            ]
        elif failure.error_code in {"remote_execution_exception", "executor_exception"}:
            summary = "执行阶段抛出了异常，先复现并缩小到最小失败调用。"
            suggested_actions = [
                "复现异常并查看原始错误信息",
                "补充异常处理与最小单测",
            ]

        return RepairAdvice(
            summary=summary,
            should_retry=False,
            suggested_actions=suggested_actions,
            candidate_files=_candidate_files_for_failure(failure),
            suggested_test_commands=_suggested_tests_for_failure(failure),
            safe_to_auto_apply=False,
            requires_human_review=True,
            metadata={"error_code": failure.error_code, "stage": failure.stage},
        )


def build_repair_plan(failure: FailureRecord, advice: RepairAdvice | None = None) -> RepairPlan:
    advice = advice or RuleBasedRepairAdvisor().analyze(failure) or RepairAdvice(summary="建议先做最小范围排查。")
    return RepairPlan(
        failure=failure,
        summary=advice.summary,
        suggested_actions=list(advice.suggested_actions),
        candidate_files=list(advice.candidate_files),
        suggested_test_commands=list(advice.suggested_test_commands),
        safe_to_auto_apply=advice.safe_to_auto_apply,
        requires_human_review=advice.requires_human_review,
        should_retry_after_fix=advice.should_retry,
        metadata=dict(advice.metadata),
    )