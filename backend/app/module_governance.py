from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "spiritkin.module_governance.v1"
TEXT_EXTENSIONS = {".py", ".js", ".ts", ".html", ".css", ".cs", ".xaml", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ps1", ".sh"}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".html", ".css", ".cs", ".xaml", ".ps1", ".sh"}
DOC_EXTENSIONS = {".md", ".rst", ".txt"}
EXCLUDED_DIR_NAMES = {".git", ".pytest_cache", "__pycache__", "state", "tmp", "publish", "node_modules", ".venv", "venv"}
EXCLUDED_PATH_FRAGMENTS = {
    "backend/models",
    "frontend/models/spirit3d/reference",
    "desktop/SpiritKinDesktop/publish",
}


@dataclass(frozen=True)
class ModuleGovernanceSpec:
    module_id: str
    label: str
    path: str
    layer: str
    owner_role: str
    criticality: str
    runtime_surface: str
    description: str
    expected_controls: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    test_keywords: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "label": self.label,
            "path": self.path,
            "layer": self.layer,
            "owner_role": self.owner_role,
            "criticality": self.criticality,
            "runtime_surface": self.runtime_surface,
            "description": self.description,
            "expected_controls": list(self.expected_controls),
            "verification_commands": list(self.verification_commands),
            "test_keywords": list(self.test_keywords),
        }


@dataclass(frozen=True)
class ModuleGovernanceRecord:
    spec: ModuleGovernanceSpec
    exists: bool
    file_count: int = 0
    code_file_count: int = 0
    test_file_count: int = 0
    doc_reference_count: int = 0
    line_count: int = 0
    last_modified: float = 0.0
    public_package_init: bool = False
    control_status: dict[str, bool] = field(default_factory=dict)
    gaps: tuple[dict[str, Any], ...] = ()
    maturity_score: int = 0
    maturity_level: str = "unmanaged"
    risk_level: str = "high"
    improvement_actions: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.spec.snapshot(),
            "exists": self.exists,
            "file_count": self.file_count,
            "code_file_count": self.code_file_count,
            "test_file_count": self.test_file_count,
            "doc_reference_count": self.doc_reference_count,
            "line_count": self.line_count,
            "last_modified": self.last_modified,
            "public_package_init": self.public_package_init,
            "control_status": dict(self.control_status),
            "gaps": [dict(item) for item in self.gaps],
            "maturity_score": self.maturity_score,
            "maturity_level": self.maturity_level,
            "risk_level": self.risk_level,
            "improvement_actions": [dict(item) for item in self.improvement_actions],
        }


