from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.state_store import resolve_state_path

SCHEMA_VERSION = "spiritkin.growth_builder_artifact.v1"
DEFAULT_ARTIFACT_ROOT = "state/growth/artifacts"
MAX_SOURCE_COUNT = 24
MAX_TEXT_LENGTH = 500
SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token", "x-api-key")

REGISTRY_TARGETS = {
    "capability": "capability_registry",
    "workflow": "workflow_registry",
    "skill": "skill_registry",
    "tool": "tool_registry",
    "code": "skill_registry",
    "model": "model_catalog",
}

VERIFICATION_CHECKS = {
    "capability": ("contract_schema", "binding_coverage", "worker_schedule", "policy_review"),
    "workflow": ("graph_schema", "dependency_validation", "dry_run", "replay_threshold", "side_effect_review"),
    "skill": ("tool_registration", "allowlist_validation", "dry_run", "replay_threshold", "audit_correlation"),
    "tool": ("source_integrity", "license_review", "sandbox_install", "help_probe", "smoke_test", "security_scan"),
    "code": ("static_analysis", "compile", "unit_test", "dry_run", "benchmark", "security_scan"),
    "model": ("source_integrity", "license_review", "resource_fit", "benchmark", "safety_evaluation"),
}


def _safe_id(value: str, fallback: str = "artifact") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return normalized[:96] or fallback


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()


def _tokens(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^a-zA-Z0-9]+", " ", str(value or "").lower())
        tokens.update(part for part in normalized.split() if len(part) >= 3)
    return tokens


