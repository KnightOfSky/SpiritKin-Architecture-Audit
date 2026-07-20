from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlencode

from backend.app.mobile_security import build_mobile_security_snapshot
from backend.app.service_ports import resolve_service_port
from backend.mobile.android_apk_promotion import approve_apk_release, build_apk_promotion_gate
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.mobile.android_permissions import build_android_permission_policy
from backend.mobile.artifact_store import MobileArtifactStore
from backend.mobile.ios_bridge import build_shortcut_url_scheme
from backend.mobile.ios_shortcuts_catalog import SHORTCUT_CATALOG

SCHEMA_VERSION = "spiritkin.mobile_management.v1"
DEFAULT_ANDROID_PACKAGE = "com.spiritkin.mobilelinkbridge"
DEFAULT_ADB_STATE_PATH = "state/mobile/adb-wireless.json"
DEFAULT_ANDROID_BRIDGE_ROOT = "mobile-link-bridge"
_DEVICE_PROBE_STATE = threading.local()


def _device_probes_disabled() -> bool:
    """Whether live device/network probing (adb, tailscale, health HTTP) is off.

    Set SPIRITKIN_DISABLE_DEVICE_PROBES=1 in tests/CI/offline hosts so read-only
    snapshots never block on external tooling that may not exist or may hang
    (e.g. a Windows adb daemon holding the stdout pipe open). Probing is also
    disabled automatically under pytest, which survives env-clearing test setups.
    """

    if bool(getattr(_DEVICE_PROBE_STATE, "disabled", False)):
        return True
    if "pytest" in sys.modules:
        return True
    return os.getenv("SPIRITKIN_DISABLE_DEVICE_PROBES", "").strip().lower() in {"1", "true", "yes", "on"}


def build_mobile_management_snapshot(*, disable_device_probes: bool = False) -> dict[str, Any]:
    previous = bool(getattr(_DEVICE_PROBE_STATE, "disabled", False))
    if disable_device_probes:
        _DEVICE_PROBE_STATE.disabled = True
    try:
        return _build_mobile_management_snapshot()
    finally:
        _DEVICE_PROBE_STATE.disabled = previous


