from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

PROJECT_PHASE_TEMPLATES = {
    "store_launch": ("intake", "selection", "listing", "traction", "creative", "advertising", "service", "aftersales", "review"),
    "growth_ops": ("intake", "listing", "creative", "advertising", "review"),
    "service_ops": ("intake", "service", "aftersales", "review"),
}
PHASE_KEYWORDS = {
    "selection": ("选品", "类目", "竞品", "sku", "爆品"),
    "listing": ("上架", "标题", "详情页", "主图", "商品页", "店铺装修"),
    "traction": ("补单", "动销", "冷启动", "测款", "测试单", "出单"),
    "creative": ("素材", "短视频", "脚本", "创意", "拍摄"),
    "advertising": ("投流", "投放", "广告", "roi", "直通车", "千川"),
    "service": ("售前", "客服", "咨询", "话术"),
    "aftersales": ("售后", "退款", "退货", "差评", "工单"),
    "review": ("复盘", "报表", "gmv", "利润", "转化率"),
}
NEXT_ACTIONS = {
    "selection": ["补齐候选选品池", "整理竞品卖点与价格带", "筛出首批测试 SKU"],
    "listing": ["准备标题/卖点/详情页", "检查主图与视频素材缺口", "完成发布前核对"],
    "traction": ["定义动销测试窗口", "记录测试单量与转化", "判断是否继续加量"],
    "creative": ["拆分素材主题与钩子", "建立素材测试批次", "归纳高点击素材特征"],
    "advertising": ["拆分广告组与预算", "绑定素材与人群包", "按 ROI/CVR 复盘调参"],
    "service": ["整理售前高频问题", "生成客服快捷回复", "定义升级人工节点"],
    "aftersales": ["归类退款退货原因", "建立异常工单 SOP", "回写商品与素材问题"],
    "review": ["汇总店铺关键指标", "评估 SKU 与素材表现", "确定下一轮优化动作"],
    "intake": ["明确店铺目标", "确认当前卡点", "选择优先推进阶段"],
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def infer_phase(request: str) -> str:
    text = (request or "").lower()
    for phase, keywords in PHASE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return phase
    return "intake"


def infer_project_type(request: str) -> str:
    text = request or ""
    if any(keyword in text for keyword in ("起店", "新店", "开店", "上新")):
        return "store_launch"
    if any(keyword in text for keyword in ("售前", "售后", "客服", "退款", "退货")):
        return "service_ops"
    return "growth_ops"


def phase_template_for_project_type(project_type: str) -> tuple[str, ...]:
    return PROJECT_PHASE_TEMPLATES.get(project_type, PROJECT_PHASE_TEMPLATES["growth_ops"])


@dataclass
class ProjectPhase:
    name: str
    status: str = "pending"
    detail: str = ""

    def snapshot(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class EcommerceProject:
    project_id: str
    project_type: str
    goal: str
    phases: list[ProjectPhase]
    status: str = "active"
    current_phase: str = "intake"
    linked_task_ids: list[str] = field(default_factory=list)
    candidate_products: list[dict[str, str]] = field(default_factory=list)
    active_skus: list[str] = field(default_factory=list)
    creative_tests: list[dict[str, str]] = field(default_factory=list)
    ad_tests: list[dict[str, str]] = field(default_factory=list)
    customer_service_rules: list[str] = field(default_factory=list)
    after_sales_issues: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    last_request: str = ""
    last_summary: str = ""
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)

    def advance_phase(self, new_phase: str) -> bool:
        template = phase_template_for_project_type(self.project_type)
        try:
            current_idx = template.index(self.current_phase)
            new_idx = template.index(new_phase)
        except ValueError:
            return False
        if new_idx < current_idx:
            return False
        self.current_phase = new_phase
        self.updated_at = _utcnow()
        for phase in self.phases:
            if phase.name == new_phase:
                phase.status = "active"
        self.next_actions = list(NEXT_ACTIONS.get(new_phase, ["持续推进"]))
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "project_type": self.project_type,
            "goal": self.goal,
            "status": self.status,
            "current_phase": self.current_phase,
            "linked_task_ids": list(self.linked_task_ids),
            "candidate_products": list(self.candidate_products),
            "active_skus": list(self.active_skus),
            "creative_tests": list(self.creative_tests),
            "ad_tests": list(self.ad_tests),
            "customer_service_rules": list(self.customer_service_rules),
            "after_sales_issues": list(self.after_sales_issues),
            "next_actions": list(self.next_actions),
            "last_request": self.last_request,
            "last_summary": self.last_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phases": [phase.snapshot() for phase in self.phases],
        }


