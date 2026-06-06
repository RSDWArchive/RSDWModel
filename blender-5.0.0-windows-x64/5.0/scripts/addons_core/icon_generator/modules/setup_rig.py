# modules/setup_rig.py

import bpy
import os
from .utils import log_message

def setup_rig():
    """Import the rig, link it to the scene, parent objects to it, and add armature modifiers."""
    # Path to Rig.blend
    rig_blend_path = os.path.join(os.path.dirname(__file__), "../assets/Rig.blend")
    rig_blend_path = bpy.path.abspath(rig_blend_path)

    # Check if the file exists
    if not os.path.exists(rig_blend_path):
        log_message(f"Error: Rig.blend file not found at {rig_blend_path}", "ERROR")
        return

    # Load the rig
    with bpy.data.libraries.load(rig_blend_path, link=False) as (data_from, data_to):
        # Filter objects whose names contain "Rig" or "Armature"
        data_to.objects = [
            name for name in data_from.objects if "Rig" in name or "Armature" in name
        ]

    if not data_to.objects:
        log_message("Error: No objects found in Rig.blend matching 'Rig' or 'Armature'", "ERROR")
        return

    # Link the rig objects to the scene
    rig_objects = []
    for obj in data_to.objects:
        if obj is not None:  # Ensure valid object
            bpy.context.scene.collection.objects.link(obj)
            rig_objects.append(obj)

    if not rig_objects:
        log_message("Error: Rig objects could not be linked to the scene.", "ERROR")
        return

    # Parent all mesh objects to the rig and add armature modifiers
    rig = rig_objects[0]  # Assume the first imported object is the main rig
    if rig.type == 'ARMATURE':
        log_message(f"Rig '{rig.name}' imported and linked successfully.")
        parent_and_add_modifiers_to_objects(rig)
    else:
        log_message(f"Error: The imported rig '{rig.name}' is not an armature.", "ERROR")


def parent_and_add_modifiers_to_objects(rig):
    """Parent all mesh objects in the scene to the rig and set up armature modifiers."""
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            # Parent the mesh to the rig
            obj.parent = rig
            log_message(f"Object '{obj.name}' parented to rig '{rig.name}'.")

            # Check if the object already has an armature modifier
            armature_modifier = None
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE':
                    armature_modifier = mod
                    break

            # Add or update the armature modifier
            if armature_modifier:
                armature_modifier.object = rig
                log_message(f"Armature modifier updated for object '{obj.name}'.")
            else:
                armature_modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
                armature_modifier.object = rig
                log_message(f"Armature modifier added to object '{obj.name}'.")


class SETUP_RIG_OT_operator(bpy.types.Operator):
    """Operator to setup the rig."""
    bl_idname = "setup.rig"
    bl_label = "Setup Rig"

    def execute(self, context):
        setup_rig()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SETUP_RIG_OT_operator)

def unregister():
    bpy.utils.unregister_class(SETUP_RIG_OT_operator)
