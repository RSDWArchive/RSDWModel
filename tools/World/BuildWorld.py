"""Build and audit the packaged static world, with independently loadable sections."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import time

import numpy as np

from BuildWorldRegion import (MODEL_ROOT, checkpoint, inventory, region_sources,
                              revision, source_fingerprint, update_provenance)
from world_source import Resolver, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, default=MODEL_ROOT.parent / "RSDWArchive")
    parser.add_argument("--retoc-root", type=Path)
    parser.add_argument("--cue-root", type=Path, default=MODEL_ROOT.parent / "CUE4Parse")
    parser.add_argument("--mode", choices=("inventory", "build", "validate"), default="build")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--section-size-cm", type=int, default=50400)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 20 or args.section_size_cm < 10000:
        parser.error("Use 1-20 workers and section size >= 10000cm")
    args.out_root = args.out_root.resolve()
    args.retoc_root = args.retoc_root or MODEL_ROOT.parent / "Retoc/RSDragonwilds" / args.version
    args.out_root.mkdir(parents=True, exist_ok=True)
    config = {"schema": "RSDW.WorldRegionConfig.v1", "id": "whole-world", "version": args.version,
              "world_package": "RSDragonwilds/Content/Maps/World/L_World", "whole_world": True,
              "bounds_cm": {"min": [-1e9]*3, "max": [1e9]*3}, "halo_cm": 0, "origin_cm": [0, 0, 0],
              "exclude_landscape_material_patterns": [], "workers": args.workers,
              "section_size_cm": args.section_size_cm, "terrain_texture_size": 1024}
    args.region_config = args.out_root / "WorldConfig.json"
    write_json(args.region_config, config)
    run = {"schema": "RSDW.WorldRegionRun.v1", "version": args.version, "mode": args.mode,
           "region": "whole-world", "started_at": datetime.now(timezone.utc).isoformat(),
           "stages": {}, "status": "running"}
    started = time.perf_counter()
    try:
        if args.mode == "validate":
            from world_build import validate
            checkpoint(args, run, "validate", "running")
            validate(args)
            checkpoint(args, run, "validate", "success")
            from world_sections import validate_sections
            validate_sections(args)
        else:
            checkpoint(args, run, "inventory", "running")
            resolver = Resolver(args.archive_root / args.version / "json")
            index = read_json(MODEL_ROOT / "website/model-index.json")
            if index["datasetVersion"] != args.version:
                raise ValueError("Model index must match the requested version")
            bounds = [np.array(config["bounds_cm"][key]) for key in ("min", "max")]
            cache_path = args.out_root / "RegionPlan.json"
            signature = source_fingerprint([Path(__file__).parent / p for p in ("BuildWorldRegion.py", "world_source.py")])
            signature += source_fingerprint([MODEL_ROOT / "website/model-index.json"])
            plan = read_json(cache_path) if args.resume and cache_path.is_file() else None
            if plan and (plan.get("inventory_signature") != signature or plan["region"] != config or
                         any(not (resolver.root/p).is_file() or [(resolver.root/p).stat().st_size, (resolver.root/p).stat().st_mtime_ns] != stamp
                             for p, stamp in plan.get("inventory_source_stamps", {}).items())):
                plan = None
            if plan:
                resolver.dependencies.update(resolver.root/p for p in plan["provenance"]["source_files"])
                print("Reusing verified world inventory", flush=True)
            else:
                packages, cells = region_sources(resolver, config, bounds)
                terrain = resolver.terrain(packages, bounds, [])
                print(f"World inventory: {len(packages)} packages, {len(terrain)} terrain components", flush=True)
                placements, assets, excluded, missing = inventory(resolver, packages, bounds, index)
                plan = {"schema": "RSDW.WorldRegionPlan.v1", "version": args.version, "region": config,
                        "packages": packages, "cells": cells, "terrain": terrain, "assets": assets,
                        "placements": placements, "excluded": excluded, "missing_models": missing,
                        "resolver_issues": resolver.issues, "counts": dict(Counter(p["layer"] for p in placements)),
                        "provenance": {"archive_revision": revision(args.archive_root), "model_revision": revision(MODEL_ROOT),
                                       "cue_revision": revision(args.cue_root)}}
                plan["inventory_signature"] = signature
                plan["inventory_source_stamps"] = {p.relative_to(resolver.root).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns]
                                                    for p in resolver.dependencies}
            update_provenance(args, plan, resolver)
            checkpoint(args, run, "inventory", "success")
            print(f"Inventoried {len(plan['placements']):,} placements / {len(plan['assets']):,} asset variants", flush=True)
            if args.mode == "build":
                if shutil.disk_usage(args.out_root).free < 12 * 1024**3:
                    raise RuntimeError("Less than 12 GiB free; use an output directory on a larger drive")
                from world_build import build
                build(args, plan, resolver, run)
                checkpoint(args, run, "sections", "running")
                from world_sections import build_sections, validate_sections
                build_sections(args)
                validate_sections(args)
                checkpoint(args, run, "sections", "success")
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
