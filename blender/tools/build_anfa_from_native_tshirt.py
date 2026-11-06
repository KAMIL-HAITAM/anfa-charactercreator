"""Build ANFA prototypes from CharacterCreator's native rigged garment meshes.

Run only after the standard CharacterCreator build has generated output/clothing_tshirt.glb
and output/clothing_jeans.glb. It adapts the actual short-sleeve T-shirt (crew neckline) and jeans
mesh, preserving bone weights and shape keys. The tee is widened for an oversized fit; the jeans
mesh is narrowed and gets stronger geometric outer-leg ribbing. These remain prototypes until
visually inspected.
"""
import json
import math
import os
import shutil
import struct
from pathlib import Path

import bpy
from mathutils import Vector

PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / "output"
MANIFEST = OUTPUT / "clothing.json"
BACKUP = OUTPUT / "clothing.json.pre_original_mesh_build.json"

TEE_ID = "anfa_core_form_tee"
LEGGINGS_ID = "anfa_architectural_rib_leggings"
TEE_FILE = f"clothing_{TEE_ID}.glb"
LEGGINGS_FILE = f"clothing_{LEGGINGS_ID}.glb"


def log(msg):
    print(f"ANFA_ORIGINALS {msg}", flush=True)


def smoothstep(a, b, x):
    if abs(b - a) < 1e-9:
        return 1.0 if x >= b else 0.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def world_bbox(points):
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    zs = [p.z for p in points]
    return {
        "minx": min(xs), "maxx": max(xs),
        "miny": min(ys), "maxy": max(ys),
        "minz": min(zs), "maxz": max(zs),
    }


def transform_world_point(p, kind, bounds):
    """Gentle shape edits to an existing garment. Blender scene coordinates are Z-up."""
    p = p.copy()
    zmin, zmax = bounds["minz"], bounds["maxz"]
    height = max(0.1, zmax - zmin)
    ymid = (bounds["miny"] + bounds["maxy"]) * 0.5

    if kind == "tee":
        # Enlarge the existing shirt body and shoulder line, retaining its original garment topology.
        # Keep the vertical profile; a little depth lets the shirt drape less tightly over the torso.
        p.x *= 1.105
        p.y = ymid + (p.y - ymid) * 1.07
        # Slightly lengthen the lower body into an oversized tee hem.
        hem_w = 1.0 - smoothstep(zmin + 0.015, zmin + height * 0.24, p.z)
        p.z -= 0.018 * hem_w
        # Very slight shoulder drop / sleeve-room adjustment above the upper torso.
        sh_w = smoothstep(zmin + height * 0.48, zmin + height * 0.82, p.z)
        p.x += (1.0 if p.x >= 0 else -1.0) * 0.010 * sh_w
        return p

    # Architectural Rib leggings: tighten the original two-leg trouser mesh, raise the waist slightly,
    # and add visible vertical outer-leg ribbing. This keeps the existing crotch and ankle topology.
    leg_w = smoothstep(zmin + height * 0.22, zmin + height * 0.70, p.z)
    hip_w = 1.0 - smoothstep(zmax - height * 0.22, zmax - height * 0.06, p.z)
    ax = abs(p.x)
    sign = 1.0 if p.x >= 0 else -1.0
    if ax > 0.045 and p.z < zmax - height * 0.10:
        center_x = sign * (0.112 + 0.010 * (1.0 - leg_w))
        factor_x = 1.0 - 0.11 * leg_w - 0.035 * (1.0 - leg_w) * hip_w
        p.x = center_x + (p.x - center_x) * factor_x
    # Slightly reduce depth around thigh/calf, preserve front/back hip fit.
    p.y = ymid + (p.y - ymid) * (1.0 - 0.10 * leg_w)
    p.z += 0.028 * smoothstep(zmax - height * 0.14, zmax, p.z)

    # Visible geometric ribbing, concentrated on the outer-side panel (not a painted stripe).
    # Ridge amplitude is 3.5 mm, large enough to read in the preview while remaining subtle in silhouette.
    if abs(p.x) > 0.065 and p.z < zmax - height * 0.12 and p.z > zmin + height * 0.035:
        center_x = sign * 0.12
        theta = math.atan2(p.y - ymid, p.x - center_x)
        panel = math.exp(-((abs(theta) / 0.58) ** 6))
        phase = ((theta + 0.58) / 1.16) * (2.0 * math.pi * 4.0)
        ridge = 0.0035 * math.cos(phase) * panel * leg_w
        radial_x = p.x - center_x
        radial_y = p.y - ymid
        norm = math.sqrt(radial_x * radial_x + radial_y * radial_y) or 1.0
        p.x += ridge * radial_x / norm
        p.y += ridge * radial_y / norm
    return p


