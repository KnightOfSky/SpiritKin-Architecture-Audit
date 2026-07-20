from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def env_flag(name: str, *, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = str(env.get(name, "")).strip().lower()
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    return default


def localhost_auth_bypass_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return env_flag("SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN", environ=environ) or env_flag(
        "SPIRITKIN_DEV_ALLOW_LOCALHOST_AUTH_BYPASS",
        environ=environ,
    )


def split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def header_host(headers: Any) -> str:
    return str(headers.get("Host") or "").split(":")[0].strip().lower()


def is_local_host(value: str) -> bool:
    return str(value or "").strip().lower() in LOCAL_HOSTS


def is_local_request(headers: Any, *, client_ip: str = "") -> bool:
    # The Host header is client-controlled (DNS rebinding / spoofing), so a known
    # client_ip takes precedence; Host is only a fallback when the socket peer is unknown.
    ip = str(client_ip or "").strip()
    if ip:
        return is_local_host(ip)
    return is_local_host(header_host(headers))


def constant_time_equals(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def token_matches(headers: Any, *, expected_token: str, header_name: str, allow_bearer: bool = True) -> bool:
    token = expected_token.strip()
    if not token:
        # An unset/empty expected token must never authorize; callers decide
        # separately whether a localhost bypass applies.
        return False
    provided = str(headers.get(header_name) or "").strip()
    if provided and constant_time_equals(provided, token):
        return True
    authorization = str(headers.get("Authorization") or "").strip()
    return bool(allow_bearer and authorization and constant_time_equals(authorization, f"Bearer {token}"))


def _origin_is_loopback(origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    return is_local_host(parsed.hostname or "")


def allowed_cors_origin(
    headers: Any,
    *,
    env_key: str,
    fallback_env_key: str = "SPIRITKIN_CORS_ALLOWED_ORIGINS",
    environ: Mapping[str, str] | None = None,
) -> str:
    origin = str(headers.get("Origin") or "").strip()
    if not origin:
        return ""
    env = os.environ if environ is None else environ
    if env_flag("SPIRITKIN_ALLOW_ANY_CORS_ORIGIN", environ=env):
        # "*" instead of reflecting the request Origin: browsers reject "*" when
        # credentials are involved, so this stays safe even if a credentialed
        # header is added later, while reflected origins would not.
        return "*"
    allowed = split_csv(env.get(env_key)) or split_csv(env.get(fallback_env_key))
    if allowed:
        return origin if origin in allowed else ""
    return origin if _origin_is_loopback(origin) else ""


def add_cors_headers(
    handler: Any,
    *,
    allowed_headers: str,
    methods: str = "GET, POST, OPTIONS",
    env_key: str,
) -> None:
    origin = allowed_cors_origin(handler.headers, env_key=env_key)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", allowed_headers)
    handler.send_header("Access-Control-Allow-Methods", methods)
