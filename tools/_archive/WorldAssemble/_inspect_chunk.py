"""One-off schema probe for a single L_World chunk JSON.

Reads a chunk, enumerates all StaticMeshComponent entries and prints every
property key they carry (so we can confirm the Location/Rotation/Scale fields
actually live on the component, and see whether StaticMeshActor entries hold
their own transform). Also summarises what top-level actor types appear and
how many reference meshes we could build a placement list from.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

CHUNK_ROOT = Path(
    r"E:/Github/RSDWArchive/0.11.0.10/json/RSDragonwilds/Content/Maps/World/L_World/_Generated_"
)

TRANSFORM_KEYS = (
    "RelativeLocation",
    "RelativeRotation",
    "RelativeScale3D",
    "Location",
    "Rotation",
    "Scale",
    "Scale3D",
    "AbsoluteLocation",
    "AbsoluteRotation",
    "AbsoluteScale",
)


def main() -> int:
    files = sorted(CHUNK_ROOT.glob("*.json"), key=lambda p: p.stat().st_size)
    if not files:
        print("no chunks found")
        return 2
    target = files[len(files) // 2]
    print(f"CHUNK: {target.name} ({target.stat().st_size/1024:.1f} KB)")
    data = json.loads(target.read_text(encoding="utf-8"))
    print(f"entries: {len(data)}")

    type_counts = Counter(e.get("Type") for e in data)
    print("\n--- top 15 types ---")
    for t, n in type_counts.most_common(15):
        print(f"  {n:4}  {t}")

    # Every StaticMeshComponent: what properties does it carry?
    print("\n--- all StaticMeshComponent entries ---")
    for i, e in enumerate(data):
        if e.get("Type") != "StaticMeshComponent":
            continue
        props = e.get("Properties", {}) or {}
        sm = props.get("StaticMesh", {})
        sm_name = sm.get("ObjectName") if isinstance(sm, dict) else None
        sm_path = sm.get("ObjectPath") if isinstance(sm, dict) else None
        tx_keys = [k for k in TRANSFORM_KEYS if k in props]
        print(f"idx {i:3}  Name={e.get('Name')}")
        print(f"          Outer={e.get('Outer')}")
        print(f"          SM={sm_name}  ({sm_path})")
        print(f"          tx_keys={tx_keys}")
        print(f"          all_prop_keys={list(props.keys())}")
        for k in tx_keys:
            print(f"            {k} = {props[k]}")

    # Every StaticMeshActor: what does it carry?
    print("\n--- all StaticMeshActor entries (first 3) ---")
    sma_count = 0
    for i, e in enumerate(data):
        if e.get("Type") != "StaticMeshActor":
            continue
        sma_count += 1
        if sma_count > 3:
            continue
        props = e.get("Properties", {}) or {}
        tx_keys = [k for k in TRANSFORM_KEYS if k in props]
        print(f"idx {i:3}  Label={e.get('ActorLabel')}  Name={e.get('Name')}")
        print(f"          tx_keys on Actor={tx_keys}")
        print(f"          all_prop_keys={list(props.keys())}")
    print(f"\n(total StaticMeshActor: {sma_count})")

    # Where do transforms typically live? Scan every entry for any transform key.
    print("\n--- types that carry TransformKeys at least once ---")
    carriers = Counter()
    for e in data:
        props = e.get("Properties", {}) or {}
        if any(k in props for k in TRANSFORM_KEYS):
            carriers[e.get("Type")] += 1
    for t, n in carriers.most_common():
        print(f"  {n:4}  {t}")

    # Dump a full example SceneComponent / root component (where Actor transforms
    # actually live in UE — the RootComponent of the actor, typically a
    # StaticMeshComponent or SceneComponent marked 'Root').
    print("\n--- first entry with RelativeLocation (full) ---")
    for e in data:
        props = e.get("Properties", {}) or {}
        if "RelativeLocation" in props:
            print(json.dumps(e, indent=2))
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
