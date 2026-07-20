from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
MODELS_DIR = FRONTEND_DIR / "models"
LIVE2D_MANIFEST_PATH = MODELS_DIR / "manifest.json"
AVATAR3D_MANIFEST_PATH = MODELS_DIR / "spirit3d" / "manifest.json"

LIVE2D_SUFFIXES = {".model3.json", ".moc3", ".json", ".png", ".jpg", ".jpeg", ".webp", ".motion3.json", ".exp3.json", ".physics3.json", ".pose3.json", ".userdata3.json"}
AVATAR3D_SUFFIXES = {".fbx", ".glb", ".gltf", ".vrm", ".bin", ".png", ".jpg", ".jpeg", ".webp", ".ktx2", ".json"}


@dataclass(frozen=True)
class AvatarAssetImportResult:
    asset_type: str
    role: str
    copied_files: int
    model_url: str
    manifest_path: str
    target_dir: str
    warnings: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "role": self.role,
            "copied_files": self.copied_files,
            "model_url": self.model_url,
            "manifest_path": self.manifest_path,
            "target_dir": self.target_dir,
            "warnings": list(self.warnings),
        }


def import_live2d_asset(
    source_path: str | Path,
    *,
    role: str = "spirit",
    display_name: str = "",
    frontend_dir: str | Path = FRONTEND_DIR,
) -> AvatarAssetImportResult:
    frontend = Path(frontend_dir).resolve()
    source = Path(source_path).resolve()
    role_id = _safe_asset_id(role)
    target = frontend / "models" / role_id
    files = _copy_asset_tree(source, target, allowed_suffixes=LIVE2D_SUFFIXES)
    model_file = _find_first(target, "*.model3.json")
    warnings: list[str] = []
    if model_file is None:
        raise ValueError("Live2D asset must contain a .model3.json file")

    manifest_path = frontend / "models" / "manifest.json"
    manifest = _read_json_object(manifest_path, default={"defaultRole": role_id, "roles": {}})
    roles = manifest.setdefault("roles", {})
    model_url = _as_frontend_url(frontend, model_file)
    roles[role_id] = {
        **dict(roles.get(role_id) or {}),
        "ready": True,
        "name": display_name or role_id,
        "model": model_url,
        "sprite_fallback": "spirit_avatar.html",
        "scale": float((roles.get(role_id) or {}).get("scale") or 0.22),
        "expressions": dict((roles.get(role_id) or {}).get("expressions") or _default_live2d_expressions()),
        "motions": dict((roles.get(role_id) or {}).get("motions") or _default_live2d_motions()),
    }
    manifest["defaultRole"] = role_id
    _write_json_object(manifest_path, manifest)
    return AvatarAssetImportResult("live2d", role_id, files, model_url, str(manifest_path), str(target), warnings)


def import_avatar3d_asset(
    source_path: str | Path,
    *,
    role: str = "spirit3d",
    display_name: str = "",
    frontend_dir: str | Path = FRONTEND_DIR,
) -> AvatarAssetImportResult:
    frontend = Path(frontend_dir).resolve()
    source = Path(source_path).resolve()
    role_id = _safe_asset_id(role)
    target = frontend / "models" / role_id
    files = _copy_asset_tree(source, target, allowed_suffixes=AVATAR3D_SUFFIXES)
    model_file = _find_first_with_suffix(target, (".vrm", ".glb", ".gltf", ".fbx"))
    if model_file is None:
        raise ValueError("3D avatar asset must contain .vrm, .glb, .gltf, or .fbx")

    manifest_path = target / "manifest.json"
    model_url = _as_frontend_url(frontend, model_file)
    manifest = _default_avatar3d_manifest(model_url)
    manifest["name"] = display_name or role_id
    _write_json_object(manifest_path, manifest)

    default_manifest_path = frontend / "models" / "spirit3d" / "manifest.json"
    default_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_object(default_manifest_path, manifest)
    return AvatarAssetImportResult("avatar3d", role_id, files, model_url, str(manifest_path), str(target), [])


