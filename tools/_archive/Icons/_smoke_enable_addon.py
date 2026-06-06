"""Quick smoke test: enable the Icon_Generator addon in portable Blender.

Also imports its modules directly to verify relative imports still resolve
under the renamed `icon_generator` package.
"""
from __future__ import annotations

import sys

import addon_utils  # type: ignore
import bpy  # type: ignore


def main() -> int:
    try:
        ok, err = addon_utils.enable("icon_generator", default_set=False, persistent=True), None
    except Exception as e:  # noqa: BLE001
        ok, err = False, e

    print(f"ADDON_ENABLED={bool(ok)} ERR={err}")
    try:
        from icon_generator.modules import setup_world, bounding_box, bounding_scale, render_objects, setup_mesh, import_camera  # type: ignore  # noqa: F401
        print("ADDON_MODULES_IMPORT_OK=True")
    except Exception as e:  # noqa: BLE001
        print(f"ADDON_MODULES_IMPORT_OK=False ERR={e}")
        return 2

    from icon_generator.modules.import_camera import setup_cameras  # type: ignore
    from icon_generator.modules.setup_world import setup_world as sw  # type: ignore

    setup_cameras()
    sw()
    cams = [o.name for o in bpy.data.objects if o.type == "CAMERA"]
    bounds = [o.name for o in bpy.data.objects if o.type == "EMPTY" and "bound" in o.name.lower()]
    world = bpy.context.scene.world.name if bpy.context.scene.world else None
    print(f"CAMERAS={cams}")
    print(f"BOUNDS={bounds}")
    print(f"WORLD={world}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
