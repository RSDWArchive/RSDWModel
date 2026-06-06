# modules/setup_mesh.py

import math
import bpy
from .utils import log_message

def select_all_mesh_objects():
    """Select all mesh objects in the scene."""
    bpy.ops.object.select_all(action='DESELECT')  # Deselect everything
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            obj.select_set(True)
    log_message("All mesh objects have been selected.")

class BATCHMESH_OT_select_all(bpy.types.Operator):
    """Operator to select all mesh objects."""
    bl_idname = "batchmesh.select_all"
    bl_label = "Select All Mesh Objects"

    def execute(self, context):
        select_all_mesh_objects()
        return {'FINISHED'}

class BATCHMESH_OT_apply_transforms(bpy.types.Operator):
    """Apply transform location and rotation to all selected mesh objects."""
    bl_idname = "batchmesh.apply_transforms"
    bl_label = "Batch Apply Transforms"

    def execute(self, context):
        # Fetch UI values
        loc_x = context.scene.batch_transform_loc_x
        loc_y = context.scene.batch_transform_loc_y
        loc_z = context.scene.batch_transform_loc_z

        # Convert rotation to radians
        rot_x = math.radians(context.scene.batch_transform_rot_x)
        rot_y = math.radians(context.scene.batch_transform_rot_y)
        rot_z = math.radians(context.scene.batch_transform_rot_z)

        # Get selected mesh objects
        selected_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']

        # Apply transforms to each object
        for obj in selected_objects:
            obj.location = (loc_x, loc_y, loc_z)  # Reset location
            obj.rotation_euler = (rot_x, rot_y, rot_z)  # Reset rotation

        # Report success
        self.report({'INFO'}, f"Applied transforms to {len(selected_objects)} objects.")
        return {'FINISHED'}

# Register and unregister functions for this module
def register():
    bpy.utils.register_class(BATCHMESH_OT_select_all)
    bpy.utils.register_class(BATCHMESH_OT_apply_transforms)  # Register the new operator

def unregister():
    bpy.utils.unregister_class(BATCHMESH_OT_select_all)
    bpy.utils.unregister_class(BATCHMESH_OT_apply_transforms)  # Unregister the new operator
