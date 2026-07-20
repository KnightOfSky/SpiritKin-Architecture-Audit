from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HEAD_PARTS = [
    "part_0",
    "part_4",
    "part_5",
    "part_6",
    "part_7",
    "part_8",
    "part_9",
    "part_10",
    "part_11",
    "part_12",
    "part_13",
    "part_14",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create SpiritKin object controls with coarse leg split.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--blend-output", default="")
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


def bake_and_remove_armatures() -> None:
    for obj in mesh_objects():
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        for modifier in list(obj.modifiers):
            if modifier.type == "ARMATURE":
                try:
                    bpy.ops.object.modifier_apply(modifier=modifier.name)
                except Exception:
                    obj.modifiers.remove(modifier)
        for group in list(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        obj.parent = None
        obj.select_set(False)
    for obj in list(bpy.context.scene.objects):
        if obj.type == "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)


def bounds_for(meshes: list[bpy.types.Object]) -> tuple[Vector, Vector, Vector]:
    points: list[Vector] = []
    for obj in meshes:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mins + maxs) * 0.5
    return mins, maxs, center


def clone_filtered_mesh(source: bpy.types.Object, name: str, keep_face) -> bpy.types.Object:
    src_mesh = source.data
    world = source.matrix_world.copy()
    vertices: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    vertex_map: dict[int, int] = {}

    for polygon in src_mesh.polygons:
        center = source.matrix_world @ polygon.center
        if not keep_face(center):
            continue
        face_indices: list[int] = []
        for index in polygon.vertices:
            if index not in vertex_map:
                vertex_map[index] = len(vertices)
                vertices.append(tuple(src_mesh.vertices[index].co))
            face_indices.append(vertex_map[index])
        if len(face_indices) >= 3:
            faces.append(face_indices)

    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = world
    for material in source.data.materials:
        obj.data.materials.append(material)
    return obj


def split_body_legs(report: dict[str, object]) -> None:
    body = bpy.data.objects.get("part_1")
    if body is None or body.type != "MESH":
        report["leg_split"] = {"warning": "part_1 not found"}
        return

    mins, maxs, center = bounds_for([body])
    lower_cut = mins.z + (maxs.z - mins.z) * 0.46
    center_gap = (maxs.x - mins.x) * 0.08

    left_leg = clone_filtered_mesh(body, "part_left_leg", lambda c: c.z <= lower_cut and c.x < center.x - center_gap)
    right_leg = clone_filtered_mesh(body, "part_right_leg", lambda c: c.z <= lower_cut and c.x > center.x + center_gap)
    body_core = clone_filtered_mesh(
        body,
        "part_body_core",
        lambda c: not (c.z <= lower_cut and abs(c.x - center.x) > center_gap),
    )

    bpy.data.objects.remove(body, do_unlink=True)
    report["leg_split"] = {
        "source": "part_1",
        "lower_cut": round(float(lower_cut), 5),
        "center_gap": round(float(center_gap), 5),
        "objects": {
            "part_body_core": len(body_core.data.polygons),
            "part_left_leg": len(left_leg.data.polygons),
            "part_right_leg": len(right_leg.data.polygons),
        },
    }


def group_pivot(name: str, mins: Vector, maxs: Vector, center: Vector) -> Vector:
    if name == "ctrl_left_arm":
        return Vector((maxs.x, center.y, maxs.z))
    if name == "ctrl_right_arm":
        return Vector((mins.x, center.y, maxs.z))
    if name == "ctrl_body":
        return Vector((center.x, center.y, maxs.z))
    if name in {"ctrl_left_leg", "ctrl_right_leg"}:
        return Vector((center.x, center.y, maxs.z))
    return center


def parent_keep_world(child: bpy.types.Object, parent: bpy.types.Object) -> None:
    matrix_world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = matrix_world


def create_controls() -> dict[str, object]:
    report: dict[str, object] = {}
    split_body_legs(report)
    groups = {
        "ctrl_head_assembly": HEAD_PARTS,
        "ctrl_body": ["part_body_core"],
        "ctrl_left_arm": ["part_2"],
        "ctrl_right_arm": ["part_3"],
        "ctrl_left_leg": ["part_left_leg"],
        "ctrl_right_leg": ["part_right_leg"],
    }
    meshes = {obj.name: obj for obj in mesh_objects()}
    root = bpy.data.objects.new("ctrl_avatar_root", None)
    bpy.context.collection.objects.link(root)

    control_report: dict[str, dict[str, object]] = {}
    for group_name, names in groups.items():
        group_meshes = [meshes[name] for name in names if name in meshes]
        if not group_meshes:
            control_report[group_name] = {"meshes": [], "warning": "no matching meshes"}
            continue
        mins, maxs, center = bounds_for(group_meshes)
        ctrl = bpy.data.objects.new(group_name, None)
        ctrl.empty_display_type = "PLAIN_AXES"
        ctrl.empty_display_size = 0.08
        ctrl.location = group_pivot(group_name, mins, maxs, center)
        bpy.context.collection.objects.link(ctrl)
        ctrl.parent = root
        for mesh in group_meshes:
            parent_keep_world(mesh, ctrl)
        control_report[group_name] = {
            "meshes": [mesh.name for mesh in group_meshes],
            "pivot": [round(float(v), 5) for v in ctrl.location],
            "bounds": {
                "min": [round(float(v), 5) for v in mins],
                "max": [round(float(v), 5) for v in maxs],
            },
        }

    for obj in mesh_objects():
        if obj.parent is None:
            parent_keep_world(obj, root)
    report["controls"] = control_report
    return report


def export_glb(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        export_yup=True,
        export_apply=False,
        export_animations=False,
        export_morph=False,
        export_materials="EXPORT",
    )


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    clear_scene()
    import_model(source)
    bake_and_remove_armatures()
    report = create_controls()
    export_glb(output)
    blend_output = Path(args.blend_output).resolve() if args.blend_output.strip() else None
    if blend_output:
        blend_output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output))
    data = {
        "source": str(source),
        "output": str(output),
        "blend_output": str(blend_output) if blend_output else "",
        **report,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
