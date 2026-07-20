from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT_DIR / "frontend" / "models" / "manifest.json"


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "JSON root must be an object"
    return data, ""


def _frontend_root(manifest_path: Path) -> Path:
    return manifest_path.parent.parent if manifest_path.parent.name == "models" else manifest_path.parent


def _resource_path(frontend_root: Path, url: str) -> Path | None:
    if not url or "://" in url or url.startswith("data:"):
        return None
    return (frontend_root / url).resolve()


def _check_model3(model_path: Path, role: str, config: dict[str, Any], warnings: list[str], errors: list[str]) -> None:
    data, error = _load_json(model_path)
    if data is None:
        errors.append(f"{role}: invalid model3 json: {error}")
        return
    refs = data.get("FileReferences") or {}
    base = model_path.parent
    moc = refs.get("Moc")
    if moc and not (base / str(moc)).exists():
        errors.append(f"{role}: missing Moc file {moc}")
    for texture in refs.get("Textures") or []:
        if not (base / str(texture)).exists():
            errors.append(f"{role}: missing texture {texture}")
    expression_names = {str(item.get("Name")) for item in refs.get("Expressions") or [] if isinstance(item, dict)}
    mapped_expressions = set((config.get("expressions") or {}).values())
    missing_expr = sorted(name for name in mapped_expressions if expression_names and name not in expression_names)
    if missing_expr:
        warnings.append(f"{role}: expression mappings not found in model3: {', '.join(missing_expr)}")
    motion_groups = set((refs.get("Motions") or {}).keys())
    mapped_motions = set((config.get("motions") or {}).values())
    missing_motion = sorted(name for name in mapped_motions if motion_groups and name not in motion_groups)
    if missing_motion:
        warnings.append(f"{role}: motion mappings not found in model3: {', '.join(missing_motion)}")


def validate_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    warnings: list[str] = []
    errors: list[str] = []
    data, error = _load_json(manifest_path)
    if data is None:
        return {"ok": False, "warnings": warnings, "errors": [error], "roles": []}
    roles = data.get("roles") or {}
    if not isinstance(roles, dict) or not roles:
        errors.append("manifest.roles must contain at least one role")
        return {"ok": False, "warnings": warnings, "errors": errors, "roles": []}
    frontend_root = _frontend_root(manifest_path)
    checked_roles: list[str] = []
    for role, config in roles.items():
        checked_roles.append(str(role))
        if not isinstance(config, dict):
            errors.append(f"{role}: config must be an object")
            continue
        enabled = bool(config.get("enabled", True))
        model = str(config.get("model") or "")
        ready = bool(config.get("ready"))
        if not enabled:
            continue
        if not model:
            if ready:
                errors.append(f"{role}: ready role must define model")
            continue
        model_path = _resource_path(frontend_root, model)
        if model_path is None:
            if ready:
                warnings.append(f"{role}: model is remote; local resource check skipped")
            continue
        if not model_path.exists():
            message = f"{role}: model file not found: {model}"
            (errors if ready else warnings).append(message)
            continue
        _check_model3(model_path, str(role), config, warnings, errors)
    return {"ok": not errors, "warnings": warnings, "errors": errors, "roles": checked_roles}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frontend/models/manifest.json Live2D resources")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    report = validate_manifest(args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