def get_mesh_and_armature(new_objects, expected_mesh_name):
    meshes = [o for o in new_objects if o.type == 'MESH' and len(o.data.vertices) > 100]
    if not meshes:
        raise RuntimeError(f"No garment mesh found after importing {expected_mesh_name}")
    mesh_obj = max(meshes, key=lambda o: len(o.data.vertices))
    armatures = [o for o in new_objects if o.type == 'ARMATURE']
    if not armatures:
        raise RuntimeError(f"No armature was imported with {expected_mesh_name}")
    armature = armatures[0]
    mesh_obj.name = expected_mesh_name
    mesh_obj.data.name = expected_mesh_name + "_Mesh"
    # Ensure an armature modifier points to the imported asset rig.
    mods = [m for m in mesh_obj.modifiers if m.type == 'ARMATURE']
    if not mods:
        mod = mesh_obj.modifiers.new("Armature", 'ARMATURE')
        mod.object = armature
    else:
        for mod in mods:
            mod.object = armature
    return mesh_obj, armature


def capture_key_records(obj, deform_kind, bounds):
    keys = obj.data.shape_keys
    if not keys or len(keys.key_blocks) < 90:
        count = 0 if not keys else len(keys.key_blocks)
        raise RuntimeError(f"{obj.name} only has {count} shape key blocks; expected Basis + ~92 morph targets")
    records = []
    inv = obj.matrix_world.inverted_safe()
    for kb in keys.key_blocks:
        points = []
        for kp in kb.data:
            world = obj.matrix_world @ kp.co
            transformed = transform_world_point(world, deform_kind, bounds)
            points.append(inv @ transformed)
        records.append({
            "name": kb.name,
            "coords": points,
            "relative_key": kb.relative_key.name if kb.relative_key else "Basis",
            "slider_min": kb.slider_min,
            "slider_max": kb.slider_max,
            "value": kb.value,
            "mute": kb.mute,
            "interpolation": kb.interpolation,
            "vertex_group": kb.vertex_group,
        })
    return records


def source_vertex_weights(obj):
    # Save the actual per-vertex memberships before replacing the Mesh datablock.
    return [[(g.group, float(g.weight)) for g in v.groups] for v in obj.data.vertices]


def copy_zone_attribute(src_mesh, dst_mesh):
    source = src_mesh.attributes.get("_CCZONE")
    if source is None or source.domain != 'POINT':
        log("warning: source mesh has no point-domain _CCZONE attribute")
        return
    try:
        target = dst_mesh.attributes.new(name="_CCZONE", type=source.data_type, domain='POINT')
        for i, item in enumerate(source.data):
            dest = target.data[i]
            if source.data_type in ('FLOAT', 'INT', 'BOOLEAN', 'INT8', 'INT32_2D'):
                dest.value = item.value
            elif source.data_type == 'FLOAT_VECTOR':
                dest.vector = item.vector
            elif source.data_type in ('FLOAT_COLOR', 'BYTE_COLOR'):
                dest.color = item.color
            else:
                log(f"warning: _CCZONE type {source.data_type} is unsupported by copier")
                return
    except Exception as exc:
        log(f"warning: could not preserve _CCZONE: {exc}")


