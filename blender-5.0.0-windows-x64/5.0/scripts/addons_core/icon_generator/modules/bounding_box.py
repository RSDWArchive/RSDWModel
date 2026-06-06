import bpy
from mathutils import Vector
from .utils import log_message
from .constants import CAMERA_BOUNDS_NAME

def create_bounding_boxes():
    """Create bounding boxes for all mesh objects in the scene, ensuring cleanup first."""
    clear_bounding_boxes()

    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH' and not any(
            child.type == 'EMPTY' and child.name.startswith(f"BoundingBox_{obj.name}") for child in obj.children
        ):
            create_bounding_box_for_object(obj)
        else:
            log_message(f"Skipping '{obj.name}': Bounding box already exists.", "INFO")
    log_message("Bounding boxes created or validated for all objects.")

def create_bounding_box_for_object(obj):
    """Create a bounding box for a specific object."""
    obj_matrix_world = obj.matrix_world  # Ensure world-space transformation

    # Evaluate the object's mesh with all modifiers applied
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()

    if not mesh_eval:
        log_message(f"Failed to create bounding box for '{obj.name}'. No mesh data.", "ERROR")
        return

    # Initialize min and max corners for bounding box calculation
    min_corner = Vector((float("inf"), float("inf"), float("inf")))
    max_corner = Vector((float("-inf"), float("-inf"), float("-inf")))

    # Calculate bounding box by iterating through all vertices in world space
    for vertex in mesh_eval.vertices:
        world_vertex = obj_matrix_world @ vertex.co
        min_corner.x = min(min_corner.x, world_vertex.x)
        min_corner.y = min(min_corner.y, world_vertex.y)
        min_corner.z = min(min_corner.z, world_vertex.z)
        max_corner.x = max(max_corner.x, world_vertex.x)
        max_corner.y = max(max_corner.y, world_vertex.y)
        max_corner.z = max(max_corner.z, world_vertex.z)

    # Compute center and dimensions of the bounding box
    center = (min_corner + max_corner) / 2
    dimensions = max_corner - min_corner

    # Create an empty cube at the center of the bounding box
    bpy.ops.object.empty_add(type='CUBE', location=center)
    empty = bpy.context.active_object
    empty.name = f"BoundingBox_{obj.name}"
    empty.scale = dimensions / 2  # Scale the bounding box to the calculated dimensions

    # Clean up: Free the evaluated mesh
    obj_eval.to_mesh_clear()

    # Parent the object to the bounding box
    bpy.context.view_layer.objects.active = empty
    obj.select_set(True)
    empty.select_set(True)
    bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)

    # Link the bounding box to the same collection(s) as the object
    for collection in obj.users_collection:
        collection.objects.link(empty)

    # Unlink the bounding box from the scene root collection
    if empty.name in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.unlink(empty)

    log_message(f"Bounding box created and object parented for '{obj.name}'. Dimensions: {dimensions}.")

def clear_bounding_boxes():
    """Remove all existing bounding boxes from the scene."""
    bounding_boxes = [
        obj for obj in bpy.data.objects if obj.type == 'EMPTY' and obj.name.startswith("BoundingBox_")
    ]
    for bbox in bounding_boxes:
        bpy.data.objects.remove(bbox, do_unlink=True)

    log_message(f"Cleared {len(bounding_boxes)} bounding boxes from the scene.", "INFO")

def scale_bounding_boxes_to_fit():
    """Scale all bounding boxes to fit within the camera bounds."""
    bounds_object = bpy.data.objects.get(CAMERA_BOUNDS_NAME)
    if not bounds_object:
        log_message(f"Camera bounds object '{CAMERA_BOUNDS_NAME}' not found.", "ERROR")
        return

    bounds_scale = bounds_object.scale
    for obj in bpy.context.scene.objects:
        if obj.type == 'EMPTY' and obj.empty_display_type == 'CUBE' and obj.name.startswith("BoundingBox_"):
            scale_factors = [bounds_scale[i] / obj.scale[i] for i in range(3)]
            min_scale_factor = min(scale_factors)
            obj.scale = [dim * min_scale_factor for dim in obj.scale]
            obj.location = (0, 0, 0)  # Center the bounding box
            log_message(f"Scaled and centered bounding box '{obj.name}' to fit within camera bounds.")

class APPLY_BOUNDING_BOXES_OT_operator(bpy.types.Operator):
    """Operator to create bounding boxes, ensuring cleanup first."""
    bl_idname = "apply.bounding_boxes"
    bl_label = "Create Bounding Boxes"

    def execute(self, context):
        create_bounding_boxes()
        return {'FINISHED'}

class CLEAR_BOUNDING_BOXES_OT_operator(bpy.types.Operator):
    """Operator to clear all bounding boxes."""
    bl_idname = "clear.bounding_boxes"
    bl_label = "Clear Bounding Boxes"

    def execute(self, context):
        clear_bounding_boxes()
        return {'FINISHED'}

class SCALE_BOUNDING_BOXES_OT_operator(bpy.types.Operator):
    """Operator to scale bounding boxes to fit camera bounds."""
    bl_idname = "scale.bounding_boxes"
    bl_label = "Scale Bounding Boxes"

    def execute(self, context):
        scale_bounding_boxes_to_fit()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(APPLY_BOUNDING_BOXES_OT_operator)
    bpy.utils.register_class(CLEAR_BOUNDING_BOXES_OT_operator)
    bpy.utils.register_class(SCALE_BOUNDING_BOXES_OT_operator)

def unregister():
    bpy.utils.unregister_class(APPLY_BOUNDING_BOXES_OT_operator)
    bpy.utils.unregister_class(CLEAR_BOUNDING_BOXES_OT_operator)
    bpy.utils.unregister_class(SCALE_BOUNDING_BOXES_OT_operator)
