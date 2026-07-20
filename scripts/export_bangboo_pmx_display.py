from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a display-only textured Bangboo PMX GLB.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--addon-root", default="")
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
    for module_name in ("bl_ext.blender_org.mmd_tools", "mmd_tools"):
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
            pass
        if has_mmd_import_operator():
            return
    raise RuntimeError("mmd_tools import operator is not available")


def make_texture_material(name: str, image_path: Path) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (1, 1, 1, 1)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    image = bpy.data.images.load(str(image_path))
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = image
    links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
    if "Alpha" in image_node.outputs and "Alpha" in bsdf.inputs:
        links.new(image_node.outputs["Alpha"], bsdf.inputs["Alpha"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    mat.blend_method = "BLEND"
    mat.use_screen_refraction = False
    return mat


def remove_helper_meshes() -> list[str]:
    removed: list[str] = []
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        mat_names = [mat.name.lower() for mat in obj.data.materials if mat]
        if obj.name.lower().startswith(tuple(f"{i:03d}_" for i in range(100))) or any("mmd_tools_rigid" in n for n in mat_names):
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
    return removed


def assign_display_materials(texture_root: Path) -> dict[str, object]:
    texture_dir = texture_root / "textures"
    body_tex = texture_dir / "Bangboo_Eous001_Body_D.png"
    eye_tex = texture_dir / "Bangboo_Eous001_Eye_D.png"
    body_mat = make_texture_material("Bangboo_Body_Display", body_tex)
    eye_mat = make_texture_material("Bangboo_Eye_Display", eye_tex)
    assignments: dict[str, list[str]] = {}

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            old_name = slot.material.name if slot.material else ""
            use_eye = "眼" in old_name or "eye" in old_name.lower() or old_name in {"新規.002", "新規.003"}
            slot.material = eye_mat if use_eye else body_mat
            assignments.setdefault(obj.name, []).append(f"{old_name}->{slot.material.name}")
        # Backface issues are common with imported PMX face strips.
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return {
        "body_texture": str(body_tex),
        "eye_texture": str(eye_tex),
        "assignments": assignments,
    }


def export_glb(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    for obj in bpy.context.scene.objects:
        obj.select_set(obj.type in {"MESH", "ARMATURE"})
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        export_yup=True,
        export_apply=False,
        export_animations=False,
        export_morph=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    report = Path(args.report).resolve()
    clear_scene()
    if source.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source))
        texture_root = Path("D:/Edge浏览器下载/1_by_看到结局的人_2d1ce8614b2e1ce62d84b7c2600e6bca/1")
    elif source.suffix.lower() in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source))
        texture_root = Path("D:/Edge浏览器下载/1_by_看到结局的人_2d1ce8614b2e1ce62d84b7c2600e6bca/1")
    else:
        enable_mmd_tools(args.addon_root)
        bpy.ops.mmd_tools.import_model(
            filepath=str(source),
            scale=1.0,
            clean_model=True,
            rename_bones=True,
            types={"MESH", "ARMATURE", "PHYSICS", "DISPLAY", "MORPHS"},
        )
        texture_root = source.parent
    removed = remove_helper_meshes()
    material_report = assign_display_materials(texture_root)
    export_glb(output)
    data = {
        "source": str(source),
        "output": str(output),
        "removed_helper_meshes": removed,
        "mesh_count": sum(1 for obj in bpy.context.scene.objects if obj.type == "MESH"),
        "armature_count": sum(1 for obj in bpy.context.scene.objects if obj.type == "ARMATURE"),
        "materials": material_report,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
