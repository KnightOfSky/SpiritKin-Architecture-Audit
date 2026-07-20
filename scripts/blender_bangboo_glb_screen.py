from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add an in-model electronic screen mesh to the Bangboo GLB.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def material_bounds(material_name: str) -> dict[str, object]:
    points: list[Vector] = []
    polygon_count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mats = [slot.material.name if slot.material else "" for slot in obj.material_slots]
        for poly in obj.data.polygons:
            if poly.material_index >= len(mats) or mats[poly.material_index] != material_name:
                continue
            polygon_count += 1
            for vertex_index in poly.vertices:
                points.append(obj.matrix_world @ obj.data.vertices[vertex_index].co)
    if not points:
        raise RuntimeError(f"material not found: {material_name}")
    mins = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maxs = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mins + maxs) * 0.5
    size = maxs - mins
    return {
        "min": [round(float(v), 5) for v in mins],
        "max": [round(float(v), 5) for v in maxs],
        "center": [round(float(v), 5) for v in center],
        "size": [round(float(v), 5) for v in size],
        "polygons": polygon_count,
    }


def object_bounds(obj: bpy.types.Object) -> dict[str, object]:
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


def material_image(material: bpy.types.Material | None) -> bpy.types.Image | None:
    if not material or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeTexImage" and node.image:
            return node.image
    return None


_image_cache: dict[str, tuple[int, int, list[float]]] = {}


def sample_image(image: bpy.types.Image | None, uv: Vector) -> tuple[float, float, float, float] | None:
    if image is None:
        return None
    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    if image.name not in _image_cache:
        _image_cache[image.name] = (width, height, list(image.pixels[:]))
    width, height, pixels = _image_cache[image.name]
    u = float(uv.x) % 1.0
    v = float(uv.y) % 1.0
    x = min(width - 1, max(0, int(u * width)))
    y = min(height - 1, max(0, int(v * height)))
    index = (y * width + x) * 4
    return tuple(float(pixels[index + channel]) for channel in range(4))


def average_poly_uv(obj: bpy.types.Object, poly: bpy.types.MeshPolygon) -> Vector | None:
    uv_layer = obj.data.uv_layers.active.data if obj.data.uv_layers.active else None
    if uv_layer is None:
        return None
    uv = Vector((0.0, 0.0))
    for loop_index in poly.loop_indices:
        uv += uv_layer[loop_index].uv
    return uv / max(len(poly.loop_indices), 1)


def in_front_face_region(point: Vector) -> bool:
    return abs(point.x) < 1.7 and -1.75 < point.y < 0.5 and 3.88 < point.z < 5.72


def collect_and_remove_original_eye_faces() -> tuple[list[tuple[float, float, float]], dict[str, int]]:
    eye_points: list[tuple[float, float, float]] = []
    removed: dict[str, int] = {}
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        target_indices = {
            index
            for index, slot in enumerate(obj.material_slots)
            if slot.material and slot.material.name == "Bangboo_Eye_Display"
        }
        if not target_indices:
            continue
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        faces = [face for face in bm.faces if face.material_index in target_indices]
        count = len(faces)
        if not faces:
            bm.free()
            continue
        for face in faces:
            for vert in face.verts:
                world = obj.matrix_world @ vert.co
                eye_points.append((float(world.x), float(world.y), float(world.z)))
        bmesh.ops.delete(bm, geom=faces, context="FACES_ONLY")
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        removed[obj.name] = count
    if not eye_points:
        raise RuntimeError("Bangboo_Eye_Display faces were not found")
    return eye_points, removed


