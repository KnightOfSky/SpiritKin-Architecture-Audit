from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "spiritkin.growth_sandbox_bundle.v1"
MAX_FILE_COUNT = 40
MAX_TOTAL_BYTES = 256 * 1024
MAX_FILE_BYTES = 128 * 1024
MAX_COMMAND_ARGS = 24
MAX_COMMAND_ARG_LENGTH = 240
ALLOWED_KINDS = {"skill", "tool", "code"}
SECRET_LITERAL_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)\b\s*[:=]\s*"
    r"[\"'](?!placeholder|example|dummy|test|your[-_])[A-Za-z0-9_./+=-]{8,}[\"']"
)


def _safe_id(value: str, fallback: str = "bundle") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return normalized[:96] or fallback


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="strict")).hexdigest()


def _relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or len(raw) > 160
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part or "\x00" in part for part in path.parts)
    ):
        raise ValueError("sandbox bundle file path must be a safe relative path")
    return path.as_posix()


def _text_file(item: Any) -> tuple[str, str, bytes]:
    if not isinstance(item, dict):
        raise ValueError("sandbox bundle files must be objects")
    relative = _relative_path(item.get("path"))
    content = item.get("content")
    if not isinstance(content, str):
        raise ValueError("sandbox bundle files must contain UTF-8 text")
    if "\x00" in content:
        raise ValueError("sandbox bundle files cannot contain NUL bytes")
    if SECRET_LITERAL_PATTERN.search(content):
        raise ValueError("sandbox bundle files must not contain literal credentials or secrets")
    encoded = content.encode("utf-8", errors="strict")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"sandbox bundle files are limited to {MAX_FILE_BYTES} bytes each")
    return relative, content, encoded


