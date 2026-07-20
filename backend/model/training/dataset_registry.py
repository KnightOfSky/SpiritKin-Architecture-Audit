from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET_REGISTRY_SCHEMA_VERSION = "spiritkin.training_dataset_registry.v1"
DEFAULT_DATASET_REGISTRY_PATH = "state/training/datasets.jsonl"
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),
)
HIGH_RISK_TERMS = (
    "delete",
    "remove-item",
    "format",
    "shutdown",
    "rm -rf",
    "删除",
    "格式化",
    "关机",
    "远程执行",
)


@dataclass(frozen=True)
class DatasetGateResult:
    allowed: bool
    status: str
    issues: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "issues": [dict(item) for item in self.issues],
            "warnings": [dict(item) for item in self.warnings],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class DatasetCard:
    dataset_id: str
    dataset_path: str
    status: str
    source: str = "training_dataset_builder"
    source_counts: dict[str, int] = field(default_factory=dict)
    example_count: int = 0
    excluded_count: int = 0
    quality_gate: dict[str, Any] = field(default_factory=dict)
    privacy_scan: dict[str, Any] = field(default_factory=dict)
    task_types: dict[str, int] = field(default_factory=dict)
    base_model_target: str = ""
    reviewer: str = ""
    linked_eval_report: str = ""
    export_kind: str = "chat_jsonl"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": DATASET_REGISTRY_SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "dataset_path": self.dataset_path,
            "status": self.status,
            "source": self.source,
            "source_counts": dict(self.source_counts),
            "example_count": self.example_count,
            "excluded_count": self.excluded_count,
            "quality_gate": dict(self.quality_gate),
            "privacy_scan": dict(self.privacy_scan),
            "task_types": dict(self.task_types),
            "base_model_target": self.base_model_target,
            "reviewer": self.reviewer,
            "linked_eval_report": self.linked_eval_report,
            "export_kind": self.export_kind,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


def resolve_dataset_registry_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_DATASET_REGISTRY_PATH", DEFAULT_DATASET_REGISTRY_PATH)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def dataset_id_for_path(dataset_path: str | os.PathLike[str], *, prefix: str = "dataset") -> str:
    path = Path(dataset_path)
    resolved = str(path.resolve() if path.exists() else path.absolute())
    digest = hashlib.sha256(f"{resolved}:{time.time_ns()}".encode()).hexdigest()[:12]
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", path.stem).strip("_")[:48] or "training"
    return f"{prefix}-{safe_name}-{digest}"


def inspect_training_jsonl(dataset_path: str | os.PathLike[str], *, max_scan_chars: int = 4000) -> dict[str, Any]:
    path = Path(dataset_path)
    metrics: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "line_count": 0,
        "valid_json_count": 0,
        "chat_example_count": 0,
        "invalid_json_count": 0,
        "empty_line_count": 0,
        "task_types": {},
        "metadata_sources": {},
        "secret_hit_count": 0,
        "high_risk_hit_count": 0,
        "sample_hash": "",
    }
    if not path.exists():
        return metrics
    digest = hashlib.sha256()
    task_types: dict[str, int] = {}
    metadata_sources: dict[str, int] = {}
    secret_hits = 0
    high_risk_hits = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    metrics["empty_line_count"] += 1
                    continue
                metrics["line_count"] += 1
                digest.update(line[:max_scan_chars].encode("utf-8", errors="ignore"))
                secret_hits += _count_secret_hits(line[:max_scan_chars])
                high_risk_hits += _count_high_risk_hits(line[:max_scan_chars])
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    metrics["invalid_json_count"] += 1
                    continue
                metrics["valid_json_count"] += 1
                messages = row.get("messages") if isinstance(row, dict) else None
                if _is_chat_messages(messages):
                    metrics["chat_example_count"] += 1
                metadata = row.get("metadata") if isinstance(row, dict) and isinstance(row.get("metadata"), dict) else {}
                task_type = str(metadata.get("task_type") or metadata.get("builder_mode") or "unknown")
                task_types[task_type] = task_types.get(task_type, 0) + 1
                source = str(metadata.get("source") or metadata.get("source_type") or "unknown")
                metadata_sources[source] = metadata_sources.get(source, 0) + 1
    except OSError as exc:
        metrics["read_error"] = f"{type(exc).__name__}: {exc}"
    metrics["task_types"] = task_types
    metrics["metadata_sources"] = metadata_sources
    metrics["secret_hit_count"] = secret_hits
    metrics["high_risk_hit_count"] = high_risk_hits
    metrics["sample_hash"] = digest.hexdigest()
    return metrics


