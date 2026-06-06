# ui_panel.py

import bpy
import os
from .utils import log_message
from .render_objects import RENDER_ALL_OT_operator
from .setup_mesh import BATCHMESH_OT_select_all

# Helper functions for camera dropdown and updates
def get_camera_items(self, context):
    """Dynamically generate a list of cameras in the scene for the dropdown."""
    cameras = [(cam.name, cam.name, "") for cam in bpy.data.objects if cam.type == 'CAMERA']
    return cameras if cameras else [("None", "None", "No cameras found")]

def update_active_camera(self, context):
    """Update the active camera in the scene."""
    camera = bpy.data.objects.get(context.scene.active_camera)
    if camera and camera.type == 'CAMERA':
        context.scene.camera = camera

class ICON_GENERATOR_PT_import(bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "VIEW3D_PT_icon_generator_import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"

    def draw(self, context):
        layout = self.layout
        layout.operator("import.batch", text="Batch Import")

class ICON_GENERATOR_PT_setup(bpy.types.Panel):
    bl_label = "Setup"
    bl_idname = "VIEW3D_PT_icon_generator_setup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_options = {'DEFAULT_CLOSED'} 

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("import.camera", text="Cameras")
        row.operator("setup.rig", text="Armature")
        row.operator("setup.world", text="World")

class ICON_GENERATOR_PT_advanced(bpy.types.Panel):
    bl_label = "Advanced"
    bl_idname = "VIEW3D_PT_icon_generator_advanced"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_parent_id = "VIEW3D_PT_icon_generator_setup"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.operator("open.camera_blend", text="Open Camera.blend")
        layout.operator("open.rig_blend", text="Open Rig.blend")
        layout.operator("open.world_blend", text="Open World.blend")

class ICON_GENERATOR_PT_mesh(bpy.types.Panel):
    bl_label = "Mesh"
    bl_idname = "VIEW3D_PT_icon_generator_mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # Button to select all mesh objects
        layout.operator("batchmesh.select_all", text="Select All Mesh Objects")

class ICON_GENERATOR_PT_batch_transforms(bpy.types.Panel):
    bl_label = "Batch Transforms"
    bl_idname = "VIEW3D_PT_icon_generator_batch_transforms"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_parent_id = "VIEW3D_PT_icon_generator_mesh"  # Set the parent panel
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # Location fields
        col = layout.column(align=True)
        col.label(text="Location:")
        col.prop(context.scene, "batch_transform_loc_x", text="X")
        col.prop(context.scene, "batch_transform_loc_y", text="Y")
        col.prop(context.scene, "batch_transform_loc_z", text="Z")

        # Rotation fields
        col.separator()
        col.label(text="Rotation:")
        col.prop(context.scene, "batch_transform_rot_x", text="X")
        col.prop(context.scene, "batch_transform_rot_y", text="Y")
        col.prop(context.scene, "batch_transform_rot_z", text="Z")

        # Button to apply transforms
        layout.operator("batchmesh.apply_transforms", text="Batch Apply Transforms")

class ICON_GENERATOR_PT_bounding_boxes(bpy.types.Panel):
    bl_label = "Bounding Boxes"
    bl_idname = "VIEW3D_PT_icon_generator_bounding_boxes"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_options = {'DEFAULT_CLOSED'} 

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("apply.bounding_boxes", text="Create")
        row.operator("scale.bounding_boxes", text="Scale")
        row.operator("clear.bounding_boxes", text="Clear")

class ICON_GENERATOR_PT_camera(bpy.types.Panel):
    bl_label = "Camera"
    bl_idname = "VIEW3D_PT_icon_generator_camera"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_options = {'DEFAULT_CLOSED'}     

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Active Camera:")
        layout.prop(scene, "active_camera", text="")
        layout.operator("view.active_camera", text="View Active Camera")

class ICON_GENERATOR_PT_render(bpy.types.Panel):
    bl_label = "Render"
    bl_idname = "VIEW3D_PT_icon_generator_render"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Icon Generator"
    bl_options = {'DEFAULT_CLOSED'} 

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Render Resolution:")
        layout.prop(scene, "render_resolution_x", text="Width")
        layout.prop(scene, "render_resolution_y", text="Height")

        layout.label(text="Render Output Format:")
        layout.prop(scene.render.image_settings, "file_format", text="")
        layout.prop(scene.render, "film_transparent", text="Transparent Background")

        layout.operator("render.all", text="Render All Icons")

# Registration functions
def register():
    bpy.utils.register_class(ICON_GENERATOR_PT_import)
    bpy.utils.register_class(ICON_GENERATOR_PT_setup)
    bpy.utils.register_class(ICON_GENERATOR_PT_advanced)
    bpy.utils.register_class(ICON_GENERATOR_PT_mesh)
    bpy.utils.register_class(ICON_GENERATOR_PT_batch_transforms)
    bpy.utils.register_class(ICON_GENERATOR_PT_bounding_boxes)
    bpy.utils.register_class(ICON_GENERATOR_PT_camera)
    bpy.utils.register_class(ICON_GENERATOR_PT_render)

    bpy.types.Scene.active_camera = bpy.props.EnumProperty(
        name="Active Camera",
        items=get_camera_items,
        update=update_active_camera,
        description="Select the active camera for rendering"
    )

    bpy.types.Scene.render_resolution_x = bpy.props.IntProperty(
        name="Width",
        default=512,
        min=1,
        description="Render resolution width"
    )
    bpy.types.Scene.render_resolution_y = bpy.props.IntProperty(
        name="Height",
        default=512,
        min=1,
        description="Render resolution height"
    )

def unregister():
    bpy.utils.unregister_class(ICON_GENERATOR_PT_import)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_setup)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_advanced)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_mesh)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_batch_transforms)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_bounding_boxes)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_camera)
    bpy.utils.unregister_class(ICON_GENERATOR_PT_render)
    
    del bpy.types.Scene.active_camera
    del bpy.types.Scene.render_resolution_x
    del bpy.types.Scene.render_resolution_y
