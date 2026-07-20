from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

BONE_LAYOUT = {
    "root": ((0.0, 0.0, 0.38), (0.0, 0.0, 0.58), None),
    "torso": ((0.0, 0.0, 0.50), (0.0, 0.0, 0.72), "root"),
    "head": ((0.0, 0.0, 0.72), (0.0, 0.0, 1.02), "torso"),
    "left_arm": ((-0.24, 0.0, 0.55), (-0.50, 0.0, 0.43), "torso"),
    "right_arm": ((0.24, 0.0, 0.55), (0.50, 0.0, 0.43), "torso"),
    "left_leg": ((-0.12, 0.0, 0.35), (-0.18, 0.0, 0.02), "root"),
    "right_leg": ((0.12, 0.0, 0.35), (0.18, 0.0, 0.02), "root"),
    "left_ear": ((-0.28, 0.0, 0.88), (-0.48, 0.0, 1.10), "head"),
    "right_ear": ((0.28, 0.0, 0.88), (0.48, 0.0, 1.10), "head"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a clean coarse rig for the current SpiritKin stylized model.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--target-height", type=float, default=1.7)
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


def meshes() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def scene_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points = []
    for obj in objects:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mins, maxs


def normalize(target_height: float) -> dict[str, float]:
    mins, maxs = scene_bounds(meshes())
    height = max(maxs.z - mins.z, 0.001)
    scale = target_height / height
    for obj in bpy.context.scene.objects:
        obj.location *= scale
        obj.scale *= scale
    bpy.context.view_layer.update()
    mins, _ = scene_bounds(meshes())
    for obj in bpy.context.scene.objects:
        obj.location.z -= mins.z
    bpy.context.view_layer.update()
    return {"original_height": height, "scale": scale}


def remove_existing_armatures() -> int:
    removed = 0
    for obj in list(bpy.context.scene.objects):
        if obj.type == "MESH":
            for modifier in list(obj.modifiers):
                if modifier.type == "ARMATURE":
                    obj.modifiers.remove(modifier)
            for group in list(obj.vertex_groups):
                obj.vertex_groups.remove(group)
        elif obj.type == "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    return removed


def create_armature(target_height: float) -> bpy.types.Object:
    bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, 0))
    armature = bpy.context.object
    armature.name = "SpiritKin_CleanRig"
    armature.data.name = "SpiritKin_CleanRig"
    first = armature.data.edit_bones[0]
    first.name = "root"
    first.head = Vector(BONE_LAYOUT["root"][0]) * target_height
    first.tail = Vector(BONE_LAYOUT["root"][1]) * target_height
    for name, (head, tail, parent) in BONE_LAYOUT.items():
        if name == "root":
            continue
        bone = armature.data.edit_bones.new(name)
        bone.head = Vector(head) * target_height
        bone.tail = Vector(tail) * target_height
        if parent:
            bone.parent = armature.data.edit_bones[parent]
    for bone in armature.data.edit_bones:
        bone.roll = 0
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def object_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mins + maxs) * 0.5
    return mins, maxs, center


def classify_mesh(obj: bpy.types.Object, scene_min: Vector, scene_max: Vector) -> str:
    mins, maxs, center = object_bounds(obj)
    height = max(scene_max.z - scene_min.z, 0.001)
    nz = (center.z - scene_min.z) / height
    width = max(scene_max.x - scene_min.x, 0.001)
    nx = (center.x - scene_min.x) / width
    sx = maxs.x - mins.x
    sz = maxs.z - mins.z
    if nz > 0.78 and nx < 0.35:
        return "left_ear"
    if nz > 0.78 and nx > 0.65:
        return "right_ear"
    if nz > 0.58:
        return "head"
    if nz < 0.28 and nx < 0.46:
        return "left_leg"
    if nz < 0.28 and nx > 0.54:
        return "right_leg"
    if 0.28 <= nz <= 0.62 and nx < 0.28 and sx < sz * 1.8:
        return "left_arm"
    if 0.28 <= nz <= 0.62 and nx > 0.72 and sx < sz * 1.8:
        return "right_arm"
    return "torso"


def assign_mesh_to_bone(obj: bpy.types.Object, role: str, armature: bpy.types.Object) -> None:
    group = obj.vertex_groups.new(name=role)
    group.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")
    modifier = obj.modifiers.new("SpiritKin_CleanRig", "ARMATURE")
    modifier.object = armature
    obj.parent = armature


def create_actions(armature: bpy.types.Object) -> list[str]:
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 90
    actions = []
    for frame, head_yaw, head_pitch, arm in ((1, 0, 0, 0), (45, 0.08, math.radians(-4), math.radians(8)), (90, 0, 0, 0)):
        bpy.context.scene.frame_set(frame)
        for pose_bone in armature.pose.bones:
            pose_bone.rotation_mode = "XYZ"
            pose_bone.rotation_euler = (0, 0, 0)
            if pose_bone.name == "head":
                pose_bone.rotation_euler.x = head_pitch
                pose_bone.rotation_euler.z = head_yaw
            if pose_bone.name in {"left_arm", "right_arm"}:
                pose_bone.rotation_euler.z = arm if pose_bone.name == "left_arm" else -arm
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)
    if armature.animation_data and armature.animation_data.action:
        armature.animation_data.action.name = "idle_clean"
        actions.append("idle_clean")
    bpy.ops.object.mode_set(mode="OBJECT")
    return actions


def export_glb(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_animations=True,
        export_morph=False,
        export_materials="EXPORT",
    )


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    report = Path(args.report).resolve()
    clear_scene()
    import_model(source)
    normalize_info = normalize(args.target_height)
    removed = remove_existing_armatures()
    armature = create_armature(args.target_height)
    scene_min, scene_max = scene_bounds(meshes())
    assignments = {}
    for obj in meshes():
        role = classify_mesh(obj, scene_min, scene_max)
        assignments[obj.name] = role
        assign_mesh_to_bone(obj, role, armature)
    actions = create_actions(armature)
    export_glb(output)
    data = {
        "source": str(source),
        "output": str(output),
        "removed_armatures": removed,
        "assignments": assignments,
        "actions": actions,
        "normalize": normalize_info,
        "warning": "Coarse clean rig: rigid mesh-to-bone assignment for this stylized toy model. Fine motion still requires manual weight polish.",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