def default_module_specs() -> tuple[ModuleGovernanceSpec, ...]:
    return (
        ModuleGovernanceSpec("backend.entrypoint", "后端入口", "backend/main.py", "runtime", "Platform Owner", "high", "python -m backend.main", "后端主入口和启动链路边界。", ("tests", "docs"), ("python -m py_compile backend/main.py",), ("main", "settings")),
        ModuleGovernanceSpec("backend.app", "应用与桌面网关", "backend/app", "application", "Application Platform Owner", "critical", "HTTP/WebSocket desktop services", "运行时装配、命令网关、桌面管理接口、诊断和治理面。", ("tests", "docs", "runbook", "audit", "service_health"), ("python -m unittest backend.tests.unit.test_command_gateway -v",), ("command_gateway", "runtime", "desktop", "diagnostics", "ecosystem")),
        ModuleGovernanceSpec("backend.orchestrator", "Agent 编排中枢", "backend/orchestrator", "orchestration", "AI Orchestration Owner", "critical", "planner / agent cluster", "任务规划、路由、执行防护、Agent 性能和能力清单。", ("tests", "docs", "evals", "safety_gate"), ("python -m unittest backend.tests.unit.test_agent_cluster backend.tests.unit.test_runtime -v",), ("agent_cluster", "planner", "execution_guard", "capability_inventory")),
        ModuleGovernanceSpec("backend.agents", "专业 Agent", "backend/agents", "domain_ai", "Agent Capability Owner", "high", "specialist agents", "编程、视觉、游戏、电商、视频动画等专业 Agent。", ("tests", "docs", "evals"), ("python -m unittest backend.tests.unit.test_agent_cluster -v",), ("agent", "programming", "vision", "ecommerce", "game")),
        ModuleGovernanceSpec("backend.tools", "工具语义层", "backend/tools", "capability", "Tooling Owner", "critical", "ToolRegistry", "工具协议、默认工具注册、知识/桌面/移动/远端工具。", ("tests", "docs", "allowlist", "audit"), ("python -m unittest backend.tests.unit.test_tooling_and_remote -v",), ("tool", "registry", "mcp", "knowledge")),
        ModuleGovernanceSpec("backend.executors", "执行器", "backend/executors", "execution", "Execution Owner", "critical", "local/remote/mobile/device execution", "把规划动作落到本机、远端节点、移动端或设备执行面。", ("tests", "docs", "rollback", "permissions"), ("python -m unittest backend.tests.unit.test_local_pc_device backend.tests.unit.test_remote_worker -v",), ("executor", "remote", "local_pc")),
        ModuleGovernanceSpec("backend.devices", "设备适配层", "backend/devices", "hardware_abstraction", "Device Integration Owner", "high", "DeviceBackend registry", "本地 PC、Android、OpenClaw 等设备后端适配。", ("tests", "docs", "hardware_contract"), ("python -m unittest backend.tests.unit.test_local_pc_device backend.tests.unit.test_android_bridge -v",), ("device", "local_pc", "android")),
        ModuleGovernanceSpec("backend.action", "动作意图层", "backend/action", "domain_action", "Action Semantics Owner", "high", "atomic/device actions", "高层动作语义、原子操作和设备动作封装。", ("tests", "docs", "inventory"), ("python -m unittest backend.tests.unit.test_architecture_layers -v",), ("atomic", "action")),
        ModuleGovernanceSpec("backend.knowledge", "知识库", "backend/knowledge", "knowledge", "Knowledge Owner", "high", "KB ingest/retrieval/indexing", "知识摄取、切块、检索、索引、增量和 vault 连接。", ("tests", "docs", "index_health"), ("python -m unittest backend.tests.unit.test_knowledge_base_management backend.tests.unit.test_incremental_indexer -v",), ("knowledge", "index", "retriever", "incremental")),
        ModuleGovernanceSpec("backend.memory", "记忆系统", "backend/memory", "memory", "Memory Owner", "high", "short/long/workflow memory", "短期、长期、人格和工作流记忆。", ("tests", "docs", "retention_policy"), ("python -m unittest backend.tests.unit.test_workflow_memory backend.tests.unit.test_long_term_memory -v",), ("memory", "workflow", "long_term")),
        ModuleGovernanceSpec("backend.perception", "感知系统", "backend/perception", "perception", "Perception Owner", "high", "audio/vision/screen input", "音频监听、实时语音、屏幕、OCR 和视觉理解。", ("tests", "docs", "device_matrix"), ("python -m unittest backend.tests.unit.test_audio_listener backend.tests.unit.test_streaming_listener backend.tests.unit.test_screen_io -v",), ("audio", "screen", "vision", "streaming")),
        ModuleGovernanceSpec("backend.expression", "表达系统", "backend/expression", "experience", "Avatar Experience Owner", "high", "TTS/avatar assets", "TTS、Avatar、音素桥、Edge TTS 和外壳表现。", ("tests", "docs", "asset_validation"), ("python -m unittest backend.tests.unit.test_tts_provider backend.tests.unit.test_avatar_assets -v",), ("avatar", "tts", "speech", "shell")),
        ModuleGovernanceSpec("backend.services", "服务客户端", "backend/services", "integration", "Integration Owner", "medium", "external service clients", "Feishu、Ollama、OpenClaw 和会话服务客户端。", ("tests", "docs", "contract_tests"), ("python -m unittest backend.tests.unit.test_conversation_engine -v",), ("service", "conversation", "feishu")),
        ModuleGovernanceSpec("backend.security", "安全与权限", "backend/security", "security", "Security Owner", "critical", "policy/audit/rate limit", "权限策略、审计、能力授权、用户身份和速率限制。", ("tests", "docs", "threat_model", "audit"), ("python -m unittest backend.tests.unit.test_permissions -v",), ("security", "permission", "policy", "audit")),
        ModuleGovernanceSpec("backend.evaluation", "评测与自改进", "backend/evaluation", "quality", "Quality Owner", "high", "replay/eval/failure loop", "回放、轨迹、失败库、Skill verifier 和自改进报告。", ("tests", "docs", "regression_suite"), ("python -m unittest backend.tests.unit.test_eval_phase2 backend.tests.unit.test_replay_harness -v",), ("eval", "replay", "trajectory", "self_training")),
        ModuleGovernanceSpec("backend.model.training", "训练工作台", "backend/model/training", "mlops", "MLOps Owner", "medium", "dataset/cloud package", "训练数据构建、云端训练包、LoRA 命令和训练工作台。", ("tests", "docs", "data_governance"), ("python -m unittest backend.tests.unit.test_training_workbench -v",), ("training", "dataset", "workbench")),
        ModuleGovernanceSpec("backend.skills", "Skill 系统", "backend/skills", "automation", "Skill Owner", "high", "Skill registry/runner/promotion", "Skill 规格、执行、持久化和候选升级。", ("tests", "docs", "promotion_gate"), ("python -m unittest backend.tests.unit.test_skill_layer backend.tests.unit.test_skill_promotion -v",), ("skill", "promotion", "workflow")),
        ModuleGovernanceSpec("backend.runtime.events", "事件持久化", "backend/runtime/events", "events", "Runtime Events Owner", "medium", "event persistence", "运行事件持久化和同步底座。", ("tests", "docs", "schema_versioning"), ("python -m py_compile backend/runtime/events/persistence.py",), ("event", "persistence")),
        ModuleGovernanceSpec("backend.mobile", "移动端桥接", "backend/mobile", "mobile", "Mobile Owner", "high", "iOS/Android bridge", "iOS Shortcuts、Android endpoint、移动端命令和推送桥接。", ("tests", "docs", "device_matrix"), ("python -m unittest backend.tests.unit.test_ios_bridge backend.tests.unit.test_android_bridge -v",), ("ios", "android", "mobile")),
        ModuleGovernanceSpec("backend.remote", "远端 Worker", "backend/remote", "distributed_execution", "Remote Execution Owner", "high", "remote worker/poller", "远端 worker、远端包导入/执行和轮询链路。", ("tests", "docs", "auth", "rollback"), ("python -m unittest backend.tests.unit.test_remote_worker -v",), ("remote", "worker")),
        ModuleGovernanceSpec("backend.search", "搜索提供方", "backend/search", "integration", "Search Owner", "medium", "search providers", "搜索 provider 抽象和外部检索入口。", ("tests", "docs", "provider_contract"), ("python -m py_compile backend/search/base.py backend/search/providers.py",), ("search", "provider")),
        ModuleGovernanceSpec("backend.tests", "测试资产", "backend/tests", "quality", "QA Owner", "critical", "unit/manual tests", "单元测试、人工测试和架构验证。", ("coverage", "test_isolation", "manual_runbook"), ("python -m unittest discover backend.tests.unit -v",), ("test",)),
        ModuleGovernanceSpec("frontend", "Web 前端", "frontend", "frontend", "Frontend Owner", "high", "desktop console/avatar pages", "桌面控制台、Avatar 3D、Live2D、审计和回放页面。", ("smoke_tests", "docs", "asset_validation"), ("python scripts/validate_desktop_delivery.py",), ("frontend", "avatar", "desktop_console")),
        ModuleGovernanceSpec("desktop", "Windows 桌面壳", "desktop", "desktop", "Desktop Shell Owner", "high", "WPF/WebView shell", "WPF 桌面应用、WebView 承载、系统托盘和原生壳。", ("build", "docs", "release_artifact"), ("dotnet build desktop\\SpiritKinDesktop\\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false",), ("desktop", "shell", "wpf")),
        ModuleGovernanceSpec("scripts", "运维与验证脚本", "scripts", "operations", "DevOps Owner", "medium", "local scripts", "启动、smoke、验证、模型准备和 Blender 资产处理脚本。", ("smoke_tests", "docs", "idempotency"), ("python scripts/validate_desktop_delivery.py",), ("script", "smoke", "validate")),
        ModuleGovernanceSpec("docs", "项目文档", "docs", "documentation", "Documentation Owner", "high", "handoff/roadmap/runbook", "架构、交接、路线图、合规和运行说明。", ("freshness", "single_source_of_truth", "review_log"), ("python -m py_compile backend/app/project_overview.py",), ("doc", "handoff", "roadmap")),
        ModuleGovernanceSpec("deploy", "部署资产", "deploy", "deployment", "DevOps Owner", "medium", "Docker/compose", "Dockerfile、docker-compose 和部署说明。", ("smoke_tests", "security_scan", "docs"), ("python scripts/smoke_docker.py",), ("deploy", "docker")),
        ModuleGovernanceSpec("config", "配置", "config", "configuration", "Configuration Owner", "high", "config.yaml/.env", "运行配置、模型配置和环境变量示例。", ("schema", "secrets_policy", "docs"), ("python -m unittest backend.tests.unit.test_settings -v",), ("config", "settings")),
        ModuleGovernanceSpec("data", "本地数据样例", "data", "data", "Data Owner", "medium", "sample state/data", "OpenClaw 等本地样例状态和测试数据。", ("data_classification", "retention_policy"), ("python -m py_compile backend/model/training/data_builder.py",), ("data", "openclaw")),
    )


