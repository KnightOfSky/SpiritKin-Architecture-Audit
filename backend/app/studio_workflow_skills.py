from __future__ import annotations

from typing import Any

from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action

STUDIO_WORKFLOW_SKILL_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "studio.project_stage_detect.workflow",
        "description": "识别项目当前阶段、已有产物、缺口和下一步入口，作为多 Agent 工作流启动前的阶段检测。",
        "trigger_intents": ["阶段检测", "project stage detect", "项目状态识别", "下一步入口"],
        "input_schema": {"project_root": "str", "request": "str", "known_artifacts": "list"},
        "content_ref": "{{known_artifacts}}",
        "tags": ["studio_workflow", "stage_detect"],
        "success_criteria": ["输出当前阶段", "列出缺失产物", "给出下一步推荐 Skill 或工作流"],
        "owner_agent_id": "main_text",
        "owner_domain": "general",
        "module_id": "studio_stage_detect",
    },
    {
        "name": "studio.architecture_decision.workflow",
        "description": "把关键技术选择写成 ADR 候选，并检查状态归属、接口契约和禁止模式冲突。",
        "trigger_intents": ["架构决策", "ADR", "architecture decision", "技术选型"],
        "input_schema": {"decision": "str", "context": "str", "constraints": "list"},
        "content_ref": "{{context}}",
        "tags": ["studio_workflow", "adr", "governance"],
        "success_criteria": ["明确决策与备选方案", "标注状态 owner 和接口契约", "列出验证命令和回滚条件"],
        "owner_agent_id": "programming",
        "owner_domain": "programming",
        "module_id": "studio_architecture_decision",
    },
    {
        "name": "studio.story_readiness.workflow",
        "description": "在实现前检查任务是否具备需求、架构、依赖、验收标准和风险边界。",
        "trigger_intents": ["故事就绪", "story readiness", "实现前检查", "任务准入"],
        "input_schema": {"story": "str", "context": "str", "dependencies": "list"},
        "content_ref": "{{context}}",
        "tags": ["studio_workflow", "readiness"],
        "success_criteria": ["给出 READY / NEEDS_WORK / BLOCKED", "覆盖至少三类维度", "列出阻塞项 owner"],
        "owner_agent_id": "main_text",
        "owner_domain": "general",
        "module_id": "studio_story_readiness",
    },
    {
        "name": "studio.dev_story.workflow",
        "description": "把就绪任务转为可执行实现流程，要求读上下文、改代码、跑验证并记录证据。",
        "trigger_intents": ["开发故事", "dev story", "实现任务", "代码任务执行"],
        "input_schema": {"story": "str", "project_root": "str", "verification_commands": "list"},
        "content_ref": "{{verification_commands}}",
        "tags": ["studio_workflow", "implementation"],
        "success_criteria": ["实现范围清楚", "验证命令已执行或说明原因", "改动和剩余风险可追踪"],
        "owner_agent_id": "programming",
        "owner_domain": "programming",
        "module_id": "studio_dev_story",
    },
    {
        "name": "studio.gate_check.workflow",
        "description": "在阶段推进、发布、回滚或高风险自动化前运行质量门禁，给出 PASS / CONCERNS / FAIL。",
        "trigger_intents": ["质量门禁", "gate check", "阶段审核", "发布检查"],
        "input_schema": {"gate_id": "str", "artifacts": "list", "review_mode": "str"},
        "content_ref": "{{artifacts}}",
        "tags": ["studio_workflow", "quality_gate"],
        "success_criteria": ["使用固定 verdict 词汇", "列出证据", "CONCERNS/FAIL 给出 owner 和修复路径"],
        "owner_agent_id": "external_reviewer",
        "owner_domain": "review",
        "module_id": "studio_gate_check",
    },
    {
        "name": "studio.team_orchestration.workflow",
        "description": "为跨领域任务选择多个 Agent，定义并行/串行顺序、交互包和阻塞上报规则。",
        "trigger_intents": ["团队协作", "team orchestration", "多 Agent 协作", "任务分派"],
        "input_schema": {"request": "str", "candidate_agents": "list", "handoff_policy": "str"},
        "content_ref": "{{candidate_agents}}",
        "tags": ["studio_workflow", "multi_agent", "handoff"],
        "success_criteria": ["列出参与 Agent 与顺序", "标明可并行部分", "任何 BLOCKED 必须立即浮出"],
        "owner_agent_id": "main_text",
        "owner_domain": "general",
        "module_id": "studio_team_orchestration",
    },
)


def seed_studio_workflow_skills() -> dict[str, Any]:
    existing_by_name = {item["name"]: item for item in build_desktop_skills_snapshot().get("skills", [])}
    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    for template in STUDIO_WORKFLOW_SKILL_TEMPLATES:
        name = str(template["name"])
        existing = existing_by_name.get(name, {})
        payload = _template_payload(template, existing)
        result = handle_desktop_skills_action(payload)
        snapshot = dict(result.get("skill") or {})
        if existing:
            updated.append(snapshot)
        else:
            created.append(snapshot)

    return {
        "ok": True,
        "created_count": len(created),
        "updated_count": len(updated),
        "created_skills": created,
        "updated_skills": updated,
        "skills": build_desktop_skills_snapshot(),
    }


def _template_payload(template: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    owner = str(existing.get("owner_agent_id") or template.get("owner_agent_id") or "skill_runner")
    module_id = str(template.get("module_id") or template["name"]).replace(".", "_")
    status = str(existing.get("status") or "candidate")
    promotion_status = str(existing.get("promotion_status") or status)
    return {
        "action": "save",
        "name": template["name"],
        "description": template["description"],
        "status": status,
        "version": str(existing.get("version") or "0.1.0"),
        "risk_level": "medium",
        "confirmation_policy": "always",
        "trigger_intents": template.get("trigger_intents") or [],
        "input_schema": template.get("input_schema") or {},
        "steps": [
            {
                "tool_name": "kb.upsert_draft",
                "arguments": {
                    "title": f"{template['name']}: {{{{request}}}}",
                    "content": template.get("content_ref") or "{{context}}",
                    "tags": template.get("tags") or ["studio_workflow"],
                },
                "description": "保存流程输入、证据和候选结论，等待核心审核。",
                "optional": False,
            }
        ],
        "tool_allowlist": ["kb.upsert_draft"],
        "rollback_strategy": "核心审核未通过则归档候选 Skill；不得自动推进生产阶段。",
        "success_criteria": template.get("success_criteria") or [],
        "eval_cases": [f"{template['name']} 输出固定结构、证据和下一步 owner。"],
        "owner_agent_id": owner,
        "owner_domain": str(existing.get("owner_domain") or template.get("owner_domain") or "general"),
        "workspace_path": str(existing.get("workspace_path") or f"state/agents/{owner}/workspace/studio_workflows/{module_id}"),
        "source_type": "claude_code_game_studios_reference",
        "promotion_status": promotion_status,
        "review_gate": "core_review",
        "metadata": {
            "status": status,
            "promotion_status": promotion_status,
            "module_id": module_id,
            "source_reference": "Donchitos/Claude-Code-Game-Studios",
            "reference_kind": "process_pattern",
            "managed_scope": "agent",
            "requires_human_review": True,
        },
    }
