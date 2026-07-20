from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_APK_PROMOTION_PATH = "state/mobile/android-apk-promotion.json"


def resolve_apk_promotion_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_ANDROID_APK_PROMOTION_PATH", DEFAULT_APK_PROMOTION_PATH)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def build_apk_promotion_gate(
    *,
    apk_path: Path,
    release_manifest: dict[str, Any],
    approval_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    approval = _read_approval(approval_path)
    validation = _validate_apk_release(apk_path=apk_path, release_manifest=release_manifest)
    approved = (
        bool(approval.get("approved"))
        and str(approval.get("sha256") or "").lower() == str(validation.get("actual_sha256") or "").lower()
        and str(approval.get("version_code") or "") == str(validation.get("version_code") or "")
        and str(approval.get("package_name") or "") == str(validation.get("package_name") or "")
    )
    status = "approved" if approved and validation.get("ok") else "needs_approval"
    if not validation.get("ok"):
        status = "blocked"
    required_actions = []
    if not validation.get("ok"):
        required_actions.extend(validation.get("required_actions") or [])
    elif not approved:
        required_actions.append("approve_android_apk_release")
    return {
        "schema_version": "spiritkin.android_apk_promotion.v1",
        "status": status,
        "approved": approved and bool(validation.get("ok")),
        "serving_allowed": approved and bool(validation.get("ok")),
        "requires_human_approval": True,
        "validation": validation,
        "approval": approval,
        "required_actions": required_actions,
        "approval_path": str(resolve_apk_promotion_path(approval_path)),
    }


def approve_apk_release(
    *,
    apk_path: Path,
    release_manifest: dict[str, Any],
    reviewer: str,
    reason: str = "",
    approval_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    validation = _validate_apk_release(apk_path=apk_path, release_manifest=release_manifest)
    if not validation.get("ok"):
        return {
            "ok": False,
            "status": "blocked",
            "message": "Android APK release is not eligible for approval.",
            "validation": validation,
        }
    approval = {
        "approved": True,
        "approved_at": time.time(),
        "reviewer": reviewer or "desktop",
        "reason": reason,
        "package_name": validation.get("package_name"),
        "version_code": validation.get("version_code"),
        "version_name": validation.get("version_name"),
        "sha256": validation.get("actual_sha256"),
        "size_bytes": validation.get("actual_size_bytes"),
        "release_manifest_updated_at": release_manifest.get("updated_at") or release_manifest.get("created_at") or "",
    }
    path = resolve_apk_promotion_path(approval_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(approval, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "ok": True,
        "status": "approved",
        "message": f"Android APK release approved: {approval['version_name'] or approval['version_code']}",
        "approval": approval,
        "promotion_gate": build_apk_promotion_gate(apk_path=apk_path, release_manifest=release_manifest, approval_path=approval_path),
    }


def _validate_apk_release(*, apk_path: Path, release_manifest: dict[str, Any]) -> dict[str, Any]:
    required_actions: list[str] = []
    errors: list[str] = []
    package_name = str(release_manifest.get("package_name") or release_manifest.get("app_id") or "")
    version_code = str(release_manifest.get("version_code") or "")
    version_name = str(release_manifest.get("version_name") or "")
    expected_sha = str(
        release_manifest.get("sha256")
        or ((release_manifest.get("integrity") if isinstance(release_manifest.get("integrity"), dict) else {}) or {}).get("sha256")
        or ""
    ).lower()
    expected_size = _int_or_zero(
        release_manifest.get("size_bytes")
        or ((release_manifest.get("integrity") if isinstance(release_manifest.get("integrity"), dict) else {}) or {}).get("size_bytes")
    )
    if not apk_path.is_file():
        errors.append("apk file missing")
        required_actions.append("build_android_bridge")
        actual_sha = ""
        actual_size = 0
    else:
        body = apk_path.read_bytes()
        actual_sha = hashlib.sha256(body).hexdigest()
        actual_size = len(body)
    if not release_manifest:
        errors.append("release manifest missing")
        required_actions.append("build_release_manifest")
    if not package_name:
        errors.append("package_name missing")
    if not version_code:
        errors.append("version_code missing")
    if not version_name:
        errors.append("version_name missing")
    if expected_sha and actual_sha and expected_sha != actual_sha:
        errors.append("sha256 mismatch")
        required_actions.append("rebuild_or_refresh_release_manifest")
    if expected_size and actual_size and expected_size != actual_size:
        errors.append("size mismatch")
        required_actions.append("rebuild_or_refresh_release_manifest")
    return {
        "ok": not errors,
        "errors": errors,
        "required_actions": list(dict.fromkeys(required_actions)),
        "package_name": package_name,
        "version_code": version_code,
        "version_name": version_name,
        "expected_sha256": expected_sha,
        "actual_sha256": actual_sha,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
    }


def _read_approval(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    target = resolve_apk_promotion_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"approved": False}
    return payload if isinstance(payload, dict) else {"approved": False}


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
