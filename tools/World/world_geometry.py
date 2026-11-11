"""Small glTF writer/reader for generated, uncompressed prototype geometry."""
import json
import struct
from pathlib import Path

import numpy as np

from world_source import write_json


def normals_for(positions, indices, normalize=True):
    normals = np.zeros_like(positions)
    triangles = positions[indices.reshape(-1, 3)]
    face = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    for corner in range(3):
        np.add.at(normals, indices.reshape(-1, 3)[:, corner], face)
    if normalize:
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-15)
    return normals


def write_mesh(path, positions, indices, uv=None, texture=None, normals=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    positions = np.asarray(positions, dtype="<f4")
    indices = np.asarray(indices, dtype="<u4").reshape(-1)
    normals = np.asarray(normals if normals is not None else normals_for(positions, indices), dtype="<f4")
    arrays = [positions, normals, indices]
    if uv is not None:
        arrays.append(np.asarray(uv, dtype="<f4"))
    data, views, accessors = bytearray(), [], []
    for i, array in enumerate(arrays):
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": array.nbytes})
        data.extend(array.tobytes())
        accessor = {"bufferView": i, "componentType": 5125 if i == 2 else 5126,
                    "count": len(array), "type": "SCALAR" if i == 2 else "VEC2" if i == 3 else "VEC3"}
        if i == 0:
            accessor.update(min=positions.min(axis=0).tolist(), max=positions.max(axis=0).tolist())
        accessors.append(accessor)
    path.with_suffix(".bin").write_bytes(data)
    attrs = {"POSITION": 0, "NORMAL": 1}
    if uv is not None:
        attrs["TEXCOORD_0"] = 3
    material = {"pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0.96}, "doubleSided": False}
    gltf = {"asset": {"version": "2.0", "generator": "RSDW World Region"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": attrs, "indices": 2, "material": 0}]}],
            "materials": [material], "buffers": [{"uri": path.with_suffix(".bin").name, "byteLength": len(data)}],
            "bufferViews": views, "accessors": accessors}
    if texture:
        gltf.update(images=[{"uri": texture}], textures=[{"source": 0, "sampler": 0}],
                    samplers=[{"wrapS": 33071, "wrapT": 33071}])
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    write_json(path, gltf)
    return gltf


def read_glb_mesh(path):
    raw = Path(path).read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError("Expected uncompressed binary glTF")
    json_size = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20+json_size])
    offset = 20+json_size
    binary = raw[offset+8:]
    def accessor(index):
        a = doc["accessors"][index]
        v = doc["bufferViews"][a["bufferView"]]
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]]
        start = v.get("byteOffset", 0)+a.get("byteOffset", 0)
        stride = v.get("byteStride", np.dtype(dtype).itemsize*components)
        return np.ndarray((a["count"], components), dtype=dtype, buffer=binary, offset=start,
                          strides=(stride, np.dtype(dtype).itemsize)).copy()
    out = []
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            out.append({"positions": accessor(primitive["attributes"]["POSITION"]),
                        "indices": accessor(primitive["indices"]).reshape(-1),
                        "uv": accessor(primitive["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in primitive["attributes"] else None})
    return out
