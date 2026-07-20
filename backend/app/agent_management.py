from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from backend.app.settings import describe_model_capabilities
from backend.remote.package_security import (
    build_remote_package_signature,
    remote_package_canonical_json,
    verify_remote_package_signature,
)
from backend.security.safety_control import evaluate_execution_safety
from backend.state_store import resolve_state_path

DEFAULT_AGENT_MANAGEMENT_PATH = "state/desktop_console/agent_management.json"
DEFAULT_REMOTE_EXPORT_DIR = "state/remote_exports"
REMOTE_AUTH_HEADER = "X-SpiritKin-Remote-Token"
REMOTE_PACKAGE_SCHEMA_VERSION = "spiritkin.remote_package.v2"
SUPPORTED_REMOTE_PACKAGE_SCHEMA_VERSIONS = (REMOTE_PACKAGE_SCHEMA_VERSION,)


@dataclass(frozen=True)
class ExternalAssistantConfig:
    assistant_id: str
    label: str
    kind: str = "cli"
    command: str = ""
    working_directory: str = ""
    enabled: bool = False
    allow_write: bool = False
    review_only: bool = True
    category: str = "general"
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "assistant_id": self.assistant_id,
            "label": self.label,
            "kind": self.kind,
            "command": self.command,
            "working_directory": self.working_directory,
            "enabled": self.enabled,
            "allow_write": self.allow_write,
            "review_only": self.review_only,
            "category": self.category,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AgentAdapterConfig:
    adapter_id: str
    label: str
    kind: str = "native"
    framework: str = "spiritkin_native"
    command: str = ""
    module: str = ""
    endpoint: str = ""
    working_directory: str = ""
    enabled: bool = True
    allow_write: bool = False
    review_only: bool = False
    capabilities: tuple[str, ...] = ()
    owner_agent_ids: tuple[str, ...] = ()
    health_status: str = "unknown"
    health_detail: str = ""
    notes: str = ""
    updated_at: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "label": self.label,
            "kind": self.kind,
            "framework": self.framework,
            "command": self.command,
            "module": self.module,
            "endpoint": self.endpoint,
            "working_directory": self.working_directory,
            "enabled": self.enabled,
            "allow_write": self.allow_write,
            "review_only": self.review_only,
            "capabilities": list(self.capabilities),
            "owner_agent_ids": list(self.owner_agent_ids),
            "health_status": self.health_status,
            "health_detail": self.health_detail,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SkillAssistPolicy:
    enabled: bool = False
    mode: str = "human_review"
    require_before_run: bool = False
    require_on_failure: bool = True
    allow_external_model: bool = True
    allow_external_cli: bool = False
    selected_assistant_id: str = "cloud_model"
    notes: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "require_before_run": self.require_before_run,
            "require_on_failure": self.require_on_failure,
            "allow_external_model": self.allow_external_model,
            "allow_external_cli": self.allow_external_cli,
            "selected_assistant_id": self.selected_assistant_id,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ManagedAgentConfig:
    agent_id: str
    label: str
    domain: str
    enabled: bool = True
    provider: str = ""
    model: str = ""
    model_id: str = ""
    framework: str = "native"
    adapter: str = "spiritkin_native"
    role: str = "specialist"
    priority: int = 50
    capabilities: tuple[str, ...] = ()
    allowed_assistant_ids: tuple[str, ...] = ()
    knowledge_base_id: str = ""
    knowledge_base_path: str = ""
    brain_profile: str = ""
    notes: str = ""
    allowed_tools: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "label": self.label,
            "domain": self.domain,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "model_id": self.model_id,
            "framework": self.framework,
            "adapter": self.adapter,
            "role": self.role,
            "priority": self.priority,
            "capabilities": list(self.capabilities),
            "allowed_assistant_ids": list(self.allowed_assistant_ids),
            "knowledge_base_id": self.knowledge_base_id,
            "knowledge_base_path": self.knowledge_base_path,
            "brain_profile": self.brain_profile,
            "notes": self.notes,
            "allowed_tools": list(self.allowed_tools),
        }


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    knowledge_base_id: str
    label: str
    owner_agent_id: str = ""
    domain: str = "general"
    path: str = ""
    shared_scope: str = "agent"
    enabled: bool = True
    notes: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "knowledge_base_id": self.knowledge_base_id,
            "label": self.label,
            "owner_agent_id": self.owner_agent_id,
            "domain": self.domain,
            "path": self.path,
            "shared_scope": self.shared_scope,
            "enabled": self.enabled,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class AgentRouteMember:
    member_id: str
    role: str
    provider: str
    model: str
    weight: float = 1.0
    enabled: bool = True
    capabilities: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "weight": self.weight,
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class AgentRouteProfile:
    profile_id: str
    label: str
    strategy: str = "primary_with_specialists"
    enabled: bool = True
    members: tuple[AgentRouteMember, ...] = ()
    notes: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "strategy": self.strategy,
            "enabled": self.enabled,
            "members": [member.snapshot() for member in self.members],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RemoteExportTarget:
    target_id: str
    label: str
    base_url: str
    token_set: bool = False
    enabled: bool = False
    capabilities: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "label": self.label,
            "base_url": self.base_url,
            "token_set": self.token_set,
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class AgentManagementState:
    skill_assist: SkillAssistPolicy
    agents: tuple[ManagedAgentConfig, ...]
    active_route_profile_id: str
    route_profiles: tuple[AgentRouteProfile, ...]
    external_assistants: tuple[ExternalAssistantConfig, ...]
    agent_adapters: tuple[AgentAdapterConfig, ...] = ()
    remote_targets: tuple[RemoteExportTarget, ...] = ()
    knowledge_bases: tuple[KnowledgeBaseConfig, ...] = ()
    recommended_improvements: tuple[dict[str, Any], ...] = ()
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "skill_assist": self.skill_assist.snapshot(),
            "agents": [agent.snapshot() for agent in self.agents],
            "active_route_profile_id": self.active_route_profile_id,
            "route_profiles": [profile.snapshot() for profile in self.route_profiles],
            "external_assistants": [assistant.snapshot() for assistant in self.external_assistants],
            "agent_adapters": [adapter.snapshot() for adapter in self.agent_adapters],
            "remote_targets": [target.snapshot() for target in self.remote_targets],
            "knowledge_bases": [knowledge.snapshot() for knowledge in self.knowledge_bases],
            "recommended_improvements": list(self.recommended_improvements),
            "updated_at": self.updated_at,
        }


def resolve_agent_management_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_AGENT_MANAGEMENT_PATH", DEFAULT_AGENT_MANAGEMENT_PATH, path)


def load_agent_management_state(path: str | os.PathLike[str] | None = None) -> AgentManagementState:
    target = resolve_agent_management_path(path)
    if not target.exists():
        return default_agent_management_state()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return state_from_dict(data)


