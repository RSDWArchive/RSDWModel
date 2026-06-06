"""Headless introspection of Icon_Generator-main/assets/Camera.blend.

Lists every camera + any CameraBounds empty inside the 'Camera' collection so
we know exactly what "render all cameras" will produce before committing.

Run as:
    blender --background --python tools/Icons/_dump_camera_blend.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMERA_BLEND = REPO_ROOT / "Icon_Generator-main" / "assets" / "Camera.blend"


def main() -> int:
    if not CAMERA_BLEND.is_file():
        print(f"[ERR] Camera.blend not found: {CAMERA_BLEND}")
        return 2

    with bpy.data.libraries.load(str(CAMERA_BLEND), link=False) as (data_from, data_to):
        data_to.collections = list(data_from.collections)
        data_to.objects = list(data_from.objects)

    # Link everything so properties resolve
    for coll in data_to.collections:
        if coll is not None:
            bpy.context.scene.collection.children.link(coll)

    report: dict = {
        "camera_blend": str(CAMERA_BLEND),
        "collections_loaded": [c.name for c in data_to.collections if c is not None],
        "cameras": [],
        "bounds_empties": [],
        "other_objects": [],
    }

    for obj in bpy.data.objects:
        if obj.type == "CAMERA":
            cam = obj.data
            report["cameras"].append({
                "name": obj.name,
                "location": list(obj.location),
                "rotation_euler_deg": [round(r * 57.29577951308232, 3) for r in obj.rotation_euler],
                "scale": list(obj.scale),
                "lens_type": cam.type,
                "lens_mm": getattr(cam, "lens", None),
                "ortho_scale": getattr(cam, "ortho_scale", None),
                "clip_start": cam.clip_start,
                "clip_end": cam.clip_end,
                "shift_x": cam.shift_x,
                "shift_y": cam.shift_y,
            })
        elif obj.type == "EMPTY" and "bound" in obj.name.lower():
            report["bounds_empties"].append({
                "name": obj.name,
                "display_type": obj.empty_display_type,
                "location": list(obj.location),
                "rotation_euler_deg": [round(r * 57.29577951308232, 3) for r in obj.rotation_euler],
                "scale": list(obj.scale),
            })
        else:
            report["other_objects"].append({"name": obj.name, "type": obj.type})

    print("CAMERA_BLEND_DUMP:" + json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
