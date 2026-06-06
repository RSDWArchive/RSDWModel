"""Render per-model icons from a single .glb/.gltf in a fresh headless Blender.

Driven by `RenderIcons.py` (parent pipeline) and `_probe_icons.py` (sample
harness). Each invocation handles ONE model so memory stays clean — matches
the `BuildGLBWorker.py` pattern.

Pipeline for one model:
    1. Enable the `icon_generator` addon (ships Camera/World/Rig assets +
       helper functions) and the gltf importer.
    2. Reset scene; `setup_cameras()` appends the 8 cameras + CameraBounds;
       `setup_world()` appends the WorldIcon shader.
    3. Import the .glb/.gltf asset.
    4. Compute a SINGLE aggregate bounding box over every imported mesh and
       fit it to `CameraBounds` (isotropic scale around origin). This differs
       from the plugin's per-mesh box scheme, which collapses multi-part
       models together; our variant treats the import as one unit.
    5. Configure render (resolution, engine, transparent bg).
    6. For each requested camera, set it active and render a PNG named
       "<stem>__<camera_slug>.png" next to the input asset.
    7. Emit a single-line RESULT:{json} for the parent to parse.

Invocation:
    blender --background --python RenderIconsWorker.py -- \\
        --asset "<abs_path>.gltf" \\
        --cameras "Orthographic Front,Perspective Front" \\
        --resolution 512 \\
        --engine BLENDER_EEVEE_NEXT \\
        --samples 32 \\
        --transparent 1 \\
        --out-suffix "__"

All arguments after the `--` are forwarded to this script.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

import addon_utils  # type: ignore
import bpy  # type: ignore
from mathutils import Vector  # type: ignore


# ---------------------------------------------------------------------------
# utilities

def _log(msg: str) -> None:
    print(f"[icons-worker] {msg}", flush=True)


def _slugify_camera(name: str) -> str:
    """Turn 'Orthographic Front' -> 'orthographic_front' (safe filename)."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def _parse_cli_args() -> argparse.Namespace:
    # Arguments after `--` are ours; everything before is Blender's.
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    p = argparse.ArgumentParser(description="Render icons for one .glb/.gltf asset")
    p.add_argument("--asset", type=Path, default=None, help="Path to .glb or .gltf input (absolute).")
    p.add_argument("--glb", type=Path, default=None, help="Legacy alias for --asset.")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Directory for PNGs. Default: same folder as the input asset.")
    p.add_argument("--cameras", type=str, default="",
                   help="Comma-separated camera names. Empty = all cameras in Camera.blend.")
    p.add_argument("--resolution", type=int, default=512)
    # NOTE: In Blender 5.0 the enum values are BLENDER_EEVEE / BLENDER_WORKBENCH / CYCLES.
    # (EEVEE Next from 4.x absorbed the BLENDER_EEVEE slot.) We keep the older
    # BLENDER_EEVEE_NEXT alias for backwards-compat with scripts/calls that still
    # use it — the worker transparently maps it back to BLENDER_EEVEE below.
    p.add_argument("--engine", type=str, default="BLENDER_EEVEE",
                   choices=["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"])
    p.add_argument("--samples", type=int, default=32,
                   help="Render samples (Cycles) or viewport samples (EEVEE Next).")
    p.add_argument("--transparent", type=int, default=1, choices=[0, 1],
                   help="Transparent PNG background if 1.")
    p.add_argument("--out-suffix", type=str, default="__",
                   help="Separator between asset stem and camera slug in filename.")
    p.add_argument("--save-blend", type=int, default=0, choices=[0, 1],
                   help="If 1, also save the assembled scene as a .blend next to the input asset "
                        "(useful for debugging framing).")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# scene ops

