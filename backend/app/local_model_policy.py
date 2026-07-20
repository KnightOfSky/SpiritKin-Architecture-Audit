from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOCAL_MODEL_POLICY_SCHEMA_VERSION = "spiritkin.local_model_policy.v1"
SCHEDULER_BENCHMARK_SCHEMA_VERSION = "spiritkin.scheduler_benchmark.v1"
DEFAULT_SCHEDULER_BENCHMARK_HISTORY_PATH = "state/model_scheduler_benchmarks.jsonl"


@dataclass(frozen=True)
class LocalHardwareProfile:
    vram_gb: float = 16.0
    ram_gb: float = 64.0
    gpu_count: int = 1
    platform: str = "local_desktop"

    def snapshot(self) -> dict[str, Any]:
        return {
            "vram_gb": self.vram_gb,
            "ram_gb": self.ram_gb,
            "gpu_count": self.gpu_count,
            "platform": self.platform,
            "hardware_class": classify_local_hardware(self),
        }


@dataclass(frozen=True)
class LocalModelRoleAssignment:
    role_id: str
    label: str
    model_id: str
    model_family: str
    architecture: str
    role_scope: tuple[str, ...]
    quantization_profile: str
    vram_policy: str
    priority: int = 50
    concurrent_with_large_model: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "label": self.label,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "architecture": self.architecture,
            "role_scope": list(self.role_scope),
            "quantization_profile": self.quantization_profile,
            "vram_policy": self.vram_policy,
            "priority": self.priority,
            "concurrent_with_large_model": self.concurrent_with_large_model,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SchedulerBenchmarkCase:
    case_id: str
    category: str
    prompt: str
    expected_route: str = ""
    expected_json_keys: tuple[str, ...] = ()
    required_tool_calls: tuple[str, ...] = ()
    expected_workflow_steps: tuple[str, ...] = ()
    required_context_ids: tuple[str, ...] = ()
    max_context_drift_items: int = 0
    weight: float = 1.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "prompt": self.prompt,
            "expected_route": self.expected_route,
            "expected_json_keys": list(self.expected_json_keys),
            "required_tool_calls": list(self.required_tool_calls),
            "expected_workflow_steps": list(self.expected_workflow_steps),
            "required_context_ids": list(self.required_context_ids),
            "max_context_drift_items": self.max_context_drift_items,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class SchedulerBenchmarkEvaluation:
    case_id: str
    category: str
    passed: bool
    score: float
    findings: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "score": round(float(self.score), 2),
            "findings": list(self.findings),
        }


