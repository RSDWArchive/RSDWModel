# modules/utils.py

import os
import bpy

def validate_path(path, is_directory=False):
    """Validate that a path exists and optionally check if it's a directory."""
    if not os.path.exists(path):
        return False, f"Path does not exist: {path}"
    if is_directory and not os.path.isdir(path):
        return False, f"Path is not a directory: {path}"
    return True, ""

def log_message(message, level="INFO"):
    """Log a message with a specified level."""
    print(f"[{level}] {message}")

def clean_scene():
    """Deselect all objects to ensure a clean starting state."""
    bpy.ops.object.select_all(action='DESELECT')

def ensure_render_settings():
    """Set default render settings for the active scene."""
    scene = bpy.context.scene
    scene.render.film_transparent = True  # Default transparent background
    scene.render.resolution_x = 512  # Default resolution width
    scene.render.resolution_y = 512  # Default resolution height
    scene.render.image_settings.file_format = 'PNG'  # Default to PNG format
    log_message("Render settings ensured to default values.", "INFO")

def apply_render_settings_on_load(dummy=None):
    """Apply render settings after the scene is loaded."""
    if bpy.context.scene:  # Only proceed if a valid scene is available
        ensure_render_settings()

def register():
    """Ensure render settings are applied after the scene is loaded."""
    bpy.app.handlers.load_post.append(apply_render_settings_on_load)
    log_message("Utils module registered.", "INFO")

def unregister():
    """Clean up render settings and remove handlers during unregistration."""
    bpy.app.handlers.load_post.remove(apply_render_settings_on_load)
    log_message("Utils module unregistered.", "INFO")
