"""Assemble a browser manifest and bake RVT-only roads onto terrain."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from PIL import Image

from world_geometry import read_glb_mesh
from world_source import identity_hash, read_json, write_json
from world_materials import render_settings
from world_texture import linear_to_rgb8, srgb_to_linear

ATMOSPHERE_MASTERS = {"M_DistantFogVolume", "M_LocalFogV2", "M_LocalFogV2_debug", "M_Aurora", "M_Aurora_FH",
                      "M_Localfog_DepthBufferMist", "M_FightCaveFogPlane"}


def atmosphere_only(asset, materials):
    refs = []
    for slot in asset["section_material_slots"] or range(len(asset["default_materials"])):
        override = asset["materials"][slot] if slot < len(asset["materials"]) else ""
        refs.append(override or (asset["default_materials"][slot] if slot < len(asset["default_materials"]) else ""))
    return bool(refs) and all(str(materials.get(ref, {}).get("master", "")).rsplit("/", 1)[-1].split(".")[0]
                              in ATMOSPHERE_MASTERS for ref in refs)


def blockout_only(asset, materials):
    source = asset['source']
    refs = []
    for slot in asset['section_material_slots'] or range(len(asset['default_materials'])):
        override = asset['materials'][slot] if slot < len(asset['materials']) else ''
        refs.append(override or (asset['default_materials'][slot] if slot < len(asset['default_materials']) else ''))
    # The shipped game reuses SM_BO_Water_Plane for real lakes and oasis water.
    if any(materials.get(ref, {}).get('settings', {}).get('family') == 'water' for ref in refs):
        return False
    if '/LevelDesign/' in source and any(folder in source for folder in ('/BlockoutMeshes/', '/NewBlockoutMeshes/')):
        return True
    if '/Developers/' in source and '/Prototype/' in source:
        return True
    if not asset['source'].startswith('Engine/Content/BasicShapes/'):
        return False
    masters = {'BasicShapeMaterial','M_Blackout','M_Debug_Grids','M_Tool_ProcGrid','M_LevelDesignMarker'}
    return bool(refs) and all(materials.get(ref,{}).get('master','').rsplit('/',1)[-1].split('.')[0] in masters
                             or ref.rsplit('/',1)[-1].split('.')[0]=='MI_Standard_Black' for ref in refs)


def landscape_color_projection(args, tiles, materials, size=4096):
    surface = [t for t in tiles if 'world_landscape' in t['material'].lower()]
    targets = [m for m in materials.values() if m['texture_gaps'] and
               (m.get('master','').endswith('/M_UMS_Dunes.0') or 'world_landscape' in m.get('master','').lower())]
    if not surface or not targets:
        return
    bounds = [np.min([t['bounds'][0][:2] for t in surface],axis=0),np.max([t['bounds'][1][:2] for t in surface],axis=0)]
    span = bounds[1]-bounds[0]
    atlas = Image.new('RGBA',(size,size),(0,0,0,0))
    for tile in surface:
        lo = np.rint((np.array(tile['bounds'][0][:2])-bounds[0])/span*size).astype(int)
        hi = np.rint((np.array(tile['bounds'][1][:2])-bounds[0])/span*size).astype(int)
        if np.any(hi<=lo):
            continue
        with Image.open(args.out_root/tile['road_texture']) as image:
            atlas.paste(image.convert('RGBA').resize(tuple(hi-lo),Image.Resampling.LANCZOS),tuple(lo))
    path = 'assets/materials/landscape-color.webp'
    atlas.save(args.out_root/path,quality=90)
    for material in targets:
        material['projection'] = 'landscape_color'
        material['landscape_bounds_cm'] = [b.tolist() for b in bounds]
        material['textures']['landscape_color'] = path
        material['approximation'] = 'World-projected baked landscape color replaces Unreal RVT/array sampling; procedural normals, grain and shader blending remain unimplemented'


def material_descriptors(args, plan, results):
    native = args.out_root / "source-data/native"
    exported = {r["source"]: r for r in results if "error" not in r}
    folder = args.out_root / "assets/materials"
    folder.mkdir(parents=True, exist_ok=True)
    jobs, materials = {}, {}
    for source, p in plan["material_parameters"].items():
        # Most original model materials are reused directly by the browser.
        desc = {"source": source, "name": source.rsplit("/", 1)[-1].split(".")[0],
                "textures": {}, "scalars": p["scalars"], "vectors": p["vectors"], "properties": p["properties"],
                "settings": render_settings(p), "master": p.get("master"), "texture_gaps": [],
                "linear_color_sources": {}}
        if "Rock BC (A=roughness)" in p["textures"] or "Rock Base Colour" in p["textures"]:
            desc["projection"] = "triplanar"
            desc["tile_meters"] = max(1, min(50, p["scalars"].get("Rock Scale Mult", 8)))
        for role, ref in p["roles"].items():
            row = exported.get(ref)
            if not row or row["kind"] != "texture":
                desc["texture_gaps"].append({"role": role, "source": ref,
                                             "reason": "Texture array needs a shader adapter" if row and row["kind"] == "texture_array" else "Texture was not recovered"})
                continue
            encode_srgb = (role in ('base_color','emissive') or role.startswith('cave_layer_')) and not p.get('texture_srgb', {}).get(ref,True)
            key = identity_hash(ref+'|linear-to-srgb' if encode_srgb else ref)
            if encode_srgb:
                desc['linear_color_sources'][role] = ref
            previous = jobs.get(key)
            lossless = role in ('normal', 'water_normal_2', 'foam_mask') or bool(previous and previous[2])
            jobs[key] = (native/row["file"], folder/(key+".webp"), lossless, encode_srgb)
            desc["textures"][role] = "assets/materials/"+key+".webp"
        for role in desc["settings"].get("required_texture_roles", []):
            if role not in p["roles"]:
                desc["texture_gaps"].append({"role": role, "source": source,
                                             "reason": "Required shader texture reference is missing or ambiguous"})
        materials[source] = desc
    def convert(job):
        src, dst, lossless, encode_srgb = job
        with Image.open(src) as source:
            image = source.copy()
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        if encode_srgb:
            pixels = np.array(image.convert('RGBA'))
            pixels[:, :, :3] = linear_to_rgb8(pixels[:, :, :3].astype(np.float32)/255)
            image = Image.fromarray(pixels)
        image.save(dst, quality=50 if lossless else 90, method=1 if lossless else 4, lossless=lossless)
    with ThreadPoolExecutor(max_workers=min(8, plan["region"]["workers"])) as pool:
        for count, _ in enumerate(pool.map(convert, jobs.values()), 1):
            if count % 100 == 0 or count == len(jobs):
                print(f"Material textures: {count}/{len(jobs)}", flush=True)
    return materials


def bake_roads(args, plan, tiles, native_by_id):
    assets = {a["id"]: a for a in plan["assets"]}
    road_placements = [p for p in plan["placements"] if "Never" in str(assets[p["asset"]].get("spline_properties", {}).get("VirtualTextureRenderPassType", ""))]
    canvases = []
    for tile in tiles:
        path = args.out_root / tile["path"]
        texture = path.with_suffix(".png")
        image = Image.open(texture).convert("RGB")
        image.save(path.with_name(path.stem+"-base.png"))
        if tile.get('overview_path'):
            overview = (args.out_root/tile['overview_path']).with_suffix('.webp')
            base_overview = overview.with_name(overview.stem+'-base.webp')
            image.resize((256,256),Image.Resampling.LANCZOS).save(base_overview,quality=80)
            tile['base_overview_texture'] = base_overview.relative_to(args.out_root).as_posix()
            tile['road_overview_texture'] = overview.relative_to(args.out_root).as_posix()
        tile["base_texture"] = str(path.with_name(path.stem+"-base.png").relative_to(args.out_root)).replace("\\", "/")
        tile["road_texture"] = str(texture.relative_to(args.out_root)).replace("\\", "/")
        canvases.append((tile, srgb_to_linear(np.asarray(image, dtype=np.float32)/255), np.zeros((image.height,image.width), dtype=bool)))
    tile_mins = np.asarray([tile['bounds'][0] for tile in tiles])
    tile_maxs = np.asarray([tile['bounds'][1] for tile in tiles])
    covered = []
    for road_index, placement in enumerate(road_placements,1):
        asset = assets[placement["asset"]]
        native = native_by_id[asset["id"]]
        parts = read_glb_mesh(args.out_root/"source-data/native"/native["file"])
        total = 0
        color_params = plan["material_parameters"].get(next(iter(asset["materials"]), ""), {}).get("vectors", {})
        tint = color_params.get("Color Var B", {"R": 0.35, "G": 0.31, "B": 0.24})
        linear = np.array([tint.get(k, 0.35) for k in "RGB"])
        for part in parts:
            # CUE's glTF contains local meters with Y/Z swapped.
            ue = part["positions"][:, [0,2,1]]*100
            world = (np.array(placement["matrix"]) @ np.column_stack((ue,np.ones(len(ue)))).T).T[:,:3]
            road_min, road_max = world.min(axis=0), world.max(axis=0)
            candidates = np.all(road_max[:2]>=tile_mins[:,:2],axis=1)&np.all(road_min[:2]<=tile_maxs[:,:2],axis=1)
            if plan['region'].get('whole_world'):
                candidates &= (road_max[2]+200>=tile_mins[:,2])&(road_min[2]-200<=tile_maxs[:,2])
            for tile_index in np.flatnonzero(candidates):
                tile, pixels, changed = canvases[tile_index]
                mn, mx = np.array(tile["bounds"])
                size = pixels.shape[0]
                projected = (world[:,:2]-mn[:2])/(mx[:2]-mn[:2])*(size-1)
                for triangle in part["indices"].reshape(-1,3):
                    xy = projected[triangle]
                    lo = np.maximum(np.floor(xy.min(axis=0)).astype(int),0)
                    hi = np.minimum(np.ceil(xy.max(axis=0)).astype(int),size-1)
                    if np.any(lo>hi):
                        continue
                    a,b,c = xy
                    determinant = (b-a)[0]*(c-a)[1]-(b-a)[1]*(c-a)[0]
                    if abs(determinant)<1e-6:
                        continue
                    xx,yy = np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1))
                    q = np.stack((xx-a[0], yy-a[1]),axis=-1)
                    u = (q[:,:,0]*(c-a)[1]-q[:,:,1]*(c-a)[0])/determinant
                    v = ((b-a)[0]*q[:,:,1]-(b-a)[1]*q[:,:,0])/determinant
                    mask = (u>=0)&(v>=0)&(u+v<=1)
                    if not mask.any():
                        continue
                    alpha = np.ones_like(u)*0.88
                    if part["uv"] is not None:
                        uv = part["uv"][triangle]
                        coords = uv[0]+u[:,:,None]*(uv[1]-uv[0])+v[:,:,None]*(uv[2]-uv[0])
                        alpha *= np.clip(np.minimum(coords[:,:,1],1-coords[:,:,1])*10,0,1)
                    view = pixels[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
                    blend = alpha[mask,None]
                    view[mask] = view[mask]*(1-blend)+linear*blend
                    changed[lo[1]:hi[1]+1,lo[0]:hi[0]+1] |= mask
                    total += int(mask.sum())
        covered.append({"source":placement["id"], "baked_pixels": total})
        if road_index%250 == 0 or road_index==len(road_placements):
            print(f"Road projection: {road_index}/{len(road_placements)}",flush=True)
    def save_canvas(canvas):
        tile, pixels, _ = canvas
        encoded = Image.fromarray(linear_to_rgb8(pixels))
        encoded.save(args.out_root/tile["road_texture"])
        if tile.get("overview_path"):
            encoded.resize((256,256),Image.Resampling.LANCZOS).save(
                (args.out_root/tile["overview_path"]).with_suffix(".webp"),quality=80)
    with ThreadPoolExecutor(max_workers=min(8, plan["region"]["workers"])) as pool:
        for count, _ in enumerate(pool.map(save_canvas, canvases), 1):
            if count % 100 == 0 or count == len(canvases):
                print(f"Terrain road textures: {count}/{len(canvases)}", flush=True)
    return covered, {p["id"] for p in road_placements}


def build_scene(args, plan, resolver, tiles, results, terrain_report):
    from world_terrain_cache import cache_terrain
    cache_terrain(args, plan, tiles)
    native = {r["id"]: r for r in results if "error" not in r}
    materials = material_descriptors(args, plan, results)
    roads, baked_ids = bake_roads(args, plan, tiles, native)
    landscape_color_projection(args,tiles,materials)
    assets = []
    for a in plan["assets"]:
        row = {k:a[k] for k in ("id","source","layer","materials","default_materials","section_material_slots")}
        if a["id"] in native:
            row.update(path="source-data/native/"+native[a["id"]]["file"], root="region", native=True)
        else:
            row.update(path=a["model_path"], root="models", native=False)
        row["spline"] = "spline_source" in a
        if blockout_only(a,materials):
            row['layer'] = 'helpers'
        assets.append(row)
    atmosphere_assets = {a["id"] for a in assets if atmosphere_only(a, materials)}
    atmosphere_excluded = [{"source": p["id"], "reason": "Source fog/aurora volume shader: atmosphere outside static reconstruction"}
                           for p in plan["placements"] if p["asset"] in atmosphere_assets]
    asset_layers = {a['id']:a['layer'] for a in assets}
    visible = [dict(p,layer=asset_layers[p['asset']]) for p in plan["placements"] if p["asset"] not in atmosphere_assets]
    scene = {"schema":"RSDW.WorldRegion.v1", "version":args.version, "region":plan["region"],
             "coordinates":"UE centimeters; column-vector affine matrices; glTF uses meters with Y/Z swapped",
             "terrain":tiles, "assets":assets, "materials":materials,
             "placements":[dict(p, baked_road=p["id"] in baked_ids) for p in visible],
             "provenance":plan["provenance"]}
    write_json(args.out_root/"WorldRegion.json", scene)
    from world_material_audit import write_material_audit
    material_audit = write_material_audit(args, scene, plan["material_parameters"])
    unavailable = [{"material":key,"sources":p["unavailable_defaults"]} for key,p in plan["material_parameters"].items() if p.get("unavailable_defaults")]
    report = {"schema":"RSDW.WorldRegionReport.v1", "version":args.version,
              "status":"built_pending_visual_comparison", "counts":dict(Counter(p["layer"] for p in visible)),
              "terrain":terrain_report, "roads":roads, "converted_missing_meshes":plan["missing_models"],
              "excluded":plan["excluded"]+atmosphere_excluded, "resolver_issues":resolver.issues,
              "material_source_gaps":unavailable,
              "material_audit": {"file": "MaterialAudit.json", **material_audit},
              "material_rendering": {
                  "source_controlled": [{"material": key, **p["settings"]} for key,p in materials.items() if p["settings"]],
                  "unsupported_texture_roles": [{"material": key, **gap} for key,p in materials.items()
                                                for gap in p["texture_gaps"] if key not in {t["material"] for t in plan["terrain"]}]},
              "approximations":[terrain_report["material_approximation"],
                "RVT road color/soft edges projected onto terrain from CUE-deformed spline geometry. Procedural erosion/noise omitted.",
                "Source static switches control emission and alpha cutouts. Foliage vertex colors are shader masks, not diffuse RGB: red AO is applied to indirect lighting when enabled; wind/blend channels are ignored. Enabled emission remains a static texture/color approximation without proximity or glint animation.",
                "Static source material textures and overrides, with world-space triplanar cliff base color. Browser water includes source-distance depth approximations; static foam masks are supported, not the complete particle shader. Refraction, animation, Unreal wind, moss blending, wetness and impostor shaders remain unreconstructed. Global ocean mask planes are excluded."],
              "in_game_comparison":"pending user reference viewpoints", "source_packages":len(plan["packages"])}
    write_json(args.out_root/"WorldRegionReport.json", report)


def validate_scene(args):
    scene = read_json(args.out_root/"WorldRegion.json")
    report = read_json(args.out_root/"WorldRegionReport.json")
    model_root = Path(__file__).resolve().parents[2]/args.version/"WebAssets"
    failures, files = [], set()
    config = read_json(args.region_config)
    if scene["version"] != args.version or scene["region"] != config or report["version"] != args.version:
        failures.append("Built scene does not match the requested version/region configuration")
    if not scene["terrain"] or any(not report["counts"].get(k) for k in ("trees", "buildings", "roads")):
        failures.append("A required region layer is empty")
    ids = [p["id"] for p in scene["placements"]]
    if len(ids)!=len(set(ids)):
        failures.append("Duplicate placement identities")
    assets = {a["id"] for a in scene["assets"]}
    for p in scene["placements"]:
        m = np.array(p["matrix"])
        if m.shape!=(4,4) or not np.isfinite(m).all() or p["asset"] not in assets:
            failures.append("Invalid placement: "+p["id"])
    for a in scene["assets"]:
        path = (model_root if a["root"]=="models" else args.out_root)/a["path"]
        files.add(path)
        if not path.is_file():
            failures.append("Missing asset: "+str(path))
            continue
        if path.suffix==".gltf":
            doc=read_json(path)
            for row in doc.get("buffers",[])+doc.get("images",[]):
                if "uri" in row and not row["uri"].startswith("data:"):
                    files.add((path.parent/unquote(row["uri"])).resolve())
    for tile in scene["terrain"]:
        path=args.out_root/tile["path"]
        files.add(path)
        doc=read_json(path)
        for row in doc["buffers"]+doc["images"]:
            files.add(path.parent/row["uri"])
        files.update(args.out_root/tile[key] for key in ("base_texture", "road_texture"))
    for material in scene["materials"].values():
        files.update(args.out_root/p for p in material["textures"].values())
    for path in files:
        if not path.exists():
            failures.append("Missing dependency: "+str(path))
    if report["resolver_issues"]:
        failures.append("Unresolved component hierarchy")
    if report["terrain"]["max_border_delta_cm"] > 1:
        failures.append("Terrain boundary height mismatch")
    if any(r["baked_pixels"]==0 for r in report["roads"]):
        failures.append("RVT road did not reach any selected terrain")
    payload = sum(p.stat().st_size for p in files if p.is_file())
    content_hash = hashlib.sha256((args.out_root/"WorldRegion.json").read_bytes()).hexdigest()
    audit_path = args.out_root / "MaterialAudit.json"
    if not audit_path.is_file() or read_json(audit_path).get("scene_sha256") != content_hash:
        failures.append("Material audit is missing or belongs to a different scene; rebuild the region")
    elif read_json(audit_path)['summary']['by_status'].get('conversion_gap', 0):
        failures.append('Material audit contains required conversion gaps')
    visual_path = args.out_root/'VisualAuditIndex.json'
    if not visual_path.is_file():
        failures.append('Visual audit index is missing; rebuild the region')
    else:
        visual = read_json(visual_path)
        expected = {p['asset'] for p in scene['placements'] if not p.get('baked_road')}
        if visual.get('scene_sha256') != content_hash or {v['asset'] for v in visual['variants']} != expected:
            failures.append('Visual audit index does not cover the current scene variants')
    report["validation"]={"status":"failed" if failures else "passed", "failures":failures,
                          "dependency_files":len(files), "payload_bytes":payload, "scene_sha256":content_hash}
    write_json(args.out_root/"WorldRegionReport.json",report)
    if failures:
        raise ValueError("; ".join(failures[:10]))
    print(f"Validated {len(ids)} placements, {len(files)} files, {payload/1024**2:.1f} MiB",flush=True)
    return report