def _command(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > MAX_COMMAND_ARGS:
        raise ValueError(f"sandbox command must contain 1 to {MAX_COMMAND_ARGS} argv items")
    command: list[str] = []
    for raw in value:
        argument = str(raw or "")
        if not argument or len(argument) > MAX_COMMAND_ARG_LENGTH or "\x00" in argument or "\n" in argument or "\r" in argument:
            raise ValueError("sandbox command arguments must be bounded single-line strings")
        if SECRET_LITERAL_PATTERN.search(argument):
            raise ValueError("sandbox command must not contain literal credentials or secrets")
        command.append(argument)
    return command


class GrowthSandboxBundleStore:
    """Stores immutable candidate text bundles for later container-only execution."""

    def __init__(self, artifact_root: str | os.PathLike[str]) -> None:
        artifact_root_path = Path(artifact_root).resolve()
        self.root = (artifact_root_path.parent / "sandboxes").resolve()

    def prepare(
        self,
        candidate: dict[str, Any],
        artifact: dict[str, Any],
        payload: dict[str, Any],
        *,
        prepared_by: str,
    ) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        kind = str(candidate.get("kind") or "").strip().lower()
        if not candidate_id or not artifact_id or not prepared_by:
            raise ValueError("candidate_id, artifact_id and prepared_by are required")
        if kind not in ALLOWED_KINDS:
            raise PermissionError("sandbox bundles are supported only for Skill, Tool and Code candidates")
        if str(artifact.get("candidate_id") or "") != candidate_id:
            raise PermissionError("Builder artifact belongs to another candidate")
        if str(artifact.get("workspace_id") or "") != str(candidate.get("workspace_id") or ""):
            raise PermissionError("Builder artifact belongs to another workspace")

        specification = payload.get("sandbox_bundle") if isinstance(payload.get("sandbox_bundle"), dict) else payload
        raw_files = specification.get("files")
        if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_FILE_COUNT:
            raise ValueError(f"sandbox bundle requires 1 to {MAX_FILE_COUNT} text files")
        files: list[tuple[str, str, bytes]] = []
        seen: set[str] = set()
        total_bytes = 0
        for raw_file in raw_files:
            relative, content, encoded = _text_file(raw_file)
            key = relative.casefold()
            if key in seen:
                raise ValueError("sandbox bundle file paths must be unique")
            seen.add(key)
            total_bytes += len(encoded)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError(f"sandbox bundle is limited to {MAX_TOTAL_BYTES} bytes")
            files.append((relative, content, encoded))

        command = _command(specification.get("command"))
        timeout_seconds = max(1, min(30, int(specification.get("timeout_seconds") or 15)))
        expected_raw = specification.get("expected_exit_codes") or [0]
        if not isinstance(expected_raw, (list, tuple)) or not expected_raw or len(expected_raw) > 8:
            raise ValueError("expected_exit_codes must be a non-empty bounded list")
        expected_exit_codes: list[int] = []
        for raw_exit_code in expected_raw:
            try:
                exit_code = int(raw_exit_code)
            except (TypeError, ValueError) as exc:
                raise ValueError("expected_exit_codes must contain integers") from exc
            if not -255 <= exit_code <= 255:
                raise ValueError("expected_exit_codes must stay between -255 and 255")
            if exit_code not in expected_exit_codes:
                expected_exit_codes.append(exit_code)
        expected_exit_codes.sort()
        if not expected_exit_codes:
            raise ValueError("expected_exit_codes did not contain a valid exit code")

        file_entries = [
            {"path": relative, "size_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
            for relative, _content, encoded in files
        ]
        identity = {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "files": file_entries,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "expected_exit_codes": expected_exit_codes,
        }
        bundle_id = f"bundle-{_digest(identity)[:16]}"
        candidate_root = (self.root / _safe_id(candidate_id, "candidate") / "bundles").resolve()
        bundle_root = (candidate_root / bundle_id).resolve()
        if not bundle_root.is_relative_to(self.root):
            raise PermissionError("unsafe sandbox bundle path")
        manifest_path = bundle_root / "manifest.json"
        if manifest_path.exists():
            existing = self.load(candidate_id, bundle_id)
            if str(existing.get("integrity", {}).get("digest") or "") != _digest(
                {key: value for key, value in existing.items() if key not in {"path", "integrity"}}
            ):
                raise PermissionError("existing sandbox bundle integrity check failed")
            return existing

        candidate_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix="bundle-stage-", dir=candidate_root)).resolve()
        try:
            files_root = temporary_root / "files"
            files_root.mkdir()
            for relative, content, _encoded in files:
                target = (files_root / Path(relative)).resolve()
                if not target.is_relative_to(files_root):
                    raise PermissionError("sandbox bundle file escaped the managed root")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "bundle_id": bundle_id,
                "candidate_id": candidate_id,
                "artifact_id": artifact_id,
                "workspace_id": str(candidate.get("workspace_id") or ""),
                "kind": kind,
                "status": "prepared",
                "prepared_by": prepared_by[:200],
                "created_at": time.time(),
                "files": file_entries,
                "file_count": len(file_entries),
                "total_bytes": total_bytes,
                "command": command,
                "timeout_seconds": timeout_seconds,
                "expected_exit_codes": expected_exit_codes,
                "policy": {
                    "text_files_only": True,
                    "credentials_allowed": False,
                    "host_execution_allowed": False,
                    "container_execution_requires_explicit_gate": True,
                    "candidate_stage_advanced": False,
                    "activation_enabled": False,
                },
                "integrity": {},
            }
            manifest["integrity"] = {
                "algorithm": "sha256",
                "digest": _digest({key: value for key, value in manifest.items() if key != "integrity"}),
            }
            (temporary_root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temporary_root.replace(bundle_root)
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise
        return {**manifest, "path": str(manifest_path)}

    def load(self, candidate_id: str, bundle_id: str) -> dict[str, Any]:
        bundle_root = (self.root / _safe_id(candidate_id, "candidate") / "bundles" / _safe_id(bundle_id)).resolve()
        if not bundle_root.is_relative_to(self.root):
            raise PermissionError("unsafe sandbox bundle path")
        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("sandbox bundle not found")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("sandbox bundle manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise ValueError("sandbox bundle manifest is invalid")
        return {**manifest, "path": str(manifest_path)}

    def verify_files(self, manifest: dict[str, Any]) -> Path:
        manifest_path = Path(str(manifest.get("path") or "")).resolve()
        bundle_root = manifest_path.parent
        files_root = (bundle_root / "files").resolve()
        if not files_root.is_relative_to(self.root) or not files_root.exists():
            raise PermissionError("sandbox bundle files escaped the managed root")
        unsigned = {key: value for key, value in manifest.items() if key not in {"path", "integrity"}}
        integrity = dict(manifest.get("integrity") or {})
        if integrity.get("algorithm") != "sha256" or integrity.get("digest") != _digest(unsigned):
            raise PermissionError("sandbox bundle manifest integrity check failed")
        expected_paths: set[str] = set()
        for item in manifest.get("files") or []:
            relative = _relative_path(dict(item).get("path"))
            expected_paths.add(relative)
            target = (files_root / Path(relative)).resolve()
            if not target.is_relative_to(files_root) or not target.is_file() or target.is_symlink():
                raise PermissionError("sandbox bundle contains an unsafe file")
            encoded = target.read_bytes()
            if len(encoded) != int(dict(item).get("size_bytes") or -1):
                raise PermissionError("sandbox bundle file size check failed")
            if hashlib.sha256(encoded).hexdigest() != str(dict(item).get("sha256") or ""):
                raise PermissionError("sandbox bundle file digest check failed")
        actual_paths = {
            path.relative_to(files_root).as_posix()
            for path in files_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if actual_paths != expected_paths:
            raise PermissionError("sandbox bundle contains untracked files")
        return files_root

    @staticmethod
    def summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "bundle_id": str(manifest.get("bundle_id") or ""),
            "status": str(manifest.get("status") or "prepared"),
            "file_count": int(manifest.get("file_count") or 0),
            "total_bytes": int(manifest.get("total_bytes") or 0),
            "command": [str(item) for item in manifest.get("command") or []],
            "timeout_seconds": int(manifest.get("timeout_seconds") or 0),
            "integrity_digest": str(dict(manifest.get("integrity") or {}).get("digest") or ""),
            "host_execution_allowed": False,
            "activation_enabled": False,
        }