def rebuild_mesh_with_faces(obj, kept_poly_indices, key_records, weights, boundary_vertices=None, boundary_planes=None):
    """Rebuild with the same vertex indices so original group weights and every morph remain mappable."""
    old = obj.data
    old_faces = [tuple(poly.vertices) for poly in old.polygons]
    old_smooth = [bool(poly.use_smooth) for poly in old.polygons]
    old_uv = []
    for uv_layer in old.uv_layers:
        per_poly = []
        for poly in old.polygons:
            per_poly.append([tuple(uv_layer.data[li].uv) for li in poly.loop_indices])
        old_uv.append((uv_layer.name, per_poly))

    # Move boundary vertices onto the sleeve-cut plane for Basis AND every morph target.
    if boundary_vertices and boundary_planes:
        inv = obj.matrix_world.inverted_safe()
        for rec in key_records:
            for vi in boundary_vertices:
                side, xcut, zcut, slope = boundary_planes[vi]
                world = obj.matrix_world @ rec["coords"][vi]
                signed = side * world.x - xcut - slope * (world.z - zcut)
                denom = 1.0 + slope * slope
                world.x -= signed * side / denom
                world.z += signed * slope / denom
                rec["coords"][vi] = inv @ world

    basis_rec = next((r for r in key_records if r["name"] == "Basis"), key_records[0])
    # Key block coordinates and mesh base coordinates must agree.
    vertices = [tuple(co) for co in basis_rec["coords"]]
    faces = [old_faces[i] for i in kept_poly_indices]
    smooth_flags = [old_smooth[i] for i in kept_poly_indices]
    new_mesh = bpy.data.meshes.new(old.name + "_Anfa")
    new_mesh.from_pydata(vertices, [], faces)
    new_mesh.update()
    for i, smooth in enumerate(smooth_flags):
        new_mesh.polygons[i].use_smooth = smooth

    # UVs are loop-domain data: preserve them face-for-face for all retained polygons.
    for uv_name, per_poly in old_uv:
        uv_new = new_mesh.uv_layers.new(name=uv_name)
        for new_pi, old_pi in enumerate(kept_poly_indices):
            new_poly = new_mesh.polygons[new_pi]
            uv_values = per_poly[old_pi]
            if len(new_poly.loop_indices) != len(uv_values):
                continue
            for new_li, uv in zip(new_poly.loop_indices, uv_values):
                uv_new.data[new_li].uv = uv

    copy_zone_attribute(old, new_mesh)

    # Record group names before swapping datablocks, then reconstruct weight membership by vertex index.
    group_names = [vg.name for vg in obj.vertex_groups]
    obj.data = new_mesh
    for name in group_names:
        # Existing object vertex groups usually remain after data swap; recreate only if Blender dropped any.
        if obj.vertex_groups.get(name) is None:
            obj.vertex_groups.new(name=name)
    for vi, memberships in enumerate(weights):
        for group_idx, weight in memberships:
            if group_idx < len(obj.vertex_groups) and weight > 0.0:
                obj.vertex_groups[group_idx].add([vi], weight, 'REPLACE')

    # Rebuild all shape keys with the same names/relative relationships.
    by_name = {}
    for rec in key_records:
        kb = obj.shape_key_add(name=rec["name"], from_mix=False)
        by_name[rec["name"]] = kb
        for i, co in enumerate(rec["coords"]):
            kb.data[i].co = co
        kb.slider_min = rec["slider_min"]
        kb.slider_max = rec["slider_max"]
        kb.value = rec["value"]
        kb.mute = rec["mute"]
        kb.interpolation = rec["interpolation"]
        kb.vertex_group = rec["vertex_group"]
    for rec in key_records:
        kb = by_name[rec["name"]]
        rel = by_name.get(rec["relative_key"])
        if rel is not None:
            kb.relative_key = rel
    new_mesh.update()

    # Weight audit: every retained mesh vertex should have at least one non-zero joint influence.
    groups = {g.index: g.name for g in obj.vertex_groups}
    bone_names = {b.name for b in next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None).data.bones}
    bone_groups = sum(1 for _, name in groups.items() if name in bone_names)
    mesh_vertices = len(new_mesh.vertices)
    weighted = 0
    max_inf = 0
    for v in new_mesh.vertices:
        memberships = [m for m in v.groups if m.weight > 1e-6 and m.group in groups and groups[m.group] in bone_names]
        if memberships:
            weighted += 1
            max_inf = max(max_inf, len(memberships))
    log(f"{obj.name}: rebuilt mesh verts={mesh_vertices}, faces={len(new_mesh.polygons)}, bone_groups={bone_groups}, weighted={weighted}/{mesh_vertices}, max_influences={max_inf}, morphs={len(key_records)-1}")
    if weighted != mesh_vertices or bone_groups < 10 or len(key_records) < 90:
        raise RuntimeError(f"{obj.name}: post-rebuild skin/morph audit failed")
    return new_mesh


