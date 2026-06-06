# modules/object_cleanup.py

import bpy
from .utils import log_message

def delete_empty_objects():
    """Delete all empty objects in the scene."""
    empties = [obj for obj in bpy.context.scene.objects if obj.type == 'EMPTY']
    for empty in empties:
        bpy.data.objects.remove(empty, do_unlink=True)
    log_message(f"Deleted {len(empties)} empty objects.")

def delete_empty_collections():
    """Delete all empty collections in the scene."""
    def is_collection_empty(collection):
        return not collection.objects and all(is_collection_empty(child) for child in collection.children)

    empty_collections = [coll for coll in bpy.data.collections if is_collection_empty(coll)]
    for coll in empty_collections:
        bpy.data.collections.remove(coll)
    log_message(f"Deleted {len(empty_collections)} empty collections.")

def delete_all_cameras():
    """Delete all cameras in the scene."""
    cameras = [obj for obj in bpy.context.scene.objects if obj.type == 'CAMERA']
    for camera in cameras:
        bpy.data.objects.remove(camera, do_unlink=True)
    log_message(f"Deleted {len(cameras)} cameras.")

def reset_scene():
    """Reset the scene by deleting empties, cameras, and empty collections."""
    delete_empty_objects()
    delete_all_cameras()
    delete_empty_collections()
    log_message("Scene reset complete.")

class RESET_SCENE_OT_operator(bpy.types.Operator):
    """Operator to reset the scene."""
    bl_idname = "reset.scene"
    bl_label = "Reset Scene"

    def execute(self, context):
        reset_scene()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(RESET_SCENE_OT_operator)

def unregister():
    bpy.utils.unregister_class(RESET_SCENE_OT_operator)
