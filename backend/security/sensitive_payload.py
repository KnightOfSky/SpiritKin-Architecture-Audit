from __future__ import annotations

from typing import Any

DEFAULT_ALLOWED_ROOT_SECRET_KEYS = {"token", "pairing_token"}

_EXACT_SENSITIVE_KEYS = {
    "auth",
    "authorization",
    "auth_header",
    "browser_profile",
    "browser_profile_path",
    "browser_user_data_dir",
    "chrome_profile",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "local_profile",
    "passwd",
    "password",
    "profile_path",
    "secret",
    "session",
    "session_cookie",
    "session_key",
    "session_secret",
    "session_token",
    "user_data_dir",
}

_SENSITIVE_FRAGMENTS = (
    "cookie",
    "credential",
    "password",
    "passwd",
    "secret",
)

_PROFILE_FRAGMENTS = (
    "browser_profile",
    "chrome_profile",
    "profile_path",
    "user_data_dir",
)


class SensitivePayloadError(ValueError):
    """Raised when untrusted device/worker payloads carry local credentials."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__("sensitive payload keys are not allowed: " + ", ".join(paths[:8]))


def find_sensitive_payload_keys(value: Any, *, allowed_root_keys: set[str] | None = None) -> list[str]:
    allowed_roots = {_normalize_key(item) for item in (allowed_root_keys or set())}
    found: list[str] = []

    def walk(item: Any, path: tuple[str, ...]) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                next_path = (*path, key_text)
                normalized = _normalize_key(key_text)
                if _is_sensitive_key(normalized) and not (len(next_path) == 1 and normalized in allowed_roots):
                    found.append(".".join(next_path))
                walk(child, next_path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, (*path, f"[{index}]"))

    walk(value, ())
    return found


def assert_no_sensitive_payload(value: Any, *, allowed_root_keys: set[str] | None = None) -> None:
    paths = find_sensitive_payload_keys(value, allowed_root_keys=allowed_root_keys)
    if paths:
        raise SensitivePayloadError(paths)


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: str) -> bool:
    if not key:
        return False
    if key in {"token_id", "pairing_token_id"}:
        return False
    if key == "token" or (key.endswith("_token") and key != "pairing_token"):
        return True
    if key in _EXACT_SENSITIVE_KEYS:
        return True
    if any(fragment in key for fragment in _SENSITIVE_FRAGMENTS):
        return True
    return any(fragment in key for fragment in _PROFILE_FRAGMENTS)
