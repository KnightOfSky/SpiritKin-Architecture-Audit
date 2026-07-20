from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from backend.evaluation.self_improvement import SelfImprovementLoop, SelfTrainingPackage, TrainingExample
from backend.model.training.dataset_registry import (
    evaluate_dataset_gate,
    load_dataset_registry,
    register_training_dataset,
)
from backend.model.training.workbench import export_self_training_dataset
from backend.orchestrator.runtime_metadata import RuntimeMetadata
from backend.prompts.review import SKILL_REVIEW_PROMPT
from backend.state_store import resolve_state_path

DEFAULT_LEARNING_DIR = Path("state/learning")
DEFAULT_LEARNING_LOG = DEFAULT_LEARNING_DIR / "learning_records.jsonl"
DEFAULT_TRAINING_DATASET = DEFAULT_LEARNING_DIR / "self_training_dataset.jsonl"
DEFAULT_MODEL_PROVIDER_STATE = "state/desktop_console/model_provider.json"
DEFAULT_MODEL_PROVIDER_HEALTH_PATH = "state/model_provider_health.jsonl"
MODEL_PROVIDER_HEALTH_SCHEMA_VERSION = "spiritkin.model_provider_health.v1"
DEFAULT_MODEL_REVIEW_TIMEOUT = 45.0
DEFAULT_ASSIST_MODEL_STATE = "state/desktop_console/assist_models.json"
DEFAULT_REVIEW_COMMITTEE_POLICY_STATE = "state/desktop_console/review_committee_policy.json"


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str
    model: str
    configured: bool
    endpoint: str = ""
    env_key: str = ""
    display_name: str = ""
    source: str = "env"
    api_key: str = field(default="", repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
            "endpoint": self.endpoint,
            "env_key": self.env_key,
            "display_name": self.display_name or self.provider,
            "source": self.source,
            "runtime_metadata": self.runtime_metadata().snapshot(),
        }

    def runtime_metadata(self) -> RuntimeMetadata:
        provider = _canonical_model_provider(self.provider)
        local = provider in {"ollama", "lmstudio", "llamacpp"}
        observation = latest_model_provider_health_observation(provider=provider, model=self.model, endpoint=self.endpoint)
        latency_hint = _int_or_none(observation.get("duration_ms")) if observation else None
        return RuntimeMetadata(
            object_type="model_provider",
            object_id=f"{provider}:{self.model or 'unconfigured'}",
            domain="model",
            owner="model_catalog",
            version=self.model,
            status="active" if self.configured else "candidate",
            tags=(provider, "local" if local else "cloud"),
            source=self.source,
            risk_level="low" if local else "medium",
            permission_scope="local_model" if local else "cloud_model",
            cost_hint="low" if local else "metered",
            latency_hint_ms=latency_hint,
            maturity="configured" if self.configured else "unconfigured",
            extra={
                "provider": provider,
                "endpoint": self.endpoint,
                "display_name": self.display_name or self.provider,
                "env_key": self.env_key,
                "configured": self.configured,
                "data_boundary": "local" if local else "cloud",
                "health_status": observation.get("health_status") if observation else ("not_configured" if not self.configured else "unknown"),
                "last_checked_at": observation.get("checked_at") if observation else None,
                "observed_model_count": observation.get("model_count") if observation else None,
                "health_error": observation.get("error") if observation else "",
            },
        )


@dataclass(frozen=True)
class ModelProviderDefinition:
    provider: str
    display_name: str
    default_endpoint: str = ""
    default_model: str = ""
    env_key: str = ""
    requires_api_key: bool = True
    local_service: bool = False
    supports_model_sync: bool = True
    protocol: str = "openai_compatible"

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "default_endpoint": self.default_endpoint,
            "default_model": self.default_model,
            "env_key": self.env_key,
            "requires_api_key": self.requires_api_key,
            "local_service": self.local_service,
            "supports_model_sync": self.supports_model_sync,
            "protocol": self.protocol,
        }


def _canonical_model_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value in {"llama_cpp", "llama.cpp", "llama-cpp"}:
        return "llamacpp"
    if value == "lm-studio":
        return "lmstudio"
    return value or "openai_compatible"


PROVIDER_DEFINITIONS: tuple[ModelProviderDefinition, ...] = (
    ModelProviderDefinition("llamacpp", "llama.cpp", "http://127.0.0.1:8080/v1", "qwen/qwen3.6-35b-a3b", "LLAMACPP_BASE_URL", False, True, True, "openai_compatible"),
    ModelProviderDefinition("ollama", "Ollama", "http://127.0.0.1:11434", "qwen2.5-coder:7b", "OLLAMA_HOST", False, True, True, "ollama"),
    ModelProviderDefinition("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1", "local-model", "LMSTUDIO_BASE_URL", False, True, True, "openai_compatible"),
    ModelProviderDefinition("openai_compatible", "OpenAI 兼容", "https://api.openai.com/v1", "gpt-4.1", "OPENAI_API_KEY", True, False, True, "openai_compatible"),
    ModelProviderDefinition("cloud_openai_compatible", "自定义 OpenAI 兼容", "", "", "CLOUD_MODEL_API_KEY", True, False, True, "openai_compatible"),
    ModelProviderDefinition("yundun", "云顿 OpenAI 兼容", "", "", "YUNDUN_API_KEY", True, False, True, "openai_compatible"),
    ModelProviderDefinition("anthropic", "Anthropic", "https://api.anthropic.com", "claude-3-7-sonnet-latest", "ANTHROPIC_API_KEY", True, False, True, "anthropic"),
    ModelProviderDefinition("gemini", "Gemini", "https://generativelanguage.googleapis.com", "gemini-2.5-pro", "GEMINI_API_KEY", True, False, True, "gemini"),
)


@dataclass(frozen=True)
class AssistModelSettings:
    model_id: str
    display_name: str
    provider: str = "openai_compatible"
    endpoint: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False, compare=False)
    enabled: bool = True
    role: str = "reviewer"
    priority: int = 50
    notes: str = ""
    request_params: dict[str, Any] = field(default_factory=dict, hash=False)

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        provider = _canonical_model_provider(self.provider)
        if provider in {"ollama", "lmstudio", "llamacpp"}:
            return bool(self.endpoint.strip() and self.model.strip())
        return bool(self.endpoint.strip() and self.model.strip() and self.api_key.strip())

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model": self.model,
            "enabled": self.enabled,
            "role": self.role,
            "priority": self.priority,
            "notes": self.notes,
            "request_params": dict(self.request_params),
            "configured": self.configured,
            "api_key_set": bool(self.api_key),
        }


@dataclass(frozen=True)
class ReviewCommitteePolicy:
    policy_id: str = "default_committee"
    label: str = "默认云端评审团"
    enabled: bool = True
    model_ids: tuple[str, ...] = ()
    required_model_ids: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ("reasoning_reviewer", "code_reviewer")
    min_success_count: int = 1
    pass_threshold: float = 0.5
    require_human_final: bool = True
    apply_to_actions: tuple[str, ...] = ("skill_promotion", "cloud_training", "self_evolution", "high_risk_code")
    notes: str = "Reviewer committee is advisory by default; human/core review remains the final promotion gate."
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "label": self.label,
            "enabled": self.enabled,
            "model_ids": list(self.model_ids),
            "required_model_ids": list(self.required_model_ids),
            "required_roles": list(self.required_roles),
            "min_success_count": self.min_success_count,
            "pass_threshold": self.pass_threshold,
            "require_human_final": self.require_human_final,
            "apply_to_actions": list(self.apply_to_actions),
            "notes": self.notes,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ModelProviderSettings:
    provider: str = "cloud_openai_compatible"
    display_name: str = "云端模型"
    endpoint: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False, compare=False)
    enabled: bool = False

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        provider = _canonical_model_provider(self.provider)
        if provider in {"ollama", "lmstudio", "llamacpp"}:
            return bool(self.endpoint.strip() and self.model.strip())
        return bool(self.endpoint.strip() and self.model.strip() and self.api_key.strip())

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "endpoint": self.endpoint,
            "model": self.model,
            "enabled": self.enabled,
            "api_key_set": bool(self.api_key),
        }


