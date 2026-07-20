from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

ANDROID_PERMISSION_TIERS = ("read_only", "open_app", "clipboard", "screenshot", "automation", "high_risk")
DEFAULT_ALLOWED_PERMISSION_TIERS = ("read_only", "open_app", "clipboard", "screenshot", "automation")
DEFAULT_CONFIRMATION_TIERS = ("high_risk",)


@dataclass(frozen=True)
class AndroidCommandPermission:
    operation: str
    tier: str
    label: str
    risk_level: str
    read_only: bool = False
    requires_confirmation: bool = False
    reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "tier": self.tier,
            "label": self.label,
            "risk_level": self.risk_level,
            "read_only": self.read_only,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
        }


def classify_android_operation(operation: str) -> AndroidCommandPermission:
    op = _normalize_operation(operation)
    tier = _operation_tier(op)
    return AndroidCommandPermission(
        operation=op,
        tier=tier,
        label=_tier_label(tier),
        risk_level=_tier_risk(tier),
        read_only=tier == "read_only",
        requires_confirmation=tier in _confirmation_tiers(),
        reason=_tier_reason(tier),
    )


def build_android_permission_policy() -> dict[str, Any]:
    allowed = _allowed_tiers()
    confirmation = _confirmation_tiers()
    return {
        "schema_version": "spiritkin.android_command_permissions.v1",
        "tiers": [
            {
                "tier": tier,
                "label": _tier_label(tier),
                "risk_level": _tier_risk(tier),
                "allowed": tier in allowed,
                "requires_confirmation": tier in confirmation,
                "description": _tier_reason(tier),
            }
            for tier in ANDROID_PERMISSION_TIERS
        ],
        "allowed_tiers": list(allowed),
        "confirmation_tiers": list(confirmation),
        "env": {
            "allowed_tiers": "SPIRITKIN_ANDROID_ALLOWED_PERMISSION_TIERS",
            "confirmation_tiers": "SPIRITKIN_ANDROID_CONFIRMATION_PERMISSION_TIERS",
        },
        "default_allowed_tiers": list(DEFAULT_ALLOWED_PERMISSION_TIERS),
    }


