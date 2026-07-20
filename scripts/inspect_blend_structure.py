from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a Blender file structure.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(source))
    objects = []
    for obj in bpy.context.scene.objects:
        entry = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else "",
            "children": [child.name for child in obj.children],
        }
        if obj.type == "MESH":
            entry["vertices"] = len(obj.data.vertices)
            entry["polygons"] = len(obj.data.polygons)
            entry["materials"] = [mat.name for mat in obj.data.materials if mat]
        if obj.type == "ARMATURE":
            entry["bones"] = [
                {
                    "name": bone.name,
                    "parent": bone.parent.name if bone.parent else "",
                    "head": [round(float(v), 5) for v in bone.head_local],
                    "tail": [round(float(v), 5) for v in bone.tail_local],
                }
                for bone in obj.data.bones
            ]
        objects.append(entry)
    data = {
        "source": str(source),
        "objects": objects,
        "armature_count": sum(1 for obj in objects if obj["type"] == "ARMATURE"),
        "mesh_count": sum(1 for obj in objects if obj["type"] == "MESH"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