def clip_sleeves(obj, key_records, bounds):
    """Cut the long sleeves off at the upper arm using existing faces; no vertices are deleted."""
    mesh = obj.data
    inv = obj.matrix_world.inverted_safe()
    basis_rec = next((r for r in key_records if r["name"] == "Basis"), key_records[0])
    basis_world = [obj.matrix_world @ c for c in basis_rec["coords"]]
    zmin, zmax = bounds["minz"], bounds["maxz"]
    height = max(0.1, zmax - zmin)
    zcut = zmin + height * 0.635
    xcut = max(abs(bounds["minx"]), abs(bounds["maxx"])) * 0.59
    slope = 0.55
    all_faces = [tuple(p.vertices) for p in mesh.polygons]
    remove, keep = [], []
    for pi, face in enumerate(all_faces):
        pts = [basis_world[v] for v in face]
        cx = sum(p.x for p in pts) / len(pts)
        cz = sum(p.z for p in pts) / len(pts)
        side = 1.0 if cx >= 0 else -1.0
        signed = side * cx - xcut - slope * (cz - zcut)
        is_sleeve_region = abs(cx) > max(0.20, xcut * 0.78) and cz < zcut + height * 0.20
        if is_sleeve_region and signed > 0.0:
            remove.append(pi)
        else:
            keep.append(pi)
    if len(remove) < 30 or len(keep) < 500:
        raise RuntimeError(f"Sleeve trim selected an implausible face count: remove={len(remove)}, keep={len(keep)}")
    rverts = {v for pi in remove for v in all_faces[pi]}
    kverts = {v for pi in keep for v in all_faces[pi]}
    boundary = rverts & kverts
    planes = {}
    for vi in boundary:
        p = basis_world[vi]
        side = 1.0 if p.x >= 0 else -1.0
        signed = side * p.x - xcut - slope * (p.z - zcut)
        if abs(p.x) > xcut * 0.55 and abs(signed) < 0.10:
            planes[vi] = (side, xcut, zcut, slope)
    log(f"{obj.name}: sleeve trim removes {len(remove)} faces; projects {len(planes)} cut-edge vertices")
    return keep, set(planes.keys()), planes


def make_color_material(obj, label):
    # A single neutral material allows CharacterCreator's runtime setColor() to work with primary tint.
    mat = bpy.data.materials.new(label)
    mat.diffuse_color = (0.055, 0.055, 0.055, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.055, 0.055, 0.055, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.84
        bsdf.inputs["Metallic"].default_value = 0.0
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.material_index = 0
        p.use_smooth = True


def export_asset(obj, armature, out_path):
    if out_path.exists():
        backup_asset = out_path.with_name(out_path.stem + ".pre_original_mesh_build.glb")
        if not backup_asset.exists():
            shutil.copy2(out_path, backup_asset)
            log(f"created one-time asset backup {backup_asset.name}")
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format='GLB',
        use_selection=True,
        export_skins=True,
        export_morph=True,
        export_animations=False,
        export_yup=True,
        export_apply=False,
        export_extras=True,
    )
    if not out_path.exists() or out_path.stat().st_size < 100_000:
        raise RuntimeError(f"Export failed or unexpectedly small: {out_path}")
    audit_glb(out_path)
    log(f"exported {out_path.name}: {out_path.stat().st_size:,} bytes")


def audit_glb(path):
    with path.open('rb') as f:
        header = f.read(12)
        if len(header) != 12:
            raise RuntimeError(f"{path.name}: invalid GLB header")
        magic, version, total = struct.unpack('<4sII', header)
        if magic != b'glTF' or version != 2:
            raise RuntimeError(f"{path.name}: not a glTF 2.0 binary")
        doc = None
        while True:
            chunk_header = f.read(8)
            if not chunk_header:
                break
            chunk_len, chunk_type = struct.unpack('<I4s', chunk_header)
            chunk = f.read(chunk_len)
            if chunk_type == b'JSON':
                doc = json.loads(chunk.decode('utf-8').rstrip('\0 \t\r\n'))
                break
    if not doc:
        raise RuntimeError(f"{path.name}: no JSON chunk")
    primitive_list = [p for m in doc.get('meshes', []) for p in m.get('primitives', [])]
    skinned = any('JOINTS_0' in p.get('attributes', {}) and 'WEIGHTS_0' in p.get('attributes', {}) for p in primitive_list)
    target_count = max([len(m.get('primitives', [{}])[0].get('targets', [])) for m in doc.get('meshes', [])] or [0])
    if not doc.get('skins') or not skinned or target_count < 90:
        raise RuntimeError(f"{path.name}: GLB audit failed (skins={len(doc.get('skins', []))}, skinned={skinned}, targets={target_count})")
    log(f"{path.name}: GLB audit PASS, skins={len(doc.get('skins', []))}, skinned={skinned}, targets={target_count}")