def build_local_model_policy_snapshot(
    *,
    model_catalog: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
    hardware: LocalHardwareProfile | None = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    hardware = hardware or detect_policy_hardware(env)
    role_assignments = build_local_model_role_assignments(model_catalog=model_catalog or {}, hardware=hardware, environ=env)
    benchmark_cases = default_scheduler_benchmark_cases()
    benchmark_summary = summarize_scheduler_benchmarks(benchmark_cases)
    history = load_scheduler_benchmark_history(limit=5, environ=env)
    if history:
        benchmark_summary = {
            **benchmark_summary,
            "last_run": history[0].get("created_at"),
            "status": "passed" if history[0].get("passed") else "failed",
            "history_count": len(history),
            "history": history,
        }
    return {
        "schema_version": LOCAL_MODEL_POLICY_SCHEMA_VERSION,
        "hardware": hardware.snapshot(),
        "policy": {
            "single_active_large_model": hardware.vram_gb < 48 or hardware.gpu_count <= 1,
            "default_mode": "single_local_scheduler" if hardware.vram_gb < 48 else "dual_local_models_available",
            "large_model_concurrency": "sequential" if hardware.vram_gb < 48 else "allowed_after_benchmark",
            "role_split": "35B-A3B scheduler/master; 27B specialist candidate for code/game when hardware and benchmark allow it.",
            "q5_allowed_without_benchmark": hardware.vram_gb >= 24,
        },
        "role_assignments": [assignment.snapshot() for assignment in role_assignments],
        "scheduler_benchmark": benchmark_summary,
        "quality_gates": {
            "json_validity_min": 0.95,
            "tool_call_accuracy_min": 0.9,
            "workflow_step_completeness_min": 0.88,
            "context_drift_max": 0.08,
            "promotion_requires_structured_results": True,
        },
        "notes": [
            "On 16GB VRAM, treat all 27B/35B local profiles as sequential large-model roles unless the service reports measured headroom.",
            "Q4 is the default safe quantization profile; Q5 requires benchmark evidence or higher VRAM headroom.",
            "Cloud reviewers remain preferred for high-risk promotion gates even when local scheduler benchmarks pass.",
        ],
    }


def detect_policy_hardware(environ: dict[str, str] | None = None) -> LocalHardwareProfile:
    env = environ if environ is not None else os.environ
    return LocalHardwareProfile(
        vram_gb=_float_env(env, "SPIRITKIN_VRAM_GB", 16.0),
        ram_gb=_float_env(env, "SPIRITKIN_RAM_GB", 64.0),
        gpu_count=max(1, int(_float_env(env, "SPIRITKIN_GPU_COUNT", 1.0))),
        platform=str(env.get("SPIRITKIN_LOCAL_MODEL_PLATFORM") or "local_desktop"),
    )


def classify_local_hardware(hardware: LocalHardwareProfile) -> str:
    if hardware.gpu_count >= 2 and hardware.vram_gb >= 48:
        return "multi_gpu_large"
    if hardware.vram_gb >= 24:
        return "single_gpu_24gb"
    if hardware.vram_gb >= 16:
        return "single_gpu_16gb"
    return "cpu_or_small_gpu"


def build_local_model_role_assignments(
    *,
    model_catalog: dict[str, Any],
    hardware: LocalHardwareProfile,
    environ: dict[str, str] | None = None,
) -> tuple[LocalModelRoleAssignment, ...]:
    env = environ if environ is not None else os.environ
    model_ids = {str(item.get("model_id") or ""): dict(item) for item in model_catalog.get("models") or [] if isinstance(item, dict)}
    scheduler_model = str(env.get("SPIRITKIN_LOCAL_SCHEDULER_MODEL") or _first_known_model(model_ids, ("Qwen/Qwen3.6-35B-A3B-Instruct", "Qwen/Qwen3.6-35B-A3B")) or "Qwen/Qwen3.6-35B-A3B-Instruct")
    specialist_model = str(env.get("SPIRITKIN_LOCAL_27B_MODEL") or _first_known_model(model_ids, ("Qwen/Qwen3.6-27B", "Qwen/Qwen3.5-27B")) or "Qwen/Qwen3.6-27B")
    q_profile = _quantization_for_hardware(hardware)
    sequential = hardware.vram_gb < 48 or hardware.gpu_count <= 1
    assignments = [
        LocalModelRoleAssignment(
            role_id="local_scheduler_master",
            label="Local Scheduler / Master",
            model_id=scheduler_model,
            model_family="Qwen3.6-35B-A3B",
            architecture="MoE",
            role_scope=("routing", "planning", "tool_calling", "commerce_general", "normal_dialogue"),
            quantization_profile=q_profile,
            vram_policy=_vram_policy(hardware, q_profile),
            priority=100,
            concurrent_with_large_model=not sequential,
            metadata={
                "parameter_hint_b": 36,
                "active_parameter_hint_b": 3,
                "default_for_single_model_mode": True,
                "requires_cloud_review_for_high_risk": True,
            },
        ),
        LocalModelRoleAssignment(
            role_id="local_27b_specialist",
            label="Local 27B Specialist Candidate",
            model_id=specialist_model,
            model_family="Qwen3.6-27B",
            architecture="dense_or_compact_multimodal",
            role_scope=("programming", "game_development", "ui_reasoning", "specialist_followup"),
            quantization_profile=q_profile if hardware.vram_gb < 24 else "Q5_K_M",
            vram_policy=_vram_policy(hardware, q_profile if hardware.vram_gb < 24 else "Q5_K_M"),
            priority=90,
            concurrent_with_large_model=not sequential,
            metadata={
                "parameter_hint_b": 28,
                "default_for_single_model_mode": False,
                "promotion_requires_scheduler_benchmark": True,
            },
        ),
    ]
    return tuple(assignments)


def default_scheduler_benchmark_cases() -> tuple[SchedulerBenchmarkCase, ...]:
    return (
        SchedulerBenchmarkCase(
            case_id="json_validity_route_plan",
            category="json_validity",
            prompt="Return a strict JSON route decision for opening a browser and searching the knowledge base.",
            expected_route="tool",
            expected_json_keys=("route", "tool_calls", "workflow_steps", "confidence"),
        ),
        SchedulerBenchmarkCase(
            case_id="tool_call_accuracy_browser",
            category="tool_call_accuracy",
            prompt="Open https://example.com and then summarize the page.",
            expected_route="executor",
            required_tool_calls=("browser.open_url",),
            expected_json_keys=("route", "tool_calls"),
        ),
        SchedulerBenchmarkCase(
            case_id="workflow_step_completeness_publish",
            category="workflow_step_completeness",
            prompt="Plan a governed ecommerce product publish workflow with review before upload.",
            expected_route="workflow",
            expected_workflow_steps=("intake", "asset_check", "review_gate", "upload_product"),
            expected_json_keys=("route", "workflow_steps"),
        ),
        SchedulerBenchmarkCase(
            case_id="context_drift_followup",
            category="context_drift",
            prompt="Given prior task id order-42 and project ecom-demo, answer the follow-up without switching projects.",
            expected_route="agent",
            required_context_ids=("order-42", "ecom-demo"),
            max_context_drift_items=0,
            expected_json_keys=("route", "context_retained_ids"),
        ),
    )


def summarize_scheduler_benchmarks(cases: Iterable[SchedulerBenchmarkCase]) -> dict[str, Any]:
    case_list = list(cases)
    categories = sorted({case.category for case in case_list})
    return {
        "schema_version": SCHEDULER_BENCHMARK_SCHEMA_VERSION,
        "case_count": len(case_list),
        "categories": categories,
        "cases": [case.snapshot() for case in case_list],
        "last_run": None,
        "status": "not_run",
    }


def evaluate_scheduler_benchmark_case(case: SchedulerBenchmarkCase, output: dict[str, Any] | str) -> SchedulerBenchmarkEvaluation:
    payload, json_ok = _payload_from_output(output)
    findings: list[str] = []
    score_parts: list[float] = []
    if not json_ok:
        findings.append("output is not valid JSON")
        return SchedulerBenchmarkEvaluation(case.case_id, case.category, False, 0.0, tuple(findings))
    if case.expected_json_keys:
        present = sum(1 for key in case.expected_json_keys if key in payload)
        score_parts.append(present / max(1, len(case.expected_json_keys)))
        missing = [key for key in case.expected_json_keys if key not in payload]
        if missing:
            findings.append(f"missing JSON keys: {', '.join(missing)}")
    if case.expected_route:
        route_ok = str(payload.get("route") or "") == case.expected_route
        score_parts.append(1.0 if route_ok else 0.0)
        if not route_ok:
            findings.append(f"expected route {case.expected_route}, got {payload.get('route')}")
    if case.required_tool_calls:
        actual = set(_tool_call_names(payload.get("tool_calls")))
        required = set(case.required_tool_calls)
        score_parts.append(len(actual & required) / max(1, len(required)))
        missing = sorted(required - actual)
        if missing:
            findings.append(f"missing tool calls: {', '.join(missing)}")
    if case.expected_workflow_steps:
        actual_steps = _workflow_step_names(payload.get("workflow_steps"))
        score_parts.append(_ordered_step_score(case.expected_workflow_steps, actual_steps))
        missing_steps = [step for step in case.expected_workflow_steps if step not in actual_steps]
        if missing_steps:
            findings.append(f"missing workflow steps: {', '.join(missing_steps)}")
    if case.required_context_ids:
        retained = {str(item) for item in payload.get("context_retained_ids") or []}
        required_context = set(case.required_context_ids)
        score_parts.append(len(retained & required_context) / max(1, len(required_context)))
        drift_items = [str(item) for item in payload.get("irrelevant_context_ids") or []]
        if len(drift_items) > case.max_context_drift_items:
            findings.append(f"context drift items exceeded: {len(drift_items)}")
            score_parts.append(0.0)
    score = sum(score_parts or [1.0]) / max(1, len(score_parts or [1.0])) * 100.0
    passed = score >= 90.0 and not findings
    return SchedulerBenchmarkEvaluation(case.case_id, case.category, passed, score, tuple(findings))


def evaluate_scheduler_benchmark_suite(outputs_by_case_id: dict[str, dict[str, Any] | str]) -> dict[str, Any]:
    cases = default_scheduler_benchmark_cases()
    evaluations = [evaluate_scheduler_benchmark_case(case, outputs_by_case_id.get(case.case_id, {})) for case in cases]
    score = sum(item.score * cases[index].weight for index, item in enumerate(evaluations)) / max(1.0, sum(case.weight for case in cases))
    category_scores: dict[str, list[float]] = {}
    for item in evaluations:
        category_scores.setdefault(item.category, []).append(item.score)
    return {
        "schema_version": SCHEDULER_BENCHMARK_SCHEMA_VERSION,
        "score": round(score, 2),
        "passed": score >= 90.0 and all(item.passed for item in evaluations),
        "category_scores": {
            category: round(sum(values) / max(1, len(values)), 2)
            for category, values in category_scores.items()
        },
        "evaluations": [item.snapshot() for item in evaluations],
    }


def record_scheduler_benchmark_result(
    result: dict[str, Any],
    *,
    outputs_by_case_id: dict[str, dict[str, Any] | str] | None = None,
    path: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    target = resolve_scheduler_benchmark_history_path(path, environ=environ)
    record = {
        "schema_version": SCHEDULER_BENCHMARK_SCHEMA_VERSION,
        "created_at": time.time(),
        "score": float(result.get("score") or 0.0),
        "passed": bool(result.get("passed")),
        "category_scores": dict(result.get("category_scores") or {}),
        "evaluation_count": len(result.get("evaluations") or []),
        "input_case_ids": sorted(str(key) for key in (outputs_by_case_id or {}).keys()),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {**record, "path": str(target)}


def load_scheduler_benchmark_history(
    *,
    limit: int = 10,
    path: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    target = resolve_scheduler_benchmark_history_path(path, environ=environ)
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
        if len(records) >= max(1, int(limit)):
            break
    return records


def resolve_scheduler_benchmark_history_path(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Path:
    env = environ if environ is not None else os.environ
    raw = path or env.get("SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH") or DEFAULT_SCHEDULER_BENCHMARK_HISTORY_PATH
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def _float_env(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key) or default)
    except (TypeError, ValueError):
        return default


def _first_known_model(models_by_id: dict[str, dict[str, Any]], candidates: tuple[str, ...]) -> str:
    for model_id in candidates:
        if model_id in models_by_id:
            return model_id
    lowered = {key.lower(): key for key in models_by_id}
    for model_id in candidates:
        found = lowered.get(model_id.lower())
        if found:
            return found
    return ""


def _quantization_for_hardware(hardware: LocalHardwareProfile) -> str:
    if hardware.vram_gb >= 24:
        return "Q5_K_M"
    if hardware.vram_gb >= 16:
        return "Q4_K_M"
    return "Q4_0_cpu_offload"


def _vram_policy(hardware: LocalHardwareProfile, quantization: str) -> str:
    if hardware.vram_gb >= 48:
        return f"{quantization}: full GPU or dual-model mode after benchmark"
    if hardware.vram_gb >= 24:
        return f"{quantization}: single large model; benchmark before concurrent loading"
    if hardware.vram_gb >= 16:
        return f"{quantization}: single active model with partial offload; Q5 requires benchmark"
    return f"{quantization}: CPU-heavy fallback; prefer smaller local/cloud route"


def _payload_from_output(output: dict[str, Any] | str) -> tuple[dict[str, Any], bool]:
    if isinstance(output, dict):
        return output, True
    try:
        parsed = json.loads(str(output or ""))
    except json.JSONDecodeError:
        return {}, False
    return (parsed, True) if isinstance(parsed, dict) else ({}, False)


def _tool_call_names(tool_calls: Any) -> list[str]:
    names: list[str] = []
    for item in tool_calls if isinstance(tool_calls, list) else []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(str(item.get("name") or item.get("tool_name") or item.get("tool") or ""))
    return [name for name in names if name]


def _workflow_step_names(workflow_steps: Any) -> list[str]:
    names: list[str] = []
    for item in workflow_steps if isinstance(workflow_steps, list) else []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(str(item.get("name") or item.get("step_id") or item.get("id") or ""))
    return [name for name in names if name]


def _ordered_step_score(expected: tuple[str, ...], actual: list[str]) -> float:
    if not expected:
        return 1.0
    matched = [step for step in expected if step in actual]
    if not matched:
        return 0.0
    order_bonus = 1.0 if [step for step in actual if step in expected][: len(expected)] == list(expected) else 0.8
    return len(matched) / len(expected) * order_bonus
