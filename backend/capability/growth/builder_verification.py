from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

REPORT_SCHEMA_VERSION = "spiritkin.growth_builder_verification.v1"


def _safe_id(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return normalized[:96] or fallback


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()


def _check(check_id: str, passed: bool, detail: str, *, status: str | None = None) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status or ("passed" if passed else "failed"),
        "passed": bool(passed),
        "detail": detail[:500],
    }


class GrowthBuilderVerifier:
    """Runs bounded, read-only Builder preflight checks in a managed sandbox root."""

    def __init__(self, artifact_root: str | os.PathLike[str]) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.root = (self.artifact_root.parent / "sandboxes").resolve()

    def verify(self, candidate: dict[str, Any], artifact: dict[str, Any], *, verified_by: str = "growth_runtime") -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not candidate_id or not artifact_id:
            raise ValueError("candidate_id and artifact_id are required")
        if str(artifact.get("candidate_id") or "") != candidate_id:
            raise PermissionError("Builder artifact belongs to another candidate")
        if str(artifact.get("workspace_id") or "") != str(candidate.get("workspace_id") or ""):
            raise PermissionError("Builder artifact belongs to another workspace")

        artifact_path = Path(str(artifact.get("path") or "")).resolve()
        if not artifact_path.is_relative_to(self.artifact_root):
            raise PermissionError("Builder artifact escaped the managed artifact root")
        sandbox_dir = (self.root / _safe_id(candidate_id, "candidate")).resolve()
        if not sandbox_dir.is_relative_to(self.root):
            raise PermissionError("unsafe Builder verification path")

        integrity = dict(artifact.get("integrity") or {})
        unsigned = {key: value for key, value in artifact.items() if key not in {"path", "integrity"}}
        unsigned["integrity"] = {}
        integrity_valid = integrity.get("algorithm") == "sha256" and integrity.get("digest") == _digest(unsigned)
        sandbox_plan = dict(artifact.get("sandbox_plan") or {})
        registry_plan = dict(artifact.get("registry_plan") or {})
        research = dict(artifact.get("research") or {})
        inventory = dict(artifact.get("inventory") or {})
        match_count = sum(
            len(inventory.get(key) or [])
            for key in ("tool_matches", "mcp_matches", "model_matches", "worker_matches")
        )
        declared_source_count = len(dict(research).get("declared_sources") or [])
        activation_disabled = not bool(dict(candidate.get("activation") or {}).get("enabled")) and not bool(registry_plan.get("activation_enabled"))
        network_disabled = sandbox_plan.get("network_enabled") is False
        execution_disabled = sandbox_plan.get("external_code_execution_enabled") is False
        install_is_proposal = sandbox_plan.get("install_mode") == "proposal_only"
        write_scopes = [Path(str(value)).resolve() for value in sandbox_plan.get("allowed_writes") or []]
        writes_scoped = bool(write_scopes) and all(path.is_relative_to(self.root) for path in write_scopes)
        resolution_available = match_count > 0 or declared_source_count > 0

        checks = [
            _check("artifact_integrity", integrity_valid, "Builder artifact digest matches the managed artifact."),
            _check("workspace_scope", True, "Candidate and artifact workspace identities match."),
            _check("managed_write_scope", writes_scoped, "All declared writes remain inside the managed sandbox root."),
            _check(
                "network_guard",
                network_disabled,
                "The verification and candidate execution plan keep sandbox networking disabled; prior metadata research is provenance only.",
            ),
            _check("external_execution_guard", execution_disabled, "External code execution remains disabled for preflight."),
            _check("install_guard", install_is_proposal, "Dependency installation remains proposal-only."),
            _check("activation_guard", activation_disabled, "Candidate and Registry activation remain disabled."),
            _check(
                "requirement_resolution",
                resolution_available,
                f"Found {match_count} local Registry matches and {declared_source_count} declared sources.",
                status="passed" if resolution_available else "needs_human",
            ),
        ]
        hard_failures = [item for item in checks if item["status"] == "failed"]
        status = "failed" if hard_failures else ("passed" if resolution_available else "needs_human")
        deferred_checks = [str(item) for item in dict(artifact.get("verification_plan") or {}).get("checks") or []]
        created_at = time.time()
        identity = {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "artifact_digest": integrity.get("digest"),
            "status": status,
            "checks": [(item["check_id"], item["status"]) for item in checks],
        }
        report_id = f"verify-{_digest(identity)[:16]}"
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_id": report_id,
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "workspace_id": str(candidate.get("workspace_id") or ""),
            "kind": str(candidate.get("kind") or "capability"),
            "status": status,
            "mode": "static_sandbox_preflight",
            "verified_by": verified_by[:200],
            "created_at": created_at,
            "checks": checks,
            "summary": {
                "passed": sum(1 for item in checks if item["status"] == "passed"),
                "failed": len(hard_failures),
                "needs_human": sum(1 for item in checks if item["status"] == "needs_human"),
                "inventory_match_count": match_count,
                "declared_source_count": declared_source_count,
            },
            "deferred_runtime_checks": deferred_checks,
            "policy": {
                "network_accessed": False,
                "external_code_executed": False,
                "dependencies_installed": False,
                "candidate_stage_advanced": False,
                "activation_enabled": False,
            },
        }
        report["integrity"] = {"algorithm": "sha256", "digest": _digest(report)}
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        report_path = sandbox_dir / f"{report_id}.json"
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)
        return {**report, "path": str(report_path)}
