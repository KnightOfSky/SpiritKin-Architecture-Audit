from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

FACE_SHAPE_KEYS = {
    "smile": {"mouth_width": 0.11, "mouth_up": 0.055, "eye_squint": -0.012},
    "happy": {"mouth_width": 0.1, "mouth_up": 0.05, "eye_squint": -0.01},
    "sad": {"mouth_width": -0.035, "mouth_up": -0.045, "brow": 0.02},
    "angry": {"mouth_width": -0.025, "mouth_up": -0.025, "brow": -0.025},
    "surprise": {"mouth_open": -0.09, "eye_wide": 0.025},
    "blink": {"eye_close": -0.045},
    "squint": {"eye_squint": -0.025},
    "aa": {"mouth_open": -0.115, "mouth_width": 0.015},
    "ih": {"mouth_open": -0.04, "mouth_width": 0.075},
    "ou": {"mouth_open": -0.055, "mouth_width": -0.055},
    "oh": {"mouth_open": -0.085, "mouth_width": -0.04},
    "ee": {"mouth_open": -0.035, "mouth_width": 0.09},
    "mouthopen": {"mouth_open": -0.11},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Blender-side SpiritKin avatar preparation pipeline.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--target-height", type=float, default=1.7)
    parser.add_argument("--front-axis", choices=["-Y", "Y", "-X", "X"], default="-Y")
    parser.add_argument("--unsafe-shape-keys", action="store_true")
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_model(source: Path) -> None:
    suffix = source.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source), automatic_bone_orientation=True)
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source))
    else:
        raise ValueError(f"unsupported source format: {source.suffix}")


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def armatures() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def scene_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points = []
    for obj in objects:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mins, maxs


def normalize_scene(target_height: float) -> dict[str, float]:
    meshes = mesh_objects()
    mins, maxs = scene_bounds(meshes)
    height = max(maxs.z - mins.z, 0.001)
    scale = target_height / height
    for obj in list(bpy.context.scene.objects):
        obj.location = obj.location * scale
        obj.scale = obj.scale * scale
    bpy.context.view_layer.update()
    meshes = mesh_objects()
    mins, _ = scene_bounds(meshes)
    for obj in list(bpy.context.scene.objects):
        obj.location.z -= mins.z
    bpy.context.view_layer.update()
    return {"original_height": height, "scale": scale}


def create_basic_armature(target_height: float, front_axis: str) -> bpy.types.Object:
    bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, 0))
    armature = bpy.context.object
    armature.name = "SpiritKin_AutoRig"
    armature.data.name = "SpiritKin_AutoRig"
    bone = armature.data.edit_bones[0]
    bone.name = "hips"
    bone.head = (0, 0, target_height * 0.48)
    bone.tail = (0, 0, target_height * 0.62)

    def add_bone(name: str, head: tuple[float, float, float], tail: tuple[float, float, float], parent: str | None = None) -> None:
        edit_bone = armature.data.edit_bones.new(name)
        edit_bone.head = head
        edit_bone.tail = tail
        if parent:
            edit_bone.parent = armature.data.edit_bones[parent]

    add_bone("spine", (0, 0, target_height * 0.62), (0, 0, target_height * 0.78), "hips")
    add_bone("chest", (0, 0, target_height * 0.78), (0, 0, target_height * 0.9), "spine")
    add_bone("neck", (0, 0, target_height * 0.9), (0, 0, target_height * 0.97), "chest")
    add_bone("head", (0, 0, target_height * 0.97), (0, 0, target_height * 1.1), "neck")
    add_bone("left_shoulder", (0, 0, target_height * 0.86), (-target_height * 0.12, 0, target_height * 0.84), "chest")
    add_bone("left_arm", (-target_height * 0.12, 0, target_height * 0.84), (-target_height * 0.34, 0, target_height * 0.7), "left_shoulder")
    add_bone("right_shoulder", (0, 0, target_height * 0.86), (target_height * 0.12, 0, target_height * 0.84), "chest")
    add_bone("right_arm", (target_height * 0.12, 0, target_height * 0.84), (target_height * 0.34, 0, target_height * 0.7), "right_shoulder")
    add_bone("left_leg", (-target_height * 0.07, 0, target_height * 0.48), (-target_height * 0.09, 0, target_height * 0.05), "hips")
    add_bone("right_leg", (target_height * 0.07, 0, target_height * 0.48), (target_height * 0.09, 0, target_height * 0.05), "hips")

    for edit_bone in armature.data.edit_bones:
        edit_bone.roll = 0
    if front_axis in {"X", "-X"}:
        armature.rotation_euler.z = math.radians(90 if front_axis == "X" else -90)
    elif front_axis == "Y":
        armature.rotation_euler.z = math.radians(180)
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def bind_meshes_to_armature(armature: bpy.types.Object) -> None:
    meshes = mesh_objects()
    if not meshes:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except RuntimeError:
        for obj in meshes:
            modifier = obj.modifiers.new("SpiritKin_AutoRig", "ARMATURE")
            modifier.object = armature


def ensure_materials() -> None:
    for obj in mesh_objects():
        for slot in obj.material_slots:
            mat = slot.material
            if mat:
                mat.use_nodes = True


