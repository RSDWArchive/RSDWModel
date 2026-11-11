"""Native recovery, terrain generation and local prototype assembly."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
import subprocess

from world_source import identity_hash, read_json, ref_id, write_json


from world_terrain_cache import restore_terrain


def recover_batches(args, requests, native, project, mapping, fingerprints):
    batches = [requests[i:i+128] for i in range(0, len(requests), 128)]
    def work(rows):
        key = hashlib.sha256("".join(fingerprints[r.get("id", identity_hash(r["source"]))] for r in rows).encode()).hexdigest()[:20]
        folder = native / "jobs" / key
        result_path = folder / "NativeExport.json"
        if args.resume and result_path.is_file():
            saved = read_json(result_path)
            if len(saved) == len(rows) and all("error" not in r and all((folder/f).is_file()
                    for f in r.get("files", [r.get("file", "")]) + ([r["raw"]] if r.get("raw") else [])) for r in saved):
                result = saved
            else:
                result = None
        else:
            result = None
        if result is None:
            if shutil.disk_usage(native).free < 8*1024**3:
                raise RuntimeError("Native recovery stopped below 8 GiB free disk space; outputs are resumable")
            request_file = folder / "Requests.json"
            write_json(request_file, rows)
            with (folder / "Export.log").open("w", encoding="utf-8") as log:
                code = subprocess.run(["dotnet", str(project/"bin/Debug/net8.0/CueWorldExport.dll"), str(args.retoc_root),
                                       str(mapping), str(request_file), str(folder)], stdout=log, stderr=subprocess.STDOUT).returncode
            if code not in (0, 1):
                raise RuntimeError(f"Native batch {key} exited {code}; see its Export.log")
            result = read_json(result_path)
        prefix = folder.relative_to(native).as_posix()+"/"
        for row in result:
            for field in ("file", "raw"):
                if row.get(field):
                    row[field] = prefix+row[field]
            if "files" in row:
                row["files"] = [prefix+f for f in row["files"]]
        return result
    completed = []
    with ThreadPoolExecutor(max_workers=min(args.workers, 8)) as pool:
        futures = [pool.submit(work, batch) for batch in batches]
        for i, future in enumerate(as_completed(futures)):
            completed.extend(future.result())
            print(f"Native batches: {i+1}/{len(batches)} ({len(completed)} resources)", flush=True)
    return completed


def recover_sources(args, plan, resolver):
    requests = {}
    for tile in plan["terrain"]:
        tile["landscape_guid"] = resolver.effective(tile["owner"]).get("LandscapeGuid", tile["owner"])
        props = tile["properties"]
        for ref in [props["HeightmapTexture"], *props.get("WeightmapTextures", [])]:
            identity = ref_id(ref)
            requests[identity] = {"source": identity, "name": resolver.get(identity)["Name"], "kind": "texture"}
    first = plan["terrain"][0]
    requests["probe"] = {"source": first["source"], "owner": first["owner"], "kind": "landscape_probe"}
    from world_materials import scene_materials
    materials = scene_materials(plan, resolver)
    plan["material_parameters"] = materials
    if plan["region"].get("whole_world"):
        write_json(args.out_root / "MaterialSources.json", materials)
    for material in materials.values():
        for source in material["roles"].values():
            obj = resolver.get(source)
            if obj["Type"] in ("Texture2D", "Texture2DArray"):
                requests[source] = {"source": source, "name": obj["Name"],
                                    "kind": "texture_array" if obj["Type"] == "Texture2DArray" else "texture"}
    for asset in plan["assets"]:
        if "spline_source" in asset or not asset["model_path"]:
            source = asset.get("spline_source", asset["source"])
            requests[asset["id"]] = {"source": source, "name": resolver.get(source)["Name"],
                                     "kind": "spline" if "spline_source" in asset else "mesh", "id": asset["id"]}
    native = args.out_root / "source-data/native"
    request_path = args.out_root / "source-data/NativeRequests.json"
    ordered_requests = sorted(requests.values(), key=lambda r: (r["source"], r["kind"], r.get("id", "")))
    write_json(request_path, ordered_requests)
    project = Path(__file__).parent / "CueWorldExport"
    mapping = sorted(args.retoc_root.glob("*.usmap"))
    if len(mapping) != 1:
        raise ValueError("Expected exactly one matching usmap")
    from BuildWorldRegion import source_fingerprint
    tool_fingerprint = source_fingerprint([mapping[0], *project.glob("*.cs"), *project.glob("*.csproj")])
    # Input metadata detects replacement of the underlying cooked packages.
    package_sources = {r["source"].rsplit(".", 1)[0] for r in ordered_requests}
    metadata = []
    for p in sorted(package_sources):
        for ext in (".umap", ".uasset", ".uexp", ".ubulk"):
            path = args.retoc_root / (p + ext)
            if path.exists():
                stat = path.stat()
                metadata.append([p+ext, stat.st_size, stat.st_mtime_ns])
    fingerprints = {}
    metadata_by_package = {}
    for m in metadata:
        metadata_by_package.setdefault(m[0].rsplit(".", 1)[0], []).append(m)
    for r in ordered_requests:
        key = r.get("id", identity_hash(r["source"]))
        package = r["source"].rsplit(".", 1)[0]
        meta = metadata_by_package.get(package, [])
        fingerprints[key] = hashlib.sha256((tool_fingerprint + json.dumps(r, sort_keys=True) + json.dumps(meta) +
                                           plan["provenance"]["cue_revision"]).encode()).hexdigest()
    stamp = native / "Fingerprint.json"
    cache = {}
    if args.resume and stamp.exists() and (native / "NativeExport.json").exists():
        saved = read_json(stamp).get("entries", {})
        previous = read_json(native / "NativeExport.json")
        for row in previous:
            key = row["id"]
            files = row.get("files", [row.get("file", "")]) + ([row["raw"]] if row.get("raw") else [])
            if key in fingerprints and saved.get(key) == fingerprints[key] and "error" not in row and all((native/p).is_file() for p in files):
                cache[key] = row
    pending = [r for r in ordered_requests if r.get("id", identity_hash(r["source"])) not in cache]
    print(f"Native recovery: {len(cache)} cached, {len(pending)} pending", flush=True)
    if pending:
        pending_path = args.out_root / "source-data/NativePending.json"
        write_json(pending_path, pending)
        subprocess.run(["dotnet", "build", str(project), f"-p:Cue4ParseRoot={args.cue_root}", "--verbosity", "quiet"], check=True)
        if plan["region"].get("whole_world"):
            native.mkdir(parents=True, exist_ok=True)
            recovered = recover_batches(args, pending, native, project, mapping[0], fingerprints)
        else:
            result = subprocess.run(["dotnet", str(project/"bin/Debug/net8.0/CueWorldExport.dll"), str(args.retoc_root),
                                     str(mapping[0]), str(pending_path), str(native)])
            if result.returncode not in (0, 1):
                raise RuntimeError(f"Native extractor terminated with exit code {result.returncode}")
            recovered = read_json(native / "NativeExport.json")
        cache.update({r["id"]: r for r in recovered})
    missing = [r["source"] for r in ordered_requests if r.get("id", identity_hash(r["source"])) not in cache]
    if missing:
        raise RuntimeError(f"Native exporter returned no result for {len(missing)} sources")
    results = [cache[r.get("id", identity_hash(r["source"]))] for r in ordered_requests]
    write_json(native / "NativeExport.json", results)
    failures = [r for r in results if "error" in r and r["kind"] != "landscape_probe"]
    write_json(stamp, {"entries": fingerprints, "package_metadata": metadata, "tool_fingerprint": tool_fingerprint})
    if failures:
        raise RuntimeError(f"Native extraction failed for {len(failures)} sources; see NativeExport.json")
    return results


def build(args, plan, resolver, run):
    from BuildWorldRegion import checkpoint, update_provenance
    checkpoint(args, run, "recover_sources", "running")
    results = recover_sources(args, plan, resolver)
    update_provenance(args, plan, resolver)
    checkpoint(args, run, "recover_sources", "success")
    print("Recovered", len(results), "native resources", flush=True)
    from world_terrain import build_terrain
    checkpoint(args, run, "terrain", "running")
    cached_terrain = restore_terrain(args, plan)
    tiles, report = cached_terrain if cached_terrain else build_terrain(args, plan, results)
    write_json(args.out_root / "source-data/TerrainReport.json", report)
    write_json(args.out_root / "source-data/TerrainTiles.json", tiles)
    checkpoint(args, run, "terrain", "success")
    from world_scene import build_scene
    checkpoint(args, run, "scene", "running")
    build_scene(args, plan, resolver, tiles, results, report)
    checkpoint(args, run, "scene", "success")
    checkpoint(args, run, "validate", "running")
    validate(args)
    checkpoint(args, run, "validate", "success")


def validate(args):
    from world_scene import validate_scene
    return validate_scene(args)
