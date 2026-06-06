"""Repo-wide survey of L_World chunk JSONs.

Answers the plausibility questions for the "chunk -> Blender scene" idea:
  1. How many distinct StaticMesh refs appear across the entire world?
  2. What fraction of those meshes do we already have a .glb built for?
  3. How many placement instances total (SMC entries with a transform)?
  4. What other placeable actor classes appear (Blueprints / foliage / etc.)
     and how many carry direct transform data?
  5. What's the world AABB (so we know the physical map extent)?

Pure read-only; no writes anywhere in E:/Github/RSDWArchive.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ARCHIVE_ROOT = Path(r"E:/Github/RSDWArchive/0.11.0.10/json")
CHUNK_ROOT = ARCHIVE_ROOT / "RSDragonwilds/Content/Maps/World/L_World/_Generated_"
MODEL_REPO = Path(r"E:/Github/RSDWModel")
BUILD_PROGRESS = MODEL_REPO / "tools" / "ModelData" / "BuildProgress.json"


_PLUGIN_ROOT_REL = "RSDragonwilds/Plugins/GameFeatures"
_GAME_ROOT_REL = "RSDragonwilds/Content"


def _normalise_mesh_path(object_path: str) -> str:
    """Map a chunk StaticMesh.ObjectPath to a repo-relative stem that aligns
    with keys in BuildProgress.json.

    Handles three input shapes we see in the chunks:
        1. 'RSDragonwilds/Content/Art/.../SM_Foo.2'  (already repo-relative)
        2. '/Game/Art/.../SM_Foo'                    (UE Game mount)
        3. '/DowdunReach/Art/.../SM_Foo'             (UE plugin mount)
           -> 'RSDragonwilds/Plugins/GameFeatures/DowdunReach/Content/Art/.../SM_Foo'
        4. 'Engine/Content/BasicShapes/Cube'         (Engine content, never built)

    Always strips a trailing '.<int>' UE sub-object index.
    """
    if not object_path:
        return ""
    s = object_path.replace("\\", "/").strip()
    if "." in s:
        head, tail = s.rsplit(".", 1)
        if tail.isdigit():
            s = head
    # /Game/ mount -> RSDragonwilds/Content/
    if s.startswith("/Game/"):
        return f"{_GAME_ROOT_REL}/{s[len('/Game/'):]}"
    # /<Plugin>/Foo -> RSDragonwilds/Plugins/GameFeatures/<Plugin>/Content/Foo
    if s.startswith("/"):
        tail = s[1:]
        if "/" in tail:
            plugin, rest = tail.split("/", 1)
            return f"{_PLUGIN_ROOT_REL}/{plugin}/Content/{rest}"
        return tail
    return s


def _load_glb_keys() -> set[str]:
    """Load repo-relative uemodel paths that successfully built a .glb.

    Keys in BuildProgress.json look like:
        'RSDragonwilds/Content/Art/Env/.../SM_Foo.uemodel'
    We strip the extension to match against the chunk's StaticMesh ref form.
    """
    if not BUILD_PROGRESS.is_file():
        return set()
    doc = json.loads(BUILD_PROGRESS.read_text(encoding="utf-8"))
    built: set[str] = set()
    for key, ent in (doc.get("entries", {}) or {}).items():
        if ent.get("status") != "success":
            continue
        stem = key[:-len(".uemodel")] if key.endswith(".uemodel") else key
        built.add(stem)
    return built


def main() -> int:
    t0 = time.time()
    files = sorted(CHUNK_ROOT.glob("*.json"))
    if not files:
        print("ERR: no chunks")
        return 2

    built_stems = _load_glb_keys()
    print(f"chunks: {len(files)}  built .glb: {len(built_stems)}")

    # Tallies
    type_counts: Counter = Counter()
    placement_count = 0
    missing_mesh_ref = 0
    mesh_usage: Counter = Counter()  # stem -> instance count
    blueprint_usage: Counter = Counter()
    non_sm_transform_types: Counter = Counter()

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")

    transform_keys = ("RelativeLocation", "Location")

    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  WARN failed to read {fp.name}: {e}")
            continue
        if not isinstance(data, list):
            continue
        for e in data:
            t = e.get("Type") or ""
            type_counts[t] += 1
            props = e.get("Properties", {}) or {}

            has_loc = any(k in props for k in transform_keys)

            # Static mesh components with an inline StaticMesh ref + a location
            # are directly placeable.
            if t == "StaticMeshComponent":
                sm = props.get("StaticMesh") or {}
                if isinstance(sm, dict):
                    stem = _normalise_mesh_path(sm.get("ObjectPath", ""))
                else:
                    stem = ""
                if has_loc:
                    placement_count += 1
                    if stem:
                        mesh_usage[stem] += 1
                    else:
                        missing_mesh_ref += 1
                    loc = props.get("RelativeLocation") or props.get("Location") or {}
                    x, y, z = loc.get("X", 0.0), loc.get("Y", 0.0), loc.get("Z", 0.0)
                    if x < min_x: min_x = x
                    if x > max_x: max_x = x
                    if y < min_y: min_y = y
                    if y > max_y: max_y = y
                    if z < min_z: min_z = z
                    if z > max_z: max_z = z

            # Track blueprint class usage for "what else is placed"
            cls = e.get("Class", "") or ""
            if cls.startswith("BlueprintGeneratedClass'") and has_loc:
                blueprint_usage[cls] += 1

            if has_loc and t not in ("StaticMeshComponent",):
                non_sm_transform_types[t] += 1

    print(f"\nelapsed: {time.time()-t0:.1f}s")
    print(f"\n=== placement summary ===")
    print(f"  SMC placements (have StaticMesh + RelativeLocation): {placement_count}")
    print(f"  SMC placements missing StaticMesh ref (likely BP-templated): {missing_mesh_ref}")
    print(f"  distinct meshes referenced:  {len(mesh_usage)}")
    built_matches = [s for s in mesh_usage if s in built_stems]
    missing_from_build = [s for s in mesh_usage if s not in built_stems]
    print(f"  .glb available for: {len(built_matches)} / {len(mesh_usage)} "
          f"({100.0*len(built_matches)/max(1,len(mesh_usage)):.1f}%)")
    print(f"  .glb missing for:   {len(missing_from_build)}")
    instances_covered = sum(mesh_usage[s] for s in built_matches)
    instances_total = sum(mesh_usage.values())
    print(f"  instance coverage: {instances_covered}/{instances_total} "
          f"({100.0*instances_covered/max(1,instances_total):.2f}%)")

    print(f"\n=== world AABB (unreal cm) ===")
    print(f"  X: {min_x:,.1f} .. {max_x:,.1f}   span {max_x-min_x:,.1f} cm  ({(max_x-min_x)/100/1000:.1f} km)")
    print(f"  Y: {min_y:,.1f} .. {max_y:,.1f}   span {max_y-min_y:,.1f} cm  ({(max_y-min_y)/100/1000:.1f} km)")
    print(f"  Z: {min_z:,.1f} .. {max_z:,.1f}   span {max_z-min_z:,.1f} cm")

    print(f"\n=== TOP 20 most-used meshes ===")
    for stem, n in mesh_usage.most_common(20):
        marker = "OK " if stem in built_stems else "MISS"
        print(f"  {marker}  {n:6}  {stem}")

    print(f"\n=== TOP 15 placed blueprints ===")
    for cls, n in blueprint_usage.most_common(15):
        print(f"  {n:5}  {cls}")

    print(f"\n=== other types that carry transforms (non-SMC, top 20) ===")
    for t, n in non_sm_transform_types.most_common(20):
        print(f"  {n:5}  {t}")

    print(f"\n=== object-type totals across all chunks (top 25) ===")
    for t, n in type_counts.most_common(25):
        print(f"  {n:7}  {t}")

    # Sample 10 MISSING mesh refs so we can categorise why they're absent
    if missing_from_build:
        print(f"\n=== sample of 10 missing mesh stems ===")
        for s in missing_from_build[:10]:
            print(f"  x{mesh_usage[s]:<5}  {s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