def _copy_asset_tree(source: Path, target: Path, *, allowed_suffixes: set[str]) -> int:
    if not source.exists():
        raise FileNotFoundError(str(source))
    target.mkdir(parents=True, exist_ok=True)
    candidates = [source] if source.is_file() else [item for item in source.rglob("*") if item.is_file()]
    copied = 0
    for item in candidates:
        if not _is_allowed_asset_file(item, allowed_suffixes):
            continue
        relative = item.name if source.is_file() else str(item.relative_to(source))
        destination = (target / relative).resolve()
        if target not in destination.parents and destination != target:
            raise ValueError("asset path escapes target directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1
    if copied == 0:
        raise ValueError("no supported avatar asset files found")
    return copied


def _is_allowed_asset_file(path: Path, allowed_suffixes: set[str]) -> bool:
    name = path.name.lower()
    if name.endswith(".model3.json") or name.endswith(".motion3.json") or name.endswith(".exp3.json") or name.endswith(".physics3.json"):
        return True
    return path.suffix.lower() in allowed_suffixes


def _find_first(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.rglob(pattern))
    return matches[0] if matches else None


def _find_first_with_suffix(root: Path, suffixes: tuple[str, ...]) -> Path | None:
    for suffix in suffixes:
        matches = sorted(item for item in root.rglob(f"*{suffix}") if item.is_file())
        if matches:
            return matches[0]
    return None


def _read_json_object(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else dict(default)


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_asset_id(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", str(value or "").strip()).strip("-").lower()
    return normalized or "spirit"


def _as_frontend_url(frontend: Path, path: Path) -> str:
    return path.resolve().relative_to(frontend.resolve()).as_posix()


def _default_live2d_expressions() -> dict[str, str]:
    return {
        "happy": "Happy",
        "thinking": "Thinking",
        "confused": "Confused",
        "neutral": "Neutral",
        "error": "Angry",
        "waiting": "Neutral",
        "listening": "Listen",
        "speaking": "Happy",
        "idle": "Neutral",
    }


def _default_live2d_motions() -> dict[str, str]:
    return {
        "idle": "Idle",
        "listen": "TapBody",
        "voice_ack": "TapBody",
        "await_confirmation": "TapBody",
        "execute_task": "TapBody",
        "thinking": "Idle",
        "speaking": "TapBody",
        "error": "TapBody",
    }


def _default_avatar3d_manifest(model_url: str) -> dict[str, Any]:
    fmt = Path(model_url).suffix.lower().lstrip(".") or "fbx"
    return {
        "version": "spiritkin.avatar3d.v1",
        "format": fmt,
        "model": model_url,
        "camera": {"fov": 35, "near": 0.1, "far": 100, "position": [0, 0.75, 5.15], "target": [0, 0.05, 0]},
        "controls": {"min_distance": 1.6, "max_distance": 6.5},
        "lights": {"hemisphere_intensity": 1.8, "directional_intensity": 2.4, "directional_position": [2, 4, 3]},
        "stage": {"floor_radius": 0.98, "floor_opacity": 0, "floor_visible": False},
        "fit": {"base_scale": 0.01 if fmt == "fbx" else 1.0, "target_size": 1.42, "auto_fit": True, "center": True, "ground": True},
        "transform": {"position": [0, -1.18, 0], "rotation_deg": [0, 0, 0], "scale": 0.8},
        "motion": {"idle_bob": 0, "speaking_bob": 0.09, "idle_yaw": 0, "speaking_yaw": 0.22, "breathing_scale": 0, "bone_motion": 0.06},
        "visemes": {
            "idle": {"keywords": ["mouth", "jaw", "viseme"], "intensity": 0},
            "open": {"keywords": ["mouthopen", "jawopen", "open", "viseme_aa"], "intensity": 1},
            "a": {"keywords": ["aa", "ah", "viseme_aa"], "intensity": 1},
            "o": {"keywords": ["oo", "ou", "oh", "viseme_oh"], "intensity": 0.85},
            "i": {"keywords": ["ih", "ee", "viseme_ih"], "intensity": 0.75},
        },
        "expressions": {
            "neutral": {"keywords": [], "intensity": 0, "pose": {"yaw": 0, "bob": 0, "scale": 1}},
            "happy": {"keywords": ["smile", "happy", "joy"], "intensity": 0.65, "pose": {"yaw": 0.06, "bob": 0.012, "scale": 1.03}},
            "thinking": {"keywords": ["think", "squint", "blink"], "intensity": 0.25, "pose": {"yaw": -0.05, "bob": 0.004, "scale": 1.01}},
            "waiting": {"keywords": ["blink", "idle"], "intensity": 0.2, "pose": {"yaw": 0.025, "bob": 0.004, "scale": 1.01}},
            "alert": {"keywords": ["surprise", "alert", "wide"], "intensity": 0.55, "pose": {"yaw": 0.1, "bob": 0.008, "scale": 1.04}},
            "error": {"keywords": ["sad", "frown", "angry"], "intensity": 0.45, "pose": {"yaw": -0.08, "bob": -0.006, "scale": 0.97}},
            "confused": {"keywords": ["confused", "brow"], "intensity": 0.45, "pose": {"yaw": -0.1, "bob": 0.004, "scale": 1.02}},
        },
    }
