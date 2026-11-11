"""Decode original packed landscape data, including duplicated subsection edges."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from world_geometry import normals_for, read_glb_mesh, write_mesh
from world_source import identity_hash, ref_id
from world_texture import linear_to_rgb8, sample_bilinear, sample_world_color


def vertex_texels(quads, subsection_quads, subsections):
    x = np.arange(quads + 1)
    return x + np.minimum(x // subsection_quads, subsections - 1)


def raw_rgba(native_root, row):
    if row.get("raw"):
        data = np.fromfile(native_root/row["raw"], dtype=np.uint8)
        if len(data) != row["width"]*row["height"]*4:
            raise ValueError(f"Unexpected data texture size: {row['source']}")
        return data.reshape(row["height"], row["width"], 4)[:, :, [2, 1, 0, 3]]
    return np.asarray(Image.open(native_root/row["file"]).convert("RGBA"))


def decode_tile(tile, native_root, textures):
    p = tile["properties"]
    quads = p["ComponentSizeQuads"]
    texels = vertex_texels(quads, p["SubsectionSizeQuads"], p["NumSubsections"])
    h = textures[ref_id(p["HeightmapTexture"])]
    if h["format"] != "PF_B8G8R8A8" or not h.get("raw"):
        raise ValueError("Heightmap must retain its original packed BGRA8 bytes")
    pixels = raw_rgba(native_root, h)
    bias = p["HeightmapScaleBias"]
    xx = texels + round(h["width"]*bias.get("Z", 0))
    yy = texels + round(h["height"]*bias.get("W", 0))
    sample = pixels[yy[:, None], xx[None, :]]
    height16 = sample[:, :, 0].astype(np.uint16)*256 + sample[:, :, 1]
    heights = (height16.astype(float)-32768)/128
    layers = {}
    for allocation in p.get("WeightmapLayerAllocations", []):
        ref = p["WeightmapTextures"][allocation["WeightmapTextureIndex"]]
        weights = raw_rgba(native_root, textures[ref_id(ref)])
        # Weightmap texels are local to the component; subsection seam duplication matches heightmaps.
        weights = weights[texels[:, None], texels[None, :], allocation["WeightmapTextureChannel"]]
        name = ref_id(allocation["LayerInfo"]).rsplit("/", 1)[-1].split(".")[0]
        layers[name] = weights
    return heights, height16, layers


def resize_mask(array, size):
    # These samples live on terrain vertices, not on image pixel centers.
    x, y = np.meshgrid(np.linspace(0, array.shape[1] - 1, size), np.linspace(0, array.shape[0] - 1, size))
    return sample_bilinear(array.astype(np.float32), x, y) / 255


def painted_weights(masks, names):
    zero = np.zeros_like(next(iter(masks.values())))
    weights = [masks.get(name, zero) for name in names]
    total = sum(weights)
    fallback = total < 1e-5
    weights[0] = weights[0] + fallback
    total = np.maximum(sum(weights), 1e-5)
    return [weight / total for weight in weights], float(np.mean(fallback))


def feature_selections(parameters, biome, masks, level, slices, audit):
    ranges = parameters['vectors'].get(f'Biome{biome}_Feature')
    if not ranges:
        return [], set()
    selections, used = [], set()
    for name, channels in (('Param_FeatureA_LayerInfo', 'RG'), ('Param_FeatureB_LayerInfo', 'BA')):
        weight = masks.get(name)
        if weight is None or not np.any(weight):
            continue
        lo, hi = (ranges.get(channel, -1) for channel in channels)
        if min(lo, hi) < 0 or max(lo, hi) >= slices:
            if audit is not None:
                audit.setdefault('unresolved_feature_ranges', []).append({'biome':biome,'layer':name,'range':[lo,hi]})
            continue
        selections.append((lo+(hi-lo)*level, weight))
        used.add(name)
        if audit is not None:
            audit.setdefault('feature_blend', []).append({'biome':biome,'layer':name,'range':[lo,hi],
                'status':'approximation: source paint and explicit range; normalized overlay, no shader height/noise equations'})
    return selections, used


def compare_native_probe(tile, probe, native, textures):
    if "error" in probe:
        return {"status": "unavailable", "error": probe["error"]}
    positions = np.concatenate([p["positions"] for p in read_glb_mesh(native/probe["file"])])
    heights, _, _ = decode_tile(tile, native, textures)
    # The native probe exports unscaled landscape-local positions in glTF meters.
    x = np.rint((positions[:, 0]-positions[:, 0].min())*100).astype(int)
    y = np.rint((positions[:, 2]-positions[:, 2].min())*100).astype(int)
    if x.max() >= heights.shape[1] or y.max() >= heights.shape[0]:
        raise ValueError("Native landscape probe grid does not match the selected component")
    local_error = np.abs(positions[:, 1]*100-heights[y, x])
    world_error = float(local_error.max()*np.linalg.norm(np.asarray(tile["matrix"])[:3, 2]))
    if world_error > 1:
        raise ValueError(f"Native landscape height comparison exceeds 1cm: {world_error}")
    return {"status": "passed", "vertices": len(positions), "max_height_delta_cm": world_error}


def bake_color(tile, heights, layers, plan, native_root, textures, size=1024, audit=None):
    parameters = plan["material_parameters"][tile["material"]]
    source = parameters["roles"].get("base_color")
    m = np.array(tile["matrix"])
    q = tile["properties"]["ComponentSizeQuads"]
    xs, ys = np.meshgrid(np.linspace(0, q, size), np.linspace(0, q, size))
    world_x = m[0, 3] + m[0, 0]*xs + m[0, 1]*ys
    world_y = m[1, 3] + m[1, 0]*xs + m[1, 1]*ys
    masks = {name: resize_mask(value, size) for name, value in layers.items()}
    masks.setdefault("Param_Level_LayerInfo", np.zeros((size, size), dtype=np.float32))
    footprint = max(np.linalg.norm(m[:2, 0]), np.linalg.norm(m[:2, 1])) * q / max(size - 1, 1)
    used_layers = set()
    if audit is not None:
        audit.update(color_space="linear blending, sRGB output", texture_filter="footprint-filtered bilinear",
                     tiling_cm=400, tiling_status="approximation: cooked material UV graph unavailable",
                     linear_source_textures=[ref for ref, srgb in parameters.get('texture_srgb', {}).items() if not srgb])
    def sample(ref):
        return sample_world_color(native_root/textures[ref]["file"], world_x, world_y, footprint_cm=footprint,
                                  srgb=parameters.get('texture_srgb', {}).get(ref, True))
    def finish(out):
        if audit is not None:
            ignored = {"LandscapeVisibilityLayerInfo", "LL_No_Grass", "No_Foliage_LayerInfo"}
            audit["active_unmodeled_layers"] = [name for name, weight in layers.items()
                if name not in used_layers | ignored and np.any(weight)]
        return linear_to_rgb8(out)
    if "cave_layer_1" in parameters["roles"]:
        layer_names = ("Rock_LayerInfo", "Dirt01_LayerInfo", "Dirt_02_LayerInfo")
        used_layers.update(layer_names)
        weights, fallback = painted_weights(masks, layer_names)
        if audit is not None:
            audit["unpainted_fallback_fraction"] = fallback
        out = np.zeros((size,size,3), dtype=np.float32)
        for layer, weight in enumerate(weights, 1):
            ref = parameters["roles"][f"cave_layer_{layer}"]
            tint = parameters["vectors"].get(f"L{layer}_Color_Muliplayer", {})
            color = np.array([tint.get(c, 1) for c in "RGB"])*parameters["scalars"].get(f"L{layer}_Brightness", 1)
            out += sample(ref)*color*weight[:,:,None]
        return finish(out)
    if not source or source not in textures:
        raise ValueError(f"No terrain base-color source: {tile['material']}")
    if "files" not in textures[source]:
        return finish(sample(source))
    files = textures[source]["files"]
    level = masks["Param_Level_LayerInfo"]
    sub_names = tuple(f"SubBiome_{name}_LayerInfo" for name in "ABC")
    biome_names = ("Biome_A_LayerInfo", "Biome_B_LayerInfo")
    (sub_a, sub_b, sub_c), sub_fallback = painted_weights(masks, sub_names)
    (biome_a, biome_b), biome_fallback = painted_weights(masks, biome_names)
    used_layers.update((*sub_names, *biome_names, "Param_Level_LayerInfo"))
    if audit is not None:
        audit.update(subbiome_unpainted_fallback_fraction=sub_fallback,
                     biome_unpainted_fallback_fraction=biome_fallback,
                     missing_subbiome_a="SubBiome_A_LayerInfo" not in layers,
                     missing_level="Param_Level_LayerInfo" not in layers,
                     array_slices=len(files), range_assumptions=[], clamped_ranges=[])
    # Cooked shader bytecode isn't portable. Reconstruct the documented biome ranges
    # from the original masks and texture array; report omitted noise/slope effects.
    selections = []
    for biome, biome_weight in (("A", biome_a), ("B", biome_b)):
        if not np.any(biome_weight):
            continue
        if audit is not None and f"Biome{biome}_Subbiome_Ranges01" not in parameters["vectors"]:
            audit["range_assumptions"].append(biome)
        ranges = parameters["vectors"].get(f"Biome{biome}_Subbiome_Ranges01", {"R":1,"G":4,"B":5,"A":7})
        ranges2 = parameters["vectors"].get(f"Biome{biome}_Subbiome_Ranges02", {"R":10,"G":12})
        features, feature_layers = feature_selections(parameters, biome, masks, level, len(files), audit)
        used_layers.update(feature_layers)
        coverage = np.clip(sum(weight for _,weight in features), 0, 1)
        for lo, hi, weight in ((ranges["R"],ranges["G"],sub_a), (ranges["B"],ranges["A"],sub_b), (ranges2["R"],ranges2["G"],sub_c)):
            if lo >= 0 and hi >= 0:
                selections.append((lo+(hi-lo)*level, weight*biome_weight*(1-coverage)))
                if audit is not None and max(lo, hi) >= len(files) and np.any(weight*biome_weight):
                    audit["clamped_ranges"].append({"biome":biome,"range":[lo,hi]})
        feature_total = np.maximum(sum(weight for _,weight in features), 1)
        selections.extend((selection, weight/feature_total*biome_weight) for selection,weight in features)
    if not selections:
        raise ValueError(f"No valid landscape array ranges: {tile['material']}")
    total = sum(weight for _,weight in selections)
    selections[0] = (selections[0][0], selections[0][1]+(total<1e-5))
    total = np.maximum(sum(weight for _,weight in selections), 1e-5)
    out = np.zeros((size, size, 3), dtype=np.float32)
    for selection, weight in selections:
        if not np.any(weight > 1e-6):
            continue
        selection = np.clip(selection, 0, len(files)-1)
        low = np.floor(selection).astype(int)
        high = np.minimum(low+1, len(files)-1)
        frac = selection-low
        active = weight > 1e-6
        for slice_id in np.unique(np.concatenate((low[active], high[active]))):
            image = sample_world_color(native_root/files[int(slice_id)], world_x, world_y, footprint_cm=footprint,
                                       srgb=parameters.get('texture_srgb', {}).get(source, True))
            contribution = ((low == slice_id)*(1-frac)+(high == slice_id)*frac)*weight/total
            out += image * contribution[:, :, None]
    return finish(out)


def stitch_boundaries(tiles, worlds):
    # UE patches neighboring landscape edges at runtime. Retain raw discrepancies
    # and use a deterministic shared-height blend for this static reconstruction.
    groups = {}
    for tile, world in zip(tiles, worlds):
        n = len(world)
        vertices = {(x,y) for x in range(n) for y in (0,n-1)} | {(x,y) for y in range(n) for x in (0,n-1)}
        for x,y in vertices:
            p = world[y,x]
            key = (tile.get('landscape_guid',tile['material']), round(p[0],3), round(p[1],3))
            groups.setdefault(key,[]).append((p,tile['source']))
    changed, shared, maximum = [], 0, 0
    for key, points in groups.items():
        if len(points)<2:
            continue
        shared += len(points)-1
        delta = max(p[0][2] for p in points)-min(p[0][2] for p in points)
        maximum = max(maximum,float(delta))
        if delta>1:
            changed.append({'xy_cm':list(key[1:]),'landscape':key[0],'delta_cm':float(delta),
                            'sources':[p[1] for p in points]})
        height = sum(p[0][2] for p in points)/len(points)
        for p,_ in points:
            p[2] = height
    return {'raw_max_border_delta_cm':maximum,'shared_border_samples':shared,'adjusted_boundaries':changed,
            'method':'Shared landscape boundary height average; static approximation of Unreal runtime edge/corner patching'}


def terrain_triangles(n, holes):
    idx = np.arange(n*n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    return np.stack((a, c, d, a, d, b), axis=-1)[~holes].reshape(-1)


def overview_indices(n, holes, stride=8):
    # Hole-bearing tiles retain their original triangles; sampling one corner of
    # a coarse quad can bridge a cave opening. Avoid mixed-resolution T-junctions.
    if np.any(holes):
        triangles = terrain_triangles(n, holes)
        if not len(triangles):
            return np.array([0,n-1,n*(n-1),n*n-1]), np.array([],dtype=np.uint32)
        return np.unique(triangles, return_inverse=True)
    stops = np.unique(np.append(np.arange(0, n, stride), n-1))
    triangles = []
    def quad(x0, y0, x1, y1):
        a,b,c,d = y0*n+x0, y0*n+x1, y1*n+x0, y1*n+x1
        triangles.extend((a,c,d,a,d,b))
    for y0,y1 in zip(stops[:-1], stops[1:]):
        for x0,x1 in zip(stops[:-1], stops[1:]):
            quad(x0,y0,x1,y1)
    if not triangles:
        return np.array([0,n-1,n*(n-1),n*n-1]), np.array([],dtype=np.uint32)
    vertices, indices = np.unique(triangles, return_inverse=True)
    return vertices, indices


def stitch_normals(tiles, worlds, normals):
    groups = {}
    for tile, world, normal in zip(tiles, worlds, normals):
        n = len(world)
        edges = {(x, y) for x in range(n) for y in (0, n-1)} | {(x, y) for y in range(n) for x in (0, n-1)}
        for x, y in edges:
            key = (tile.get('landscape_guid', tile['material']), *np.round(world[y, x], 3))
            groups.setdefault(key, []).append(normal[y, x])
    shared, maximum = 0, 0.0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        active = [row / np.linalg.norm(row) for row in rows if np.linalg.norm(row) > 1e-15]
        if len(active) > 1:
            cosine = np.clip(np.asarray(active) @ np.asarray(active).T, -1, 1)
            maximum = max(maximum, float(np.degrees(np.arccos(cosine.min()))))
        combined = np.sum(rows, axis=0)
        for row in rows:
            row[:] = combined
        shared += len(rows) - 1
    for normal in normals:
        normal /= np.maximum(np.linalg.norm(normal, axis=2)[:, :, None], 1e-15)
    return {'shared_border_samples':shared, 'raw_max_angle_degrees':maximum,
            'method':'Area-weighted shared vertex normals within the same landscape; overview reuses full-detail normals'}


def build_terrain(args, plan, native_results):
    textures = {r["source"]: r for r in native_results if r["kind"].startswith("texture") and "error" not in r}
    native = args.out_root / "source-data/native"
    origin = np.array(plan["region"]["origin_cm"])
    decoded, worlds = {}, []
    for tile in plan['terrain']:
        heights, height16, layers = decode_tile(tile,native,textures)
        n = len(heights)
        x,y = np.meshgrid(np.arange(n),np.arange(n))
        local = np.stack((x,y,heights,np.ones_like(x)),axis=-1).reshape(-1,4)
        world = (np.array(tile['matrix'])@local.T).T[:,:3].reshape(n,n,3)
        decoded[tile['source']] = (heights,height16,layers,world)
        worlds.append(world)
    boundary_report = stitch_boundaries(plan['terrain'],worlds)
    geometry = {}
    normals = []
    for tile, world in zip(plan['terrain'], worlds):
        n = len(world)
        layers = decoded[tile['source']][2]
        holes = layers.get('LandscapeVisibilityLayerInfo', np.zeros((n,n)))[:-1,:-1] >= 170
        triangles = terrain_triangles(n, holes)
        pos = ((world.reshape(-1,3)-origin)/100)[:, [0,2,1]]
        normal = normals_for(pos, triangles, normalize=False).reshape(n,n,3)
        normals.append(normal)
        geometry[tile['source']] = (pos, triangles, holes, normal)
    normal_report = stitch_normals(plan['terrain'], worlds, normals)
    def build_one(tile):
        heights, height16, layers, world = decoded[tile['source']]
        n = heights.shape[0]
        x, y = np.meshgrid(np.arange(n), np.arange(n))
        world = world.reshape(-1,3)
        pos, triangles, holes, normal = geometry[tile['source']]
        uv = np.stack((x/(n-1), y/(n-1)), axis=-1).reshape(-1, 2)
        key = identity_hash(tile["source"])
        folder = args.out_root/"assets/terrain"
        folder.mkdir(parents=True, exist_ok=True)
        color_audit = {}
        size = plan['region'].get('terrain_texture_size', 1024)
        image = bake_color(tile, heights, layers, plan, native, textures, size=size, audit=color_audit)
        Image.fromarray(image).save(folder/(key+".png"))
        Image.fromarray(height16).save(args.out_root/"source-data"/(key+"-height16.png"))
        write_mesh(folder/(key+".gltf"), pos, triangles, uv, key+".png", normals=normal.reshape(-1,3))
        overview = None
        if plan["region"].get("whole_world"):
            vertices, ot = overview_indices(n, holes)
            Image.fromarray(image).resize((256,256),Image.Resampling.LANCZOS).save(folder/(key+"-overview.webp"),quality=80)
            write_mesh(folder/(key+"-overview.gltf"),pos[vertices],ot,
                       uv[vertices],key+"-overview.webp",normals=normal.reshape(-1,3)[vertices])
            overview = "assets/terrain/"+key+"-overview.gltf"
        row = {"source": tile["source"], "path": "assets/terrain/"+key+".gltf", "bounds": tile["bounds"],
               "height_min_cm": float(world[:, 2].min()), "height_max_cm": float(world[:, 2].max()),
               "vertices": len(pos), "triangles": len(triangles)//3, "hole_quads": int(holes.sum()),
               "source_heightmap": ref_id(tile["properties"]["HeightmapTexture"]), "layers": list(layers),
               "material": tile["material"], "overview_path": overview}
        samples = [{"vertex": [int(x), int(y)], "packed_height": int(height16[y,x]),
                    "local_height": float(heights[y,x]), "world_cm": world[y*n+x].tolist()}
                   for x,y in ((0,0),(n//2,n//2),(n-1,n-1))]
        return row, {"source": tile["source"], "samples": samples, "color_bake": color_audit}, world.reshape(n,n,3)
    with ThreadPoolExecutor(max_workers=min(8, plan["region"]["workers"])) as pool:
        built = []
        for result in pool.map(build_one, plan['terrain']):
            built.append(result)
            if len(built)%25 == 0 or len(built)==len(plan['terrain']):
                print(f"Terrain: {len(built)}/{len(plan['terrain'])}",flush=True)
    if not any(row["height_max_cm"]-row["height_min_cm"] > 1 for row, _, _ in built):
        raise ValueError("Terrain is flat")
    # Compare coincident boundary vertices across components in the same landscape.
    edges, errors = {}, []
    for tile, (row, _, world) in zip(plan["terrain"], built):
        for p in np.concatenate((world[0], world[-1], world[:,0], world[:,-1])):
            key = (tile.get("landscape_guid", tile["material"]), round(p[0], 3), round(p[1], 3))
            if key in edges:
                errors.append(abs(edges[key]-p[2]))
            edges[key] = p[2]
    report = {"tiles": [row for row,_,_ in built], "samples": [s for _,s,_ in built],
              "boundary_reconstruction": boundary_report,
              "normal_reconstruction": normal_report,
              "shared_border_samples": len(errors), "max_border_delta_cm": max(errors, default=0),
              "native_probe": next(r for r in native_results if r["kind"] == "landscape_probe"),
              "material_approximation": "Source texture-array biome ranges and original layer weights, absent painted layers are zero, vertex-aligned mask filtering and linear-light color blending. Valid explicit feature ranges use a normalized paint overlay approximation. Footprint-filtered 4m world-space tiling remains approximate. Unreal procedural noise, height blending, wetness, RVT shading and full slope shader are not reconstructed; per-tile color_bake reports approximate features, unmodeled layers and range assumptions."}
    report["native_probe_comparison"] = compare_native_probe(plan["terrain"][0], report["native_probe"], native, textures)
    if report["max_border_delta_cm"] > 1:
        raise ValueError(f"Terrain seam exceeds 1cm: {report['max_border_delta_cm']}")
    return [r for r,_,_ in built], report