def process_source(filename, mesh_name, kind, out_filename):
    src = OUTPUT / filename
    if not src.exists():
        raise FileNotFoundError(f"Required original mesh is missing: {src}")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(src))
    imported = list(set(bpy.data.objects) - before)
    obj, armature = get_mesh_and_armature(imported, mesh_name)
    bpy.context.view_layer.update()

    basis = obj.data.shape_keys.key_blocks[0]
    orig_world = [obj.matrix_world @ p.co for p in basis.data]
    bounds = world_bbox(orig_world)
    log(f"source {filename}: object={obj.name}, verts={len(obj.data.vertices)}, shape_keys={len(obj.data.shape_keys.key_blocks)}, bounds={json.dumps(bounds)}")
    key_records = capture_key_records(obj, kind, bounds)
    weights = source_vertex_weights(obj)

    if kind == 'tee' and filename == 'clothing_shirt.glb':
        # Legacy fallback only: the long-sleeve shirt has a collar and must be sleeve-trimmed.
        kept, boundary, planes = clip_sleeves(obj, key_records, bounds)
        new_mesh = rebuild_mesh_with_faces(obj, kept, key_records, weights, boundary, planes)
    else:
        # Native clothing_tshirt.glb already has short sleeves and a crew neckline; keep its topology.
        kept = list(range(len(obj.data.polygons)))
        new_mesh = rebuild_mesh_with_faces(obj, kept, key_records, weights)

    obj.name = mesh_name
    new_mesh.name = mesh_name + "_Mesh"
    make_color_material(obj, "ANFA_Matte_Knit")
    # Item object remains a sibling/child of the imported rig and uses its armature modifier.
    mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if mod is None or mod.object is not armature:
        if mod is None:
            mod = obj.modifiers.new("Armature", 'ARMATURE')
        mod.object = armature
    out_path = OUTPUT / out_filename
    export_asset(obj, armature, out_path)

    # Remove imported source scene objects only after export; preloaded base_body.blend scene is untouched.
    for o in imported:
        if o.name in bpy.data.objects:
            bpy.data.objects.remove(o, do_unlink=True)


def update_manifest():
    if not MANIFEST.exists():
        raise FileNotFoundError(f"Missing manifest: {MANIFEST}")
    if not BACKUP.exists():
        shutil.copy2(MANIFEST, BACKUP)
        log(f"created one-time manifest backup {BACKUP.name}")
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    items = data.setdefault('items', [])
    custom = [
        {
            "id": TEE_ID,
            "file": TEE_FILE,
            "mesh": "Cloth_anfa_core_form_tee",
            "label": {"da": "Oversize T-shirt · Core Form", "en": "Oversized T-shirt · Core Form"},
            "slot": "top", "occupies": ["top"], "conflicts": [], "layer": 3,
            "hidesBodyZones": ["torso"],
            "colors": {"primary": "#111111"},
            "license": "CC0 source garment + project-original geometric modifications",
            "source": "Adapted from the original rigged CharacterCreator long-sleeve shirt mesh; prototype, inspect before release"
        },
        {
            "id": LEGGINGS_ID,
            "file": LEGGINGS_FILE,
            "mesh": "Cloth_anfa_architectural_rib_leggings",
            "label": {"da": "Leggings · Architectural Rib", "en": "Leggings · Architectural Rib"},
            "slot": "bottom", "occupies": ["bottom"], "conflicts": [], "layer": 2,
            "hidesBodyZones": ["legs"],
            "colors": {"primary": "#111111"},
            "license": "CC0 source garment + project-original geometric modifications",
            "source": "Adapted from the original rigged CharacterCreator jeans mesh; prototype, inspect before release"
        }
    ]
    replace_ids = {x['id'] for x in custom}
    data['items'] = [x for x in items if x.get('id') not in replace_ids] + custom
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f"updated {MANIFEST.name} with {TEE_ID} and {LEGGINGS_ID}")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    process_source("clothing_tshirt.glb", "Cloth_anfa_core_form_tee", "tee", TEE_FILE)
    process_source("clothing_jeans.glb", "Cloth_anfa_architectural_rib_leggings", "leggings", LEGGINGS_FILE)
    update_manifest()
    log("BUILD OK — native T-shirt / jeans meshes transformed; visually inspect before website integration")


if __name__ == "__main__":
    main()
