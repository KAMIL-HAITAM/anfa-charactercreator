"""Build refined ANFA-specific garment prototypes from CharacterCreator's rigged base mesh.

Run from the CharacterCreator project root in PowerShell:
  & $blender -b build\\blend\\base_body.blend --python blender\\tools\\build_anfa_garments.py

Outputs two skinned/morph-capable GLBs and adds them to output/clothing.json:
  clothing_anfa_core_form_tee.glb
  clothing_anfa_architectural_rib_leggings.glb

These are surface-derived garment prototypes with edge-interpolated region clipping, preserved morph targets and
interpolated bone weights. Vertex-group names and source deform weights are snapshotted before replacing the mesh,
then group definitions are restored before assigning weights. They are not final production assets; inspect fit and
refine the patterns before launch.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Vector

PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / "output"
MANIFEST = OUTPUT / "clothing.json"


def report(message: str) -> None:
    print(f"ANFA_GARMENTS {message}", flush=True)


def hex_rgba(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (1.0,)


def find_body_and_rig():
    body = bpy.data.objects.get("Body")
    if not body or body.type != "MESH":
        candidates = [o for o in bpy.data.objects if o.type == "MESH" and o.data.shape_keys]
        candidates.sort(key=lambda o: len(o.data.vertices), reverse=True)
        body = candidates[0] if candidates else None
    if body is None:
        raise RuntimeError("Could not find the rigged Body mesh in build/blend/base_body.blend")
    arm_mod = next((m for m in body.modifiers if m.type == "ARMATURE" and m.object), None)
    if arm_mod is None:
        raise RuntimeError(f"Mesh {body.name!r} has no Armature modifier")
    rig = arm_mod.object
    if not body.data.shape_keys or len(body.data.shape_keys.key_blocks) < 2:
        raise RuntimeError("Body has no exported morph/shape-key set; rebuild base_body.blend first")
    report(f"source body={body.name!r}, vertices={len(body.data.vertices)}, shape_keys={len(body.data.shape_keys.key_blocks)}, rig={rig.name!r}")
    return body, rig


def get_bounds(mesh_obj):
    coords = [v.co for v in mesh_obj.data.vertices]
    min_x = min(v.x for v in coords)
    max_x = max(v.x for v in coords)
    min_y = min(v.y for v in coords)
    max_y = max(v.y for v in coords)
    min_z = min(v.z for v in coords)
    max_z = max(v.z for v in coords)
    height = max_z - min_z
    if height <= 0.5:
        raise RuntimeError(f"Body vertical bounds look wrong: z={min_z:.3f}..{max_z:.3f}")
    return {
        "cx": (min_x + max_x) * 0.5,
        "cy": (min_y + max_y) * 0.5,
        "minx": min_x,
        "maxx": max_x,
        "miny": min_y,
        "maxy": max_y,
        "minz": min_z,
        "maxz": max_z,
        "height": height,
    }


def smooth01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def tee_field(co, b):
    """Signed surface field for a boxier oversized tee with a shallow crew neck and sloping sleeve cuffs.

    Positive values are inside the garment. Distances are in metres so clipping constraints are comparable.
    """
    t = (co.z - b["minz"]) / b["height"]
    ax = abs(co.x - b["cx"])

    torso_half_width = 0.145 + 0.105 * smooth01((t - 0.555) / (0.81 - 0.555))
    # The neckline dips slightly at the centre while the shoulder edge sits higher.
    torso_top = 0.81 - 0.025 * math.exp(-((ax / 0.065) ** 2))
    torso = min(
        (t - 0.555) * b["height"],
        (torso_top - t) * b["height"],
        torso_half_width - ax,
    )

    # Sleeve field slopes down as it moves outward, creating a more natural sleeve opening
    # than deleting faces by their centres in a horizontal height band.
    sleeve_lower = 0.76 - 0.22 * max(0.0, ax - 0.16)
    sleeve_upper = 0.805 - 0.05 * max(0.0, ax - 0.16)
    sleeve = min(
        ax - 0.16,
        0.435 - ax,
        (t - sleeve_lower) * b["height"],
        (sleeve_upper - t) * b["height"],
    )
    return max(torso, sleeve)


def leggings_field(co, b):
    """Signed surface field for a high-waisted, two-leg fitted legging shell."""
    t = (co.z - b["minz"]) / b["height"]
    ax = abs(co.x - b["cx"])

    # Continuous high waist / pelvis panel runs below the crotch so it can blend into both legs.
    hip = min(
        (t - 0.345) * b["height"],
        (0.615 - t) * b["height"],
        0.285 - ax,
    )

    # Slightly wider at the upper thigh and tapered toward the ankle. The inner gap opens
    # gradually down the legs, but remains nearly closed at the crotch join.
    inner_gap = 0.008 + 0.052 * (1.0 - smooth01((t - 0.10) / 0.38))
    outer_edge = 0.12 + 0.135 * smooth01((t - 0.035) / 0.42)
    leg = min(
        (t - 0.035) * b["height"],
        (0.565 - t) * b["height"],
        ax - inner_gap,
        outer_edge - ax,
    )
    return max(hip, leg)


def clip_body_surface_region(obj, rig, field_func, bounds):
    """Clip every source triangle against a continuous signed field.

    Intersections are inserted on triangle edges, instead of keeping/deleting whole faces by
    face-centre tests. This gives clean neckline, sleeve, waist and ankle boundaries while
    carrying all source morph targets and interpolated skin weights onto the new topology.
    """
    source_mesh = obj.data
    source_keys = source_mesh.shape_keys.key_blocks if source_mesh.shape_keys else []
    if len(source_keys) < 2:
        raise RuntimeError(f"{obj.name} source mesh has no morph targets")

    # Save object-level vertex-group names and mesh deform weights BEFORE replacing obj.data.
    # A freshly created mesh has no deform layer; assigning it can make Blender drop the
    # object's vertex-group definitions. The old mesh remains readable, so snapshot both now.
    source_group_names = [group.name for group in obj.vertex_groups]
    source_weight_maps = [
        {int(g.group): float(g.weight) for g in vert.groups}
        for vert in source_mesh.vertices
    ]
    source_weighted = sum(1 for weights in source_weight_maps if weights)
    report(f"{obj.name}: source skin groups={len(source_group_names)}, "
           f"weighted_source_vertices={source_weighted}/{len(source_mesh.vertices)}")
    if not source_group_names or source_weighted < len(source_mesh.vertices) * 0.98:
        raise RuntimeError(
            f"{obj.name} source copy has incomplete skin weights "
            f"(groups={len(source_group_names)}, weighted={source_weighted}/{len(source_mesh.vertices)})"
        )

    # Snapshot the complete shape-key data before replacing the mesh.
    key_data = []
    for key in source_keys:
        coords = np.asarray([tuple(v.co) for v in key.data], dtype=np.float64)
        key_data.append((key.name, coords, key.slider_min, key.slider_max, key.value, key.interpolation))
    basis_coords = key_data[0][1]
    fields = np.asarray([field_func(Vector(co), bounds) for co in basis_coords], dtype=np.float64)

    source_mesh.calc_loop_triangles()
    triangles = [tuple(int(i) for i in tri.vertices) for tri in source_mesh.loop_triangles]
    node_sources = {}
    node_lookup = {}
    new_sources = []
    new_faces = []

    def vertex_key(i):
        return ("v", int(i))

    def intersection_key(i, j):
        fi, fj = fields[i], fields[j]
        if abs(fi) <= 1e-12:
            return vertex_key(i)
        if abs(fj) <= 1e-12:
            return vertex_key(j)
        lo, hi = (i, j) if i < j else (j, i)
        flo, fhi = fields[lo], fields[hi]
        denom = flo - fhi
        if abs(denom) <= 1e-14:
            return vertex_key(lo if abs(flo) < abs(fhi) else hi)
        t = max(0.0, min(1.0, float(flo / denom)))
        key = ("e", lo, hi)
        node_sources[key] = (lo, hi, t)
        return key

    def ensure_node(key):
        if key in node_lookup:
            return node_lookup[key]
        if key[0] == "v":
            src = (key[1], key[1], 0.0)
            node_sources[key] = src
        src = node_sources[key]
        node_lookup[key] = len(new_sources)
        new_sources.append(src)
        return node_lookup[key]

    for tri in triangles:
        if all(fields[i] < 0.0 for i in tri):
            continue
        if all(fields[i] >= 0.0 for i in tri):
            poly_keys = [vertex_key(i) for i in tri]
        else:
            poly_keys = []
            for idx in range(3):
                prev_i = tri[(idx - 1) % 3]
                curr_i = tri[idx]
                prev_in = fields[prev_i] >= 0.0
                curr_in = fields[curr_i] >= 0.0
                if curr_in:
                    if not prev_in:
                        poly_keys.append(intersection_key(prev_i, curr_i))
                    poly_keys.append(vertex_key(curr_i))
                elif prev_in:
                    poly_keys.append(intersection_key(prev_i, curr_i))
            deduped = []
            for key in poly_keys:
                if not deduped or deduped[-1] != key:
                    deduped.append(key)
            if len(deduped) > 1 and deduped[0] == deduped[-1]:
                deduped.pop()
            poly_keys = deduped

        if len(poly_keys) < 3:
            continue
        ids = [ensure_node(key) for key in poly_keys]
        for k in range(1, len(ids) - 1):
            face = (ids[0], ids[k], ids[k + 1])
            if len(set(face)) == 3:
                new_faces.append(face)

    if len(new_faces) < 500 or len(new_sources) < 300:
        raise RuntimeError(f"{obj.name} clipping produced too little geometry: {len(new_sources)} vertices, {len(new_faces)} faces")

    # Build the clipped Basis coordinates using edge interpolation.
    source_pairs = np.asarray([(i, j) for i, j, _ in new_sources], dtype=np.int32)
    mix = np.asarray([t for _, _, t in new_sources], dtype=np.float64)[:, None]
    new_basis = basis_coords[source_pairs[:, 0]] * (1.0 - mix) + basis_coords[source_pairs[:, 1]] * mix

    mesh = bpy.data.meshes.new(obj.name + "_ClippedMesh")
    mesh.from_pydata(new_basis.tolist(), [], new_faces)
    mesh.update(calc_edges=True)
    old_mesh = obj.data
    obj.data = mesh

    # Recreate group definitions after assigning the fresh mesh, which has no deform-vertex layer.
    # Names and indices deliberately retain the source body's order so the snapshotted weights map correctly.
    current_group_names = [group.name for group in obj.vertex_groups]
    if current_group_names != source_group_names:
        for group in list(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        for group_name in source_group_names:
            obj.vertex_groups.new(name=group_name)

    # Rebuild all shape keys with exactly the same names and order as the body.
    for ki, (key_name, coords, slider_min, slider_max, value, interpolation) in enumerate(key_data):
        if ki == 0:
            new_key = obj.shape_key_add(name=key_name, from_mix=False)
        else:
            new_key = obj.shape_key_add(name=key_name, from_mix=False)
        interpolated = coords[source_pairs[:, 0]] * (1.0 - mix) + coords[source_pairs[:, 1]] * mix
        new_key.data.foreach_set("co", interpolated.astype(np.float32).ravel())
        new_key.slider_min = slider_min
        new_key.slider_max = slider_max
        new_key.value = value
        new_key.interpolation = interpolation

    # Interpolate bone weights for inserted vertices. Blender's VertexGroup.add() takes ONE
    # scalar weight per call, not a per-vertex weight array. The previous batched call left the
    # exported garment without a glTF skin, which the exporter reported as "has no skin".
    # Keep at most four influences per vertex (the glTF limit) and normalise them explicitly.
    group_entries = [[] for _ in source_group_names]
    for new_i, (a, b, t) in enumerate(new_sources):
        wa = source_weight_maps[a]
        wb = source_weight_maps[b]
        merged = {}
        for gi, weight in wa.items():
            merged[gi] = merged.get(gi, 0.0) + weight * (1.0 - t)
        for gi, weight in wb.items():
            merged[gi] = merged.get(gi, 0.0) + weight * t

        # Ignore negligible weights and group indices not present on the copied object.
        candidates = [(gi, w) for gi, w in merged.items()
                      if gi < len(source_group_names) and w > 1e-5]
        candidates.sort(key=lambda pair: pair[1], reverse=True)
        candidates = candidates[:4]
        total = sum(w for _, w in candidates)
        if total > 1e-8:
            for gi, weight in candidates:
                group_entries[gi].append((new_i, weight / total))

    # Assign weights one vertex at a time using the documented Blender API.
    for gi, entries in enumerate(group_entries):
        group = obj.vertex_groups[gi]
        for vertex_index, weight in entries:
            group.add([int(vertex_index)], float(weight), "REPLACE")

    # Fail before export rather than silently writing a non-skinned GLB.
    bone_names = {bone.name for bone in rig.data.bones}
    matching_groups = [group.name for group in obj.vertex_groups if group.name in bone_names]
    weighted_vertices = sum(1 for vertex in mesh.vertices if len(vertex.groups) > 0)
    max_influences = max((len(vertex.groups) for vertex in mesh.vertices), default=0)
    armature_modifiers = [m for m in obj.modifiers if m.type == "ARMATURE" and m.object == rig]
    report(f"{obj.name}: skin audit groups={len(obj.vertex_groups)}, bone_groups={len(matching_groups)}, "
           f"weighted_vertices={weighted_vertices}/{len(mesh.vertices)}, max_influences={max_influences}, "
           f"armature_modifiers={len(armature_modifiers)}")
    if not matching_groups or weighted_vertices < len(mesh.vertices) * 0.98 or not armature_modifiers:
        raise RuntimeError(f"{obj.name} did not receive complete armature weights; refusing to export unskinned GLB")

    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.update()
    # The copy mesh is now superseded; removing this orphan also drops its shape-key datablock.
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    report(f"{obj.name}: clipped clean boundaries, {len(new_sources)} vertices / {len(new_faces)} faces; preserved {len(key_data)} shape keys")


def create_material(name, color, roughness):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = hex_rgba(color)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = hex_rgba(color)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = 0.0
    mat.use_backface_culling = False
    return mat


def glyph_rows(char):
    # Small bitmap font for a restrained tonal ANFA mark, baked into the garment UV texture.
    font = {
        "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
        "N": ("10001", "11001", "10101", "10101", "10011", "10011", "10001"),
        "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
        "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
        "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
        "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
        "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
        "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
        "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
        "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    }
    return font.get(char, ("00000",) * 7)


def paint_bitmap(img, text, center_x, top_y, scale, tone):
    h, w, _ = img.shape
    patterns = [glyph_rows(ch) for ch in text]
    letter_w = 5 * scale
    spacing = max(1, scale // 2)
    total_w = len(patterns) * letter_w + (len(patterns) - 1) * spacing
    start_x = int(center_x - total_w / 2)
    for pi, rows in enumerate(patterns):
        for ry, row in enumerate(rows):
            for rx, on in enumerate(row):
                if on != "1":
                    continue
                x0 = start_x + pi * (letter_w + spacing) + rx * scale
                y0 = top_y + ry * scale
                x1, y1 = min(w, x0 + scale), min(h, y0 + scale)
                if x1 > 0 and y1 > 0 and x0 < w and y0 < h:
                    img[max(0, y0):y1, max(0, x0):x1, :3] = tone


def make_garment_texture(kind, path):
    size = 1024
    yy, xx = np.mgrid[0:size, 0:size]
    # Neutral greyscale texture: the runtime's colour picker tints this map. Logo and detail values stay tonal.
    grain = 0.012 * np.sin(xx * 0.19 + yy * 0.07) + 0.008 * np.sin(xx * 0.043 - yy * 0.11)
    base = np.clip(0.78 + grain, 0.68, 0.9)
    arr = np.empty((size, size, 4), dtype=np.float32)
    arr[..., 0] = base
    arr[..., 1] = base
    arr[..., 2] = base
    arr[..., 3] = 1.0

    def row_for_t(t):
        v = 0.05 + max(0.0, min(1.0, t)) * 0.9
        return int((1.0 - v) * size)

    def u_span(is_front):
        return (0.05, 0.45) if is_front else (0.55, 0.95)

    if kind == "tee":
        # Low-contrast hem stitch and restrained front-chest wordmark, like Core Form's tonal branding.
        for front in (True, False):
            u0, u1 = u_span(front)
            x0, x1 = int(u0 * size), int(u1 * size)
            for t, val in ((0.594, 0.64), (0.598, 0.70)):
                py = row_for_t(t)
                arr[max(0, py - 1):min(size, py + 1), x0:x1, :3] = val
        center = int(0.25 * size)
        y_top = row_for_t(0.754) - 14
        paint_bitmap(arr, "ANFA", center, y_top, 4, (0.93, 0.93, 0.93))
        # Tiny geometric A marker below the wordmark, kept deliberately subtle.
        mark_y = y_top + 34
        cx = center
        for k in range(12):
            lx = cx - 10 + k
            rx = cx + 10 - k
            py = mark_y + k
            if 0 <= py < size:
                if 0 <= lx < size: arr[py, lx:lx + 2, :3] = 0.90
                if 0 <= rx < size: arr[py, rx:rx + 2, :3] = 0.90
        if 0 <= mark_y + 8 < size:
            arr[mark_y + 8:mark_y + 10, cx - 5:cx + 6, :3] = 0.90
    else:
        # Architectural Rib: a slightly structured high waistband and narrow, vertical outer-leg rib channels.
        t_low, t_high = 0.525, 0.60
        y_top, y_bottom = row_for_t(t_high), row_for_t(t_low)
        for front in (True, False):
            u0, u1 = u_span(front)
            x0, x1 = int(u0 * size), int(u1 * size)
            arr[y_top:y_bottom, x0:x1, :3] *= 0.92
            for t, val in ((0.526, 0.60), (0.535, 0.70), (0.59, 0.66), (0.598, 0.60)):
                py = row_for_t(t)
                arr[max(0, py - 1):min(size, py + 1), x0:x1, :3] = val
            span = x1 - x0
            for frac in (0.14, 0.86):
                panel_c = x0 + int(frac * span)
                panel_half = int(0.045 * span)
                left, right = max(x0, panel_c - panel_half), min(x1, panel_c + panel_half)
                if right > left:
                    arr[y_bottom:row_for_t(0.07), left:right, :3] *= 1.035
                    for line_x in range(left + 2, right, 5):
                        arr[row_for_t(0.50):row_for_t(0.07), line_x:min(right, line_x + 1), :3] *= 0.84
                    for line_x in range(left + 4, right, 5):
                        arr[row_for_t(0.50):row_for_t(0.07), line_x:min(right, line_x + 1), :3] = np.clip(
                            arr[row_for_t(0.50):row_for_t(0.07), line_x:min(right, line_x + 1), :3] * 1.12, 0, 1
                        )
        # Small tonal ANFA wordmark near the front upper hip.
        paint_bitmap(arr, "ANFA", int(0.25 * size), row_for_t(0.555) - 12, 3, (0.95, 0.95, 0.95))

    image = bpy.data.images.new(
        "ANFA_CoreForm_Texture" if kind == "tee" else "ANFA_ArchitecturalRib_Texture",
        width=size, height=size, alpha=True, float_buffer=False,
    )
    image.pixels.foreach_set(arr[::-1].copy().ravel())
    image.file_format = "PNG"
    image.filepath_raw = str(path)
    image.colorspace_settings.name = "sRGB"
    image.save()
    image.pack()
    return image


def apply_uvs_and_texture(obj, kind, bounds):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="ANFA_UV")
    # Front and back are laid out on separate halves of the atlas. Faces on the character's front
    # are those facing -Y in Blender's Z-up source scene.
    width = max(abs(v.co.x - bounds["cx"]) for v in mesh.vertices) or 0.3
    for poly in mesh.polygons:
        is_front = poly.normal.y < 0.0
        u0, u1 = (0.05, 0.45) if is_front else (0.55, 0.95)
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            co = mesh.vertices[vi].co
            xfrac = max(0.0, min(1.0, (co.x - (bounds["cx"] - width)) / (2.0 * width)))
            t = (co.z - bounds["minz"]) / bounds["height"]
            uv_layer.data[li].uv = (u0 + xfrac * (u1 - u0), 0.05 + max(0.0, min(1.0, t)) * 0.9)

    texture_path = OUTPUT / ("anfa_core_form_tee_basecolor.png" if kind == "tee" else "anfa_architectural_rib_leggings_basecolor.png")
    image = make_garment_texture(kind, texture_path)
    mat = mesh.materials[0]
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.name = "ANFA woven tonal details"
    tex_node.label = "ANFA tonal logo / garment detail"
    tex_node.image = image
    tex_node.interpolation = "Linear"
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)


def style_surface(obj, kind, bounds, offset):
    mesh = obj.data
    mesh.materials.clear()
    color = "#202022" if kind == "tee" else "#171719"
    mat = create_material("ANFA_CoreForm_MatteJersey" if kind == "tee" else "ANFA_ArchitecturalRib_MatteKnit", color, 0.82 if kind == "tee" else 0.9)
    mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.material_index = 0
        poly.use_smooth = True

    # Slightly loosen the surface shell. Widen the T-shirt notably more than leggings.
    sx, sy = (1.14, 1.10) if kind == "tee" else (1.025, 1.025)
    keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
    basis = keys[0] if keys else None
    if basis is None:
        raise RuntimeError(f"{obj.name} is missing the Basis shape key")

    original_basis = [v.co.copy() for v in basis.data]
    basis_t = [(co.z - bounds["minz"]) / bounds["height"] for co in original_basis]
    # Apply the same spatial scaling/hem shaping to every morph so the GLB keeps its shape-key layout.
    for key in keys:
        for i, p in enumerate(key.data):
            p.co.x = bounds["cx"] + (p.co.x - bounds["cx"]) * sx
            p.co.y = bounds["cy"] + (p.co.y - bounds["cy"]) * sy
            if kind == "tee":
                # Subtle dropped hem: most visible at the lower edge, fading out before the waist.
                hem = 0.028 * (1.0 - smooth01((basis_t[i] - 0.585) / 0.16))
                p.co.z -= hem

    mesh.update()
    # Recompute the basis normals after widening, then offset every morph shape outward from the skin.
    basis_normals = [v.normal.copy() for v in mesh.vertices]
    basis_positions = [v.co.copy() for v in mesh.vertices]
    for key in keys:
        for i, p in enumerate(key.data):
            n = basis_normals[i].copy()
            if n.length > 0:
                n.normalize()
            extra = offset
            if kind == "tee":
                t = basis_t[i]
                extra += 0.006 * (1.0 - smooth01((t - 0.585) / 0.18))
            else:
                # High-waist panel gets a tiny extra stand-off to suggest a structured waistband.
                t = basis_t[i]
                extra += 0.0035 * smooth01((t - 0.49) / 0.08) * (1.0 - smooth01((t - 0.585) / 0.035))
                # Small raised vertical ribs along the outer-side panels (sub-millimetre, not texture noise).
                co = basis_positions[i]
                ax = abs(co.x - bounds["cx"])
                rib_progress = smooth01((t - 0.035) / 0.46)
                centre = 0.088 + 0.052 * rib_progress
                half_width = 0.063 + 0.047 * rib_progress
                side_line = centre + half_width * 0.48
                lateral = ax - side_line
                panel_window = math.exp(-((lateral / 0.040) ** 4)) if 0.0 <= t <= 0.49 else 0.0
                extra += 0.00065 * math.sin(lateral * (2.0 * math.pi / 0.006)) * panel_window
            p.co += n * extra
    mesh.update()
    obj["anfa_prototype"] = True
    obj["anfa_style"] = "Core Form oversized T-shirt blockout" if kind == "tee" else "Architectural Rib leggings blockout"


def duplicate_and_build(body, rig, bounds, name, kind):
    existing = bpy.data.objects.get(name)
    if existing:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = body.copy()
    obj.data = body.data.copy()
    obj.name = name
    obj.data.name = name + "_Mesh"
    # Link in the same collection as Body; duplicate keeps vertex groups, shape keys and armature modifier.
    collection = body.users_collection[0] if body.users_collection else bpy.context.scene.collection
    collection.objects.link(obj)
    obj.matrix_world = body.matrix_world.copy()
    clip_body_surface_region(obj, rig, tee_field if kind == "tee" else leggings_field, bounds)
    style_surface(obj, kind, bounds, 0.014 if kind == "tee" else 0.008)
    apply_uvs_and_texture(obj, kind, bounds)
    obj.parent = body.parent
    obj.matrix_parent_inverse = body.matrix_parent_inverse.copy()
    for mod in obj.modifiers:
        if mod.type == "ARMATURE":
            mod.object = rig
    obj.hide_render = False
    obj.hide_viewport = False
    return obj


def export_mesh_and_rig(obj, rig, path):
    armature_modifiers = [m for m in obj.modifiers if m.type == "ARMATURE" and m.object == rig]
    weighted_vertices = sum(1 for vertex in obj.data.vertices if len(vertex.groups) > 0)
    if not armature_modifiers or weighted_vertices < len(obj.data.vertices) * 0.98:
        raise RuntimeError(
            f"Refusing to export {obj.name}: skin audit failed "
            f"(armature_modifiers={len(armature_modifiers)}, weighted_vertices={weighted_vertices}/{len(obj.data.vertices)})"
        )
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = obj
    path.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_skins=True,
        export_morph=True,
        export_animations=False,
        export_yup=True,
        export_apply=False,
        export_materials="EXPORT",
    )
    if "FINISHED" not in result or not path.exists() or path.stat().st_size < 50000:
        raise RuntimeError(f"GLB export failed or was unexpectedly small: {path} ({result})")
    report(f"exported {path.name}: {path.stat().st_size:,} bytes")


def patch_manifest(tee_path, leggings_path, tee_obj, leggings_obj):
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    else:
        data = {"version": 1, "slots": ["underwear", "bra", "top", "bottom", "shoes", "outerwear"], "items": []}
    data.setdefault("slots", ["underwear", "bra", "top", "bottom", "shoes", "outerwear"])
    data.setdefault("items", [])
    items = data["items"]
    ids = {"anfa_core_form_tee", "anfa_architectural_rib_leggings"}
    items[:] = [item for item in items if item.get("id") not in ids]
    items.extend([
        {
            "id": "anfa_core_form_tee",
            "file": tee_path.name,
            "mesh": tee_obj.name,
            "label": {"da": "T-shirt oversize · Core Form", "en": "Oversized T-shirt · Core Form"},
            "slot": "top", "occupies": ["top"], "conflicts": [], "layer": 3,
            "hidesBodyZones": ["torso"],
            "colors": {"primary": "#202022", "secondary": "#343437"},
            "vertices": len(tee_obj.data.vertices), "faces": len(tee_obj.data.polygons),
            "license": "Prototype derived from the project's CC0 base-body mesh; review notices before distribution",
            "source": "ANFA Core Form edge-clipped surface prototype; not yet a final commercial garment asset",
        },
        {
            "id": "anfa_architectural_rib_leggings",
            "file": leggings_path.name,
            "mesh": leggings_obj.name,
            "label": {"da": "Legging · Architectural Rib", "en": "Leggings · Architectural Rib"},
            "slot": "bottom", "occupies": ["bottom"], "conflicts": [], "layer": 2,
            "hidesBodyZones": ["legs", "hips"],
            "colors": {"primary": "#171719", "secondary": "#343437"},
            "vertices": len(leggings_obj.data.vertices), "faces": len(leggings_obj.data.polygons),
            "license": "Prototype derived from the project's CC0 base-body mesh; review notices before distribution",
            "source": "ANFA Architectural Rib edge-clipped surface prototype with subtle raised outer-side ribbing; not yet a final commercial garment asset",
        },
    ])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report(f"updated {MANIFEST.relative_to(PROJECT)} with two ANFA prototype garments")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    body, rig = find_body_and_rig()
    bounds = get_bounds(body)
    report("local bounds=" + json.dumps({k: round(v, 4) for k, v in bounds.items() if isinstance(v, (int, float))}))

    tee = duplicate_and_build(body, rig, bounds, "Cloth_anfa_core_form_tee", "tee")
    leggings = duplicate_and_build(body, rig, bounds, "Cloth_anfa_architectural_rib_leggings", "leggings")

    tee_path = OUTPUT / "clothing_anfa_core_form_tee.glb"
    leggings_path = OUTPUT / "clothing_anfa_architectural_rib_leggings.glb"
    export_mesh_and_rig(tee, rig, tee_path)
    export_mesh_and_rig(leggings, rig, leggings_path)
    patch_manifest(tee_path, leggings_path, tee, leggings)
    report("BUILD OK — skin-audited clipped-surface prototypes; visually inspect before treating as final product models")


if __name__ == "__main__":
    main()
