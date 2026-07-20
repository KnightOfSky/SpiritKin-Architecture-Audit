from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

SEMANTIC_MESH_ROLES = {
    "part_0": "head",
    "part_14": "screen",
    "part_5": "left_ear",
    "part_6": "right_ear",
    "part_2": "left_arm",
    "part_3": "right_arm",
    "part_8": "left_side_panel",
    "part_9": "left_side_panel",
    "part_10": "left_side_panel",
    "part_11": "left_side_panel",
    "part_12": "left_side_panel",
    "part_4": "right_side_panel",
    "part_7": "right_side_panel",
    "part_13": "right_side_panel",
}

RIG_BONES = (
    "root",
    "body",
    "head",
    "screen",
    "left_ear",
    "right_ear",
    "left_side_panel",
    "right_side_panel",
    "left_arm",
    "right_arm",
    "left_leg",
    "right_leg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a clean semantic rig candidate for a stylized SpiritKin avatar.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--blend-output", default="", help="Optional .blend work file for manual rig/weight polish.")
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
        raise ValueError(f"unsupported source format: {source.suffix}")


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def scene_bounds(meshes: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points = []
    for obj in meshes:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    mins = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    maxs = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    return mins, maxs


def remove_existing_rig() -> None:
    for obj in mesh_objects():
        for modifier in list(obj.modifiers):
            if modifier.type == "ARMATURE":
                obj.modifiers.remove(modifier)
        for group in list(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        obj.parent = None
    for obj in list(bpy.context.scene.objects):
        if obj.type == "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)


def create_clean_armature(mins: Vector, maxs: Vector) -> bpy.types.Object:
    center = (mins + maxs) * 0.5
    size = maxs - mins
    z0 = mins.z
    h = max(size.z, 0.001)
    bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, 0))
    arm = bpy.context.object
    arm.name = "SpiritKin_CleanRig"
    arm.data.name = "SpiritKin_CleanRig"
    root = arm.data.edit_bones[0]
    root.name = "root"
    root.head = (center.x, center.y, z0 + h * 0.28)
    root.tail = (center.x, center.y, z0 + h * 0.42)

    def add(name: str, head: tuple[float, float, float], tail: tuple[float, float, float], parent: str) -> None:
        bone = arm.data.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        bone.parent = arm.data.edit_bones[parent]
        bone.use_connect = False

    add("body", (center.x, center.y, z0 + h * 0.24), (center.x, center.y, z0 + h * 0.52), "root")
    add("head", (center.x, center.y, z0 + h * 0.45), (center.x, center.y, z0 + h * 0.80), "body")
    add("screen", (center.x, mins.y + size.y * 0.08, z0 + h * 0.56), (center.x, mins.y + size.y * 0.02, z0 + h * 0.66), "head")
    add("left_ear", (center.x - size.x * 0.30, center.y, z0 + h * 0.68), (center.x - size.x * 0.43, center.y, z0 + h * 0.92), "head")
    add("right_ear", (center.x + size.x * 0.30, center.y, z0 + h * 0.68), (center.x + size.x * 0.43, center.y, z0 + h * 0.92), "head")
    add("left_side_panel", (mins.x + size.x * 0.14, mins.y + size.y * 0.23, z0 + h * 0.47), (mins.x + size.x * 0.04, mins.y + size.y * 0.20, z0 + h * 0.55), "head")
    add("right_side_panel", (maxs.x - size.x * 0.14, mins.y + size.y * 0.23, z0 + h * 0.47), (maxs.x - size.x * 0.04, mins.y + size.y * 0.20, z0 + h * 0.55), "head")
    add("left_arm", (center.x - size.x * 0.18, center.y, z0 + h * 0.34), (mins.x + size.x * 0.08, center.y, z0 + h * 0.18), "body")
    add("right_arm", (center.x + size.x * 0.18, center.y, z0 + h * 0.34), (maxs.x - size.x * 0.08, center.y, z0 + h * 0.18), "body")
    add("left_leg", (center.x - size.x * 0.12, center.y, z0 + h * 0.28), (center.x - size.x * 0.18, center.y, z0 + h * 0.02), "root")
    add("right_leg", (center.x + size.x * 0.12, center.y, z0 + h * 0.28), (center.x + size.x * 0.18, center.y, z0 + h * 0.02), "root")
    for bone in arm.data.edit_bones:
        bone.roll = 0
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def classify_vertex(mesh_name: str, world: Vector, mins: Vector, maxs: Vector) -> str:
    mesh_role = SEMANTIC_MESH_ROLES.get(mesh_name)
    if mesh_role:
        return mesh_role
    center = (mins + maxs) * 0.5
    size = maxs - mins
    z_norm = (world.z - mins.z) / max(size.z, 0.001)
    x_norm = (world.x - center.x) / max(size.x, 0.001)
    if z_norm >= 0.48:
        return "head"
    if z_norm <= 0.28:
        if x_norm < -0.11:
            return "left_leg"
        if x_norm > 0.11:
            return "right_leg"
        return "root"
    if x_norm < -0.28:
        return "left_arm"
    if x_norm > 0.28:
        return "right_arm"
    return "body"