@dataclass
class EcommerceProjectRegistry:
    _projects: dict[str, EcommerceProject] = field(default_factory=dict, init=False)

    def _touch(self, project: EcommerceProject) -> EcommerceProject:
        project.updated_at = _utcnow()
        return project

    @staticmethod
    def _phase_status_for_project(project: EcommerceProject) -> str:
        if project.status == "queued":
            return "queued"
        if project.status == "blocked":
            return "blocked"
        return "running"

    def _activate_phase(self, project: EcommerceProject, phase_name: str, detail: str = "") -> EcommerceProject:
        phase_names = [phase.name for phase in project.phases]
        if not phase_names:
            return project
        current_name = project.current_phase if project.current_phase in phase_names else phase_names[0]
        current_index = phase_names.index(current_name)
        target_index = phase_names.index(phase_name) if phase_name in phase_names else current_index
        if target_index < current_index:
            target_index = current_index
        target_name = phase_names[target_index]

        for index, phase in enumerate(project.phases):
            if index == current_index and current_index < target_index and phase.status in {"pending", "running", "queued", "blocked"}:
                phase.status = "complete"
            elif phase.name == target_name:
                phase.status = self._phase_status_for_project(project)
                if detail:
                    phase.detail = detail
            elif phase.status in {"running", "queued", "blocked"}:
                phase.status = "pending"
        project.current_phase = target_name
        project.next_actions = list(NEXT_ACTIONS.get(target_name, NEXT_ACTIONS["intake"]))
        return self._touch(project)

    def _find_active(self, project_type: str) -> EcommerceProject | None:
        for project in self._projects.values():
            if project.project_type == project_type and project.status not in {"complete", "archived", "failed"}:
                return project
        return None

    def ensure_project(self, request: str, task_id: str = "") -> EcommerceProject:
        project_type = infer_project_type(request)
        project = self._find_active(project_type)
        if project is None:
            project = EcommerceProject(
                project_id=f"ecom_{uuid4().hex[:10]}",
                project_type=project_type,
                goal=(request or "电商经营任务")[:120],
                phases=[ProjectPhase(name=name) for name in phase_template_for_project_type(project_type)],
            )
            self._projects[project.project_id] = project
        if task_id and task_id not in project.linked_task_ids:
            project.linked_task_ids.append(task_id)
        project.last_request = request
        return self._activate_phase(project, infer_phase(request), detail="由最新电商请求触发")

    def get(self, project_id: str) -> EcommerceProject | None:
        return self._projects.get(project_id)

    def get_snapshot(self, project_id: str) -> dict[str, object] | None:
        project = self._projects.get(project_id)
        return project.snapshot() if project is not None else None

    def note_task(self, project_id: str, *, task_id: str = "", status: str = "", summary: str = "", detail: str = "") -> EcommerceProject | None:
        project = self._projects.get(project_id)
        if project is None:
            return None
        if task_id and task_id not in project.linked_task_ids:
            project.linked_task_ids.append(task_id)
        if status:
            project.status = status
            phase = next((item for item in project.phases if item.name == project.current_phase), None)
            if phase is not None:
                phase.status = "queued" if status == "queued" else ("blocked" if status == "blocked" else "running")
        if summary:
            project.last_summary = summary
        if detail:
            phase = next((item for item in project.phases if item.name == project.current_phase), None)
            if phase is not None:
                phase.detail = detail
        return self._touch(project)

    def list_projects(self, active_only: bool = True, project_type: str | None = None, status: str | None = None) -> list[dict[str, object]]:
        projects = list(self._projects.values())
        if active_only:
            projects = [project for project in projects if project.status not in {"complete", "archived"}]
        if project_type:
            projects = [project for project in projects if project.project_type == project_type]
        if status:
            projects = [project for project in projects if project.status == status]
        return [project.snapshot() for project in projects]