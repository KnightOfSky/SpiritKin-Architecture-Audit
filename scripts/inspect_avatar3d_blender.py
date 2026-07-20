from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect an avatar model inside Blender.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--report", required=True)
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


def mesh_report(obj: bpy.types.Object) -> dict[str, object]:
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    if points:
        mins = [min(getattr(point, axis) for point in points) for axis in ("x", "y", "z")]
        maxs = [max(getattr(point, axis) for point in points) for axis in ("x", "y", "z")]
        center = [(mins[index] + maxs[index]) / 2 for index in range(3)]
        size = [maxs[index] - mins[index] for index in range(3)]
    else:
        mins = maxs = center = size = [0.0, 0.0, 0.0]
    vertex_group_names = [group.name for group in obj.vertex_groups]
    weighted_vertices = 0
    group_usage: dict[str, int] = {name: 0 for name in vertex_group_names}
    for vertex in obj.data.vertices:
        if vertex.groups:
            weighted_vertices += 1
        for group_ref in vertex.groups:
            if 0 <= group_ref.group < len(vertex_group_names):
                group_usage[vertex_group_names[group_ref.group]] += 1
    armature_modifiers = [
        {
            "name": modifier.name,
            "object": modifier.object.name if getattr(modifier, "object", None) else "",
        }
        for modifier in obj.modifiers
        if modifier.type == "ARMATURE"
    ]
    return {
        "name": obj.name,
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "bounds": {
            "min": [round(float(v), 5) for v in mins],
            "max": [round(float(v), 5) for v in maxs],
            "center": [round(float(v), 5) for v in center],
            "size": [round(float(v), 5) for v in size],
        },
        "materials": [slot.material.name if slot.material else "" for slot in obj.material_slots],
        "vertex_groups": vertex_group_names,
        "weighted_vertices": weighted_vertices,
        "weighted_ratio": round(weighted_vertices / max(1, len(obj.data.vertices)), 4),
        "used_vertex_groups": {name: count for name, count in group_usage.items() if count},
        "shape_keys": list(obj.data.shape_keys.key_blocks.keys()) if obj.data.shape_keys else [],
        "armature_modifiers": armature_modifiers,
    }


def bone_influence_report(meshes: list[bpy.types.Object]) -> dict[str, dict[str, object]]:
    accum: dict[str, dict[str, object]] = {}
    for obj in meshes:
        group_names = [group.name for group in obj.vertex_groups]
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            for group_ref in vertex.groups:
                if not (0 <= group_ref.group < len(group_names)):
                    continue
                name = group_names[group_ref.group]
                entry = accum.setdefault(
                    name,
                    {
                        "count": 0,
                        "weight_sum": 0.0,
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.0,
                        "min": [world.x, world.y, world.z],
                        "max": [world.x, world.y, world.z],
                        "meshes": {},
                    },
                )
                weight = float(group_ref.weight)
                entry["count"] = int(entry["count"]) + 1
                entry["weight_sum"] = float(entry["weight_sum"]) + weight
                entry["x"] = float(entry["x"]) + world.x * weight
                entry["y"] = float(entry["y"]) + world.y * weight
                entry["z"] = float(entry["z"]) + world.z * weight
                entry["min"] = [min(entry["min"][0], world.x), min(entry["min"][1], world.y), min(entry["min"][2], world.z)]
                entry["max"] = [max(entry["max"][0], world.x), max(entry["max"][1], world.y), max(entry["max"][2], world.z)]
                entry["meshes"][obj.name] = int(entry["meshes"].get(obj.name, 0)) + 1
    report: dict[str, dict[str, object]] = {}
    for name, entry in accum.items():
        weight_sum = max(float(entry["weight_sum"]), 0.000001)
        report[name] = {
            "count": entry["count"],
            "weight_sum": round(float(entry["weight_sum"]), 4),
            "centroid": [round(float(entry["x"]) / weight_sum, 5), round(float(entry["y"]) / weight_sum, 5), round(float(entry["z"]) / weight_sum, 5)],
            "min": [round(float(v), 5) for v in entry["min"]],
            "max": [round(float(v), 5) for v in entry["max"]],
            "top_meshes": sorted(entry["meshes"].items(), key=lambda item: item[1], reverse=True)[:6],
        }
    return report


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    report = Path(args.report).resolve()
    clear_scene()
    import_model(source)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    data = {
        "source": str(source),
        "meshes": [mesh_report(obj) for obj in meshes],
        "armatures": [
            {
                "name": obj.name,
                "bones": [
                    {
                        "name": bone.name,
                        "parent": bone.parent.name if bone.parent else "",
                        "head_local": [round(float(v), 5) for v in bone.head_local],
                        "tail_local": [round(float(v), 5) for v in bone.tail_local],
                    }
                    for bone in obj.data.bones
                ],
                "bone_count": len(obj.data.bones),
            }
            for obj in armatures
        ],
        "bone_influences": bone_influence_report(meshes),
        "objects": [{"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else ""} for obj in bpy.context.scene.objects],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