def _reset_scene() -> None:
    """Wipe the scene so we don't inherit startup defaults or a custom
    user startup.blend (which might ship a Cube, Icosphere, HDRI sphere, etc.).

    Uses direct data API (not bpy.ops) because `bpy.ops.object.select_all`
    silently no-ops in --background mode when there's no view layer context,
    which previously left a stray Icosphere in the scene and blew out the
    aggregate bbox for small objects (helmet appeared miniature).
    """
    # 1. Unlink and remove every object regardless of selection/visibility.
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception as e:  # noqa: BLE001
            _log(f"WARN failed to remove object {obj.name}: {e}")

    # 2. Purge orphaned data-blocks so later lookups don't find leftovers.
    for block_collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.lights,
        bpy.data.cameras,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.curves,
        bpy.data.metaballs,
        bpy.data.lattices,
        bpy.data.grease_pencils,
    ):
        for block in list(block_collection):
            if block.users == 0:
                try:
                    block_collection.remove(block)
                except Exception:
                    pass

    # 3. Drop any pre-existing collections so we can re-link fresh ones
    # from the Icon_Generator assets.
    scene_col = bpy.context.scene.collection
    for child in list(scene_col.children):
        scene_col.children.unlink(child)
    for coll in list(bpy.data.collections):
        if coll.users == 0:
            try:
                bpy.data.collections.remove(coll)
            except Exception:
                pass

    # 4. Blow away the scene world too — setup_world() will append a fresh one.
    if bpy.context.scene.world is not None:
        old_world = bpy.context.scene.world
        bpy.context.scene.world = None
        if old_world.users == 0:
            try:
                bpy.data.worlds.remove(old_world)
            except Exception:
                pass


def _ensure_addons() -> None:
    for mod in ("io_scene_gltf2", "icon_generator"):
        try:
            addon_utils.enable(mod, default_set=False, persistent=True)
        except Exception as e:  # noqa: BLE001
            _log(f"WARN addon_utils.enable({mod}) failed: {e}")


def _import_gltf_asset(asset_path: Path) -> list[bpy.types.Object]:
    """Import .glb/.gltf and return the list of newly-imported objects.

    Tracks by NAME rather than Python id() — Blender's RNA wrappers do not
    preserve Python identity between collection accesses, so id-based diffs
    silently over-include objects that were present before the import.
    """
    pre_names = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=str(asset_path))
    new_objs = [o for o in bpy.data.objects if o.name not in pre_names]
    return new_objs


_BLENDER_GLTF_HIDDEN_COLLECTION = "glTF_not_exported"


def _is_bone_shape_helper(obj: bpy.types.Object) -> bool:
    """Blender's built-in gltf importer adds an Icosphere as a bone-display
    shape when importing any skinned mesh; it lives in a hidden collection
    called 'glTF_not_exported'. That sphere is NOT part of the subject and
    would dominate the aggregate AABB (blowing up framing for small meshes
    like a helmet). Filter it out."""
    if obj.type != "MESH":
        return False
    for coll in obj.users_collection:
        if coll.name == _BLENDER_GLTF_HIDDEN_COLLECTION or coll.hide_render:
            return True
    return False


def _meshes_in(objs: list[bpy.types.Object]) -> list[bpy.types.Object]:
    """Keep visible, render-eligible meshes; skip bone-shape helpers and any
    object/collection that is hidden from render."""
    kept: list[bpy.types.Object] = []
    for o in objs:
        if o.type != "MESH":
            continue
        if o.hide_render:
            continue
        if _is_bone_shape_helper(o):
            continue
        kept.append(o)
    return kept


