from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose Blender rig locality and deformation risk.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--angle-deg", type=float, default=15.0)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_model(source: Path) -> None:
    if source.suffix.lower() == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source), automatic_bone_orientation=True)
    elif source.suffix.lower() in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source))
    else:
        raise ValueError(f"unsupported format: {source.suffix}")


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def armature_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def evaluated_vertices(meshes: list[bpy.types.Object]) -> dict[str, list[Vector]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result: dict[str, list[Vector]] = {}
    for obj in meshes:
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        try:
            result[obj.name] = [eval_obj.matrix_world @ vertex.co for vertex in eval_mesh.vertices]
        finally:
            eval_obj.to_mesh_clear()
    return result


def displacement_summary(before: dict[str, list[Vector]], after: dict[str, list[Vector]]) -> dict[str, object]:
    mesh_rows = []
    all_distances: list[float] = []
    for name, points in before.items():
        other = after.get(name, [])
        distances = [(a - b).length for a, b in zip(points, other, strict=False)]
        if not distances:
            continue
        moved = [distance for distance in distances if distance > 0.0005]
        all_distances.extend(distances)
        mesh_rows.append(
            {
                "mesh": name,
                "vertices": len(distances),
                "moved_vertices": len(moved),
                "moved_ratio": round(len(moved) / max(1, len(distances)), 4),
                "avg_delta": round(sum(distances) / len(distances), 6),
                "max_delta": round(max(distances), 6),
            }
        )
    moved_all = [distance for distance in all_distances if distance > 0.0005]
    return {
        "moved_ratio_total": round(len(moved_all) / max(1, len(all_distances)), 4),
        "avg_delta_total": round(sum(all_distances) / max(1, len(all_distances)), 6),
        "max_delta_total": round(max(all_distances or [0]), 6),
        "top_meshes": sorted(mesh_rows, key=lambda row: row["moved_ratio"], reverse=True)[:8],
    }


def reset_pose(armature: bpy.types.Object) -> None:
    for pose_bone in armature.pose.bones:
        pose_bone.rotation_mode = "XYZ"
        pose_bone.rotation_euler = (0, 0, 0)
        pose_bone.location = (0, 0, 0)
        pose_bone.scale = (1, 1, 1)
    bpy.context.view_layer.update()


def diagnose_bone(armature: bpy.types.Object, meshes: list[bpy.types.Object], bone_name: str, angle_rad: float) -> dict[str, object]:
    reset_pose(armature)
    before = evaluated_vertices(meshes)
    pose_bone = armature.pose.bones[bone_name]
    pose_bone.rotation_mode = "XYZ"
    axis_results = {}
    for axis_index, axis_name in enumerate(("x", "y", "z")):
        reset_pose(armature)
        pose_bone = armature.pose.bones[bone_name]
        pose_bone.rotation_mode = "XYZ"
        values = [0.0, 0.0, 0.0]
        values[axis_index] = angle_rad
        pose_bone.rotation_euler = values
        bpy.context.view_layer.update()
        after = evaluated_vertices(meshes)
        axis_results[axis_name] = displacement_summary(before, after)
    reset_pose(armature)
    return {"bone": bone_name, "axes": axis_results}


def main() -> int:
    args = parse_args()
    clear_scene()
    import_model(Path(args.source).resolve())
    meshes = mesh_objects()
    armatures = armature_objects()
    if not armatures:
        raise RuntimeError("no armature found")
    armature = armatures[0]
    angle_rad = math.radians(args.angle_deg)
    report = {
        "source": str(Path(args.source).resolve()),
        "angle_deg": args.angle_deg,
        "armature": armature.name,
        "bones": [bone.name for bone in armature.pose.bones],
        "diagnostics": [diagnose_bone(armature, meshes, bone.name, angle_rad) for bone in armature.pose.bones],
    }
    path = Path(args.report).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
