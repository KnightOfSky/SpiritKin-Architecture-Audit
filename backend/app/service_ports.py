from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

DEFAULT_HOST = "127.0.0.1"
SCHEMA_VERSION = "spiritkin.service_ports.v1"
CONFIG_SCHEMA_VERSION = "spiritkin.service_ports.config.v1"
DEFAULT_SERVICE_PORT_CONFIG_PATH = "state/service_ports/config.json"
RESTART_GUIDANCE_SCHEMA_VERSION = "spiritkin.service_ports.restart_guidance.v1"
MANAGED_RESTART_SERVICE_IDS = {"frontend", "event_bridge", "command_gateway", "remote_worker", "android_endpoint", "ios_endpoint"}


@dataclass(frozen=True)
class ServicePortSpec:
    service_id: str
    label: str
    default_port: int
    env_var: str
    protocol: str = "http"
    path: str = ""
    required: bool = True
    description: str = ""

    def resolved_port(self, config: dict[str, Any] | None = None) -> int:
        port, _, _ = _resolved_port_details(self, config or {})
        return port

    def snapshot(self, *, host: str = DEFAULT_HOST, timeout: float = 0.25, config: dict[str, Any] | None = None) -> dict[str, Any]:
        port, source, config_value = _resolved_port_details(self, config or {})
        env_value = os.getenv(self.env_var, "").strip()
        conflict = port > 0 and _port_accepts_connection(_browser_host(host), port, timeout=timeout)
        url = _service_url(self.protocol, _browser_host(host), port, self.path) if port > 0 else ""
        return {
            "service_id": self.service_id,
            "label": self.label,
            "host": host,
            "browser_host": _browser_host(host),
            "port": port,
            "default_port": self.default_port,
            "env_var": self.env_var,
            "env_value": env_value,
            "config_value": config_value,
            "source": source,
            "editable": not bool(env_value),
            "uses_default": source == "default",
            "protocol": self.protocol,
            "path": self.path,
            "url": url,
            "required": self.required,
            "listening": conflict,
            "description": self.description,
        }


PORT_SPECS: tuple[ServicePortSpec, ...] = (
    ServicePortSpec(
        "frontend",
        "前端静态服务",
        8787,
        "SPIRITKIN_FRONTEND_PORT",
        protocol="http",
        path="/desktop_console.html",
        description="桌面 Web 控制台、3D avatar 和静态资源。",
    ),
    ServicePortSpec(
        "event_bridge",
        "事件 WebSocket",
        8765,
        "SPIRITKIN_EVENTS_PORT",
        protocol="ws",
        description="运行事件、桌面同步事件和 avatar 状态广播。",
    ),
    ServicePortSpec(
        "command_gateway",
        "命令网关",
        8788,
        "SPIRITKIN_COMMAND_PORT",
        protocol="http",
        path="/command",
        description="桌面端、移动端、Skills 和 Agent 调度 HTTP 入口。",
    ),
    ServicePortSpec(
        "remote_worker",
        "远端 Worker",
        8790,
        "SPIRITKIN_REMOTE_WORKER_PORT",
        protocol="http",
        required=False,
        description="可选远端 Skill 包、子 Worker 和受限工作节点入口。",
    ),
    ServicePortSpec(
        "android_endpoint",
        "Android Control Bridge",
        8791,
        "SPIRITKIN_ANDROID_PORT",
        protocol="http",
        path="/android/link",
        required=False,
        description="Android Companion/控制桥接端命令队列和手机分享链接入口。",
    ),
    ServicePortSpec(
        "ios_endpoint",
        "iOS 控制入口",
        8792,
        "SPIRITKIN_IOS_PORT",
        protocol="http",
        path="/ios/shortcut",
        required=False,
        description="iOS Shortcuts/PWA 控制入口。",
    ),
)


def resolve_service_port_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SERVICE_PORT_CONFIG_PATH", DEFAULT_SERVICE_PORT_CONFIG_PATH, path)


