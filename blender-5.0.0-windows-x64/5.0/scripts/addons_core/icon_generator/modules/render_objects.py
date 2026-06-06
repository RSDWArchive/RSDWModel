import bpy
from .utils import log_message

def ensure_camera_exists(scene):
    """Ensure there is a valid active camera in the scene. If not, create one."""
    if not scene.camera:
        # Check if there are any cameras in the scene
        cameras = [obj for obj in bpy.data.objects if obj.type == 'CAMERA']
        if cameras:
            scene.camera = cameras[0]
            log_message(f"No active camera found. Using {cameras[0].name} as the active camera.")
        else:
            # Create a new camera if no cameras exist
            bpy.ops.object.camera_add(location=(0, -5, 5))
            new_camera = bpy.context.active_object
            new_camera.name = "Auto_Camera"
            new_camera.rotation_euler = (1.1, 0, 0)  # Point toward origin
            scene.camera = new_camera
            log_message("No camera found. Created a new camera for rendering.")

def collection_contains_mesh(collection):
    """Check if a collection contains at least one mesh object."""
    return any(obj.type == 'MESH' for obj in collection.objects)

def render_by_collection(export_directory):
    """Render each collection that contains at least one mesh object."""
    scene = bpy.context.scene
    ensure_camera_exists(scene)  # Ensure a camera exists for rendering

    # Set resolution dynamically from UI properties
    scene.render.resolution_x = scene.render_resolution_x
    scene.render.resolution_y = scene.render_resolution_y

    for collection in bpy.data.collections:
        # Skip collections without any mesh objects
        if not collection_contains_mesh(collection):
            log_message(f"Skipping collection '{collection.name}' (no mesh objects).", "INFO")
            continue

        # Deselect all objects
        bpy.ops.object.select_all(action='DESELECT')

        # Hide all collections except the current one
        for other_collection in bpy.data.collections:
            for obj in other_collection.objects:
                obj.hide_render = other_collection != collection

        # Set all objects in the collection to be visible
        for obj in collection.objects:
            obj.hide_render = False

        # Set the output path for the render
        output_path = f"{export_directory}/{collection.name}.png"
        scene.render.filepath = output_path

        # Render the collection
        log_message(f"Rendering collection '{collection.name}' to {output_path}")
        bpy.ops.render.render(write_still=True)

    # Restore original visibility for all objects
    for obj in bpy.data.objects:
        obj.hide_render = False

    log_message("All collections rendered successfully.")

class RENDER_ALL_OT_operator(bpy.types.Operator):
    """Operator to render all collections individually."""
    bl_idname = "render.all"
    bl_label = "Render All Collections"

    directory: bpy.props.StringProperty(
        name="Export Directory",
        default="//",
        subtype='DIR_PATH'
    )

    def execute(self, context):
        render_by_collection(self.directory)  # Corrected function name
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def register():
    bpy.utils.register_class(RENDER_ALL_OT_operator)

def unregister():
    bpy.utils.unregister_class(RENDER_ALL_OT_operator)