def save_agent_management_state(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> AgentManagementState:
    current = load_agent_management_state(path).snapshot()
    merged = {
        **current,
        **dict(payload or {}),
        "updated_at": time.time(),
    }
    state = state_from_dict(merged)
    target = resolve_agent_management_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def default_agent_management_state() -> AgentManagementState:
    model_caps = describe_model_capabilities()
    text = dict(model_caps.get("text") or {})
    vision = dict(model_caps.get("vision") or {})
    return AgentManagementState(
        skill_assist=SkillAssistPolicy(),
        agents=tuple(default_managed_agents()),
        active_route_profile_id="default_hybrid",
        route_profiles=tuple(default_route_profiles(text=text, vision=vision)),
        external_assistants=(
            ExternalAssistantConfig(
                "codex_cli",
                "Codex CLI",
                command="codex exec --skip-git-repo-check --color never --json -",
                review_only=True,
            ),
            ExternalAssistantConfig("claude_code", "Claude Code", command="claude", review_only=True),
            ExternalAssistantConfig("cloud_model", "云端模型评审", kind="api", enabled=True, review_only=True),
        ),
        agent_adapters=tuple(_merge_default_agent_adapters([])),
        remote_targets=(),
        knowledge_bases=tuple(default_knowledge_bases(default_managed_agents())),
        recommended_improvements=tuple(build_project_improvement_recommendations()),
    )


def state_from_dict(data: dict[str, Any]) -> AgentManagementState:
    route_profiles = tuple(_route_profile_from_dict(item) for item in data.get("route_profiles") or [])
    route_profiles = tuple(_merge_default_route_profiles(list(route_profiles)))
    agents = tuple(_managed_agent_from_dict(item) for item in data.get("agents") or [])
    if not agents:
        agents = tuple(default_managed_agents())
    knowledge_bases = tuple(_knowledge_base_from_dict(item) for item in data.get("knowledge_bases") or [])
    if not knowledge_bases:
        knowledge_bases = tuple(default_knowledge_bases(agents))
    else:
        knowledge_bases = tuple(_merge_default_knowledge_bases(list(knowledge_bases), agents))
    active_route_profile_id = str(data.get("active_route_profile_id") or "")
    if not active_route_profile_id and route_profiles:
        preferred = next((item for item in route_profiles if item.profile_id == "default_hybrid"), None)
        active_route_profile_id = (preferred or route_profiles[0]).profile_id
    agent_adapters = tuple(_agent_adapter_from_dict(item) for item in data.get("agent_adapters") or [])
    agent_adapters = tuple(_merge_default_agent_adapters(list(agent_adapters)))
    return AgentManagementState(
        skill_assist=_skill_assist_from_dict(dict(data.get("skill_assist") or {})),
        agents=agents,
        active_route_profile_id=active_route_profile_id,
        route_profiles=route_profiles,
        external_assistants=tuple(_external_assistant_from_dict(item) for item in data.get("external_assistants") or []),
        agent_adapters=agent_adapters,
        remote_targets=tuple(_remote_target_from_dict(item) for item in data.get("remote_targets") or []),
        knowledge_bases=knowledge_bases,
        recommended_improvements=tuple(dict(item) for item in (data.get("recommended_improvements") or build_project_improvement_recommendations())),
        updated_at=float(data.get("updated_at") or time.time()),
    )


def default_route_profiles(*, text: dict[str, Any] | None = None, vision: dict[str, Any] | None = None) -> list[AgentRouteProfile]:
    text = dict(text or (describe_model_capabilities().get("text") or {}))
    vision = dict(vision or (describe_model_capabilities().get("vision") or {}))
    text_provider = str(text.get("provider") or "local_transformers")
    text_model = str(text.get("model") or "")
    vision_provider = str(vision.get("provider") or "openai_compatible")
    vision_model = str(vision.get("model") or "")
    return [
        AgentRouteProfile(
            profile_id="default_hybrid",
            label="本地优先 + 云端评审",
            strategy="primary_with_specialists",
            members=(
                AgentRouteMember("main_text", "primary_text", text_provider, text_model, weight=1.0, capabilities=("planning", "coding", "dialogue", "tool_routing")),
                AgentRouteMember("vision_model", "vision", vision_provider, vision_model, weight=0.75, capabilities=("screen_understanding", "image_reasoning")),
                AgentRouteMember("cloud_model", "reviewer", "cloud_openai_compatible", "", weight=0.35, enabled=False, capabilities=("quality_review", "repair_suggestion")),
            ),
            notes="默认走本地/主文本模型执行低延迟任务；云模型默认作为可开启的审查者，不直接接管写入。",
        ),
        AgentRouteProfile(
            profile_id="cloud_review_gate",
            label="本地执行 + 云端二次审查",
            strategy="committee_review",
            members=(
                AgentRouteMember("main_text", "primary_text", text_provider, text_model, weight=1.0, capabilities=("planning", "coding", "tool_routing")),
                AgentRouteMember("cloud_model", "reviewer", "cloud_openai_compatible", "", weight=0.65, enabled=False, capabilities=("code_review", "risk_check", "long_context_review")),
            ),
            notes="适合高风险修改、大范围重构、长文档总结：本地模型先产出方案，云模型只做审查和反例检查。",
        ),
        AgentRouteProfile(
            profile_id="cloud_fallback_chain",
            label="本地失败后云端降级",
            strategy="fallback_chain",
            members=(
                AgentRouteMember("main_text", "primary_text", text_provider, text_model, weight=1.0, capabilities=("dialogue", "tool_routing")),
                AgentRouteMember("cloud_model", "fallback_text", "cloud_openai_compatible", "", weight=0.8, enabled=False, capabilities=("fallback_reasoning", "repair_suggestion")),
            ),
            notes="本地模型超时、模型未加载或输出低置信度时再切云端；需要 API Key 和人工确认写入边界。",
        ),
    ]


def _merge_default_route_profiles(route_profiles: list[AgentRouteProfile]) -> list[AgentRouteProfile]:
    defaults = default_route_profiles()
    if not route_profiles:
        return defaults
    existing = {profile.profile_id for profile in route_profiles}
    merged = list(route_profiles)
    for profile in defaults:
        if profile.profile_id not in existing:
            merged.append(profile)
    return merged


def default_agent_adapters() -> list[AgentAdapterConfig]:
    return [
        AgentAdapterConfig(
            "coordinator_router",
            "SpiritKin native dispatcher",
            kind="native",
            framework="spiritkin_native",
            enabled=True,
            allow_write=False,
            review_only=False,
            capabilities=("planning", "dialogue", "tool_routing", "agent_dispatch"),
            owner_agent_ids=("main_text",),
            notes="Default chief dispatcher. It routes work through the local AgentCluster and ToolRegistry.",
        ),
        AgentAdapterConfig(
            "spiritkin_native",
            "SpiritKin native generic adapter",
            kind="native",
            framework="spiritkin_native",
            enabled=True,
            allow_write=False,
            review_only=False,
            capabilities=("planning", "dialogue", "tool_routing", "skill_execution"),
            owner_agent_ids=(),
            notes="Compatibility adapter for older Agent configs that did not declare a specific adapter id.",
        ),
        AgentAdapterConfig(
            "code_agent_adapter",
            "Codex/native code adapter",
            kind="cli",
            framework="codex_or_native",
            command="codex",
            enabled=True,
            allow_write=False,
            review_only=True,
            capabilities=("code_edit", "tests", "debugging", "code_review"),
            owner_agent_ids=("programming",),
            notes="Use Codex CLI as a governed code reviewer/executor candidate. Keep review-only until write gates are configured.",
        ),
        AgentAdapterConfig(
            "vision_agent_adapter",
            "Native vision adapter",
            kind="native",
            framework="spiritkin_native",
            enabled=True,
            allow_write=False,
            review_only=False,
            capabilities=("screen_understanding", "image_reasoning", "asset_check"),
            owner_agent_ids=("vision_model",),
        ),
        AgentAdapterConfig(
            "timeline_agent_adapter",
            "LangGraph timeline adapter candidate",
            kind="framework",
            framework="langgraph_candidate",
            module="langgraph",
            enabled=True,
            allow_write=False,
            review_only=True,
            capabilities=("animation_plan", "timeline", "asset_pipeline"),
            owner_agent_ids=("video_animation",),
            notes="Candidate professional framework adapter. Enable only after dependency/runtime checks pass.",
        ),
        AgentAdapterConfig(
            "game_team_adapter",
            "CrewAI/native game team adapter candidate",
            kind="framework",
            framework="crewai_or_native",
            module="crewai",
            enabled=True,
            allow_write=False,
            review_only=True,
            capabilities=("game_logic", "ui", "playtest"),
            owner_agent_ids=("game_development",),
            notes="Candidate team framework adapter. Native workflow remains the fallback.",
        ),
        AgentAdapterConfig(
            "commerce_ops_adapter",
            "Commerce operations native adapter",
            kind="native",
            framework="spiritkin_native",
            enabled=True,
            allow_write=False,
            review_only=False,
            capabilities=("listing", "campaign", "operations"),
            owner_agent_ids=("ecommerce",),
        ),
        AgentAdapterConfig(
            "skill_runner_adapter",
            "Skill Runner adapter",
            kind="native",
            framework="skill_runner",
            enabled=True,
            allow_write=True,
            review_only=False,
            capabilities=("skill_execution", "tool_steps", "learning_feedback"),
            owner_agent_ids=("skill_runner",),
        ),
        AgentAdapterConfig(
            "review_agent_adapter",
            "Cloud/API review adapter",
            kind="api",
            framework="remote_or_api",
            enabled=True,
            allow_write=False,
            review_only=True,
            capabilities=("quality_review", "correction", "dataset_feedback", "risk_check"),
            owner_agent_ids=("external_reviewer",),
            notes="Reviewer-only cloud/API adapter. It should not directly mutate project state.",
        ),
        AgentAdapterConfig(
            "remote_worker_adapter",
            "Remote Worker adapter",
            kind="remote",
            framework="spiritkin_remote_worker",
            endpoint="",
            enabled=False,
            allow_write=False,
            review_only=False,
            capabilities=("remote_skill_execution", "device_task", "package_import"),
            owner_agent_ids=(),
            notes="Bridge to governed Remote Workers. Requires base URL/token and package verification before use.",
        ),
    ]


def _merge_default_agent_adapters(agent_adapters: list[AgentAdapterConfig]) -> list[AgentAdapterConfig]:
    defaults = default_agent_adapters()
    if not agent_adapters:
        return [_with_adapter_health(adapter) for adapter in defaults]
    existing = {adapter.adapter_id for adapter in agent_adapters}
    merged = list(agent_adapters)
    for adapter in defaults:
        if adapter.adapter_id not in existing:
            merged.append(adapter)
    return [_with_adapter_health(adapter) for adapter in merged]


def default_managed_agents() -> list[ManagedAgentConfig]:
    model_caps = describe_model_capabilities()
    text = dict(model_caps.get("text") or {})
    vision = dict(model_caps.get("vision") or {})
    text_provider = str(text.get("provider") or "openai_compatible")
    text_model = str(text.get("model") or "")
    vision_provider = str(vision.get("provider") or "openai_compatible")
    vision_model = str(vision.get("model") or "")
    agents = [
        ManagedAgentConfig("main_text", "主 Agent", "general", True, text_provider, text_model, "default_text", "native", "coordinator_router", "primary", 100, ("planning", "dialogue", "tool_routing", "coding_review"), ("cloud_model",), "kb_main_text", "state/knowledge_bases/agents/main_text", "primary_text_brain"),
        ManagedAgentConfig("programming", "编程 Agent", "programming", True, text_provider, text_model, "default_text", "codex_or_native", "code_agent_adapter", "specialist", 90, ("code_edit", "tests", "debugging"), ("codex_cli", "cloud_model"), "kb_programming", "state/knowledge_bases/agents/programming", "programming_brain"),
        ManagedAgentConfig("vision_model", "视觉 Agent", "vision", True, vision_provider, vision_model, "default_vision", "native", "vision_agent_adapter", "specialist", 85, ("screen_understanding", "image_reasoning", "asset_check"), ("cloud_model",), "kb_vision_model", "state/knowledge_bases/agents/vision_model", "vision_brain"),
        ManagedAgentConfig("video_animation", "视频动画 Agent", "video_animation", True, text_provider, text_model, "default_text", "langgraph_candidate", "timeline_agent_adapter", "specialist", 70, ("animation_plan", "timeline", "asset_pipeline"), ("cloud_model",), "kb_video_animation", "state/knowledge_bases/agents/video_animation", "video_animation_brain"),
        ManagedAgentConfig("game_development", "游戏开发 Agent", "game_development", True, text_provider, text_model, "default_text", "crewai_or_native", "game_team_adapter", "specialist", 70, ("game_logic", "ui", "playtest"), ("codex_cli", "cloud_model"), "kb_game_development", "state/knowledge_bases/agents/game_development", "game_development_brain"),
        ManagedAgentConfig("ecommerce", "电商项目 Agent", "ecommerce", True, text_provider, text_model, "default_text", "native", "commerce_ops_adapter", "specialist", 60, ("listing", "campaign", "operations"), ("cloud_model",), "kb_ecommerce", "state/knowledge_bases/agents/ecommerce", "ecommerce_brain"),
        ManagedAgentConfig("skill_runner", "Skill 执行器", "skill", True, "local", "SkillRunner", "skill_runner", "native", "skill_runner_adapter", "executor", 80, ("skill_execution", "tool_steps", "learning_feedback"), (), "kb_skill_runner", "state/knowledge_bases/agents/skill_runner", "skill_runner_brain"),
        ManagedAgentConfig("external_reviewer", "外部评审 Agent", "review", False, "cloud_openai_compatible", "", "review_model", "remote_or_api", "review_agent_adapter", "reviewer", 40, ("quality_review", "correction", "dataset_feedback"), ("codex_cli", "claude_code", "cloud_model"), "kb_external_reviewer", "state/knowledge_bases/agents/external_reviewer", "external_reviewer_brain"),
    ]
    persona_tools = {
        "game_development": ("python.run_script", "browser.worker_open_url", "browser.worker_search", "git.status"),
        "video_animation": ("python.run_script", "ffmpeg.probe", "ffmpeg.transcode", "browser.worker_search"),
    }
    reviewer_enabled = str(os.getenv("SPIRITKIN_EXTERNAL_REVIEWER_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    return [
        ManagedAgentConfig(
            **{
                **agent.__dict__,
                "enabled": reviewer_enabled if agent.agent_id == "external_reviewer" else agent.enabled,
                "allowed_tools": persona_tools.get(agent.agent_id, agent.allowed_tools),
            }
        )
        for agent in agents
    ]


def default_knowledge_bases(agents: tuple[ManagedAgentConfig, ...] | list[ManagedAgentConfig] | None = None) -> list[KnowledgeBaseConfig]:
    selected_agents = list(agents or default_managed_agents())
    knowledge_bases: list[KnowledgeBaseConfig] = [
        KnowledgeBaseConfig(
            knowledge_base_id="kb_domain_general",
            label="通用领域知识库",
            owner_agent_id="",
            domain="general",
            path="state/knowledge_bases/domains/general",
            shared_scope="domain",
            enabled=True,
            notes="领域级知识库，可被通用 Agent 引用。",
        ),
        KnowledgeBaseConfig(
            knowledge_base_id="wiki_agent_registry",
            label="Agent Wiki / 角色与交接规则",
            owner_agent_id="main_text",
            domain="agent_wiki",
            path="state/knowledge_bases/wiki/agents",
            shared_scope="global",
            enabled=True,
            notes="记录 Agent 职责、能力、工具边界、状态归属、交接协议和升级路径。",
        ),
        KnowledgeBaseConfig(
            knowledge_base_id="wiki_model_registry",
            label="LLM Wiki / 模型路由与限制",
            owner_agent_id="external_reviewer",
            domain="model_wiki",
            path="state/knowledge_bases/wiki/models",
            shared_scope="global",
            enabled=True,
            notes="记录模型卡、上下文/成本/延迟、适用任务、限制、fallback 链和最后验证来源。",
        ),
        KnowledgeBaseConfig(
            knowledge_base_id="wiki_project_knowledge",
            label="Project Wiki / 项目知识与来源",
            owner_agent_id="main_text",
            domain="project_wiki",
            path="state/knowledge_bases/wiki/project",
            shared_scope="global",
            enabled=True,
            notes="记录项目事实、架构决策、运行手册、来源引用、置信度和复审日期。",
        ),
    ]
    for agent in selected_agents:
        kb_id = agent.knowledge_base_id or f"kb_{agent.agent_id}"
        knowledge_bases.append(
            KnowledgeBaseConfig(
                knowledge_base_id=kb_id,
                label=f"{agent.label} 知识库",
                owner_agent_id=agent.agent_id,
                domain=agent.domain or "general",
                path=agent.knowledge_base_path or f"state/knowledge_bases/agents/{agent.agent_id}",
                shared_scope="agent",
                enabled=True,
            )
        )
    return knowledge_bases


def _merge_default_knowledge_bases(existing: list[KnowledgeBaseConfig], agents: tuple[ManagedAgentConfig, ...] | list[ManagedAgentConfig]) -> list[KnowledgeBaseConfig]:
    seen = {item.knowledge_base_id for item in existing}
    merged = list(existing)
    for item in default_knowledge_bases(agents):
        if item.knowledge_base_id not in seen:
            merged.append(item)
            seen.add(item.knowledge_base_id)
    return merged


def _skill_assist_from_dict(data: dict[str, Any]) -> SkillAssistPolicy:
    return SkillAssistPolicy(
        enabled=bool(data.get("enabled", False)),
        mode=str(data.get("mode") or "human_review"),
        require_before_run=bool(data.get("require_before_run", False)),
        require_on_failure=bool(data.get("require_on_failure", True)),
        allow_external_model=bool(data.get("allow_external_model", True)),
        allow_external_cli=bool(data.get("allow_external_cli", False)),
        selected_assistant_id=str(data.get("selected_assistant_id") or "cloud_model"),
        notes=str(data.get("notes") or ""),
    )


def _managed_agent_from_dict(data: dict[str, Any]) -> ManagedAgentConfig:
    return ManagedAgentConfig(
        agent_id=str(data.get("agent_id") or data.get("id") or ""),
        label=str(data.get("label") or data.get("agent_id") or ""),
        domain=str(data.get("domain") or "general"),
        enabled=bool(data.get("enabled", True)),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        model_id=str(data.get("model_id") or ""),
        framework=str(data.get("framework") or "native"),
        adapter=str(data.get("adapter") or "spiritkin_native"),
        role=str(data.get("role") or "specialist"),
        priority=int(data.get("priority") or 50),
        capabilities=tuple(str(item) for item in data.get("capabilities") or ()),
        allowed_assistant_ids=tuple(str(item) for item in data.get("allowed_assistant_ids") or ()),
        knowledge_base_id=str(data.get("knowledge_base_id") or ""),
        knowledge_base_path=str(data.get("knowledge_base_path") or ""),
        brain_profile=str(data.get("brain_profile") or data.get("brain_profile_id") or ""),
        notes=str(data.get("notes") or ""),
        allowed_tools=tuple(str(item) for item in data.get("allowed_tools") or ()),
    )


def _external_assistant_from_dict(data: dict[str, Any]) -> ExternalAssistantConfig:
    assistant_id = str(data.get("assistant_id") or data.get("id") or "")
    return ExternalAssistantConfig(
        assistant_id=assistant_id,
        label=str(data.get("label") or data.get("assistant_id") or ""),
        kind=str(data.get("kind") or "cli"),
        command=normalize_external_assistant_command(assistant_id, str(data.get("command") or "")),
        working_directory=str(data.get("working_directory") or ""),
        enabled=bool(data.get("enabled", False)),
        allow_write=bool(data.get("allow_write", False)),
        review_only=bool(data.get("review_only", True)),
        category=str(data.get("category") or "general"),
        metadata=dict(data.get("metadata") or {}),
    )


def normalize_external_assistant_command(assistant_id: str, command: str) -> str:
    """Upgrade known CLI assistants to their structured event mode."""
    normalized = str(command or "").strip()
    assistant_key = str(assistant_id or "").strip().lower().replace("-", "_")
    if assistant_key not in {"codex", "codex_cli"}:
        return normalized
    if not re.search(r"\bcodex(?:\.exe|\.cmd)?\s+exec\b", normalized, flags=re.IGNORECASE):
        return normalized
    if re.search(r"(?:^|\s)--json(?:\s|$)", normalized, flags=re.IGNORECASE):
        return normalized
    return re.sub(
        r"(\bcodex(?:\.exe|\.cmd)?\s+exec\b)",
        r"\1 --json",
        normalized,
        count=1,
        flags=re.IGNORECASE,
    )


def _agent_adapter_from_dict(data: dict[str, Any]) -> AgentAdapterConfig:
    return _with_adapter_health(
        AgentAdapterConfig(
            adapter_id=str(data.get("adapter_id") or data.get("id") or data.get("adapter") or ""),
            label=str(data.get("label") or data.get("adapter_id") or data.get("id") or ""),
            kind=str(data.get("kind") or "native"),
            framework=str(data.get("framework") or data.get("runtime") or "spiritkin_native"),
            command=str(data.get("command") or ""),
            module=str(data.get("module") or ""),
            endpoint=str(data.get("endpoint") or data.get("base_url") or ""),
            working_directory=str(data.get("working_directory") or ""),
            enabled=bool(data.get("enabled", True)),
            allow_write=bool(data.get("allow_write", False)),
            review_only=bool(data.get("review_only", False)),
            capabilities=tuple(str(item) for item in data.get("capabilities") or ()),
            owner_agent_ids=tuple(str(item) for item in data.get("owner_agent_ids") or data.get("agent_ids") or ()),
            health_status=str(data.get("health_status") or "unknown"),
            health_detail=str(data.get("health_detail") or ""),
            notes=str(data.get("notes") or ""),
            updated_at=float(data.get("updated_at") or 0.0),
        )
    )


def _with_adapter_health(adapter: AgentAdapterConfig) -> AgentAdapterConfig:
    status, detail = _adapter_static_health(adapter)
    return AgentAdapterConfig(
        adapter.adapter_id,
        adapter.label or adapter.adapter_id,
        adapter.kind,
        adapter.framework,
        adapter.command,
        adapter.module,
        adapter.endpoint,
        adapter.working_directory,
        adapter.enabled,
        adapter.allow_write,
        adapter.review_only,
        adapter.capabilities,
        adapter.owner_agent_ids,
        status,
        detail,
        adapter.notes,
        adapter.updated_at or time.time(),
    )


def _adapter_static_health(adapter: AgentAdapterConfig) -> tuple[str, str]:
    kind = adapter.kind.strip().lower()
    if not adapter.enabled:
        return "disabled", "Adapter is present but disabled."
    if kind in {"native", "internal", "skill"}:
        return "ready", "Native adapter is available through SpiritKin runtime."
    if kind == "cli":
        return ("ready", "CLI command is configured.") if adapter.command.strip() else ("missing_config", "CLI adapter requires a command.")
    if kind == "framework":
        if adapter.command.strip() or adapter.module.strip():
            return "configured", "Framework adapter has a command/module binding; runtime launch is still governed."
        return "missing_config", "Framework adapter requires a command or module."
    if kind in {"api", "cloud"}:
        if adapter.endpoint.strip() or adapter.command.strip() or adapter.review_only:
            return "configured", "API adapter is configured for governed review/use."
        return "missing_config", "API adapter requires an endpoint or review-only binding."
    if kind == "remote":
        return ("configured", "Remote endpoint is configured.") if adapter.endpoint.strip() else ("missing_config", "Remote adapter requires an endpoint.")
    if kind == "mcp":
        return ("configured", "MCP adapter binding is configured.") if adapter.command.strip() or adapter.endpoint.strip() else ("missing_config", "MCP adapter requires command or endpoint.")
    return "unknown", "Unknown adapter kind; review before enabling."


def _knowledge_base_from_dict(data: dict[str, Any]) -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        knowledge_base_id=str(data.get("knowledge_base_id") or data.get("id") or ""),
        label=str(data.get("label") or data.get("knowledge_base_id") or ""),
        owner_agent_id=str(data.get("owner_agent_id") or ""),
        domain=str(data.get("domain") or "general"),
        path=str(data.get("path") or ""),
        shared_scope=str(data.get("shared_scope") or "agent"),
        enabled=bool(data.get("enabled", True)),
        notes=str(data.get("notes") or ""),
    )


def _route_member_from_dict(data: dict[str, Any]) -> AgentRouteMember:
    return AgentRouteMember(
        member_id=str(data.get("member_id") or data.get("id") or ""),
        role=str(data.get("role") or "assistant"),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        weight=float(data.get("weight") or 1.0),
        enabled=bool(data.get("enabled", True)),
        capabilities=tuple(str(item) for item in data.get("capabilities") or ()),
    )


def _route_profile_from_dict(data: dict[str, Any]) -> AgentRouteProfile:
    return AgentRouteProfile(
        profile_id=str(data.get("profile_id") or data.get("id") or ""),
        label=str(data.get("label") or data.get("profile_id") or ""),
        strategy=str(data.get("strategy") or "primary_with_specialists"),
        enabled=bool(data.get("enabled", True)),
        members=tuple(_route_member_from_dict(item) for item in data.get("members") or []),
        notes=str(data.get("notes") or ""),
    )


def _remote_target_from_dict(data: dict[str, Any]) -> RemoteExportTarget:
    return RemoteExportTarget(
        target_id=str(data.get("target_id") or data.get("id") or ""),
        label=str(data.get("label") or data.get("target_id") or ""),
        base_url=str(data.get("base_url") or ""),
        token_set=bool(data.get("token_set") or data.get("token")),
        enabled=bool(data.get("enabled", False)),
        capabilities=tuple(str(item) for item in data.get("capabilities") or ()),
    )


def build_project_improvement_recommendations() -> list[dict[str, Any]]:
    return [
        {
            "id": "local-cloud-contract",
            "priority": "high",
            "title": "把本地模型和云模型分成执行/评审两种职责",
            "detail": "默认本地模型处理低延迟任务和工具路由；云模型只在高风险、长上下文、失败修复时评审或降级，避免同一轮里多个模型同时改写状态。",
        },
        {
            "id": "model-health-gates",
            "priority": "high",
            "title": "模型调用前先做健康检查",
            "detail": "本地模型要检查端口、模型列表和加载状态；云模型要检查 endpoint/API key/model，并把失败写入诊断页而不是静默失败。",
        },
        {
            "id": "review-gates",
            "priority": "high",
            "title": "把文档、Skill、远端导出都放进审核门",
            "detail": "自动生成的改动先形成 proposal 和 diff，再由人工或指定外部助手确认。",
        },
        {
            "id": "route-profiles",
            "priority": "high",
            "title": "显式管理模型组合",
            "detail": "将主文本模型、视觉模型、代码审查模型、低延迟路由模型拆成可开关的 route profile。",
        },
        {
            "id": "service-operations",
            "priority": "high",
            "title": "服务端口必须可操作",
            "detail": "桌面端需要把 frontend、event bridge、command gateway、remote worker 作为服务对象管理，并记录启动日志。",
        },
        {
            "id": "daily-ops",
            "priority": "medium",
            "title": "增加每日任务和学习日报",
            "detail": "每天汇总任务状态、学习样本、错误日志和服务健康，作为人工和外部模型接手项目的入口。",
        },
        {
            "id": "remote-skill-packages",
            "priority": "medium",
            "title": "Skill 远端导出使用包格式",
            "detail": "导出时包含 skill spec、允许工具、验证命令、回滚说明和远端 worker 目标。",
        },
    ]


def build_active_route_runtime_snapshot(state: AgentManagementState | None = None) -> dict[str, Any]:
    state = state or load_agent_management_state()
    profile = next((item for item in state.route_profiles if item.profile_id == state.active_route_profile_id), None)
    if profile is None:
        profile = next((item for item in state.route_profiles if item.enabled), None)
    if profile is None:
        return {"enabled": False, "active_route_profile_id": "", "profile": {}, "primary_text": {}}

    primary = select_route_member(profile, roles=("primary_text", "primary", "main_text", "dialogue"))
    return {
        "enabled": bool(profile.enabled),
        "active_route_profile_id": profile.profile_id,
        "profile": profile.snapshot(),
        "primary_text": primary.snapshot() if primary is not None else {},
    }


def select_route_member(
    profile: AgentRouteProfile,
    *,
    roles: tuple[str, ...],
) -> AgentRouteMember | None:
    role_set = {role.strip().lower() for role in roles if role.strip()}
    members = [member for member in profile.members if member.enabled and member.provider and member.model]
    preferred = [member for member in members if member.role.strip().lower() in role_set]
    candidates = preferred or members
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.weight, reverse=True)[0]


