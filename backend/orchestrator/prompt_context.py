"""Pure prompt/context builders extracted from agent_cluster.

Module-level functions with no dependency on AgentCluster state: they take
user input or request metadata and return prompt fragments or plain data.
"""

from __future__ import annotations

import re

ACTION_REQUEST_KEYWORDS = (
    "打开",
    "启动",
    "运行",
    "扫描",
    "列出",
    "枚举",
    "搜索",
    "查一下",
    "搜一下",
    "读取",
    "写入",
    "保存",
    "文件",
    "开起来",
    "开一下",
    "关掉",
    "关闭",
    "发消息",
    "发个消息",
    "发送",
    "同步",
    "通知",
    "提醒",
    "转告",
    "告诉",
    "点击",
    "双击",
    "鼠标",
    "光标",
    "输入",
    "键入",
    "打字",
    "按一下",
    "快捷键",
    "机械臂",
    "夹爪",
    "回零",
    "拍照",
    "摄像头",
    "自拍",
    "相机",
    "拍一张",
    "camera",
    "cam",
    "浏览器",
    "标签页",
    "剪贴板",
)


def build_plan_mode_steps(user_input: str) -> list[dict[str, object]]:
    target = user_input.strip() or "当前请求"
    return [
        {"index": 1, "title": "确认目标", "detail": f"明确要达成的结果：{target[:120]}", "status": "pending"},
        {"index": 2, "title": "检查上下文", "detail": "确认项目、权限、模型、分支、附件和本地运行目标是否正确。", "status": "pending"},
        {"index": 3, "title": "拆分步骤", "detail": "把任务拆成可验证的小步骤，并标出需要人工确认的高风险动作。", "status": "pending"},
        {"index": 4, "title": "执行前确认", "detail": "仅在用户确认后才进入执行或工具调用。", "status": "pending"},
    ]


def format_plan_mode_text(steps: list[dict[str, object]]) -> str:
    lines = ["计划如下："]
    for step in steps:
        lines.append(f"{step['index']}. {step['title']}：{step['detail']}")
    return "\n".join(lines)


def goal_metadata(metadata: dict | None) -> dict[str, object]:
    metadata = metadata or {}
    text = str(metadata.get("goal_text") or metadata.get("active_goal") or "").strip()
    return {
        "text": text,
        "session_id": str(metadata.get("session_id") or "").strip(),
        "project_id": str(metadata.get("project_id") or "").strip(),
        "project_title": str(metadata.get("project_title") or "").strip(),
    }


def build_goal_context(metadata: dict | None) -> str:
    goal = goal_metadata(metadata)
    text = str(goal.get("text") or "").strip()
    if not text:
        return ""
    project = str(goal.get("project_title") or goal.get("project_id") or "").strip()
    project_part = f"\n项目：{project}" if project else ""
    return f"\n持续目标：{text}{project_part}\n"


def build_attachment_context(metadata: dict | None) -> str:
    metadata = metadata or {}
    documents = metadata.get("attachment_documents") or []
    if not isinstance(documents, list) or not documents:
        return ""
    lines = ["\n附件文本预览："]
    for item in documents[:6]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "attachment").strip()
        preview = str(item.get("text_preview") or "").strip()
        if preview:
            lines.append(f"- {path}:\n{preview[:1600]}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"


def build_long_term_memory_context(metadata: dict | None) -> str:
    metadata = metadata or {}
    hits = metadata.get("long_term_memory_hits") or []
    if not isinstance(hits, list) or not hits:
        return ""
    lines = ["\n已激活的长期记忆（仅作辅助；若与用户当前输入冲突，以当前输入为准）："]
    for item in hits[:5]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        category = str(item.get("category") or "memory").strip()
        lines.append(f"- [{category}] {content[:800]}")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def build_relationship_context(metadata: dict | None) -> str:
    metadata = metadata or {}
    relationship = metadata.get("relationship") or {}
    if not isinstance(relationship, dict):
        return ""
    stage = str(relationship.get("stage") or "new").strip()
    care = relationship.get("care_strategy") or {}
    care = care if isinstance(care, dict) else {}
    lines = [
        "\n关系与用户边界（高优先级约束；不得用亲密感推断覆盖明确边界）：",
        f"- 关系阶段: {stage}; 关怀模式: {str(care.get('mode') or 'focused_support')}; 主动程度: {str(care.get('proactive_level') or 'low')}",
    ]
    boundaries = relationship.get("boundaries") or []
    if isinstance(boundaries, list) and boundaries:
        lines.append("- 用户明确边界（必须遵守，除非用户已明确撤回）：")
        for item in boundaries[:12]:
            if not isinstance(item, dict) or item.get("active") is False:
                continue
            subject = str(item.get("subject") or "").strip()
            if subject:
                lines.append(f"  - [{str(item.get('kind') or 'general')}] 不要{subject[:120]}")
    lines.append("- 不使用未经用户允许的昵称或越级亲密表达；发现纠正时先承认并调整，不争辩。")
    return "\n".join(lines) + "\n"


def looks_like_action_request(user_input: str) -> bool:
    normalized = user_input.strip().lower().replace(" ", "")
    if any(keyword in normalized for keyword in ACTION_REQUEST_KEYWORDS):
        return True
    visual_targets = ("屏幕", "界面", "页面", "窗口")
    visual_verbs = ("看", "读", "分析", "识别")
    return any(target in normalized for target in visual_targets) and any(verb in normalized for verb in visual_verbs)


def format_inventory_software(items, limit: int = 30) -> list[str]:
    names = []
    for item in list(items or [])[:limit]:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("display_name") or "").strip()
            if name:
                launchable = "可启动" if item.get("can_launch") else "已发现"
                names.append(f"{name}({launchable})")
    return names


def format_inventory_hardware(items, limit: int = 20) -> list[str]:
    names = []
    for item in list(items or [])[:limit]:
        if isinstance(item, dict):
            name = str(item.get("FriendlyName") or item.get("name") or item.get("Class") or "").strip()
            if name:
                names.append(name)
    return names


def looks_like_backend_web_search(user_input: str) -> bool:
    query = user_input.strip().lower()
    web_keywords = (
        "联网",
        "网上",
        "网页",
        "网络",
        "最新",
        "新闻",
        "web search",
        "internet",
    )
    if any(keyword in query for keyword in web_keywords):
        return True
    return bool(re.search(r"(?:搜索|搜|查)(?:一下)?(?:.+)(?:官网|价格|新闻|最新|资料|网页)", query))


def extract_backend_web_search_query(user_input: str) -> str:
    query = re.sub(r"^(?:联网|网上|网络|网页)?(?:搜索|搜一下|搜索一下|查一下|查查|查询)\s*", "", user_input.strip(), flags=re.IGNORECASE)
    return query.strip(" ：:，,。") or user_input.strip()


def web_search_requested(metadata: dict[str, object], user_input: str) -> bool:
    enabled = metadata.get("web_search_enabled")
    if enabled is True:
        return True
    if isinstance(enabled, str) and enabled.strip().lower() in {"1", "true", "yes", "on", "enabled"}:
        return True
    if enabled is False:
        return False
    if isinstance(enabled, str) and enabled.strip().lower() in {"0", "false", "no", "off", "disabled"}:
        return False
    return looks_like_backend_web_search(user_input)