def build_module_governance_snapshot(project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    docs_index = _build_docs_index(root)
    tests = _list_test_files(root)
    records = [_assess_module(root, spec, docs_index=docs_index, tests=tests) for spec in default_module_specs()]
    module_snapshots = [record.snapshot() for record in records]
    backlog = _build_backlog(records)
    portfolio = _portfolio_summary(records, backlog)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "project_root": str(root),
        "portfolio": portfolio,
        "modules": module_snapshots,
        "improvement_backlog": backlog,
        "operating_model": enterprise_operating_model(),
    }


def enterprise_operating_model() -> dict[str, Any]:
    return {
        "cadence": {
            "daily": "Review service health, error logs, learning records, and pending ecosystem proposals.",
            "weekly": "Review high-risk module gaps, stale docs, and test coverage deltas.",
            "release": "Block release when a critical module has high risk, failing tests, or unmanaged external write paths.",
        },
        "required_controls": [
            "Each critical/high module has an owner_role and verification command.",
            "Code modules have targeted unit tests or smoke tests.",
            "Runtime/integration modules have a runbook or handoff document.",
            "External write and high-risk execution are gated by proposal approval.",
            "Learning and Skill changes are promoted through review, test, and rollback notes.",
        ],
        "risk_policy": {
            "critical_high_modules": "No production change without tests and a named verification command.",
            "medium_modules": "Manual review is enough when tests are not practical, but gaps stay visible in backlog.",
            "assets_and_data": "Use manifest, retention, and classification controls rather than unit-test-only scoring.",
        },
    }


