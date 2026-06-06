# modules/file_access.py

import bpy
import os
from .utils import log_message

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# Open Camera.blend Operator
class OPEN_CAMERA_BLEND_OT_operator(bpy.types.Operator):
    bl_idname = "open.camera_blend"
    bl_label = "Open Camera.blend"

    def execute(self, context):
        camera_blend_path = os.path.join(ASSETS_DIR, "Camera.blend")
        if os.path.exists(camera_blend_path):
            bpy.ops.wm.open_mainfile(filepath=camera_blend_path)
        else:
            self.report({'ERROR'}, f"Camera.blend not found at {camera_blend_path}")
        return {'FINISHED'}

# Open Rig.blend Operator
class OPEN_RIG_BLEND_OT_operator(bpy.types.Operator):
    bl_idname = "open.rig_blend"
    bl_label = "Open Rig.blend"

    def execute(self, context):
        rig_blend_path = os.path.join(ASSETS_DIR, "Rig.blend")
        if os.path.exists(rig_blend_path):
            bpy.ops.wm.open_mainfile(filepath=rig_blend_path)
        else:
            self.report({'ERROR'}, f"Rig.blend not found at {rig_blend_path}")
        return {'FINISHED'}

# Open World.blend Operator
class OPEN_WORLD_BLEND_OT_operator(bpy.types.Operator):
    bl_idname = "open.world_blend"
    bl_label = "Open World.blend"

    def execute(self, context):
        world_blend_path = os.path.join(ASSETS_DIR, "World.blend")
        if os.path.exists(world_blend_path):
            bpy.ops.wm.open_mainfile(filepath=world_blend_path)
        else:
            self.report({'ERROR'}, f"World.blend not found at {world_blend_path}")
        return {'FINISHED'}

def register():
    bpy.utils.register_class(OPEN_CAMERA_BLEND_OT_operator)
    bpy.utils.register_class(OPEN_RIG_BLEND_OT_operator)
    bpy.utils.register_class(OPEN_WORLD_BLEND_OT_operator)

def unregister():
    bpy.utils.unregister_class(OPEN_CAMERA_BLEND_OT_operator)
    bpy.utils.unregister_class(OPEN_RIG_BLEND_OT_operator)
    bpy.utils.unregister_class(OPEN_WORLD_BLEND_OT_operator)
