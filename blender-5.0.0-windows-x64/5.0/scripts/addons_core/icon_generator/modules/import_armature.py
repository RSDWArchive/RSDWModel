# modules/import_armature.py

import bpy
import os
from .utils import validate_path, log_message

def import_armature(filepath):
    """Import an armature from the specified .blend file."""
    log_message(f"Importing armature from: {filepath}")
    try:
        with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
            data_to.objects = [name for name in data_from.objects if name.startswith("Armature")]

        armature_obj = None
        for obj in data_to.objects:
            if obj and obj.type == 'ARMATURE':
                bpy.context.scene.collection.objects.link(obj)
                armature_obj = obj
                log_message(f"Armature '{obj.name}' imported.")
                break

        if not armature_obj:
            log_message(f"No armature found in {filepath}.", "WARNING")
            return

        # Parent all mesh objects to the armature
        for obj in bpy.context.scene.objects:
            if obj.type == 'MESH':
                obj.parent = armature_obj
                log_message(f"Parented '{obj.name}' to armature '{armature_obj.name}'.")
    except Exception as e:
        log_message(f"Failed to import armature: {e}", "ERROR")

class IMPORT_ARMATURE_OT_operator(bpy.types.Operator):
    """Operator to import an armature from a .blend file."""
    bl_idname = "import.armature"
    bl_label = "Import Armature"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        is_valid, error = validate_path(self.filepath)
        if not is_valid:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        import_armature(self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def register():
    bpy.utils.register_class(IMPORT_ARMATURE_OT_operator)

def unregister():
    bpy.utils.unregister_class(IMPORT_ARMATURE_OT_operator)
