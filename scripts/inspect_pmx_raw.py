from __future__ import annotations

import argparse
import importlib.util
import json
import logging
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read PMX structure without importing it into Blender.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--pmx-module", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def vec(values) -> list[float]:
    return [round(float(v), 5) for v in values]


def resolve_name(items, index: int | None) -> str:
    if index is None or index < 0 or index >= len(items):
        return ""
    return getattr(items[index], "name", "")


def load_pmx_module(path: Path):
    spec = importlib.util.spec_from_file_location("mmd_tools_raw_pmx", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load PMX module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    pmx_module = Path(args.pmx_module).resolve()
    report = Path(args.report).resolve()
    logging.basicConfig(level=logging.WARNING)

    pmx = load_pmx_module(pmx_module)
    model = pmx.load(str(source))
    texture_paths = [texture.path for texture in model.textures]

    weight_types = Counter(getattr(vertex.weight, "type", -1) for vertex in model.vertices)
    weighted_bones = Counter()
    for vertex in model.vertices:
        for bone_index in getattr(vertex.weight, "bones", []) or []:
            if isinstance(bone_index, int) and 0 <= bone_index < len(model.bones):
                weighted_bones[model.bones[bone_index].name] += 1

    materials = []
    for material in model.materials:
        materials.append(
            {
                "name": material.name,
                "name_e": material.name_e,
                "texture": texture_paths[material.texture] if material.texture != -1 else "",
                "sphere_texture": texture_paths[material.sphere_texture] if material.sphere_texture != -1 else "",
                "vertex_count": material.vertex_count,
                "diffuse": vec(material.diffuse),
                "ambient": vec(material.ambient),
                "double_sided": bool(material.is_double_sided),
                "edge_enabled": bool(material.enabled_toon_edge),
                "edge_size": round(float(material.edge_size), 5),
            }
        )

    bones = []
    for index, bone in enumerate(model.bones):
        bones.append(
            {
                "index": index,
                "name": bone.name,
                "name_e": bone.name_e,
                "parent": resolve_name(model.bones, bone.parent),
                "location": vec(bone.location),
                "rotatable": bool(bone.isRotatable),
                "movable": bool(bone.isMovable),
                "visible": bool(bone.visible),
                "controllable": bool(bone.isControllable),
                "ik": bool(bone.isIK),
                "ik_target": resolve_name(model.bones, getattr(bone, "target", None)) if bone.isIK else "",
                "ik_links": [
                    resolve_name(model.bones, link.target)
                    for link in getattr(bone, "ik_links", [])
                ],
                "additional_transform": (
                    {
                        "bone": resolve_name(model.bones, bone.additionalTransform[0]),
                        "influence": round(float(bone.additionalTransform[1]), 5),
                    }
                    if bone.additionalTransform is not None
                    else None
                ),
            }
        )

    morph_type_names = {
        0: "group",
        1: "vertex",
        2: "bone",
        3: "uv",
        4: "uv1",
        5: "uv2",
        6: "uv3",
        7: "uv4",
        8: "material",
    }
    morphs = [
        {
            "name": morph.name,
            "name_e": morph.name_e,
            "category": morph.category,
            "type": morph_type_names.get(morph.type_index(), str(morph.type_index())),
            "offset_count": len(morph.offsets),
        }
        for morph in model.morphs
    ]

    rigid_shape_names = {0: "sphere", 1: "box", 2: "capsule"}
    rigid_mode_names = {0: "static", 1: "dynamic", 2: "dynamic_bone"}
    rigids = []
    for index, rigid in enumerate(model.rigids):
        rigids.append(
            {
                "index": index,
                "name": rigid.name,
                "name_e": rigid.name_e,
                "bone": resolve_name(model.bones, rigid.bone),
                "shape": rigid_shape_names.get(rigid.type, str(rigid.type)),
                "mode": rigid_mode_names.get(rigid.mode, str(rigid.mode)),
                "size": vec(rigid.size),
                "location": vec(rigid.location),
                "rotation": vec(rigid.rotation),
                "mass": round(float(rigid.mass), 5),
                "velocity_attenuation": round(float(rigid.velocity_attenuation), 5),
                "rotation_attenuation": round(float(rigid.rotation_attenuation), 5),
                "bounce": round(float(rigid.bounce), 5),
                "friction": round(float(rigid.friction), 5),
            }
        )

    joints = []
    for index, joint in enumerate(model.joints):
        joints.append(
            {
                "index": index,
                "name": joint.name,
                "src": resolve_name(model.rigids, joint.src_rigid),
                "dest": resolve_name(model.rigids, joint.dest_rigid),
                "location": vec(joint.location),
                "min_location": vec(joint.minimum_location),
                "max_location": vec(joint.maximum_location),
                "min_rotation": vec(joint.minimum_rotation),
                "max_rotation": vec(joint.maximum_rotation),
                "spring": vec(joint.spring_constant),
                "spring_rotation": vec(joint.spring_rotation_constant),
            }
        )

    data = {
        "source": str(source),
        "model_name": model.name,
        "model_name_e": model.name_e,
        "comment": model.comment,
        "counts": {
            "vertices": len(model.vertices),
            "faces": len(model.faces),
            "textures": len(model.textures),
            "materials": len(model.materials),
            "bones": len(model.bones),
            "morphs": len(model.morphs),
            "display": len(model.display),
            "rigids": len(model.rigids),
            "joints": len(model.joints),
        },
        "textures": texture_paths,
        "materials": materials,
        "bones": bones,
        "morphs": morphs,
        "rigids": rigids,
        "joints": joints,
        "weight_types": dict(sorted(weight_types.items())),
        "weighted_bones_top": weighted_bones.most_common(80),
    }

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["counts"], ensure_ascii=False))
    print("report=", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
