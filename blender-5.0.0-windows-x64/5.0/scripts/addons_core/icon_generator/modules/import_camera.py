import bpy
import os
from .utils import log_message

def setup_cameras():
    """Setup cameras by appending the Camera collection from Camera.blend."""
    addon_dir = os.path.dirname(__file__)  # Directory of this script
    camera_blend_path = os.path.join(addon_dir, "../assets/Camera.blend")
    camera_blend_path = bpy.path.abspath(camera_blend_path)

    if not os.path.exists(camera_blend_path):
        log_message(f"Error: Camera.blend file not found at {camera_blend_path}", "ERROR")
        return

    collection_name = "Camera"
    with bpy.data.libraries.load(camera_blend_path, link=False) as (data_from, data_to):
        if collection_name in data_from.collections:
            data_to.collections = [collection_name]
        else:
            log_message(f"Error: '{collection_name}' not found in Camera.blend", "ERROR")
            return

    for collection in data_to.collections:
        if collection is not None:
            bpy.context.scene.collection.children.link(collection)

    scene = bpy.context.scene
    if hasattr(scene, "render_resolution_x"):
        scene.render.resolution_x = scene.render_resolution_x
        scene.render.resolution_y = scene.render_resolution_y

    log_message("Camera collection appended and settings applied.")

def view_active_camera(context):
    """Set the viewport to view through the active camera."""
    scene = context.scene
    camera = bpy.data.objects.get(scene.active_camera)
    if camera and camera.type == 'CAMERA':
        scene.camera = camera
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.region_3d.view_perspective = 'CAMERA'
                        break
        log_message(f"Switched to view through camera: {camera.name}")
    else:
        log_message("No active camera found or set.", "WARNING")

def get_camera_items(self, context):
    """Dynamically generate a list of cameras in the scene for the dropdown."""
    cameras = [(cam.name, cam.name, "") for cam in bpy.data.objects if cam.type == 'CAMERA']
    return cameras if cameras else [("None", "None", "No cameras found")]

def update_active_camera(self, context):
    """Update the active camera in the scene."""
    camera = bpy.data.objects.get(context.scene.active_camera)
    if camera and camera.type == 'CAMERA':
        context.scene.camera = camera

class IMPORT_CAMERA_OT_operator(bpy.types.Operator):
    bl_idname = "import.camera"
    bl_label = "Import Cameras"

    def execute(self, context):
        setup_cameras()
        return {'FINISHED'}

class VIEW_ACTIVE_CAMERA_OT_operator(bpy.types.Operator):
    bl_idname = "view.active_camera"
    bl_label = "View Active Camera"

    def execute(self, context):
        view_active_camera(context)
        return {'FINISHED'}

def register():
    bpy.utils.register_class(IMPORT_CAMERA_OT_operator)
    bpy.utils.register_class(VIEW_ACTIVE_CAMERA_OT_operator)

def unregister():
    bpy.utils.unregister_class(IMPORT_CAMERA_OT_operator)
    bpy.utils.unregister_class(VIEW_ACTIVE_CAMERA_OT_operator)
