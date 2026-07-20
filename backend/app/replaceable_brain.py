from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

BRAIN_REPLACEMENT_SCHEMA_VERSION = "spiritkin.replaceable_brain.v1"
DEFAULT_BRAIN_ADAPTER_REGISTRY_PATH = "state/models/brain_adapters.json"


@dataclass(frozen=True)
class BrainAdapterRecord:
    adapter_id: str
    label: str
    adapter_type: str = "base_model"
    provider: str = ""
    model_id: str = ""
    base_model_id: str = ""
    artifact_path: str = ""
    status: str = "candidate"
    brain_profile_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    benchmark_suite_id: str = "default_capability_benchmark"
    review_state: str = "candidate"
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "label": self.label,
            "adapter_type": self.adapter_type,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_model_id": self.base_model_id,
            "artifact_path": self.artifact_path,
            "status": self.status,
            "brain_profile_ids": list(self.brain_profile_ids),
            "capability_ids": list(self.capability_ids),
            "benchmark_suite_id": self.benchmark_suite_id,
            "review_state": self.review_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityBenchmarkCase:
    case_id: str
    capability_id: str
    category: str
    prompt: str
    expected_assets: tuple[str, ...] = ()
    expected_output_keys: tuple[str, ...] = ()
    min_score: float = 85.0
    critical: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability_id": self.capability_id,
            "category": self.category,
            "prompt": self.prompt,
            "expected_assets": list(self.expected_assets),
            "expected_output_keys": list(self.expected_output_keys),
            "min_score": self.min_score,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class BrainReplacementDecision:
    allowed: bool
    status: str
    current_adapter_id: str
    candidate_adapter_id: str
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    benchmark_results: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": BRAIN_REPLACEMENT_SCHEMA_VERSION,
            "allowed": self.allowed,
            "status": self.status,
            "current_adapter_id": self.current_adapter_id,
            "candidate_adapter_id": self.candidate_adapter_id,
            "score": round(float(self.score), 2),
            "reasons": list(self.reasons),
            "required_actions": list(self.required_actions),
            "benchmark_results": [dict(item) for item in self.benchmark_results],
        }


def resolve_brain_adapter_registry_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_BRAIN_ADAPTER_REGISTRY_PATH", DEFAULT_BRAIN_ADAPTER_REGISTRY_PATH, path)


def default_capability_benchmark_cases() -> tuple[CapabilityBenchmarkCase, ...]:
    return (
        CapabilityBenchmarkCase(
            case_id="publish_product_workflow_assets",
            capability_id="publish_product",
            category="workflow_asset_reuse",
            prompt="Plan product publishing by reusing approved ecommerce workflow and review gate assets.",
            expected_assets=("workflow:content.ecommerce_product_publish.v1", "skill:publish.product", "policy:review_gate"),
            expected_output_keys=("route", "workflow_id", "capability_id", "review_gate"),
            min_score=88,
            critical=True,
        ),
        CapabilityBenchmarkCase(
            case_id="customer_reply_kb_grounding",
            capability_id="customer_reply",
            category="knowledge_grounding",
            prompt="Draft a customer reply using the KB policy and cite the retained customer/order context.",
            expected_assets=("kb:ecommerce", "policy:privacy", "skill:customer.reply"),
            expected_output_keys=("answer", "citations", "risk_level"),
            min_score=86,
            critical=True,
        ),
        CapabilityBenchmarkCase(
            case_id="run_tests_tool_boundary",
            capability_id="run_tests",
            category="tool_boundary",
            prompt="Run tests through the worker/tool boundary and report command, status, and artifacts.",
            expected_assets=("tool:test.run", "worker:local", "policy:safety"),
            expected_output_keys=("tool_calls", "worker_id", "status"),
            min_score=90,
            critical=True,
        ),
        CapabilityBenchmarkCase(
            case_id="generate_video_pipeline",
            capability_id="generate_video",
            category="multi_step_workflow",
            prompt="Create a governed video generation plan with asset intake, render worker, review, and export.",
            expected_assets=("workflow:content.video_generation.v1", "skill:generate.video", "policy:artifact_lifecycle"),
            expected_output_keys=("workflow_steps", "artifacts", "review_gate"),
            min_score=84,
            critical=False,
        ),
    )


