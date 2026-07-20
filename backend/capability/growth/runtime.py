from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from backend.capability.growth.builder_artifacts import GrowthBuilderArtifactStore
from backend.capability.growth.builder_verification import GrowthBuilderVerifier
from backend.capability.growth.remote_research import GitHubRepositoryResearcher
from backend.capability.growth.sandbox_bundle import GrowthSandboxBundleStore
from backend.capability.growth.sandbox_executor import GrowthDockerSandboxExecutor
from backend.capability.growth.sandbox_runtime import GrowthSandboxRuntimeProbe
from backend.evaluation import BenchmarkRuntime, build_model_jury_prompt, build_model_jury_report
from backend.state_store import resolve_state_path

SCHEMA_VERSION = "spiritkin.growth_runtime.v1"
DEFAULT_EVENT_LOG = "state/growth/events.jsonl"
DEFAULT_REGISTRY_LOG = "state/growth/registry.jsonl"

GROWTH_STAGES: tuple[str, ...] = (
    "gap_analysis",
    "research",
    "design",
    "sandbox",
    "dry_run",
    "benchmark",
    "review",
    "registry",
)

KIND_STAGES: dict[str, tuple[str, ...]] = {
    "capability": ("gap_analysis", "research", "design", "benchmark", "review", "registry"),
    "workflow": ("gap_analysis", "design", "dry_run", "benchmark", "review", "registry"),
    "skill": ("gap_analysis", "research", "design", "sandbox", "dry_run", "benchmark", "review", "registry"),
    "tool": ("gap_analysis", "research", "sandbox", "dry_run", "benchmark", "review", "registry"),
    "code": ("gap_analysis", "design", "sandbox", "dry_run", "benchmark", "review", "registry"),
    "model": ("gap_analysis", "research", "benchmark", "review", "registry"),
}

RISK_LEVEL_BY_KIND = {
    "capability": "medium",
    "workflow": "medium",
    "skill": "high",
    "tool": "high",
    "code": "high",
    "model": "high",
}

ESCALATION_TARGETS: dict[str, tuple[str, ...]] = {
    "capability": ("workflow", "skill", "tool", "code", "model", "human"),
    "workflow": ("skill", "tool", "code", "model", "human"),
    "skill": ("tool", "code", "model", "human"),
    "tool": ("code", "model", "human"),
    "code": ("model", "human"),
    "model": ("human",),
}

INTERNAL_PATH_KEYS = {"path", "root", "event_path", "registry_path"}


def _candidate_risk(kind: str) -> dict[str, Any]:
    level = RISK_LEVEL_BY_KIND.get(kind, "medium")
    return {
        "level": level,
        "reasons": ["growth_candidate_requires_human_review"],
        "requires_human_review": True,
    }