def create_screen_from_original_eye_geometry() -> tuple[bpy.types.Object, dict[str, int], dict[str, object]]:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    removed: dict[str, int] = {}

    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        target_indices = {
            index
            for index, slot in enumerate(obj.material_slots)
            if slot.material and slot.material.name == "Bangboo_Eye_Display"
        }
        if not target_indices:
            continue

        vertex_map: dict[int, int] = {}
        copied_faces = 0
        for poly in obj.data.polygons:
            if poly.material_index not in target_indices:
                continue
            face: list[int] = []
            for vertex_index in poly.vertices:
                if vertex_index not in vertex_map:
                    world = obj.matrix_world @ obj.data.vertices[vertex_index].co
                    vertex_map[vertex_index] = len(verts)
                    verts.append((float(world.x), float(world.y), float(world.z)))
                face.append(vertex_map[vertex_index])
            faces.append(tuple(face))
            copied_faces += 1

        if copied_faces:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            remove_faces = [face for face in bm.faces if face.material_index in target_indices]
            bmesh.ops.delete(bm, geom=remove_faces, context="FACES_ONLY")
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()
            removed[obj.name] = copied_faces

    if not verts or not faces:
        raise RuntimeError("Bangboo_Eye_Display faces were not found")

    mesh = bpy.data.meshes.new("Bangboo_GLBScreen_DisplayMesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    min_x = min(v[0] for v in verts)
    max_x = max(v[0] for v in verts)
    min_z = min(v[2] for v in verts)
    max_z = max(v[2] for v in verts)
    width = max(max_x - min_x, 0.0001)
    height = max(max_z - min_z, 0.0001)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            uv_layer.data[loop_index].uv = ((co.x - min_x) / width, (co.z - min_z) / height)

    obj = bpy.data.objects.new("Bangboo_GLBScreen_Display", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(make_screen_material())
    obj.show_transparent = False
    obj["screen_note"] = "original_bangboo_eye_display_geometry_with_rebuilt_uv"
    obj["screen_vertex_count"] = len(verts)
    obj["screen_face_count"] = len(faces)
    for poly in obj.data.polygons:
        poly.use_smooth = True

    screen_parameters = {
        "source_geometry": "Bangboo_Eye_Display",
        "uv_projection": "world_xz_bounds",
        "geometry_offset": 0,
        "preserves_original_face_outline": True,
    }
    obj["screen_parameters"] = json.dumps(screen_parameters, sort_keys=True)
    return obj, removed, screen_parameters


def make_screen_material() -> bpy.types.Material:
    mat = bpy.data.materials.new("Bangboo_GLBScreen_Display")
    mat.use_nodes = True
    mat.diffuse_color = (0.02, 0.055, 0.01, 1.0)
    for node in mat.node_tree.nodes:
        if node.bl_idname != "ShaderNodeBsdfPrincipled":
            continue
        if "Base Color" in node.inputs:
            node.inputs["Base Color"].default_value = (0.02, 0.055, 0.01, 1.0)
        if "Emission Color" in node.inputs:
            node.inputs["Emission Color"].default_value = (0.42, 1.0, 0.0, 1.0)
        if "Emission Strength" in node.inputs:
            node.inputs["Emission Strength"].default_value = 0.35
        if "Alpha" in node.inputs:
            node.inputs["Alpha"].default_value = 1.0
    mat.blend_method = "OPAQUE"
    mat.use_backface_culling = False
    return mat


def assign_screen_to_dark_face_surface() -> tuple[bpy.types.Object, dict[str, object]]:
    candidates: list[tuple[bpy.types.Object, int, float]] = []
    selected_points: list[Vector] = []
    candidate_count = 0
    threshold = 0.24

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mats = [slot.material for slot in obj.material_slots]
        body_indices = {
            index
            for index, mat in enumerate(mats)
            if mat and mat.name == "Bangboo_Body_Display"
        }
        if not body_indices or not obj.data.uv_layers.active:
            continue
        images = {index: material_image(mats[index]) for index in body_indices}
        for poly in obj.data.polygons:
            if poly.material_index not in body_indices:
                continue
            center = obj.matrix_world @ poly.center
            if not in_front_face_region(center):
                continue
            uv = average_poly_uv(obj, poly)
            rgba = sample_image(images.get(poly.material_index), uv) if uv is not None else None
            if rgba is None:
                continue
            brightness = sum(rgba[:3]) / 3.0
            candidate_count += 1
            if brightness > threshold:
                continue
            candidates.append((obj, poly.index, brightness))

    selected = largest_connected_poly_component(candidates)
    for obj, poly_index, _brightness in selected:
        poly = obj.data.polygons[poly_index]
        for vertex_index in poly.vertices:
            selected_points.append(obj.matrix_world @ obj.data.vertices[vertex_index].co)

    if not selected or not selected_points:
        raise RuntimeError("front dark Bangboo face surface was not found")

    min_x = min(p.x for p in selected_points)
    max_x = max(p.x for p in selected_points)
    min_z = min(p.z for p in selected_points)
    max_z = max(p.z for p in selected_points)
    width = max(max_x - min_x, 0.0001)
    height = max(max_z - min_z, 0.0001)

    screen_material = make_screen_material()
    per_object: dict[str, int] = {}
    representative = selected[0][0]
    selected_by_object: dict[bpy.types.Object, set[int]] = {}
    for obj, poly_index, _brightness in selected:
        selected_by_object.setdefault(obj, set()).add(poly_index)

    for obj, poly_indices in selected_by_object.items():
        obj.data.materials.append(screen_material)
        screen_material_index = len(obj.data.materials) - 1
        uv_layer = obj.data.uv_layers.active.data
        for poly_index in poly_indices:
            poly = obj.data.polygons[poly_index]
            poly.material_index = screen_material_index
            for loop_index in poly.loop_indices:
                vertex_index = obj.data.loops[loop_index].vertex_index
                co = obj.matrix_world @ obj.data.vertices[vertex_index].co
                uv_layer[loop_index].uv = ((co.x - min_x) / width, (co.z - min_z) / height)
            poly.use_smooth = True
        obj.data.update()
        per_object[obj.name] = len(poly_indices)

    screen_parameters = {
        "source_geometry": "Bangboo_Body_Display",
        "surface": "front_dark_body_face_surface",
        "selection_rule": "largest_connected_dark_component_with_forehead_cap",
        "uv_projection": "world_xz_bounds",
        "preserves_original_face_outline": True,
        "dark_threshold": threshold,
        "candidate_face_count": candidate_count,
        "dark_candidate_face_count": len(candidates),
        "selected_face_count": len(selected),
        "selected_objects": per_object,
        "width": round(float(width), 5),
        "height": round(float(height), 5),
        "x_min": round(float(min_x), 5),
        "x_max": round(float(max_x), 5),
        "z_min": round(float(min_z), 5),
        "z_max": round(float(max_z), 5),
    }
    representative["screen_note"] = "body_black_face_surface_reassigned_to_canvas_screen_material"
    representative["screen_parameters"] = json.dumps(screen_parameters, sort_keys=True)
    representative["screen_face_count"] = len(selected)
    representative["screen_vertex_count"] = len({(round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in selected_points})
    return representative, screen_parameters


def largest_connected_poly_component(
    candidates: list[tuple[bpy.types.Object, int, float]],
) -> list[tuple[bpy.types.Object, int, float]]:
    if not candidates:
        return []
    by_object: dict[bpy.types.Object, list[tuple[bpy.types.Object, int, float]]] = {}
    for item in candidates:
        by_object.setdefault(item[0], []).append(item)

    best: list[tuple[bpy.types.Object, int, float]] = []
    for obj, items in by_object.items():
        index_by_poly = {poly_index: idx for idx, (_obj, poly_index, _brightness) in enumerate(items)}
        vertex_to_faces: dict[tuple[float, float, float], list[int]] = {}
        for idx, (_obj, poly_index, _brightness) in enumerate(items):
            poly = obj.data.polygons[poly_index]
            for vertex_index in poly.vertices:
                co = obj.matrix_world @ obj.data.vertices[vertex_index].co
                key = (round(float(co.x), 4), round(float(co.y), 4), round(float(co.z), 4))
                vertex_to_faces.setdefault(key, []).append(idx)

        adjacency = [set() for _ in items]
        for faces in vertex_to_faces.values():
            for face in faces:
                adjacency[face].update(other for other in faces if other != face)

        seen: set[int] = set()
        for start in range(len(items)):
            if start in seen:
                continue
            stack = [start]
            seen.add(start)
            component_indices: list[int] = []
            while stack:
                current = stack.pop()
                component_indices.append(current)
                for next_index in adjacency[current]:
                    if next_index not in seen:
                        seen.add(next_index)
                        stack.append(next_index)
            component = [items[index] for index in component_indices]
            if len(component) > len(best):
                best = component
        # Keep the local variable meaningful for static analyzers.
        _ = index_by_poly
    return best


def create_curved_screen_mesh(bounds: dict[str, object], eye_points: list[tuple[float, float, float]]) -> bpy.types.Object:
    center = Vector(tuple(float(v) for v in bounds["center"]))
    size = Vector(tuple(float(v) for v in bounds["size"]))
    min_y = min(p[1] for p in eye_points)
    width = max(2.86, size.x * 1.32)
    height = max(1.28, size.z * 1.74)
    z_center = center.z + size.z * 0.03
    x_center = center.x
    half_width = width * 0.5
    half_height = height * 0.5
    cols = 104
    rows = 38
    front_y = min_y - 0.13
    side_recede = 0.4
    vertical_recede = 0.075
    surface_offset = 0.025
    screen_parameters = {
        "width": round(float(width), 5),
        "height": round(float(height), 5),
        "z_center": round(float(z_center), 5),
        "x_center": round(float(x_center), 5),
        "front_y": round(float(front_y), 5),
        "side_recede": round(float(side_recede), 5),
        "vertical_recede": round(float(vertical_recede), 5),
        "surface_offset": round(float(surface_offset), 5),
        "cols": cols,
        "rows": rows,
        "surface": "compact_arch_front_visor_curve",
    }

    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []

    def visor_mask(x_rel: float, z_rel: float) -> bool:
        nx = abs(x_rel) / max(half_width, 0.0001)
        nz = z_rel / max(half_height, 0.0001)
        if nx > 1.0:
            return False
        bottom = -0.86 + 0.11 * (nx**1.55)
        top = 0.44 + 0.43 * (max(0.0, 1.0 - nx**2) ** 0.58)
        if nz < bottom or nz > top:
            return False
        return True

    def fitted_surface_y(x: float, z: float, fallback: float) -> float:
        return fallback - surface_offset

    grid: list[list[int | None]] = []
    for row in range(rows + 1):
        v = row / rows
        z_rel = (v - 0.5) * height
        grid_row: list[int | None] = []
        for col in range(cols + 1):
            u = col / cols
            x_rel = (u - 0.5) * width
            if not visor_mask(x_rel, z_rel):
                grid_row.append(None)
                continue
            x = x_center + x_rel
            z = z_center + z_rel
            nx = x_rel / max(half_width, 0.0001)
            nz = z_rel / max(half_height, 0.0001)
            side_curve = side_recede * (abs(nx) ** 1.75)
            top_curve = vertical_recede * max(nz + 0.18, 0.0) ** 2
            lower_curve = 0.018 * max(-nz - 0.48, 0.0) ** 2
            regular_y = front_y + side_curve + top_curve + lower_curve
            y = fitted_surface_y(x, z, regular_y)
            verts.append((x, y, z))
            uvs.append((u, v))
            grid_row.append(len(verts) - 1)
        grid.append(grid_row)

    faces = []
    for row in range(rows):
        for col in range(cols):
            a, b, c, d = grid[row][col], grid[row][col + 1], grid[row + 1][col + 1], grid[row + 1][col]
            if None not in (a, b, c, d):
                faces.append((a, b, c, d))

    mesh = bpy.data.meshes.new("Bangboo_GLBScreen_DisplayMesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            uv_layer.data[loop_index].uv = uvs[mesh.loops[loop_index].vertex_index]

    obj = bpy.data.objects.new("Bangboo_GLBScreen_Display", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(make_screen_material())
    obj.show_transparent = False
    obj["screen_note"] = "compact_arch_panel_sits_inside_bangboo_black_face_region"
    obj["screen_parameters"] = json.dumps(screen_parameters, sort_keys=True)
    obj["screen_vertex_count"] = len(verts)
    obj["screen_face_count"] = len(faces)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def export_glb(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
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
    bpy.ops.import_scene.gltf(filepath=str(source))
    original_eye_bounds = material_bounds("Bangboo_Eye_Display")
    eye_points, removed_eye_faces = collect_and_remove_original_eye_faces()
    screen_obj, screen_parameters = assign_screen_to_dark_face_surface()
    export_glb(output)

    data = {
        "source": str(source),
        "output": str(output),
        "original_eye_bounds": original_eye_bounds,
        "removed_eye_faces": removed_eye_faces,
        "screen_mode": "front_dark_body_face_surface",
        "sampled_eye_points": len(eye_points),
        "screen_object": screen_obj.name,
        "screen_material": "Bangboo_GLBScreen_Display",
        "screen_parameters": screen_parameters,
        "screen_bounds": material_bounds("Bangboo_GLBScreen_Display"),
        "screen_vertex_count": int(screen_obj.get("screen_vertex_count", 0)),
        "screen_face_count": int(screen_obj.get("screen_face_count", 0)),
        "mesh_count": sum(1 for obj in bpy.context.scene.objects if obj.type == "MESH"),
        "armature_count": sum(1 for obj in bpy.context.scene.objects if obj.type == "ARMATURE"),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
