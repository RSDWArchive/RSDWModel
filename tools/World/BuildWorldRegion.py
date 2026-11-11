"""Opt-in local world reconstruction. Never invoked by the release pipeline."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from world_source import (Resolver, identity_hash, instance_matrix, intersects, property_bounds, transform_bounds,
                          read_json, ref_id, vector, write_json)

MODEL_ROOT = Path(__file__).resolve().parents[2]


def source_fingerprint(paths):
    h = hashlib.sha256()
    for path in sorted(set(paths)):
        h.update(str(path).encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                h.update(chunk)
    return h.hexdigest()


def revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def checkpoint(args, run, stage, status):
    run["stages"][stage] = status
    run["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(args.out_root / "PipelineRun.json", run)


def update_provenance(args, plan, resolver):
    paths = sorted(resolver.dependencies)
    tools = sorted(Path(__file__).parent.glob("*.py"))
    plan["provenance"].update(
        source_fingerprint=source_fingerprint(paths),
        source_files=[p.relative_to(resolver.root).as_posix() for p in paths],
        tool_fingerprint=source_fingerprint(tools),
        model_index_fingerprint=source_fingerprint([MODEL_ROOT / "website/model-index.json"]),
    )
    write_json(args.out_root / "RegionPlan.json", plan)


def region_sources(resolver, config, bounds):
    world = config["world_package"]
    packages = [world]
    cells = []
    for obj in resolver.package(world):
        if obj.get("Type") != "WorldPartitionRuntimeCellDataSpatialHash":
            continue
        props = obj["Properties"]
        if "HLOD" in props.get("GridName", ""):
            continue
        cell = resolver.get(obj["Outer"])["Name"]
        box = props.get("ContentBounds")
        if box and box.get("IsValid", 1):
            cell_bounds = [vector(box["Min"]).tolist(), vector(box["Max"]).tolist()]
            if not intersects(cell_bounds, bounds):
                continue
        path = world + "/_Generated_/" + cell
        if (resolver.root / (path + ".json")).exists():
            packages.append(path)
            cells.append({"cell": cell, "grid": props.get("GridName"), "bounds": box})
    return packages, cells


def classify(mesh, obj):
    lower = (mesh + " " + obj.get("Name", "")).lower()
    if obj["Type"] == "SplineMeshComponent" or any(s in lower for s in ("pavement", "paving", "pathdecal", "road_")):
        return "roads"
    if any(s in lower for s in ("tree", "foliage", "sapling", "shrub", "bush", "grass", "dandelion")):
        return "trees"
    if any(s in lower for s in ("architecture", "castle", "ruin", "fort", "building", "bridge", "temple", "wall_", "roof_")):
        return "buildings"
    return "scenery"


def inventory(resolver, packages, bounds, index):
    model_map = {row["path"].removesuffix(".uemodel"): row for row in index["models"]}
    placements, excluded, missing, assets = [], [], [], {}
    for package in packages:
        print("inventory", package.rsplit("/", 1)[-1], flush=True)
        for i, obj in enumerate(resolver.package(package)):
            typ = obj.get("Type", "")
            if "Component" not in typ:
                continue
            identity = f"{package}.{i}"
            props = resolver.effective(identity)
            mesh_ref = props.get("StaticMesh")
            if not isinstance(mesh_ref, dict) or not mesh_ref.get("ObjectName", "").startswith("StaticMesh'"):
                continue
            owner = resolver.get(obj["Outer"]) if obj.get("Outer") else {}
            reason = None
            if "HLOD" in typ or "HLOD" in owner.get("Type", ""):
                reason = "HLOD substitute"
            elif props.get("bHiddenInGame") or props.get("bVisible") is False or owner.get("Properties", {}).get("bHidden"):
                reason = "Hidden source component"
            elif props.get("bVisibleInSceneCaptureOnly"):
                reason = "Scene-capture-only component (not visible in the game view)"
            elif "Audio" in typ:
                reason = "Audio helper"
            elif "/VFX/" in str(mesh_ref.get("ObjectPath", "")):
                reason = "VFX shader geometry (outside static prototype)"
            elif any("Water_SimpleOpaque" in str(ref) for ref in props.get("OverrideMaterials", [])):
                reason = "Global ocean mask plane requires Unreal world-mask shader (outside static prototype)"
            elif props.get("bRenderInMainPass") is False and "Never" not in str(props.get("VirtualTextureRenderPassType")):
                reason = "Non-rendered helper"
            elif any(s in str(props.get("OverrideMaterials", [])).lower() for s in
                     ("/fog/", "proxylight", "grassrvterasing", "depthfoam", "witheringdecal")):
                reason = "Atmosphere, lighting, or vegetation-mask helper (outside static prototype)"
            if reason:
                excluded.append({"source": identity, "reason": reason})
                continue
            cached = property_bounds(props)
            if cached and not intersects(cached, bounds):
                continue
            socket = props.get("AttachSocketName")
            parent = ref_id(obj.get("Properties", {}).get("AttachParent"))
            if socket and socket != "None" and parent and "SkeletalMeshComponent" in resolver.get(parent).get("Type", ""):
                excluded.append({"source": identity, "reason": "Skeletal socket-attached prop requires animated actor reconstruction"})
                continue
            transform = resolver.transform(identity)
            instances = resolver.inherited_instances(identity)
            matrices = [transform @ instance_matrix(x) for x in instances] if instances is not None else [transform]
            if not matrices:
                continue
            mesh = resolver.canonical(mesh_ref)
            mesh_package = mesh.rsplit(".", 1)[0]
            model = model_map.get(mesh_package)
            layer = classify(mesh_package, obj)
            mesh_obj = resolver.get(mesh)
            box = mesh_obj.get("Properties", {}).get("ExtendedBounds") or mesh_obj.get("RenderData", {}).get("Bounds")
            if box and "BoxExtent" in box:
                c, e = vector(box.get("Origin")), vector(box["BoxExtent"])
                local_bounds = [c-e, c+e]
            else:
                raise ValueError(f"Mesh has no source bounds: {mesh}")
            kept = []
            for n, m in enumerate(matrices):
                b = transform_bounds(local_bounds, m)
                if typ == "SplineMeshComponent" and cached:
                    b = cached
                if intersects(b, bounds):
                    kept.append((n, m, b))
            if not kept:
                continue
            materials = [ref_id(x) for x in props.get("OverrideMaterials", [])]
            asset_id = identity_hash(mesh + json.dumps(materials) + (identity if typ == "SplineMeshComponent" else ""))
            if asset_id not in assets:
                assets[asset_id] = {"id": asset_id, "source": mesh, "model_path": model["gltfPath"] if model else None,
                                    "materials": materials, "layer": layer}
                if typ == "SplineMeshComponent":
                    assets[asset_id]["spline_source"] = identity
                    assets[asset_id]["spline_properties"] = props
                if not model:
                    missing.append({"source": mesh, "component": identity})
            for n, m, world_bounds in kept:
                placements.append({"id": identity + (f"#{n}" if instances is not None else ""),
                                   "asset": asset_id, "layer": layer, "matrix": m.tolist(),
                                   "bounds": world_bounds, "actor": owner.get("ActorLabel", owner.get("Name", ""))})
    return placements, list(assets.values()), excluded, missing


def make_plan(args, config):
    resolver = Resolver(args.archive_root / args.version / "json")
    bounds = [np.array(config["bounds_cm"]["min"], dtype=float), np.array(config["bounds_cm"]["max"], dtype=float)]
    bounds[0][:2] -= config["halo_cm"]
    bounds[1][:2] += config["halo_cm"]
    index = read_json(MODEL_ROOT / "website/model-index.json")
    if index["datasetVersion"] != args.version:
        raise ValueError("Model index must match the pinned dataset")
    packages, cells = region_sources(resolver, config, bounds)
    terrain = resolver.terrain(packages, bounds, config["exclude_landscape_material_patterns"])
    placements, assets, excluded, missing = inventory(resolver, packages, bounds, index)
    plan = {"schema": "RSDW.WorldRegionPlan.v1", "version": args.version, "region": config,
            "packages": packages, "cells": cells, "terrain": terrain, "assets": assets,
            "placements": placements, "excluded": excluded, "missing_models": missing,
            "resolver_issues": resolver.issues, "counts": dict(Counter(p["layer"] for p in placements))}
    source_paths = sorted(resolver.dependencies)
    plan["provenance"] = {"archive_revision": revision(args.archive_root), "model_revision": revision(MODEL_ROOT),
                          "cue_revision": revision(args.cue_root),
                          "source_fingerprint": source_fingerprint(source_paths),
                          "source_files": [str(p.relative_to(resolver.root)).replace("\\", "/") for p in source_paths]}
    write_json(args.out_root / "RegionPlan.json", plan)
    print(json.dumps({"packages": len(packages), "terrain": len(terrain), "assets": len(assets),
                      "placements": len(placements), "layers": plan["counts"], "missing_models": len(missing),
                      "resolver_issues": len(resolver.issues)}, indent=2), flush=True)
    if not terrain or any(not plan["counts"].get(k) for k in ("trees", "buildings", "roads")):
        raise ValueError("Region does not cover all four required layers")
    return plan, resolver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--region-config", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, default=MODEL_ROOT.parent / "RSDWArchive")
    parser.add_argument("--retoc-root", type=Path)
    parser.add_argument("--cue-root", type=Path, default=MODEL_ROOT.parent / "CUE4Parse")
    parser.add_argument("--mode", choices=("plan", "build", "validate"), default="plan")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.out_root = args.out_root.resolve()
    args.retoc_root = args.retoc_root or MODEL_ROOT.parent / "Retoc/RSDragonwilds" / args.version
    config = read_json(args.region_config)
    if config["version"] != args.version:
        parser.error("Region config and --version disagree")
    args.out_root.mkdir(parents=True, exist_ok=True)
    run = {"schema": "RSDW.WorldRegionRun.v1", "version": args.version, "mode": args.mode,
           "region": config["id"], "started_at": datetime.now(timezone.utc).isoformat(),
           "stages": {}, "status": "running"}
    write_json(args.out_root / "PipelineRun.json", run)
    started = time.perf_counter()
    try:
        if args.mode == "validate":
            from world_build import validate
            checkpoint(args, run, "validate", "running")
            validate(args)
            checkpoint(args, run, "validate", "success")
        else:
            checkpoint(args, run, "plan", "running")
            plan, resolver = make_plan(args, config)
            update_provenance(args, plan, resolver)
            checkpoint(args, run, "plan", "success")
            if args.mode == "build":
                from world_build import build
                build(args, plan, resolver, run)
        run["status"] = "success"
    except (Exception, KeyboardInterrupt) as exc:
        run["status"] = "failed"
        run["error"] = str(exc) or type(exc).__name__
        for stage, status in run["stages"].items():
            if status == "running":
                run["stages"][stage] = "failed"
        raise
    finally:
        run["elapsed_seconds"] = round(time.perf_counter()-started, 3)
        write_json(args.out_root / "PipelineRun.json", run)


if __name__ == "__main__":
    main()
