"""Spatial sections share models/materials and retain stable placement identities."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

from world_source import read_json, write_json


def identity_digest(ids):
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def write_payload(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, separators=(",", ":"), allow_nan=False)


def section_cells(bounds, size, limit=64):
    lo = [math.floor(bounds[0][i]/size) for i in range(2)]
    hi = [math.floor(bounds[1][i]/size) for i in range(2)]
    if (hi[0]-lo[0]+1)*(hi[1]-lo[1]+1)>limit:
        return []
    return [(x,y) for x in range(lo[0],hi[0]+1) for y in range(lo[1],hi[1]+1)]


def section_id(cell):
    return f"x{cell[0]}_y{cell[1]}"


def terrain_region(source):
    name = source.rsplit("/",1)[-1].split(".")[0]
    for needle, label in (("Fellhollow","Fellhollow"),("DowdunReach","Dowdun Reach"),
                          ("UmbralSands","Umbral Sands"),("ScornedWilderness","Scorned Wilderness"),
                          ("Sand_Vault","Sand Vaults"),("Cave","Caves")):
        if needle in name:
            return label
    return "Ashenfall"


def content_summary(placements, assets):
    layers = Counter(assets[p['asset']]['layer'] for p in placements)
    visible = [p for p in placements if assets[p['asset']]['layer'] != 'helpers']
    bounds = None
    if visible:
        bounds = {'min':[min(p['bounds'][0][i] for p in visible) for i in range(3)],
                  'max':[max(p['bounds'][1][i] for p in visible) for i in range(3)]}
    return {'layers':dict(layers), 'visible_placements':len(visible), 'content_bounds_cm':bounds}


def build_sections(args):
    scene = read_json(args.out_root / "WorldRegion.json")
    audit = read_json(args.out_root / "MaterialAudit.json")
    assets = {a["id"]: a for a in scene["assets"]}
    audits = {r["material"]:r for r in audit["materials"]}
    size = scene["region"]["section_size_cm"]
    sections, spanning = {}, []
    def ensure(cell):
        key = section_id(cell)
        if key not in sections:
            sections[key] = {"schema":"RSDW.WorldSection.v1", "id":key, "cell":list(cell),
                             "bounds_cm":{"min":[cell[0]*size,cell[1]*size,-1e9],"max":[(cell[0]+1)*size,(cell[1]+1)*size,1e9]},
                             "placements":[], "terrain":[]}
        return sections[key]
    for tile in scene["terrain"]:
        for cell in section_cells(tile["bounds"],size):
            ensure(cell)["terrain"].append(tile)
    for placement in scene["placements"]:
        if placement.get("baked_road"):
            continue
        cells = section_cells(placement["bounds"],size)
        if not cells:
            spanning.append(placement)
        for cell in cells:
            ensure(cell)["placements"].append(placement)
    used_assets = {p["asset"] for p in scene["placements"] if not p.get("baked_road")}
    used_materials = set()
    for aid in used_assets:
        a = assets[aid]
        for slot in a["section_material_slots"] or range(len(a["default_materials"])):
            override = a["materials"][slot] if slot < len(a["materials"]) else ""
            used_materials.add(override or (a["default_materials"][slot] if slot < len(a["default_materials"]) else ""))
    shared = {key: scene[key] for key in ("version","coordinates","region")}
    shared["assets"] = [a for a in scene["assets"] if a["id"] in used_assets]
    shared["materials"] = {key: value for key, value in scene["materials"].items() if key in used_materials}
    shared["schema"] = "RSDW.WorldShared.v1"
    shared["overview"] = [t for t in scene["terrain"] if t.get("overview_path")]
    write_payload(args.out_root/"WorldShared.json", shared)
    write_payload(args.out_root/"SpanningPlacements.json", spanning)
    rows, reports = [], []
    for key, section in sorted(sections.items()):
        material_ids, asset_ids = set(), set()
        for p in section["placements"]:
            asset_ids.add(p["asset"])
        for aid in asset_ids:
            a = assets[aid]
            for slot in a["section_material_slots"] or range(len(a["default_materials"])):
                override = a["materials"][slot] if slot<len(a["materials"]) else ""
                material_ids.add(override or (a["default_materials"][slot] if slot<len(a["default_materials"]) else ""))
        statuses = dict(Counter(audits[m]["status"] for m in material_ids if m in audits))
        biome_counts = Counter(terrain_region(t["material"]) for t in section["terrain"])
        surface = {name:n for name,n in biome_counts.items() if name not in ("Caves","Sand Vaults")}
        biome = max(surface or biome_counts,key=(surface or biome_counts).get) if biome_counts else "Outlying Scenery"
        bounds = [t["bounds"] for t in section["terrain"]] + [p["bounds"] for p in section["placements"]]
        zmin = min(b[0][2] for b in bounds)
        zmax = max(b[1][2] for b in bounds)
        section["bounds_cm"]["min"][2] = zmin
        section["bounds_cm"]["max"][2] = zmax
        name = f"{biome} [{section['cell'][0]}, {section['cell'][1]}]"
        section.update(name=name, biome=biome, asset_ids=sorted(asset_ids))
        path = "sections/"+key+".json"
        write_payload(args.out_root/path, section)
        rows.append({"id":key,"name":name,"biome":biome,"path":path,"bounds_cm":section["bounds_cm"],
                     "placements":len(section["placements"]),"assets":len(asset_ids),"terrain":len(section["terrain"]),
                     **content_summary(section['placements'], assets),
                     "classification":"landscape_material" if biome_counts else "no_landscape_fallback",
                     "material_statuses":statuses, "bytes":(args.out_root/path).stat().st_size})
        reports.append({"section":key,"name":name,"status":"built_pending_visual_review",
                        "models":sorted({assets[a]["source"] for a in asset_ids}),"materials":sorted(material_ids),
                        "material_statuses":statuses})
    world_bounds = {"min":[min(t["bounds"][0][i] for t in scene["terrain"]) for i in range(3)],
                    "max":[max(t["bounds"][1][i] for t in scene["terrain"]) for i in range(3)]}
    scene_hash = hashlib.sha256((args.out_root/"WorldRegion.json").read_bytes()).hexdigest()
    world = {"schema":"RSDW.WorldIndex.v1", "version":scene["version"], "shared":"WorldShared.json",
             "spanning":"SpanningPlacements.json", "bounds_cm":world_bounds, "sections":rows,
             "scene_sha256":scene_hash, "distinct_models":len({a['source'] for a in shared['assets']}),
             "unique_placements":sum(not p.get('baked_road') for p in scene['placements']),
             "placement_ids_sha256":identity_digest(p['id'] for p in scene['placements'] if not p.get('baked_road')),
             "terrain_ids_sha256":identity_digest(t['source'] for t in scene['terrain']),
             "spanning_placements":len(spanning), "section_size_cm":size,
             "coverage":"Packaged static world. Excluded helpers and unsupported runtime actor attachments are listed in WorldRegionReport.json"}
    write_json(args.out_root/"WorldIndex.json",world)
    write_json(args.out_root/"SectionAudit.json",{"schema":"RSDW.WorldSectionAudit.v1","version":scene["version"],
               "scene_sha256":scene_hash,"sections":reports,"material_audit":"MaterialAudit.json",
               "exclusions":dict(Counter(x['reason'] for x in read_json(args.out_root/'WorldRegionReport.json')['excluded'])),
               "visual_parity":"Pending in-game comparison; material review flags are not silently accepted"})
    print(f"Built {len(rows)} sections, {len(spanning)} spanning placements",flush=True)


def validate_sections(args):
    index = read_json(args.out_root/"WorldIndex.json")
    audit = read_json(args.out_root/"MaterialAudit.json")
    if index["scene_sha256"] != audit["scene_sha256"]:
        raise ValueError("World index and material audit do not match")
    shared = read_json(args.out_root/index["shared"])
    assets = {a["id"] for a in shared["assets"]}
    seen, terrain_seen, failures = set(), set(), []
    for tile in shared["overview"]:
        path = args.out_root / tile["overview_path"]
        if not path.is_file():
            failures.append(tile["source"]+": missing overview")
            continue
        doc = read_json(path)
        for dependency in doc.get("buffers", [])+doc.get("images", []):
            if not (path.parent/dependency["uri"]).is_file():
                failures.append(str(path)+": missing overview dependency")
    for row in index["sections"]:
        section = read_json(args.out_root/row["path"])
        if section["id"] != row["id"] or len(section["placements"]) != row["placements"]:
            failures.append(row["id"]+": section manifest mismatch")
        for p in section["placements"]:
            seen.add(p["id"])
            if p["asset"] not in assets:
                failures.append(p["id"]+": missing asset")
        terrain_seen.update(t["source"] for t in section["terrain"])
    for p in read_json(args.out_root/index["spanning"]):
        seen.add(p["id"])
        if p["asset"] not in assets:
            failures.append(p["id"]+": missing spanning asset")
    if len(seen) != index["unique_placements"]:
        failures.append("Section partition lost or duplicated placement identities")
    if identity_digest(seen) != index["placement_ids_sha256"]:
        failures.append("Section placement identity digest mismatch")
    if identity_digest(terrain_seen) != index["terrain_ids_sha256"]:
        failures.append("Section terrain coverage mismatch")
    result = {"status":"failed" if failures else "passed","sections":len(index['sections']),
              "unique_placements":len(seen),"failures":failures}
    write_json(args.out_root/"SectionValidation.json",result)
    if failures:
        raise ValueError("; ".join(failures[:10]))
    print("Section coverage validated:",result,flush=True)