def _build_mobile_management_snapshot() -> dict[str, Any]:
    adb_path = _resolve_adb_path()
    adb_state = _load_adb_state()
    device_ip = _android_device_ip(adb_state)
    android_port = resolve_service_port("android_endpoint", 8791)
    ios_port = resolve_service_port("ios_endpoint", 8792)
    pc_tailscale_ip = _pc_tailscale_ip()
    bridge_root = _android_bridge_root()
    apk_path = bridge_root / "out" / "mobile-link-bridge.apk"
    apk = _apk_snapshot(apk_path)
    android_release_manifest = _android_release_manifest_snapshot(bridge_root)

    devices = _adb_devices(adb_path)
    active_device = _active_android_device(devices, device_ip)
    package_info = _android_package_info(adb_path, active_device.get("serial", ""))
    companion = AndroidCompanionStore().snapshot()
    artifacts = MobileArtifactStore().snapshot()
    android_health = _http_health(f"http://127.0.0.1:{android_port}/android/health")
    ios_control_base_url = f"http://{pc_tailscale_ip or '127.0.0.1'}:{android_port}"
    ios_health = _http_health(f"http://127.0.0.1:{android_port}/health")
    receiver_url = f"http://{pc_tailscale_ip or '127.0.0.1'}:{android_port}/android/link"
    pairing_base_url = f"http://{pc_tailscale_ip or '127.0.0.1'}:{android_port}/pairing"
    ios_base_url = f"http://{pc_tailscale_ip or '127.0.0.1'}:{ios_port}"
    control_snapshot = _control_plane_snapshot(android_health)
    workspaces = _control_workspaces(control_snapshot)
    workspace_devices = _control_workspace_devices(control_snapshot, workspaces)
    accounts = _control_accounts(control_snapshot)
    default_workspace_id = workspaces[0]["workspace_id"] if workspaces else "local-ecommerce"
    pairing_url = f"{pairing_base_url}?workspace_id={default_workspace_id}"
    # Native iOS control uses the authenticated multi-client receiver. The
    # standalone 8792 endpoint is optional and often occupied by the PWA.
    ios_native_pairing = _ios_native_terminal_pairing(ios_control_base_url, workspace_id=default_workspace_id)
    security = build_mobile_security_snapshot(
        pc_tailscale_ip=pc_tailscale_ip,
        android_receiver_url=receiver_url,
        android_pairing_url=pairing_url,
        ios_base_url=ios_control_base_url,
    )
    binding = _mobile_binding_snapshot(
        workspaces=workspaces,
        default_workspace_id=default_workspace_id,
        security=security,
        android_receiver_url=receiver_url,
        android_pairing_url=pairing_url,
        ios_base_url=ios_base_url,
        ios_control_base_url=ios_control_base_url,
        ios_native_pairing=ios_native_pairing,
    )
    android_worker = _android_worker_snapshot(
        companion=companion,
        android_health=android_health,
        apk=apk,
        installed=package_info,
        release_manifest=android_release_manifest,
        receiver_url=receiver_url,
        package=DEFAULT_ANDROID_PACKAGE,
        bridge_root=bridge_root,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "workspaces": workspaces,
        "workspace_devices": workspace_devices,
        "accounts": accounts,
        "default_workspace_id": default_workspace_id,
        "security": security,
        "binding": binding,
        "android_command_permissions": build_android_permission_policy(),
        "android": {
            "package": DEFAULT_ANDROID_PACKAGE,
            "adb_path": str(adb_path) if adb_path else "",
            "device_ip": device_ip,
            "known_port": int(adb_state.get("port") or 0),
            "active_device": active_device,
            "devices": devices,
            "bridge_root": str(bridge_root),
            "apk_path": str(apk_path),
            "apk": apk,
            "release_manifest": android_release_manifest,
            "installed": package_info,
            "worker": android_worker,
            "companion": companion,
            "artifacts": artifacts,
            "receiver_url": receiver_url,
            "pairing_url": pairing_url,
            "endpoint": {
                "local_health_url": f"http://127.0.0.1:{android_port}/android/health",
                "tailscale_health_url": f"http://{pc_tailscale_ip or '127.0.0.1'}:{android_port}/android/health",
                "port": android_port,
                "pc_tailscale_ip": pc_tailscale_ip,
                "health": android_health,
            },
            "reconnect_script": str((Path.cwd() / "scripts" / "adb_wireless_reconnect.ps1").resolve()),
        },
        "ios": {
            "endpoint": {
                "local_health_url": f"http://127.0.0.1:{android_port}/health",
                "tailscale_base_url": ios_base_url,
                "control_base_url": ios_control_base_url,
                "control_port": android_port,
                "pwa_port": ios_port,
                "port": ios_port,
                "pc_tailscale_ip": pc_tailscale_ip,
                "health": ios_health,
            },
            "native_terminal": ios_native_pairing,
            "shortcuts": [
                {
                    "name": item.name,
                    "description": item.description,
                    "icon": item.icon,
                    "color": item.color,
                    "output_type": item.output_type,
                    "confirmation_required": item.confirmation_required,
                    "url": build_shortcut_url_scheme(item.name, ios_control_base_url, token=""),
                }
                for item in SHORTCUT_CATALOG
            ],
            "terminal_status": "ready" if ios_health.get("ok") else "endpoint_stopped",
        },
    }


def handle_mobile_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "refresh").strip()
    result: dict[str, Any]
    if action == "refresh":
        result = {"ok": True, "status": "refreshed", "message": "移动端 Bridge 状态已刷新。"}
    elif action == "adb_reconnect":
        result = _run_adb_reconnect(payload)
    elif action == "install_android_bridge":
        result = _install_android_bridge(payload)
    elif action == "approve_android_apk_release":
        result = _approve_android_apk_release(payload)
    elif action == "enqueue_android_command":
        result = _enqueue_android_command(payload)
    elif action == "clear_android_commands":
        result = _clear_android_commands(payload)
    elif action == "ingest_mobile_artifacts":
        result = _ingest_mobile_artifacts(payload)
    elif action == "cleanup_mobile_artifacts":
        result = _cleanup_mobile_artifacts(payload)
    elif action == "create_android_pairing":
        result = _create_android_pairing(payload)
    elif action in {
        "add_device_workflow",
        "approve_pairing_request",
        "assign_workspace_to_account",
        "clear_binding_history",
        "clear_pairing_history",
        "create_account",
        "delete_device_workflow",
        "get_account_usage",
        "list_accounts",
        "reject_pairing_request",
        "set_device_workflow_state",
        "set_account_status",
        "repair_device_workflow",
        "update_account_plan",
    }:
        result = _control_plane_action(payload)
    elif action in {"start_android_endpoint", "restart_android_endpoint"}:
        result = _service_action("restart" if action.startswith("restart") else "start", "android_endpoint")
    elif action in {"start_ios_endpoint", "restart_ios_endpoint"}:
        result = _service_action("restart" if action.startswith("restart") else "start", "ios_endpoint")
    else:
        result = {"ok": False, "status": "unknown_action", "message": f"unknown mobile management action: {action}"}
    return {"ok": bool(result.get("ok")), "action": action, "result": result, "mobile_management": build_mobile_management_snapshot()}