def build_android_device_permission_posture(
    device_state: dict[str, Any] | None,
    *,
    installed_apps: list[Any] | None = None,
    capabilities: list[Any] | None = None,
    command_catalog: list[Any] | None = None,
) -> dict[str, Any]:
    state = dict(device_state or {})
    device_id = str(state.get("device_id") or "android_device")
    capability_set = _string_set(capabilities if capabilities is not None else state.get("capabilities") or [])
    catalog = _command_catalog(command_catalog if command_catalog is not None else state.get("command_catalog") or [], capability_set)
    installed_package_set = _installed_package_set(installed_apps if installed_apps is not None else state.get("installed_apps") or [])
    accessibility_granted = _truthy(state.get("pdd_accessibility_granted") or state.get("accessibility_granted") or state.get("accessibility_enabled"))
    accessibility_connected = _truthy(state.get("pdd_accessibility_connected") or state.get("accessibility_connected") or state.get("accessibility_active"))
    screenshot_authorized = _truthy(state.get("screen_capture_authorized") or state.get("screenshot_authorized") or state.get("screen_capture_granted"))
    allowed_tiers = set(_allowed_tiers())
    operations: list[dict[str, Any]] = []
    gap_map: dict[str, dict[str, Any]] = {}

    if not capability_set:
        _add_gap(gap_map, "android_capabilities_missing", "medium", "Android heartbeat did not report capabilities.", "Open the bridge app and sync commands.")
    if not catalog:
        _add_gap(gap_map, "android_command_catalog_missing", "medium", "Android heartbeat did not report a command catalog.", "Upgrade or resync the Android bridge APK.")

    for raw in catalog:
        operation = str(raw.get("operation") or "").strip()
        if not operation:
            continue
        permission = classify_android_operation(operation).snapshot()
        required_capabilities = [str(item).strip() for item in raw.get("required_capabilities") or [] if str(item).strip()]
        required_packages = [str(item).strip() for item in raw.get("required_packages") or [] if str(item).strip()]
        blockers: list[dict[str, str]] = []
        missing_capabilities = [item for item in required_capabilities if item not in capability_set]
        missing_packages = [item for item in required_packages if item.lower() not in installed_package_set]
        if permission["tier"] not in allowed_tiers:
            blocker = {
                "id": "android_permission_tier_blocked",
                "severity": "high",
                "message": f"Operation tier is blocked by policy: {permission['tier']}",
            }
            blockers.append(blocker)
            _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Adjust SPIRITKIN_ANDROID_ALLOWED_PERMISSION_TIERS only after review.")
        if missing_capabilities:
            blocker = {
                "id": "android_required_capability_missing",
                "severity": "medium",
                "message": "Missing capabilities: " + ", ".join(missing_capabilities[:4]),
            }
            blockers.append(blocker)
            _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Upgrade or resync the Android bridge APK.")
        if missing_packages:
            blocker = {
                "id": "android_required_package_missing",
                "severity": "medium",
                "message": "Missing Android packages: " + ", ".join(missing_packages[:4]),
            }
            blockers.append(blocker)
            _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Install the required app on the controlled Android device.")
        if bool(raw.get("requires_accessibility")):
            if not accessibility_granted:
                blocker = {
                    "id": "android_accessibility_required",
                    "severity": "high",
                    "message": "Accessibility permission is required but not granted.",
                }
                blockers.append(blocker)
                _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Open Android accessibility settings and enable SpiritKin Bridge.")
            elif not accessibility_connected:
                blocker = {
                    "id": "android_accessibility_inactive",
                    "severity": "medium",
                    "message": "Accessibility permission is granted but the service is not connected.",
                }
                blockers.append(blocker)
                _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Open the bridge app or restart the accessibility service.")
        if operation in {"android.screenshot.capture", "screenshot.capture", "screen.capture"} and not screenshot_authorized:
            blocker = {
                "id": "android_screenshot_permission_missing",
                "severity": "medium",
                "message": "Screenshot capture permission has not been granted.",
            }
            blockers.append(blocker)
            _add_gap(gap_map, blocker["id"], blocker["severity"], blocker["message"], "Run android.screenshot.request_permission from the bridge app.")
        operations.append(
            {
                "operation": operation,
                "permission": permission,
                "risk": str(raw.get("risk") or permission.get("risk_level") or "unknown"),
                "required_capabilities": required_capabilities,
                "required_packages": required_packages,
                "requires_accessibility": bool(raw.get("requires_accessibility")),
                "requires_artifact": bool(raw.get("requires_artifact")),
                "available": not blockers,
                "blockers": blockers,
            }
        )

    available_count = sum(1 for item in operations if item.get("available"))
    gaps = sorted(gap_map.values(), key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(str(item.get("severity")), 3))
    if not operations:
        status = "unknown"
    elif available_count == 0:
        status = "blocked"
    elif gaps:
        status = "partial"
    else:
        status = "ready"
    return {
        "schema_version": "spiritkin.android_device_permission_posture.v1",
        "device_id": device_id,
        "status": status,
        "operation_count": len(operations),
        "available_operation_count": available_count,
        "blocked_operation_count": max(0, len(operations) - available_count),
        "capability_count": len(capability_set),
        "accessibility": {
            "granted": accessibility_granted,
            "connected": accessibility_connected,
        },
        "screenshot": {
            "authorized": screenshot_authorized,
        },
        "operations": operations,
        "gaps": gaps,
        "gap_count": len(gaps),
        "next_actions": _android_permission_next_actions(gaps),
    }


def enforce_android_command_permission(operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(params or {})
    permission = classify_android_operation(operation)
    allowed_tiers = _allowed_tiers()
    if permission.tier not in allowed_tiers:
        return {
            "allowed": False,
            "error_code": "android_permission_tier_blocked",
            "message": f"Android command tier {permission.tier} is not allowed: {permission.operation}",
            "permission": permission.snapshot(),
            "policy": build_android_permission_policy(),
        }
    if permission.requires_confirmation and not _confirmed(payload):
        return {
            "allowed": False,
            "error_code": "android_high_risk_confirmation_required",
            "message": f"Android command requires explicit confirmation: {permission.operation}",
            "permission": permission.snapshot(),
            "policy": build_android_permission_policy(),
        }
    return {"allowed": True, "permission": permission.snapshot(), "policy": build_android_permission_policy()}


def _operation_tier(operation: str) -> str:
    if operation in {
        "device.status",
        "device_status",
        "status",
        "list_installed_apps",
        "apps.list",
        "app.list",
        "foreground_app",
        "app.current",
        "current_app",
        "artifact.cache.status",
        "workflow.android_step.status",
        "workflow.command_result",
    }:
        return "read_only"
    if operation in {"app.launch", "launch_app", "app.close", "close_app", "url.open", "open_url", "pdd.launch", "android.open_accessibility_settings", "android.open_bridge"}:
        return "open_app"
    if operation in {"clipboard.read", "clipboard_read", "clipboard.write", "clipboard_write"}:
        return "clipboard"
    if operation in {"screenshot.capture", "screen.capture", "screen_capture", "screenshot", "android.screenshot", "android.screenshot.capture", "android.screenshot.request_permission"}:
        return "screenshot"
    if operation in {"pdd.create_listing", "adb.shell", "adb.shell.rm", "package.install", "package.uninstall", "app.uninstall"}:
        return "high_risk"
    if operation.startswith(("accessibility.", "ui.", "tap.", "input.", "swipe.", "key.")) or operation in {
        "tap",
        "click",
        "click_pointer",
        "enter_text",
        "press_keys",
        "automation.run",
        "pdd.flow.run",
        "android.ui_snapshot",
        "artifact.download",
        "artifact.cache.cleanup",
        "image.share_to_app",
        "pdd.share_image",
        "workflow.android_step",
    }:
        return "automation"
    return "high_risk"


def _normalize_operation(operation: str) -> str:
    return str(operation or "").strip().lower()


def _allowed_tiers() -> tuple[str, ...]:
    return _tier_env("SPIRITKIN_ANDROID_ALLOWED_PERMISSION_TIERS", DEFAULT_ALLOWED_PERMISSION_TIERS)


def _confirmation_tiers() -> tuple[str, ...]:
    return _tier_env("SPIRITKIN_ANDROID_CONFIRMATION_PERMISSION_TIERS", DEFAULT_CONFIRMATION_TIERS)


def _tier_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    tiers = tuple(item.strip().lower() for item in raw.replace(";", ",").split(",") if item.strip())
    return tuple(item for item in tiers if item in ANDROID_PERMISSION_TIERS) or default


def _confirmed(params: dict[str, Any]) -> bool:
    value = params.get("confirmed_high_risk", params.get("confirm_high_risk", params.get("permission_confirmed")))
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "confirmed", "allow_high_risk"}


