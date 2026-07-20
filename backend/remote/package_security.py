from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any

REMOTE_PACKAGE_SIGNATURE_ALGORITHM = "hmac-sha256"
REMOTE_PACKAGE_RUNTIME_FIELDS = {
    "activation",
    "executed_at",
    "imported_at",
    "remote_node_id",
    "signature_verification",
    "source_package_path",
    "status",
    "verification",
}


def remote_package_canonical_json(package: Mapping[str, Any]) -> str:
    excluded = {"integrity", "signature", *REMOTE_PACKAGE_RUNTIME_FIELDS}
    payload = {key: value for key, value in dict(package).items() if key not in excluded}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def remote_package_sha256(package: Mapping[str, Any]) -> str:
    return hashlib.sha256(remote_package_canonical_json(package).encode("utf-8")).hexdigest()


def build_remote_package_signature(
    package: Mapping[str, Any],
    *,
    signer: str = "desktop",
    signing_key: str = "",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    key = _resolve_signing_key(signing_key=signing_key, environ=environ)
    if not key:
        return {
            "algorithm": "none",
            "signed": False,
            "verified": False,
            "signer": signer or "desktop",
            "message": "remote package signing key not configured",
        }
    canonical = remote_package_canonical_json(package)
    digest = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "algorithm": REMOTE_PACKAGE_SIGNATURE_ALGORITHM,
        "signed": True,
        "verified": True,
        "signer": signer or "desktop",
        "signed_at": time.time(),
        "key_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
        "payload_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "digest": digest,
    }


def verify_remote_package_signature(
    package: Mapping[str, Any],
    *,
    signing_key: str = "",
    require_signature: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    signature = dict(dict(package).get("signature") or {})
    algorithm = str(signature.get("algorithm") or "").strip().lower()
    signed = bool(signature.get("signed")) or algorithm == REMOTE_PACKAGE_SIGNATURE_ALGORITHM
    if not signed or algorithm in {"", "none"}:
        if require_signature:
            raise ValueError("remote package signature missing")
        return {
            "algorithm": algorithm or "none",
            "signed": False,
            "verified": False,
            "required": False,
        }
    if algorithm != REMOTE_PACKAGE_SIGNATURE_ALGORITHM:
        raise ValueError(f"unsupported remote package signature algorithm: {algorithm}")
    key = _resolve_signing_key(signing_key=signing_key, environ=environ)
    if not key:
        raise ValueError("remote package signing key not configured")
    expected = str(signature.get("digest") or "").strip().lower()
    if not expected:
        raise ValueError("remote package signature digest missing")
    canonical = remote_package_canonical_json(package)
    actual = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, actual):
        raise ValueError("remote package signature mismatch")
    payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    claimed_payload_sha256 = str(signature.get("payload_sha256") or "").strip().lower()
    if claimed_payload_sha256 and claimed_payload_sha256 != payload_sha256:
        raise ValueError("remote package signed payload hash mismatch")
    return {
        "algorithm": REMOTE_PACKAGE_SIGNATURE_ALGORITHM,
        "signed": True,
        "verified": True,
        "required": bool(require_signature),
        "key_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
        "payload_sha256": payload_sha256,
    }


def _resolve_signing_key(*, signing_key: str = "", environ: Mapping[str, str] | None = None) -> str:
    if signing_key:
        return signing_key.strip()
    source = environ if environ is not None else os.environ
    for name in ("SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY", "SPIRITKIN_REMOTE_WORKER_TOKEN", "SPIRITKIN_REMOTE_TOKEN"):
        value = str(source.get(name) or "").strip()
        if value:
            return value
    return ""