def build_agent_management_desktop_snapshot(state: AgentManagementState | None = None) -> dict[str, Any]:
    state = state or load_agent_management_state()
    snapshot = state.snapshot()
    snapshot["distribution_summary"] = build_agent_distribution_summary(state)
    return snapshot


def build_agent_distribution_summary(state: AgentManagementState | None = None) -> dict[str, Any]:
    state = state or load_agent_management_state()
    agents = list(state.agents)
    enabled_agents = [agent for agent in agents if agent.enabled]
    disabled_agents = [agent for agent in agents if not agent.enabled]
    active_profile = next((item for item in state.route_profiles if item.profile_id == state.active_route_profile_id), None)
    if active_profile is None:
        active_profile = next((item for item in state.route_profiles if item.enabled), None)
    active_members = [member for member in active_profile.members if member.enabled] if active_profile is not None else []
    primary_member = _select_display_primary_route_member(active_members)

    by_domain: dict[str, int] = {}
    by_role: dict[str, int] = {}
    provider_models: dict[str, set[str]] = {}
    capability_map: dict[str, set[str]] = {}
    for agent in enabled_agents:
        domain = agent.domain or "general"
        role = agent.role or "specialist"
        by_domain[domain] = by_domain.get(domain, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
        provider = agent.provider or "unconfigured"
        provider_models.setdefault(provider, set())
        if agent.model:
            provider_models[provider].add(agent.model)
        for capability in agent.capabilities:
            capability_map.setdefault(capability, set()).add(agent.agent_id)

    enabled_assistants = [assistant for assistant in state.external_assistants if assistant.enabled]
    enabled_remote_targets = [target for target in state.remote_targets if target.enabled]
    adapters = list(state.agent_adapters)
    enabled_adapters = [adapter for adapter in adapters if adapter.enabled]
    adapter_by_id = {adapter.adapter_id: adapter for adapter in adapters}
    gaps = _agent_distribution_gaps(state, enabled_agents, active_profile, primary_member)
    high_gap = any(str(gap.get("priority")) == "high" for gap in gaps)

    return {
        "status": "blocked" if high_gap else ("needs_attention" if gaps else "ready"),
        "counts": {
            "agents_total": len(agents),
            "agents_enabled": len(enabled_agents),
            "agents_disabled": len(disabled_agents),
            "route_profiles_total": len(state.route_profiles),
            "active_route_members_enabled": len(active_members),
            "external_assistants_enabled": len(enabled_assistants),
            "agent_adapters_total": len(adapters),
            "agent_adapters_enabled": len(enabled_adapters),
            "remote_targets_enabled": len(enabled_remote_targets),
            "knowledge_bases_enabled": sum(1 for item in state.knowledge_bases if item.enabled),
        },
        "active_route": {
            "profile_id": active_profile.profile_id if active_profile is not None else "",
            "label": active_profile.label if active_profile is not None else "",
            "strategy": active_profile.strategy if active_profile is not None else "",
            "enabled": bool(active_profile.enabled) if active_profile is not None else False,
            "primary_text": primary_member.snapshot() if primary_member is not None else {},
            "members": [member.snapshot() for member in active_members],
        },
        "agents_by_domain": {key: by_domain[key] for key in sorted(by_domain)},
        "agents_by_role": {key: by_role[key] for key in sorted(by_role)},
        "providers": [
            {
                "provider": provider,
                "enabled_agent_count": sum(1 for agent in enabled_agents if (agent.provider or "unconfigured") == provider),
                "models": sorted(models),
            }
            for provider, models in sorted(provider_models.items())
        ],
        "enabled_agents": [
            {
                "agent_id": agent.agent_id,
                "label": agent.label,
                "domain": agent.domain,
                "role": agent.role,
                "priority": agent.priority,
                "provider": agent.provider,
                "model": agent.model,
                "framework": agent.framework,
                "adapter": agent.adapter,
                "adapter_health": adapter_by_id.get(agent.adapter).health_status if adapter_by_id.get(agent.adapter) is not None else "missing",
                "capabilities": list(agent.capabilities),
            }
            for agent in sorted(enabled_agents, key=lambda item: (-item.priority, item.agent_id))
        ],
        "capability_coverage": [
            {"capability": capability, "agent_ids": sorted(agent_ids), "count": len(agent_ids)}
            for capability, agent_ids in sorted(capability_map.items())
        ],
        "external_assistants": [
            {
                "assistant_id": assistant.assistant_id,
                "label": assistant.label,
                "kind": assistant.kind,
                "review_only": assistant.review_only,
                "allow_write": assistant.allow_write,
            }
            for assistant in enabled_assistants
        ],
        "agent_adapters": [
            {
                "adapter_id": adapter.adapter_id,
                "label": adapter.label,
                "kind": adapter.kind,
                "framework": adapter.framework,
                "enabled": adapter.enabled,
                "review_only": adapter.review_only,
                "allow_write": adapter.allow_write,
                "health_status": adapter.health_status,
                "owner_agent_ids": list(adapter.owner_agent_ids),
            }
            for adapter in enabled_adapters
        ],
        "remote_distribution": {
            "targets_total": len(state.remote_targets),
            "targets_enabled": len(enabled_remote_targets),
            "targets": [target.snapshot() for target in state.remote_targets],
            "capabilities": sorted({capability for target in enabled_remote_targets for capability in target.capabilities}),
        },
        "gaps": gaps,
    }


def _select_display_primary_route_member(members: list[AgentRouteMember]) -> AgentRouteMember | None:
    primary_roles = {"primary_text", "primary", "main_text", "dialogue"}
    preferred = [member for member in members if member.role.strip().lower() in primary_roles]
    candidates = preferred or members
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.weight, reverse=True)[0]


