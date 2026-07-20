from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import an MMD PMX model and write structure diagnostics.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--addon-root", default="")
    parser.add_argument("--blend-output", default="")
    parser.add_argument("--glb-output", default="")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def has_mmd_import_operator() -> bool:
    try:
        bpy.ops.mmd_tools.import_model.get_rna_type()
        return True
    except Exception:
        return False


def enable_mmd_tools(addon_root: str) -> None:
    module_names = ("bl_ext.blender_org.mmd_tools", "mmd_tools")
    for module_name in module_names:
        try:
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception:
            pass
        if has_mmd_import_operator():
            return

    if addon_root:
        root = str(Path(addon_root).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        import mmd_tools  # type: ignore

        try:
            mmd_tools.register()
        except Exception:
            # Blender raises if classes are already registered; the operator may still be usable.
            pass
        if has_mmd_import_operator():
            return

    raise RuntimeError("mmd_tools import operator is not available")


def bounds_for(obj: bpy.types.Object) -> dict[str, list[float]]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mins + maxs) * 0.5
    size = maxs - mins
    return {
        "min": [round(float(v), 5) for v in mins],
        "max": [round(float(v), 5) for v in maxs],
        "center": [round(float(v), 5) for v in center],
        "size": [round(float(v), 5) for v in size],
    }


def rigid_body_for(obj: bpy.types.Object) -> dict[str, object] | None:
    rb = obj.rigid_body
    if not rb:
        return None
    return {
        "name": obj.name,
        "object_type": obj.type,
        "rigid_type": rb.type,
        "collision_shape": rb.collision_shape,
        "mass": round(float(rb.mass), 5),
        "kinematic": bool(rb.kinematic),
    }


def joint_for(obj: bpy.types.Object) -> dict[str, object] | None:
    constraint = obj.rigid_body_constraint
    if not constraint:
        return None
    return {
        "name": obj.name,
        "object_type": obj.type,
        "constraint_type": constraint.type,
        "object1": constraint.object1.name if constraint.object1 else "",
        "object2": constraint.object2.name if constraint.object2 else "",
    }


def material_info(material: bpy.types.Material) -> dict[str, object]:
    textures: list[dict[str, str]] = []
    if material.node_tree:
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexImage" and getattr(node, "image", None):
                image = node.image
                textures.append(
                    {
                        "node": node.name,
                        "image": image.name,
                        "filepath": bpy.path.abspath(image.filepath) if image.filepath else "",
                    }
                )
    return {
        "name": material.name,
        "diffuse_color": [round(float(v), 5) for v in material.diffuse_color],
        "use_nodes": bool(material.use_nodes),
        "textures": textures,
    }


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    report = Path(args.report).resolve()
    clear_scene()
    enable_mmd_tools(args.addon_root)
    bpy.ops.mmd_tools.import_model(
        filepath=str(source),
        scale=1.0,
        clean_model=True,
        rename_bones=True,
        types={"MESH", "ARMATURE", "PHYSICS", "DISPLAY", "MORPHS"},
    )

    meshes = []
    armatures = []
    rigid_bodies = []
    joints = []
    materials = {}
    for obj in bpy.context.scene.objects:
        rb = rigid_body_for(obj)
        if rb:
            rigid_bodies.append(rb)
        joint = joint_for(obj)
        if joint:
            joints.append(joint)
        if obj.type == "MESH":
            for mat in obj.data.materials:
                if mat:
                    materials[mat.name] = material_info(mat)
            meshes.append(
                {
                    "name": obj.name,
                    "vertices": len(obj.data.vertices),
                    "polygons": len(obj.data.polygons),
                    "bounds": bounds_for(obj),
                    "rigid_body": rb,
                    "shape_keys": [key.name for key in (obj.data.shape_keys.key_blocks if obj.data.shape_keys else [])][:80],
                    "materials": [mat.name for mat in obj.data.materials if mat],
                    "vertex_groups": [group.name for group in obj.vertex_groups][:80],
                    "modifiers": [modifier.type for modifier in obj.modifiers],
                }
            )
        elif obj.type == "ARMATURE":
            bones = list(obj.data.bones)
            armatures.append(
                {
                    "name": obj.name,
                    "bone_count": len(bones),
                    "bones": [
                        {
                            "name": bone.name,
                            "parent": bone.parent.name if bone.parent else "",
                            "head": [round(float(v), 5) for v in bone.head_local],
                            "tail": [round(float(v), 5) for v in bone.tail_local],
                        }
                        for bone in bones[:160]
                    ],
                }
            )

    data = {
        "source": str(source),
        "objects": len(bpy.context.scene.objects),
        "meshes": meshes,
        "armatures": armatures,
        "materials": list(materials.values()),
        "rigid_bodies": rigid_bodies[:160],
        "joints": joints[:160],
    }

    if args.blend_output.strip():
        blend_output = Path(args.blend_output).resolve()
        blend_output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output))
        data["blend_output"] = str(blend_output)

    if args.glb_output.strip():
        glb_output = Path(args.glb_output).resolve()
        glb_output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(
            filepath=str(glb_output),
            export_format="GLB",
            export_yup=True,
            export_apply=False,
            export_animations=True,
            export_morph=True,
            export_materials="EXPORT",
        )
        data["glb_output"] = str(glb_output)

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