def _control_plane_action(payload: dict[str, Any]) -> dict[str, Any]:
    android_port = resolve_service_port("android_endpoint", 8791)
    url = f"http://127.0.0.1:{android_port}/ios/control/action"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    token = os.getenv("SPIRITKIN_MANAGEMENT_TOKEN", "").strip() or os.getenv("SPIRITKIN_CONTROL_TOKEN", "").strip()
    if token:
        req.add_header("X-SpiritKin-Token", token)
    try:
        with request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict):
                ok = bool(data.get("ok", 200 <= response.status < 300))
                return {
                    "ok": ok,
                    "status": "ok" if ok else str(data.get("status") or "failed"),
                    "message": str(data.get("message") or data.get("error") or "控制面动作已执行。"),
                    "control_response": data,
                }
    except Exception as exc:
        return {"ok": False, "status": "control_action_failed", "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "status": "invalid_control_response", "message": "控制面返回格式无效。"}


def _create_android_pairing(payload: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(payload.get("workspace_id") or "local-ecommerce").strip() or "local-ecommerce"
    ttl_minutes = int(payload.get("ttl_minutes") or 30)
    android_port = resolve_service_port("android_endpoint", 8791)
    pc_tailscale_ip = _pc_tailscale_ip()
    base_url = f"http://{pc_tailscale_ip or '127.0.0.1'}:{android_port}"
    query = urlencode({"workspace_id": workspace_id, "ttl_minutes": ttl_minutes, "format": "json", "requested_by": "desktop"})
    url = f"{base_url}/pairing?{query}"
    response = _http_json(url, timeout=5)
    if not response.get("ok"):
        return {"ok": False, "status": "pairing_failed", "message": str(response.get("error") or "pairing endpoint failed"), "url": url}
    pairing = dict(response.get("pairing") or {})
    pairing_page_url = f"{base_url}/pairing?{urlencode({'workspace_id': workspace_id})}"
    pairing["pairing_page_url"] = pairing_page_url
    return {"ok": True, "status": "pairing_created", "message": f"已生成 Android 配对 token：{workspace_id}", "pairing": pairing}


def _ios_native_terminal_pairing(base_url: str, *, workspace_id: str) -> dict[str, Any]:
    pairing_token = os.getenv("SPIRITKIN_IOS_PAIRING_TOKEN", "").strip()
    payload = {
        "base_url": base_url.rstrip("/"),
        "workspace_id": workspace_id,
        "pairing_token": pairing_token,
        "schema_version": "spiritkin.ios_terminal_config.v1",
    }
    public_payload = {key: value for key, value in payload.items() if key != "pairing_token" or value}
    pairing_url = f"{base_url.rstrip('/')}/ios/control/pairing?{urlencode({'workspace_id': workspace_id, 'device_role': 'ios_terminal', 'requested_by': 'desktop', 'format': 'json'})}"
    public_payload["pairing_url"] = pairing_url
    query = urlencode(
        {
            "server_url": payload["base_url"],
            "workspace_id": workspace_id,
            "device_role": "ios_terminal",
            **({"pairing_token": pairing_token} if pairing_token else {}),
        }
    )
    deep_link = f"spiritkin://pair?{query}"
    return {
        "app_id": "com.spiritkin.terminal",
        "scheme": "spiritkin",
        "base_url": payload["base_url"],
        "workspace_id": workspace_id,
        "has_pairing_token": bool(pairing_token),
        "requires_pairing": not bool(pairing_token),
        "pairing_url": pairing_url,
        "deep_link": deep_link,
        "config_json": json.dumps(public_payload, ensure_ascii=False, sort_keys=True),
    }


def _mobile_binding_snapshot(
    *,
    workspaces: list[dict[str, str]],
    default_workspace_id: str,
    security: dict[str, Any],
    android_receiver_url: str,
    android_pairing_url: str,
    ios_base_url: str,
    ios_control_base_url: str,
    ios_native_pairing: dict[str, Any],
) -> dict[str, Any]:
    token_state = dict(security.get("tokens") or {})
    network_scope = str(security.get("network_scope") or "unknown")
    ios_terminal_url = _ios_terminal_url(ios_base_url, workspace_id=default_workspace_id)
    ios_install_url = _ios_install_url(ios_control_base_url, workspace_id=default_workspace_id)
    return {
        "schema_version": "spiritkin.mobile_binding.v1",
        "workspace_id": default_workspace_id,
        "workspaces": workspaces,
        "network": {
            "scope": network_scope,
            "pc_tailscale_ip": str(security.get("pc_tailscale_ip") or ""),
            "https_required_for_public": bool(security.get("https_required_for_public", True)),
            "public_transport": "https_or_wss",
            "preferred_private_transport": "tailscale",
            "operator_hint": str(security.get("operator_hint") or ""),
        },
        "tokens": {
            "command_gateway": {
                "configured": bool(token_state.get("command_gateway")),
                "env": "SPIRITKIN_MOBILE_TOKEN",
                "required_when": "desktop command gateway is reachable from phone/public network",
            },
            "android_endpoint": {
                "configured": bool(token_state.get("android_endpoint")),
                "env": "SPIRITKIN_ANDROID_TOKEN",
                "required_when": "Android endpoint is reachable outside localhost",
            },
            "ios_endpoint": {
                "configured": bool(token_state.get("ios_endpoint")),
                "env": "SPIRITKIN_IOS_TOKEN",
                "required_when": "iOS PWA/native terminal is reachable outside localhost",
            },
        },
        "desktop": {
            "role": "primary_controller",
            "workspace_binding": default_workspace_id,
        },
        "android": {
            "role": "controlled_executor",
            "workspace_binding": default_workspace_id,
            "receiver_url": android_receiver_url,
            "pairing_page_url": android_pairing_url,
            "pairing_action": "create_android_pairing",
            "pairing_ttl_minutes": 30,
            "requires_endpoint_token": bool(token_state.get("android_endpoint")),
        },
        "ios": {
            "role": "primary_mobile_controller",
            "workspace_binding": default_workspace_id,
            "pwa_url": ios_terminal_url,
            "control_snapshot_url": f"{ios_control_base_url.rstrip('/')}/ios/control/snapshot",
            "native_terminal": ios_native_pairing,
            "requires_endpoint_token": bool(token_state.get("ios_endpoint")),
            "home_screen": {
                "install_url": ios_install_url,
                "preview_url": ios_terminal_url,
                "manifest_url": f"{ios_control_base_url.rstrip('/')}/ios/terminal.webmanifest",
                "status": "available",
            },
        },
        "setup_steps": _mobile_binding_setup_steps(network_scope=network_scope, tokens=token_state),
    }


def _ios_terminal_url(base_url: str, *, workspace_id: str) -> str:
    query: dict[str, str] = {"workspace_id": workspace_id}
    token = os.getenv("SPIRITKIN_IOS_TOKEN", "").strip()
    if token:
        query["token"] = token
    # 8792 is the lightweight static PWA preview in the local stack. The
    # installable/authenticated PWA remains on the official 8791 control plane.
    return f"{base_url.rstrip('/')}/frontend/ios_controller_prototype.html?{urlencode(query)}"


def _ios_install_url(base_url: str, *, workspace_id: str) -> str:
    query: dict[str, str] = {"workspace_id": workspace_id}
    token = os.getenv("SPIRITKIN_IOS_TOKEN", "").strip()
    if token:
        query["token"] = token
    return f"{base_url.rstrip('/')}/ios/terminal?{urlencode(query)}"


def _mobile_binding_setup_steps(*, network_scope: str, tokens: dict[str, Any]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    if network_scope in {"public_or_unknown", "mixed"}:
        steps.append(
            {
                "id": "secure_public_transport",
                "severity": "high",
                "title": "公网访问必须使用 HTTPS/WSS",
                "detail": "把 Android/iOS/命令网关放到受控 HTTPS/WSS 反向代理后，或改用 Tailscale 私有地址。",
            }
        )
    if network_scope in {"local_only", "unknown"}:
        steps.append(
            {
                "id": "choose_phone_reachable_route",
                "severity": "medium",
                "title": "配置手机可访问入口",
                "detail": "真机使用需要 Tailscale IP、受控局域网地址或 HTTPS 域名；127.0.0.1 只适合本机调试。",
            }
        )
    missing_tokens = [key for key, configured in tokens.items() if not configured]
    if missing_tokens:
        steps.append(
            {
                "id": "set_endpoint_tokens",
                "severity": "medium",
                "title": "补齐移动端 token",
                "detail": "至少设置 SPIRITKIN_MOBILE_TOKEN、SPIRITKIN_ANDROID_TOKEN、SPIRITKIN_IOS_TOKEN 后再开放给手机网络。",
            }
        )
    steps.append(
        {
            "id": "bind_workspace",
            "severity": "low",
            "title": "绑定 workspace",
            "detail": "桌面端、iOS 控制端和 Android 被控端应使用同一个 workspace_id，避免命令和产物串到其它项目。",
        }
    )
    return steps


def _enqueue_android_command(payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "android_device").strip() or "android_device"
    operation = str(payload.get("operation") or "").strip()
    if not operation:
        return {"ok": False, "status": "missing_operation", "message": "operation is required."}
    params = dict(payload.get("params") or {})
    params.setdefault("actor", payload.get("actor") or "mobile_management")
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if workspace_id:
        control_payload = {
            "action": "queue_android_command",
            "workspace_id": workspace_id,
            "device_id": device_id,
            "operation": _normalize_android_operation(operation),
            "params": params,
            "requested_by": "desktop",
        }
        control_result = _control_plane_action(control_payload)
        if control_result.get("ok"):
            return {
                "ok": True,
                "status": "queued",
                "message": f"Android command queued on control plane: {control_payload['operation']}",
                **control_result,
            }
    result = AndroidCompanionStore().enqueue_command(device_id, operation, params)
    permission = result.get("permission") if isinstance(result.get("permission"), dict) else {}
    tier = str(permission.get("tier") or "")
    label = str(permission.get("label") or tier)
    return {
        "ok": bool(result.get("queued")),
        "status": "queued" if result.get("queued") else result.get("error_code", "not_queued"),
        "message": f"Android command queued: {operation} ({label})" if result.get("queued") else str(result.get("message") or "Android command was not queued."),
        **result,
    }


def _normalize_android_operation(operation: str) -> str:
    aliases = {
        "screenshot.capture": "android.screenshot.capture",
    }
    return aliases.get(operation, operation)


def _clear_android_commands(payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "").strip()
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if workspace_id:
        control_result = _control_plane_action(
            {
                "action": "clear_android_commands",
                "workspace_id": workspace_id,
                "device_id": device_id,
                "requested_by": "desktop",
            }
        )
        if control_result.get("ok"):
            removed = (((control_result.get("control_response") or {}).get("result") or {}).get("removed") or 0)
            return {"ok": True, "message": f"Cleared {removed} Android control-plane command(s).", **control_result}
    result = AndroidCompanionStore().clear_commands(device_id)
    return {"ok": True, "message": f"Cleared {result.get('removed', 0)} Android command(s).", **result}


def _ingest_mobile_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "mobile_management").strip() or "mobile_management"
    device_id = str(payload.get("device_id") or "").strip()
    result = MobileArtifactStore().ingest(payload, source=source, device_id=device_id)
    return result


def _cleanup_mobile_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    expired_only = bool(payload.get("expired_only", True))
    keep_recent = int(payload.get("keep_recent") or 200)
    result = MobileArtifactStore().cleanup(expired_only=expired_only, keep_recent=keep_recent)
    return {"message": f"已清理 {result.get('removed', 0)} 个移动端 artifact。", **result}


def _run_adb_reconnect(payload: dict[str, Any]) -> dict[str, Any]:
    script = Path.cwd() / "scripts" / "adb_wireless_reconnect.ps1"
    if not script.exists():
        return {"ok": False, "status": "missing_script", "message": str(script)}
    adb_state = _load_adb_state()
    device_ip = str(payload.get("device_ip") or _android_device_ip(adb_state)).strip()
    known_port = int(payload.get("known_port") or adb_state.get("port") or 41055)
    return _run_process(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-DeviceIp", device_ip, "-KnownPort", str(known_port)],
        timeout=75,
    )


def _install_android_bridge(payload: dict[str, Any]) -> dict[str, Any]:
    bridge_root = _android_bridge_root()
    build_script = bridge_root / "build.ps1"
    if bool(payload.get("build", True)):
        build = _run_process(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(build_script)], cwd=bridge_root, timeout=120)
        if not build.get("ok"):
            return {"ok": False, "status": "build_failed", "message": build.get("message", ""), "output": build.get("output", "")}

    reconnect = _run_adb_reconnect(payload)
    if not reconnect.get("ok"):
        return {"ok": False, "status": "adb_reconnect_failed", "message": reconnect.get("message", ""), "output": reconnect.get("output", "")}

    adb_path = _resolve_adb_path()
    if adb_path is None:
        return {"ok": False, "status": "missing_adb", "message": "adb.exe was not found."}
    state = _load_adb_state()
    device = _active_android_device(_adb_devices(adb_path), _android_device_ip(state))
    serial = str(device.get("serial") or state.get("serial") or "").strip()
    if not serial:
        return {"ok": False, "status": "missing_device", "message": "No connected Android ADB device."}
    apk_path = bridge_root / "out" / "mobile-link-bridge.apk"
    install = _run_process([str(adb_path), "-s", serial, "install", "-r", "-d", "--no-streaming", str(apk_path)], timeout=180)
    if not install.get("ok"):
        return {"ok": False, "status": "install_failed", "message": install.get("message", ""), "output": install.get("output", "")}
    _run_process([str(adb_path), "-s", serial, "shell", "am", "start", "-n", f"{DEFAULT_ANDROID_PACKAGE}/.MainActivity"], timeout=30)
    return {"ok": True, "status": "installed", "message": "Android Control Bridge APK 已构建、安装并启动。", "serial": serial, "output": install.get("output", "")}


def _approve_android_apk_release(payload: dict[str, Any]) -> dict[str, Any]:
    bridge_root = _android_bridge_root()
    apk_path = bridge_root / "out" / "mobile-link-bridge.apk"
    release_manifest = _read_android_release_manifest(bridge_root)
    return approve_apk_release(
        apk_path=apk_path,
        release_manifest=release_manifest,
        reviewer=str(payload.get("reviewer") or payload.get("actor") or "desktop"),
        reason=str(payload.get("reason") or payload.get("review_reason") or ""),
    )


def _service_action(action: str, service_id: str) -> dict[str, Any]:
    from backend.app.operations_center import handle_service_action

    return handle_service_action({"action": action, "service_id": service_id})


def _resolve_adb_path() -> Path | None:
    candidates: list[str] = [os.getenv("SPIRITKIN_ADB_PATH", "")]
    for sdk_env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk_root = os.getenv(sdk_env, "")
        if sdk_root:
            candidates.append(str(Path(sdk_root) / "platform-tools" / "adb.exe"))
    for value in candidates:
        if value and Path(value).exists():
            return Path(value)
    if _device_probes_disabled():
        return None
    local_appdata = os.getenv("LOCALAPPDATA", "")
    if local_appdata:
        winget_packages = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        for path in sorted(winget_packages.glob("Google.PlatformTools*/platform-tools/adb.exe")):
            return path
    located = shutil.which("adb")
    return Path(located) if located else None


def _android_bridge_root() -> Path:
    value = os.getenv("SPIRITKIN_ANDROID_BRIDGE_ROOT", DEFAULT_ANDROID_BRIDGE_ROOT)
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    workspace = Path(os.getenv("SPIRITKIN_WORKSPACE_ROOT", "") or Path.cwd()).resolve()
    return (workspace / path).resolve()


def _load_adb_state() -> dict[str, Any]:
    path = Path(os.getenv("SPIRITKIN_ADB_WIRELESS_STATE", DEFAULT_ADB_STATE_PATH))
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _android_device_ip(state: dict[str, Any]) -> str:
    return str(os.getenv("SPIRITKIN_ANDROID_ADB_IP") or state.get("device_ip") or "100.118.62.77").strip()


def _adb_devices(adb_path: Path | None) -> list[dict[str, Any]]:
    if adb_path is None:
        return []
    result = _run_process([str(adb_path), "devices", "-l"], timeout=15)
    devices: list[dict[str, Any]] = []
    for line in str(result.get("output") or "").splitlines():
        match = re.match(r"^(\S+)\s+(\S+)(.*)$", line.strip())
        if not match or match.group(1) == "List":
            continue
        devices.append({"serial": match.group(1), "state": match.group(2), "detail": match.group(3).strip()})
    return devices


def _active_android_device(devices: list[dict[str, Any]], device_ip: str) -> dict[str, Any]:
    for item in devices:
        if str(item.get("serial", "")).startswith(f"{device_ip}:") and item.get("state") == "device":
            return dict(item)
    for item in devices:
        if item.get("state") == "device":
            return dict(item)
    return {}


def _android_package_info(adb_path: Path | None, serial: str) -> dict[str, Any]:
    if adb_path is None or not serial:
        return {"installed": False}
    result = _run_process([str(adb_path), "-s", serial, "shell", "dumpsys", "package", DEFAULT_ANDROID_PACKAGE], timeout=30)
    output = str(result.get("output") or "")
    version_code = _regex(output, r"versionCode=(\d+)")
    version_name = _regex(output, r"versionName=([^\s]+)")
    updated_at = _regex(output, r"lastUpdateTime=([^\n\r]+)")
    return {
        "installed": bool(version_code or version_name),
        "version_code": version_code,
        "version_name": "" if version_name == "null" else version_name,
        "last_update_time": updated_at.strip(),
    }


def _apk_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    return {"exists": True, "path": str(path), "size_bytes": path.stat().st_size, "updated_at": path.stat().st_mtime}


def _android_release_manifest_snapshot(bridge_root: Path) -> dict[str, Any]:
    path = bridge_root / "out" / "release-manifest.json"
    if not path.exists():
        return {"exists": False, "path": str(path)}
    payload = _read_android_release_manifest(bridge_root)
    if not payload:
        return {"exists": True, "path": str(path), "status": "invalid", "error": "release manifest is not a JSON object"}
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "status": "ok",
        "version_name": str(payload.get("version_name") or payload.get("versionName") or app.get("version_name") or app.get("versionName") or ""),
        "version_code": str(payload.get("version_code") or payload.get("versionCode") or app.get("version_code") or app.get("versionCode") or ""),
        "sha256": str(payload.get("sha256") or payload.get("apk_sha256") or app.get("sha256") or ""),
        "created_at": str(payload.get("created_at") or payload.get("built_at") or payload.get("updated_at") or ""),
    }


