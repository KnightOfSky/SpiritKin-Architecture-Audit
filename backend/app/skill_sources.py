from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib import parse, request

from backend.security.safety_control import evaluate_execution_safety
from backend.state_store import now_ts, read_json_state, resolve_state_path, safe_id, write_json_state

SCHEMA_VERSION = "spiritkin.skill_sources.v1"
DEFAULT_SKILL_SOURCE_STATE = "state/skills/sources.json"
DEFAULT_SKILL_QUARANTINE_DIR = "state/skills/quarantine"
DEFAULT_SKILL_LOCK_PATH = "state/skills/skills.lock.json"
DEFAULT_SKILL_SOURCE_POLICY_PATH = "state/skills/source_policy.json"
SUPPORTED_SKILL_FILE_NAMES = {"skills.json", "skill.json", ".spiritkin-skills.json", "openclaw_skills.json"}
SUPPORTED_SKILL_FILE_SUFFIXES = {".skill.json", ".skills.json"}
SUPPORTED_TEXT_SUFFIXES = {".md", ".txt"}
IGNORED_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv", "state", "tmp", "dist", "build"}
KNOWN_SOURCE_ADAPTERS = {"declarative_config", "github_search", "git", "local", "openclaw_cli"}
SOURCE_POLICY_MODES = {"suggest_and_stage", "manual_only", "disabled"}
DANGEROUS_PATTERNS = {
    "rm -rf": "destructive_shell",
    "del /s": "destructive_shell",
    "format ": "destructive_shell",
    "powershell -enc": "encoded_powershell",
    "invoke-webrequest": "download_or_remote_script",
    "curl ": "download_or_remote_script",
    "wget ": "download_or_remote_script",
    "| sh": "pipe_to_shell",
    "| bash": "pipe_to_shell",
    "shell=true": "python_shell",
    "os.system": "python_shell",
    "subprocess.": "python_subprocess",
    "eval(": "dynamic_code_execution",
    "exec(": "dynamic_code_execution",
}


def resolve_skill_source_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SKILL_SOURCE_STATE", DEFAULT_SKILL_SOURCE_STATE, path)


