# modules/bounding_scale.py

import bpy
from .constants import CAMERA_BOUNDS_NAME
from .utils import log_message

def scale_bounding_boxes_to_fit():
    """Scale all bounding boxes to fit within the camera bounds and center them at (0, 0, 0)."""
    bounds_object = bpy.data.objects.get(CAMERA_BOUNDS_NAME)
    if not bounds_object:
        log_message(f"Camera bounds object '{CAMERA_BOUNDS_NAME}' not found.", "ERROR")
        return

    bounds_scale = bounds_object.scale
    for obj in bpy.context.scene.objects:
        if obj.type == 'EMPTY' and obj.empty_display_type == 'CUBE' and obj.name.startswith("BoundingBox_"):
            # Scale the bounding box to fit the camera bounds
            scale_factors = [bounds_scale[i] / obj.scale[i] for i in range(3)]
            min_scale_factor = min(scale_factors)
            obj.scale = [dim * min_scale_factor for dim in obj.scale]
            log_message(f"Scaled '{obj.name}' to fit within camera bounds.")

            # Center the bounding box at the origin (0, 0, 0)
            obj.location = (0.0, 0.0, 0.0)
            log_message(f"Centered '{obj.name}' at the origin.")

class SCALE_BOUNDING_BOXES_OT_operator(bpy.types.Operator):
    """Operator to scale bounding boxes to fit camera bounds."""
    bl_idname = "scale.bounding_boxes"
    bl_label = "Scale Bounding Boxes"

    def execute(self, context):
        scale_bounding_boxes_to_fit()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SCALE_BOUNDING_BOXES_OT_operator)

def unregister():
    bpy.utils.unregister_class(SCALE_BOUNDING_BOXES_OT_operator)