def _read_android_release_manifest(bridge_root: Path) -> dict[str, Any]:
    path = bridge_root / "out" / "release-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _android_worker_snapshot(
    *,
    companion: dict[str, Any],
    android_health: dict[str, Any],
    apk: dict[str, Any],
    installed: dict[str, Any],
    release_manifest: dict[str, Any],
    receiver_url: str,
    package: str,
    bridge_root: Path,
) -> dict[str, Any]:
    base = dict(companion.get("worker") or {})
    queue = dict(base.get("queue") or {})
    lifecycle = dict(base.get("lifecycle") or {})
    health_ok = bool(android_health.get("ok"))
    status = str(base.get("status") or "needs_pairing")
    if not health_ok:
        status = "endpoint_offline"
    release_version = str(release_manifest.get("version_name") or "")
    installed_version = str(installed.get("version_name") or "")
    update_available = bool(apk.get("exists")) and bool(installed.get("installed")) and bool(release_version) and bool(installed_version) and release_version != installed_version
    apk_path = Path(str(apk.get("path") or "")) if str(apk.get("path") or "") else bridge_root / "out" / "mobile-link-bridge.apk"
    promotion_gate = build_apk_promotion_gate(apk_path=apk_path, release_manifest=_read_android_release_manifest(bridge_root))
    return {
        "schema_version": str(base.get("schema_version") or "spiritkin.android_worker.v1"),
        "worker_id": str(base.get("worker_id") or "android_control_worker"),
        "label": str(base.get("label") or "Android Control Worker"),
        "role": str(base.get("role") or "controlled_execution_worker"),
        "status": status,
        "device_count": int(base.get("device_count") or companion.get("device_count") or 0),
        "online_device_count": int(base.get("online_device_count") or 0),
        "pending_command_count": int(base.get("pending_command_count") or companion.get("pending_command_count") or 0),
        "inflight_command_count": int(base.get("inflight_command_count") or 0),
        "command_status_counts": base.get("command_status_counts") or companion.get("command_status_counts") or {},
        "permission_gap_count": int(base.get("permission_gap_count") or ((base.get("permissions") or {}).get("gap_count") or 0)),
        "capability_count": int(base.get("capability_count") or 0),
        "capabilities": base.get("capabilities") or [],
        "workspace_ids": base.get("workspace_ids") or [],
        "queue": {
            "pending": int(queue.get("pending") or base.get("pending_command_count") or companion.get("pending_command_count") or 0),
            "inflight": int(queue.get("inflight") or base.get("inflight_command_count") or 0),
            "status_counts": queue.get("status_counts") or base.get("command_status_counts") or companion.get("command_status_counts") or {},
        },
        "permissions": base.get("permissions") or {},
        "lifecycle": {
            **lifecycle,
            "endpoint_online": health_ok,
            "receiver_url": receiver_url,
            "package": package,
            "bridge_root": str(bridge_root),
        },
        "update": {
            "apk_exists": bool(apk.get("exists")),
            "apk_path": str(apk.get("path") or ""),
            "installed": bool(installed.get("installed")),
            "installed_version_name": installed_version,
            "installed_version_code": str(installed.get("version_code") or ""),
            "release_manifest_exists": bool(release_manifest.get("exists")),
            "release_version_name": release_version,
            "release_version_code": str(release_manifest.get("version_code") or ""),
            "update_available": update_available,
            "promotion_status": promotion_gate.get("status"),
            "serving_allowed": bool(promotion_gate.get("serving_allowed")),
        },
        "promotion_gate": promotion_gate,
        "architecture": {
            "controller_roles": ["desktop_primary_controller", "ios_mobile_controller"],
            "worker_role": "android_control_worker",
            "boundary": "Android executes approved device operations; planning, model routing, and promotion decisions stay in controller/runtime services.",
        },
    }


