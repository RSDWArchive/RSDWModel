"""Dump all objects + per-mesh bbox/visibility from a saved debug .blend.

Usage (headless):
    blender --background <path.blend> --python tools/Icons/_inspect_debug_blend.py
"""
from __future__ import annotations

import sys

import bpy  # type: ignore
from mathutils import Vector  # type: ignore


def _mesh_bbox(obj: bpy.types.Object) -> tuple[Vector, Vector] | None:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    if mesh is None or len(mesh.vertices) == 0:
        return None
    mw = obj.matrix_world
    vmin = Vector((float("inf"),) * 3)
    vmax = Vector((float("-inf"),) * 3)
    for v in mesh.vertices:
        wv = mw @ v.co
        vmin.x = min(vmin.x, wv.x); vmax.x = max(vmax.x, wv.x)
        vmin.y = min(vmin.y, wv.y); vmax.y = max(vmax.y, wv.y)
        vmin.z = min(vmin.z, wv.z); vmax.z = max(vmax.z, wv.z)
    obj_eval.to_mesh_clear()
    return (vmin, vmax)


def main() -> int:
    print("=" * 72)
    print(f"Blend loaded: {bpy.data.filepath}")
    print(f"Scene: {bpy.context.scene.name}")
    print("")
    print("ALL OBJECTS:")
    for obj in bpy.data.objects:
        flags = []
        if obj.hide_viewport: flags.append("hide_viewport")
        if obj.hide_render: flags.append("hide_render")
        if obj.hide_select: flags.append("hide_select")
        if not obj.visible_get(): flags.append("!visible_get")
        parent = obj.parent.name if obj.parent else "-"
        print(f"  [{obj.type:8}] {obj.name:50}  parent={parent:30}  flags={','.join(flags) or 'none'}")

    print("")
    print("PER-MESH BBOX (world-space):")
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        bb = _mesh_bbox(obj)
        if bb is None:
            print(f"  {obj.name:60}  <no verts>")
            continue
        vmin, vmax = bb
        size = vmax - vmin
        print(f"  {obj.name:60}  size=({size.x:.3f}, {size.y:.3f}, {size.z:.3f})  "
              f"min=({vmin.x:.2f},{vmin.y:.2f},{vmin.z:.2f})  max=({vmax.x:.2f},{vmax.y:.2f},{vmax.z:.2f})  "
              f"material_slots={len(obj.material_slots)}  "
              f"mats={[s.material.name if s.material else None for s in obj.material_slots]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
