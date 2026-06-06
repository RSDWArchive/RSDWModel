"""Look at HierarchicalInstancedStaticMeshComponent / InstancedStaticMeshComponent
structure. Those store N instances of one mesh in a single component and are
the main mechanism for cliffs/rocks/foliage. We need to understand where the
per-instance transforms live before we can replicate the scene faithfully.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CHUNK_ROOT = Path(
    r"E:/Github/RSDWArchive/0.11.0.10/json/RSDragonwilds/Content/Maps/World/L_World/_Generated_"
)


def main() -> int:
    files = sorted(CHUNK_ROOT.glob("*.json"), key=lambda p: p.stat().st_size, reverse=True)
    print(f"biggest chunks:")
    for p in files[:5]:
        print(f"  {p.stat().st_size/1024/1024:6.1f} MB  {p.name}")

    # Pick a big chunk and dump the first HISM/ISM entry.
    for candidate in files:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        hism = next((e for e in data if e.get("Type") == "HierarchicalInstancedStaticMeshComponent"
                     and (e.get("Properties") or {}).get("StaticMesh")), None)
        if hism is None:
            continue
        print(f"\n=== HISM sample from {candidate.name} ===")
        props = hism.get("Properties", {}) or {}
        print(f"  Name: {hism.get('Name')}")
        print(f"  all prop keys: {list(props.keys())}")
        sm = props.get("StaticMesh", {})
        print(f"  StaticMesh: {sm.get('ObjectName') if isinstance(sm, dict) else sm}")
        # PerInstanceSMData / InstanceBuffer are the fields we care about
        for key in ("PerInstanceSMData", "PerInstanceSMCustomData", "InstanceBuffer"):
            val = props.get(key)
            if val is None:
                continue
            if isinstance(val, list):
                print(f"  {key}: list of {len(val)}")
                if val:
                    print(f"    first element: {json.dumps(val[0], indent=2)[:1500]}")
            else:
                print(f"  {key}: {type(val).__name__}  {json.dumps(val, indent=2)[:1500]}")
        # Show any transform-y key
        for k in ("RelativeLocation", "RelativeRotation", "RelativeScale3D"):
            if k in props:
                print(f"  {k}: {props[k]}")
        break
    else:
        print("no HISM with a StaticMesh found")
        return 1

    # Same for ISM
    for candidate in files:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        ism = next((e for e in data if e.get("Type") == "InstancedStaticMeshComponent"
                    and (e.get("Properties") or {}).get("StaticMesh")), None)
        if ism is None:
            continue
        print(f"\n=== ISM sample from {candidate.name} ===")
        props = ism.get("Properties", {}) or {}
        print(f"  all prop keys: {list(props.keys())}")
        sm = props.get("StaticMesh", {})
        print(f"  StaticMesh: {sm.get('ObjectName') if isinstance(sm, dict) else sm}")
        per = props.get("PerInstanceSMData")
        if isinstance(per, list) and per:
            print(f"  PerInstanceSMData: {len(per)} instances")
            print(f"  first element:\n{json.dumps(per[0], indent=2)[:1500]}")
        break

    # SplineMeshComponent — how is a spline mesh represented?
    for candidate in files:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        spline = next((e for e in data if e.get("Type") == "SplineMeshComponent"), None)
        if spline is None:
            continue
        print(f"\n=== SplineMeshComponent sample from {candidate.name} ===")
        props = spline.get("Properties", {}) or {}
        print(f"  all prop keys: {list(props.keys())}")
        for k in ("StaticMesh", "SplineParams", "SplineUpDir", "SplineBoundaryMin", "SplineBoundaryMax",
                  "StartPosition", "StartTangent", "EndPosition", "EndTangent",
                  "RelativeLocation", "RelativeRotation"):
            if k in props:
                print(f"  {k}: {json.dumps(props[k], indent=2)[:500]}")
        break

    # LevelInstanceComponent: this is how sub-levels (shared architecture packs
    # like "one cathedral") get referenced with a transform. Verify schema.
    for candidate in files:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        li = next((e for e in data if e.get("Type") == "LevelInstanceComponent"), None)
        if li is None:
            continue
        print(f"\n=== LevelInstanceComponent sample from {candidate.name} ===")
        print(json.dumps(li, indent=2)[:2000])
        break

    return 0


if __name__ == "__main__":
    sys.exit(main())
