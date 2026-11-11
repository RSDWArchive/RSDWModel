"""Identity-preserving Archive resolver and cooked Unreal transform math."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def identity_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def normalize_package(path):
    path = path.replace("\\", "/").strip("/")
    if path.startswith("Game/"):
        return "RSDragonwilds/Content/" + path[5:]
    if path.startswith("Engine/") and not path.startswith(("Engine/Content/", "Engine/Plugins/")):
        return "Engine/Content/" + path[7:]
    if not path.startswith(("RSDragonwilds/", "Engine/")) and "/" in path:
        mount, rest = path.split("/", 1)
        return f"RSDragonwilds/Plugins/GameFeatures/{mount}/Content/{rest}"
    return path


def ref_id(ref):
    path = ref.get("ObjectPath", "") if isinstance(ref, dict) else str(ref or "")
    return normalize_package(path) if path else ""


def vector(value=None, default=0):
    return np.array([(value or {}).get(k, default) for k in "XYZ"], dtype=float)


def quat_matrix(q):
    x, y, z = vector(q)
    w = q.get("W", 1)
    # Serialized FTransform uses normalized rotations; tolerate serialization noise.
    length = math.sqrt(x*x + y*y + z*z + w*w)
    if length < 1e-12:
        raise ValueError("Zero quaternion")
    x, y, z, w = (v / length for v in (x, y, z, w))
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def rotator_matrix(rot):
    # FRotator::Quaternion convention, distinct from user-authored JSON rotators.
    p, y, r = (math.radians((rot or {}).get(k, 0)) / 2 for k in ("Pitch", "Yaw", "Roll"))
    sp, sy, sr = math.sin(p), math.sin(y), math.sin(r)
    cp, cy, cr = math.cos(p), math.cos(y), math.cos(r)
    return quat_matrix(dict(X=cr*sp*sy-sr*cp*cy, Y=-cr*sp*cy-sr*cp*sy,
                            Z=cr*cp*sy-sr*sp*cy, W=cr*cp*cy+sr*sp*sy))


def matrix(location=None, rotation=None, scale=None, quaternion=False):
    out = np.eye(4)
    out[:3, :3] = (quat_matrix(rotation or {}) if quaternion else rotator_matrix(rotation)) @ np.diag(vector(scale, 1))
    out[:3, 3] = vector(location)
    return out


def instance_matrix(data):
    t = data.get("TransformData", data)
    return matrix(t.get("Translation"), t.get("Rotation"), t.get("Scale3D"), True)


def transform_bounds(bounds, transform):
    corners = np.array([list(p) + [1] for p in itertools.product(*zip(bounds[0], bounds[1]))])
    points = (transform @ corners.T).T[:, :3]
    return [points.min(axis=0).tolist(), points.max(axis=0).tolist()]


def intersects(a, b, dimensions=3):
    return all(a[0][i] <= b[1][i] and a[1][i] >= b[0][i] for i in range(dimensions))


def property_bounds(props):
    for key in ("CachedBounds", "CachedBounds[1]"):
        b = props.get(key, {})
        b = b.get("Value", b)
        if "Origin" in b and "BoxExtent" in b:
            origin, extent = vector(b["Origin"]), vector(b["BoxExtent"])
            return [(origin-extent).tolist(), (origin+extent).tolist()]
    return None


class Resolver:
    def __init__(self, root):
        self.root = Path(root)
        self.packages = {}
        self.properties = {}
        self.transforms = {}
        self.dependencies = set()
        self.issues = []
        self.engine_mounts = {}
        for content in (self.root / "Engine/Plugins").glob("**/Content"):
            mount = content.parent.name
            if not (self.root / "RSDragonwilds/Plugins/GameFeatures" / mount / "Content").is_dir():
                if mount in self.engine_mounts:
                    raise ValueError(f"Ambiguous engine plugin mount: {mount}")
                self.engine_mounts[mount] = content.relative_to(self.root).as_posix()

    def mounted_identity(self, identity):
        prefix = "RSDragonwilds/Plugins/GameFeatures/"
        if identity.startswith(prefix):
            mount, separator, rest = identity[len(prefix):].partition("/Content/")
            if separator and mount in self.engine_mounts:
                return self.engine_mounts[mount]+"/"+rest
        return identity

    def package(self, name):
        name = self.mounted_identity(normalize_package(name))
        if name not in self.packages:
            path = self.root / (name + ".json")
            self.packages[name] = read_json(path)
            self.dependencies.add(path)
        return self.packages[name]

    def get(self, identity):
        identity = self.canonical(identity)
        package, index = identity.rsplit(".", 1)
        if not index.isdigit():
            raise ValueError(f"Reference has no version-bound export index: {identity}")
        rows = self.package(package)
        return rows[int(index)]

    def canonical(self, ref):
        identity = self.mounted_identity(ref_id(ref))
        if "." in identity and identity.rsplit(".", 1)[1].isdigit():
            return identity
        package = identity.split(".", 1)[0]
        name = identity.rsplit(".", 1)[1] if "." in identity else package.rsplit("/", 1)[-1]
        if isinstance(ref, dict) and "'" in ref.get("ObjectName", ""):
            name = ref["ObjectName"].split("'", 1)[1].rstrip("'")
        found = [i for i, obj in enumerate(self.package(package)) if obj.get("Name") == name]
        if len(found) != 1:
            raise ValueError(f"Ambiguous or missing named export: {ref}")
        return f"{package}.{found[0]}"

    def effective(self, identity, chain=()):
        identity = ref_id(identity)
        if identity in self.properties:
            return self.properties[identity]
        if identity in chain:
            raise ValueError(f"Template cycle: {chain} -> {identity}")
        obj = self.get(identity)
        props = {}
        template = ref_id(obj.get("Template"))
        if template and "/Script/" not in template:
            try:
                props.update(self.effective(template, chain + (identity,)))
            except (FileNotFoundError, ValueError, IndexError) as exc:
                self.issues.append({"source": identity, "template": template, "error": str(exc)})
        props.update(obj.get("Properties", {}))
        self.properties[identity] = props
        return props

    def transform(self, identity, chain=()):
        identity = ref_id(identity)
        if identity in self.transforms:
            return self.transforms[identity]
        if identity in chain:
            raise ValueError(f"Attachment cycle: {chain} -> {identity}")
        props = self.effective(identity)
        local = matrix(props.get("RelativeLocation"), props.get("RelativeRotation"), props.get("RelativeScale3D"))
        # Instance attachments must not resolve to a Blueprint template's parent.
        parent = ref_id(self.get(identity).get("Properties", {}).get("AttachParent"))
        result = local
        if parent:
            parent_m = self.transform(parent, chain + (identity,))
            result = parent_m @ local
            if props.get("bAbsoluteLocation"):
                result[:3, 3] = local[:3, 3]
            if props.get("bAbsoluteRotation") or props.get("bAbsoluteScale"):
                parent_scale = np.linalg.norm(parent_m[:3, :3], axis=0)
                parent_rotation = parent_m[:3, :3] / np.maximum(parent_scale, 1e-15)
                if props.get("bAbsoluteRotation"):
                    parent_rotation = np.eye(3)
                if props.get("bAbsoluteScale"):
                    parent_scale = np.ones(3)
                result[:3, :3] = parent_rotation @ np.diag(parent_scale) @ local[:3, :3]
            socket = props.get("AttachSocketName")
            if socket and socket != "None":
                self.issues.append({"source": identity, "socket": socket, "error": "Socket attachment needs explicit resolution"})
        self.transforms[identity] = result
        return result

    def inherited_instances(self, identity):
        seen = set()
        while identity and identity not in seen:
            seen.add(identity)
            obj = self.get(identity)
            if "PerInstanceSMData" in obj:
                return obj["PerInstanceSMData"]
            identity = ref_id(obj.get("Template"))
        return None

    def terrain(self, packages, bounds, excluded):
        rows = []
        for package in packages:
            for index, obj in enumerate(self.package(package)):
                if obj.get("Type") != "LandscapeComponent":
                    continue
                identity = f"{package}.{index}"
                props = self.effective(identity)
                owner_id = ref_id(obj["Outer"])
                owner = self.effective(owner_id)
                material = ref_id(owner.get("LandscapeMaterial"))
                if any(s.lower() in material.lower() for s in excluded):
                    continue
                local = props.get("CachedLocalBox")
                if not local:
                    raise ValueError(f"Landscape has no bounds: {identity}")
                world_bounds = transform_bounds([vector(local["Min"]), vector(local["Max"])], self.transform(identity))
                if intersects(world_bounds, bounds):
                    rows.append({"source": identity, "name": obj["Name"], "owner": owner_id,
                                 "material": material, "matrix": self.transform(identity).tolist(),
                                 "bounds": world_bounds, "properties": props})
        return rows
