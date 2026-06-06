import bpy
import os
from .utils import validate_path, log_message

def import_collections_from_blend(filepath):
    """Import collections from a .blend file."""
    log_message(f"Importing collections from: {filepath}")
    try:
        with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
            data_to.collections = data_from.collections

        for collection in data_to.collections:
            if collection:
                bpy.context.scene.collection.children.link(collection)
                log_message(f"Collection '{collection.name}' imported.")
    except Exception as e:
        log_message(f"Failed to import collections from {filepath}: {e}", "ERROR")

def import_meshes_from_blend(filepath):
    """Import all mesh objects from a .blend file, regardless of their hierarchy."""
    log_message(f"Importing meshes from: {filepath}")
    try:
        # Define the path to the objects in the .blend file
        blend_dir = os.path.join(filepath, "Object")

        # List all objects in the .blend file
        with bpy.data.libraries.load(filepath, link=False) as (data_from, _):
            object_names = [name for name in data_from.objects]

        # Append each object of type 'MESH'
        for obj_name in object_names:
            append_path = os.path.join(filepath, "Object", obj_name)
            bpy.ops.wm.append(filepath=append_path, directory=blend_dir, filename=obj_name)
            obj = bpy.context.scene.objects.get(obj_name)
            if obj and obj.type == 'MESH':
                log_message(f"Mesh object '{obj.name}' appended to the scene.")
    except Exception as e:
        log_message(f"Failed to import meshes from {filepath}: {e}", "ERROR")

class IMPORT_OT_batch(bpy.types.Operator):
    """Operator to batch import files."""
    bl_idname = "import.batch"
    bl_label = "Batch Import"
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    # Dropdown for file types
    file_type: bpy.props.EnumProperty(
        name="File Type",
        description="Select the type of files to batch import",
        items=[
            ("BLEND", ".blend", "Import .blend files"),
            ("GLB", ".glb", "Import .glb files"),
            ("FBX", ".fbx", "Import .fbx files"),
        ],
        default="BLEND",
    )

    # Dropdown for import options (only for .blend files)
    import_option: bpy.props.EnumProperty(
        name="",
        description="Choose what to import from the selected file type",
        items=[
            ("COLLECTIONS", "Collections", "Import collections from all .blend files in the directory"),
            ("OBJECTS", "Objects", "Import objects (meshes) from all .blend files in the directory"),
        ],
        default="COLLECTIONS",
    )

    def execute(self, context):
        # Validate the directory
        is_valid, error = validate_path(self.directory, is_directory=True)
        if not is_valid:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        # Get all files of the selected type in the directory
        extension = self.file_type.lower()
        valid_files = [f for f in os.listdir(self.directory) if f.lower().endswith(extension)]
        if not valid_files:
            self.report({'WARNING'}, f"No {extension} files found in the selected directory.")
            return {'CANCELLED'}

        # Process each file
        for file in valid_files:
            filepath = os.path.join(self.directory, file)
            if self.file_type == "BLEND":
                # Handle .blend files with collection/object options
                if self.import_option == "COLLECTIONS":
                    import_collections_from_blend(filepath)
                elif self.import_option == "OBJECTS":
                    import_meshes_from_blend(filepath)
            elif self.file_type == "GLB":
                # Import .glb file
                bpy.ops.import_scene.gltf(filepath=filepath)
                log_message(f"Imported .glb file: {filepath}")
            elif self.file_type == "FBX":
                # Import .fbx file
                bpy.ops.import_scene.fbx(filepath=filepath)
                log_message(f"Imported .fbx file: {filepath}")

        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout

        # Label for folder path
        layout.label(text="Batch Imports from Folder Path:")

        # Dropdown for file types
        layout.prop(self, "file_type", text="")

        # Dropdown for import options (only for .blend files)
        if self.file_type == "BLEND":
            layout.prop(self, "import_option", text="")

def register():
    bpy.utils.register_class(IMPORT_OT_batch)

def unregister():
    bpy.utils.unregister_class(IMPORT_OT_batch)
