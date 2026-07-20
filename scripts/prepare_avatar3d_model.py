from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT_DIR / "frontend" / "models" / "spirit3d" / "SpiritKinAI.fbx"
DEFAULT_OUTPUT = ROOT_DIR / "frontend" / "models" / "spirit3d" / "SpiritKinAI.rigged.glb"
DEFAULT_MANIFEST = ROOT_DIR / "frontend" / "models" / "spirit3d" / "manifest.rigged.json"
DEFAULT_ACTIVE_MANIFEST = ROOT_DIR / "frontend" / "models" / "spirit3d" / "manifest.json"
BLENDER_PIPELINE = ROOT_DIR / "scripts" / "blender_avatar3d_pipeline.py"


def find_blender(explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    env_path = os.getenv("BLENDER_EXE", "").strip()
    if env_path:
        return env_path
    found = shutil.which("blender")
    if found:
        return found
    for candidate in (
        Path("E:/blender.exe"),
        Path("E:/Blender/blender.exe"),
        Path("D:/blender.exe"),
        Path("D:/Blender/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.3/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.1/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.0/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 3.6/blender.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return ""


def frontend_url_for(path: Path) -> str:
    return path.resolve().relative_to((ROOT_DIR / "frontend").resolve()).as_posix()


def build_manifest(model_url: str, *, name: str = "SpiritKinAI Rigged", include_morph_bindings: bool = False) -> dict[str, Any]:
    expressions = {
        "neutral": {"keywords": [], "intensity": 0, "pose": {"yaw": 0, "bob": 0, "scale": 1}},
        "happy": {"keywords": ["smile", "happy", "joy"] if include_morph_bindings else [], "intensity": 0.65 if include_morph_bindings else 0, "pose": {"yaw": 0.06, "bob": 0.012, "scale": 1.03}},
        "thinking": {"keywords": ["think", "squint", "blink"] if include_morph_bindings else [], "intensity": 0.28 if include_morph_bindings else 0, "pose": {"yaw": -0.05, "bob": 0.004, "scale": 1.01}},
        "waiting": {"keywords": ["blink", "idle"] if include_morph_bindings else [], "intensity": 0.22 if include_morph_bindings else 0, "pose": {"yaw": 0.025, "bob": 0.004, "scale": 1.01}},
        "alert": {"keywords": ["surprise", "alert", "wide"] if include_morph_bindings else [], "intensity": 0.55 if include_morph_bindings else 0, "pose": {"yaw": 0.1, "bob": 0.008, "scale": 1.04}},
        "error": {"keywords": ["sad", "frown", "angry"] if include_morph_bindings else [], "intensity": 0.45 if include_morph_bindings else 0, "pose": {"yaw": -0.08, "bob": -0.006, "scale": 0.97}},
        "confused": {"keywords": ["confused", "brow", "squint"] if include_morph_bindings else [], "intensity": 0.45 if include_morph_bindings else 0, "pose": {"yaw": -0.1, "bob": 0.004, "scale": 1.02}},
    }
    visemes = {
        "idle": {"keywords": ["mouth", "jaw", "viseme"] if include_morph_bindings else [], "intensity": 0},
        "open": {"keywords": ["mouthopen", "jawopen", "open", "viseme_aa", "aa"] if include_morph_bindings else [], "intensity": 1 if include_morph_bindings else 0},
        "a": {"keywords": ["aa", "ah", "viseme_aa", "mouthopen"] if include_morph_bindings else [], "intensity": 1 if include_morph_bindings else 0},
        "aa": {"keywords": ["aa", "ah", "viseme_aa", "mouthopen"] if include_morph_bindings else [], "intensity": 1 if include_morph_bindings else 0},
        "o": {"keywords": ["oo", "ou", "oh", "viseme_oh", "mouth_o"] if include_morph_bindings else [], "intensity": 0.85 if include_morph_bindings else 0},
        "ou": {"keywords": ["oo", "ou", "oh", "viseme_oh", "mouth_o"] if include_morph_bindings else [], "intensity": 0.85 if include_morph_bindings else 0},
        "oh": {"keywords": ["oo", "ou", "oh", "viseme_oh", "mouth_o"] if include_morph_bindings else [], "intensity": 0.85 if include_morph_bindings else 0},
        "i": {"keywords": ["ih", "ee", "viseme_ih", "mouth_i"] if include_morph_bindings else [], "intensity": 0.75 if include_morph_bindings else 0},
        "ih": {"keywords": ["ih", "ee", "viseme_ih", "mouth_i"] if include_morph_bindings else [], "intensity": 0.75 if include_morph_bindings else 0},
        "ee": {"keywords": ["ih", "ee", "viseme_ih", "mouth_i"] if include_morph_bindings else [], "intensity": 0.75 if include_morph_bindings else 0},
    }
    return {
        "version": "spiritkin.avatar3d.v1",
        "format": Path(model_url).suffix.lower().lstrip(".") or "glb",
        "name": name,
        "model": model_url,
        "camera": {"fov": 35, "near": 0.1, "far": 100, "position": [0, 1.05, 3.4], "target": [0, 0.95, 0]},
        "controls": {"min_distance": 1.2, "max_distance": 5.8},
        "lights": {"hemisphere_intensity": 1.8, "directional_intensity": 2.4, "directional_position": [2, 4, 3]},
        "stage": {"floor_radius": 1.0, "floor_opacity": 0, "floor_visible": False},
        "fit": {"base_scale": 1, "target_size": 1.7, "auto_fit": True, "center": True, "ground": True},
        "transform": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": 1},
        "motion": {
            "idle_bob": 0.0015,
            "speaking_bob": 0.008,
            "idle_yaw": 0.003,
            "speaking_yaw": 0.012,
            "breathing_scale": 0.003,
            "bone_motion": 0,
            "procedural_bones": False,
        },
        "visemes": visemes,
        "expressions": expressions,
        "pipeline": {
            "source": "blender_avatar3d_pipeline.py",
            "rigging": "heuristic_auto_weights_safe_overlay",
            "expressions": "disabled_by_default" if not include_morph_bindings else "heuristic_shape_keys",
            "frontend_motion_mode": "safe_overlay_until_polished",
            "requires_manual_polish": True,
        },
    }


def write_manifest(path: Path, model_url: str, *, name: str, include_morph_bindings: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(model_url, name=name, include_morph_bindings=include_morph_bindings), ensure_ascii=False, indent=2), encoding="utf-8")


def build_blender_command(
    blender: str,
    *,
    source: Path,
    output: Path,
    report: Path,
    target_height: float,
    front_axis: str,
    unsafe_shape_keys: bool = False,
) -> list[str]:
    command = [
        blender,
        "--background",
        "--python",
        str(BLENDER_PIPELINE),
        "--",
        "--source",
        str(source),
        "--output",
        str(output),
        "--report",
        str(report),
        "--target-height",
        str(target_height),
        f"--front-axis={front_axis}",
    ]
    if unsafe_shape_keys:
        command.append("--unsafe-shape-keys")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a SpiritKin 3D avatar with Blender auto rigging and GLB export.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Input .fbx/.glb/.gltf model path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output .glb path.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Manifest path to write after export.")
    parser.add_argument("--activate", action="store_true", help="Also replace frontend/models/spirit3d/manifest.json with the generated GLB manifest.")
    parser.add_argument("--name", default="SpiritKinAI Rigged", help="Display name stored in manifest.")
    parser.add_argument("--blender", default="", help="Explicit blender executable. Also supports BLENDER_EXE env var.")
    parser.add_argument("--target-height", type=float, default=1.7, help="Normalize model height in Blender units.")
    parser.add_argument("--front-axis", choices=["-Y", "Y", "-X", "X"], default="-Y", help="Approximate character front axis for expression heuristics.")
    parser.add_argument("--unsafe-shape-keys", action="store_true", help="Generate heuristic facial shape keys. This can distort non-humanoid or stylized meshes.")
    parser.add_argument("--enable-morph-bindings", action="store_true", help="Bind manifest expressions/visemes to morph target names.")
    parser.add_argument("--print-command", action="store_true", help="Print the Blender command without executing.")
    parser.add_argument("--write-manifest-only", action="store_true", help="Only write manifest for an existing output file.")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    manifest = Path(args.manifest).resolve()
    report = output.with_suffix(".pipeline-report.json")
    model_url = frontend_url_for(output)

    if args.write_manifest_only:
        write_manifest(manifest, model_url, name=args.name, include_morph_bindings=args.enable_morph_bindings)
        if args.activate:
            write_manifest(DEFAULT_ACTIVE_MANIFEST, model_url, name=args.name, include_morph_bindings=args.enable_morph_bindings)
        print(f"[manifest] wrote {manifest}")
        return 0

    if not source.exists():
        print(f"[error] source model not found: {source}", file=sys.stderr)
        return 2

    blender = find_blender(args.blender)
    if not blender and args.print_command:
        blender = "blender"
    if not blender:
        print("[error] Blender executable was not found.", file=sys.stderr)
        print("Install Blender or pass --blender C:\\path\\to\\blender.exe / set BLENDER_EXE.", file=sys.stderr)
        return 2

    command = build_blender_command(
        blender,
        source=source,
        output=output,
        report=report,
        target_height=args.target_height,
        front_axis=args.front_axis,
        unsafe_shape_keys=args.unsafe_shape_keys,
    )
    if args.print_command:
        print(" ".join(f'"{part}"' if " " in part else part for part in command))
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    if completed.returncode != 0:
        print(f"[error] Blender pipeline failed with exit code {completed.returncode}", file=sys.stderr)
        return completed.returncode
    if not output.exists():
        print(f"[error] Blender finished but output was not created: {output}", file=sys.stderr)
        return 3

    write_manifest(manifest, model_url, name=args.name, include_morph_bindings=args.enable_morph_bindings)
    if args.activate:
        write_manifest(DEFAULT_ACTIVE_MANIFEST, model_url, name=args.name, include_morph_bindings=args.enable_morph_bindings)
    print(f"[ok] exported {output}")
    print(f"[ok] manifest {manifest}")
    if args.activate:
        print(f"[ok] active manifest {DEFAULT_ACTIVE_MANIFEST}")
    if report.exists():
        print(f"[ok] report {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