def resolve_skill_quarantine_dir(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SKILL_QUARANTINE_DIR", DEFAULT_SKILL_QUARANTINE_DIR, path)


def resolve_skill_lock_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SKILL_LOCK_PATH", DEFAULT_SKILL_LOCK_PATH, path)


def resolve_skill_source_policy_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SKILL_SOURCE_POLICY_PATH", DEFAULT_SKILL_SOURCE_POLICY_PATH, path)


def _now() -> float:
    return now_ts()


def _load_json(path: Path, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    return read_json_state(path, fallback)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_state(path, payload)


def _default_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sources": [], "updated_at": 0.0}


def _default_source_policy() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "suggest_and_stage",
        "allow_autonomous_discovery": False,
        "allow_direct_activation": False,
        "allowed_adapters": sorted(KNOWN_SOURCE_ADAPTERS),
        "max_discovery_results": 10,
        "require_quarantine": True,
        "require_core_review": True,
        "require_lockfile": True,
        "updated_at": 0.0,
    }


def _load_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state = _load_json(resolve_skill_source_state_path(path), _default_state())
    sources = state.get("sources")
    if not isinstance(sources, list):
        sources = []
    state["schema_version"] = SCHEMA_VERSION
    state["sources"] = [dict(item) for item in sources if isinstance(item, dict)]
    state.setdefault("updated_at", 0.0)
    return state


def _load_source_policy(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    policy = _load_json(resolve_skill_source_policy_path(path), _default_source_policy())
    return _normalize_source_policy(policy)


def _normalize_source_policy(policy: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_source_policy()
    merged = {**defaults, **policy}
    mode = str(merged.get("mode") or defaults["mode"]).strip().lower()
    if mode not in SOURCE_POLICY_MODES:
        mode = defaults["mode"]
    adapters = merged.get("allowed_adapters")
    if not isinstance(adapters, list):
        adapters = defaults["allowed_adapters"]
    normalized_adapters = sorted(
        {str(item).strip().lower() for item in adapters if str(item).strip().lower() in KNOWN_SOURCE_ADAPTERS}
    )
    if not normalized_adapters:
        normalized_adapters = defaults["allowed_adapters"]
    try:
        max_results = int(merged.get("max_discovery_results") or defaults["max_discovery_results"])
    except (TypeError, ValueError):
        max_results = defaults["max_discovery_results"]
    merged.update(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "allowed_adapters": normalized_adapters,
            "max_discovery_results": max(1, min(max_results, 50)),
            "allow_autonomous_discovery": bool(merged.get("allow_autonomous_discovery", False)),
            "allow_direct_activation": False,
            "require_quarantine": True,
            "require_core_review": bool(merged.get("require_core_review", True)),
            "require_lockfile": bool(merged.get("require_lockfile", True)),
        }
    )
    return merged


def save_skill_source_policy(payload: dict[str, Any]) -> dict[str, Any]:
    policy_payload = payload.get("policy") if isinstance(payload.get("policy"), dict) else payload
    policy = _load_source_policy()
    for key in ("mode", "allow_autonomous_discovery", "max_discovery_results", "allowed_adapters", "require_core_review", "require_lockfile"):
        if key in policy_payload:
            policy[key] = policy_payload[key]
    policy["updated_at"] = _now()
    policy = _normalize_source_policy(policy)
    _save_json(resolve_skill_source_policy_path(), policy)
    return policy


def _source_policy_denial(adapter: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    policy = _load_source_policy()
    normalized_adapter = adapter.strip().lower()
    if policy["mode"] == "disabled":
        return {"ok": False, "error": "skill_source_discovery_disabled", "adapter": normalized_adapter, "policy": policy}
    if normalized_adapter not in set(policy["allowed_adapters"]):
        return {"ok": False, "error": "skill_source_adapter_blocked", "adapter": normalized_adapter, "policy": policy}
    if bool(payload.get("autonomous") or payload.get("scheduled")) and not bool(policy.get("allow_autonomous_discovery")):
        return {"ok": False, "error": "autonomous_discovery_disabled", "adapter": normalized_adapter, "policy": policy}
    return None


def _save_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = _now()
    _save_json(resolve_skill_source_state_path(path), state)
    return state


def _safe_id(value: str, fallback: str = "skill-source") -> str:
    return safe_id(value, fallback)


def _source_id_from_url(url: str) -> str:
    parsed = parse.urlparse(url)
    base = Path(parsed.path or url).stem or parsed.netloc or "source"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{_safe_id(base)}-{digest}"


def _source_by_id(state: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    return next((item for item in state.get("sources", []) if str(item.get("source_id") or "") == source_id), None)


def _quarantine_path_for_source(source: dict[str, Any]) -> Path:
    root = resolve_skill_quarantine_dir()
    return (root / _safe_id(str(source.get("source_id") or "source"))).resolve()


def _normalize_expected_file_hashes(value: Any, include_paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if isinstance(value, str):
        if len(include_paths) == 1 and value.strip():
            hashes[include_paths[0].replace("\\", "/").strip("/")] = value.strip().lower()
        return hashes
    if isinstance(value, dict):
        for path, digest in value.items():
            rel = str(path or "").replace("\\", "/").strip("/")
            sha = str(digest or "").strip().lower()
            if rel and sha:
                hashes[rel] = sha
        return hashes
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or item.get("file") or "").replace("\\", "/").strip("/")
            sha = str(item.get("sha256") or item.get("hash") or item.get("digest") or "").strip().lower()
            if rel and sha:
                hashes[rel] = sha
    return hashes


def build_skill_sources_snapshot() -> dict[str, Any]:
    state = _load_state()
    lock = _load_json(resolve_skill_lock_path(), {"schema_version": SCHEMA_VERSION, "skills": {}, "sources": {}})
    policy = _load_source_policy()
    return {
        "schema_version": SCHEMA_VERSION,
        "state_path": str(resolve_skill_source_state_path()),
        "quarantine_dir": str(resolve_skill_quarantine_dir()),
        "lock_path": str(resolve_skill_lock_path()),
        "policy_path": str(resolve_skill_source_policy_path()),
        "policy": policy,
        "count": len(state["sources"]),
        "sources": state["sources"],
        "lock": lock,
        "updated_at": state.get("updated_at", 0.0),
    }


def register_skill_source(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url") or payload.get("repo_url") or payload.get("path") or "").strip()
    if not url:
        raise ValueError("skill source url/path is required")
    source_type = str(payload.get("source_type") or ("local" if _local_path_from_url(url) else "git")).strip().lower()
    denial = _source_policy_denial("local" if source_type == "local" else "git", payload)
    if denial:
        return denial
    state = _load_state()
    source_id = _safe_id(str(payload.get("source_id") or "")) if payload.get("source_id") else _source_id_from_url(url)
    include_paths = payload.get("include_paths") if isinstance(payload.get("include_paths"), list) else []
    normalized_include_paths = [str(item).strip().replace("\\", "/") for item in include_paths if str(item).strip()]
    expected_file_hashes = _normalize_expected_file_hashes(
        payload.get("expected_file_hashes") or payload.get("expected_hashes") or payload.get("expected_sha256"),
        normalized_include_paths,
    )
    source = {
        "source_id": source_id,
        "label": str(payload.get("label") or payload.get("name") or source_id),
        "url": url,
        "branch": str(payload.get("branch") or payload.get("ref") or "").strip(),
        "expected_ref": str(payload.get("expected_ref") or payload.get("commit") or payload.get("sha") or "").strip(),
        "expected_file_hashes": expected_file_hashes,
        "source_type": source_type,
        "enabled": bool(payload.get("enabled", True)),
        "target_scope": str(payload.get("target_scope") or "project"),
        "trust_level": str(payload.get("trust_level") or "untrusted"),
        "include_paths": normalized_include_paths,
        "status": "registered",
        "registered_at": _now(),
        "last_sync_at": 0.0,
        "last_scan_at": 0.0,
        "quarantine_path": str(_quarantine_path_for_source({"source_id": source_id})),
        "metadata": dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
    }
    sources = [item for item in state["sources"] if str(item.get("source_id") or "") != source_id]
    sources.append(source)
    state["sources"] = sorted(sources, key=lambda item: str(item.get("source_id") or ""))
    _save_state(state)
    return {"ok": True, "source": source, "sources": build_skill_sources_snapshot()}


def delete_skill_source(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = _safe_id(str(payload.get("source_id") or ""))
    if not source_id:
        raise ValueError("source_id is required")
    state = _load_state()
    before = len(state["sources"])
    state["sources"] = [item for item in state["sources"] if str(item.get("source_id") or "") != source_id]
    _save_state(state)
    return {"ok": len(state["sources"]) != before, "deleted": source_id, "sources": build_skill_sources_snapshot()}


def sync_skill_source(payload: dict[str, Any]) -> dict[str, Any]:
    safety = evaluate_execution_safety(
        target="skill_source",
        operation="sync",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}
    source_id = _safe_id(str(payload.get("source_id") or ""))
    state = _load_state()
    source = _source_by_id(state, source_id)
    if source is None:
        raise ValueError(f"unknown skill source: {source_id}")
    if not bool(source.get("enabled", True)):
        return {"ok": False, "error": "source_disabled", "source": source, "sources": build_skill_sources_snapshot()}
    sync = _sync_source_to_quarantine(source)
    expected_ref = str(source.get("expected_ref") or "").strip()
    if sync.get("ok") and expected_ref:
        resolved_ref = str(sync.get("resolved_ref") or "")
        if not resolved_ref or (
            not resolved_ref.lower().startswith(expected_ref.lower())
            and not expected_ref.lower().startswith(resolved_ref.lower())
        ):
            sync.update({"ok": False, "error": f"expected_ref_mismatch: expected {expected_ref}, got {resolved_ref}", "error_code": "expected_ref_mismatch"})
    source.update(
        {
            "status": "synced" if sync.get("ok") else "sync_failed",
            "last_sync_at": _now(),
            "quarantine_path": sync.get("quarantine_path") or source.get("quarantine_path"),
            "resolved_ref": sync.get("resolved_ref") or "",
            "sync_method": sync.get("sync_method") or "",
            "integrity_status": "ref_verified" if sync.get("ok") and expected_ref else ("unchecked" if sync.get("ok") else "failed"),
            "last_error": sync.get("error") or "",
        }
    )
    _save_state(state)
    scan = scan_skill_source({"source_id": source_id}) if sync.get("ok") else {"ok": False, "candidates": []}
    return {"ok": bool(sync.get("ok")), "sync": sync, "scan": scan, "sources": build_skill_sources_snapshot()}


def scan_skill_source(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = _safe_id(str(payload.get("source_id") or ""))
    state = _load_state()
    source = _source_by_id(state, source_id)
    if source is None:
        raise ValueError(f"unknown skill source: {source_id}")
    root = Path(str(source.get("quarantine_path") or _quarantine_path_for_source(source))).resolve()
    candidates, files, warnings, conflicts = _scan_quarantined_candidates(source, root)
    hash_failures = [f"{record.get('path')}:expected_hash_mismatch" for record in files if record.get("expected_sha256") and not bool(record.get("hash_ok"))]
    warnings.extend(hash_failures)
    source.update(
        {
            "status": "scan_failed" if hash_failures else ("scanned" if root.exists() else "missing_quarantine"),
            "last_scan_at": _now(),
            "candidate_count": 0 if hash_failures else len(candidates),
            "scanned_file_count": len(files),
            "warnings": warnings[:20],
            "conflict_count": len(conflicts),
            "conflicts": conflicts[:20],
            "integrity_status": "hash_mismatch" if hash_failures else ("hash_verified" if any(record.get("expected_sha256") for record in files) else source.get("integrity_status", "unchecked")),
        }
    )
    _save_state(state)
    return {
        "ok": root.exists() and not hash_failures,
        "source": source,
        "candidate_count": 0 if hash_failures else len(candidates),
        "candidates": [] if hash_failures else candidates,
        "files": files,
        "warnings": warnings,
        "conflicts": conflicts,
        "hash_failures": hash_failures,
    }


def load_declarative_skill_config(payload: dict[str, Any]) -> dict[str, Any]:
    denial = _source_policy_denial("declarative_config", payload)
    if denial:
        return denial
    safety = evaluate_execution_safety(
        target="skill_source",
        operation="load_declarative_config",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}
    project_root = Path(str(payload.get("project_root") or Path.cwd())).resolve()
    raw_path = str(payload.get("config_path") or "").strip()
    if raw_path:
        config_path = Path(raw_path)
        if not config_path.is_absolute():
            config_path = project_root / config_path
    else:
        config_path = project_root / ".spiritkin" / "skills.json"
        if not config_path.exists():
            config_path = project_root / ".agent" / "skills.json"
    config_path = config_path.resolve()
    if not config_path.exists():
        return {"ok": False, "error": "declarative_config_not_found", "config_path": str(config_path), "sources": build_skill_sources_snapshot()}
    config = _load_json(config_path, {})
    repos = config.get("repos")
    if not isinstance(repos, list):
        repos = []
    registered: list[dict[str, Any]] = []
    for item in repos:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("repo_url") or "").strip()
        if not url:
            continue
        result = register_skill_source(
            {
                "url": url,
                "branch": str(item.get("branch") or item.get("ref") or ""),
                "source_id": str(item.get("source_id") or ""),
                "label": str(item.get("label") or item.get("name") or ""),
                "target_scope": str(item.get("target_scope") or config.get("target_scope") or "project"),
                "trust_level": str(item.get("trust_level") or config.get("trust_level") or "untrusted"),
                "include_paths": item.get("include_paths") if isinstance(item.get("include_paths"), list) else config.get("include_paths", []),
                "expected_ref": str(item.get("expected_ref") or item.get("commit") or item.get("sha") or ""),
                "expected_file_hashes": item.get("expected_file_hashes") or item.get("expected_hashes") or item.get("expected_sha256") or {},
                "metadata": {"declared_in": str(config_path)},
            }
        )
        if not result.get("ok"):
            return result
        registered.append(result["source"])
    return {"ok": True, "config_path": str(config_path), "registered_count": len(registered), "registered_sources": registered, "sources": build_skill_sources_snapshot()}


def discover_github_skill_sources(payload: dict[str, Any]) -> dict[str, Any]:
    denial = _source_policy_denial("github_search", payload)
    if denial:
        return denial
    policy = _load_source_policy()
    query = str(payload.get("query") or "agent skill SKILL.md").strip()
    requested_limit = int(payload.get("limit") or policy["max_discovery_results"])
    limit = max(1, min(requested_limit, int(policy["max_discovery_results"]), 25))
    url = "https://api.github.com/search/repositories?" + parse.urlencode(
        {"q": query, "sort": "updated", "order": "desc", "per_page": str(limit)}
    )
    try:
        req = request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SpiritKinAI-SkillSources"})
        with request.urlopen(req, timeout=float(payload.get("timeout") or 8.0)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"github_discovery_failed:{type(exc).__name__}", "detail": str(exc), "query": query}
    items = data.get("items") if isinstance(data, dict) else []
    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "full_name": str(item.get("full_name") or ""),
                "html_url": str(item.get("html_url") or ""),
                "clone_url": str(item.get("clone_url") or ""),
                "description": str(item.get("description") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "stargazers_count": int(item.get("stargazers_count") or 0),
            }
        )
    return {"ok": True, "query": query, "count": len(results), "results": results, "policy": policy}


def sync_openclaw_skill_source(payload: dict[str, Any]) -> dict[str, Any]:
    denial = _source_policy_denial("openclaw_cli", payload)
    if denial:
        return denial
    safety = evaluate_execution_safety(
        target="skill_source",
        operation="openclaw_sync",
        actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
    )
    if not safety.allowed:
        return {"ok": False, "error_code": safety.error_code, "message": safety.message, "safety": safety.snapshot()}

    command = str(payload.get("openclaw_command") or os.getenv("SPIRITKIN_OPENCLAW_COMMAND") or "openclaw").strip()
    if not command:
        command = "openclaw"
    executable = command
    if not any(separator in command for separator in ("\\", "/")):
        resolved = shutil.which(command)
        if not resolved:
            return {"ok": False, "error": "openclaw_not_found", "detail": f"{command} was not found on PATH", "sources": build_skill_sources_snapshot()}
        executable = resolved
    args = payload.get("args") if isinstance(payload.get("args"), list) else ["skills", "--json"]
    normalized_args = [str(item) for item in args if str(item).strip()]
    try:
        completed = subprocess.run(
            [executable, *normalized_args],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=float(payload.get("timeout") or 30.0),
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": f"openclaw_sync_failed:{type(exc).__name__}", "detail": str(exc), "sources": build_skill_sources_snapshot()}
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": "openclaw_command_failed",
            "detail": (completed.stderr or completed.stdout or "")[-1200:],
            "returncode": completed.returncode,
            "sources": build_skill_sources_snapshot(),
        }

    try:
        skills = _normalize_openclaw_cli_payload(completed.stdout)
    except ValueError as exc:
        return {"ok": False, "error": "openclaw_invalid_json", "detail": str(exc), "sources": build_skill_sources_snapshot()}
    source_id = _safe_id(str(payload.get("source_id") or "openclaw-cli"))
    target = _quarantine_path_for_source({"source_id": source_id})
    target.parent.mkdir(parents=True, exist_ok=True)
    _safe_remove_quarantine_target(target)
    target.mkdir(parents=True, exist_ok=True)
    export_payload = {
        "skills": skills,
        "metadata": {
            "adapter": "openclaw_cli",
            "command": command,
            "args": normalized_args,
            "captured_at": _now(),
        },
    }
    export_file = target / "openclaw_skills.json"
    export_file.write_text(json.dumps(export_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    state = _load_state()
    source = _source_by_id(state, source_id) or {
        "source_id": source_id,
        "registered_at": _now(),
        "last_sync_at": 0.0,
        "last_scan_at": 0.0,
    }
    source.update(
        {
            "label": str(payload.get("label") or "OpenClaw CLI"),
            "url": "openclaw://cli",
            "branch": "",
            "expected_ref": "",
            "expected_file_hashes": {},
            "source_type": "openclaw_cli",
            "enabled": True,
            "target_scope": str(payload.get("target_scope") or "project"),
            "trust_level": str(payload.get("trust_level") or "untrusted"),
            "include_paths": [],
            "status": "synced",
            "last_sync_at": _now(),
            "quarantine_path": str(target),
            "resolved_ref": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
            "sync_method": "openclaw_cli",
            "integrity_status": "cli_export_captured",
            "last_error": "",
            "metadata": {"command": command, "args": normalized_args},
        }
    )
    state["sources"] = sorted(
        [item for item in state["sources"] if str(item.get("source_id") or "") != source_id] + [source],
        key=lambda item: str(item.get("source_id") or ""),
    )
    _save_state(state)
    scan = scan_skill_source({"source_id": source_id})
    return {
        "ok": True,
        "source": get_skill_source(source_id),
        "sync": {"ok": True, "sync_method": "openclaw_cli", "quarantine_path": str(target), "skill_count": len(skills)},
        "scan": scan,
        "sources": build_skill_sources_snapshot(),
    }


def _normalize_openclaw_cli_payload(stdout: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"openclaw skills output must be JSON: {exc}") from exc
    items: list[Any]
    if isinstance(raw, dict) and isinstance(raw.get("skills"), list):
        items = list(raw["skills"])
    elif isinstance(raw, dict) and isinstance(raw.get("items"), list):
        items = list(raw["items"])
    elif isinstance(raw, list):
        items = list(raw)
    else:
        items = []
    skills: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("skill_name") or item.get("id") or f"openclaw.skill.{index}").strip()
        description = str(item.get("description") or item.get("summary") or "OpenClaw exported Skill candidate").strip()
        steps = item.get("steps") if isinstance(item.get("steps"), list) else []
        allowlist = item.get("tool_allowlist") if isinstance(item.get("tool_allowlist"), list) else item.get("tools")
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        metadata.update({"openclaw_source": True, "openclaw_original": {key: value for key, value in item.items() if key not in {"metadata"}}})
        skills.append(
            {
                "name": name,
                "description": description,
                "trigger_intents": item.get("trigger_intents") if isinstance(item.get("trigger_intents"), list) else [name],
                "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
                "preconditions": item.get("preconditions") if isinstance(item.get("preconditions"), list) else [],
                "steps": steps,
                "tool_allowlist": allowlist if isinstance(allowlist, list) else [],
                "risk_level": str(item.get("risk_level") or "medium"),
                "confirmation_policy": str(item.get("confirmation_policy") or "always"),
                "rollback_strategy": str(item.get("rollback_strategy") or "disable candidate and remove from lockfile"),
                "success_criteria": item.get("success_criteria") if isinstance(item.get("success_criteria"), list) else [],
                "eval_cases": item.get("eval_cases") if isinstance(item.get("eval_cases"), list) else [],
                "version": str(item.get("version") or "0.1.0"),
                "metadata": metadata,
            }
        )
    return skills


def build_candidate_payloads_from_source(payload: dict[str, Any]) -> list[dict[str, Any]]:
    scan = scan_skill_source(payload)
    candidates = scan.get("candidates")
    return [dict(item) for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []


def update_skill_source_lock(*, source: dict[str, Any], imported_skills: list[dict[str, Any]]) -> dict[str, Any]:
    lock_path = resolve_skill_lock_path()
    lock = _load_json(lock_path, {"schema_version": SCHEMA_VERSION, "sources": {}, "skills": {}, "updated_at": 0.0})
    sources = lock.get("sources") if isinstance(lock.get("sources"), dict) else {}
    skills = lock.get("skills") if isinstance(lock.get("skills"), dict) else {}
    source_id = str(source.get("source_id") or "")
    sources[source_id] = {
        "url": str(source.get("url") or ""),
        "branch": str(source.get("branch") or ""),
        "source_type": str(source.get("source_type") or ""),
        "resolved_ref": str(source.get("resolved_ref") or ""),
        "expected_ref": str(source.get("expected_ref") or ""),
        "integrity_status": str(source.get("integrity_status") or "unchecked"),
        "quarantine_path": str(source.get("quarantine_path") or ""),
        "locked_at": _now(),
    }
    for skill in imported_skills:
        name = str(skill.get("name") or "")
        if not name:
            continue
        metadata = skill.get("metadata") if isinstance(skill.get("metadata"), dict) else {}
        skills[name] = {
            "source_id": source_id,
            "source_file": str(metadata.get("source_file") or ""),
            "source_sha256": str(metadata.get("source_sha256") or ""),
            "status": str(skill.get("status") or metadata.get("status") or "candidate"),
            "locked_at": _now(),
        }
    lock.update({"schema_version": SCHEMA_VERSION, "sources": sources, "skills": skills, "updated_at": _now()})
    _save_json(lock_path, lock)
    return lock


def handle_skill_source_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "source_snapshot").strip().lower()
    if action in {"source_snapshot", "list_sources", "skill_sources"}:
        return {"ok": True, "skill_sources": build_skill_sources_snapshot()}
    if action in {"save_source_policy", "source_policy", "update_source_policy"}:
        policy = save_skill_source_policy(payload)
        return {"ok": True, "policy": policy, "skill_sources": build_skill_sources_snapshot()}
    if action in {"register_source", "add_source", "source_register"}:
        return register_skill_source(payload)
    if action in {"delete_source", "remove_source", "source_delete"}:
        return delete_skill_source(payload)
    if action in {"sync_source", "source_sync"}:
        return sync_skill_source(payload)
    if action in {"scan_source", "source_scan"}:
        return scan_skill_source(payload)
    if action in {"sync_declarative_config", "load_declarative_config"}:
        return load_declarative_skill_config(payload)
    if action in {"discover_github", "search_github_sources"}:
        return discover_github_skill_sources(payload)
    if action in {"sync_openclaw", "sync_openclaw_source", "openclaw_sync", "discover_openclaw"}:
        return sync_openclaw_skill_source(payload)
    raise ValueError(f"unsupported skill source action: {action}")


def get_skill_source(source_id: str) -> dict[str, Any] | None:
    return _source_by_id(_load_state(), _safe_id(source_id))


def _local_path_from_url(url: str) -> Path | None:
    if not url:
        return None
    direct = Path(url)
    if direct.exists():
        return direct.resolve()
    parsed = parse.urlparse(url)
    if parsed.scheme == "file":
        path = Path(parse.unquote(parsed.path))
        return path.resolve() if path.exists() else None
    if parsed.scheme:
        return None
    path = Path(url)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve() if path.exists() else None


def _safe_remove_quarantine_target(target: Path) -> None:
    root = resolve_skill_quarantine_dir().resolve()
    target = target.resolve()
    if target == root or root not in target.parents:
        raise ValueError("quarantine target must stay under skill quarantine dir")
    if target.exists():
        shutil.rmtree(target)


def _sync_source_to_quarantine(source: dict[str, Any]) -> dict[str, Any]:
    target = _quarantine_path_for_source(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    _safe_remove_quarantine_target(target)
    url = str(source.get("url") or "")
    local_path = _local_path_from_url(url)
    if local_path is not None:
        shutil.copytree(local_path, target, ignore=shutil.ignore_patterns(*IGNORED_DIR_NAMES))
        return {
            "ok": True,
            "sync_method": "local_copy",
            "quarantine_path": str(target),
            "resolved_ref": _git_rev_parse(local_path),
        }

    command = ["git", "clone", "--depth", "1"]
    branch = str(source.get("branch") or "").strip()
    if branch:
        command.extend(["--branch", branch])
    command.extend([url, str(target)])
    completed = subprocess.run(command, cwd=str(Path.cwd()), capture_output=True, text=True, timeout=90, check=False)
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "git clone failed")[-1200:],
            "sync_method": "git_clone",
            "quarantine_path": str(target),
        }
    return {
        "ok": True,
        "sync_method": "git_clone",
        "quarantine_path": str(target),
        "resolved_ref": _git_rev_parse(target),
    }


def _git_rev_parse(path: Path) -> str:
    try:
        completed = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _iter_skill_files(root: Path, include_paths: list[str]) -> list[Path]:
    search_roots: list[Path] = []
    if include_paths:
        for item in include_paths:
            candidate = (root / item).resolve()
            if candidate == root or root in candidate.parents:
                search_roots.append(candidate)
    else:
        search_roots.append(root)
    ranked_files: dict[Path, tuple[int, Path]] = {}
    for priority, search_root in enumerate(search_roots):
        if not search_root.exists():
            continue
        candidates = [search_root] if search_root.is_file() else search_root.rglob("*")
        for path in candidates:
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                rel_parts = path.parts
            if any(part in IGNORED_DIR_NAMES for part in rel_parts):
                continue
            name = path.name.lower()
            suffix = path.suffix.lower()
            if name in SUPPORTED_SKILL_FILE_NAMES or any(name.endswith(item) for item in SUPPORTED_SKILL_FILE_SUFFIXES) or name == "skill.md" or suffix in SUPPORTED_TEXT_SUFFIXES:
                ranked_files.setdefault(path.resolve(), (priority, path))
    return [item[1] for item in sorted(ranked_files.values(), key=lambda item: (item[0], item[1].as_posix()))]


def _scan_quarantined_candidates(
    source: dict[str, Any],
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, str]]]:
    if not root.exists():
        return [], [], ["quarantine_missing"], []
    include_paths = [str(item) for item in source.get("include_paths") or []]
    files = _iter_skill_files(root, include_paths)
    candidates: list[dict[str, Any]] = []
    file_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    expected_hashes = source.get("expected_file_hashes") if isinstance(source.get("expected_file_hashes"), dict) else {}
    for path in files[:200]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        file_warnings = _scan_text_warnings(text)
        warnings.extend(f"{rel}:{item}" for item in file_warnings)
        expected_sha = str(expected_hashes.get(rel) or "").strip().lower()
        hash_ok = not expected_sha or digest.lower() == expected_sha
        file_records.append({"path": rel, "sha256": digest, "expected_sha256": expected_sha, "hash_ok": hash_ok, "warnings": file_warnings})
        parsed = _candidate_payloads_from_file(source, root, path, text, digest, file_warnings)
        candidates.extend(parsed)
    resolved: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, str]] = []
    for candidate in candidates:
        name = str(candidate.get("name") or candidate.get("skill_name") or "").strip()
        if not name:
            resolved.append(candidate)
            continue
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = candidate
            resolved.append(candidate)
            continue
        existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        candidate_metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        conflicts.append(
            {
                "skill_name": name,
                "winner_file": str(existing_metadata.get("source_file") or ""),
                "shadowed_file": str(candidate_metadata.get("source_file") or ""),
                "resolution": "first_include_path_then_lexical_path_wins",
            }
        )
    warnings.extend(f"skill_conflict:{item['skill_name']}:{item['shadowed_file']}" for item in conflicts)
    return resolved, file_records, warnings, conflicts


def _scan_text_warnings(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({label for pattern, label in DANGEROUS_PATTERNS.items() if pattern in lowered})


def _candidate_payloads_from_file(
    source: dict[str, Any],
    root: Path,
    path: Path,
    text: str,
    digest: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rel = path.relative_to(root).as_posix()
    name = path.name.lower()
    payloads: list[dict[str, Any]] = []
    if name.endswith(".json"):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = None
        for item in _extract_skill_payloads(raw):
            payloads.append(_decorate_candidate_payload(item, source=source, rel_path=rel, digest=digest, warnings=warnings))
        return payloads
    if name == "skill.md":
        payload = {
            "name": f"git.{_safe_id(str(source.get('source_id') or 'source'))}.{_safe_id(path.parent.name or path.stem)}",
            "description": _markdown_description(text) or f"Candidate Skill imported from {rel}",
            "trigger_intents": [path.parent.name or path.stem],
            "steps": [],
            "tool_allowlist": [],
            "risk_level": "medium" if warnings else "low",
        }
        return [_decorate_candidate_payload(payload, source=source, rel_path=rel, digest=digest, warnings=warnings)]
    return []


def _extract_skill_payloads(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("skills"), list):
        return [dict(item) for item in raw["skills"] if isinstance(item, dict)]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict) and (raw.get("name") or raw.get("skill_name")):
        return [dict(raw)]
    return []


def _decorate_candidate_payload(
    payload: dict[str, Any],
    *,
    source: dict[str, Any],
    rel_path: str,
    digest: str,
    warnings: list[str],
) -> dict[str, Any]:
    source_kind = str(source.get("source_type") or "git").strip().lower()
    candidate_source_type = source_kind if source_kind in {"openclaw_cli"} else "git"
    validation = _validate_skill_manifest(payload)
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "status": "candidate",
            "source_type": candidate_source_type,
            "source_id": str(source.get("source_id") or ""),
            "source_url": str(source.get("url") or ""),
            "source_branch": str(source.get("branch") or ""),
            "source_ref": str(source.get("resolved_ref") or ""),
            "source_file": rel_path,
            "source_sha256": digest,
            "source_trust_level": str(source.get("trust_level") or "untrusted"),
            "source_review": {
                "state": "candidate_staged",
                "warnings": list(warnings) + [f"manifest:{item}" for item in validation["warnings"]],
                "manifest": validation,
                "requires_core_review": True,
            },
        }
    )
    result = dict(payload)
    result["metadata"] = metadata
    result["status"] = "candidate"
    result["source_type"] = candidate_source_type
    result["promotion_status"] = "candidate"
    result["review_gate"] = str(payload.get("review_gate") or "core_review")
    if (warnings or validation["warnings"] or validation["errors"]) and str(result.get("risk_level") or "low") == "low":
        result["risk_level"] = "medium"
    return result