def _assess_module(
    root: Path,
    spec: ModuleGovernanceSpec,
    *,
    docs_index: dict[str, str],
    tests: list[Path],
) -> ModuleGovernanceRecord:
    path = (root / spec.path).resolve()
    exists = path.exists()
    files = _list_module_files(path, root) if exists else []
    code_files = [item for item in files if item.suffix.lower() in CODE_EXTENSIONS]
    line_count = sum(_count_lines(item) for item in files if item.suffix.lower() in TEXT_EXTENSIONS)
    last_modified = max((item.stat().st_mtime for item in files if item.exists()), default=(path.stat().st_mtime if exists else 0.0))
    test_files = _matching_tests(spec, tests)
    doc_refs = _doc_reference_count(spec, docs_index)
    module_readme = _has_module_readme(path)
    public_init = _has_public_init(path, spec)
    control_status = _control_status(spec, exists=exists, test_count=len(test_files), doc_refs=doc_refs, public_init=public_init)
    gaps = tuple(
        _gaps_for_module(
            spec,
            exists=exists,
            control_status=control_status,
            file_count=len(files),
            code_file_count=len(code_files),
            module_readme=module_readme,
        )
    )
    score = _maturity_score(spec, exists=exists, control_status=control_status, gaps=gaps, file_count=len(files))
    risk = _risk_level(spec, gaps, score)
    actions = tuple(_improvement_actions(spec, gaps, risk))
    return ModuleGovernanceRecord(
        spec=spec,
        exists=exists,
        file_count=len(files),
        code_file_count=len(code_files),
        test_file_count=len(test_files),
        doc_reference_count=doc_refs,
        line_count=line_count,
        last_modified=last_modified,
        public_package_init=public_init,
        control_status=control_status,
        gaps=gaps,
        maturity_score=score,
        maturity_level=_maturity_level(score),
        risk_level=risk,
        improvement_actions=actions,
    )


def _list_module_files(path: Path, root: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path] if _include_file(path, root) else []
    files: list[Path] = []
    for item in path.rglob("*"):
        if item.is_file() and _include_file(item, root):
            files.append(item)
    return files


