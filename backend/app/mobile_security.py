from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

TAILSCALE_IPV4_RANGE = ipaddress.ip_network("100.64.0.0/10")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def build_mobile_security_snapshot(*, pc_tailscale_ip: str, android_receiver_url: str, android_pairing_url: str, ios_base_url: str) -> dict[str, Any]:
    # The primary 8791 receiver uses the management token and explicit pairing
    # flags. Keep the diagnostic surface aligned with those real controls.
    management_token = os.getenv("SPIRITKIN_MANAGEMENT_TOKEN", "").strip()
    mobile_token = os.getenv("SPIRITKIN_MOBILE_TOKEN", "").strip() or management_token
    android_token = os.getenv("SPIRITKIN_ANDROID_TOKEN", "").strip()
    ios_token = os.getenv("SPIRITKIN_IOS_TOKEN", "").strip()
    production_mode = os.getenv("SPIRITKIN_PRODUCTION_MODE", "").strip().lower() in {"1", "true", "yes", "on", "production"}
    pairing_required = os.getenv("SPIRITKIN_REQUIRE_PAIRING_TOKEN", "").strip().lower() in {"1", "true", "yes", "on", "required", "production"} or production_mode
    endpoints = [
        {"id": "android_receiver", "label": "Android Receiver", **_url_security_profile(android_receiver_url)},
        {"id": "android_pairing", "label": "Android Pairing", **_url_security_profile(android_pairing_url)},
        {"id": "ios_terminal", "label": "iOS Terminal", **_url_security_profile(ios_base_url)},
    ]
    scopes = {endpoint["scope"] for endpoint in endpoints}
    network_scope = _network_scope_label(scopes)
    warnings: list[dict[str, str]] = []

    if not pc_tailscale_ip.strip():
        warnings.append(
            _security_warning(
                "tailscale_ip_missing",
                "medium",
                "未发现 PC Tailscale IP",
                "手机端地址会回落到 127.0.0.1，只适合本机调试；真机建议先启用 Tailscale 或配置受控 HTTPS/LAN 入口。",
            )
        )

    for endpoint in endpoints:
        scope = str(endpoint["scope"])
        if not endpoint["encrypted"] and scope in {"public", "dns", "network"}:
            warnings.append(
                _security_warning(
                    f"{endpoint['id']}_public_http",
                    "high",
                    f"{endpoint['label']} 正在使用公网/域名 HTTP",
                    "跨公网使用时应放到 HTTPS/WSS 反向代理后，或改走 Tailscale 私有地址。",
                )
            )
        elif not endpoint["encrypted"] and scope == "private_lan":
            warnings.append(
                _security_warning(
                    f"{endpoint['id']}_lan_http",
                    "medium",
                    f"{endpoint['label']} 正在使用局域网 HTTP",
                    "局域网可用于内测，但应配合 endpoint token；外网访问必须升级到 HTTPS/WSS。",
                )
            )

    if any(endpoint["scope"] != "local" for endpoint in endpoints):
        if not management_token:
            warnings.append(
                _security_warning(
                    "command_gateway_token_missing",
                    "medium",
                    "命令网关未配置移动访问 token",
                    "主控服务应配置 SPIRITKIN_MANAGEMENT_TOKEN；未配置时只允许受信任的本机来源。",
                )
            )
        if not pairing_required:
            warnings.append(
                _security_warning(
                    "android_endpoint_token_missing",
                    "medium",
                    "Android endpoint 未配置固定 token",
                    "当前未强制配对令牌；真机或非本机网络使用时应设置 SPIRITKIN_REQUIRE_PAIRING_TOKEN=true。",
                )
            )
        if not ios_token and not management_token:
            warnings.append(
                _security_warning(
                    "ios_endpoint_token_missing",
                    "medium",
                    "iOS endpoint 未配置 token",
                    "原生 iOS endpoint 未配置独立令牌；建议通过 8791 配对下发 iOS terminal binding。",
                )
            )

    severity_order = {"high": 3, "medium": 2, "low": 1}
    max_severity = max((severity_order.get(item["severity"], 0) for item in warnings), default=0)
    return {
        "status": "needs_attention" if max_severity >= 3 else "review" if max_severity else "ready",
        "network_scope": network_scope,
        "pc_tailscale_ip": pc_tailscale_ip,
        "https_required_for_public": True,
        "tokens": {
            "command_gateway": bool(management_token),
            "android_endpoint": bool(android_token),
            "ios_endpoint": bool(ios_token),
        },
        "endpoints": endpoints,
        "warnings": warnings,
        "operator_hint": _security_operator_hint(network_scope, warnings),
    }


def _url_security_profile(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = (parsed.scheme or "http").lower()
    scope = _host_scope(host)
    return {
        "url": url,
        "scheme": scheme,
        "host": host,
        "scope": scope,
        "encrypted": scheme in {"https", "wss"},
    }


def _host_scope(host: str) -> str:
    value = host.strip().strip("[]").lower()
    if value in LOCAL_HOSTS:
        return "local"
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return "dns" if value else "unknown"
    if ip.is_loopback:
        return "local"
    if ip.version == 4 and ip in TAILSCALE_IPV4_RANGE:
        return "tailscale"
    if ip.is_private:
        return "private_lan"
    if ip.is_global:
        return "public"
    return "network"


def _network_scope_label(scopes: set[str]) -> str:
    if not scopes or scopes <= {"local"}:
        return "local_only"
    if "public" in scopes or "dns" in scopes or "network" in scopes:
        return "public_or_unknown"
    if "private_lan" in scopes:
        return "private_lan"
    if "tailscale" in scopes:
        return "tailscale"
    return "mixed"


def _security_warning(warning_id: str, severity: str, title: str, detail: str) -> dict[str, str]:
    return {"warning_id": warning_id, "severity": severity, "title": title, "detail": detail}


def _security_operator_hint(network_scope: str, warnings: list[dict[str, str]]) -> str:
    if any(item.get("severity") == "high" for item in warnings):
        return "不要把当前 HTTP 手机入口直接暴露到公网；先加 HTTPS/WSS 反向代理或改用 Tailscale。"
    if network_scope == "tailscale":
        return "当前更适合 Tailscale 私有网络内使用；仍建议给 Android/iOS endpoint 配置 token。"
    if network_scope == "local_only":
        return "当前地址仅适合本机调试；真机访问需要 Tailscale、局域网地址或 HTTPS 入口。"
    return "用于真机或多人访问前，请确认 token、网络范围和 HTTPS/Tailscale 策略。"