def build_brain_replacement_snapshot(
    *,
    model_catalog: dict[str, Any] | None = None,
    capability_graph: dict[str, Any] | None = None,
    skills: list[dict[str, Any]] | None = None,
    workflows: dict[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    adapters = load_brain_adapter_registry(path=path, model_catalog=model_catalog)
    benchmark_cases = default_capability_benchmark_cases()
    capability_ids = _capability_ids_from_graph(capability_graph)
    return {
        "schema_version": BRAIN_REPLACEMENT_SCHEMA_VERSION,
        "independent_assets": {
            "capability_count": len(capability_ids),
            "capability_ids": sorted(capability_ids)[:24],
            "skill_count": len(skills or []),
            "workflow_count": _workflow_count(workflows),
            "knowledge_policy_independent": True,
            "skill_policy_independent": True,
            "workflow_policy_independent": True,
            "model_bound_assets_allowed": False,
        },
        "adapter_registry": adapters,
        "benchmark_suite": {
            "suite_id": "default_capability_benchmark",
            "case_count": len(benchmark_cases),
            "cases": [case.snapshot() for case in benchmark_cases],
        },
        "replacement_gate": {
            "minimum_average_score": 88.0,
            "critical_cases_must_pass": True,
            "requires_structured_benchmark_results": True,
            "requires_review_for_active_promotion": True,
            "auto_replace_allowed": False,
        },
        "capabilities": {
            "brain_adapter_registry": True,
            "lora_adapter_registry": True,
            "capability_benchmarks": True,
            "model_replacement_gate": True,
            "auto_replace_live_brain": False,
        },
    }


def load_brain_adapter_registry(
    *,
    path: str | os.PathLike[str] | None = None,
    model_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = resolve_brain_adapter_registry_path(path)
    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("adapters"), list):
            adapters = [normalize_brain_adapter(item).snapshot() for item in payload.get("adapters") if isinstance(item, dict)]
            return {
                "schema_version": BRAIN_REPLACEMENT_SCHEMA_VERSION,
                "path": str(target),
                "adapter_count": len(adapters),
                "adapters": adapters,
            }
    adapters = [adapter.snapshot() for adapter in default_brain_adapters(model_catalog=model_catalog or {})]
    return {
        "schema_version": BRAIN_REPLACEMENT_SCHEMA_VERSION,
        "path": str(target),
        "adapter_count": len(adapters),
        "adapters": adapters,
    }


def save_brain_adapter_registry(payload: dict[str, Any], *, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_brain_adapter_registry_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    adapters = [normalize_brain_adapter(item).snapshot() for item in payload.get("adapters") or [] if isinstance(item, dict)]
    saved = {
        "schema_version": BRAIN_REPLACEMENT_SCHEMA_VERSION,
        "updated_at": time.time(),
        "adapters": adapters,
    }
    target.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_brain_adapter_registry(path=path)


def handle_brain_replacement_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "get"}:
        return {"ok": True, "brain_replacement": build_brain_replacement_snapshot()}
    if action == "register_brain_adapter":
        registry = load_brain_adapter_registry()
        adapters = [dict(item) for item in registry.get("adapters") or [] if isinstance(item, dict)]
        record = normalize_brain_adapter(dict(payload.get("adapter") or payload))
        adapters = [item for item in adapters if str(item.get("adapter_id") or "") != record.adapter_id]
        adapters.append(record.snapshot())
        saved = save_brain_adapter_registry({"adapters": adapters})
        return {"ok": True, "adapter": record.snapshot(), "brain_replacement": build_brain_replacement_snapshot(path=saved.get("path"))}
    if action == "evaluate_brain_replacement":
        decision = evaluate_brain_replacement(
            current_adapter_id=str(payload.get("current_adapter_id") or ""),
            candidate_adapter=normalize_brain_adapter(dict(payload.get("candidate_adapter") or payload.get("adapter") or {})),
            benchmark_results=[dict(item) for item in payload.get("benchmark_results") or [] if isinstance(item, dict)],
            minimum_average_score=float(payload.get("minimum_average_score") or 88.0),
        )
        return {"ok": True, "brain_replacement_decision": decision.snapshot()}
    return {"ok": False, "error": f"unsupported brain replacement action: {action}"}


def evaluate_brain_replacement(
    *,
    current_adapter_id: str,
    candidate_adapter: BrainAdapterRecord,
    benchmark_results: list[dict[str, Any]],
    minimum_average_score: float = 88.0,
) -> BrainReplacementDecision:
    reasons: list[str] = []
    required_actions: list[str] = []
    normalized_results = [_normalize_benchmark_result(item) for item in benchmark_results]
    if not candidate_adapter.adapter_id:
        reasons.append("missing candidate adapter id")
    if candidate_adapter.adapter_type in {"lora", "qlora"} and not candidate_adapter.artifact_path:
        reasons.append("LoRA/QLoRA adapter requires artifact_path")
        required_actions.append("register_downloaded_adapter_artifact")
    if candidate_adapter.review_state not in {"reviewed", "approved", "active"}:
        required_actions.append("committee_or_human_review")
    if not normalized_results:
        reasons.append("missing structured benchmark results")
        required_actions.append("run_capability_benchmark_suite")
    score = sum(float(item.get("score") or 0) for item in normalized_results) / max(1, len(normalized_results))
    failed_critical = [item for item in normalized_results if bool(item.get("critical")) and not bool(item.get("passed"))]
    if score < minimum_average_score:
        reasons.append(f"average benchmark score {score:.2f} below {minimum_average_score:.2f}")
    if failed_critical:
        reasons.append(f"{len(failed_critical)} critical benchmark case(s) failed")
    allowed = not reasons and candidate_adapter.review_state in {"reviewed", "approved", "active"}
    return BrainReplacementDecision(
        allowed=allowed,
        status="approved_for_staging" if allowed else "blocked",
        current_adapter_id=current_adapter_id,
        candidate_adapter_id=candidate_adapter.adapter_id,
        score=score,
        reasons=tuple(reasons),
        required_actions=tuple(dict.fromkeys(required_actions)),
        benchmark_results=tuple(normalized_results),
    )


def default_brain_adapters(*, model_catalog: dict[str, Any]) -> tuple[BrainAdapterRecord, ...]:
    models = [dict(item) for item in model_catalog.get("models") or [] if isinstance(item, dict)]
    selected = [
        item for item in models
        if str(item.get("model_id") or "") in {"Qwen/Qwen3.6-35B-A3B-Instruct", "Qwen/Qwen3.6-27B", "Qwen/Qwen3-Coder-30B-A3B-Instruct"}
    ]
    adapters: list[BrainAdapterRecord] = []
    now = time.time()
    for item in selected:
        model_id = str(item.get("model_id") or "")
        policy = dict(item.get("metadata") or {}).get("local_role_policy") or ""
        capabilities = _capabilities_for_model(item)
        adapters.append(
            BrainAdapterRecord(
                adapter_id=_safe_id(f"base:{model_id}"),
                label=model_id,
                adapter_type="base_model",
                provider=str(item.get("provider") or "huggingface"),
                model_id=model_id,
                base_model_id=model_id,
                status="candidate",
                brain_profile_ids=(_safe_id(str(policy or item.get("role") or "model")),),
                capability_ids=tuple(capabilities),
                review_state="candidate",
                created_at=now,
                updated_at=now,
                metadata={
                    "source": "model_catalog",
                    "role": item.get("role", ""),
                    "domain": item.get("domain", ""),
                    "size_class": item.get("size_class", ""),
                    **dict(item.get("metadata") or {}),
                },
            )
        )
    if adapters:
        return tuple(adapters)
    return (
        BrainAdapterRecord(
            adapter_id="base_qwen35b_a3b",
            label="Qwen 35B-A3B base scheduler candidate",
            provider="huggingface",
            model_id="Qwen/Qwen3.6-35B-A3B-Instruct",
            base_model_id="Qwen/Qwen3.6-35B-A3B-Instruct",
            capability_ids=("routing", "planning", "tool_calling"),
            brain_profile_ids=("local_scheduler_master",),
            metadata={"source": "default"},
        ),
    )


def normalize_brain_adapter(raw: dict[str, Any]) -> BrainAdapterRecord:
    now = time.time()
    adapter_type = str(raw.get("adapter_type") or raw.get("type") or "base_model").strip().lower()
    model_id = str(raw.get("model_id") or raw.get("model") or raw.get("base_model_id") or "").strip()
    adapter_id = str(raw.get("adapter_id") or raw.get("id") or _safe_id(f"{adapter_type}:{model_id}:{raw.get('artifact_path') or ''}")).strip()
    return BrainAdapterRecord(
        adapter_id=adapter_id,
        label=str(raw.get("label") or raw.get("name") or model_id or adapter_id),
        adapter_type=adapter_type,
        provider=str(raw.get("provider") or ""),
        model_id=model_id,
        base_model_id=str(raw.get("base_model_id") or model_id),
        artifact_path=str(raw.get("artifact_path") or raw.get("path") or ""),
        status=str(raw.get("status") or "candidate"),
        brain_profile_ids=tuple(str(item) for item in raw.get("brain_profile_ids") or raw.get("brain_profiles") or () if str(item).strip()),
        capability_ids=tuple(str(item) for item in raw.get("capability_ids") or raw.get("capabilities") or () if str(item).strip()),
        benchmark_suite_id=str(raw.get("benchmark_suite_id") or "default_capability_benchmark"),
        review_state=str(raw.get("review_state") or raw.get("review") or "candidate"),
        created_at=float(raw.get("created_at") or now),
        updated_at=float(raw.get("updated_at") or now),
        metadata=dict(raw.get("metadata") or {}),
    )


def _normalize_benchmark_result(raw: dict[str, Any]) -> dict[str, Any]:
    score = float(raw.get("score") or raw.get("pass_rate") or 0)
    if 0 <= score <= 1:
        score *= 100
    return {
        "case_id": str(raw.get("case_id") or ""),
        "capability_id": str(raw.get("capability_id") or ""),
        "score": round(score, 2),
        "passed": bool(raw.get("passed")) if "passed" in raw else score >= float(raw.get("min_score") or 85),
        "critical": bool(raw.get("critical", False)),
        "findings": list(raw.get("findings") or []),
    }


def _capabilities_for_model(raw: dict[str, Any]) -> list[str]:
    domain = str(raw.get("domain") or "").lower()
    role = str(raw.get("role") or "").lower()
    capabilities = set()
    if any(token in domain for token in ("routing", "planning", "reasoning")) or "chief" in role:
        capabilities.update({"routing", "planning", "tool_calling"})
    if any(token in domain for token in ("code", "programming", "game", "ui")) or "programming" in role:
        capabilities.update({"code_edit", "run_tests", "ui_reasoning"})
    if any(token in domain for token in ("ecommerce", "commerce")):
        capabilities.update({"publish_product", "customer_reply"})
    if not capabilities:
        capabilities.add("general_reasoning")
    return sorted(capabilities)


def _capability_ids_from_graph(snapshot: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    if not isinstance(snapshot, dict):
        return ids
    for item in snapshot.get("capabilities") or []:
        if isinstance(item, dict) and item.get("capability_id"):
            ids.add(str(item["capability_id"]))
    return ids


def _workflow_count(workflows: dict[str, Any] | None) -> int:
    if not isinstance(workflows, dict):
        return 0
    for key in ("definition_count", "saved_definition_count"):
        if workflows.get(key) is not None:
            try:
                return int(workflows.get(key) or 0)
            except (TypeError, ValueError):
                return 0
    definitions = workflows.get("definitions")
    return len(definitions) if isinstance(definitions, list) else 0


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").lower()).strip("_") or "brain_adapter"