def _validate_skill_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    name = str(payload.get("name") or payload.get("skill_name") or "").strip()
    if not name:
        errors.append("missing_name")
    if not str(payload.get("description") or "").strip():
        warnings.append("missing_description")
    steps = payload.get("steps")
    if steps is None:
        warnings.append("missing_steps")
        steps = []
    if not isinstance(steps, list):
        errors.append("steps_not_list")
        steps = []
    tool_names: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step_{index}_not_object")
            continue
        tool_name = str(step.get("tool_name") or "").strip()
        if not tool_name:
            errors.append(f"step_{index}_missing_tool_name")
            continue
        tool_names.append(tool_name)
        if "arguments" in step and not isinstance(step.get("arguments"), dict):
            errors.append(f"step_{index}_arguments_not_object")
    allowlist = payload.get("tool_allowlist")
    if allowlist is None:
        allowlist = []
    if not isinstance(allowlist, list):
        errors.append("tool_allowlist_not_list")
        allowlist = []
    allowlisted = {str(item).strip() for item in allowlist if str(item).strip()}
    missing_from_allowlist = sorted({name for name in tool_names if allowlisted and name not in allowlisted})
    if missing_from_allowlist:
        errors.append("steps_not_in_tool_allowlist")
    if tool_names and not allowlisted:
        warnings.append("missing_tool_allowlist")
    risk = str(payload.get("risk_level") or "low").strip().lower()
    if risk not in {"low", "medium", "high"}:
        warnings.append("unknown_risk_level")
    return {
        "schema_version": "spiritkin.skill_manifest.v1",
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "tool_count": len(tool_names),
        "allowlist_count": len(allowlisted),
    }


def _markdown_description(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:240]
    return ""