def _world_bbox(meshes: list[bpy.types.Object]) -> tuple[Vector, Vector] | None:
    """Aggregate world-space AABB across ALL meshes. Returns (min, max) or None."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    vmin = Vector((float("inf"),) * 3)
    vmax = Vector((float("-inf"),) * 3)
    any_points = False
    for obj in meshes:
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.to_mesh()
        if mesh is None:
            continue
        mw = obj.matrix_world
        for v in mesh.vertices:
            wv = mw @ v.co
            vmin.x = min(vmin.x, wv.x); vmax.x = max(vmax.x, wv.x)
            vmin.y = min(vmin.y, wv.y); vmax.y = max(vmax.y, wv.y)
            vmin.z = min(vmin.z, wv.z); vmax.z = max(vmax.z, wv.z)
            any_points = True
        obj_eval.to_mesh_clear()
    return (vmin, vmax) if any_points else None


def _fit_import_to_camera_bounds(new_objs: list[bpy.types.Object]) -> dict:
    """Create one wrapping empty for the whole import and scale+center it so
    its AABB fits inside CameraBounds (which is a unit cube by default).

    Returns a small dict describing what was done (for RESULT JSON).
    """
    meshes = _meshes_in(new_objs)
    if not meshes:
        return {"fit": "no_meshes"}

    bbox = _world_bbox(meshes)
    if bbox is None:
        return {"fit": "empty_bbox"}

    vmin, vmax = bbox
    size = vmax - vmin
    center = (vmin + vmax) / 2.0
    largest = max(size.x, size.y, size.z)
    if largest <= 1e-9:
        return {"fit": "degenerate"}

    bounds = bpy.data.objects.get("CameraBounds")
    # CameraBounds is a cube empty; its world-space extent along each axis is
    # 2 * scale[i]. We treat the smallest-axis scale as the target half-size
    # so nothing clips the frame regardless of orientation.
    target_half = 1.0  # default if no CameraBounds
    if bounds is not None:
        target_half = min(abs(bounds.scale.x), abs(bounds.scale.y), abs(bounds.scale.z))
    half_extent = largest / 2.0
    scale_factor = target_half / half_extent

    # Create an empty, parent all top-level imported objects under it, then
    # scale+translate the empty so the aggregate AABB becomes [-half, +half]
    # centered on origin.
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0, 0, 0))
    wrapper = bpy.context.active_object
    wrapper.name = "IconImportWrapper"

    # Parent every top-level imported object (those without a parent among new_objs)
    new_set = set(new_objs)
    top_level = [o for o in new_objs if (o.parent is None or o.parent not in new_set)]
    for obj in top_level:
        obj.parent = wrapper
        # Preserve current world transform under the new parent.
        obj.matrix_parent_inverse = wrapper.matrix_world.inverted()

    # First re-center by offsetting wrapper by -center, then scale by factor.
    # Doing translate BEFORE scale keeps math simple: children's local coords
    # are their world coords (wrapper at origin, identity), so we can move the
    # wrapper to -center and then scale.
    wrapper.location = (-center.x * scale_factor, -center.y * scale_factor, -center.z * scale_factor)
    wrapper.scale = (scale_factor, scale_factor, scale_factor)

    return {
        "fit": "ok",
        "bbox_size": [round(size.x, 4), round(size.y, 4), round(size.z, 4)],
        "bbox_center": [round(center.x, 4), round(center.y, 4), round(center.z, 4)],
        "scale_factor": round(scale_factor, 6),
        "target_half": round(target_half, 4),
        "mesh_count": len(meshes),
    }


def _configure_render(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(args.transparent)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if args.transparent else "RGB"

    # Blender 5.0: BLENDER_EEVEE_NEXT was folded back into BLENDER_EEVEE.
    engine = "BLENDER_EEVEE" if args.engine == "BLENDER_EEVEE_NEXT" else args.engine
    try:
        scene.render.engine = engine
    except Exception as e:  # noqa: BLE001
        _log(f"WARN cannot set engine {engine}: {e}. Falling back to whatever is active.")

    if scene.render.engine == "CYCLES":
        scene.cycles.samples = args.samples
        scene.cycles.device = "GPU" if any(
            d.use for d in getattr(bpy.context.preferences.addons.get("cycles", None).preferences, "devices", [])  # type: ignore[union-attr]
        ) else "CPU"
    elif scene.render.engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.eevee.taa_render_samples = args.samples
        except Exception:
            pass


def _select_cameras(requested_csv: str) -> list[bpy.types.Object]:
    all_cams = [o for o in bpy.data.objects if o.type == "CAMERA"]
    if not requested_csv.strip():
        return all_cams
    names = [n.strip() for n in requested_csv.split(",") if n.strip()]
    by_name = {o.name: o for o in all_cams}
    # Allow exact name or lowercased slug match
    slug_to_obj = {_slugify_camera(o.name): o for o in all_cams}
    picked: list[bpy.types.Object] = []
    missing: list[str] = []
    for n in names:
        if n in by_name:
            picked.append(by_name[n])
        elif _slugify_camera(n) in slug_to_obj:
            picked.append(slug_to_obj[_slugify_camera(n)])
        else:
            missing.append(n)
    if missing:
        _log(f"WARN requested cameras not found: {missing}. Available: {[c.name for c in all_cams]}")
    return picked


def _render_cameras(
    asset_path: Path,
    out_dir: Path,
    cameras: list[bpy.types.Object],
    out_suffix: str,
) -> list[dict]:
    results: list[dict] = []
    scene = bpy.context.scene
    stem = asset_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for cam in cameras:
        scene.camera = cam
        slug = _slugify_camera(cam.name)
        png = out_dir / f"{stem}{out_suffix}{slug}.png"
        scene.render.filepath = str(png)
        t0 = time.time()
        try:
            bpy.ops.render.render(write_still=True)
            ok = png.is_file()
            err = None if ok else "render returned but file missing"
        except Exception as e:  # noqa: BLE001
            ok = False
            err = f"{type(e).__name__}: {e}"
        results.append({
            "camera": cam.name,
            "slug": slug,
            "png": str(png),
            "ok": ok,
            "err": err,
            "elapsed_s": round(time.time() - t0, 3),
        })
    return results


# ---------------------------------------------------------------------------
# main

def main() -> int:
    args = _parse_cli_args()

    result: dict = {
        "kind": "icon_worker_result",
        "asset": None,
        "glb": str(args.glb) if args.glb else None,
        "ok": False,
        "renders": [],
        "fit": None,
        "errors": [],
        "elapsed_s": 0.0,
    }
    t_total = time.time()
    try:
        asset_path = args.asset if args.asset is not None else args.glb
        result["asset"] = str(asset_path) if asset_path else None
        if asset_path is None:
            raise ValueError("Pass --asset <.gltf/.glb> or legacy --glb <.glb>")
        if not asset_path.is_file():
            raise FileNotFoundError(f"asset not found: {asset_path}")

        _ensure_addons()
        _reset_scene()

        # 1. Bring in cameras + world from the plugin's assets.
        from icon_generator.modules.import_camera import setup_cameras  # type: ignore
        from icon_generator.modules.setup_world import setup_world  # type: ignore

        setup_cameras()
        setup_world()

        # 2. Import the glTF asset. NOTE: Blender's built-in gltf importer auto-adds an
        # Icosphere bone-shape helper for skinned meshes; it lives in the hidden
        # "glTF_not_exported" collection and is filtered out by `_meshes_in`.
        new_objs = _import_gltf_asset(asset_path)
        result["import_count"] = len(new_objs)

        # 3. Fit it to CameraBounds (aggregate-box variant).
        fit = _fit_import_to_camera_bounds(new_objs)
        result["fit"] = fit
        if fit.get("fit") != "ok":
            raise RuntimeError(f"fit failed: {fit}")

        # 4. Configure render settings + pick cameras.
        _configure_render(args)
        cams = _select_cameras(args.cameras)
        if not cams:
            raise RuntimeError("no cameras available to render")

        # 5. Optional debug .blend snapshot.
        if args.save_blend:
            out_dir = args.out_dir if args.out_dir else asset_path.parent
            dbg_blend = out_dir / f"{asset_path.stem}{args.out_suffix}icons_debug.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(dbg_blend), copy=True)
            result["debug_blend"] = str(dbg_blend)

        # 6. Render every camera.
        out_dir = args.out_dir if args.out_dir else asset_path.parent
        renders = _render_cameras(asset_path, out_dir, cams, args.out_suffix)
        result["renders"] = renders
        result["ok"] = all(r["ok"] for r in renders)
        if not result["ok"]:
            result["errors"] = [r for r in renders if not r["ok"]]
    except Exception as e:  # noqa: BLE001
        result["errors"].append({"stage": "top", "msg": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
    finally:
        result["elapsed_s"] = round(time.time() - t_total, 3)
        print("RESULT:" + json.dumps(result), flush=True)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
