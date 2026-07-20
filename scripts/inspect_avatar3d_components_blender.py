from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect loose mesh components in an avatar model.")
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


def component_indices(mesh: bpy.types.Mesh) -> list[list[int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].add(b)
        adjacency[b].add(a)
    remaining = set(range(len(mesh.vertices)))
    components: list[list[int]] = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        component = [start]
        while queue:
            current = queue.popleft()
            for other in adjacency[current]:
                if other in remaining:
                    remaining.remove(other)
                    queue.append(other)
                    component.append(other)
        components.append(component)
    return sorted(components, key=len, reverse=True)


def bounds_for_vertices(obj: bpy.types.Object, indices: list[int]) -> dict[str, object]:
    points = [obj.matrix_world @ obj.data.vertices[index].co for index in indices]
    mins = [min(getattr(point, axis) for point in points) for axis in ("x", "y", "z")]
    maxs = [max(getattr(point, axis) for point in points) for axis in ("x", "y", "z")]
    center = [(mins[index] + maxs[index]) / 2 for index in range(3)]
    size = [maxs[index] - mins[index] for index in range(3)]
    return {
        "min": [round(float(v), 5) for v in mins],
        "max": [round(float(v), 5) for v in maxs],
        "center": [round(float(v), 5) for v in center],
        "size": [round(float(v), 5) for v in size],
    }


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    clear_scene()
    import_model(source)
    rows = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        components = component_indices(obj.data)
        rows.append(
            {
                "mesh": obj.name,
                "vertices": len(obj.data.vertices),
                "component_count": len(components),
                "components": [
                    {
                        "index": index,
                        "vertices": len(component),
                        "ratio": round(len(component) / max(1, len(obj.data.vertices)), 4),
                        "bounds": bounds_for_vertices(obj, component),
                    }
                    for index, component in enumerate(components[:24])
                ],
            }
        )
    report = {"source": str(source), "meshes": rows}
    path = Path(args.report).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
