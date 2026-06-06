# __init__.py

bl_info = {
    "name": "Icon Generator",
    "author": "Beb",
    "version": (1, 0),
    "blender": (4, 2, 5),
    "location": "View3D > Tools",
    "description": "Automates the generation of icons by importing, processing, and rendering collections.",
    "category": "Import-Export",
}

import bpy
from .modules import (
    utils,
    import_collections,
    import_camera,
    setup_world,
    setup_rig,
    bounding_box,
    bounding_scale,
    render_objects,
    ui_panel,
    file_access,
    setup_mesh,
)

def register():
    # Register modules and operators
    bpy.types.Scene.batch_transform_loc_x = bpy.props.FloatProperty(name="X", default=0.0)
    bpy.types.Scene.batch_transform_loc_y = bpy.props.FloatProperty(name="Y", default=0.0)
    bpy.types.Scene.batch_transform_loc_z = bpy.props.FloatProperty(name="Z", default=0.0)
    bpy.types.Scene.batch_transform_rot_x = bpy.props.FloatProperty(name="X", default=0.0)
    bpy.types.Scene.batch_transform_rot_y = bpy.props.FloatProperty(name="Y", default=0.0)
    bpy.types.Scene.batch_transform_rot_z = bpy.props.FloatProperty(name="Z", default=0.0)

    utils.register()
    import_collections.register()
    import_camera.register()
    setup_world.register()
    setup_rig.register()
    bounding_box.register()
    bounding_scale.register()
    render_objects.register()
    file_access.register()
    setup_mesh.register()
    ui_panel.register()
    print("Icon Generator add-on registered.")

def unregister():
    # Unregister properties
    del bpy.types.Scene.batch_transform_loc_x
    del bpy.types.Scene.batch_transform_loc_y
    del bpy.types.Scene.batch_transform_loc_z
    del bpy.types.Scene.batch_transform_rot_x
    del bpy.types.Scene.batch_transform_rot_y
    del bpy.types.Scene.batch_transform_rot_z
    
    utils.unregister()
    import_collections.unregister()
    import_camera.unregister()
    setup_world.unregister()
    setup_rig.unregister()
    bounding_box.unregister()
    bounding_scale.unregister()
    render_objects.unregister()
    file_access.unregister()
    setup_mesh.unregister()
    ui_panel.unregister()
    print("Icon Generator add-on unregistered.")
