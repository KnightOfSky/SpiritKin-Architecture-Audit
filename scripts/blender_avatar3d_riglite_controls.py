from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bmesh
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
    parser = argparse.ArgumentParser(description="Create a safer rig-lite control GLB for SpiritKin.")
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


def bounds_for(objects: list[bpy.types.Object]) -> tuple[Vector, Vector, Vector, Vector]:
    points: list[Vector] = []
    for obj in objects:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mins + maxs) * 0.5
    size = maxs - mins
    return mins, maxs, center, size


def vector3(v: Vector) -> list[float]:
    return [round(float(v.x), 5), round(float(v.y), 5), round(float(v.z), 5)]


def duplicate_filtered_mesh(source: bpy.types.Object, name: str, keep_face) -> bpy.types.Object | None:
    obj = source.copy()
    obj.data = source.data.copy()
    obj.name = name
    obj.data.name = name + "_mesh"
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = source.matrix_world.copy()

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    matrix = obj.matrix_world.copy()
    delete_faces = [face for face in bm.faces if not keep_face(matrix @ face.calc_center_median())]
    if delete_faces:
        bmesh.ops.delete(bm, geom=delete_faces, context="FACES")
    bm.verts.ensure_lookup_table()
    loose_verts = [vert for vert in bm.verts if not vert.link_faces]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
    face_count = len(bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    if face_count <= 0:
        bpy.data.objects.remove(obj, do_unlink=True)
        return None
    return obj


def split_body_preserve_uv(report: dict[str, object]) -> None:
    body = bpy.data.objects.get("part_1")
    if body is None or body.type != "MESH":
        report["body_split"] = {"warning": "part_1 not found"}
        return

    mins, maxs, center, size = bounds_for([body])
    leg_cut = mins.z + size.z * 0.43
    center_gap = size.x * 0.055

    left_leg = duplicate_filtered_mesh(
        body,
        "part_left_leg",
        lambda c: c.z <= leg_cut and c.x < center.x - center_gap,
    )
    right_leg = duplicate_filtered_mesh(
        body,
        "part_right_leg",
        lambda c: c.z <= leg_cut and c.x > center.x + center_gap,
    )
    body_core = duplicate_filtered_mesh(
        body,
        "part_body_core",
        lambda c: not (c.z <= leg_cut and abs(c.x - center.x) > center_gap),
    )

    bpy.data.objects.remove(body, do_unlink=True)
    report["body_split"] = {
        "source": "part_1",
        "leg_cut": round(float(leg_cut), 5),
        "center_gap": round(float(center_gap), 5),
        "objects": {
            "part_body_core": len(body_core.data.polygons) if body_core else 0,
            "part_left_leg": len(left_leg.data.polygons) if left_leg else 0,
            "part_right_leg": len(right_leg.data.polygons) if right_leg else 0,
        },
    }


def material_for(name: str) -> bpy.types.Material | None:
    obj = bpy.data.objects.get(name)
    if obj and obj.type == "MESH" and obj.data.materials:
        return obj.data.materials[0]
    return None


def add_socket(name: str, location: Vector, radius: float, material: bpy.types.Material | None) -> bpy.types.Object:
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name + "_mesh"
    obj.scale.y *= 0.72
    if material:
        obj.data.materials.append(material)
    return obj


def add_shoulder_sockets(report: dict[str, object]) -> list[str]:
    meshes = {obj.name: obj for obj in mesh_objects()}
    left = meshes.get("part_2")
    right = meshes.get("part_3")
    material = material_for("part_body_core") or material_for("part_0")
    sockets: list[str] = []
    if left:
        mins, maxs, center, size = bounds_for([left])
        loc = Vector((maxs.x, center.y, maxs.z - size.z * 0.08))
        sockets.append(add_socket("part_left_shoulder_socket", loc, max(size.x, size.y, size.z) * 0.18, material).name)
    if right:
        mins, maxs, center, size = bounds_for([right])
        loc = Vector((mins.x, center.y, maxs.z - size.z * 0.08))
        sockets.append(add_socket("part_right_shoulder_socket", loc, max(size.x, size.y, size.z) * 0.18, material).name)
    report["shoulder_sockets"] = sockets
    return sockets


def group_pivot(name: str, meshes: list[bpy.types.Object]) -> Vector:
    mins, maxs, center, size = bounds_for(meshes)
    if name == "ctrl_head_assembly":
        return Vector((center.x, center.y, mins.z + size.z * 0.08))
    if name == "ctrl_left_arm":
        return Vector((maxs.x, center.y, maxs.z - size.z * 0.08))
    if name == "ctrl_right_arm":
        return Vector((mins.x, center.y, maxs.z - size.z * 0.08))
    if name in {"ctrl_left_leg", "ctrl_right_leg"}:
        return Vector((center.x, center.y, maxs.z - size.z * 0.05))
    if name == "ctrl_body":
        return Vector((center.x, center.y, mins.z + size.z * 0.58))
    return center


def parent_keep_world(child: bpy.types.Object, parent: bpy.types.Object) -> None:
    matrix_world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = matrix_world


def create_controls(report: dict[str, object], sockets: list[str]) -> None:
    groups = {
        "ctrl_head_assembly": HEAD_PARTS,
        "ctrl_body": ["part_body_core", *sockets],
        "ctrl_left_arm": ["part_2"],
        "ctrl_right_arm": ["part_3"],
        "ctrl_left_leg": ["part_left_leg"],
        "ctrl_right_leg": ["part_right_leg"],
    }
    meshes = {obj.name: obj for obj in mesh_objects()}
    root = bpy.data.objects.new("ctrl_avatar_root", None)
    bpy.context.collection.objects.link(root)

    control_report: dict[str, object] = {}
    for group_name, names in groups.items():
        group_meshes = [meshes[name] for name in names if name in meshes]
        if not group_meshes:
            control_report[group_name] = {"meshes": [], "warning": "no matching meshes"}
            continue
        mins, maxs, center, size = bounds_for(group_meshes)
        ctrl = bpy.data.objects.new(group_name, None)
        ctrl.empty_display_type = "PLAIN_AXES"
        ctrl.empty_display_size = 0.08
        ctrl.location = group_pivot(group_name, group_meshes)
        bpy.context.collection.objects.link(ctrl)
        ctrl.parent = root
        for mesh in group_meshes:
            parent_keep_world(mesh, ctrl)
        control_report[group_name] = {
            "meshes": [mesh.name for mesh in group_meshes],
            "pivot": vector3(ctrl.location),
            "bounds": {
                "min": vector3(mins),
                "max": vector3(maxs),
                "center": vector3(center),
                "size": vector3(size),
            },
        }

    for obj in mesh_objects():
        if obj.parent is None:
            parent_keep_world(obj, root)
    report["controls"] = control_report


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
    report: dict[str, object] = {}
    split_body_preserve_uv(report)
    sockets = add_shoulder_sockets(report)
    create_controls(report, sockets)
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