def _match_score(search_tokens: set[str], *values: str) -> int:
    if not search_tokens:
        return 0
    inventory_tokens = _tokens(list(values))
    return len(search_tokens.intersection(inventory_tokens))


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key or "").lower()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _clean_source(source: Any) -> dict[str, Any]:
    if _contains_sensitive_key(source):
        raise ValueError("research sources cannot contain credentials or secret fields")
    if isinstance(source, str):
        raw: dict[str, Any] = {"label": source}
    elif isinstance(source, dict):
        raw = dict(source)
    else:
        raise ValueError("research source must be a string or object")
    cleaned: dict[str, Any] = {}
    for key in ("type", "label", "name", "url", "package", "provider", "version", "notes", "license"):
        value = raw.get(key)
        if value is None:
            continue
        cleaned[key] = str(value)[:MAX_TEXT_LENGTH]
    url = str(cleaned.get("url") or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("research source url must use http or https")
    if not cleaned:
        raise ValueError("research source must include a supported field")
    cleaned.setdefault("type", "declared_source")
    return cleaned


def _clean_sources(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    if len(items) > MAX_SOURCE_COUNT:
        raise ValueError(f"at most {MAX_SOURCE_COUNT} research sources are allowed")
    return [_clean_source(item) for item in items]


class GrowthBuilderArtifactStore:
    """Writes candidate-only Builder plans; it never downloads or executes code."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = resolve_state_path("SPIRITKIN_GROWTH_ARTIFACT_ROOT", DEFAULT_ARTIFACT_ROOT, root)

    def prepare(self, candidate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        kind = str(candidate.get("kind") or "capability").strip().lower()
        if kind not in REGISTRY_TARGETS:
            raise ValueError(f"unsupported growth candidate kind: {kind}")
        requirements = [str(item).strip() for item in candidate.get("requirements") or [] if str(item).strip()]
        evidence = dict(candidate.get("evidence") or {})
        remote_research = evidence.get("remote_research") if isinstance(evidence.get("remote_research"), dict) else {}
        remote_sources = [
            {
                "type": "github_repository_metadata",
                "label": str(item.get("full_name") or ""),
                "url": str(item.get("url") or ""),
                "license": str(item.get("license_spdx") or "NOASSERTION"),
                "notes": str(item.get("description") or ""),
            }
            for item in remote_research.get("repositories") or []
            if isinstance(item, dict) and item.get("full_name") and item.get("url")
        ]
        declared_sources = _clean_sources(
            payload.get("research_sources")
            or payload.get("sources")
            or remote_sources
            or evidence.get("research_targets")
        )
        inventory = self._inventory(requirements, kind=kind)
        identity = {
            "candidate_id": candidate_id,
            "kind": kind,
            "requirements": sorted(requirements),
            "sources": declared_sources,
        }
        artifact_id = f"builder-{_digest(identity)[:16]}"
        candidate_dir = (self.root / _safe_id(candidate_id, "candidate")).resolve()
        if not candidate_dir.is_relative_to(self.root.resolve()):
            raise ValueError("unsafe growth artifact path")
        artifact_path = candidate_dir / f"{artifact_id}.json"
        sandbox_root = (self.root.parent / "sandboxes" / _safe_id(candidate_id, "candidate")).resolve()
        found_count = sum(len(inventory[key]) for key in ("tool_matches", "mcp_matches", "model_matches", "worker_matches"))
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "candidate_id": candidate_id,
            "kind": kind,
            "workspace_id": str(candidate.get("workspace_id") or ""),
            "status": "prepared",
            "created_at": time.time(),
            "requirements": requirements,
            "risk": dict(candidate.get("risk") or {}),
            "research": {
                "mode": "local_inventory_and_reviewed_remote_metadata" if remote_sources else "local_inventory_and_declared_sources",
                "network_accessed": bool(remote_sources),
                "remote_report_id": str(remote_research.get("report_id") or ""),
                "declared_sources": declared_sources,
                "inventory_match_count": found_count,
                "unresolved": found_count == 0 and not declared_sources,
            },
            "inventory": inventory,
            "sandbox_plan": {
                "root": str(sandbox_root),
                "mode": "isolated_candidate",
                "network_enabled": False,
                "external_code_execution_enabled": False,
                "install_mode": "proposal_only",
                "allowed_writes": [str(sandbox_root)],
                "required_gate": "advance_stage:sandbox",
            },
            "verification_plan": {
                "checks": list(VERIFICATION_CHECKS[kind]),
                "execution_status": "not_run",
                "requires_evidence": True,
            },
            "registry_plan": {
                "target": REGISTRY_TARGETS[kind],
                "candidate_only": True,
                "activation_enabled": False,
                "requires_review": True,
            },
            "human_escalation": {
                "required": found_count == 0 and not declared_sources,
                "reason": "no inventory or declared source can satisfy the requirement" if found_count == 0 and not declared_sources else "",
            },
            "integrity": {},
        }
        artifact["integrity"] = {"algorithm": "sha256", "digest": _digest(artifact)}
        candidate_dir.mkdir(parents=True, exist_ok=True)
        temporary = artifact_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(artifact_path)
        return {**artifact, "path": str(artifact_path)}

    def list_for_candidate(self, candidate_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        candidate_dir = (self.root / _safe_id(candidate_id, "candidate")).resolve()
        if not candidate_dir.is_relative_to(self.root.resolve()) or not candidate_dir.exists():
            return []
        artifacts: list[dict[str, Any]] = []
        for path in sorted(candidate_dir.glob("builder-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[: max(1, limit)]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                artifacts.append({**payload, "path": str(path)})
        return artifacts

    def latest_for_candidate(self, candidate_id: str, artifact_id: str = "") -> dict[str, Any]:
        artifacts = self.list_for_candidate(candidate_id, limit=20)
        if artifact_id:
            match = next((item for item in artifacts if str(item.get("artifact_id") or "") == artifact_id), None)
            if match is None:
                raise ValueError("Builder artifact not found")
            return match
        if not artifacts:
            raise ValueError("Builder artifact must be prepared before verification")
        return artifacts[0]

    def record_verification(self, candidate_id: str, artifact_id: str, report: dict[str, Any]) -> dict[str, Any]:
        artifact = self.latest_for_candidate(candidate_id, artifact_id)
        artifact_path = Path(str(artifact.get("path") or "")).resolve()
        if not artifact_path.is_relative_to(self.root.resolve()):
            raise ValueError("unsafe growth artifact path")
        summary = {
            "report_id": str(report.get("report_id") or ""),
            "status": str(report.get("status") or "failed"),
            "mode": str(report.get("mode") or "static_sandbox_preflight"),
            "created_at": float(report.get("created_at") or time.time()),
            "summary": dict(report.get("summary") or {}),
            "activation_enabled": False,
        }
        updated = {key: value for key, value in artifact.items() if key != "path"}
        updated["verification_plan"] = {
            **dict(updated.get("verification_plan") or {}),
            "execution_status": summary["status"],
            "latest_report": summary,
        }
        updated["integrity"] = {}
        updated["integrity"] = {"algorithm": "sha256", "digest": _digest(updated)}
        temporary = artifact_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(artifact_path)
        return {**updated, "path": str(artifact_path)}

    def record_sandbox_bundle(
        self, candidate_id: str, artifact_id: str, bundle_summary: dict[str, Any]
    ) -> dict[str, Any]:
        artifact = self.latest_for_candidate(candidate_id, artifact_id)
        artifact_path = Path(str(artifact.get("path") or "")).resolve()
        if not artifact_path.is_relative_to(self.root.resolve()):
            raise ValueError("unsafe growth artifact path")
        updated = {key: value for key, value in artifact.items() if key != "path"}
        updated["sandbox_plan"] = {
            **dict(updated.get("sandbox_plan") or {}),
            "bundle": dict(bundle_summary),
            "bundle_prepared": True,
            "external_code_execution_enabled": False,
        }
        updated["verification_plan"] = {
            key: value
            for key, value in dict(updated.get("verification_plan") or {}).items()
            if key not in {"latest_report", "latest_sandbox_execution", "sandbox_execution_status"}
        }
        updated["verification_plan"]["execution_status"] = "not_run"
        updated["integrity"] = {}
        updated["integrity"] = {"algorithm": "sha256", "digest": _digest(updated)}
        temporary = artifact_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(artifact_path)
        return {**updated, "path": str(artifact_path)}

    def record_sandbox_execution(
        self, candidate_id: str, artifact_id: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        artifact = self.latest_for_candidate(candidate_id, artifact_id)
        artifact_path = Path(str(artifact.get("path") or "")).resolve()
        if not artifact_path.is_relative_to(self.root.resolve()):
            raise ValueError("unsafe growth artifact path")
        execution_summary = {
            "execution_id": str(report.get("execution_id") or ""),
            "bundle_id": str(report.get("bundle_id") or ""),
            "status": str(report.get("status") or "failed"),
            "failure_reason": str(report.get("failure_reason") or ""),
            "exit_code": int(report.get("exit_code") or 0),
            "duration_ms": float(report.get("duration_ms") or 0),
            "created_at": float(report.get("created_at") or time.time()),
            "activation_enabled": False,
        }
        updated = {key: value for key, value in artifact.items() if key != "path"}
        updated["verification_plan"] = {
            **dict(updated.get("verification_plan") or {}),
            "sandbox_execution_status": execution_summary["status"],
            "latest_sandbox_execution": execution_summary,
        }
        updated["integrity"] = {}
        updated["integrity"] = {"algorithm": "sha256", "digest": _digest(updated)}
        temporary = artifact_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(artifact_path)
        return {**updated, "path": str(artifact_path)}

    def snapshot(self, candidate_ids: list[str] | None = None) -> dict[str, Any]:
        artifacts: list[dict[str, Any]] = []
        for candidate_id in candidate_ids or []:
            artifacts.extend(self.list_for_candidate(candidate_id, limit=5))
        artifacts.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        recent = [
            {
                "artifact_id": item.get("artifact_id"),
                "candidate_id": item.get("candidate_id"),
                "kind": item.get("kind"),
                "workspace_id": item.get("workspace_id"),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
                "inventory_match_count": dict(item.get("research") or {}).get("inventory_match_count", 0),
                "verification_status": dict(item.get("verification_plan") or {}).get("execution_status", "not_run"),
                "sandbox_bundle_prepared": bool(dict(item.get("sandbox_plan") or {}).get("bundle_prepared")),
                "sandbox_execution_status": dict(item.get("verification_plan") or {}).get(
                    "sandbox_execution_status", "not_run"
                ),
                "registry_target": dict(item.get("registry_plan") or {}).get("target", ""),
                "human_required": bool(dict(item.get("human_escalation") or {}).get("required")),
            }
            for item in artifacts[:20]
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(artifacts),
            "recent": recent,
            "policy": {
                "network_accessed": False,
                "host_external_code_execution_enabled": False,
                "container_execution_requires_explicit_gate": True,
                "automatic_activation": False,
            },
        }

    @staticmethod
    def _inventory(requirements: list[str], *, kind: str) -> dict[str, list[dict[str, Any]]]:
        search_tokens = _tokens(requirements)
        from backend.app.mcp_management import build_mcp_management_snapshot
        from backend.app.model_catalog import load_model_catalog
        from backend.orchestrator.worker_pool import planned_worker_seed_descriptors
        from backend.tools.registry import build_default_tool_registry

        tool_matches: list[dict[str, Any]] = []
        for spec in build_default_tool_registry(allow_dynamic_mcp_discovery=False).list_specs():
            score = _match_score(search_tokens, spec.name, spec.description, spec.target, spec.operation)
            if score:
                tool_matches.append(
                    {
                        "tool_id": spec.name,
                        "target": spec.target,
                        "operation": spec.operation,
                        "risk_level": spec.risk_level,
                        "read_only": spec.read_only,
                        "match_score": score,
                    }
                )
        tool_matches.sort(key=lambda item: (-int(item["match_score"]), str(item["tool_id"])))

        mcp_matches: list[dict[str, Any]] = []
        for server in build_mcp_management_snapshot().get("servers") or []:
            if not isinstance(server, dict):
                continue
            tool_names = [str(item.get("internal_tool_name") or item.get("mcp_tool_name") or "") for item in server.get("tools") or [] if isinstance(item, dict)]
            score = _match_score(search_tokens, str(server.get("server_id") or ""), str(server.get("label") or ""), *tool_names)
            if score:
                mcp_matches.append(
                    {
                        "server_id": str(server.get("server_id") or ""),
                        "label": str(server.get("label") or ""),
                        "review_state": str(server.get("review_state") or "candidate"),
                        "enabled": bool(server.get("enabled")),
                        "tool_names": tool_names[:20],
                        "match_score": score,
                    }
                )

        model_matches: list[dict[str, Any]] = []
        if kind == "model":
            for model in load_model_catalog().get("models") or []:
                if not isinstance(model, dict):
                    continue
                score = _match_score(
                    search_tokens,
                    str(model.get("model_id") or ""),
                    str(model.get("role") or ""),
                    str(model.get("domain") or ""),
                    str(model.get("notes") or ""),
                )
                if score:
                    model_matches.append(
                        {
                            "model_id": str(model.get("model_id") or ""),
                            "provider": str(model.get("provider") or ""),
                            "role": str(model.get("role") or ""),
                            "size_class": str(model.get("size_class") or ""),
                            "online_verified": bool(model.get("online")),
                            "match_score": score,
                        }
                    )

        worker_matches: list[dict[str, Any]] = []
        for worker in planned_worker_seed_descriptors():
            score = _match_score(
                search_tokens,
                worker.worker_id,
                worker.label,
                *worker.capabilities,
                *worker.operations,
                *worker.capability_namespaces,
            )
            if score:
                worker_matches.append(
                    {
                        "worker_id": worker.worker_id,
                        "worker_type": worker.worker_type,
                        "worker_subtype": worker.worker_subtype,
                        "health_status": worker.health_status,
                        "schedulable": False,
                        "match_score": score,
                    }
                )

        return {
            "tool_matches": tool_matches[:20],
            "mcp_matches": sorted(mcp_matches, key=lambda item: (-int(item["match_score"]), str(item["server_id"])))[:20],
            "model_matches": sorted(model_matches, key=lambda item: (-int(item["match_score"]), str(item["model_id"])))[:20],
            "worker_matches": sorted(worker_matches, key=lambda item: (-int(item["match_score"]), str(item["worker_id"])))[:20],
        }