def assign_clean_weights(armature: bpy.types.Object, mins: Vector, maxs: Vector) -> dict[str, dict[str, int]]:
    weight_report: dict[str, dict[str, int]] = {}
    for obj in mesh_objects():
        groups = {name: obj.vertex_groups.new(name=name) for name in RIG_BONES}
        counts = {name: 0 for name in groups}
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            role = classify_vertex(obj.name, world, mins, maxs)
            groups[role].add([vertex.index], 1.0, "REPLACE")
            counts[role] += 1
        modifier = obj.modifiers.new("SpiritKin_CleanRig", "ARMATURE")
        modifier.object = armature
        obj.parent = armature
        weight_report[obj.name] = counts
    return weight_report


def add_preview_action(armature: bpy.types.Object) -> list[str]:
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 96
    keyed = []
    for frame, yaw, bob in ((1, 0.0, 0.0), (24, 0.08, 0.02), (48, 0.0, 0.0), (72, -0.08, 0.02), (96, 0.0, 0.0)):
        bpy.context.scene.frame_set(frame)
        for bone in armature.pose.bones:
            bone.rotation_mode = "XYZ"
            bone.rotation_euler = (0, 0, 0)
            bone.location = (0, 0, 0)
            if bone.name == "head":
                bone.rotation_euler.z = yaw
            elif bone.name in {"left_ear", "right_ear"}:
                side = -1 if bone.name == "left_ear" else 1
                bone.rotation_euler.y = math.sin(frame / 96 * math.tau) * 0.035 * side
            elif bone.name in {"left_side_panel", "right_side_panel"}:
                side = -1 if bone.name == "left_side_panel" else 1
                bone.rotation_euler.z = math.sin(frame / 96 * math.tau) * 0.025 * side
            elif bone.name == "body":
                bone.location.z = bob
            elif bone.name in {"left_arm", "right_arm"}:
                bone.rotation_euler.x = math.sin(frame / 96 * math.tau) * 0.08
            bone.keyframe_insert(data_path="rotation_euler", frame=frame)
            bone.keyframe_insert(data_path="location", frame=frame)
    if armature.animation_data and armature.animation_data.action:
        armature.animation_data.action.name = "idle_clean"
        keyed.append("idle_clean")
    bpy.ops.object.mode_set(mode="OBJECT")
    return keyed


def export_glb(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(output),
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
    meshes = mesh_objects()
    mins, maxs = scene_bounds(meshes)
    remove_existing_rig()
    armature = create_clean_armature(mins, maxs)
    weights = assign_clean_weights(armature, mins, maxs)
    actions = add_preview_action(armature)
    export_glb(output)
    blend_output = Path(args.blend_output).resolve() if args.blend_output.strip() else None
    if blend_output:
        blend_output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output))
    data = {
        "source": str(source),
        "output": str(output),
        "blend_output": str(blend_output) if blend_output else "",
        "armature": armature.name,
        "bones": [bone.name for bone in armature.data.bones],
        "bounds": {
            "min": [round(float(v), 5) for v in mins],
            "max": [round(float(v), 5) for v in maxs],
        },
        "weights": weights,
        "actions": actions,
        "warnings": [
            "This is a clean candidate rig generated from spatial regions. Inspect and polish weights in Blender before treating it as final.",
            "The original model has stylized connected surfaces, so head/body separation may still need manual topology or weight cleanup.",
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