def _pc_tailscale_ip() -> str:
    if _device_probes_disabled():
        return ""
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5, check=False, **_hidden_run_kwargs())
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").splitlines()[0].strip() if result.stdout else ""


def _http_health(url: str) -> dict[str, Any]:
    if _device_probes_disabled():
        return {"ok": False, "status": 0, "url": url, "error": "probes_disabled"}
    try:
        with request.urlopen(url, timeout=2) as response:
            payload = {}
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                payload = {}
            return {"ok": 200 <= response.status < 300, "status": response.status, "url": url, **({"body": payload} if isinstance(payload, dict) else {})}
    except Exception as exc:
        return {"ok": False, "status": 0, "url": url, "error": type(exc).__name__}


def _http_json(url: str, *, timeout: int = 5) -> dict[str, Any]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {"ok": False, "error": "json response is not object"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _control_plane_snapshot(android_health: dict[str, Any]) -> dict[str, Any]:
    body = android_health.get("body")
    if isinstance(body, dict):
        state = body.get("state")
        if isinstance(state, dict):
            return state
    return {}


def _control_workspaces(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    raw = snapshot.get("workspaces")
    workspaces: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            workspace_id = str(item.get("workspace_id") or "").strip()
            if not workspace_id:
                continue
            workspaces.append(
                {
                    "workspace_id": workspace_id,
                    "name": str(item.get("name") or workspace_id),
                    "status": str(item.get("status") or "active"),
                }
            )
    if not workspaces:
        workspaces.append({"workspace_id": "local-ecommerce", "name": "Local Ecommerce Workspace", "status": "active"})
    return workspaces


def _control_accounts(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("accounts")
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return raw
    return {"schema_version": "spiritkin.control_plane.accounts.v1", "total": 0, "items": []}


def _control_workspace_devices(snapshot: dict[str, Any], workspaces: list[dict[str, str]]) -> dict[str, Any]:
    raw = snapshot.get("workspace_devices")
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return raw
    items: list[dict[str, Any]] = []
    for workspace in workspaces:
        workspace_id = str(workspace.get("workspace_id") or "local-ecommerce")
        items.append(
            {
                "workspace_id": workspace_id,
                "name": str(workspace.get("name") or workspace_id),
                "status": str(workspace.get("status") or "active"),
                "counts": {
                    "android": 0,
                    "ios_controllers": 0,
                    "remote_workers": 0,
                    "active_bindings": 0,
                    "pending_pairings": 0,
                },
                "android_devices": [],
                "ios_controllers": [],
                "remote_workers": [],
                "active_bindings": [],
                "pending_pairings": [],
                "last_seen_at": "",
            }
        )
    return {
        "count": len(items),
        "total_counts": {
            "android": 0,
            "ios_controllers": 0,
            "remote_workers": 0,
            "active_bindings": 0,
            "pending_pairings": 0,
        },
        "items": items,
    }


def _run_process(command: list[str], *, cwd: Path | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False, **_hidden_run_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "status": "process_failed", "message": f"{type(exc).__name__}: {exc}", "output": ""}
    output = ((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")).strip()
    return {"ok": result.returncode == 0, "status": "ok" if result.returncode == 0 else "failed", "exit_code": result.returncode, "message": output.splitlines()[0] if output else "", "output": output[-4000:]}


def _regex(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _hidden_run_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
