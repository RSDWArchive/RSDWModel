# modules/setup_world.py

import bpy
import os
from .utils import log_message

def setup_world():
    """Import the world settings from World.blend and apply them."""
    world_blend_path = os.path.join(os.path.dirname(__file__), "../assets/World.blend")
    world_blend_path = bpy.path.abspath(world_blend_path)

    if not os.path.exists(world_blend_path):
        log_message(f"Error: World.blend file not found at {world_blend_path}", "ERROR")
        return

    with bpy.data.libraries.load(world_blend_path, link=False) as (data_from, data_to):
        data_to.worlds = [name for name in data_from.worlds]

    if data_to.worlds:
        bpy.context.scene.world = data_to.worlds[0]
        log_message(f"World '{data_to.worlds[0].name}' applied successfully.")
    else:
        log_message("No world settings found in World.blend", "ERROR")

class SETUP_WORLD_OT_operator(bpy.types.Operator):
    """Operator to setup the world."""
    bl_idname = "setup.world"
    bl_label = "Setup World"

    def execute(self, context):
        setup_world()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SETUP_WORLD_OT_operator)

def unregister():
    bpy.utils.unregister_class(SETUP_WORLD_OT_operator)