def _builder_artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    research = dict(artifact.get("research") or {})
    verification = dict(artifact.get("verification_plan") or {})
    registry = dict(artifact.get("registry_plan") or {})
    human = dict(artifact.get("human_escalation") or {})
    sandbox = dict(artifact.get("sandbox_plan") or {})
    bundle = dict(sandbox.get("bundle") or {})
    execution = dict(verification.get("latest_sandbox_execution") or {})
    return {
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "status": str(artifact.get("status") or "prepared"),
        "inventory_match_count": int(research.get("inventory_match_count") or 0),
        "verification_checks": [str(item) for item in verification.get("checks") or []],
        "verification_status": str(verification.get("execution_status") or "not_run"),
        "registry_target": str(registry.get("target") or ""),
        "human_required": bool(human.get("required")),
        "sandbox_bundle_prepared": bool(sandbox.get("bundle_prepared")),
        "sandbox_bundle_id": str(bundle.get("bundle_id") or ""),
        "sandbox_bundle_file_count": int(bundle.get("file_count") or 0),
        "sandbox_execution_status": str(verification.get("sandbox_execution_status") or "not_run"),
        "sandbox_execution_id": str(execution.get("execution_id") or ""),
        "sandbox_exit_code": int(execution.get("exit_code") or 0),
        "activation_enabled": False,
    }


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Add governance fields to candidates written by older runtime versions."""

    normalized = dict(candidate)
    kind = str(normalized.get("kind") or "capability").strip().lower()
    normalized["kind"] = kind if kind in KIND_STAGES else "capability"
    normalized["stages"] = list(KIND_STAGES[normalized["kind"]])
    normalized.setdefault("current_stage", normalized["stages"][0])
    normalized.setdefault("status", "candidate")
    normalized.setdefault("promotion_status", normalized["status"])
    normalized.setdefault("review", {"required": True, "approved": False, "reviewer": "", "evidence": {}})
    normalized.setdefault("activation", {"enabled": False, "mode": "manual_activation_required"})
    candidate_id = str(normalized.get("candidate_id") or "").strip()
    lineage = normalized.get("lineage") if isinstance(normalized.get("lineage"), dict) else {}
    parent_candidate_id = str(lineage.get("parent_candidate_id") or "").strip()
    normalized["lineage"] = {
        "parent_candidate_id": parent_candidate_id,
        "root_candidate_id": str(lineage.get("root_candidate_id") or candidate_id).strip(),
        "depth": max(0, int(lineage.get("depth") or (1 if parent_candidate_id else 0))),
        "transition": str(lineage.get("transition") or "").strip(),
    }
    resolution = normalized.get("resolution") if isinstance(normalized.get("resolution"), dict) else {}
    normalized["resolution"] = {
        "status": str(resolution.get("status") or "unrouted").strip(),
        "target_kind": str(resolution.get("target_kind") or "").strip(),
        "child_candidate_id": str(resolution.get("child_candidate_id") or "").strip(),
        "requires_human": bool(resolution.get("requires_human", False)),
        **{key: value for key, value in resolution.items() if key not in {"status", "target_kind", "child_candidate_id", "requires_human"}},
    }
    risk = normalized.get("risk") if isinstance(normalized.get("risk"), dict) else {}
    normalized["risk"] = {**_candidate_risk(normalized["kind"]), **risk, "requires_human_review": True}
    return normalized


def _without_internal_paths(value: Any) -> Any:
    """Remove managed filesystem locations from client-facing Growth data."""

    if isinstance(value, dict):
        return {
            key: _without_internal_paths(item)
            for key, item in value.items()
            if key not in INTERNAL_PATH_KEYS and not key.endswith("_path")
        }
    if isinstance(value, list):
        return [_without_internal_paths(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_internal_paths(item) for item in value)
    return value


def _path(value: str | os.PathLike[str] | None, env_key: str, default: str) -> Path:
    return resolve_state_path(env_key, default, value)


def _now() -> float:
    return time.time()


def _read_jsonl(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)) :]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        return []
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def _safe_id(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return (normalized[:96] or fallback).strip("-._") or fallback


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _latest(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get(key) or "").strip()
        if identity:
            # Candidate lifecycle events such as ``tool_proposed`` carry the
            # id for traceability but are not candidate snapshots. Keep the
            # latest row that still has the candidate contract fields.
            if key == "candidate_id" and not (row.get("status") or row.get("kind")):
                continue
            latest[identity] = dict(row)
    return latest


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


class GrowthRuntime:
    """Governed growth pipeline; it creates reviewable artifacts, never live code."""

    def __init__(
        self,
        *,
        event_path: str | os.PathLike[str] | None = None,
        registry_path: str | os.PathLike[str] | None = None,
        artifact_root: str | os.PathLike[str] | None = None,
        sandbox_state_path: str | os.PathLike[str] | None = None,
        benchmark_log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.event_path = _path(event_path, "SPIRITKIN_GROWTH_EVENT_LOG", DEFAULT_EVENT_LOG)
        self.registry_path = _path(registry_path, "SPIRITKIN_GROWTH_REGISTRY_LOG", DEFAULT_REGISTRY_LOG)
        self.artifact_store = GrowthBuilderArtifactStore(artifact_root)
        self.builder_verifier = GrowthBuilderVerifier(self.artifact_store.root)
        self.remote_researcher = GitHubRepositoryResearcher(self.artifact_store.root)
        self.sandbox_runtime = GrowthSandboxRuntimeProbe(sandbox_state_path)
        self.sandbox_bundle_store = GrowthSandboxBundleStore(self.artifact_store.root)
        self.sandbox_executor = GrowthDockerSandboxExecutor(
            self.artifact_store.root,
            self.sandbox_runtime,
            self.sandbox_bundle_store,
        )
        if benchmark_log_path is None and event_path is not None:
            benchmark_log_path = Path(event_path).parent / "benchmarks.jsonl"
        self.benchmarks = BenchmarkRuntime(benchmark_log_path)

    def _events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.event_path)

    def _candidates(self) -> dict[str, dict[str, Any]]:
        return {
            candidate_id: _normalize_candidate(candidate)
            for candidate_id, candidate in _latest(self._events(), "candidate_id").items()
        }

    def _candidate(
        self,
        *,
        kind: str,
        request: str,
        title: str,
        requirements: list[str],
        evidence: dict[str, Any] | None = None,
        domain: str = "general",
        workspace_id: str = "",
        metadata: dict[str, Any] | None = None,
        parent_candidate_id: str = "",
        root_candidate_id: str = "",
        lineage_depth: int = 0,
    ) -> dict[str, Any]:
        normalized_kind = kind if kind in KIND_STAGES else "capability"
        identity = {
            "kind": normalized_kind,
            "request": request.strip(),
            "requirements": sorted(set(requirements)),
            "domain": domain,
            "workspace_id": workspace_id,
        }
        if parent_candidate_id:
            identity["parent_candidate_id"] = parent_candidate_id
        candidate_id = f"growth-{normalized_kind}-{_digest(identity)}"
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "kind": normalized_kind,
            "title": title.strip() or f"候选 {normalized_kind}",
            "request": request.strip(),
            "domain": domain.strip() or "general",
            "workspace_id": workspace_id.strip(),
            "requirements": sorted(set(requirements)),
            "status": "candidate",
            "promotion_status": "candidate",
            "current_stage": "gap_analysis",
            "stages": list(KIND_STAGES[normalized_kind]),
            "review": {"required": True, "approved": False, "reviewer": "", "evidence": {}},
            "activation": {"enabled": False, "mode": "manual_activation_required"},
            "lineage": {
                "parent_candidate_id": parent_candidate_id,
                "root_candidate_id": root_candidate_id or candidate_id,
                "depth": max(0, int(lineage_depth)),
                "transition": "",
            },
            "resolution": {
                "status": "unrouted",
                "target_kind": "",
                "child_candidate_id": "",
                "requires_human": False,
            },
            "risk": _candidate_risk(normalized_kind),
            "evidence": dict(evidence or {}),
            "metadata": {"auto_apply": False, **dict(metadata or {})},
            "created_at": _now(),
            "updated_at": _now(),
        }

    def _store_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        existing = self._candidates().get(str(candidate.get("candidate_id") or ""))
        if existing:
            return existing
        return _append_jsonl(self.event_path, {"event": "candidate_created", **candidate})

    def analyze_gap(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = str(payload.get("request") or payload.get("intent") or payload.get("user_input") or "").strip()
        required = _as_strings(payload.get("required_capabilities") or payload.get("capabilities") or payload.get("needs"))
        available = {item.lower() for item in _as_strings(payload.get("available_capabilities"))}
        missing = [item for item in required if item.lower() not in available]
        gap_id = f"gap-{_digest({'request': request, 'required': sorted(required), 'available': sorted(available)})}"
        gap = {
            "gap_id": gap_id,
            "request": request,
            "required_capabilities": required,
            "available_capabilities": sorted(available),
            "missing_capabilities": missing,
            "status": "gap_found" if missing else ("no_gap" if required else "needs_planner"),
            "created_at": _now(),
        }
        candidates: list[dict[str, Any]] = []
        for capability in missing:
            kind = str(payload.get("kind") or "capability").strip().lower() or "capability"
            candidate = self._candidate(
                kind=kind,
                request=request,
                title=f"补齐能力：{capability}",
                requirements=[capability],
                domain=str(payload.get("domain") or "general"),
                workspace_id=str(payload.get("workspace_id") or ""),
                evidence={"gap_id": gap_id, "source": "planner_gap_analysis", "missing_capability": capability},
            )
            candidates.append(self._store_candidate(candidate))
        if not any(row.get("event") == "gap_analyzed" and row.get("gap_id") == gap_id for row in self._events()):
            _append_jsonl(self.event_path, {"event": "gap_analyzed", **gap})
        return {"ok": True, "gap": gap, "candidates": candidates, "growth": self.snapshot()}

    def escalate_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Route one unresolved candidate to the next governed Builder or a human."""

        candidate_id = str(payload.get("candidate_id") or "").strip()
        target_kind = str(payload.get("target_kind") or payload.get("next_kind") or "").strip().lower()
        reason = str(payload.get("reason") or "").strip()
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        requested_by = str(payload.get("requested_by") or payload.get("actor") or payload.get("submitted_by") or "").strip()
        if not candidate_id or not target_kind or not reason or not evidence or not requested_by:
            raise ValueError("candidate_id, target_kind, reason, evidence and requested_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        source_kind = str(candidate.get("kind") or "capability").strip().lower()
        if str(candidate.get("status") or "candidate") != "candidate":
            raise PermissionError("only active candidates can be escalated")
        allowed_targets = ESCALATION_TARGETS.get(source_kind, ("human",))
        if target_kind not in allowed_targets:
            raise ValueError(f"invalid growth escalation: {source_kind} -> {target_kind}")
        current_resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
        if str(current_resolution.get("status") or "unrouted") != "unrouted":
            raise PermissionError("candidate already has a growth resolution route")

        now = _now()
        resolution = {
            "status": "needs_human" if target_kind == "human" else "escalated",
            "target_kind": target_kind,
            "child_candidate_id": "",
            "requires_human": target_kind == "human",
            "reason": reason,
            "evidence": dict(evidence),
            "requested_by": requested_by,
            "routed_at": now,
        }
        if target_kind == "human":
            updated = {
                **candidate,
                "status": "needs_human",
                "promotion_status": "needs_human",
                "resolution": resolution,
                "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
                "updated_at": now,
            }
            _append_jsonl(self.event_path, {"event": "candidate_escalated", **updated})
            return {
                "ok": True,
                "candidate": updated,
                "child_candidate": None,
                "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
            }

        requirements = _as_strings(payload.get("requirements")) or _as_strings(candidate.get("requirements"))
        requirement = requirements[0] if requirements else str(candidate.get("title") or candidate_id)
        source_lineage = candidate.get("lineage") if isinstance(candidate.get("lineage"), dict) else {}
        root_candidate_id = str(source_lineage.get("root_candidate_id") or candidate_id).strip()
        depth = max(0, int(source_lineage.get("depth") or 0)) + 1
        child = self._candidate(
            kind=target_kind,
            request=str(payload.get("request") or candidate.get("request") or reason),
            title=str(payload.get("title") or f"候选 {target_kind.title()}：{requirement}"),
            requirements=requirements or [requirement],
            domain=str(candidate.get("domain") or "general"),
            workspace_id=str(candidate.get("workspace_id") or ""),
            evidence={
                "source": "growth_escalation",
                "parent_candidate_id": candidate_id,
                "escalation_reason": reason,
                "transition_evidence": dict(evidence),
            },
            metadata={"auto_apply": False, "created_by_growth_route": True},
            parent_candidate_id=candidate_id,
            root_candidate_id=root_candidate_id,
            lineage_depth=depth,
        )
        child["lineage"]["transition"] = f"{source_kind}->{target_kind}"
        stored_child = self._store_candidate(child)
        resolution["child_candidate_id"] = str(stored_child.get("candidate_id") or "")
        updated = {
            **candidate,
            "status": "escalated",
            "promotion_status": "escalated",
            "resolution": resolution,
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
            "updated_at": now,
        }
        _append_jsonl(self.event_path, {"event": "candidate_escalated", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "child_candidate": stored_child,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def mine_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps = [dict(item) for item in payload.get("steps") or [] if isinstance(item, dict)]
        if len(steps) < 2:
            raise ValueError("at least two workflow steps are required")
        labels = [str(item.get("capability_id") or item.get("operation") or item.get("tool_name") or "step").strip() for item in steps]
        candidate = self._candidate(
            kind="workflow",
            request=str(payload.get("request") or "挖掘重复任务流程"),
            title=str(payload.get("title") or "候选 Workflow"),
            requirements=labels,
            domain=str(payload.get("domain") or "general"),
            workspace_id=str(payload.get("workspace_id") or ""),
            evidence={"source": "trajectory_mining", "steps": steps, "occurrence_count": int(payload.get("occurrence_count") or 1)},
        )
        stored = self._store_candidate(candidate)
        _append_jsonl(self.event_path, {"event": "workflow_mined", "candidate_id": stored["candidate_id"], "occurrence_count": int(payload.get("occurrence_count") or 1), "at": _now()})
        return {"ok": True, "candidate": stored, "growth": self.snapshot()}

    def propose_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        failures = [dict(item) for item in payload.get("failures") or [] if isinstance(item, dict)]
        evidence_count = int(payload.get("failure_count") or len(failures) or 1)
        requirement = str(payload.get("capability_id") or payload.get("missing_capability") or "skill.improvement").strip()
        candidate = self._candidate(
            kind="skill",
            request=str(payload.get("request") or "根据失败轨迹生成 Skill"),
            title=str(payload.get("title") or f"候选 Skill：{requirement}"),
            requirements=[requirement],
            domain=str(payload.get("domain") or "general"),
            workspace_id=str(payload.get("workspace_id") or ""),
            evidence={"source": "failure_trajectory", "failure_count": evidence_count, "failures": failures[:20]},
        )
        stored = self._store_candidate(candidate)
        _append_jsonl(self.event_path, {"event": "skill_proposed", "candidate_id": stored["candidate_id"], "failure_count": evidence_count, "at": _now()})
        return {"ok": True, "candidate": stored, "growth": self.snapshot()}

    def propose_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        requirement = str(payload.get("capability_id") or payload.get("missing_capability") or payload.get("requirement") or "").strip()
        if not requirement:
            raise ValueError("capability_id, missing_capability or requirement is required")
        candidate = self._candidate(
            kind="code",
            request=str(payload.get("request") or f"编写代码实现 {requirement}"),
            title=str(payload.get("title") or f"候选 Code：{requirement}"),
            requirements=[requirement],
            domain=str(payload.get("domain") or "general"),
            workspace_id=str(payload.get("workspace_id") or ""),
            evidence={
                "source": str(payload.get("source") or "code_gap"),
                "sandbox_required": True,
                "compile_and_test_required": True,
            },
        )
        stored = self._store_candidate(candidate)
        _append_jsonl(self.event_path, {"event": "code_proposed", "candidate_id": stored["candidate_id"], "at": _now()})
        return {"ok": True, "candidate": stored, "growth": self.snapshot()}

    def propose_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        requirement = str(payload.get("model_id") or payload.get("capability_id") or payload.get("requirement") or "model.route" ).strip()
        candidate = self._candidate(
            kind="model",
            request=str(payload.get("request") or f"寻找或评测模型 {requirement}"),
            title=str(payload.get("title") or f"候选 Model：{requirement}"),
            requirements=[requirement],
            domain=str(payload.get("domain") or "system"),
            workspace_id=str(payload.get("workspace_id") or ""),
            evidence={
                "source": str(payload.get("source") or "model_gap"),
                "deploy_allowed": False,
                "benchmark_required": True,
            },
        )
        stored = self._store_candidate(candidate)
        _append_jsonl(self.event_path, {"event": "model_proposed", "candidate_id": stored["candidate_id"], "at": _now()})
        return {"ok": True, "candidate": stored, "growth": self.snapshot()}

    def observe_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest one failure trajectory and create a Skill candidate at a bounded threshold."""

        stage = str(payload.get("stage") or "runtime").strip()
        tool_name = str(payload.get("tool_name") or payload.get("operation") or "").strip()
        error_code = str(payload.get("error_code") or "unknown_failure").strip()
        message = str(payload.get("message") or payload.get("error") or "failure").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        failure_key = _safe_id("|".join(part for part in (workspace_id, stage, tool_name, error_code, message[:120]) if part), "failure")
        previous = [
            row for row in self._events()
            if row.get("event") == "failure_observed"
            and str(row.get("failure_key") or "") == failure_key
        ]
        count = len(previous) + 1
        observation = {
            "event": "failure_observed",
            "failure_key": failure_key,
            "failure_count": count,
            "stage": stage,
            "tool_name": tool_name,
            "error_code": error_code,
            "message": message[:500],
            "workspace_id": workspace_id,
            "trajectory_id": str(payload.get("trajectory_id") or ""),
            "at": _now(),
        }
        _append_jsonl(self.event_path, observation)
        threshold = max(2, min(1000, int(os.getenv("SPIRITKIN_GROWTH_FAILURE_THRESHOLD", "5") or "5")))
        candidate = None
        if count == threshold:
            result = self.propose_skill(
                {
                    "request": str(payload.get("request") or f"修复重复失败：{failure_key}"),
                    "title": str(payload.get("title") or f"失败轨迹改进：{tool_name or stage}"),
                    "capability_id": f"failure.{failure_key}",
                    "domain": str(payload.get("domain") or "general"),
                    "workspace_id": workspace_id,
                    "failure_count": count,
                    "failures": [
                        {
                            "stage": stage,
                            "tool_name": tool_name,
                            "error_code": error_code,
                            "message": message[:500],
                            "trajectory_id": observation["trajectory_id"],
                        }
                    ],
                }
            )
            candidate = result.get("candidate")
        return {
            "ok": True,
            "observed": observation,
            "candidate": candidate,
            "growth": self.snapshot(workspace_id=workspace_id or None),
        }

    def observe_trajectory(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        """Connect normalized Runtime trajectories to failure and workflow growth."""

        if not isinstance(trajectory, dict):
            return {"ok": False, "observed": False}
        metadata = dict(trajectory.get("metadata") or {}) if isinstance(trajectory.get("metadata"), dict) else {}
        if not bool(trajectory.get("overall_success", False)):
            failed_step = next((item for item in trajectory.get("steps") or [] if isinstance(item, dict) and not item.get("success")), {})
            step_metadata = dict(failed_step.get("metadata") or {}) if isinstance(failed_step, dict) else {}
            return self.observe_failure(
                {
                    "stage": str(trajectory.get("bottleneck_stage") or failed_step.get("stage") or "runtime"),
                    "actor": str(trajectory.get("agent_id") or ""),
                    "message": str(failed_step.get("detail") or trajectory.get("execution_result") or "failure"),
                    "error_code": str(failed_step.get("error_code") or "unknown_failure"),
                    "tool_name": str(step_metadata.get("tool_name") or metadata.get("tool_name") or metadata.get("operation") or ""),
                    "operation": str(metadata.get("operation") or ""),
                    "request": str(trajectory.get("user_input") or ""),
                    "domain": str(trajectory.get("domain") or "general"),
                    "workspace_id": str(metadata.get("workspace_id") or trajectory.get("workspace_id") or ""),
                    "trajectory_id": str(trajectory.get("trajectory_id") or ""),
                }
            )

        workflow_name = str(metadata.get("workflow_name") or "").strip()
        steps = [item for item in trajectory.get("steps") or [] if isinstance(item, dict)]
        if not workflow_name:
            workflow_name = next((str(dict(item.get("metadata") or {}).get("workflow_name") or "").strip() for item in steps if isinstance(item.get("metadata"), dict)), "")
        if not workflow_name:
            return {"ok": True, "observed": False, "growth": self.snapshot()}
        workspace_id = str(metadata.get("workspace_id") or trajectory.get("workspace_id") or "").strip()
        run_id = str(metadata.get("run_id") or trajectory.get("trajectory_id") or _digest(trajectory)).strip()
        step = str(metadata.get("node_id") or metadata.get("operation") or "").strip()
        if not step and steps:
            first_metadata = dict(steps[0].get("metadata") or {})
            step = str(first_metadata.get("node_id") or first_metadata.get("operation") or first_metadata.get("tool_name") or steps[0].get("stage") or "step").strip()
        workflow_key = _safe_id(f"{workspace_id}|{workflow_name}", "workflow")
        existing = [row for row in self._events() if row.get("event") == "trajectory_observed" and row.get("workflow_key") == workflow_key]
        trajectory_id = str(trajectory.get("trajectory_id") or "")
        if trajectory_id and any(str(row.get("trajectory_id") or "") == trajectory_id for row in existing):
            return {"ok": True, "observed": False, "duplicate": True, "growth": self.snapshot(workspace_id=workspace_id or None)}
        observed = {
            "event": "trajectory_observed",
            "workflow_key": workflow_key,
            "workflow_name": workflow_name,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "trajectory_id": trajectory_id,
            "step": step or "step",
            "at": _now(),
        }
        _append_jsonl(self.event_path, observed)
        runs = {str(row.get("run_id") or "") for row in existing + [observed]}
        threshold = max(2, min(1000, int(os.getenv("SPIRITKIN_GROWTH_WORKFLOW_THRESHOLD", "5") or "5")))
        candidate = None
        if len(runs) == threshold:
            ordered_steps: list[str] = []
            for row in sorted(existing + [observed], key=lambda item: float(item.get("at") or 0)):
                value = str(row.get("step") or "step")
                if value not in ordered_steps:
                    ordered_steps.append(value)
            result = self.mine_workflow(
                {
                    "title": f"候选 Workflow：{workflow_name}",
                    "request": f"从轨迹挖掘 {workflow_name}",
                    "domain": str(trajectory.get("domain") or "general"),
                    "workspace_id": workspace_id,
                    "occurrence_count": len(runs),
                    "steps": [{"capability_id": item} for item in ordered_steps[:40]],
                }
            )
            candidate = result.get("candidate")
        return {"ok": True, "observed": observed, "candidate": candidate, "growth": self.snapshot(workspace_id=workspace_id or None)}

    def advance_stage(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        requested_stage = str(payload.get("stage") or payload.get("current_stage") or "").strip().lower()
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if not candidate_id or not requested_stage or not evidence:
            raise ValueError("candidate_id, stage and evidence are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can advance")
        stages = list(candidate.get("stages") or KIND_STAGES.get(str(candidate.get("kind") or "capability"), KIND_STAGES["capability"]))
        current = str(candidate.get("current_stage") or stages[0]).strip().lower()
        if requested_stage not in stages:
            raise ValueError(f"stage is not valid for candidate: {requested_stage}")
        try:
            current_index = stages.index(current)
            requested_index = stages.index(requested_stage)
        except ValueError as exc:
            raise ValueError("candidate stage is invalid") from exc
        if requested_index != current_index + 1:
            raise ValueError("candidate stages must advance one step at a time")
        if requested_stage == "registry":
            raise PermissionError("registry requires an approved human review")
        if requested_stage == "review":
            benchmark = self.benchmarks.latest_for_candidate(candidate_id)
            if not benchmark or not bool((benchmark.get("promotion_gate") or {}).get("passed")):
                raise PermissionError("a passed measured Benchmark is required before review")
        updated = {
            **candidate,
            "current_stage": requested_stage,
            "evidence": {**dict(candidate.get("evidence") or {}), requested_stage: dict(evidence)},
            "stage_submitted_by": str(payload.get("submitted_by") or payload.get("actor") or "growth_runtime").strip(),
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "candidate_stage_advanced", **updated})
        return {"ok": True, "candidate": updated, "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None)}

    def propose_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        requirement = str(payload.get("capability_id") or payload.get("missing_capability") or "").strip()
        if not requirement:
            raise ValueError("capability_id or missing_capability is required")
        candidate = self._candidate(
            kind="tool",
            request=str(payload.get("request") or f"寻找工具实现 {requirement}"),
            title=str(payload.get("title") or f"候选 Tool：{requirement}"),
            requirements=[requirement],
            domain=str(payload.get("domain") or "general"),
            workspace_id=str(payload.get("workspace_id") or ""),
            evidence={"source": "tool_gap", "research_targets": _as_strings(payload.get("research_targets")), "install_allowed": False},
        )
        stored = self._store_candidate(candidate)
        _append_jsonl(self.event_path, {"event": "tool_proposed", "candidate_id": stored["candidate_id"], "at": _now()})
        return {"ok": True, "candidate": stored, "growth": self.snapshot()}

    def research_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        researched_by = str(payload.get("researched_by") or payload.get("actor") or "").strip()
        if not candidate_id or not researched_by:
            raise ValueError("candidate_id and researched_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can be researched")
        if str(candidate.get("current_stage") or "") in {"review", "registry"}:
            raise PermissionError("candidate research must finish before review")
        report = self.remote_researcher.research(candidate, payload)
        report_summary = {
            "report_id": report["report_id"],
            "status": report["status"],
            "provider": report["provider"],
            "query": report["query"],
            "result_count": report["result_count"],
            "total_count": report["total_count"],
            "incomplete_results": report["incomplete_results"],
            "repositories": list(report["repositories"]),
            "rate_limit": dict(report["rate_limit"]),
            "network_accessed": True,
            "downloaded": False,
            "installed": False,
            "external_code_executed": False,
            "researched_by": researched_by,
            "created_at": report["created_at"],
            "activation_enabled": False,
        }
        updated = {
            **candidate,
            "evidence": {**dict(candidate.get("evidence") or {}), "remote_research": report_summary},
            "metadata": {**dict(candidate.get("metadata") or {}), "remote_research_completed": True, "auto_apply": False},
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "candidate_remote_researched", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "research_report": report,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def prepare_builder_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") in {"rejected", "registered", "escalated", "needs_human"}:
            raise PermissionError("closed or escalated candidates cannot prepare Builder artifacts")
        artifact = self.artifact_store.prepare(candidate, payload)
        artifact_summary = _builder_artifact_summary(artifact)
        updated = {
            **candidate,
            "evidence": {**dict(candidate.get("evidence") or {}), "builder_artifact": artifact_summary},
            "metadata": {**dict(candidate.get("metadata") or {}), "builder_prepared": True, "auto_apply": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "builder_artifact_prepared", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "builder_artifact": artifact,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def verify_builder_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can run Builder verification")
        allowed_stages = {
            "capability": {"design"},
            "workflow": {"dry_run", "benchmark"},
            "skill": {"sandbox", "dry_run", "benchmark"},
            "tool": {"sandbox", "dry_run", "benchmark"},
            "code": {"sandbox", "dry_run", "benchmark"},
            "model": {"benchmark"},
        }
        kind = str(candidate.get("kind") or "capability")
        current_stage = str(candidate.get("current_stage") or "gap_analysis")
        if current_stage not in allowed_stages.get(kind, set()):
            raise PermissionError(f"Builder verification is not available at stage: {current_stage}")
        artifact = self.artifact_store.latest_for_candidate(candidate_id, str(payload.get("artifact_id") or "").strip())
        report = self.builder_verifier.verify(
            candidate,
            artifact,
            verified_by=str(payload.get("verified_by") or payload.get("actor") or "growth_runtime").strip(),
        )
        updated_artifact = self.artifact_store.record_verification(candidate_id, str(artifact.get("artifact_id") or ""), report)
        report_summary = {
            "report_id": report["report_id"],
            "artifact_id": report["artifact_id"],
            "status": report["status"],
            "mode": report["mode"],
            "summary": dict(report.get("summary") or {}),
            "deferred_runtime_check_count": len(report.get("deferred_runtime_checks") or []),
            "activation_enabled": False,
        }
        updated = {
            **candidate,
            "evidence": {
                **dict(candidate.get("evidence") or {}),
                "builder_artifact": _builder_artifact_summary(updated_artifact),
                "builder_verification": report_summary,
            },
            "metadata": {**dict(candidate.get("metadata") or {}), "builder_verified": True, "auto_apply": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "builder_artifact_verified", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "builder_artifact": updated_artifact,
            "verification_report": report,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def prepare_sandbox_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        prepared_by = str(payload.get("prepared_by") or payload.get("actor") or "").strip()
        if not candidate_id or not prepared_by:
            raise ValueError("candidate_id and prepared_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can prepare sandbox bundles")
        allowed_stages = {
            "skill": {"design", "sandbox", "dry_run", "benchmark"},
            "tool": {"research", "sandbox", "dry_run", "benchmark"},
            "code": {"design", "sandbox", "dry_run", "benchmark"},
        }
        kind = str(candidate.get("kind") or "")
        current_stage = str(candidate.get("current_stage") or "")
        if current_stage not in allowed_stages.get(kind, set()):
            raise PermissionError(f"sandbox bundle preparation is not available at stage: {current_stage}")
        artifact = self.artifact_store.latest_for_candidate(
            candidate_id, str(payload.get("artifact_id") or "").strip()
        )
        bundle = self.sandbox_bundle_store.prepare(
            candidate,
            artifact,
            payload,
            prepared_by=prepared_by,
        )
        bundle_summary = self.sandbox_bundle_store.summary(bundle)
        updated_artifact = self.artifact_store.record_sandbox_bundle(
            candidate_id, str(artifact.get("artifact_id") or ""), bundle_summary
        )
        updated = {
            **candidate,
            "evidence": {
                **dict(candidate.get("evidence") or {}),
                "builder_artifact": _builder_artifact_summary(updated_artifact),
                "sandbox_bundle": bundle_summary,
            },
            "metadata": {
                **dict(candidate.get("metadata") or {}),
                "sandbox_bundle_prepared": True,
                "auto_apply": False,
            },
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "sandbox_bundle_prepared", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "builder_artifact": updated_artifact,
            "sandbox_bundle": bundle,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def execute_builder_sandbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        executed_by = str(payload.get("executed_by") or payload.get("actor") or "").strip()
        if not candidate_id or not executed_by:
            raise ValueError("candidate_id and executed_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can run sandbox execution")
        if str(candidate.get("kind") or "") not in {"skill", "tool", "code"}:
            raise PermissionError("container execution is supported only for Skill, Tool and Code candidates")
        current_stage = str(candidate.get("current_stage") or "")
        if current_stage not in {"sandbox", "dry_run", "benchmark"}:
            raise PermissionError(f"container execution is not available at stage: {current_stage}")
        artifact = self.artifact_store.latest_for_candidate(
            candidate_id, str(payload.get("artifact_id") or "").strip()
        )
        report = self.sandbox_executor.execute(
            candidate,
            artifact,
            payload,
            executed_by=executed_by,
        )
        updated_artifact = self.artifact_store.record_sandbox_execution(
            candidate_id, str(artifact.get("artifact_id") or ""), report
        )
        report_summary = {
            "execution_id": report["execution_id"],
            "bundle_id": report["bundle_id"],
            "status": report["status"],
            "failure_reason": report["failure_reason"],
            "exit_code": report["exit_code"],
            "duration_ms": report["duration_ms"],
            "checks": dict(report.get("checks") or {}),
            "candidate_stage_advanced": False,
            "activation_enabled": False,
        }
        updated = {
            **candidate,
            "evidence": {
                **dict(candidate.get("evidence") or {}),
                "builder_artifact": _builder_artifact_summary(updated_artifact),
                "sandbox_execution": report_summary,
            },
            "metadata": {
                **dict(candidate.get("metadata") or {}),
                "sandbox_execution_completed": True,
                "auto_apply": False,
            },
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "sandbox_execution_completed", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "builder_artifact": updated_artifact,
            "sandbox_execution": report,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def record_candidate_benchmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        recorded_by = str(payload.get("recorded_by") or payload.get("actor") or "").strip()
        if not candidate_id or not recorded_by:
            raise ValueError("candidate_id and recorded_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate":
            raise PermissionError("only active candidates can be benchmarked")
        if str(candidate.get("current_stage") or "") != "benchmark":
            raise PermissionError("candidate must reach the benchmark stage before measurement")
        kind = str(candidate.get("kind") or "capability")
        evidence = dict(candidate.get("evidence") or {})
        benchmark_payload = (
            dict(payload.get("benchmark") or {})
            if isinstance(payload.get("benchmark"), dict)
            else dict(payload)
        )
        benchmark_payload["target"] = candidate_id
        benchmark_payload["target_type"] = kind
        if kind in {"skill", "tool", "code"}:
            execution = evidence.get("sandbox_execution") if isinstance(evidence.get("sandbox_execution"), dict) else {}
            if str(execution.get("status") or "") != "passed" or not str(execution.get("execution_id") or ""):
                raise PermissionError("a passed isolated sandbox execution is required before benchmarking")
            benchmark_payload["measurement_source"] = f"sandbox_execution:{execution['execution_id']}"
        elif kind == "workflow":
            if not isinstance(evidence.get("dry_run"), dict) or not evidence.get("dry_run"):
                raise PermissionError("workflow dry-run evidence is required before benchmarking")
            benchmark_payload["measurement_source"] = "growth_stage_evidence:dry_run"
        report = self.benchmarks.record_comparison(
            benchmark_payload,
            candidate_id=candidate_id,
            workspace_id=str(candidate.get("workspace_id") or ""),
            recorded_by=recorded_by,
        )
        report_summary = self.benchmarks.summary(report)
        updated = {
            **candidate,
            "evidence": {**evidence, "benchmark_report": report_summary},
            "metadata": {
                **dict(candidate.get("metadata") or {}),
                "benchmark_measured": True,
                "benchmark_promotion_passed": report_summary["promotion_passed"],
                "auto_apply": False,
            },
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "candidate_benchmark_recorded", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "benchmark_report": report,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def run_model_jury(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        requested_by = str(payload.get("requested_by") or payload.get("actor") or "").strip()
        if not candidate_id or not requested_by:
            raise ValueError("candidate_id and requested_by are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "candidate" or str(candidate.get("kind") or "") != "model":
            raise PermissionError("Model Jury is available only for active model candidates")
        if str(candidate.get("current_stage") or "") != "benchmark":
            raise PermissionError("model candidate must reach benchmark before Jury review")
        benchmark = self.benchmarks.latest_for_candidate(candidate_id)
        if not benchmark:
            raise PermissionError("a measured model Benchmark is required before Jury review")

        from backend.app.learning_workflow import request_multi_model_review

        prompt = build_model_jury_prompt(benchmark)
        committee = request_multi_model_review(
            prompt,
            skill_name="model_jury",
            context=json.dumps({"benchmark_id": benchmark.get("benchmark_id")}, ensure_ascii=False),
            model_ids=[str(item) for item in payload.get("model_ids") or []] if isinstance(payload.get("model_ids"), list) else None,
        ).snapshot()
        jury_report = build_model_jury_report(benchmark, committee, requested_by=requested_by)
        updated_benchmark = self.benchmarks.attach_model_jury(benchmark, jury_report)
        summary = self.benchmarks.summary(updated_benchmark)
        updated = {
            **candidate,
            "evidence": {
                **dict(candidate.get("evidence") or {}),
                "benchmark_report": summary,
                "model_jury": {
                    "jury_report_id": jury_report["jury_report_id"],
                    "status": jury_report["status"],
                    "structured_review_count": jury_report["structured_review_count"],
                    "approval_count": jury_report["approval_count"],
                    "activation_enabled": False,
                },
            },
            "metadata": {
                **dict(candidate.get("metadata") or {}),
                "benchmark_promotion_passed": summary["promotion_passed"],
                "auto_apply": False,
            },
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False},
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "model_jury_completed", **updated})
        return {
            "ok": True,
            "candidate": updated,
            "model_jury": jury_report,
            "benchmark_report": updated_benchmark,
            "growth": self.snapshot(workspace_id=str(candidate.get("workspace_id") or "") or None),
        }

    def review_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        decision = str(payload.get("decision") or payload.get("status") or "").strip().lower()
        reviewer = str(payload.get("reviewer") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if not candidate_id or decision not in {"approve", "approved", "reject", "rejected"} or not reviewer or not reason or not evidence:
            raise ValueError("candidate_id, decision, reviewer, reason and evidence are required")
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") in {"registered", "escalated", "needs_human"}:
            raise PermissionError("closed or escalated candidates cannot be reviewed")
        approved = decision in {"approve", "approved"}
        current_stage = str(candidate.get("current_stage") or "").strip().lower()
        if approved and current_stage != "review":
            raise PermissionError("candidate must complete ordered evidence and reach review before approval")
        if approved:
            benchmark = self.benchmarks.latest_for_candidate(candidate_id)
            if not benchmark or not bool((benchmark.get("promotion_gate") or {}).get("passed")):
                raise PermissionError("a passed measured Benchmark is required before approval")
        updated = {
            **candidate,
            "status": "approved" if approved else "rejected",
            "promotion_status": "approved" if approved else "rejected",
            "review": {
                "required": True,
                "approved": approved,
                "reviewer": reviewer,
                "reason": reason,
                "evidence": dict(evidence),
                "reviewed_at": _now(),
                "reviewed_stage": current_stage,
            },
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "candidate_reviewed", **updated})
        return {"ok": True, "candidate": updated, "growth": self.snapshot()}

    def register_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(payload.get("candidate_id") or "").strip()
        candidate = self._candidate_for_action(candidate_id, payload)
        if candidate.get("status") != "approved" or not (candidate.get("review") or {}).get("reviewer"):
            raise PermissionError("approved review with reviewer is required before registry")
        if str(candidate.get("current_stage") or "") != "review":
            raise PermissionError("candidate must reach the review stage before registry")
        registered_by = str(payload.get("registered_by") or payload.get("actor") or "").strip()
        registry_evidence = payload.get("registry_evidence") if isinstance(payload.get("registry_evidence"), dict) else payload.get("evidence")
        if not registered_by or not isinstance(registry_evidence, dict) or not registry_evidence:
            raise ValueError("registered_by and registry evidence are required")
        registered = {
            **candidate,
            "status": "registered",
            "promotion_status": "registered",
            "current_stage": "registry",
            "activation": {**dict(candidate.get("activation") or {}), "enabled": False, "mode": "manual_activation_required"},
            "registry": {
                "registered_by": registered_by,
                "registered_at": _now(),
                "activation_required": True,
                "evidence": dict(registry_evidence),
            },
            "updated_at": _now(),
        }
        _append_jsonl(self.event_path, {"event": "candidate_registered", **registered})
        _append_jsonl(
            self.registry_path,
            {
                "registry_event": "candidate_registered",
                "candidate_id": candidate_id,
                "kind": candidate.get("kind"),
                "workspace_id": candidate.get("workspace_id", ""),
                "activation": registered.get("activation"),
                "registered_by": registered_by,
                "evidence": dict(registry_evidence),
                "registered_at": _now(),
            },
        )
        return {"ok": True, "candidate": registered, "registry": self.registry_snapshot(), "growth": self.snapshot()}

    def _candidate_for_action(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidates().get(candidate_id)
        if not candidate:
            raise ValueError("candidate not found")
        requested_workspace = str(payload.get("workspace_id") or "").strip()
        candidate_workspace = str(candidate.get("workspace_id") or "").strip()
        if requested_workspace and candidate_workspace != requested_workspace:
            if not (not candidate_workspace and bool(payload.get("allow_unscoped_governance"))):
                raise PermissionError("growth candidate belongs to another workspace")
        return candidate

    def registry_snapshot(self, *, workspace_id: str | None = None, include_unscoped: bool = True) -> dict[str, Any]:
        rows = _read_jsonl(self.registry_path)
        if workspace_id:
            rows = [
                row for row in rows
                if str(row.get("workspace_id") or "") == workspace_id
                or (include_unscoped and not str(row.get("workspace_id") or ""))
            ]
        return {"count": len(rows), "recent": _without_internal_paths(rows[-20:])}

    def snapshot(self, *, workspace_id: str | None = None, include_unscoped: bool = True) -> dict[str, Any]:
        candidates = list(self._candidates().values())
        if workspace_id:
            candidates = [
                item for item in candidates
                if str(item.get("workspace_id") or "") == workspace_id
                or (include_unscoped and not str(item.get("workspace_id") or ""))
            ]
        counts = Counter(str(item.get("status") or "unknown") for item in candidates)
        pending_review = counts.get("candidate", 0)
        pending_registry = counts.get("approved", 0)
        human_required = counts.get("needs_human", 0)
        builder_artifacts = self.artifact_store.snapshot([str(item.get("candidate_id") or "") for item in candidates])
        research_reports = self.remote_researcher.snapshot([str(item.get("candidate_id") or "") for item in candidates])
        sandbox_executions = self.sandbox_executor.snapshot(
            [str(item.get("candidate_id") or "") for item in candidates]
        )
        benchmark_snapshot = self.benchmarks.snapshot(
            candidate_ids=[str(item.get("candidate_id") or "") for item in candidates],
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "status": "needs_human" if human_required else ("needs_review" if pending_review else ("needs_registry" if pending_registry else "ready")),
            "pipeline": {
                "stages": list(GROWTH_STAGES),
                "candidate_only": True,
                "auto_apply": False,
                "requires_review": True,
                "escalation_targets": {key: list(value) for key, value in ESCALATION_TARGETS.items()},
            },
            "status_counts": dict(counts),
            "pending_review_count": pending_review,
            "pending_registry_count": pending_registry,
            "human_required_count": human_required,
            "candidate_count": len(candidates),
            "candidates": _without_internal_paths(
                sorted(candidates, key=lambda item: float(item.get("updated_at") or 0), reverse=True)[:100]
            ),
            "registry": self.registry_snapshot(workspace_id=workspace_id, include_unscoped=include_unscoped),
            "builder_artifacts": builder_artifacts,
            "research_reports": research_reports,
            "sandbox_runtime": self.sandbox_runtime.snapshot(),
            "sandbox_executions": sandbox_executions,
            "benchmarks": benchmark_snapshot,
        }

    def probe_sandbox_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        report = self.sandbox_runtime.probe()
        _append_jsonl(
            self.event_path,
            {
                "event": "sandbox_runtime_probed",
                "status": report.get("status"),
                "reason": report.get("reason"),
                "created_at": _now(),
            },
        )
        return {"ok": True, "sandbox_runtime": report, "growth": self.snapshot()}


def build_growth_snapshot(*, workspace_id: str | None = None, include_unscoped: bool = True) -> dict[str, Any]:
    return GrowthRuntime().snapshot(workspace_id=workspace_id, include_unscoped=include_unscoped)


def handle_growth_action(payload: dict[str, Any]) -> dict[str, Any]:
    # API callers cannot redirect Growth writes to arbitrary filesystem paths.
    runtime = GrowthRuntime()
    action = str(payload.get("action") or "snapshot").strip().lower()
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if action in {"snapshot", "refresh"}:
        return _without_internal_paths({"ok": True, "growth": runtime.snapshot(workspace_id=workspace_id or None)})
    if action in {"probe_sandbox_runtime", "sandbox_probe"}:
        if payload.get("confirmed") is not True:
            raise PermissionError("explicit confirmation is required for the sandbox execution probe")
        result = runtime.probe_sandbox_runtime(payload)
        if workspace_id:
            result["growth"] = runtime.snapshot(workspace_id=workspace_id)
        return _without_internal_paths(result)
    if action in {"research_candidate", "remote_research", "prepare_sandbox_bundle", "sandbox_bundle_prepare", "execute_builder_sandbox", "sandbox_execute", "verify_builder_artifact", "builder_verify", "record_candidate_benchmark", "benchmark_candidate", "run_model_jury", "model_jury", "review_candidate", "register_candidate", "escalate_candidate", "route_candidate"} and payload.get("confirmed") is not True:
        raise PermissionError("explicit confirmation is required for growth governance actions")
    handlers = {
        "analyze_gap": runtime.analyze_gap,
        "gap_analysis": runtime.analyze_gap,
        "mine_workflow": runtime.mine_workflow,
        "workflow_mining": runtime.mine_workflow,
        "propose_skill": runtime.propose_skill,
        "skill_growth": runtime.propose_skill,
        "propose_tool": runtime.propose_tool,
        "tool_growth": runtime.propose_tool,
        "propose_code": runtime.propose_code,
        "code_growth": runtime.propose_code,
        "propose_model": runtime.propose_model,
        "model_growth": runtime.propose_model,
        "research_candidate": runtime.research_candidate,
        "remote_research": runtime.research_candidate,
        "escalate_candidate": runtime.escalate_candidate,
        "route_candidate": runtime.escalate_candidate,
        "observe_failure": runtime.observe_failure,
        "observe_trajectory": runtime.observe_trajectory,
        "prepare_builder": runtime.prepare_builder_artifact,
        "prepare_builder_artifact": runtime.prepare_builder_artifact,
        "builder_prepare": runtime.prepare_builder_artifact,
        "prepare_sandbox_bundle": runtime.prepare_sandbox_bundle,
        "sandbox_bundle_prepare": runtime.prepare_sandbox_bundle,
        "probe_sandbox_runtime": runtime.probe_sandbox_runtime,
        "sandbox_probe": runtime.probe_sandbox_runtime,
        "verify_builder_artifact": runtime.verify_builder_artifact,
        "builder_verify": runtime.verify_builder_artifact,
        "execute_builder_sandbox": runtime.execute_builder_sandbox,
        "sandbox_execute": runtime.execute_builder_sandbox,
        "record_candidate_benchmark": runtime.record_candidate_benchmark,
        "benchmark_candidate": runtime.record_candidate_benchmark,
        "run_model_jury": runtime.run_model_jury,
        "model_jury": runtime.run_model_jury,
        "advance_stage": runtime.advance_stage,
        "record_stage_evidence": runtime.advance_stage,
        "review_candidate": runtime.review_candidate,
        "register_candidate": runtime.register_candidate,
    }
    handler = handlers.get(action)
    if handler is None:
        raise ValueError(f"unsupported growth action: {action}")
    result = handler(payload)
    if workspace_id and isinstance(result, dict):
        result["growth"] = runtime.snapshot(workspace_id=workspace_id)
        if "registry" in result:
            result["registry"] = runtime.registry_snapshot(workspace_id=workspace_id)
    return _without_internal_paths(result)
