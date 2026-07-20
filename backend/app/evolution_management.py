from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from backend.app.agent_management import ManagedAgentConfig, load_agent_management_state
from backend.app.knowledge_base_management import import_knowledge_base_files, index_knowledge_base
from backend.app.learning_workflow import (
    ModelProviderConfig,
    build_learning_workflow_report,
    discover_model_providers,
    export_learning_dataset,
    resolve_training_dataset,
)
from backend.app.review_gate import evaluate_review_gate, payload_bool, resolve_review_gate_log_path
from backend.app.settings import (
    resolve_text_api_key,
    resolve_text_base_url,
    resolve_text_generation_profile,
    resolve_text_model,
    resolve_text_provider,
    resolve_vision_api_key,
    resolve_vision_base_url,
    resolve_vision_generation_profile,
    resolve_vision_model,
    resolve_vision_provider,
)
from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action
from backend.evaluation.failure_db import build_failure_sample_db
from backend.evaluation.self_improvement import SelfImprovementLoop
from backend.evaluation.trajectory import TrajectoryAnalyzer, TrajectoryReport, TrajectoryStep
from backend.model.training import (
    build_cloud_training_package,
    build_training_command,
    detect_local_hardware_profile,
    evaluate_dataset_gate,
    export_self_training_dataset,
    load_dataset_registry,
    recommend_training_recipe,
    register_training_dataset,
)
from backend.state_store import read_json_state, resolve_workspace_path, write_json_state

SCHEMA_VERSION = "spiritkin.evolution_management.v1"
DEFAULT_EVOLUTION_STATE = "state/desktop_console/evolution_management.json"
DEFAULT_TRAJECTORY_LOG = "state/evolution/trajectories.jsonl"
DEFAULT_FAILURE_LOG = "state/evolution/failure_samples.jsonl"
DEFAULT_EVOLUTION_EVAL_CASES = "state/evolution/eval_cases.jsonl"
DEFAULT_SELF_TRAINING_DATASET = "state/evolution/self_training_dataset.jsonl"
DEFAULT_LEARNING_ARTIFACT_LOG = "state/evolution/learning_artifacts.jsonl"
DEFAULT_EVOLUTION_JOB_LOG = "state/evolution/jobs.jsonl"

DOMAIN_SKILL_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "module_id": "code_generation",
        "label": "代码生成",
        "owner_agent_id": "programming",
        "domain": "programming",
        "skill_name": "evolution.code_generation.workflow",
        "description": "从需求、论文或失败轨迹中生成代码变更方案，并进入测试与核心审核。",
        "trigger_intents": ("代码生成", "修复代码", "生成脚本", "code generation"),
        "tool_allowlist": ("file.search", "file.read", "kb.upsert_draft"),
        "success_criteria": ("生成变更方案", "列出验证命令", "核心审核通过后再应用"),
    },
    {
        "module_id": "automation_decision",
        "label": "自动化（决策）",
        "owner_agent_id": "main_text",
        "domain": "general",
        "skill_name": "evolution.automation_decision.workflow",
        "description": "将输入拆解为可审核任务路线、执行条件、失败返工和交付标准。",
        "trigger_intents": ("自动化决策", "任务调度", "流程编排", "automation decision"),
        "tool_allowlist": ("kb.upsert_draft",),
        "success_criteria": ("输出执行计划", "明确审核点", "失败可返工"),
    },
    {
        "module_id": "video_generation",
        "label": "视频生成",
        "owner_agent_id": "video_animation",
        "domain": "video_animation",
        "skill_name": "evolution.video_generation.workflow",
        "description": "把需求、视频样例或论文方法整理为脚本、镜头、素材和验证清单。",
        "trigger_intents": ("视频生成", "分镜", "剪辑", "video generation"),
        "tool_allowlist": ("kb.upsert_draft", "file.search", "file.read"),
        "success_criteria": ("生成镜头计划", "列出素材依赖", "进入人工审核"),
    },
    {
        "module_id": "image_generation",
        "label": "图像生成",
        "owner_agent_id": "vision_model",
        "domain": "vision",
        "skill_name": "evolution.image_generation.workflow",
        "description": "将图像需求沉淀为提示词、参考约束、质量检查和审核输出。",
        "trigger_intents": ("图像生成", "图片生成", "视觉素材", "image generation"),
        "tool_allowlist": ("kb.upsert_draft", "file.search", "file.read"),
        "success_criteria": ("生成提示词/规格", "保留参考与限制", "输出待审核资产说明"),
    },
    {
        "module_id": "music_generation",
        "label": "音乐生成",
        "owner_agent_id": "video_animation",
        "domain": "audio",
        "skill_name": "evolution.music_generation.workflow",
        "description": "把音乐需求整理为风格、结构、时长、情绪和审听标准。",
        "trigger_intents": ("音乐生成", "配乐", "音频生成", "music generation"),
        "tool_allowlist": ("kb.upsert_draft",),
        "success_criteria": ("输出音乐规格", "列出版权/风格限制", "进入人工审听"),
    },
    {
        "module_id": "ecommerce_operations",
        "label": "电商运营",
        "owner_agent_id": "ecommerce",
        "domain": "ecommerce",
        "skill_name": "evolution.ecommerce_operations.workflow",
        "description": "将商品、投放、店铺和运营数据沉淀为可审核执行清单。",
        "trigger_intents": ("电商运营", "商品运营", "店铺运营", "ecommerce operations"),
        "tool_allowlist": ("kb.upsert_draft", "browser.search", "file.search", "file.read"),
        "success_criteria": ("生成运营动作", "列出数据依据", "核心审核通过后执行"),
    },
)


def resolve_evolution_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_EVOLUTION_STATE", DEFAULT_EVOLUTION_STATE)
    return resolve_workspace_path(raw)


def resolve_trajectory_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_TRAJECTORY_LOG", DEFAULT_TRAJECTORY_LOG)
    return resolve_workspace_path(raw)


def resolve_failure_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_FAILURE_SAMPLE_LOG", DEFAULT_FAILURE_LOG)
    return resolve_workspace_path(raw)


def resolve_evolution_eval_cases_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_EVOLUTION_EVAL_CASES", DEFAULT_EVOLUTION_EVAL_CASES)
    return resolve_workspace_path(raw)


def resolve_self_training_dataset_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_EVOLUTION_DATASET", DEFAULT_SELF_TRAINING_DATASET)
    return resolve_workspace_path(raw)


def resolve_learning_artifact_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_LEARNING_ARTIFACT_LOG", DEFAULT_LEARNING_ARTIFACT_LOG)
    return resolve_workspace_path(raw)


def resolve_evolution_job_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_EVOLUTION_JOB_LOG", DEFAULT_EVOLUTION_JOB_LOG)
    return resolve_workspace_path(raw)