@dataclass(frozen=True)
class ModelReviewResult:
    ok: bool
    provider: str
    model: str
    prompt: str
    response_text: str = ""
    status: str = "ok"
    endpoint: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "response_text": self.response_text,
            "status": self.status,
            "endpoint": self.endpoint,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MultiModelReviewResult:
    ok: bool
    prompt: str
    reviews: tuple[ModelReviewResult, ...]
    status: str = "ok"
    policy: ReviewCommitteePolicy | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "prompt": self.prompt,
            "reviews": [review.snapshot() for review in self.reviews],
            "status": self.status,
            "policy": self.policy.snapshot() if self.policy is not None else {},
            "decision": dict(self.decision),
            "created_at": self.created_at,
            "success_count": sum(1 for review in self.reviews if review.ok),
            "total_count": len(self.reviews),
        }


@dataclass(frozen=True)
class ModelProviderActionResult:
    ok: bool
    action: str
    provider: str
    display_name: str
    endpoint: str
    model: str = ""
    models: tuple[str, ...] = ()
    saved_models: tuple[dict[str, Any], ...] = ()
    status: str = "ok"
    message: str = ""
    error: str = ""
    duration_ms: int = 0
    health_status: str = ""
    checked_at: float = 0.0
    model_count: int = 0
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "provider": self.provider,
            "display_name": self.display_name,
            "endpoint": self.endpoint,
            "model": self.model,
            "models": list(self.models),
            "saved_models": [dict(model) for model in self.saved_models],
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "health_status": self.health_status,
            "checked_at": self.checked_at,
            "model_count": self.model_count,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LearningRecord:
    record_id: str
    created_at: float
    source: str
    problem: str
    correction: str
    skill_name: str = ""
    project: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "created_at": self.created_at,
            "source": self.source,
            "problem": self.problem,
            "correction": self.correction,
            "skill_name": self.skill_name,
            "project": self.project,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class LearningWorkflowReport:
    generated_at: float
    records: tuple[LearningRecord, ...]
    model_providers: tuple[ModelProviderConfig, ...]
    dataset_path: str
    dataset_count: int
    model_provider_settings: ModelProviderSettings = field(default_factory=ModelProviderSettings)
    assist_models: tuple[AssistModelSettings, ...] = ()
    review_committee_policy: ReviewCommitteePolicy = field(default_factory=ReviewCommitteePolicy)
    improvement_report: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "records": [record.snapshot() for record in self.records],
            "provider_definitions": [definition.snapshot() for definition in list_model_provider_definitions()],
            "model_providers": [provider.snapshot() for provider in self.model_providers],
            "model_provider_settings": self.model_provider_settings.snapshot(),
            "assist_models": [model.snapshot() for model in self.assist_models],
            "review_committee_policy": self.review_committee_policy.snapshot(),
            "review_committee_summary": build_review_committee_summary(self.review_committee_policy, self.assist_models),
            "workflow_modes": [
                {
                    "id": "human_review",
                    "label": "人工纠错",
                    "source": "human",
                },
                {
                    "id": "cloud_model_review",
                    "label": "云端模型评审",
                    "source": "external_model",
                },
            ],
            "dataset": {"path": self.dataset_path, "count": self.dataset_count},
            "improvement_report": dict(self.improvement_report),
            "self_improvement_summary": build_self_improvement_summary(
                self.improvement_report,
                record_count=len(self.records),
                dataset_count=self.dataset_count,
                dataset_path=self.dataset_path,
            ),
        }