def add_shape_key_if_absent(obj: bpy.types.Object, name: str) -> bpy.types.ShapeKey:
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis")
    existing = obj.data.shape_keys.key_blocks.get(name)
    if existing:
        return existing
    return obj.shape_key_add(name=name)


def vertex_face_regions(obj: bpy.types.Object) -> dict[str, list[int]]:
    vertices = obj.data.vertices
    if not vertices:
        return {}
    coords = [obj.matrix_world @ v.co for v in vertices]
    min_x, max_x = min(v.x for v in coords), max(v.x for v in coords)
    min_z, max_z = min(v.z for v in coords), max(v.z for v in coords)
    width = max(max_x - min_x, 0.001)
    height = max(max_z - min_z, 0.001)
    regions = {"mouth": [], "eyes": [], "brow": []}
    for index, world in enumerate(coords):
        nx = (world.x - min_x) / width
        nz = (world.z - min_z) / height
        if 0.34 <= nx <= 0.66 and 0.58 <= nz <= 0.74:
            regions["mouth"].append(index)
        if 0.26 <= nx <= 0.74 and 0.77 <= nz <= 0.9:
            regions["eyes"].append(index)
        if 0.28 <= nx <= 0.72 and 0.86 <= nz <= 0.96:
            regions["brow"].append(index)
    return regions


def apply_shape_delta(obj: bpy.types.Object, shape_key: bpy.types.ShapeKey, regions: dict[str, list[int]], spec: dict[str, float]) -> None:
    basis = obj.data.shape_keys.key_blocks["Basis"]
    mouth = regions.get("mouth", [])
    eyes = regions.get("eyes", [])
    brow = regions.get("brow", [])
    for index in mouth:
        co = basis.data[index].co.copy()
        local_x = co.x
        side = 1 if local_x >= 0 else -1
        co.x += side * float(spec.get("mouth_width", 0.0))
        co.z += float(spec.get("mouth_up", 0.0))
        co.z += float(spec.get("mouth_open", 0.0))
        shape_key.data[index].co = co
    for index in eyes:
        co = basis.data[index].co.copy()
        co.z += float(spec.get("eye_close", 0.0))
        co.z += float(spec.get("eye_squint", 0.0))
        co.z += float(spec.get("eye_wide", 0.0))
        shape_key.data[index].co = co
    for index in brow:
        co = basis.data[index].co.copy()
        co.z += float(spec.get("brow", 0.0))
        shape_key.data[index].co = co


def add_heuristic_shape_keys() -> dict[str, int]:
    created: dict[str, int] = {}
    for obj in mesh_objects():
        regions = vertex_face_regions(obj)
        if not any(regions.values()):
            continue
        for name, spec in FACE_SHAPE_KEYS.items():
            key = add_shape_key_if_absent(obj, name)
            apply_shape_delta(obj, key, regions, spec)
            created[name] = created.get(name, 0) + 1
    return created


def add_idle_talk_actions(armature: bpy.types.Object) -> list[str]:
    if armature.type != "ARMATURE":
        return []
    actions = []
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 90
    for frame, yaw, chest in ((1, 0, 0), (45, 0.04, -0.02), (90, 0, 0)):
        bpy.context.scene.frame_set(frame)
        for pose_bone in armature.pose.bones:
            pose_bone.rotation_mode = "XYZ"
            pose_bone.rotation_euler = (0, 0, 0)
            if pose_bone.name == "head":
                pose_bone.rotation_euler.z = yaw
            if pose_bone.name == "chest":
                pose_bone.rotation_euler.y = chest
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)
    if armature.animation_data and armature.animation_data.action:
        armature.animation_data.action.name = "idle"
        actions.append("idle")
    bpy.ops.object.mode_set(mode="OBJECT")
    return actions


def neutralize_shape_keys() -> int:
    removed = 0
    for obj in mesh_objects():
        if obj.data.shape_keys:
            count = len(obj.data.shape_keys.key_blocks)
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.shape_key_remove(all=True)
            removed += count
            obj.select_set(False)
    return removed


def export_glb(output: Path, *, export_morph: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_animations=True,
        export_morph=export_morph,
        export_materials="EXPORT",
    )


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    report = Path(args.report).resolve()

    clear_scene()
    import_model(source)
    ensure_materials()
    normalize_info = normalize_scene(args.target_height)
    existing_armatures = armatures()
    armature = existing_armatures[0] if existing_armatures else create_basic_armature(args.target_height, args.front_axis)
    if not existing_armatures:
        bind_meshes_to_armature(armature)
    if args.unsafe_shape_keys:
        removed_shape_keys = 0
        shape_keys = add_heuristic_shape_keys()
    else:
        removed_shape_keys = neutralize_shape_keys()
        shape_keys = {}
    actions = add_idle_talk_actions(armature)
    export_glb(output, export_morph=args.unsafe_shape_keys)

    data = {
        "source": str(source),
        "output": str(output),
        "meshes": [obj.name for obj in mesh_objects()],
        "armature": armature.name if armature else "",
        "created_armature": not bool(existing_armatures),
        "shape_keys": shape_keys,
        "removed_shape_keys": removed_shape_keys,
        "actions": actions,
        "normalize": normalize_info,
        "warnings": [
            "Safe mode does not generate facial shape keys. Use --unsafe-shape-keys only after identifying face-only meshes.",
            "High-quality expressions require clean face topology and manual correction.",
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
