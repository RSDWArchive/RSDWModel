"""Dump World.blend contents to see what comes with the world shader."""
from __future__ import annotations

from pathlib import Path

import bpy  # type: ignore

WORLD_BLEND = Path(__file__).resolve().parents[2] / "Icon_Generator-main" / "assets" / "World.blend"

print(f"Loading: {WORLD_BLEND}")
with bpy.data.libraries.load(str(WORLD_BLEND), link=False) as (data_from, data_to):
    data_to.worlds = list(data_from.worlds)
    data_to.collections = list(data_from.collections)
    data_to.objects = list(data_from.objects)
    data_to.meshes = list(data_from.meshes)

print("Worlds:", [w.name for w in data_to.worlds if w is not None])
print("Collections:", [c.name for c in data_to.collections if c is not None])
print("Objects (appended from world.blend):")
for obj in data_to.objects:
    if obj is None:
        continue
    print(f"  - [{obj.type}] {obj.name}")
print("Meshes:", [m.name for m in data_to.meshes if m is not None])