def _include_file(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    if any(fragment in relative for fragment in EXCLUDED_PATH_FRAGMENTS):
        return False
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    return path.stat().st_size <= 2_000_000


def _list_test_files(root: Path) -> list[Path]:
    tests_root = root / "backend" / "tests"
    if not tests_root.exists():
        return []
    return [path for path in tests_root.rglob("test_*.py") if path.is_file()]


def _matching_tests(spec: ModuleGovernanceSpec, tests: list[Path]) -> list[Path]:
    keywords = {item.lower() for item in spec.test_keywords or ()}
    keywords.add(spec.module_id.rsplit(".", 1)[-1].lower())
    matches: list[Path] = []
    for test in tests:
        name = test.stem.lower()
        if any(keyword and keyword in name for keyword in keywords):
            matches.append(test)
    return matches


def _build_docs_index(root: Path) -> dict[str, str]:
    docs_root = root / "docs"
    if not docs_root.exists():
        return {}
    index: dict[str, str] = {}
    for path in docs_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        try:
            index[path.as_posix()] = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
    readme = root / "README.md"
    if readme.exists():
        try:
            index[readme.as_posix()] = readme.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            pass
    return index


def _doc_reference_count(spec: ModuleGovernanceSpec, docs_index: dict[str, str]) -> int:
    needles = {
        spec.path.lower().replace("\\", "/"),
        spec.module_id.lower(),
        spec.module_id.replace(".", "/").lower(),
        spec.label.lower(),
    }
    needles.update(item.lower() for item in spec.test_keywords[:3])
    count = 0
    for text in docs_index.values():
        if any(needle and needle in text for needle in needles):
            count += 1
    return count


def _has_public_init(path: Path, spec: ModuleGovernanceSpec) -> bool:
    if not spec.path.startswith("backend/"):
        return True
    init_file = path / "__init__.py"
    return init_file.exists()


def _has_module_readme(path: Path) -> bool:
    return path.is_dir() and (path / "README.md").exists()


def _control_status(
    spec: ModuleGovernanceSpec,
    *,
    exists: bool,
    test_count: int,
    doc_refs: int,
    public_init: bool,
) -> dict[str, bool]:
    status: dict[str, bool] = {}
    for control in spec.expected_controls:
        if control in {"tests", "coverage", "smoke_tests", "build", "contract_tests"}:
            status[control] = test_count > 0 or bool(spec.verification_commands)
        elif control in {"docs", "runbook", "manual_runbook", "freshness", "single_source_of_truth"}:
            status[control] = doc_refs > 0
        elif control == "public_api":
            status[control] = public_init
        else:
            status[control] = exists
    status["exists"] = exists
    status["verification_command"] = bool(spec.verification_commands)
    return status


def _gaps_for_module(
    spec: ModuleGovernanceSpec,
    *,
    exists: bool,
    control_status: dict[str, bool],
    file_count: int,
    code_file_count: int,
    module_readme: bool = False,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not exists:
        gaps.append(_gap("missing_path", "high", "模块路径不存在", f"{spec.path} 不存在，台账与代码不一致。"))
        return gaps
    if code_file_count > 0 and not any(control_status.get(item, False) for item in ("tests", "coverage", "smoke_tests", "build", "contract_tests")):
        gaps.append(_gap("missing_tests", "high" if spec.criticality in {"critical", "high"} else "medium", "缺少可见测试控制", "代码模块没有匹配到测试或验证命令。"))
    if spec.criticality in {"critical", "high"} and not any(control_status.get(item, False) for item in ("docs", "runbook", "manual_runbook", "freshness", "single_source_of_truth")):
        gaps.append(_gap("missing_docs", "medium", "缺少文档/运行手册控制", "关键模块需要模块边界、运行手册或交接文档引用。"))
    if spec.path.startswith("backend/") and not control_status.get("public_api", True):
        gaps.append(_gap("missing_public_init", "low", "缺少包级 public API 标记", "后端包建议保留 __init__.py，明确导出边界。"))
    if spec.criticality == "critical" and not control_status.get("verification_command"):
        gaps.append(_gap("missing_verification", "high", "缺少验证命令", "关键模块需要能被 release checklist 直接调用的验证命令。"))
    if file_count > 60 and spec.criticality in {"critical", "high"} and not module_readme:
        gaps.append(_gap("large_module_surface", "medium", "模块表面积较大", "文件数量较多，应拆分子域 owner 或增加模块内 README。", {"file_count": file_count}))
    return gaps


def _gap(gap_id: str, severity: str, title: str, detail: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"gap_id": gap_id, "severity": severity, "title": title, "detail": detail, "evidence": dict(evidence or {})}


def _maturity_score(
    spec: ModuleGovernanceSpec,
    *,
    exists: bool,
    control_status: dict[str, bool],
    gaps: tuple[dict[str, Any], ...],
    file_count: int,
) -> int:
    if not exists:
        return 0
    score = 45
    if file_count:
        score += 8
    if any(control_status.get(item, False) for item in ("tests", "coverage", "smoke_tests", "build", "contract_tests")):
        score += 17
    if any(control_status.get(item, False) for item in ("docs", "runbook", "manual_runbook", "freshness", "single_source_of_truth")):
        score += 14
    if control_status.get("verification_command"):
        score += 8
    if control_status.get("public_api", True):
        score += 4
    if not gaps:
        score += 8
    for gap in gaps:
        severity = str(gap.get("severity") or "")
        if severity == "high":
            score -= 14
        elif severity == "medium":
            score -= 7
        else:
            score -= 3
    if spec.criticality == "critical" and gaps:
        score -= 5
    return int(max(0, min(100, score)))


def _maturity_level(score: int) -> str:
    if score >= 85:
        return "optimized"
    if score >= 70:
        return "enterprise_managed"
    if score >= 55:
        return "managed"
    if score >= 40:
        return "emerging"
    return "unmanaged"


def _risk_level(spec: ModuleGovernanceSpec, gaps: tuple[dict[str, Any], ...], score: int) -> str:
    severities = {str(gap.get("severity") or "") for gap in gaps}
    if "high" in severities and spec.criticality in {"critical", "high"}:
        return "high"
    if score < 50 and spec.criticality in {"critical", "high"}:
        return "high"
    if "medium" in severities or score < 65:
        return "medium"
    return "low"


def _improvement_actions(spec: ModuleGovernanceSpec, gaps: tuple[dict[str, Any], ...], risk_level: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for gap in gaps:
        gap_id = str(gap.get("gap_id") or "gap")
        priority = "high" if gap.get("severity") == "high" else ("medium" if gap.get("severity") == "medium" else "low")
        actions.append(
            {
                "action_id": f"module_governance:{spec.module_id}:{gap_id}",
                "module_id": spec.module_id,
                "owner_role": spec.owner_role,
                "priority": priority,
                "risk_level": risk_level,
                "title": f"{spec.label}: {gap.get('title')}",
                "detail": gap.get("detail"),
                "control": gap_id,
                "verification_commands": list(spec.verification_commands),
                "proposal_type": "manual.module_governance",
            }
        )
    return actions


def _build_backlog(records: list[ModuleGovernanceRecord]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for record in records:
        actions.extend(dict(item) for item in record.improvement_actions)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    risk_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(actions, key=lambda item: (priority_order.get(str(item.get("priority")), 9), risk_order.get(str(item.get("risk_level")), 9), str(item.get("module_id"))))


def _portfolio_summary(records: list[ModuleGovernanceRecord], backlog: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total:
        score = round(sum(record.maturity_score for record in records) / total)
    else:
        score = 0
    by_risk: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    by_maturity: dict[str, int] = {}
    critical_high_risk = 0
    for record in records:
        by_risk[record.risk_level] = by_risk.get(record.risk_level, 0) + 1
        by_layer[record.spec.layer] = by_layer.get(record.spec.layer, 0) + 1
        by_maturity[record.maturity_level] = by_maturity.get(record.maturity_level, 0) + 1
        if record.spec.criticality == "critical" and record.risk_level == "high":
            critical_high_risk += 1
    return {
        "score": int(score),
        "status": _portfolio_status(score, critical_high_risk),
        "module_count": total,
        "risk_counts": by_risk,
        "layer_counts": by_layer,
        "maturity_counts": by_maturity,
        "critical_high_risk_count": critical_high_risk,
        "high_priority_action_count": sum(1 for item in backlog if item.get("priority") == "high"),
        "top_risks": [
            {
                "module_id": record.spec.module_id,
                "label": record.spec.label,
                "risk_level": record.risk_level,
                "maturity_score": record.maturity_score,
                "gaps": [gap.get("gap_id") for gap in record.gaps],
            }
            for record in sorted(records, key=lambda item: (_risk_sort(item.risk_level), item.maturity_score, item.spec.module_id))[:8]
            if record.risk_level in {"high", "medium"}
        ],
    }


def _portfolio_status(score: int, critical_high_risk: int) -> str:
    if critical_high_risk:
        return "release_blocked"
    if score >= 82:
        return "enterprise_ready"
    if score >= 68:
        return "managed"
    if score >= 50:
        return "needs_governance"
    return "unmanaged"


def _risk_sort(risk: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(risk, 9)


def _count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0
