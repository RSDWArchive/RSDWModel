# modules/import_world.py

import bpy
import os
from .utils import validate_path, log_message

def import_world(filepath, world_name="WorldIcon"):
    """Import a world from the specified .blend file."""
    log_message(f"Importing world '{world_name}' from: {filepath}")
    try:
        with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
            data_to.worlds = [name for name in data_from.worlds if name == world_name]

        if world_name in bpy.data.worlds:
            bpy.context.scene.world = bpy.data.worlds[world_name]
            log_message(f"World '{world_name}' imported and applied to the scene.")
        else:
            log_message(f"World '{world_name}' not found in {filepath}.", "WARNING")
    except Exception as e:
        log_message(f"Failed to import world: {e}", "ERROR")

class IMPORT_WORLD_OT_operator(bpy.types.Operator):
    """Operator to import a world from a .blend file."""
    bl_idname = "import.world"
    bl_label = "Import World"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        is_valid, error = validate_path(self.filepath)
        if not is_valid:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        import_world(self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def register():
    bpy.utils.register_class(IMPORT_WORLD_OT_operator)

def unregister():
    bpy.utils.unregister_class(IMPORT_WORLD_OT_operator)