def evaluate_dataset_gate(
    dataset_path: str | os.PathLike[str],
    *,
    min_examples: int = 1,
    allow_secrets: bool = False,
    allow_high_risk: bool = False,
) -> DatasetGateResult:
    metrics = inspect_training_jsonl(dataset_path)
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not metrics.get("exists"):
        issues.append({"code": "dataset_missing", "message": "dataset file does not exist"})
    if metrics.get("invalid_json_count", 0):
        issues.append({"code": "invalid_jsonl", "count": metrics.get("invalid_json_count", 0)})
    if int(metrics.get("chat_example_count") or 0) < int(min_examples):
        issues.append({"code": "too_few_examples", "count": metrics.get("chat_example_count", 0), "min_examples": min_examples})
    if int(metrics.get("secret_hit_count") or 0) and not allow_secrets:
        issues.append({"code": "secret_like_content", "count": metrics.get("secret_hit_count", 0)})
    if int(metrics.get("high_risk_hit_count") or 0):
        item = {"code": "high_risk_instruction_content", "count": metrics.get("high_risk_hit_count", 0)}
        if allow_high_risk:
            warnings.append(item)
        else:
            issues.append(item)
    status = "verified" if not issues else "rejected"
    return DatasetGateResult(allowed=not issues, status=status, issues=tuple(issues), warnings=tuple(warnings), metrics=metrics)


def register_training_dataset(
    dataset_path: str | os.PathLike[str],
    *,
    source: str = "training_dataset_builder",
    source_counts: dict[str, int] | None = None,
    excluded_count: int = 0,
    base_model_target: str = "",
    reviewer: str = "",
    linked_eval_report: str = "",
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
    registry_path: str | os.PathLike[str] | None = None,
    gate: DatasetGateResult | None = None,
) -> DatasetCard:
    gate = gate or evaluate_dataset_gate(dataset_path)
    metrics = gate.metrics
    card = DatasetCard(
        dataset_id=dataset_id_for_path(dataset_path),
        dataset_path=str(Path(dataset_path)),
        status=status or ("training_ready" if gate.allowed else "rejected"),
        source=source,
        source_counts=dict(source_counts or {}),
        example_count=int(metrics.get("chat_example_count") or 0),
        excluded_count=int(excluded_count),
        quality_gate=gate.snapshot(),
        privacy_scan={
            "secret_hit_count": int(metrics.get("secret_hit_count") or 0),
            "high_risk_hit_count": int(metrics.get("high_risk_hit_count") or 0),
            "sample_hash": str(metrics.get("sample_hash") or ""),
        },
        task_types={str(k): int(v) for k, v in dict(metrics.get("task_types") or {}).items()},
        base_model_target=base_model_target,
        reviewer=reviewer,
        linked_eval_report=linked_eval_report,
        metadata=dict(metadata or {}),
    )
    append_dataset_card(card, registry_path=registry_path)
    return card


def append_dataset_card(card: DatasetCard, *, registry_path: str | os.PathLike[str] | None = None) -> None:
    path = resolve_dataset_registry_path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(card.snapshot(), ensure_ascii=False) + "\n")


def load_dataset_registry(*, registry_path: str | os.PathLike[str] | None = None, limit: int = 100) -> dict[str, Any]:
    path = resolve_dataset_registry_path(registry_path)
    cards: list[dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    cards.append(item)
        except OSError:
            pass
    cards = cards[-max(1, int(limit)) :]
    return {
        "schema_version": DATASET_REGISTRY_SCHEMA_VERSION,
        "path": str(path),
        "dataset_count": len(cards),
        "datasets": cards,
    }


def _is_chat_messages(messages: Any) -> bool:
    if not isinstance(messages, list) or len(messages) < 2:
        return False
    roles = [str(item.get("role") or "") for item in messages if isinstance(item, dict)]
    return "user" in roles and "assistant" in roles


def _count_secret_hits(text: str) -> int:
    return sum(len(pattern.findall(text or "")) for pattern in SECRET_PATTERNS)


def _count_high_risk_hits(text: str) -> int:
    lowered = str(text or "").lower()
    return sum(1 for term in HIGH_RISK_TERMS if term.lower() in lowered)
