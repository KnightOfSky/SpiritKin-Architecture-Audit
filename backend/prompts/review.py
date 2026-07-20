"""Prompts for model-based review / jury / ecosystem evaluation."""

from __future__ import annotations

from string import Template

SKILL_REVIEW_PROMPT = Template(
    "你是 SpiritKinAI 的技能/代码质量审查员。请给出可执行纠错建议，"
    "要求包含：问题归因、最小修复步骤、应加入的数据集样本、验证命令。\n\n"
    "$label"
    "问题：\n$problem\n\n"
    "上下文：\n$context"
)

CODE_JURY_PROMPT = Template(
    "You are a SpiritKinAI code/UI jury reviewer. Treat the package as evidence and return only JSON.\n"
    "Required schema:\n"
    '{"decision":"approved|changes_requested|blocked",'
    '"scores":{"criterion":0-100},'
    '"findings":[{"severity":"low|medium|high|critical","category":"criterion",'
    '"title":"...","detail":"...","file_path":"...","line":0,"evidence":"...","suggested_fix":"..."}],'
    '"suggestions":["..."],"confidence":0.0}\n\n'
    "Criteria: $criteria\n"
    "Review type: $review_type\n"
    "Requirement:\n$requirement\n\n"
    "Files changed:\n$files_changed\n\n"
    "Candidate diff:\n$candidate_diff\n\n"
    "Build results:\n$build_results\n\n"
    "Unit/static results:\n$unit_static_results\n\n"
    "Screenshots:\n$screenshots"
)

ECOSYSTEM_REVIEW_PROMPT = Template(
    "请作为 SpiritKinAI 多模型生态评审员，基于下面的系统评分、issue 和 proposal，"
    "输出 JSON：{\"score\":0-100,\"proposals\":[{\"title\":\"...\",\"detail\":\"...\","
    "\"risk_level\":\"low|medium|high\",\"actions\":[{\"type\":\"manual.review_model_suggestion\"}]}]}。"
    "不要建议直接执行高风险代码改动；代码修改必须先进入 proposal。\n\n"
    "Score:\n$score\n\n"
    "Issues:\n$issues\n\n"
    "Current proposals:\n$proposals"
)

SKILL_ASSIST_FALLBACK_PROMPT = Template(
    "请审查 Skill $skill_name。\n问题：$problem\n用户输入：$user_input\n$context"
)