def resolve_learning_log(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_LEARNING_LOG", str(DEFAULT_LEARNING_LOG), path)


def resolve_training_dataset(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_LEARNING_DATASET", str(DEFAULT_TRAINING_DATASET), path)


def resolve_model_provider_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MODEL_PROVIDER_STATE", DEFAULT_MODEL_PROVIDER_STATE, path)


def resolve_model_provider_health_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MODEL_PROVIDER_HEALTH", DEFAULT_MODEL_PROVIDER_HEALTH_PATH, path)


def resolve_assist_model_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_ASSIST_MODEL_STATE", DEFAULT_ASSIST_MODEL_STATE, path)


def resolve_review_committee_policy_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_REVIEW_COMMITTEE_POLICY_STATE", DEFAULT_REVIEW_COMMITTEE_POLICY_STATE, path)


def _now() -> float:
    return time.time()


def _int_or_none(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _duration_ms(started_at: float, finished_at: float) -> int:
    return max(0, int(round((finished_at - started_at) * 1000)))


def record_model_provider_health_observation(
    provider: ModelProviderConfig,
    *,
    action: str,
    started_at: float,
    finished_at: float,
    health_status: str,
    error: str = "",
    model_count: int = 0,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    target = resolve_model_provider_health_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    observation = {
        "schema_version": MODEL_PROVIDER_HEALTH_SCHEMA_VERSION,
        "provider": _canonical_model_provider(provider.provider),
        "display_name": provider.display_name or provider.provider,
        "endpoint": provider.endpoint,
        "model": provider.model,
        "action": action,
        "started_at": started_at,
        "finished_at": finished_at,
        "checked_at": finished_at,
        "duration_ms": _duration_ms(started_at, finished_at),
        "health_status": health_status,
        "error": error,
        "model_count": int(model_count),
        "source": "desktop_provider_action",
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(observation, ensure_ascii=False) + "\n")
    return observation


def load_model_provider_health_observations(
    path: str | os.PathLike[str] | None = None,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    target = resolve_model_provider_health_path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows[-max(1, int(limit)):]


def latest_model_provider_health_observation(
    *,
    provider: str,
    model: str = "",
    endpoint: str = "",
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    provider_id = _canonical_model_provider(provider)
    model_id = str(model or "").strip()
    endpoint_id = str(endpoint or "").strip().rstrip("/")
    fallback: dict[str, Any] = {}
    for observation in reversed(load_model_provider_health_observations(path, limit=500)):
        if _canonical_model_provider(str(observation.get("provider") or "")) != provider_id:
            continue
        observed_endpoint = str(observation.get("endpoint") or "").strip().rstrip("/")
        observed_model = str(observation.get("model") or "").strip()
        if endpoint_id and observed_endpoint and observed_endpoint != endpoint_id:
            continue
        if not fallback:
            fallback = observation
        if model_id and observed_model and observed_model != model_id:
            continue
        return observation
    return fallback


def _record_provider_action_health(
    provider: ModelProviderConfig,
    *,
    action: str,
    started_at: float,
    health_status: str,
    error: str = "",
    model_count: int = 0,
) -> dict[str, Any]:
    finished_at = _now()
    return record_model_provider_health_observation(
        provider,
        action=action,
        started_at=started_at,
        finished_at=finished_at,
        health_status=health_status,
        error=error,
        model_count=model_count,
    )


def _record_from_dict(data: dict[str, Any]) -> LearningRecord:
    return LearningRecord(
        record_id=str(data.get("record_id") or f"learn-{int(_now() * 1000)}"),
        created_at=float(data.get("created_at") or _now()),
        source=str(data.get("source") or "human"),
        problem=str(data.get("problem") or ""),
        correction=str(data.get("correction") or ""),
        skill_name=str(data.get("skill_name") or ""),
        project=str(data.get("project") or ""),
        tags=tuple(str(tag) for tag in data.get("tags") or ()),
        metadata=dict(data.get("metadata") or {}),
    )


def load_learning_records(path: str | os.PathLike[str] | None = None, *, limit: int = 100) -> list[LearningRecord]:
    target = resolve_learning_log(path)
    if not target.exists():
        return []
    records: list[LearningRecord] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            record = _record_from_dict(data)
            if record.problem.strip() or record.correction.strip():
                records.append(record)
    return records[-max(1, int(limit)):]


def append_learning_record(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> LearningRecord:
    target = resolve_learning_log(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = _record_from_dict({
        **payload,
        "record_id": payload.get("record_id") or f"learn-{int(_now() * 1000)}",
        "created_at": payload.get("created_at") or _now(),
    })
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")
    return record


def load_model_provider_settings(path: str | os.PathLike[str] | None = None) -> ModelProviderSettings:
    target = resolve_model_provider_state_path(path)
    if not target.exists():
        return ModelProviderSettings(
            endpoint=os.getenv("YUNDUN_BASE_URL", os.getenv("CLOUD_MODEL_BASE_URL", "")),
            model=os.getenv("YUNDUN_MODEL", os.getenv("CLOUD_MODEL_MODEL", "")),
            api_key=os.getenv("YUNDUN_API_KEY", os.getenv("CLOUD_MODEL_API_KEY", "")),
            enabled=bool(os.getenv("YUNDUN_API_KEY") or os.getenv("CLOUD_MODEL_API_KEY")),
        )
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return ModelProviderSettings(
        provider=_canonical_model_provider(str(data.get("provider") or "cloud_openai_compatible")),
        display_name=str(data.get("display_name") or "云端模型"),
        endpoint=str(data.get("endpoint") or ""),
        model=str(data.get("model") or ""),
        api_key=str(data.get("api_key") or ""),
        enabled=bool(data.get("enabled", False)),
    )


def save_model_provider_settings(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> ModelProviderSettings:
    current = load_model_provider_settings(path)
    incoming = dict(payload or {})
    provider = _canonical_model_provider(str(incoming.get("provider") or current.provider or "cloud_openai_compatible"))
    api_key = str(incoming.get("api_key") or "")
    if not api_key and bool(incoming.get("keep_existing_key", True)) and provider == _canonical_model_provider(current.provider):
        api_key = current.api_key
    settings = ModelProviderSettings(
        provider=provider,
        display_name=str(incoming.get("display_name") or current.display_name or "云端模型"),
        endpoint=str(incoming.get("endpoint") or current.endpoint or "").rstrip("/"),
        model=str(incoming.get("model") or current.model or ""),
        api_key=api_key,
        enabled=bool(incoming.get("enabled", current.enabled)),
    )
    target = resolve_model_provider_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**settings.snapshot(), "api_key": settings.api_key}, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def load_assist_models(path: str | os.PathLike[str] | None = None) -> list[AssistModelSettings]:
    target = resolve_assist_model_state_path(path)
    if not target.exists():
        return default_assist_models()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    raw_models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(raw_models, list):
        return default_assist_models()
    models = [_assist_model_from_dict(item) for item in raw_models if isinstance(item, dict)]
    return models


def save_assist_models(models: list[AssistModelSettings], path: str | os.PathLike[str] | None = None) -> list[AssistModelSettings]:
    ordered = sorted(models, key=lambda item: item.priority, reverse=True)
    target = resolve_assist_model_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"models": [{**model.snapshot(), "api_key": model.api_key} for model in ordered], "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ordered


def save_assist_model(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> AssistModelSettings:
    current = load_assist_models(path)
    fallback_id = str(payload.get("model_id") or payload.get("id") or "").strip() or f"assist_{int(_now())}"
    existing = next((item for item in current if item.model_id == fallback_id), None)
    incoming = dict(payload or {})
    provider = _canonical_model_provider(str(incoming.get("provider") or (existing.provider if existing else "openai_compatible")))
    api_key = str(incoming.get("api_key") or "")
    if not api_key and bool(incoming.get("keep_existing_key", True)) and existing is not None and provider == _canonical_model_provider(existing.provider):
        api_key = existing.api_key
    model = AssistModelSettings(
        model_id=fallback_id,
        display_name=str(incoming.get("display_name") or incoming.get("label") or (existing.display_name if existing else fallback_id)),
        provider=provider,
        endpoint=str(incoming.get("endpoint") or (existing.endpoint if existing else "")).rstrip("/"),
        model=str(incoming.get("model") or (existing.model if existing else "")),
        api_key=api_key,
        enabled=bool(incoming.get("enabled", existing.enabled if existing else True)),
        role=str(incoming.get("role") or (existing.role if existing else "reviewer")),
        priority=int(incoming.get("priority", existing.priority if existing else 50) or 50),
        notes=str(incoming.get("notes") or (existing.notes if existing else "")),
        request_params=_coerce_request_params(
            incoming.get("request_params"),
            fallback=existing.request_params if existing else None,
        ),
    )
    others = [item for item in current if item.model_id != model.model_id]
    save_assist_models([*others, model], path)
    return model


def delete_assist_model(model_id: str, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    current = load_assist_models(path)
    remaining = [item for item in current if item.model_id != model_id]
    deleted = len(remaining) != len(current)
    save_assist_models(remaining, path)
    return {"deleted": model_id if deleted else "", "models": [model.snapshot() for model in remaining]}


def default_assist_models() -> list[AssistModelSettings]:
    environ = os.environ
    models = [
        AssistModelSettings(
            "deepseek",
            "DeepSeek",
            "openai_compatible",
            environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            environ.get("DEEPSEEK_API_KEY", ""),
            bool(environ.get("DEEPSEEK_API_KEY")),
            "reasoning_reviewer",
            90,
        ),
        AssistModelSettings(
            "gpt",
            "GPT",
            "openai_compatible",
            environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            environ.get("SPIRITKIN_REVIEW_MODEL", "gpt-4.1"),
            environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY", ""),
            bool(environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY")),
            "code_reviewer",
            80,
        ),
        AssistModelSettings(
            "opus",
            "Claude Opus",
            "anthropic",
            "https://api.anthropic.com",
            environ.get("SPIRITKIN_ANTHROPIC_MODEL", "claude-3-opus-latest"),
            environ.get("ANTHROPIC_API_KEY", ""),
            bool(environ.get("ANTHROPIC_API_KEY")),
            "architecture_reviewer",
            70,
        ),
    ]
    if str(environ.get("SPIRITKIN_ENABLE_OLLAMA") or "").lower() in {"1", "true", "yes", "on"} or environ.get("OLLAMA_HOST"):
        models.append(
            AssistModelSettings(
                "local35b",
                "本地 35B",
                "ollama",
                environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
                environ.get("SPIRITKIN_OLLAMA_MODEL", "qwen2.5-coder:35b"),
                "",
                bool(environ.get("OLLAMA_HOST")),
                "primary_worker",
                100,
            )
        )
    return models


def _assist_model_from_dict(data: dict[str, Any]) -> AssistModelSettings:
    return AssistModelSettings(
        model_id=str(data.get("model_id") or data.get("id") or ""),
        display_name=str(data.get("display_name") or data.get("label") or data.get("model_id") or ""),
        provider=_canonical_model_provider(str(data.get("provider") or "openai_compatible")),
        endpoint=str(data.get("endpoint") or ""),
        model=str(data.get("model") or ""),
        api_key=str(data.get("api_key") or ""),
        enabled=bool(data.get("enabled", True)),
        role=str(data.get("role") or "reviewer"),
        priority=int(data.get("priority") or 50),
        notes=str(data.get("notes") or ""),
        request_params={str(key): item for key, item in data.get("request_params").items()} if isinstance(data.get("request_params"), dict) else {},
    )


def _coerce_request_params(value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Accept a dict or a JSON-object string; raise for anything else non-empty.

    ``None`` keeps the fallback (existing params); an empty string clears them,
    because the desktop editor always submits the textbox content.
    """
    if value is None:
        return dict(fallback or {})
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"request_params 不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("request_params 必须是 JSON 对象，例如 {\"reasoning_effort\": \"low\"}")
        return {str(key): item for key, item in parsed.items()}
    raise ValueError("request_params 必须是 JSON 对象")


def load_review_committee_policy(path: str | os.PathLike[str] | None = None) -> ReviewCommitteePolicy:
    target = resolve_review_committee_policy_path(path)
    if not target.exists():
        return ReviewCommitteePolicy()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return _review_committee_policy_from_dict(data)


def save_review_committee_policy(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> ReviewCommitteePolicy:
    current = load_review_committee_policy(path)
    incoming = dict(payload or {})
    policy = ReviewCommitteePolicy(
        policy_id=str(incoming.get("policy_id") or current.policy_id or "default_committee"),
        label=str(incoming.get("label") or current.label or "默认云端评审团"),
        enabled=bool(incoming.get("enabled", current.enabled)),
        model_ids=tuple(str(item).strip() for item in incoming.get("model_ids", current.model_ids) or () if str(item).strip()),
        required_model_ids=tuple(str(item).strip() for item in incoming.get("required_model_ids", current.required_model_ids) or () if str(item).strip()),
        required_roles=tuple(str(item).strip() for item in incoming.get("required_roles", current.required_roles) or () if str(item).strip()),
        min_success_count=max(0, int(incoming.get("min_success_count", current.min_success_count) or 0)),
        pass_threshold=min(1.0, max(0.0, float(incoming.get("pass_threshold", current.pass_threshold) or 0.0))),
        require_human_final=bool(incoming.get("require_human_final", current.require_human_final)),
        apply_to_actions=tuple(str(item).strip() for item in incoming.get("apply_to_actions", current.apply_to_actions) or () if str(item).strip()),
        notes=str(incoming.get("notes") if incoming.get("notes") is not None else current.notes),
        updated_at=_now(),
    )
    target = resolve_review_committee_policy_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(policy.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def _review_committee_policy_from_dict(data: dict[str, Any]) -> ReviewCommitteePolicy:
    return ReviewCommitteePolicy(
        policy_id=str(data.get("policy_id") or data.get("id") or "default_committee"),
        label=str(data.get("label") or "默认云端评审团"),
        enabled=bool(data.get("enabled", True)),
        model_ids=tuple(str(item).strip() for item in data.get("model_ids") or () if str(item).strip()),
        required_model_ids=tuple(str(item).strip() for item in data.get("required_model_ids") or () if str(item).strip()),
        required_roles=tuple(str(item).strip() for item in data.get("required_roles") or ("reasoning_reviewer", "code_reviewer") if str(item).strip()),
        min_success_count=max(0, int(data.get("min_success_count", 1) or 0)),
        pass_threshold=min(1.0, max(0.0, float(data.get("pass_threshold", 0.5) or 0.0))),
        require_human_final=bool(data.get("require_human_final", True)),
        apply_to_actions=tuple(str(item).strip() for item in data.get("apply_to_actions") or ("skill_promotion", "cloud_training", "self_evolution", "high_risk_code") if str(item).strip()),
        notes=str(data.get("notes") or ""),
        updated_at=float(data.get("updated_at") or _now()),
    )


def build_review_committee_summary(
    policy: ReviewCommitteePolicy | None = None,
    models: tuple[AssistModelSettings, ...] | list[AssistModelSettings] | None = None,
) -> dict[str, Any]:
    policy = policy or load_review_committee_policy()
    selected_models = tuple(models if models is not None else load_assist_models())
    selected = select_review_committee_models(policy, selected_models)
    configured_ids = {model.model_id for model in selected if model.configured}
    available_ids = {model.model_id for model in selected}
    missing_required_models = [model_id for model_id in policy.required_model_ids if model_id not in configured_ids]
    configured_roles = {model.role for model in selected if model.configured}
    missing_required_roles = [role for role in policy.required_roles if role not in configured_roles]
    return {
        "status": "disabled" if not policy.enabled else ("ready" if not missing_required_models and not missing_required_roles and configured_ids else "needs_attention"),
        "selected_model_ids": [model.model_id for model in selected],
        "configured_model_ids": sorted(configured_ids),
        "available_model_ids": sorted(available_ids),
        "missing_required_model_ids": missing_required_models,
        "missing_required_roles": missing_required_roles,
        "configured_count": len(configured_ids),
        "selected_count": len(selected),
        "min_success_count": policy.min_success_count,
        "pass_threshold": policy.pass_threshold,
        "require_human_final": policy.require_human_final,
    }


def select_review_committee_models(
    policy: ReviewCommitteePolicy,
    models: tuple[AssistModelSettings, ...] | list[AssistModelSettings],
    *,
    requested_model_ids: set[str] | None = None,
) -> list[AssistModelSettings]:
    requested = {item for item in requested_model_ids or set() if item}
    policy_ids = set(policy.model_ids)
    required_ids = set(policy.required_model_ids)
    selected: list[AssistModelSettings] = []
    for model in sorted(models, key=lambda item: item.priority, reverse=True):
        if not model.enabled:
            continue
        if requested and model.model_id not in requested:
            continue
        if not requested and policy.enabled and (policy_ids or required_ids):
            if model.model_id not in policy_ids and model.model_id not in required_ids:
                continue
        selected.append(model)
    return selected


def evaluate_review_committee_decision(
    reviews: tuple[ModelReviewResult, ...],
    policy: ReviewCommitteePolicy,
) -> dict[str, Any]:
    success_count = sum(1 for review in reviews if review.ok)
    total_count = len(reviews)
    required_success = max(policy.min_success_count, int((total_count * policy.pass_threshold) + 0.999999))
    required_success = min(max(required_success, 0), total_count)
    ok = total_count > 0 and success_count >= required_success
    return {
        "ok": ok,
        "status": "passed" if ok else ("not_configured" if total_count == 0 else "failed_threshold"),
        "success_count": success_count,
        "total_count": total_count,
        "required_success_count": required_success,
        "pass_threshold": policy.pass_threshold,
        "require_human_final": policy.require_human_final,
    }


def _endpoint_port_open(endpoint: str, timeout: float = 0.25) -> bool:
    try:
        parsed = parse.urlparse(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_model_provider_definitions() -> tuple[ModelProviderDefinition, ...]:
    return PROVIDER_DEFINITIONS


def _provider_definition(provider: str) -> ModelProviderDefinition:
    provider_id = _canonical_model_provider(provider)
    for definition in PROVIDER_DEFINITIONS:
        if definition.provider == provider_id:
            return definition
    return ModelProviderDefinition(
        provider_id,
        provider_id,
        requires_api_key=False,
        supports_model_sync=True,
        protocol="openai_compatible",
    )


def _provider_env_endpoint(definition: ModelProviderDefinition, environ: dict[str, str]) -> str:
    provider = definition.provider
    if provider == "ollama":
        return environ.get("OLLAMA_HOST", definition.default_endpoint)
    if provider == "lmstudio":
        return environ.get("LMSTUDIO_BASE_URL", definition.default_endpoint)
    if provider == "llamacpp":
        return environ.get("LLAMACPP_BASE_URL", definition.default_endpoint)
    if provider == "openai_compatible":
        return environ.get("OPENAI_BASE_URL", definition.default_endpoint)
    if provider == "cloud_openai_compatible":
        return environ.get("CLOUD_MODEL_BASE_URL", definition.default_endpoint)
    if provider == "yundun":
        return environ.get("YUNDUN_BASE_URL", environ.get("CLOUD_MODEL_BASE_URL", definition.default_endpoint))
    if provider == "anthropic":
        return environ.get("ANTHROPIC_BASE_URL", definition.default_endpoint)
    if provider == "gemini":
        return environ.get("GEMINI_BASE_URL", definition.default_endpoint)
    return definition.default_endpoint


def _provider_env_api_key(definition: ModelProviderDefinition, environ: dict[str, str]) -> str:
    provider = definition.provider
    if provider == "openai_compatible":
        return environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY", "")
    if provider == "cloud_openai_compatible":
        return environ.get("CLOUD_MODEL_API_KEY", "")
    if provider == "llamacpp":
        return environ.get("LLAMACPP_API_KEY", "")
    if provider == "yundun":
        return environ.get("YUNDUN_API_KEY", environ.get("CLOUD_MODEL_API_KEY", ""))
    if provider == "anthropic":
        return environ.get("ANTHROPIC_API_KEY", "")
    if provider == "gemini":
        return environ.get("GEMINI_API_KEY") or environ.get("GOOGLE_API_KEY", "")
    return ""


def _provider_payload_config(payload: dict[str, Any], environ: dict[str, str] | None = None) -> tuple[ModelProviderConfig, ModelProviderDefinition]:
    environ = environ or os.environ
    incoming = dict(payload or {})
    provider = _canonical_model_provider(str(incoming.get("provider") or incoming.get("id") or ""))
    definition = _provider_definition(provider)
    model_id = str(incoming.get("model_id") or "").strip()
    display_name = str(incoming.get("display_name") or definition.display_name or provider)
    endpoint = str(incoming.get("endpoint") or "").strip().rstrip("/")
    model = str(incoming.get("model") or "").strip()
    api_key = str(incoming.get("api_key") or "").strip()

    assist_models = load_assist_models()
    saved_candidates: list[AssistModelSettings] = []
    if model_id:
        saved_candidates.extend(item for item in assist_models if item.model_id == model_id)
    saved_candidates.extend(item for item in assist_models if item.provider.lower() == provider)
    for saved in saved_candidates:
        endpoint = endpoint or saved.endpoint
        model = model or saved.model
        api_key = api_key or saved.api_key
        display_name = display_name or saved.display_name
        if endpoint and model and (api_key or not definition.requires_api_key):
            break

    local_settings = load_model_provider_settings()
    if local_settings.provider.lower() == provider:
        endpoint = endpoint or local_settings.endpoint
        model = model or local_settings.model
        api_key = api_key or local_settings.api_key
        display_name = display_name or local_settings.display_name

    endpoint = (endpoint or _provider_env_endpoint(definition, environ)).rstrip("/")
    model = model or environ.get(f"SPIRITKIN_{provider.upper()}_MODEL", "") or definition.default_model
    api_key = api_key or _provider_env_api_key(definition, environ)
    configured = bool(endpoint and (not definition.requires_api_key or api_key))
    return (
        ModelProviderConfig(
            provider,
            model,
            configured,
            endpoint,
            definition.env_key,
            display_name=display_name,
            source="desktop_provider_action",
            api_key=api_key,
        ),
        definition,
    )


def _get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    req = request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        if value:
            req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc


def _openai_compatible_models(provider: ModelProviderConfig, timeout: float) -> list[str]:
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    data = _get_json(f"{provider.endpoint.rstrip('/')}/models", headers, timeout)
    models = data.get("data") if isinstance(data, dict) else []
    if not isinstance(models, list):
        return []
    result: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            result.append(model_id)
    return sorted(set(result), key=str.lower)


def _ollama_models(provider: ModelProviderConfig, timeout: float) -> list[str]:
    data = _get_json(f"{provider.endpoint.rstrip('/')}/api/tags", {}, timeout)
    models = data.get("models") if isinstance(data, dict) else []
    if not isinstance(models, list):
        return []
    result: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("name") or item.get("model") or "").strip()
        if model_id:
            result.append(model_id)
    return sorted(set(result), key=str.lower)


def _static_provider_models(provider: ModelProviderConfig, definition: ModelProviderDefinition) -> list[str]:
    return [item for item in [provider.model, definition.default_model] if item]


def _discover_provider_models(provider: ModelProviderConfig, definition: ModelProviderDefinition, timeout: float) -> list[str]:
    if definition.protocol == "ollama":
        return _ollama_models(provider, timeout)
    if definition.protocol == "openai_compatible":
        return _openai_compatible_models(provider, timeout)
    return _static_provider_models(provider, definition)


def _stable_provider_model_id(provider: str, model: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in f"{provider}_{model}".lower())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:96] or f"{provider}_{int(_now())}"


def _save_discovered_provider_models(provider: ModelProviderConfig, definition: ModelProviderDefinition, models: list[str]) -> tuple[dict[str, Any], ...]:
    if not models:
        return ()
    current = load_assist_models()
    saved: list[dict[str, Any]] = []
    for index, model_name in enumerate(models):
        existing = next(
            (
                item
                for item in current
                if item.provider.lower() == provider.provider.lower()
                and item.model.lower() == model_name.lower()
            ),
            None,
        )
        model = save_assist_model(
            {
                "model_id": existing.model_id if existing else _stable_provider_model_id(provider.provider, model_name),
                "display_name": f"{definition.display_name} · {model_name}",
                "provider": provider.provider,
                "endpoint": provider.endpoint,
                "model": model_name,
                "api_key": provider.api_key,
                "keep_existing_key": True,
                "enabled": True,
                "role": "primary_worker" if definition.local_service else "reviewer",
                "priority": 100 - min(index, 40) if definition.local_service else 70 - min(index, 30),
                "notes": "由 Provider 同步发现",
            }
        )
        current = [item for item in current if item.model_id != model.model_id] + [model]
        saved.append(model.snapshot())
    return tuple(saved)


def sync_model_provider(payload: dict[str, Any], environ: dict[str, str] | None = None, *, timeout: float = 10.0) -> ModelProviderActionResult:
    started_at = _now()
    provider, definition = _provider_payload_config(payload, environ)
    if not provider.endpoint:
        observation = _record_provider_action_health(
            provider,
            action="sync_provider_models",
            started_at=started_at,
            health_status="not_configured",
            error="Provider endpoint is empty.",
        )
        return ModelProviderActionResult(
            False,
            "sync_provider_models",
            provider.provider,
            definition.display_name,
            provider.endpoint,
            provider.model,
            status="not_configured",
            error="Provider endpoint is empty.",
            duration_ms=observation["duration_ms"],
            health_status=observation["health_status"],
            checked_at=observation["checked_at"],
            model_count=observation["model_count"],
        )
    try:
        models = _discover_provider_models(provider, definition, timeout)
    except Exception as exc:
        observation = _record_provider_action_health(
            provider,
            action="sync_provider_models",
            started_at=started_at,
            health_status="error",
            error=str(exc),
        )
        return ModelProviderActionResult(
            False,
            "sync_provider_models",
            provider.provider,
            definition.display_name,
            provider.endpoint,
            provider.model,
            status="error",
            error=str(exc),
            duration_ms=observation["duration_ms"],
            health_status=observation["health_status"],
            checked_at=observation["checked_at"],
            model_count=observation["model_count"],
        )
    save_discovered = bool((payload or {}).get("save_discovered", definition.local_service))
    saved = _save_discovered_provider_models(provider, definition, models) if save_discovered else ()
    health_status = "ready" if models else "degraded"
    observation = _record_provider_action_health(
        provider,
        action="sync_provider_models",
        started_at=started_at,
        health_status=health_status,
        model_count=len(models),
    )
    message = f"发现 {len(models)} 个模型"
    if saved:
        message += f"，已保存 {len(saved)} 个"
    return ModelProviderActionResult(
        True,
        "sync_provider_models",
        provider.provider,
        definition.display_name,
        provider.endpoint,
        provider.model,
        models=tuple(models),
        saved_models=saved,
        message=message,
        duration_ms=observation["duration_ms"],
        health_status=observation["health_status"],
        checked_at=observation["checked_at"],
        model_count=observation["model_count"],
    )


def test_model_provider_connection(payload: dict[str, Any], environ: dict[str, str] | None = None, *, timeout: float = 10.0) -> ModelProviderActionResult:
    started_at = _now()
    provider, definition = _provider_payload_config(payload, environ)
    if not provider.endpoint:
        observation = _record_provider_action_health(
            provider,
            action="test_provider",
            started_at=started_at,
            health_status="not_configured",
            error="Provider endpoint is empty.",
        )
        return ModelProviderActionResult(
            False,
            "test_provider",
            provider.provider,
            definition.display_name,
            provider.endpoint,
            provider.model,
            status="not_configured",
            error="Provider endpoint is empty.",
            duration_ms=observation["duration_ms"],
            health_status=observation["health_status"],
            checked_at=observation["checked_at"],
            model_count=observation["model_count"],
        )
    if definition.requires_api_key and not provider.api_key:
        observation = _record_provider_action_health(
            provider,
            action="test_provider",
            started_at=started_at,
            health_status="not_configured",
            error=f"{definition.display_name} requires an API key.",
        )
        return ModelProviderActionResult(
            False,
            "test_provider",
            provider.provider,
            definition.display_name,
            provider.endpoint,
            provider.model,
            status="not_configured",
            error=f"{definition.display_name} requires an API key.",
            duration_ms=observation["duration_ms"],
            health_status=observation["health_status"],
            checked_at=observation["checked_at"],
            model_count=observation["model_count"],
        )
    try:
        models = _discover_provider_models(provider, definition, timeout)
    except Exception as exc:
        observation = _record_provider_action_health(
            provider,
            action="test_provider",
            started_at=started_at,
            health_status="error",
            error=str(exc),
        )
        return ModelProviderActionResult(
            False,
            "test_provider",
            provider.provider,
            definition.display_name,
            provider.endpoint,
            provider.model,
            status="error",
            error=str(exc),
            duration_ms=observation["duration_ms"],
            health_status=observation["health_status"],
            checked_at=observation["checked_at"],
            model_count=observation["model_count"],
        )
    message = f"连接正常，发现 {len(models)} 个模型" if models else "连接正常，但未返回模型列表"
    observation = _record_provider_action_health(
        provider,
        action="test_provider",
        started_at=started_at,
        health_status="ready" if models else "degraded",
        model_count=len(models),
    )
    return ModelProviderActionResult(
        True,
        "test_provider",
        provider.provider,
        definition.display_name,
        provider.endpoint,
        provider.model or (models[0] if models else ""),
        models=tuple(models),
        message=message,
        duration_ms=observation["duration_ms"],
        health_status=observation["health_status"],
        checked_at=observation["checked_at"],
        model_count=observation["model_count"],
    )


def discover_model_providers(environ: dict[str, str] | None = None) -> list[ModelProviderConfig]:
    environ = environ or os.environ
    local_settings = load_model_provider_settings()
    local_definition = _provider_definition(local_settings.provider)
    providers = [
        *[
            ModelProviderConfig(
                model.provider,
                model.model,
                model.configured,
                model.endpoint,
                "local_state",
                display_name=model.display_name,
                source="assist_models",
                api_key=model.api_key,
            )
            for model in load_assist_models()
        ],
        ModelProviderConfig(
            local_settings.provider,
            local_settings.model,
            local_settings.configured,
            local_settings.endpoint,
            local_definition.env_key,
            display_name=local_settings.display_name,
            source="local_state",
            api_key=local_settings.api_key,
        ),
        ModelProviderConfig("openai_compatible", environ.get("SPIRITKIN_REVIEW_MODEL", "gpt-4.1"), bool(environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY")), environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), "OPENAI_API_KEY", display_name="OpenAI 兼容", api_key=environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY") or ""),
        ModelProviderConfig("anthropic", environ.get("SPIRITKIN_ANTHROPIC_MODEL", "claude-3-7-sonnet-latest"), bool(environ.get("ANTHROPIC_API_KEY")), "https://api.anthropic.com", "ANTHROPIC_API_KEY", display_name="Anthropic", api_key=environ.get("ANTHROPIC_API_KEY", "")),
        ModelProviderConfig("gemini", environ.get("SPIRITKIN_GEMINI_MODEL", "gemini-2.5-pro"), bool(environ.get("GEMINI_API_KEY") or environ.get("GOOGLE_API_KEY")), "https://generativelanguage.googleapis.com", "GEMINI_API_KEY", display_name="Gemini", api_key=environ.get("GEMINI_API_KEY") or environ.get("GOOGLE_API_KEY") or ""),
    ]
    if str(environ.get("SPIRITKIN_ENABLE_OLLAMA") or "").lower() in {"1", "true", "yes", "on"} or environ.get("OLLAMA_HOST"):
        ollama_endpoint = environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        providers.append(ModelProviderConfig("ollama", environ.get("SPIRITKIN_OLLAMA_MODEL", "qwen2.5-coder:7b"), bool(environ.get("OLLAMA_HOST")) or _endpoint_port_open(ollama_endpoint), ollama_endpoint, "OLLAMA_HOST", display_name="Ollama 本地模型"))
    if str(environ.get("SPIRITKIN_ENABLE_LLAMACPP") or "").lower() in {"1", "true", "yes", "on"} or environ.get("LLAMACPP_BASE_URL"):
        llamacpp_endpoint = environ.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080/v1")
        providers.append(ModelProviderConfig("llamacpp", environ.get("SPIRITKIN_LLAMACPP_MODEL", "local-gguf-model"), bool(environ.get("LLAMACPP_BASE_URL")) or _endpoint_port_open(llamacpp_endpoint), llamacpp_endpoint, "LLAMACPP_BASE_URL", display_name="llama.cpp", api_key=environ.get("LLAMACPP_API_KEY", "")))
    return providers


def build_review_prompt(problem: str, *, skill_name: str = "", context: str = "") -> str:
    label = f"Skill: {skill_name}\n" if skill_name else ""
    return SKILL_REVIEW_PROMPT.substitute(
        label=label,
        problem=problem.strip(),
        context=context.strip(),
    ).strip()


def request_model_review(
    problem: str,
    *,
    skill_name: str = "",
    context: str = "",
    provider: str = "",
    model: str = "",
    timeout: float = DEFAULT_MODEL_REVIEW_TIMEOUT,
    environ: dict[str, str] | None = None,
) -> ModelReviewResult:
    environ = environ or os.environ
    prompt = build_review_prompt(problem, skill_name=skill_name, context=context)
    providers = discover_model_providers(environ)
    selected = _select_provider(providers, provider, model)
    if selected is None:
        return ModelReviewResult(
            ok=False,
            provider=provider or "",
            model=model,
            prompt=prompt,
            status="not_configured",
            error="No configured review provider. Configure the desktop cloud provider or set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY.",
        )

    provider_model = model or selected.model
    try:
        if selected.provider in {"openai_compatible", "cloud_openai_compatible", "yundun", "yundun_openai_compatible", "lmstudio", "llamacpp"}:
            text = _request_openai_compatible_review(prompt, selected, provider_model, environ, timeout)
        elif selected.provider == "anthropic":
            text = _request_anthropic_review(prompt, selected, provider_model, environ, timeout)
        elif selected.provider == "gemini":
            text = _request_gemini_review(prompt, selected, provider_model, environ, timeout)
        elif selected.provider == "ollama":
            text = _request_ollama_review(prompt, selected, provider_model, timeout)
        else:
            raise ValueError(f"unsupported provider: {selected.provider}")
    except Exception as exc:
        return ModelReviewResult(
            ok=False,
            provider=selected.provider,
            model=provider_model,
            prompt=prompt,
            status="request_failed",
            endpoint=selected.endpoint,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ModelReviewResult(
        ok=True,
        provider=selected.provider,
        model=provider_model,
        prompt=prompt,
        response_text=text.strip(),
        endpoint=selected.endpoint,
    )


def request_multi_model_review(
    problem: str,
    *,
    skill_name: str = "",
    context: str = "",
    model_ids: list[str] | None = None,
    policy: ReviewCommitteePolicy | None = None,
    timeout: float = DEFAULT_MODEL_REVIEW_TIMEOUT,
    environ: dict[str, str] | None = None,
) -> MultiModelReviewResult:
    prompt = build_review_prompt(problem, skill_name=skill_name, context=context)
    requested = {item.strip() for item in model_ids or [] if item.strip()}
    policy = policy or load_review_committee_policy()
    models = [
        model
        for model in select_review_committee_models(policy, load_assist_models(), requested_model_ids=requested)
        if model.configured
    ]
    reviews = tuple(_request_assist_model_review(prompt, model, timeout=timeout, environ=environ or os.environ) for model in models)
    decision = evaluate_review_committee_decision(reviews, policy)
    status = str(decision.get("status") or ("ok" if any(review.ok for review in reviews) else "not_configured"))
    return MultiModelReviewResult(ok=bool(decision.get("ok")), prompt=prompt, reviews=reviews, status=status, policy=policy, decision=decision)


def _request_assist_model_review(prompt: str, model: AssistModelSettings, *, timeout: float, environ: dict[str, str]) -> ModelReviewResult:
    provider = _canonical_model_provider(model.provider)
    config = ModelProviderConfig(
        provider=provider,
        model=model.model,
        configured=model.configured,
        endpoint=model.endpoint,
        env_key="assist_model_state",
        display_name=model.display_name,
        source="assist_models",
        api_key=model.api_key,
    )
    try:
        if provider in {"openai_compatible", "cloud_openai_compatible", "yundun", "yundun_openai_compatible", "lmstudio", "llamacpp"}:
            text = _request_openai_compatible_review(prompt, config, model.model, environ, timeout)
        elif provider == "anthropic":
            text = _request_anthropic_review(prompt, config, model.model, environ, timeout)
        elif provider == "gemini":
            text = _request_gemini_review(prompt, config, model.model, environ, timeout)
        elif provider == "ollama":
            text = _request_ollama_review(prompt, config, model.model, timeout)
        else:
            raise ValueError(f"unsupported provider: {model.provider}")
    except Exception as exc:
        return ModelReviewResult(
            ok=False,
            provider=provider,
            model=model.model,
            prompt=prompt,
            status="request_failed",
            endpoint=model.endpoint,
            error=f"{model.display_name}: {type(exc).__name__}: {exc}",
        )
    return ModelReviewResult(ok=True, provider=provider, model=model.model, prompt=prompt, response_text=text, endpoint=model.endpoint)


_OPENAI_COMPATIBLE_PROVIDER_FAMILY = {"openai_compatible", "cloud_openai_compatible", "yundun", "yundun_openai_compatible", "lmstudio", "llamacpp"}


def _select_provider(providers: list[ModelProviderConfig], provider: str = "", model: str = "") -> ModelProviderConfig | None:
    requested = _canonical_model_provider(provider) if provider.strip() else ""
    requested_model = str(model or "").strip()
    if requested_model:
        # 同一 provider 名可能对应多个端点（本地 LM Studio 与云端 DeepSeek 都可注册为
        # openai_compatible）。优先选 model 精确匹配的候选，避免把模型名发到错误端点。
        for item in providers:
            if not item.configured or str(item.model or "").strip() != requested_model:
                continue
            candidate = _canonical_model_provider(item.provider)
            if not requested or candidate == requested or (requested in _OPENAI_COMPATIBLE_PROVIDER_FAMILY and candidate in _OPENAI_COMPATIBLE_PROVIDER_FAMILY):
                return item
    if requested:
        return next((item for item in providers if _canonical_model_provider(item.provider) == requested and item.configured), None)
    for preferred in ("openai_compatible", "anthropic", "gemini", "ollama", "llamacpp", "lmstudio"):
        match = next((item for item in providers if _canonical_model_provider(item.provider) == preferred and item.configured), None)
        if match is not None:
            return match
    return None


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        if value:
            req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc


def _request_openai_compatible_review(
    prompt: str,
    provider: ModelProviderConfig,
    model: str,
    environ: dict[str, str],
    timeout: float,
) -> str:
    api_key = environ.get("OPENAI_API_KEY") or environ.get("SPIRITKIN_OPENAI_API_KEY") or ""
    if provider.api_key:
        api_key = provider.api_key
    base_url = provider.endpoint.rstrip("/")
    data = _post_json(
        f"{base_url}/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "You review coding-agent skills and produce concise, actionable corrections."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        {"Authorization": f"Bearer {api_key}"},
        timeout,
    )
    return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "")


def _request_anthropic_review(
    prompt: str,
    provider: ModelProviderConfig,
    model: str,
    environ: dict[str, str],
    timeout: float,
) -> str:
    data = _post_json(
        f"{provider.endpoint.rstrip('/')}/v1/messages",
        {
            "model": model,
            "max_tokens": 1600,
            "temperature": 0.2,
            "system": "You review coding-agent skills and produce concise, actionable corrections.",
            "messages": [{"role": "user", "content": prompt}],
        },
        {"x-api-key": provider.api_key or environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"},
        timeout,
    )
    parts = data.get("content") or []
    return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def _request_gemini_review(
    prompt: str,
    provider: ModelProviderConfig,
    model: str,
    environ: dict[str, str],
    timeout: float,
) -> str:
    api_key = provider.api_key or environ.get("GEMINI_API_KEY") or environ.get("GOOGLE_API_KEY") or ""
    endpoint = f"{provider.endpoint.rstrip('/')}/v1beta/models/{parse.quote(model, safe='')}:generateContent?key={parse.quote(api_key, safe='')}"
    data = _post_json(
        endpoint,
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}},
        {},
        timeout,
    )
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def _request_ollama_review(prompt: str, provider: ModelProviderConfig, model: str, timeout: float) -> str:
    data = _post_json(
        f"{provider.endpoint.rstrip('/')}/api/chat",
        {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You review coding-agent skills and produce concise, actionable corrections."},
                {"role": "user", "content": prompt},
            ],
        },
        {},
        timeout,
    )
    return str(data.get("message", {}).get("content") or data.get("response") or "")


def build_training_package_from_learning_records(records: list[LearningRecord]) -> SelfTrainingPackage:
    examples: list[TrainingExample] = []
    for index, record in enumerate(records, start=1):
        if not record.problem.strip() or not record.correction.strip():
            continue
        examples.append(TrainingExample(
            example_id=record.record_id or f"learning-{index}",
            source=record.source,
            task_type="skill_or_code_correction" if record.skill_name else "human_feedback",
            input_text=record.problem,
            expected_behavior=record.correction,
            weight=1.0,
            metadata={
                "skill_name": record.skill_name,
                "project": record.project,
                "tags": list(record.tags),
                **record.metadata,
            },
        ))
    return SelfTrainingPackage(
        package_id=f"learning-package-{int(_now())}",
        generated_at=_now(),
        purpose="Convert human/mainstream-model corrections into SpiritKinAI training samples",
        examples=examples,
        evaluator_notes=[
            "人工或外部大模型纠错先进入训练样本和 eval，不直接覆盖生产 Skill。",
            "Skill 修正应先通过 replay / verify，再由人工确认是否提升为正式 Skill。",
        ],
        safety_notes=[
            "不要保存密钥、完整私有源码或不可公开的数据。",
            "外部模型建议必须被视为候选，不应自动执行高风险改动。",
        ],
    )


def export_learning_dataset(
    *,
    records: list[LearningRecord] | None = None,
    output_path: str | os.PathLike[str] | None = None,
    register: bool = True,
) -> dict[str, Any]:
    selected_records = records if records is not None else load_learning_records()
    package = build_training_package_from_learning_records(selected_records)
    export = export_self_training_dataset(package, resolve_training_dataset(output_path))
    gate = evaluate_dataset_gate(export.path)
    dataset_card = None
    if register:
        dataset_card = register_training_dataset(
            export.path,
            source="learning_records",
            source_counts={"learning_records": len(selected_records), "examples": int(export.count)},
            excluded_count=max(0, len(selected_records) - int(export.count)),
            metadata={
                "package_id": package.package_id,
                "purpose": package.purpose,
                "task_types": dict(export.task_types),
            },
            gate=gate,
        )
    return {
        "package": package.snapshot(),
        "export": export.snapshot(),
        "dataset_card": dataset_card.snapshot() if dataset_card else {},
        "dataset_gate": gate.snapshot(),
        "dataset_registry": load_dataset_registry(limit=20),
    }


def build_self_improvement_summary(
    improvement_report: dict[str, Any] | None = None,
    *,
    record_count: int = 0,
    dataset_count: int = 0,
    dataset_path: str = "",
) -> dict[str, Any]:
    report = dict(improvement_report or {})
    actions = [dict(item) for item in report.get("actions") or [] if isinstance(item, dict)]
    eval_cases = [dict(item) for item in report.get("eval_cases") or [] if isinstance(item, dict)]
    training_package = report.get("training_package") if isinstance(report.get("training_package"), dict) else {}
    training_examples = [dict(item) for item in training_package.get("examples") or [] if isinstance(item, dict)]
    performance = report.get("performance") if isinstance(report.get("performance"), dict) else {}
    trajectory = report.get("trajectory") if isinstance(report.get("trajectory"), dict) else {}
    failure_samples = report.get("failure_samples") if isinstance(report.get("failure_samples"), dict) else {}
    signal_count = len(actions) + len(eval_cases) + len(training_examples) + int(dataset_count) + int(record_count)
    high_priority_count = sum(1 for item in actions if str(item.get("priority") or "").lower() == "high")
    status = "needs_attention" if high_priority_count else ("active" if signal_count else "collecting")
    return {
        "status": status,
        "loop": {
            "runtime_feedback_collected": bool(record_count or trajectory or failure_samples),
            "training_dataset_exported": bool(dataset_count),
            "improvement_actions_generated": bool(actions),
            "eval_cases_generated": bool(eval_cases),
            "auto_code_apply_enabled": False,
            "human_review_required": True,
        },
        "counts": {
            "learning_records": int(record_count),
            "dataset_examples": int(dataset_count),
            "improvement_actions": len(actions),
            "high_priority_actions": high_priority_count,
            "eval_cases": len(eval_cases),
            "self_training_examples": len(training_examples),
            "performance_agents": len(performance.get("ranking") or []) if isinstance(performance, dict) else 0,
            "trajectory_bottlenecks": len(trajectory.get("bottlenecks") or []) if isinstance(trajectory, dict) else 0,
            "failure_error_types": len(failure_samples.get("by_error_code") or {}) if isinstance(failure_samples, dict) else 0,
        },
        "dataset": {
            "path": dataset_path,
            "count": int(dataset_count),
        },
        "latest_actions": actions[:5],
        "training_package": {
            "package_id": str(training_package.get("package_id") or ""),
            "purpose": str(training_package.get("purpose") or ""),
            "example_count": len(training_examples),
            "generated_at": float(training_package.get("generated_at") or 0.0),
        },
        "next_steps": _self_improvement_next_steps(status, actions, dataset_count),
    }


def _self_improvement_next_steps(status: str, actions: list[dict[str, Any]], dataset_count: int) -> list[dict[str, Any]]:
    if actions:
        return [
            {
                "id": "review_improvement_actions",
                "priority": "high" if status == "needs_attention" else "medium",
                "title": "审核自我改进建议",
                "detail": "先把建议转成 eval、路由权重或 Skill 候选，再通过人工确认进入代码或配置。",
            },
            {
                "id": "promote_eval_cases",
                "priority": "medium",
                "title": "把失败轨迹沉淀为回归用例",
                "detail": "优先处理重复失败的阶段和错误类型，避免同类问题再次进入运行链路。",
            },
        ]
    if dataset_count:
        return [
            {
                "id": "run_training_review",
                "priority": "medium",
                "title": "审查训练样本并运行验证",
                "detail": "训练样本已经导出，下一步应让模型或人工复核样本质量，再决定是否加入训练或评测。",
            }
        ]
    return [
        {
            "id": "collect_feedback",
            "priority": "medium",
            "title": "继续收集失败轨迹和人工纠错",
            "detail": "当前还没有足够信号生成改进动作；先通过桌面反馈、Skill 失败记录或 replay 收集样本。",
        }
    ]


def build_learning_workflow_report(*, include_improvement: bool = True) -> LearningWorkflowReport:
    records = load_learning_records()
    dataset = export_learning_dataset(records=records, register=False)
    improvement = SelfImprovementLoop().build_report().snapshot() if include_improvement else {}
    export = dataset["export"]
    return LearningWorkflowReport(
        generated_at=_now(),
        records=tuple(records),
        model_providers=tuple(discover_model_providers()),
        dataset_path=str(export.get("path") or ""),
        dataset_count=int(export.get("count") or 0),
        model_provider_settings=load_model_provider_settings(),
        assist_models=tuple(load_assist_models()),
        review_committee_policy=load_review_committee_policy(),
        improvement_report=improvement,
    )