def load_service_port_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config_path = resolve_service_port_config_path(path)
    if not config_path.exists():
        return _default_config()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    if not isinstance(payload, dict):
        return _default_config()
    config = _default_config()
    config.update(payload)
    config["schema_version"] = CONFIG_SCHEMA_VERSION
    raw_overrides = config.get("overrides")
    config["overrides"] = _normalize_overrides(raw_overrides)
    config["profiles"] = _normalize_profiles(config.get("profiles"))
    history = config.get("history")
    config["history"] = list(history)[-80:] if isinstance(history, list) else []
    return config


def save_service_port_config(config: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config_path = resolve_service_port_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _default_config()
    normalized.update(config)
    normalized["schema_version"] = CONFIG_SCHEMA_VERSION
    normalized["overrides"] = _normalize_overrides(normalized.get("overrides"))
    normalized["profiles"] = _normalize_profiles(normalized.get("profiles"))
    normalized["history"] = list(normalized.get("history") or [])[-80:]
    normalized["updated_at"] = time.time()
    config_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def resolve_service_port(service_id: str, default: int = 0) -> int:
    config = load_service_port_config()
    for spec in PORT_SPECS:
        if spec.service_id == service_id:
            return spec.resolved_port(config)
    return default


def build_service_port_snapshot(*, host: str | None = None, include_conflicts: bool = True) -> dict[str, Any]:
    bind_host = host or os.getenv("SPIRITKIN_SERVICE_HOST") or os.getenv("SPIRITKIN_COMMAND_HOST") or DEFAULT_HOST
    config = load_service_port_config()
    services = [spec.snapshot(host=bind_host, config=config) for spec in PORT_SPECS]
    duplicate_ports = _duplicate_ports(services)
    issues: list[dict[str, Any]] = []
    for item in services:
        env_value = str(item.get("env_value") or "").strip()
        if env_value and _parse_port(env_value) is None:
            issues.append(
                {
                    "issue_id": f"invalid-env-port-{item['service_id']}",
                    "severity": "medium",
                    "title": f"{item['env_var']} 不是有效端口",
                    "detail": env_value,
                }
            )
    for port, service_ids in duplicate_ports.items():
        issues.append(
            {
                "issue_id": f"duplicate-port-{port}",
                "severity": "high",
                "title": f"端口 {port} 被多个 SpiritKin 服务声明",
                "detail": ", ".join(service_ids),
            }
        )
    if include_conflicts:
        for item in services:
            if item["listening"]:
                issues.append(
                    {
                        "issue_id": f"port-listening-{item['service_id']}-{item['port']}",
                        "severity": "info",
                        "title": f"{item['label']} 端口正在监听",
                        "detail": item["url"] or f"{item['browser_host']}:{item['port']}",
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "host": bind_host,
        "config_path": str(resolve_service_port_config_path()),
        "config": {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "path": str(resolve_service_port_config_path()),
            "overrides": dict(config.get("overrides") or {}),
            "profiles": dict(config.get("profiles") or {}),
            "updated_at": float(config.get("updated_at") or 0.0),
            "history": list(config.get("history") or [])[-20:],
        },
        "defaults": {item.service_id: item.default_port for item in PORT_SPECS},
        "ports": {item["service_id"]: item["port"] for item in services},
        "services": services,
        "duplicate_ports": duplicate_ports,
        "issues": issues,
        "env_overrides": {item["service_id"]: item["env_value"] for item in services if item["env_value"]},
    }


def handle_service_port_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "status"}:
        return {"ok": True, "service_ports": build_service_port_snapshot()}
    if action in {"save_port", "set_port", "update_port"}:
        return save_service_port_override(
            service_id=str(payload.get("service_id") or ""),
            port=payload.get("port"),
            actor=str(payload.get("actor") or "desktop"),
        )
    if action in {"reset_port", "clear_port"}:
        return reset_service_port_override(str(payload.get("service_id") or ""), actor=str(payload.get("actor") or "desktop"))
    if action in {"reset_all", "clear_all"}:
        return reset_all_service_port_overrides(actor=str(payload.get("actor") or "desktop"))
    if action in {"repair_duplicates", "auto_repair", "auto_assign"}:
        return repair_duplicate_service_ports(actor=str(payload.get("actor") or "desktop"))
    if action in {"save_profile", "capture_profile"}:
        return save_service_port_profile(
            profile_id=str(payload.get("profile_id") or ""),
            label=str(payload.get("label") or ""),
            project_id=str(payload.get("project_id") or ""),
            workspace_path=str(payload.get("workspace_path") or ""),
            actor=str(payload.get("actor") or "desktop"),
        )
    if action in {"apply_profile", "use_profile"}:
        return apply_service_port_profile(str(payload.get("profile_id") or ""), actor=str(payload.get("actor") or "desktop"))
    if action in {"delete_profile", "remove_profile"}:
        return delete_service_port_profile(str(payload.get("profile_id") or ""), actor=str(payload.get("actor") or "desktop"))
    raise ValueError(f"unsupported service port action: {action}")


def save_service_port_override(*, service_id: str, port: Any, actor: str = "desktop") -> dict[str, Any]:
    spec = _spec_for_service(service_id)
    if spec is None:
        raise ValueError(f"unknown service_id: {service_id}")
    normalized_port = _normalize_user_port(port, required=spec.required)
    before = build_service_port_snapshot(include_conflicts=False)
    config = load_service_port_config()
    overrides = dict(config.get("overrides") or {})
    overrides[spec.service_id] = normalized_port
    config["overrides"] = overrides
    _append_history(config, "save_port", spec.service_id, normalized_port, actor)
    saved = save_service_port_config(config)
    env_locked = bool(os.getenv(spec.env_var, "").strip())
    service_ports = build_service_port_snapshot()
    return {
        "ok": True,
        "service_id": spec.service_id,
        "port": normalized_port,
        "env_override_active": env_locked,
        "message": _service_port_message(spec, normalized_port, env_locked),
        "config": saved,
        "restart_guidance": _build_restart_guidance([spec.service_id], before=before, after=service_ports),
        "service_ports": service_ports,
    }


def reset_service_port_override(service_id: str, *, actor: str = "desktop") -> dict[str, Any]:
    spec = _spec_for_service(service_id)
    if spec is None:
        raise ValueError(f"unknown service_id: {service_id}")
    before = build_service_port_snapshot(include_conflicts=False)
    config = load_service_port_config()
    overrides = dict(config.get("overrides") or {})
    removed = overrides.pop(spec.service_id, None)
    config["overrides"] = overrides
    _append_history(config, "reset_port", spec.service_id, removed, actor)
    saved = save_service_port_config(config)
    service_ports = build_service_port_snapshot()
    return {
        "ok": True,
        "service_id": spec.service_id,
        "removed_port": removed,
        "message": f"{spec.label} 已恢复默认/环境变量端口；重启服务后生效。",
        "config": saved,
        "restart_guidance": _build_restart_guidance([spec.service_id], before=before, after=service_ports),
        "service_ports": service_ports,
    }


def reset_all_service_port_overrides(*, actor: str = "desktop") -> dict[str, Any]:
    before = build_service_port_snapshot(include_conflicts=False)
    config = load_service_port_config()
    previous = dict(config.get("overrides") or {})
    config["overrides"] = {}
    _append_history(config, "reset_all", "", previous, actor)
    saved = save_service_port_config(config)
    service_ports = build_service_port_snapshot()
    return {
        "ok": True,
        "removed": previous,
        "message": "已清空端口配置覆盖；重启相关服务后生效。",
        "config": saved,
        "restart_guidance": _build_restart_guidance(list(previous.keys()), before=before, after=service_ports),
        "service_ports": service_ports,
    }


def repair_duplicate_service_ports(*, actor: str = "desktop") -> dict[str, Any]:
    snapshot = build_service_port_snapshot(include_conflicts=False)
    services = [dict(item) for item in snapshot.get("services") or [] if isinstance(item, dict)]
    duplicate_ports = dict(snapshot.get("duplicate_ports") or {})
    if not duplicate_ports:
        service_ports = build_service_port_snapshot()
        return {
            "ok": True,
            "changed": {},
            "blocked": [],
            "message": "没有重复端口需要修复。",
            "restart_guidance": _build_restart_guidance([], before=snapshot, after=service_ports),
            "service_ports": service_ports,
        }

    by_id = {str(item.get("service_id") or ""): item for item in services}
    config = load_service_port_config()
    overrides = dict(config.get("overrides") or {})
    used_ports = {int(item.get("port") or 0) for item in services if int(item.get("port") or 0) > 0}
    changed: dict[str, int] = {}
    blocked: list[dict[str, Any]] = []

    for port, service_ids in duplicate_ports.items():
        ids = [str(item) for item in service_ids]
        locked = [sid for sid in ids if str(by_id.get(sid, {}).get("env_value") or "").strip()]
        mutable = [sid for sid in ids if sid not in locked]
        if not mutable:
            blocked.append({"port": port, "service_ids": ids, "reason": "全部由环境变量锁定，无法写配置修复。"})
            continue
        keep = locked[0] if locked else mutable.pop(0)
        for service_id in list(mutable):
            spec = _spec_for_service(service_id)
            if spec is None:
                continue
            next_port = _find_available_port(spec, used_ports)
            used_ports.add(next_port)
            overrides[spec.service_id] = next_port
            changed[spec.service_id] = next_port
        if keep:
            used_ports.add(int(port))

    if changed:
        config["overrides"] = overrides
        _append_history(config, "repair_duplicates", "", {"changed": changed, "blocked": blocked}, actor)
        save_service_port_config(config)
    ok = not blocked
    message = f"已为 {len(changed)} 个服务分配无重复端口；重启相关服务后生效。" if changed else "未能自动修复重复端口。"
    service_ports = build_service_port_snapshot()
    return {
        "ok": ok,
        "changed": changed,
        "blocked": blocked,
        "message": message,
        "restart_guidance": _build_restart_guidance(list(changed.keys()), before=snapshot, after=service_ports, blocked=blocked),
        "service_ports": service_ports,
    }


def save_service_port_profile(
    *,
    profile_id: str,
    label: str = "",
    project_id: str = "",
    workspace_path: str = "",
    actor: str = "desktop",
) -> dict[str, Any]:
    normalized_id = _normalize_profile_id(profile_id or project_id or label or workspace_path)
    snapshot = build_service_port_snapshot(include_conflicts=False)
    config = load_service_port_config()
    profiles = _normalize_profiles(config.get("profiles"))
    profile = {
        "profile_id": normalized_id,
        "label": label.strip() or normalized_id,
        "project_id": project_id.strip(),
        "workspace_path": workspace_path.strip(),
        "overrides": dict(config.get("overrides") or {}),
        "ports": dict(snapshot.get("ports") or {}),
        "created_at": float(profiles.get(normalized_id, {}).get("created_at") or time.time()),
        "updated_at": time.time(),
        "actor": actor,
    }
    profiles[normalized_id] = profile
    config["profiles"] = profiles
    _append_history(config, "save_profile", normalized_id, {"project_id": profile["project_id"], "workspace_path": profile["workspace_path"]}, actor)
    saved = save_service_port_config(config)
    service_ports = build_service_port_snapshot()
    return {
        "ok": True,
        "profile_id": normalized_id,
        "profile": saved.get("profiles", {}).get(normalized_id, profile),
        "message": f"已保存端口 Profile：{profile['label']}。",
        "config": saved,
        "restart_guidance": _build_restart_guidance([], before=snapshot, after=service_ports),
        "service_ports": service_ports,
    }


def apply_service_port_profile(profile_id: str, *, actor: str = "desktop") -> dict[str, Any]:
    normalized_id = _normalize_profile_id(profile_id)
    before = build_service_port_snapshot(include_conflicts=False)
    config = load_service_port_config()
    profiles = _normalize_profiles(config.get("profiles"))
    profile = profiles.get(normalized_id)
    if profile is None:
        raise ValueError(f"unknown service port profile: {profile_id}")
    config["overrides"] = _normalize_overrides(profile.get("overrides"))
    _append_history(config, "apply_profile", normalized_id, dict(config["overrides"]), actor)
    saved = save_service_port_config(config)
    service_ports = build_service_port_snapshot()
    changed_ids = _changed_service_ids(before, service_ports)
    return {
        "ok": True,
        "profile_id": normalized_id,
        "profile": saved.get("profiles", {}).get(normalized_id, profile),
        "message": f"已应用端口 Profile：{profile.get('label') or normalized_id}；如运行端口变化，请重启相关服务。",
        "config": saved,
        "restart_guidance": _build_restart_guidance(changed_ids, before=before, after=service_ports),
        "service_ports": service_ports,
    }


def delete_service_port_profile(profile_id: str, *, actor: str = "desktop") -> dict[str, Any]:
    normalized_id = _normalize_profile_id(profile_id)
    config = load_service_port_config()
    profiles = _normalize_profiles(config.get("profiles"))
    removed = profiles.pop(normalized_id, None)
    if removed is None:
        raise ValueError(f"unknown service port profile: {profile_id}")
    config["profiles"] = profiles
    _append_history(config, "delete_profile", normalized_id, {"label": removed.get("label")}, actor)
    saved = save_service_port_config(config)
    service_ports = build_service_port_snapshot()
    return {
        "ok": True,
        "profile_id": normalized_id,
        "removed": removed,
        "message": f"已删除端口 Profile：{removed.get('label') or normalized_id}。",
        "config": saved,
        "restart_guidance": _build_restart_guidance([], before=service_ports, after=service_ports),
        "service_ports": service_ports,
    }


def build_default_service_env(*, host: str = DEFAULT_HOST, token: str = "") -> dict[str, str]:
    browser_host = _browser_host(host)
    events_port = resolve_service_port("event_bridge", 8765)
    command_port = resolve_service_port("command_gateway", 8788)
    frontend_port = resolve_service_port("frontend", 8787)
    env = {
        "SPIRITKIN_EVENTS_BIND_HOST": host,
        "SPIRITKIN_EVENTS_HOST": browser_host,
        "SPIRITKIN_EVENTS_PORT": str(events_port),
        "SPIRITKIN_EVENTS_WS_URL": f"ws://{browser_host}:{events_port}",
        "SPIRITKIN_COMMAND_HOST": host,
        "SPIRITKIN_COMMAND_PORT": str(command_port),
        "SPIRITKIN_FRONTEND_PORT": str(frontend_port),
    }
    if token:
        env["SPIRITKIN_MOBILE_TOKEN"] = token
    return env


def _default_config() -> dict[str, Any]:
    return {"schema_version": CONFIG_SCHEMA_VERSION, "overrides": {}, "profiles": {}, "updated_at": 0.0, "history": []}


def _normalize_overrides(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    known = {spec.service_id: spec for spec in PORT_SPECS}
    normalized: dict[str, int] = {}
    for service_id, value in raw.items():
        spec = known.get(str(service_id))
        if spec is None:
            continue
        try:
            port = _normalize_user_port(value, required=spec.required)
        except ValueError:
            continue
        normalized[spec.service_id] = port
    return normalized


def _normalize_profiles(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    profiles: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        profile_id = _normalize_profile_id(str(value.get("profile_id") or key))
        overrides = _normalize_overrides(value.get("overrides"))
        if not profile_id:
            continue
        profiles[profile_id] = {
            "profile_id": profile_id,
            "label": str(value.get("label") or profile_id).strip() or profile_id,
            "project_id": str(value.get("project_id") or "").strip(),
            "workspace_path": str(value.get("workspace_path") or "").strip(),
            "overrides": overrides,
            "ports": {service_id: port for service_id, port in _normalize_port_map(value.get("ports")).items()},
            "created_at": float(value.get("created_at") or 0.0),
            "updated_at": float(value.get("updated_at") or 0.0),
            "actor": str(value.get("actor") or "").strip(),
        }
    return profiles


def _normalize_port_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    known = {spec.service_id for spec in PORT_SPECS}
    ports: dict[str, int] = {}
    for service_id, value in raw.items():
        normalized_id = str(service_id or "").strip()
        port = _parse_port(value)
        if normalized_id in known and port is not None:
            ports[normalized_id] = port
    return ports


def _normalize_profile_id(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    if not normalized:
        raise ValueError("profile_id is required")
    return normalized[:80]


def _changed_service_ids(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_ports = dict(before.get("ports") or {}) if isinstance(before, dict) else {}
    after_ports = dict(after.get("ports") or {}) if isinstance(after, dict) else {}
    service_ids = sorted(set(before_ports) | set(after_ports))
    return [service_id for service_id in service_ids if int(before_ports.get(service_id) or 0) != int(after_ports.get(service_id) or 0)]


def _parse_port(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 65535 else None


def _normalize_user_port(value: Any, *, required: bool = True) -> int:
    parsed = _parse_port(value)
    if parsed is None:
        raise ValueError("port must be an integer between 1 and 65535")
    if parsed == 0 and required:
        raise ValueError("required service port cannot be 0")
    if parsed < 0 or parsed > 65535:
        raise ValueError("port must be between 1 and 65535")
    return parsed


def _resolved_port_details(spec: ServicePortSpec, config: dict[str, Any]) -> tuple[int, str, int | None]:
    env_value = os.getenv(spec.env_var, "").strip()
    env_port = _parse_port(env_value)
    overrides = config.get("overrides") if isinstance(config, dict) else {}
    config_port = None
    if isinstance(overrides, dict) and spec.service_id in overrides:
        config_port = _parse_port(overrides.get(spec.service_id))
    if env_value and env_port is not None:
        return env_port, "env", config_port
    if config_port is not None:
        return config_port, "config", config_port
    return spec.default_port, "default", config_port


def _spec_for_service(service_id: str) -> ServicePortSpec | None:
    normalized = str(service_id or "").strip()
    return next((spec for spec in PORT_SPECS if spec.service_id == normalized), None)


def _append_history(config: dict[str, Any], action: str, service_id: str, value: Any, actor: str) -> None:
    history = list(config.get("history") or [])
    history.append({"at": time.time(), "action": action, "service_id": service_id, "value": value, "actor": actor})
    config["history"] = history[-80:]


def _service_port_message(spec: ServicePortSpec, port: int, env_locked: bool) -> str:
    if env_locked:
        return f"{spec.label} 已保存配置端口 {port}，但当前 {spec.env_var} 环境变量仍优先生效。"
    return f"{spec.label} 已保存端口 {port}；重启相关服务后生效。"


def _build_restart_guidance(
    service_ids: list[str],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    blocked: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    before_services = _services_by_id(before)
    after_services = _services_by_id(after)
    changed_ids: list[str] = []
    affected: list[dict[str, Any]] = []
    env_locked_ids: list[str] = []
    normalized_ids = _unique_service_ids(service_ids)

    for service_id in normalized_ids:
        current = after_services.get(service_id, {})
        previous = before_services.get(service_id, {})
        if not current and not previous:
            continue
        spec = _spec_for_service(service_id)
        before_port = int(previous.get("port") or 0)
        after_port = int(current.get("port") or 0)
        port_changed = before_port != after_port
        env_value = str(current.get("env_value") or "").strip()
        if env_value:
            env_locked_ids.append(service_id)
        if port_changed:
            changed_ids.append(service_id)
        affected.append(
            {
                "service_id": service_id,
                "label": str(current.get("label") or previous.get("label") or service_id),
                "before_port": before_port,
                "after_port": after_port,
                "before_url": str(previous.get("url") or ""),
                "after_url": str(current.get("url") or ""),
                "before_source": str(previous.get("source") or ""),
                "after_source": str(current.get("source") or ""),
                "env_var": str(current.get("env_var") or (spec.env_var if spec else "")),
                "env_value": env_value,
                "port_changed": port_changed,
                "restart_managed": service_id in MANAGED_RESTART_SERVICE_IDS,
            }
        )

    restart_required = bool(changed_ids)
    managed_ids = [service_id for service_id in changed_ids if service_id in MANAGED_RESTART_SERVICE_IDS]
    unmanaged_ids = [service_id for service_id in changed_ids if service_id not in MANAGED_RESTART_SERVICE_IDS]
    guidance = {
        "schema_version": RESTART_GUIDANCE_SCHEMA_VERSION,
        "restart_required": restart_required,
        "service_ids": changed_ids,
        "managed_service_ids": managed_ids,
        "unmanaged_service_ids": unmanaged_ids,
        "primary_service_id": managed_ids[0] if managed_ids else "",
        "blocked_by_env": [item for item in affected if item["service_id"] in env_locked_ids and not item["port_changed"]],
        "services": affected,
        "migration_notes": _build_migration_notes(affected),
        "manual_steps": [],
        "message": "",
        "blocked": list(blocked or []),
    }
    guidance["manual_steps"] = _build_restart_steps(guidance)
    guidance["message"] = _restart_guidance_message(guidance)
    return guidance


def _services_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    services = snapshot.get("services") if isinstance(snapshot, dict) else []
    if not isinstance(services, list):
        return {}
    return {str(item.get("service_id") or ""): dict(item) for item in services if isinstance(item, dict)}


def _unique_service_ids(service_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for service_id in service_ids:
        normalized = str(service_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _build_migration_notes(services: list[dict[str, Any]]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for item in services:
        if not item.get("port_changed"):
            continue
        service_id = str(item.get("service_id") or "")
        after_url = str(item.get("after_url") or "")
        after_port = int(item.get("after_port") or 0)
        if service_id == "command_gateway":
            notes.append(
                {
                    "service_id": service_id,
                    "title": "更新桌面/API 客户端命令网关地址",
                    "detail": f"命令网关重启会短暂断开当前请求；重启后将桌面 Api URL、移动端或脚本改为 http://127.0.0.1:{after_port}。",
                    "target_url": f"http://127.0.0.1:{after_port}",
                }
            )
        elif service_id == "event_bridge":
            notes.append(
                {
                    "service_id": service_id,
                    "title": "更新实时事件 WebSocket 地址",
                    "detail": f"重启事件桥后，将桌面/网页事件通道改为 {after_url or f'ws://127.0.0.1:{after_port}'}。",
                    "target_url": after_url or f"ws://127.0.0.1:{after_port}",
                }
            )
        elif service_id == "frontend":
            notes.append(
                {
                    "service_id": service_id,
                    "title": "更新桌面 Web 控制台地址",
                    "detail": f"前端服务重启后，浏览器入口改为 {after_url or f'http://127.0.0.1:{after_port}/desktop_console.html'}。",
                    "target_url": after_url or f"http://127.0.0.1:{after_port}/desktop_console.html",
                }
            )
        elif service_id in {"android_endpoint", "ios_endpoint"}:
            notes.append(
                {
                    "service_id": service_id,
                    "title": "更新移动端控制入口",
                    "detail": f"把手机端/PWA/Shortcuts/桥接端中的控制入口改为 {after_url or f'http://127.0.0.1:{after_port}'}。",
                    "target_url": after_url or f"http://127.0.0.1:{after_port}",
                }
            )
        elif service_id == "remote_worker":
            notes.append(
                {
                    "service_id": service_id,
                    "title": "更新 Remote Worker 地址",
                    "detail": f"重启 Remote Worker 后，将远端 Worker 连接地址改为 {after_url or f'http://127.0.0.1:{after_port}'}。",
                    "target_url": after_url or f"http://127.0.0.1:{after_port}",
                }
            )
    return notes


def _build_restart_steps(guidance: dict[str, Any]) -> list[str]:
    restart_required = bool(guidance.get("restart_required"))
    services = list(guidance.get("services") or [])
    if not restart_required:
        if guidance.get("blocked"):
            return ["部分端口未能自动修复；检查重复端口服务上的环境变量锁定，移除或改写后再运行修复。"]
        if guidance.get("blocked_by_env"):
            locked = ", ".join(str(item.get("env_var") or item.get("service_id")) for item in guidance.get("blocked_by_env") or [])
            return [f"当前端口仍由环境变量锁定（{locked}）；移除环境变量并重启对应服务后配置才会生效。"]
        return ["运行端口未变化，无需重启服务。"]
    steps = []
    managed = [item for item in services if item.get("port_changed") and item.get("restart_managed")]
    unmanaged = [item for item in services if item.get("port_changed") and not item.get("restart_managed")]
    if managed:
        names = ", ".join(str(item.get("label") or item.get("service_id")) for item in managed)
        steps.append(f"在 Services 页面重启：{names}。")
    if unmanaged:
        names = ", ".join(str(item.get("label") or item.get("service_id")) for item in unmanaged)
        steps.append(f"这些端口不由 Operations Center 托管，需要重启对应外部桥接端或客户端：{names}。")
    if any(str(item.get("service_id")) == "command_gateway" and item.get("port_changed") for item in services):
        steps.append("命令网关重启会短暂断开桌面 API；如果当前桌面仍指向旧端口，请手动更新 Api URL 后刷新。")
    if guidance.get("migration_notes"):
        steps.append("同步更新桌面、网页、移动端、Remote Worker 或脚本中保存的旧 URL。")
    return steps


def _restart_guidance_message(guidance: dict[str, Any]) -> str:
    if guidance.get("restart_required"):
        service_count = len(guidance.get("service_ids") or [])
        managed_count = len(guidance.get("managed_service_ids") or [])
        if managed_count:
            return f"{service_count} 个服务端口已变化；可在桌面端重启 {managed_count} 个托管服务后生效。"
        return f"{service_count} 个服务端口已变化；请重启对应外部服务后生效。"
    if guidance.get("blocked"):
        return "端口修复被环境变量锁定阻止；需要先处理环境变量后再修复。"
    if guidance.get("blocked_by_env"):
        return "配置已保存，但当前运行端口仍由环境变量锁定；移除环境变量并重启后才会切换。"
    return "端口配置已更新；运行端口未变化，无需重启。"


def _find_available_port(spec: ServicePortSpec, used_ports: set[int]) -> int:
    candidates = [spec.default_port, spec.default_port + 100, 8800, 8900, 9000]
    for start in candidates:
        port = max(1, min(65535, int(start)))
        for candidate in range(port, min(65535, port + 300)):
            if candidate in used_ports:
                continue
            if not _port_accepts_connection(DEFAULT_HOST, candidate, timeout=0.05):
                return candidate
    raise ValueError(f"no available port found for {spec.service_id}")


def _browser_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _service_url(protocol: str, host: str, port: int, path: str) -> str:
    suffix = path if path.startswith("/") or not path else f"/{path}"
    return f"{protocol}://{host}:{port}{suffix}"


def _device_probes_disabled() -> bool:
    if "pytest" in sys.modules:
        return True
    return os.getenv("SPIRITKIN_DISABLE_DEVICE_PROBES", "").strip().lower() in {"1", "true", "yes", "on"}


def _port_accepts_connection(host: str, port: int, *, timeout: float = 0.25) -> bool:
    if _device_probes_disabled():
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _duplicate_ports(services: list[dict[str, Any]]) -> dict[int, list[str]]:
    by_port: dict[int, list[str]] = {}
    for item in services:
        port = int(item.get("port") or 0)
        if port <= 0:
            continue
        by_port.setdefault(port, []).append(str(item.get("service_id") or "unknown"))
    return {port: service_ids for port, service_ids in by_port.items() if len(service_ids) > 1}