def _agent_distribution_gaps(
    state: AgentManagementState,
    enabled_agents: list[ManagedAgentConfig],
    active_profile: AgentRouteProfile | None,
    primary_member: AgentRouteMember | None,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not enabled_agents:
        gaps.append({
            "id": "no_enabled_agents",
            "priority": "high",
            "title": "没有启用的 Agent",
            "detail": "至少需要启用主 Agent 或一个可执行 specialist。",
        })
    if active_profile is None:
        gaps.append({
            "id": "no_active_route_profile",
            "priority": "high",
            "title": "没有可用 route profile",
            "detail": "桌面端需要一个启用的 route profile 才能描述主模型和评审模型分工。",
        })
    elif not active_profile.enabled:
        gaps.append({
            "id": "active_route_disabled",
            "priority": "high",
            "title": "当前 route profile 已禁用",
            "detail": "请选择一个启用的 route profile 或重新启用当前 profile。",
        })
    if active_profile is not None and primary_member is None:
        gaps.append({
            "id": "missing_primary_route_member",
            "priority": "high",
            "title": "当前 route 缺少主文本成员",
            "detail": "至少需要一个 primary_text 成员承担规划、对话或工具路由。",
        })
    elif primary_member is not None and (not primary_member.provider or not primary_member.model):
        gaps.append({
            "id": "primary_route_unconfigured",
            "priority": "medium",
            "title": "主文本 route 未完整配置模型",
            "detail": "主文本成员需要 provider 和 model，运行时才能稳定注入 LLM client。",
        })
    adapter_by_id = {adapter.adapter_id: adapter for adapter in state.agent_adapters}
    for agent in enabled_agents:
        adapter_id = agent.adapter or ""
        adapter = adapter_by_id.get(adapter_id)
        if adapter is None:
            gaps.append({
                "id": f"agent_adapter_missing:{agent.agent_id}",
                "priority": "high",
                "title": f"{agent.label} 缺少 AgentAdapter",
                "detail": f"Agent {agent.agent_id} 指向 adapter '{adapter_id or '--'}'，但 registry 中不存在该 adapter。",
            })
            continue
        if not adapter.enabled:
            gaps.append({
                "id": f"agent_adapter_disabled:{agent.agent_id}",
                "priority": "medium",
                "title": f"{agent.label} 的 Adapter 已关闭",
                "detail": f"Adapter {adapter.adapter_id} 存在但未启用；运行时会退回 native/人工确认路径。",
            })
        if adapter.health_status in {"missing_config", "unknown"}:
            gaps.append({
                "id": f"agent_adapter_unready:{agent.agent_id}",
                "priority": "medium",
                "title": f"{agent.label} 的 Adapter 未就绪",
                "detail": f"Adapter {adapter.adapter_id}: {adapter.health_detail}",
            })
    if state.skill_assist.enabled:
        selected_id = state.skill_assist.selected_assistant_id
        selected = next((assistant for assistant in state.external_assistants if assistant.assistant_id == selected_id), None)
        if selected is None or not selected.enabled:
            gaps.append({
                "id": "skill_assist_target_disabled",
                "priority": "medium",
                "title": "Skill 辅助评审目标未启用",
                "detail": "Skill assist 已开启，但选中的外部助手不可用。",
            })
    return gaps


def build_managed_agent_runtime_snapshot(state: AgentManagementState | None = None) -> dict[str, Any]:
    state = state or load_agent_management_state()
    knowledge_by_id = {item.knowledge_base_id: item.snapshot() for item in state.knowledge_bases}
    adapter_by_id = {item.adapter_id: item.snapshot() for item in state.agent_adapters}
    enabled_adapter_by_id = {item.adapter_id: item.snapshot() for item in state.agent_adapters if item.enabled}
    assistants_by_id = {item.assistant_id: item.snapshot() for item in state.external_assistants}
    enabled_assistants_by_id = {item.assistant_id: item.snapshot() for item in state.external_assistants if item.enabled}
    return {
        "framework": "spiritkin_unified_agent_cluster",
        "architecture": {
            "layers": ["chief_dispatcher", "specialist_agent", "worker_agent_or_executor"],
            "chief_agent_id": "main_text",
            "dispatcher": "Runtime security kernel + AgentCluster + BrainRouter + Coordinator/Router LLM",
            "specialist_runtime": "AgentCapabilityContainer + ManagedAgentConfig + AgentAdapter contract",
            "worker_runtime": "WorkerPool + ToolRegistry + BaseExecutor + external assistants",
            "openclaw_layer": "worker_executor",
        },
        "adapter_contract": {
            "input": ["task", "session_context", "memory_context", "allowed_tools", "risk_policy", "budget", "attachments"],
            "output": ["text", "plan", "tool_calls", "result", "confidence", "requires_confirmation", "metadata", "events"],
            "rule": "Agent adapters may use native, LangGraph, CrewAI, Codex, MCP or remote runtimes, but all tool/executor execution must return through ToolRegistry/ExecutionGuard and WorkerPool.",
        },
        "agents": [agent.snapshot() for agent in state.agents],
        "agent_profiles_by_id": {agent.agent_id: agent.snapshot() for agent in state.agents},
        "enabled_agent_ids": [agent.agent_id for agent in state.agents if agent.enabled],
        "disabled_agent_ids": [agent.agent_id for agent in state.agents if not agent.enabled],
        "agent_adapters_by_id": adapter_by_id,
        "enabled_agent_adapters_by_id": enabled_adapter_by_id,
        "adapter_by_agent": {
            agent.agent_id: adapter_by_id.get(agent.adapter, {})
            for agent in state.agents
        },
        "external_assistants_by_id": assistants_by_id,
        "enabled_external_assistants_by_id": enabled_assistants_by_id,
        "assistant_allowlist_by_agent": {agent.agent_id: list(agent.allowed_assistant_ids) for agent in state.agents},
        "knowledge_base_by_agent": {
            agent.agent_id: knowledge_by_id.get(agent.knowledge_base_id, {})
            for agent in state.agents
            if agent.knowledge_base_id
        },
    }


def export_remote_submodule(
    payload: dict[str, Any],
    *,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    target = Path(output_dir or os.getenv("SPIRITKIN_REMOTE_EXPORT_DIR", DEFAULT_REMOTE_EXPORT_DIR))
    if not target.is_absolute():
        target = Path.cwd() / target
    target.mkdir(parents=True, exist_ok=True)
    export_id = str(payload.get("export_id") or f"remote-export-{int(time.time())}")
    safe_id = "".join(ch for ch in export_id if ch.isalnum() or ch in {"-", "_"}) or f"remote-export-{int(time.time())}"
    package_path = target / f"{safe_id}.json"
    package = {
        "package_schema_version": REMOTE_PACKAGE_SCHEMA_VERSION,
        "export_id": safe_id,
        "created_at": time.time(),
        "target_id": str(payload.get("target_id") or ""),
        "module_type": str(payload.get("module_type") or "skill"),
        "skill_names": list(payload.get("skill_names") or []),
        "include_training_dataset": bool(payload.get("include_training_dataset", True)),
        "verification_commands": list(payload.get("verification_commands") or ["python -m unittest backend.tests.unit.test_command_gateway -v"]),
        "rollback": str(payload.get("rollback") or "Disable the imported module on the remote worker and restore the previous skill package."),
        "rollback_plan": _remote_package_rollback_plan(payload),
        "compatibility": _remote_package_compatibility(payload),
        "notes": str(payload.get("notes") or ""),
        "status": "package_created",
    }
    package["manifest"] = _remote_package_manifest(package, reviewer=str(payload.get("reviewer") or "desktop"))
    package["integrity"] = _remote_package_integrity(package)
    package["signature"] = build_remote_package_signature(package, signer=str(payload.get("reviewer") or "desktop"))
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "package_path": str(package_path), "package": package}


def push_remote_submodule(payload: dict[str, Any]) -> dict[str, Any]:
    safety = evaluate_execution_safety(
        target="remote_submodule",
        operation="push_remote",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}
    package_path, package = _resolve_remote_package(payload)
    integrity = _verify_remote_package_integrity(package)
    target = _resolve_remote_target(payload, package=package)
    response = _post_remote_json(
        target["base_url"],
        "/remote-package/import",
        {"package": package, "source_package_path": str(package_path), "integrity": integrity},
        auth_token=_resolve_remote_target_token(target, payload),
    )
    return {
        "ok": bool(response.get("ok", False)),
        "target": target,
        "package_path": str(package_path),
        "integrity": integrity,
        "remote_response": response,
    }


def execute_remote_submodule(payload: dict[str, Any]) -> dict[str, Any]:
    safety = evaluate_execution_safety(
        target="remote_submodule",
        operation="execute_remote",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}
    package_path, package = _resolve_remote_package(payload)
    integrity = _verify_remote_package_integrity(package)
    target = _resolve_remote_target(payload, package=package)
    response = _post_remote_json(
        target["base_url"],
        "/remote-package/execute",
        {
            "package": package,
            "package_id": str(payload.get("package_id") or package.get("export_id") or ""),
            "run_verification": bool(payload.get("run_verification", True)),
            "integrity": integrity,
        },
        auth_token=_resolve_remote_target_token(target, payload),
    )
    return {
        "ok": bool(response.get("ok", False)),
        "target": target,
        "package_path": str(package_path),
        "integrity": integrity,
        "remote_response": response,
    }


def rollback_remote_submodule(payload: dict[str, Any]) -> dict[str, Any]:
    safety = evaluate_execution_safety(
        target="remote_submodule",
        operation="rollback_remote",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}
    package_path, package = _resolve_remote_package(payload)
    target = _resolve_remote_target(payload, package=package)
    response = _post_remote_json(
        target["base_url"],
        "/remote-package/rollback",
        {
            "package_id": str(payload.get("package_id") or package.get("export_id") or ""),
            "require_signature": bool(payload.get("require_signature", True)),
        },
        auth_token=_resolve_remote_target_token(target, payload),
    )
    return {
        "ok": bool(response.get("ok", False)),
        "target": target,
        "package_path": str(package_path),
        "remote_response": response,
    }


def _resolve_remote_package(payload: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    package_payload = payload.get("package")
    if isinstance(package_payload, dict):
        package_path = Path(str(payload.get("package_path") or ""))
        package = dict(package_payload)
        _verify_remote_package_compatibility(package)
        _verify_remote_package_integrity(package)
        _verify_remote_package_signature(package)
        return package_path, package

    raw_path = str(payload.get("package_path") or "").strip()
    if raw_path:
        package_path = Path(raw_path)
        if not package_path.is_absolute():
            package_path = Path.cwd() / package_path
    else:
        export_dir = Path(os.getenv("SPIRITKIN_REMOTE_EXPORT_DIR", DEFAULT_REMOTE_EXPORT_DIR))
        if not export_dir.is_absolute():
            export_dir = Path.cwd() / export_dir
        packages = sorted(export_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not packages:
            raise FileNotFoundError("no remote export package found")
        package_path = packages[0]

    package_path = package_path.resolve()
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise ValueError("remote package must be a JSON object")
    _verify_remote_package_compatibility(package)
    _verify_remote_package_integrity(package)
    _verify_remote_package_signature(package)
    return package_path, package


def _resolve_remote_target(payload: dict[str, Any], *, package: dict[str, Any]) -> dict[str, str]:
    state = load_agent_management_state()
    target_id = str(payload.get("target_id") or package.get("target_id") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    target = next((item for item in state.remote_targets if item.target_id == target_id), None)
    if target is not None:
        if base_url and base_url.rstrip("/") != target.base_url.rstrip("/"):
            raise ValueError("remote target base_url override does not match the registered target")
        base_url = target.base_url
        label = target.label
        token_set = target.token_set
    else:
        label = target_id or base_url or "remote"
        token_set = False
    if not base_url:
        raise ValueError("missing remote target base_url")
    base_url = _validated_remote_base_url(base_url, resolve_host=False)
    return {
        "target_id": target_id,
        "label": label,
        "base_url": base_url.rstrip("/"),
        "token_set": str(bool(token_set)).lower(),
    }


def _resolve_remote_target_token(target: dict[str, str], payload: dict[str, Any]) -> str:
    explicit = str(payload.get("auth_token") or payload.get("token") or "").strip()
    if explicit:
        return explicit
    target_id = str(target.get("target_id") or "").strip()
    if target_id:
        env_name = "SPIRITKIN_REMOTE_TARGET_" + "".join(ch if ch.isalnum() else "_" for ch in target_id.upper()) + "_TOKEN"
        token = os.getenv(env_name, "").strip()
        if token:
            return token
    if not target_id:
        return ""
    return os.getenv("SPIRITKIN_REMOTE_WORKER_TOKEN", "").strip() or os.getenv("SPIRITKIN_REMOTE_TOKEN", "").strip()


def _validated_remote_base_url(value: str, *, resolve_host: bool = True) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = str(parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("remote target must be a credential-free origin")
    allowed_hosts = {item.strip().lower() for item in os.getenv("SPIRITKIN_REMOTE_TARGET_ALLOWED_HOSTS", "").split(",") if item.strip()}
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("remote target must use HTTPS; HTTP is allowed only for loopback")
    if not loopback and host not in allowed_hosts:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError("remote target resolves to a private or special-use address")
        if resolve_host and literal is None:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
            except OSError as exc:
                raise ValueError("remote target host could not be resolved") from exc
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise ValueError("remote target resolves to a private or special-use address")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


class _NoRemoteRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _remote_package_manifest(package: dict[str, Any], *, reviewer: str) -> dict[str, Any]:
    return {
        "schema_version": package.get("package_schema_version", REMOTE_PACKAGE_SCHEMA_VERSION),
        "export_id": str(package.get("export_id") or ""),
        "module_type": str(package.get("module_type") or ""),
        "target_id": str(package.get("target_id") or ""),
        "skill_names": list(package.get("skill_names") or []),
        "verification_commands": list(package.get("verification_commands") or []),
        "rollback_plan": dict(package.get("rollback_plan") or {}),
        "compatibility": dict(package.get("compatibility") or {}),
        "created_by": reviewer or "desktop",
    }


def _remote_package_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "worker_api_version": str(payload.get("worker_api_version") or "spiritkin.remote_worker.v1"),
        "supported_package_schema_versions": list(SUPPORTED_REMOTE_PACKAGE_SCHEMA_VERSIONS),
        "required_worker_features": [
            "remote_package_import",
            "sha256_integrity",
            "rollback_plan",
            "verification_commands",
        ],
        "import_endpoints": ["/remote-package/import", "/remote-package/execute", "/remote-package/rollback"],
        "requires_review_gate": True,
    }


def _remote_package_rollback_plan(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": str(payload.get("rollback") or "Disable the imported module and restore the previous package."),
        "steps": [
            "Disable the imported module on the remote worker.",
            "Restore the previous skill package or registry snapshot.",
            "Re-run verification commands before re-enabling write access.",
        ],
        "owner": str(payload.get("reviewer") or "desktop"),
    }


def _remote_package_integrity(package: dict[str, Any]) -> dict[str, Any]:
    canonical = _remote_package_canonical_json(package)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "algorithm": "sha256",
        "digest": digest,
        "canonical_length": len(canonical),
        "verified": True,
    }


def _remote_package_canonical_json(package: dict[str, Any]) -> str:
    return remote_package_canonical_json(package)


def _verify_remote_package_integrity(package: dict[str, Any]) -> dict[str, Any]:
    integrity = dict(package.get("integrity") or {})
    expected = str(integrity.get("digest") or "").strip().lower()
    algorithm = str(integrity.get("algorithm") or "sha256").strip().lower()
    if algorithm not in {"sha256", ""}:
        raise ValueError(f"unsupported remote package integrity algorithm: {algorithm}")
    if not expected:
        raise ValueError("remote package integrity digest missing")
    actual = hashlib.sha256(_remote_package_canonical_json(package).encode("utf-8")).hexdigest()
    if expected and expected != actual:
        raise ValueError("remote package integrity mismatch")
    expected_length = int(integrity.get("canonical_length") or 0)
    actual_length = len(_remote_package_canonical_json(package))
    if expected_length and expected_length != actual_length:
        raise ValueError("remote package integrity length mismatch")
    return {
        "algorithm": "sha256",
        "expected_digest": expected,
        "actual_digest": actual,
        "canonical_length": actual_length,
        "verified": True,
    }


def _verify_remote_package_signature(package: dict[str, Any]) -> dict[str, Any]:
    return verify_remote_package_signature(package, require_signature=False)


def _verify_remote_package_compatibility(package: dict[str, Any]) -> dict[str, Any]:
    schema_version = str(package.get("package_schema_version") or "").strip()
    if schema_version not in SUPPORTED_REMOTE_PACKAGE_SCHEMA_VERSIONS:
        supported = ", ".join(SUPPORTED_REMOTE_PACKAGE_SCHEMA_VERSIONS)
        raise ValueError(f"unsupported remote package schema: {schema_version or 'missing'}; supported: {supported}")
    manifest = package.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("remote package manifest missing")
    compatibility = package.get("compatibility")
    if not isinstance(compatibility, dict):
        raise ValueError("remote package compatibility metadata missing")
    rollback_plan = package.get("rollback_plan")
    if not isinstance(rollback_plan, dict) or not rollback_plan.get("steps"):
        raise ValueError("remote package rollback plan missing")
    verification_commands = package.get("verification_commands")
    if verification_commands is not None and not isinstance(verification_commands, list):
        raise ValueError("remote package verification_commands must be a list")
    required_features = {str(item).strip() for item in compatibility.get("required_worker_features") or [] if str(item).strip()}
    missing_features = {"sha256_integrity", "rollback_plan"} - required_features
    if missing_features:
        raise ValueError(f"remote package compatibility missing required features: {', '.join(sorted(missing_features))}")
    return {
        "schema_version": schema_version,
        "worker_api_version": str(compatibility.get("worker_api_version") or ""),
        "required_worker_features": sorted(required_features),
        "compatible": True,
    }


def _post_remote_json(base_url: str, path: str, payload: dict[str, Any], *, auth_token: str = "", timeout_seconds: float = 15.0) -> dict[str, Any]:
    base_url = _validated_remote_base_url(base_url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(f"{base_url.rstrip('/')}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if auth_token:
        req.add_header(REMOTE_AUTH_HEADER, auth_token)
    try:
        with request.build_opener(_NoRemoteRedirectHandler()).open(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8") or "{}"
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp is not None else "{}"
        try:
            failure = json.loads(body or "{}")
        except Exception:
            failure = {"ok": False, "error": body or str(exc)}
        raise RuntimeError(str(failure.get("error") or exc)) from exc
    return json.loads(body or "{}")