def _load_json(path: Path, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    return read_json_state(path, fallback)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    write_json_state(path, data)


def _load_trajectory_records(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    target = path or resolve_trajectory_log_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
    except OSError:
        return []
    return records[-max(1, int(limit)) :]


def _load_learning_artifacts(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    target = path or resolve_learning_artifact_log_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
    except OSError:
        return []
    return records[-max(1, int(limit)) :]


def _append_learning_artifact(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or resolve_learning_artifact_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _append_evolution_job(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or resolve_evolution_job_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _append_trajectory_record(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or resolve_trajectory_log_path()
    normalized = {
        "trajectory_id": str(record.get("trajectory_id") or f"traj-{int(time.time() * 1000)}"),
        "created_at": float(record.get("created_at") or time.time()),
        "user_input": str(record.get("user_input") or ""),
        "overall_success": bool(record.get("overall_success", True)),
        "score": float(record.get("score", 0.0) or 0.0),
        "agent_id": str(record.get("agent_id") or ""),
        "domain": str(record.get("domain") or ""),
        "bottleneck_stage": str(record.get("bottleneck_stage") or ""),
        "ci_log": str(record.get("ci_log") or ""),
        "execution_result": str(record.get("execution_result") or ""),
        "steps": [dict(item) for item in record.get("steps") or [] if isinstance(item, dict)],
        "metadata": dict(record.get("metadata") or {}),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    return normalized


def _trajectory_analyzer_from_records(records: list[dict[str, Any]]) -> TrajectoryAnalyzer:
    analyzer = TrajectoryAnalyzer()
    for record in records:
        steps = []
        for step in record.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                TrajectoryStep(
                    stage=str(step.get("stage") or "unknown"),
                    detail=str(step.get("detail") or ""),
                    success=bool(step.get("success", True)),
                    error_code=str(step.get("error_code") or ""),
                    latency_ms=float(step.get("latency_ms") or 0.0),
                    metadata=dict(step.get("metadata") or {}),
                )
            )
        analyzer.record_trajectory(
            TrajectoryReport(
                user_input=str(record.get("user_input") or ""),
                steps=steps,
                overall_success=bool(record.get("overall_success", True)),
                bottleneck_stage=str(record.get("bottleneck_stage") or ""),
            )
        )
    return analyzer


def _trajectory_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    failures = sum(1 for item in records if not bool(item.get("overall_success", True)))
    scored = [float(item.get("score") or 0.0) for item in records]
    high = [item for item in records if float(item.get("score") or 0.0) >= 0.8 and bool(item.get("overall_success", True))]
    return {
        "total": total,
        "failures": failures,
        "success_rate": (total - failures) / max(1, total),
        "scored_count": len([value for value in scored if value > 0]),
        "average_score": sum(scored) / max(1, len(scored)),
        "high_score_count": len(high),
        "latest_high_score": high[-5:],
    }


def _agent_skill_distribution(skills: dict[str, Any], agents: list[dict[str, Any]]) -> dict[str, Any]:
    owner_counts = dict(skills.get("owner_counts") or {})
    missing_owner = int(skills.get("missing_owner_count") or 0)
    rows: list[dict[str, Any]] = []
    for agent in agents:
        agent_id = str(agent.get("agent_id") or "")
        if not agent_id:
            continue
        rows.append(
            {
                "agent_id": agent_id,
                "label": str(agent.get("label") or agent_id),
                "domain": str(agent.get("domain") or ""),
                "enabled": bool(agent.get("enabled", True)),
                "skill_count": int(owner_counts.get(agent_id) or 0),
                "workspace_path": str(agent.get("workspace_path") or f"state/agents/{agent_id}/workspace"),
                "knowledge_base_path": str(agent.get("knowledge_base_path") or ""),
            }
        )
    return {
        "agents": rows,
        "owner_counts": owner_counts,
        "missing_owner_count": missing_owner,
        "isolated": missing_owner == 0,
        "agent_count": len(rows),
    }


def _build_loop_steps(summary: dict[str, Any], quality: dict[str, Any], learning_artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    loop = dict(summary.get("loop") or {})
    counts = dict(summary.get("counts") or {})
    return [
        {
            "step_id": "collect_trajectory",
            "label": "收集任务轨迹",
            "status": "ready" if int(quality.get("total") or 0) else "collecting",
            "detail": f"轨迹 {int(quality.get('total') or 0)} · 失败 {int(quality.get('failures') or 0)}",
        },
        {
            "step_id": "judge_score",
            "label": "裁判模型/规则打分",
            "status": "ready" if int(quality.get("scored_count") or 0) else "needs_attention",
            "detail": f"已评分 {int(quality.get('scored_count') or 0)} · 平均 {float(quality.get('average_score') or 0.0):.2f}",
        },
        {
            "step_id": "select_high_score",
            "label": "筛选高分轨迹",
            "status": "ready" if int(quality.get("high_score_count") or 0) else "collecting",
            "detail": f"高分成功轨迹 {int(quality.get('high_score_count') or 0)}",
        },
        {
            "step_id": "generate_eval",
            "label": "沉淀 Eval / 改进动作",
            "status": "ready" if int(counts.get("eval_cases") or 0) or int(counts.get("improvement_actions") or 0) else "collecting",
            "detail": f"eval {int(counts.get('eval_cases') or 0)} · 改进动作 {int(counts.get('improvement_actions') or 0)}",
        },
        {
            "step_id": "artifact_learning",
            "label": "论文 / 视频转 Skill",
            "status": "ready" if int(learning_artifacts.get("artifact_count") or 0) else "collecting",
            "detail": f"Artifact {int(learning_artifacts.get('artifact_count') or 0)} · 候选 Skill {int(learning_artifacts.get('candidate_skill_count') or 0)}",
        },
        {
            "step_id": "training_schedule",
            "label": "LoRA/云训练调度",
            "status": "manual_review" if bool(loop.get("human_review_required", True)) else "ready",
            "detail": "训练包只生成候选，必须通过核心审核后执行。",
        },
    ]


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": time.time(),
        "review_gate": {
            "core_review_required": True,
            "auto_apply_code": False,
            "auto_promote_skill": False,
            "allow_training_schedule": False,
        },
        "last_action": {},
    }


def build_evolution_management_snapshot() -> dict[str, Any]:
    state = {**_default_state(), **_load_json(resolve_evolution_state_path(), _default_state())}
    records = _load_trajectory_records()
    analyzer = _trajectory_analyzer_from_records(records)
    failure_db = build_failure_sample_db(resolve_failure_log_path())
    improvement = SelfImprovementLoop(trajectory_analyzer=analyzer, failure_db=failure_db).build_report().snapshot()
    eval_cases_path = resolve_evolution_eval_cases_path()
    exported_eval_cases = _eval_case_summary(eval_cases_path)
    dataset_path = resolve_self_training_dataset_path()
    exported_dataset_count = _count_jsonl(dataset_path)
    learning = build_learning_workflow_report(include_improvement=False).snapshot()
    learning_summary = dict(learning.get("self_improvement_summary") or {})
    summary = _merge_summary_with_report(learning_summary, improvement, exported_dataset_count, str(dataset_path))
    quality = _trajectory_quality(records)
    skills = build_desktop_skills_snapshot()
    agents = [agent.snapshot() for agent in load_agent_management_state().agents]
    distribution = _agent_skill_distribution(skills, agents)
    learning_artifacts = _learning_artifact_summary(_load_learning_artifacts(), skills)
    jobs = _evolution_job_summary(_load_learning_artifacts(resolve_evolution_job_log_path()))
    review_audit = _review_gate_audit_summary(_load_learning_artifacts(resolve_review_gate_log_path()))
    hardware = detect_local_hardware_profile()
    recipe = recommend_training_recipe(hardware)
    dataset_for_command = str(dataset_path if dataset_path.exists() else resolve_training_dataset())
    command = build_training_command(
        dataset_path=dataset_for_command,
        output_dir="runs/lora/spiritkin-evolution",
        base_model=os.getenv("SPIRITKIN_EVOLUTION_BASE_MODEL", "Qwen/Qwen2.5-3B-Instruct"),
        recipe=recipe,
    )
    loop_steps = _build_loop_steps(summary, quality, learning_artifacts)
    action_items = _build_action_items(summary, quality, distribution, state, learning_artifacts)
    status = _evolution_status(action_items, distribution)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "status": status,
        "state_path": str(resolve_evolution_state_path()),
        "trajectory_log_path": str(resolve_trajectory_log_path()),
        "failure_log_path": str(resolve_failure_log_path()),
        "eval_cases_path": str(eval_cases_path),
        "dataset_path": str(dataset_path),
        "learning_dataset_path": str(resolve_training_dataset()),
        "learning_artifact_log_path": str(resolve_learning_artifact_log_path()),
        "job_log_path": str(resolve_evolution_job_log_path()),
        "review_gate_log_path": str(resolve_review_gate_log_path()),
        "review_gate": dict(state.get("review_gate") or {}),
        "loop_steps": loop_steps,
        "trajectory": {
            **quality,
            "stats": analyzer.stats(),
            "recent": records[-10:],
        },
        "failure_samples": {
            **failure_db.stats(),
            "recent": failure_db.recent(10),
        },
        "improvement_report": improvement,
        "self_improvement_summary": summary,
        "eval_cases_export": exported_eval_cases,
        "learning_artifacts": learning_artifacts,
        "jobs": jobs,
        "review_gate_audit": review_audit,
        "domain_skill_templates": _domain_skill_template_summary(skills),
        "agent_skill_distribution": distribution,
        "training": {
            "hardware": hardware.__dict__,
            "recipe": recipe.snapshot(),
            "dataset_exported": dataset_path.exists(),
            "dataset_count": exported_dataset_count,
            "training_command": command,
            "training_command_text": " ".join(command),
            "cloud_package_ready": False,
        },
        "capabilities": {
            "trajectory_capture": True,
            "rule_judge": True,
            "eval_case_export": True,
            "model_judge": False,
            "model_extraction": True,
            "lmstudio_openai_compatible": True,
            "skill_ownership_enforcement": True,
            "paper_to_skills": True,
            "video_to_skills": True,
            "domain_skill_templates": True,
            "auto_code_apply": False,
        },
        "action_items": action_items,
        "last_action": dict(state.get("last_action") or {}),
    }


def _merge_summary_with_report(
    learning_summary: dict[str, Any],
    improvement: dict[str, Any],
    dataset_count: int,
    dataset_path: str,
) -> dict[str, Any]:
    actions = [dict(item) for item in improvement.get("actions") or [] if isinstance(item, dict)]
    eval_cases = [dict(item) for item in improvement.get("eval_cases") or [] if isinstance(item, dict)]
    package = improvement.get("training_package") if isinstance(improvement.get("training_package"), dict) else {}
    examples = [dict(item) for item in package.get("examples") or [] if isinstance(item, dict)]
    base_counts = dict(learning_summary.get("counts") or {})
    counts = {
        **base_counts,
        "improvement_actions": len(actions),
        "eval_cases": len(eval_cases),
        "self_training_examples": len(examples),
        "evolution_dataset_examples": dataset_count,
    }
    high_priority = sum(1 for item in actions if str(item.get("priority") or "").lower() == "high")
    signal_count = sum(int(counts.get(key) or 0) for key in ("improvement_actions", "eval_cases", "self_training_examples", "evolution_dataset_examples"))
    status = "needs_attention" if high_priority else ("active" if signal_count else "collecting")
    return {
        **learning_summary,
        "status": status,
        "counts": counts,
        "latest_actions": actions[:8],
        "eval_cases": eval_cases[:8],
        "dataset": {"path": dataset_path, "count": dataset_count},
        "training_package": {
            "package_id": str(package.get("package_id") or ""),
            "purpose": str(package.get("purpose") or ""),
            "example_count": len(examples),
            "generated_at": float(package.get("generated_at") or 0.0),
        },
    }


def _build_action_items(
    summary: dict[str, Any],
    quality: dict[str, Any],
    distribution: dict[str, Any],
    state: dict[str, Any],
    learning_artifacts: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if int(quality.get("total") or 0) == 0:
        actions.append(_action("collect_trajectory", "medium", "开始收集任务轨迹", "先记录输入、执行阶段、结果和 CI/验证日志。"))
    if int(quality.get("scored_count") or 0) == 0:
        actions.append(_action("judge_score", "medium", "补充轨迹评分", "使用简单规则先给成功率、失败阶段和日志质量打分。"))
    if int(distribution.get("missing_owner_count") or 0):
        actions.append(_action("skill_ownership", "high", "补齐 Skill 归属隔离", f"{distribution.get('missing_owner_count')} 个 Skill 缺少 owner_agent_id。"))
    if int((summary.get("counts") or {}).get("self_training_examples") or 0) and not int((summary.get("counts") or {}).get("evolution_dataset_examples") or 0):
        actions.append(_action("training_dataset", "medium", "导出自我训练集", "已有训练样本候选，但尚未导出进化训练集。"))
    missing_templates = [item for item in _domain_skill_template_summary().get("templates") or [] if not bool(item.get("exists"))]
    if missing_templates:
        actions.append(_action("domain_skill_templates", "medium", "生成领域 Skill 模板", f"{len(missing_templates)} 个能力域还没有候选 Skill 模板。"))
    if int(learning_artifacts.get("artifact_count") or 0) and int(learning_artifacts.get("candidate_skill_count") or 0) < int(learning_artifacts.get("artifact_count") or 0):
        actions.append(_action("artifact_candidates", "medium", "审核 Artifact Skill 候选", "部分论文/视频学习记录尚未形成或审核 Skill 候选。"))
    review_gate = dict(state.get("review_gate") or {})
    if not bool(review_gate.get("core_review_required", True)):
        actions.append(_action("review_gate", "high", "恢复核心审核门", "进化输出、Skill 晋升和训练调度应默认需要核心审核。"))
    for item in summary.get("latest_actions") or []:
        if isinstance(item, dict):
            actions.append(
                _action(
                    "improvement_action",
                    str(item.get("priority") or "medium"),
                    str(item.get("title") or "审核改进建议"),
                    str(item.get("detail") or ""),
                    action_id=str(item.get("action_id") or ""),
                )
            )
    return actions[:16]


def _action(category: str, priority: str, title: str, detail: str, *, action_id: str = "") -> dict[str, Any]:
    return {
        "action_id": action_id or f"{category}-{int(time.time() * 1000)}",
        "category": category,
        "priority": priority if priority in {"high", "medium", "low"} else "medium",
        "title": title,
        "detail": detail,
    }


def _evolution_status(actions: list[dict[str, Any]], distribution: dict[str, Any]) -> str:
    if any(str(item.get("priority")) == "high" for item in actions):
        return "blocked" if int(distribution.get("missing_owner_count") or 0) else "needs_attention"
    if actions:
        return "needs_attention"
    return "ready"


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _eval_case_summary(path: Path | None = None) -> dict[str, Any]:
    target = path or resolve_evolution_eval_cases_path()
    by_stage: dict[str, int] = {}
    count = 0
    latest: list[dict[str, Any]] = []
    if target.exists():
        try:
            for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                count += 1
                stage = str(row.get("bottleneck_stage") or "unknown")
                by_stage[stage] = by_stage.get(stage, 0) + 1
                latest.append(row)
        except OSError:
            pass
    return {
        "path": str(target),
        "count": count,
        "by_stage": by_stage,
        "recent": latest[-8:],
    }


def _export_eval_cases(cases: list[dict[str, Any]], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    by_stage: dict[str, int] = {}
    with output_path.open("w", encoding="utf-8") as fh:
        for index, case in enumerate(cases, start=1):
            row = _normalize_eval_case(case, index=index)
            stage = str(row.get("bottleneck_stage") or "unknown")
            by_stage[stage] = by_stage.get(stage, 0) + 1
            exported.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "path": str(output_path),
        "count": len(exported),
        "by_stage": by_stage,
        "cases": exported[:20],
    }


def _normalize_eval_case(case: dict[str, Any], *, index: int) -> dict[str, Any]:
    user_input = str(case.get("user_input") or "").strip()
    bottleneck_stage = str(case.get("bottleneck_stage") or "unknown").strip() or "unknown"
    failed_steps = [dict(item) for item in case.get("failed_steps") or [] if isinstance(item, dict)]
    identity = json.dumps(
        {
            "user_input": user_input,
            "bottleneck_stage": bottleneck_stage,
            "failed_steps": [
                {
                    "stage": str(item.get("stage") or ""),
                    "error_code": str(item.get("error_code") or ""),
                    "detail": str(item.get("detail") or "")[:200],
                }
                for item in failed_steps
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(identity.encode("utf-8", errors="ignore")).hexdigest()[:12]
    expected_behavior = "识别失败阶段，避免重复失败，并给出可验证的修复或澄清步骤。"
    if failed_steps:
        first = failed_steps[0]
        expected_behavior = (
            f"当 {first.get('stage') or bottleneck_stage} 阶段出现 "
            f"{first.get('error_code') or 'unknown'} 时，先解释失败原因，再输出可回放验证步骤。"
        )
    return {
        "schema_version": "spiritkin.evolution_eval_case.v1",
        "eval_case_id": str(case.get("eval_case_id") or f"eval-{digest}"),
        "source": "trajectory",
        "index": index,
        "user_input": user_input,
        "bottleneck_stage": bottleneck_stage,
        "step_count": int(case.get("step_count") or 0),
        "failed_steps": failed_steps,
        "expected_behavior": expected_behavior,
        "metadata": {
            "source_case": dict(case),
            "generated_at": time.time(),
        },
    }


def _int_payload(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _learning_artifact_summary(records: list[dict[str, Any]], skills: dict[str, Any]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    candidate_names: set[str] = set()
    for record in records:
        source_type = str(record.get("artifact_type") or "unknown")
        owner = str(record.get("owner_agent_id") or "")
        by_type[source_type] = by_type.get(source_type, 0) + 1
        if owner:
            by_agent[owner] = by_agent.get(owner, 0) + 1
        skill = dict(record.get("skill_candidate") or {})
        name = str(skill.get("name") or "")
        if name:
            candidate_names.add(name)
    stored_names = {str(item.get("name") or "") for item in skills.get("skills") or [] if isinstance(item, dict)}
    return {
        "artifact_count": len(records),
        "by_type": by_type,
        "by_agent": by_agent,
        "candidate_skill_count": len(candidate_names),
        "stored_candidate_skill_count": len(candidate_names & stored_names),
        "recent": records[-8:],
    }


def _evolution_job_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        job_id = str(record.get("job_id") or "")
        if job_id:
            latest_by_id[job_id] = record
    latest = list(latest_by_id.values())
    by_status: dict[str, int] = {}
    for record in latest:
        status = str(record.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    failed = [record for record in latest if str(record.get("status") or "") == "failed"]
    return {
        "job_count": len(latest),
        "by_status": by_status,
        "failed_count": len(failed),
        "recent": latest[-8:],
        "failed_recent": failed[-5:],
    }


def _review_gate_audit_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_gate: dict[str, int] = {}
    denied = 0
    allowed = 0
    for record in records:
        gate_id = str(record.get("gate_id") or "unknown")
        by_gate[gate_id] = by_gate.get(gate_id, 0) + 1
        if bool(record.get("allowed", False)):
            allowed += 1
        else:
            denied += 1
    return {
        "record_count": len(records),
        "allowed_count": allowed,
        "denied_count": denied,
        "by_gate": by_gate,
        "recent": records[-8:],
    }


def _domain_skill_template_summary(skills: dict[str, Any] | None = None) -> dict[str, Any]:
    if skills is None:
        skills = build_desktop_skills_snapshot()
    names = {str(item.get("name") or "") for item in skills.get("skills") or [] if isinstance(item, dict)}
    templates = [
        {
            "module_id": str(item.get("module_id") or ""),
            "label": str(item.get("label") or ""),
            "owner_agent_id": str(item.get("owner_agent_id") or ""),
            "skill_name": str(item.get("skill_name") or ""),
            "exists": str(item.get("skill_name") or "") in names,
        }
        for item in DOMAIN_SKILL_TEMPLATES
    ]
    return {
        "templates": templates,
        "count": len(templates),
        "existing_count": sum(1 for item in templates if bool(item.get("exists"))),
        "missing_count": sum(1 for item in templates if not bool(item.get("exists"))),
    }


def _agent_by_id(agent_id: str) -> ManagedAgentConfig:
    state = load_agent_management_state()
    match = next((agent for agent in state.agents if agent.agent_id == agent_id), None)
    if match is not None:
        return match
    fallback = next((agent for agent in state.agents if agent.agent_id == "skill_runner"), None)
    if fallback is not None:
        return fallback
    if state.agents:
        return state.agents[0]
    raise ValueError("no managed agents configured")


def _select_owner_agent_id(payload: dict[str, Any], artifact_type: str) -> str:
    explicit = str(payload.get("owner_agent_id") or payload.get("agent_id") or "").strip()
    if explicit:
        return explicit
    domain = str(payload.get("domain") or "").lower()
    text = " ".join(str(payload.get(key) or "").lower() for key in ("title", "summary", "content", "method"))
    if artifact_type == "paper":
        if any(hint in text or hint in domain for hint in ("code", "代码", "programming", "agent", "算法", "framework")):
            return "programming"
        if any(hint in text or hint in domain for hint in ("video", "动画", "timeline")):
            return "video_animation"
        if any(hint in text or hint in domain for hint in ("image", "vision", "视觉", "图像")):
            return "vision_model"
        return "programming"
    if artifact_type == "video":
        if any(hint in text or hint in domain for hint in ("电商", "shop", "sku", "listing", "commerce")):
            return "ecommerce"
        return "video_animation"
    return "skill_runner"


def _safe_slug(value: str, fallback: str) -> str:
    raw = value.strip().lower()
    raw = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", "-", raw)
    raw = raw.strip("-._")
    return (raw[:80] or fallback).strip("-._") or fallback


def _model_provider_snapshot(provider: ModelProviderConfig | None) -> dict[str, Any]:
    if provider is None:
        return {"configured": False}
    return {
        "provider": provider.provider,
        "model": provider.model,
        "endpoint": provider.endpoint,
        "display_name": provider.display_name or provider.provider,
        "configured": provider.configured,
        "source": provider.source,
    }


def _select_text_provider(payload: dict[str, Any]) -> ModelProviderConfig:
    requested_provider = str(payload.get("provider") or payload.get("model_provider") or "").strip().lower()
    requested_model = str(payload.get("model") or payload.get("text_model") or "").strip()
    providers = discover_model_providers()
    candidates = [item for item in providers if item.configured]
    if requested_provider:
        match = next((item for item in candidates if item.provider.lower() == requested_provider), None)
        if match is not None:
            return ModelProviderConfig(match.provider, requested_model or match.model, match.configured, match.endpoint, match.env_key, match.display_name, match.source, match.api_key)
    configured_provider = resolve_text_provider().strip().lower()
    preferred = next((item for item in candidates if item.provider.lower() == configured_provider), None)
    if preferred is None:
        preferred = next((item for item in candidates if item.provider.lower() in {"llamacpp", "llama_cpp", "llama.cpp", "llama-cpp"}), None)
    if preferred is not None:
        return ModelProviderConfig(preferred.provider, requested_model or preferred.model, preferred.configured, preferred.endpoint, preferred.env_key, preferred.display_name, preferred.source, preferred.api_key)
    provider = resolve_text_provider(str(payload.get("provider") or "") or None)
    endpoint = str(payload.get("base_url") or payload.get("text_base_url") or resolve_text_base_url()).rstrip("/")
    model = requested_model or resolve_text_model()
    if provider == "openai_compatible" and ("1234" in endpoint or "lmstudio" in endpoint.lower()):
        provider = "lmstudio"
    api_key = str(payload.get("api_key") or payload.get("text_api_key") or resolve_text_api_key())
    return ModelProviderConfig(provider, model, bool(endpoint and model), endpoint, "SPIRIT_TEXT_PROVIDER", display_name=provider, source="text_config", api_key=api_key)


def _select_vision_provider(payload: dict[str, Any]) -> ModelProviderConfig:
    requested_model = str(payload.get("vision_model") or payload.get("model") or "").strip()
    requested_provider = str(payload.get("vision_provider") or payload.get("provider") or "").strip().lower()
    providers = [item for item in discover_model_providers() if item.configured]
    configured_provider = resolve_vision_provider().strip().lower()
    preferred = next((item for item in providers if item.provider.lower() == configured_provider), None)
    if preferred is None:
        preferred = next(
            (item for item in providers if item.provider.lower() in {"llamacpp", "llama_cpp", "llama.cpp", "llama-cpp"}),
            None,
        )
    if requested_provider:
        preferred = next((item for item in providers if item.provider.lower() == requested_provider), preferred)
    if preferred is not None:
        return ModelProviderConfig(preferred.provider, requested_model or preferred.model, preferred.configured, preferred.endpoint, preferred.env_key, preferred.display_name, preferred.source, preferred.api_key)
    provider = str(payload.get("vision_provider") or resolve_vision_provider()).strip() or "openai_compatible"
    text_endpoint = resolve_text_base_url()
    endpoint = str(
        payload.get("vision_base_url")
        or payload.get("base_url")
        or os.getenv("SPIRITKIN_EVOLUTION_VISION_BASE_URL")
        or os.getenv("LLAMACPP_BASE_URL")
        or text_endpoint
        or resolve_vision_base_url()
    ).rstrip("/")
    model = requested_model or os.getenv("SPIRITKIN_LLAMACPP_MODEL") or resolve_vision_model()
    api_key = str(payload.get("vision_api_key") or payload.get("api_key") or resolve_vision_api_key())
    if endpoint and "1234" in endpoint and api_key in {"", "ollama"}:
        api_key = "lm-studio"
    return ModelProviderConfig(provider, model, bool(endpoint and model), endpoint, "SPIRIT_VISION_PROVIDER", display_name=provider, source="vision_config", api_key=api_key)


def _model_extraction_enabled(payload: dict[str, Any]) -> bool:
    if payload_bool(payload.get("disable_model_extraction"), False):
        return False
    return payload_bool(os.getenv("SPIRITKIN_EVOLUTION_MODEL_EXTRACTION"), True)


def _json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", stripped)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _post_openai_compatible_chat(
    provider: ModelProviderConfig,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1200,
    timeout: float = 45.0,
) -> str:
    if not provider.endpoint or not provider.model:
        raise ValueError("model provider endpoint/model is not configured")
    payload = {
        "model": provider.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(f"{provider.endpoint.rstrip('/')}/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if provider.api_key:
        req.add_header("Authorization", f"Bearer {provider.api_key}")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def _extract_paper_with_model(payload: dict[str, Any], title: str) -> dict[str, Any]:
    source_text = str(payload.get("paper_text") or payload.get("content") or payload.get("summary") or payload.get("abstract") or payload.get("method") or "").strip()
    if not source_text or not _model_extraction_enabled(payload):
        return {"ok": False, "status": "skipped", "reason": "no paper text"}
    provider = _select_text_provider(payload)
    profile = resolve_text_generation_profile("strong")
    prompt = (
        "请把下面论文/方法内容提炼成 SpiritKinAI 可用的结构化学习结果。"
        "只输出 JSON，字段必须包含 summary、extracted_actions、skill_description、eval_cases。"
        "extracted_actions 是 3-8 个英文或中文短动作；不要输出 Markdown。\n\n"
        f"标题：{title}\n\n内容：\n{source_text[:12000]}"
    )
    try:
        response = _post_openai_compatible_chat(
            provider,
            [
                {"role": "system", "content": "You convert papers into structured agent skills. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=float(profile.get("temperature") or 0.0),
            max_tokens=int(profile.get("max_new_tokens") or 1200),
            timeout=float(payload.get("model_timeout") or 45.0),
        )
        parsed = _json_object_from_text(response)
        if not parsed:
            raise ValueError("model did not return a JSON object")
        actions = _extract_list(parsed.get("extracted_actions") or parsed.get("actions") or parsed.get("steps"))
        eval_cases = _extract_list(parsed.get("eval_cases") or parsed.get("evaluations"))
        return {
            "ok": True,
            "status": "ok",
            "provider": _model_provider_snapshot(provider),
            "summary": str(parsed.get("summary") or "").strip(),
            "extracted_actions": actions[:12],
            "skill_description": str(parsed.get("skill_description") or parsed.get("description") or "").strip(),
            "eval_cases": eval_cases[:8],
            "raw_response": response[:4000],
        }
    except Exception as exc:
        return {"ok": False, "status": "failed", "provider": _model_provider_snapshot(provider), "error": f"{type(exc).__name__}: {exc}"}


def _extract_video_with_model(payload: dict[str, Any], title: str) -> dict[str, Any]:
    image_values = [str(item).strip() for item in (payload.get("frames") or payload.get("frame_paths") or payload.get("screenshots") or []) if str(item).strip()] if isinstance(payload.get("frames") or payload.get("frame_paths") or payload.get("screenshots"), list) else []
    single_image = str(payload.get("image") or payload.get("image_url") or payload.get("screenshot") or payload.get("screenshot_path") or "").strip()
    if single_image:
        image_values.insert(0, single_image)
    if not image_values or not _model_extraction_enabled(payload):
        return {"ok": False, "status": "skipped", "reason": "no video frames"}
    provider = _select_vision_provider(payload)
    profile = resolve_vision_generation_profile("detailed")
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请从这些视频帧/截图中提取可自动化的 UI 操作序列。只输出 JSON，字段包含 summary 和 operation_sequence。"
                "operation_sequence 是数组，每项包含 action、target、value、confidence、notes。"
                "不要猜测高风险操作；无法确定 target 时写空字符串。"
            ),
        }
    ]
    for image in image_values[:6]:
        url = image if image.startswith(("data:", "http://", "https://")) else _image_path_to_data_url(image)
        content.append({"type": "image_url", "image_url": {"url": url}})
    try:
        response = _post_openai_compatible_chat(
            provider,
            [
                {"role": "system", "content": "You extract UI automation steps from screenshots/video frames. Return valid JSON only."},
                {"role": "user", "content": content},
            ],
            temperature=float(profile.get("temperature") or 0.0),
            max_tokens=int(profile.get("max_tokens") or 800),
            timeout=float(payload.get("model_timeout") or 45.0),
        )
        parsed = _json_object_from_text(response)
        if not parsed:
            raise ValueError("model did not return a JSON object")
        steps = _normalize_video_steps(parsed.get("operation_sequence") or parsed.get("steps") or parsed.get("actions"))
        actions = [
            " / ".join(part for part in (str(step.get("action") or ""), str(step.get("target") or ""), str(step.get("value") or "")) if part)
            for step in steps
        ]
        return {
            "ok": True,
            "status": "ok",
            "provider": _model_provider_snapshot(provider),
            "summary": str(parsed.get("summary") or f"{title} 的视频操作学习记录。").strip(),
            "operation_sequence": steps[:20],
            "extracted_actions": actions[:20],
            "raw_response": response[:4000],
        }
    except Exception as exc:
        return {"ok": False, "status": "failed", "provider": _model_provider_snapshot(provider), "error": f"{type(exc).__name__}: {exc}"}


def _image_path_to_data_url(value: str) -> str:
    import base64
    import mimetypes

    path = Path(value)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _normalize_video_steps(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [{"action": item} for item in _extract_list(value)]
    if not isinstance(value, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            steps.append(
                {
                    "action": str(item.get("action") or item.get("type") or item.get("operation") or "observe").strip() or "observe",
                    "target": str(item.get("target") or item.get("selector") or item.get("element") or "").strip(),
                    "value": str(item.get("value") or item.get("text") or "").strip(),
                    "confidence": float(item.get("confidence") or 0.0),
                    "notes": str(item.get("notes") or item.get("reason") or "").strip(),
                    "raw": dict(item),
                }
            )
        elif str(item).strip():
            steps.append({"action": str(item).strip(), "target": "", "value": "", "confidence": 0.0, "notes": "", "raw": {"text": str(item)}})
    return steps


def _workspace_for_agent(agent_id: str, *parts: str) -> Path:
    safe = _safe_slug(agent_id, "skill_runner")
    root = resolve_workspace_path(Path("state") / "agents" / safe / "workspace")
    target = root.joinpath(*[_safe_slug(part, "item") for part in parts if str(part).strip()]).resolve()
    if target != root and root not in target.parents:
        raise ValueError("artifact workspace must stay under owner agent workspace")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _knowledge_base_for_agent(agent: ManagedAgentConfig) -> tuple[str, str]:
    kb_id = agent.knowledge_base_id or f"kb_{agent.agent_id}"
    path = agent.knowledge_base_path or f"state/knowledge_bases/agents/{agent.agent_id}"
    return kb_id, path


def _artifact_markdown(record: dict[str, Any]) -> str:
    actions = "\n".join(f"- {item}" for item in record.get("extracted_actions") or [])
    skill = dict(record.get("skill_candidate") or {})
    return "\n".join(
        [
            f"# {record.get('title') or record.get('artifact_id')}",
            "",
            f"- Type: {record.get('artifact_type')}",
            f"- Owner Agent: {record.get('owner_agent_id')}",
            f"- Source: {record.get('source')}",
            f"- Skill Candidate: {skill.get('name', '')}",
            "",
            "## Summary",
            str(record.get("summary") or ""),
            "",
            "## Extracted Actions",
            actions or "- 未提取到动作",
            "",
            "## Review",
            "核心模块审核通过前，该记录只能作为候选 Skill / 知识条目使用。",
        ]
    )


def _extract_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.replace("；", "\n").replace(";", "\n").splitlines() if line.strip(" -\t")]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _extract_steps_from_video(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("operation_sequence") or payload.get("steps") or payload.get("actions")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [{"action": item} for item in _extract_list(raw)]
    if not isinstance(raw, list):
        raw = []
    steps: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            action = str(item.get("action") or item.get("type") or item.get("operation") or "").strip()
            target = str(item.get("target") or item.get("selector") or item.get("element") or "").strip()
            value = str(item.get("value") or item.get("text") or "").strip()
            steps.append({"action": action or "observe", "target": target, "value": value, "raw": dict(item)})
        elif str(item).strip():
            steps.append({"action": str(item).strip(), "target": "", "value": "", "raw": {"text": str(item)}})
    return steps


def _skill_steps_for_artifact(artifact_type: str, extracted_steps: list[dict[str, Any]], summary: str) -> list[dict[str, Any]]:
    if artifact_type == "paper":
        return [
            {
                "tool_name": "kb.upsert_draft",
                "arguments": {
                    "title": "{{artifact_title}}",
                    "content": "{{method_summary}}",
                    "tags": ["paper_to_skill", "candidate"],
                },
                "description": "把论文方法写入待审核知识草稿。",
                "optional": False,
            }
        ]
    skill_steps: list[dict[str, Any]] = []
    for step in extracted_steps[:8]:
        action = str(step.get("action") or "").lower()
        target = str(step.get("target") or "")
        value = str(step.get("value") or "")
        if any(word in action for word in ("click", "点击", "tap")):
            binding = f"待绑定点击元素: {target or action}"
        elif any(word in action for word in ("input", "输入", "type")):
            binding = f"待绑定输入元素: {target or value or action}"
        elif any(word in action for word in ("hotkey", "key", "快捷键", "按键")):
            binding = f"待绑定按键动作: {value or target or action}"
        else:
            binding = f"待确认视频动作: {action or target or value}"
        skill_steps.append(
            {
                "tool_name": "kb.upsert_draft",
                "arguments": {
                    "title": "{{artifact_title}}",
                    "content": f"{summary}\n\n{binding}",
                    "tags": ["video_to_skill", "ui_binding_required"],
                },
                "description": binding,
                "optional": False,
            }
        )
    if not skill_steps:
        skill_steps.append({"tool_name": "kb.upsert_draft", "arguments": {"title": "{{artifact_title}}", "content": summary, "tags": ["video_to_skill", "ui_binding_required"]}, "description": "记录待绑定视频 Skill。", "optional": False})
    return skill_steps[:10]


def _candidate_skill_from_artifact(record: dict[str, Any], *, extracted_steps: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_type = str(record.get("artifact_type") or "artifact")
    owner = str(record.get("owner_agent_id") or "skill_runner")
    slug = _safe_slug(str(record.get("title") or record.get("artifact_id") or artifact_type), f"{artifact_type}-{int(time.time())}")
    name = str(record.get("skill_name") or f"artifact.{artifact_type}.{owner}.{slug}").strip()
    summary = str(record.get("summary") or "")
    model_extraction = dict(record.get("model_extraction") or {})
    eval_cases = _extract_list(record.get("eval_cases")) or [f"审核 {record.get('title') or artifact_type} 生成的 Skill 候选"]
    tool_allowlist = ["kb.upsert_draft"]
    if artifact_type == "video":
        tool_allowlist = ["kb.upsert_draft"]
    return {
        "action": "save",
        "name": name,
        "description": str(record.get("skill_description") or f"{record.get('title')} 生成的待审核 Skill 候选。"),
        "status": "candidate",
        "owner_agent_id": owner,
        "workspace_path": str(record.get("workspace_path") or f"state/agents/{owner}/workspace/artifacts/{artifact_type}/{slug}"),
        "source_type": "paper_to_skill" if artifact_type == "paper" else "video_to_skill",
        "review_gate": "core_review",
        "trigger_intents": [str(record.get("title") or artifact_type), artifact_type, "evolution_learning"],
        "input_schema": {"artifact_title": "str", "method_summary": "str", "screenshot_path": "str", "input_text": "str"},
        "steps": _skill_steps_for_artifact(artifact_type, extracted_steps, summary),
        "tool_allowlist": tool_allowlist,
        "risk_level": "medium" if artifact_type == "video" else "low",
        "confirmation_policy": "always" if artifact_type == "video" else "risk_based",
        "rollback_strategy": "核心审核拒绝则归档候选 Skill；视频 Skill 未绑定 UI 元素前不得执行。",
        "success_criteria": ["候选 Skill 通过人工审核", "有知识记录和最小 eval", "视频 Skill 完成 UI 元素绑定后才能执行"],
        "eval_cases": eval_cases[:8],
        "metadata": {
            "artifact_id": str(record.get("artifact_id") or ""),
            "artifact_type": artifact_type,
            "source": str(record.get("source") or ""),
            "status": "candidate",
            "promotion_status": "candidate",
            "ui_binding_status": "required" if artifact_type == "video" else "not_required",
            "model_extraction_status": str(model_extraction.get("status") or "skipped"),
            "model_extraction_provider": dict(model_extraction.get("provider") or {}),
            "total_count": 0,
            "success_count": 0,
            "success_rate": 0.0,
        },
    }


def _store_artifact_record(payload: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    owner_id = _select_owner_agent_id(payload, artifact_type)
    agent = _agent_by_id(owner_id)
    title = str(payload.get("title") or payload.get("paper_title") or payload.get("video_title") or f"{artifact_type}-{int(time.time())}").strip()
    slug = _safe_slug(title, f"{artifact_type}-{int(time.time())}")
    job_id = str(payload.get("job_id") or f"artifact-{artifact_type}-{int(time.time() * 1000)}")

    def mark(status: str, stage: str, **extra: Any) -> None:
        _append_evolution_job(
            {
                "job_id": job_id,
                "artifact_type": artifact_type,
                "title": title,
                "owner_agent_id": agent.agent_id,
                "status": status,
                "stage": stage,
                "updated_at": time.time(),
                **extra,
            }
        )

    mark("running", "created")
    workspace = _workspace_for_agent(agent.agent_id, "artifacts", artifact_type, slug)
    relative_workspace = f"state/agents/{agent.agent_id}/workspace/artifacts/{artifact_type}/{slug}"
    try:
        model_extraction = _extract_video_with_model(payload, title) if artifact_type == "video" else _extract_paper_with_model(payload, title)
        extracted_steps = _normalize_video_steps(model_extraction.get("operation_sequence")) if artifact_type == "video" and model_extraction.get("ok") else (_extract_steps_from_video(payload) if artifact_type == "video" else [])
        extracted_actions = _extract_list(payload.get("extracted_actions") or payload.get("methods") or payload.get("action_sequence"))
        if model_extraction.get("ok") and not extracted_actions:
            extracted_actions = _extract_list(model_extraction.get("extracted_actions"))
        if artifact_type == "video" and not extracted_actions:
            extracted_actions = [
                " / ".join(part for part in (str(step.get("action") or ""), str(step.get("target") or ""), str(step.get("value") or "")) if part)
                for step in extracted_steps
            ]
        summary = str(payload.get("summary") or payload.get("method") or payload.get("content") or "").strip()
        if model_extraction.get("ok") and str(model_extraction.get("summary") or "").strip():
            summary = str(model_extraction.get("summary") or "").strip()
        if not summary:
            summary = f"{title} 的{('论文方法' if artifact_type == 'paper' else '视频操作')}学习记录。"
        if artifact_type == "paper" and not extracted_actions:
            extracted_actions = _extract_list(summary)[:8]

        record = {
            "job_id": job_id,
            "artifact_id": str(payload.get("artifact_id") or job_id),
            "artifact_type": artifact_type,
            "title": title,
            "source": str(payload.get("source") or payload.get("url") or payload.get("path") or ""),
            "owner_agent_id": agent.agent_id,
            "owner_domain": agent.domain,
            "workspace_path": relative_workspace,
            "workspace_resolved_path": str(workspace),
            "knowledge_base_id": agent.knowledge_base_id or f"kb_{agent.agent_id}",
            "knowledge_base_path": agent.knowledge_base_path or f"state/knowledge_bases/agents/{agent.agent_id}",
            "summary": summary,
            "extracted_actions": extracted_actions[:20],
            "operation_sequence": extracted_steps[:20],
            "model_extraction": model_extraction,
            "review_status": "pending_core_review",
            "created_at": time.time(),
            "metadata": dict(payload.get("metadata") or {}),
        }
        if model_extraction.get("ok"):
            if str(model_extraction.get("skill_description") or "").strip():
                record["skill_description"] = str(model_extraction.get("skill_description") or "").strip()
            model_eval_cases = _extract_list(model_extraction.get("eval_cases"))
            if model_eval_cases:
                record["eval_cases"] = model_eval_cases[:8]
        candidate = _candidate_skill_from_artifact(record, extracted_steps=extracted_steps)
        record["skill_candidate"] = {
            "name": candidate["name"],
            "status": "candidate",
            "owner_agent_id": agent.agent_id,
            "source_type": candidate["source_type"],
        }

        note = _artifact_markdown(record)
        (workspace / "artifact.md").write_text(note, encoding="utf-8")
        (workspace / "skill_candidate.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        mark("running", "artifact_written", workspace_path=relative_workspace)
        kb_id, kb_path = _knowledge_base_for_agent(agent)
        import_knowledge_base_files(kb_path, [{"path": f"evolution/{artifact_type}/{slug}.md", "text": note}])
        index_report = index_knowledge_base(kb_id, kb_path).snapshot()
        mark("running", "kb_indexed", knowledge_base_id=kb_id, knowledge_base_path=kb_path)
        saved = handle_desktop_skills_action(candidate)
        mark("running", "skill_candidate_saved", skill_name=str(saved.get("skill", {}).get("name") or candidate["name"]))
        record["knowledge_index"] = index_report
        record["skill_candidate"] = saved.get("skill", record["skill_candidate"])
        _append_learning_artifact(record)
        mark("pending_review", "pending_core_review", artifact_id=record["artifact_id"], skill_name=str(record["skill_candidate"].get("name") or ""))
        return record
    except Exception as exc:
        mark("failed", "failed", error=f"{type(exc).__name__}: {exc}")
        raise


def _seed_domain_skill_templates() -> dict[str, Any]:
    seeded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    skills = build_desktop_skills_snapshot()
    existing = {str(item.get("name") or "") for item in skills.get("skills") or [] if isinstance(item, dict)}
    for item in DOMAIN_SKILL_TEMPLATES:
        name = str(item.get("skill_name") or "")
        owner = str(item.get("owner_agent_id") or "skill_runner")
        if name in existing:
            skipped.append({"skill_name": name, "reason": "exists"})
            continue
        payload = {
            "action": "save",
            "name": name,
            "description": str(item.get("description") or ""),
            "status": "candidate",
            "owner_agent_id": owner,
            "workspace_path": f"state/agents/{owner}/workspace/domain_skills/{item.get('module_id')}",
            "source_type": "domain_template",
            "review_gate": "core_review",
            "trigger_intents": list(item.get("trigger_intents") or []),
            "input_schema": {"request": "str", "context": "str", "review_notes": "str"},
            "steps": [
                {
                    "tool_name": "kb.upsert_draft",
                    "arguments": {
                        "title": "{{request}}",
                        "content": "{{context}}",
                        "tags": ["domain_skill", str(item.get("module_id") or "")],
                    },
                    "description": "先把执行方案写入待审核知识草稿。",
                    "optional": False,
                }
            ],
            "tool_allowlist": list(item.get("tool_allowlist") or ("kb.upsert_draft",)),
            "risk_level": "medium",
            "confirmation_policy": "always",
            "rollback_strategy": "核心审核未通过则归档候选 Skill。",
            "success_criteria": list(item.get("success_criteria") or []),
            "eval_cases": [f"{item.get('label')} 候选 Skill 生成后进入核心审核"],
            "metadata": {
                "status": "candidate",
                "promotion_status": "candidate",
                "module_id": str(item.get("module_id") or ""),
                "domain_template": True,
            },
        }
        result = handle_desktop_skills_action(payload)
        seeded.append(result.get("skill", {"name": name}))
    return {"seeded_count": len(seeded), "skipped_count": len(skipped), "seeded": seeded, "skipped": skipped}


def handle_evolution_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh"}:
        return {"ok": True, "evolution": build_evolution_management_snapshot()}
    if action == "record_trajectory":
        record = _append_trajectory_record(payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else payload)
        return _with_last_action({"ok": True, "trajectory_record": record}, "record_trajectory")
    if action == "export_eval_cases":
        snapshot = build_evolution_management_snapshot()
        cases = [dict(item) for item in (snapshot.get("improvement_report") or {}).get("eval_cases") or [] if isinstance(item, dict)]
        export = _export_eval_cases(cases, resolve_evolution_eval_cases_path(payload.get("output_path")))
        return _with_last_action({"ok": True, "eval_cases": export, "evolution": build_evolution_management_snapshot()}, action)
    if action == "export_self_training_dataset":
        snapshot = build_evolution_management_snapshot()
        package = snapshot["improvement_report"].get("training_package") or {}
        export = export_self_training_dataset(package, resolve_self_training_dataset_path(payload.get("output_path")))
        gate = evaluate_dataset_gate(
            export.path,
            min_examples=_int_payload(payload, "min_examples", 1),
            allow_secrets=bool(payload.get("allow_secrets", False)),
            allow_high_risk=bool(payload.get("allow_high_risk_training_samples", False)),
        )
        dataset_card = register_training_dataset(
            export.path,
            source="evolution_self_training",
            source_counts={
                "examples": int(getattr(export, "example_count", export.count)),
                "trajectories": int((package.get("source_counts") or {}).get("trajectories") or 0),
                "failure_samples": int((package.get("source_counts") or {}).get("failure_samples") or 0),
            },
            base_model_target=str(payload.get("base_model") or os.getenv("SPIRITKIN_EVOLUTION_BASE_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")),
            reviewer=str(payload.get("reviewer") or ""),
            linked_eval_report=str(resolve_evolution_eval_cases_path()),
            metadata={"package_source": "evolution_management", "artifact_count": int(getattr(export, "example_count", export.count))},
            gate=gate,
        )
        return _with_last_action(
            {
                "ok": True,
                "dataset": export.snapshot(),
                "dataset_gate": gate.snapshot(),
                "dataset_card": dataset_card.snapshot(),
                "dataset_registry": load_dataset_registry(limit=20),
                "evolution": build_evolution_management_snapshot(),
            },
            action,
        )
    if action == "export_learning_dataset":
        export = export_learning_dataset(output_path=payload.get("output_path"))
        return _with_last_action({"ok": True, "dataset": export.get("export", {}), "package": export.get("package", {}), "evolution": build_evolution_management_snapshot()}, action)
    if action == "build_cloud_training_package":
        decision = evaluate_review_gate(payload, "evolution.cloud_training_package", subject=str(payload.get("dataset_path") or resolve_self_training_dataset_path()))
        if not decision.allowed:
            return {"ok": False, "error": "review_required", "review_gate": decision.snapshot(), "evolution": build_evolution_management_snapshot()}
        dataset_path = str(payload.get("dataset_path") or resolve_self_training_dataset_path()).strip()
        if not Path(dataset_path).exists():
            snapshot = build_evolution_management_snapshot()
            package = snapshot["improvement_report"].get("training_package") or {}
            export_self_training_dataset(package, resolve_self_training_dataset_path())
            dataset_path = str(resolve_self_training_dataset_path())
        dataset_gate = evaluate_dataset_gate(
            dataset_path,
            min_examples=_int_payload(payload, "min_examples", 1),
            allow_secrets=bool(payload.get("allow_secrets", False)),
            allow_high_risk=bool(payload.get("allow_high_risk_training_samples", False)),
        )
        if not dataset_gate.allowed:
            return {
                "ok": False,
                "error": "dataset_gate_failed",
                "dataset_gate": dataset_gate.snapshot(),
                "review_gate": decision.snapshot(),
                "evolution": build_evolution_management_snapshot(),
            }
        package = build_cloud_training_package(
            dataset_path=dataset_path,
            base_model=str(payload.get("base_model") or os.getenv("SPIRITKIN_EVOLUTION_BASE_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")),
            package_id=str(payload.get("package_id") or f"evolution-train-{int(time.time())}"),
            adapter_output_dir=str(payload.get("adapter_output_dir") or "outputs/spiritkin-evolution-lora"),
            notes=str(payload.get("notes") or "Generated by desktop evolution management. Core review required before training."),
        )
        return _with_last_action({"ok": True, "cloud_training_package": package.snapshot(), "review_gate": decision.snapshot(), "dataset_gate": dataset_gate.snapshot(), "evolution": build_evolution_management_snapshot()}, action)
    if action == "enforce_skill_ownership":
        result = handle_desktop_skills_action({"action": "enforce_ownership"})
        return _with_last_action({"ok": True, "skill_ownership": result, "evolution": build_evolution_management_snapshot()}, action)
    if action in {"ingest_paper", "paper_to_skill"}:
        record = _store_artifact_record(payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload, "paper")
        return _with_last_action({"ok": True, "learning_artifact": record, "evolution": build_evolution_management_snapshot()}, action)
    if action in {"ingest_video", "video_to_skill"}:
        record = _store_artifact_record(payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload, "video")
        return _with_last_action({"ok": True, "learning_artifact": record, "evolution": build_evolution_management_snapshot()}, action)
    if action == "retry_artifact_job":
        retry_payload = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else dict(payload)
        job_id = str(payload.get("job_id") or retry_payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        retry_payload["job_id"] = job_id
        artifact_type = str(payload.get("artifact_type") or retry_payload.get("artifact_type") or "paper").strip().lower()
        if artifact_type not in {"paper", "video"}:
            raise ValueError("artifact_type must be paper or video")
        record = _store_artifact_record(retry_payload, artifact_type)
        return _with_last_action({"ok": True, "learning_artifact": record, "evolution": build_evolution_management_snapshot()}, action)
    if action in {"seed_domain_skill_templates", "seed_module_skills"}:
        result = _seed_domain_skill_templates()
        return _with_last_action({"ok": True, "domain_skill_templates": result, "evolution": build_evolution_management_snapshot()}, action)
    if action == "save_review_gate":
        state = {**_default_state(), **_load_json(resolve_evolution_state_path(), _default_state())}
        incoming = payload.get("review_gate") if isinstance(payload.get("review_gate"), dict) else payload
        core_review_required = payload_bool(incoming.get("core_review_required"), True)
        gate = {
            **dict(state.get("review_gate") or {}),
            "core_review_required": core_review_required,
            "auto_apply_code": False,
            "auto_promote_skill": payload_bool(incoming.get("auto_promote_skill"), False) and not core_review_required,
            "allow_training_schedule": payload_bool(incoming.get("allow_training_schedule"), False),
        }
        state["review_gate"] = gate
        state["updated_at"] = time.time()
        _save_json(resolve_evolution_state_path(), state)
        return _with_last_action({"ok": True, "review_gate": gate, "evolution": build_evolution_management_snapshot()}, action)
    raise ValueError(f"unsupported evolution action: {action}")


def _with_last_action(payload: dict[str, Any], action: str) -> dict[str, Any]:
    state_path = resolve_evolution_state_path()
    state = {**_default_state(), **_load_json(state_path, _default_state())}
    state["last_action"] = {"action": action, "ok": bool(payload.get("ok", True)), "at": time.time()}
    state["updated_at"] = time.time()
    _save_json(state_path, state)
    if "evolution" not in payload:
        payload["evolution"] = build_evolution_management_snapshot()
    return payload