def _tier_label(tier: str) -> str:
    return {
        "read_only": "只读",
        "open_app": "打开 App / URL",
        "clipboard": "剪贴板",
        "screenshot": "截图",
        "automation": "自动化",
        "high_risk": "高风险操作",
    }.get(tier, tier)


def _tier_risk(tier: str) -> str:
    return {
        "read_only": "low",
        "open_app": "low",
        "clipboard": "medium",
        "screenshot": "medium",
        "automation": "medium",
        "high_risk": "high",
    }.get(tier, "high")


def _tier_reason(tier: str) -> str:
    return {
        "read_only": "只读取设备、应用或前台状态，不改变手机状态。",
        "open_app": "启动/关闭 App 或打开 URL，影响前台应用但不直接输入内容。",
        "clipboard": "读取或写入手机剪贴板，可能包含敏感内容。",
        "screenshot": "请求手机截图，可能包含隐私信息，需要产物审计。",
        "automation": "通过 Accessibility/输入动作操作 UI，必须依赖观察和失败回传。",
        "high_risk": "ADB shell、安装/卸载、支付、删除、权限变更等操作默认阻止。",
    }.get(tier, "未识别操作按高风险处理。")


def _command_catalog(raw: Any, capability_set: set[str]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        catalog = [dict(item) for item in raw if isinstance(item, dict)]
        if catalog:
            return catalog
    return [{"operation": capability, "required_capabilities": [capability]} for capability in sorted(capability_set) if "." in capability]


def _installed_package_set(raw: Any) -> set[str]:
    packages: set[str] = set()
    if not isinstance(raw, list):
        return packages
    for item in raw:
        if isinstance(item, dict):
            for key in ("package", "package_name", "id", "name"):
                value = str(item.get(key) or "").strip().lower()
                if value:
                    packages.add(value)
        else:
            value = str(item or "").strip().lower()
            if value:
                packages.add(value)
    return packages


def _string_set(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "granted", "ready", "active", "authorized"}


def _add_gap(gaps: dict[str, dict[str, Any]], gap_id: str, severity: str, message: str, next_action: str) -> None:
    if gap_id in gaps:
        return
    gaps[gap_id] = {
        "id": gap_id,
        "severity": severity,
        "message": message,
        "next_action": next_action,
    }


def _android_permission_next_actions(gaps: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    gap_ids = {str(gap.get("id") or "") for gap in gaps}
    if "android_accessibility_required" in gap_ids or "android_accessibility_inactive" in gap_ids:
        actions.append({"operation": "android.open_accessibility_settings", "label": "打开无障碍设置", "reason": "Accessibility is required for UI snapshot or automation commands."})
    if "android_screenshot_permission_missing" in gap_ids:
        actions.append({"operation": "android.screenshot.request_permission", "label": "申请截图权限", "reason": "Screenshot capture requires explicit Android MediaProjection consent."})
    if "android_command_catalog_missing" in gap_ids or "android_required_capability_missing" in gap_ids:
        actions.append({"operation": "android.open_bridge", "label": "打开 Bridge 并同步", "reason": "The controlled device should refresh capabilities and command catalog."})
    return actions
