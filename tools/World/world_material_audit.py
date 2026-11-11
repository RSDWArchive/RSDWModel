"""Source-grounded material diagnostics, not an automatic visual-parity verdict."""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from urllib.parse import unquote

from world_source import read_json, write_json


TEXTURE_FIELDS = {"base_color": "baseColorTexture", "normal": "normalTexture", "emissive": "emissiveTexture"}


def uses_adapter(asset, slot, desc):
    settings = desc.get("settings", {})
    override = asset["materials"][slot] if slot < len(asset["materials"]) else ""
    default = asset["default_materials"][slot] if slot < len(asset["default_materials"]) else ""
    return bool(asset["native"] or "emissive_enabled" in settings or settings.get("family") in ("foliage", "surface_maps", "rock", "water", "foam")
                or settings.get("surface_color") or desc.get("linear_color_sources")
                or any(mode in desc.get("properties", {}).get("BlendMode", "") for mode in ("Additive", "Modulate", "AlphaComposite"))
                or desc.get("projection") in ("triplanar", "landscape_color") or (override and override != default))


def texture_source(doc, material, role, gltf_path, model_root, identities):
    owner = material.get("pbrMetallicRoughness", {}) if role == "base_color" else material
    binding = owner.get(TEXTURE_FIELDS[role])
    if binding is None:
        return False, None
    texture = doc["textures"][binding["index"]]
    index = texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source", texture.get("source"))
    if index is None:
        return True, None
    uri = doc["images"][index].get("uri", "")
    if not uri or uri.startswith("data:"):
        return True, None
    path = (gltf_path.parent / unquote(uri)).resolve()
    try:
        key = path.relative_to(model_root.resolve()).as_posix()
    except ValueError:
        return True, None
    candidates = identities.get(key, set())
    return True, next(iter(candidates)) if len(candidates) == 1 else None


def surface_role_findings(params):
    """Check source intent independently of the inferred output role aliases."""
    textures, roles = params.get('textures', {}), params.get('roles', {})
    findings = []
    master = params.get('master', '').rsplit('/', 1)[-1].split('.')[0]
    if master == 'M_Rock' and textures.get('Rock BaseColor') != roles.get('base_color'):
        if textures.get('Rock BaseColor'):
            findings.append(('source_primary_color_mismatch', 'conversion_gap',
                             {'expected':textures['Rock BaseColor'], 'actual':roles.get('base_color')}))
    if master.startswith('M_Standard_Env_MR') and params.get('switches', {}).get('Is VT') is True:
        normal = 'Virtual Normal Map' if params.get('switches', {}).get('Use new Packing') else 'Virtual Normal'
        for role, name in (('base_color','Virtual BaseColor'), ('normal',normal)):
            if textures.get(name) and roles.get(role) != textures[name]:
                findings.append(('enabled_virtual_texture_branch_mismatch', 'conversion_gap',
                                 {'role':role,'expected':textures[name],'actual':roles.get(role)}))
    if master == 'M_Decal':
        if params.get('switches', {}).get('Use R as Alpha') is False:
            for role,name in (('base_color','BCO'),('normal','NR')):
                if textures.get(name) and roles.get(role) != textures[name]:
                    findings.append(('decal_surface_mapping_mismatch','conversion_gap',{'role':role,'expected':textures[name]}))
        else:
            findings.append(('decal_alpha_branch_unreconstructed','needs_review','Red-channel alpha branch requires explicit reconstruction'))
    if master == 'M_Master':
        for role, name in (('base_color', 'Albedo00'), ('normal', 'Normal00')):
            if textures.get(name) and roles.get(role) != textures[name]:
                findings.append(('legacy_primary_surface_mismatch', 'conversion_gap',
                                 {'role':role, 'expected':textures[name], 'actual':roles.get(role)}))
    if master == 'M_Character_Standard':
        virtual = params.get('switches', {}).get('UseVT') is True
        for role, name in (('base_color', 'BaseColor_VT' if virtual else 'BaseColor Map'),
                           ('normal', 'Normal_VT' if virtual else 'Normal')):
            if textures.get(name) and roles.get(role) != textures[name]:
                findings.append(('character_surface_branch_mismatch', 'conversion_gap',
                                 {'role':role, 'expected':textures[name], 'actual':roles.get(role)}))
    if master == 'M_Withered' and textures.get('RockFractures') != roles.get('base_color'):
        if textures.get('RockFractures'):
            findings.append(('withered_surface_proxy_missing', 'conversion_gap',
                             {'expected':textures['RockFractures'], 'actual':roles.get('base_color')}))
    if master in ('M_Kuldra_Basic', 'M_BasicMasked'):
        names = ('BCR', 'NM') if master == 'M_Kuldra_Basic' else ('BCA', 'NR')
        for role, name in zip(('base_color', 'normal'), names):
            if textures.get(name) and roles.get(role) != textures[name]:
                findings.append(('named_surface_mapping_missing', 'conversion_gap',
                                 {'role':role, 'expected':textures[name], 'actual':roles.get(role)}))
    if master in ('M_SW_Crystals', 'M_SW_CrystalsFade'):
        if roles.get('base_color') or roles.get('emissive'):
            findings.append(('crystal_mask_misassigned', 'conversion_gap', roles))
        if textures.get('Crystal Normal') and roles.get('normal') != textures['Crystal Normal']:
            findings.append(('crystal_normal_missing', 'conversion_gap', textures['Crystal Normal']))
    if master == 'M_StainedGlassOpaque':
        for role, name in (('base_color','BC'),('normal','N')):
            if textures.get(name) and roles.get(role) != textures[name]:
                findings.append(('stained_glass_mapping_missing', 'conversion_gap', {'role':role,'expected':textures[name]}))
    switches = params.get('switches', {})
    if ((master == 'M_ShellFur' and switches.get('UseTexture?') is False)
            or (master == 'M_NPC_Ghost' and switches.get('UseBaseColour') is False)
            or master == 'M_VFX_SW_AnimaDoor_Air_Persist') and roles.get('base_color'):
        findings.append(('inactive_or_effect_texture_used_as_diffuse', 'conversion_gap', roles['base_color']))
    if master in ('M_SimpleGlass', 'M_NPC_Ghost') and switches.get('UseNormal') is False and roles.get('normal'):
        findings.append(('source_disabled_normal_selected', 'conversion_gap', roles['normal']))
    color = roles.get('base_color', '')
    if '/DefaultTextures/' in color or '/EngineMaterials/Default' in color:
        findings.append(('default_color_texture_review', 'needs_review',
                         {'selected':color, 'reason':'Default color texture is not proof of intentional flat color; verify the active source branch'}))
    return findings


def audit_materials(scene, parameters, model_root):
    model_root = Path(model_root)
    manifest_path = model_root / "WebAssetManifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    identities = defaultdict(set)
    for entry in manifest.get("entries", {}).values():
        # This is the upstream manifest's serialized source -> output mapping.
        for mapping in entry.get("used_textures", []):
            source, separator, output = mapping.partition(" -> ")
            if separator:
                identities[output.replace("\\", "/")].add(source.rsplit(".", 1)[0])
    placements = defaultdict(list)
    for placement in scene["placements"]:
        if not placement.get("baked_road"):
            placements[placement["asset"]].append(placement)
    rows, documents, checked_sections = {}, {}, 0
    for asset in scene["assets"]:
        instances = placements[asset["id"]]
        if not instances:
            continue
        doc, primitives = {}, []
        path = model_root / asset["path"]
        if asset["root"] == "models" and path.suffix == ".gltf" and path.is_file():
            if asset["path"] not in documents:
                documents[asset["path"]] = read_json(path)
            doc = documents[asset["path"]]
            primitives = [p for mesh in doc.get("meshes", []) for p in mesh.get("primitives", [])]
        slots = asset["section_material_slots"] or list(range(len(asset["default_materials"])))
        for section, slot in enumerate(slots):
            checked_sections += 1
            default = asset["default_materials"][slot] if slot < len(asset["default_materials"]) else ""
            override = asset["materials"][slot] if slot < len(asset["materials"]) else ""
            source = override or default
            desc = scene["materials"].get(source, {})
            params = parameters.get(source, {})
            if source not in rows:
                rows[source] = {"material": source, "master": desc.get("master"), "models": set(), "assets": set(),
                                "placements": set(), "issues": [], "sample": {"placement": instances[0]["id"],
                                "position_cm": [instances[0]["matrix"][i][3] for i in range(3)]}}
            row = rows[source]
            row["models"].add(asset["source"])
            row["assets"].add(asset["id"])
            row["placements"].update(p["id"] for p in instances)

            def issue(code, status, detail, confidence="source_confirmed"):
                value = dict(code=code, status=status, confidence=confidence, detail=detail)
                if value not in row["issues"]:
                    row["issues"].append(value)

            if not desc:
                issue("material_descriptor_missing" if source else "source_material_unassigned",
                      "conversion_gap" if source else "needs_review",
                      "No resolved source descriptor" if source else "The original mesh slot and component override are both null; no source texture assignment exists")
            for code, status, detail in surface_role_findings(params):
                issue(code, status, detail)
            for gap in desc.get("texture_gaps", []):
                issue("texture_role_unavailable", "needs_review" if desc.get("projection")=="landscape_color" else "conversion_gap", gap)
            if params.get("unavailable_defaults"):
                issue("source_defaults_unavailable", "needs_review", params["unavailable_defaults"])
            if desc.get('linear_color_sources'):
                issue('source_linear_color_encoded', 'corrected_in_preview', desc['linear_color_sources'],
                      'Source SRGB=false color channels encoded for browser color sampling; alpha unchanged')
            if desc.get('settings', {}).get('family') == 'water':
                issue('source_water_surface_restored', 'corrected_in_preview',
                      {'normal':desc.get('textures', {}).get('normal'), 'settings':desc['settings']['water']},
                      'Source normal maps and roughness restored; color and coordinate reconstruction remain approximations')
            if desc.get('settings', {}).get('family') == 'foam':
                issue('source_foam_mask_restored', 'corrected_in_preview',
                      {'mask':desc.get('textures', {}).get('foam_mask'), 'settings':desc['settings']['foam']},
                      'Static source texture/opacity subset; not a reconstructed particle shader')
            if desc.get('settings', {}).get('deformation_review'):
                issue('dune_deformation_unreconstructed', 'needs_review', desc['settings']['deformation_review'])
            if desc.get('settings', {}).get('surface_color'):
                issue('source_color_proxy_applied', 'corrected_in_preview', desc['settings']['surface_color'],
                      'Source RGB tint confirmed; tint-only proxy is not proof of original shader math. Brightness remains unapplied.')
                issue('source_color_proxy_manual_review', 'needs_review', desc['settings']['color_review'],
                      'Manual in-game comparison required')
            references = params.get("referenced_textures", [])
            if references and not params.get("textures") and not params.get("roles"):
                issue("hardwired_shader_unmapped", "needs_review", references,
                      "Shader references textures, but their connections are not reconstructed")
            if params.get("textures") and not params.get("roles"):
                issue("parameterized_shader_unmapped", "needs_review", sorted(params["textures"]),
                      "Texture parameters are present, but no supported surface texture roles are known")
            master = str(desc.get("master", "")).rsplit("/", 1)[-1].split(".")[0]
            if master.startswith("M_VFX_") and desc.get("settings", {}).get("family") != "geyser_stones":
                issue("effect_shader_unreconstructed", "needs_review", master,
                      "Static geometry exists; procedural VFX shader and animation are not reproduced")
            if desc.get("settings", {}).get("approximation"):
                issue("shader_approximation", "needs_review", desc["settings"]["approximation"])
            if desc.get("projection") or desc.get("approximation"):
                issue("shader_approximation", "needs_review", desc.get("projection") or desc["approximation"])
            tint = params.get("vectors", {}).get("Color Multiplier")
            brightness = params.get("scalars", {}).get("Brightness", 1)
            if (tint and any(abs(tint.get(c, 1)-1)>0.001 for c in "RGB")) or brightness != 1:
                issue("color_adjustment_unverified", "needs_review", {"tint": tint, "brightness": brightness},
                      "Parameters exist; their shader math and cached conversion need comparison")
            # Compare upstream maps only to the same material, not a component override.
            if source != default or section >= len(primitives):
                continue
            primitive = primitives[section]
            if "material" not in primitive:
                issue("upstream_material_missing", "conversion_gap", asset["source"])
                continue
            original = doc["materials"][primitive["material"]]
            adapted = uses_adapter(asset, slot, desc)
            for role, expected in params.get("roles", {}).items():
                if role not in TEXTURE_FIELDS:
                    continue
                present, actual = texture_source(doc, original, role, path, model_root, identities)
                if present and actual == expected.rsplit(".", 1)[0]:
                    continue
                if present and actual is None:
                    issue("texture_identity_unverified", "needs_review", {"role": role, "model": asset["source"]})
                    continue
                corrected = adapted and role in desc.get("textures", {})
                issue("upstream_texture_mismatch" if present else "upstream_texture_missing",
                      "corrected_in_preview" if corrected else "needs_review",
                      {"role": role, "expected": expected, "actual": actual, "model": asset["source"]},
                      "Descriptor mapping applied by preview" if corrected else
                      "Parameter-derived role; verify active shader branches before changing cached material")
            emission = max(original.get("emissiveFactor", [0, 0, 0]))
            if desc.get("settings", {}).get("emissive_enabled") is False and emission > 0:
                issue("upstream_source_disabled_emission", "corrected_in_preview" if adapted else "conversion_gap",
                      {"model": asset["source"], "emissive_factor": original["emissiveFactor"]})
    priority = {"conversion_gap": 0, "needs_review": 1, "corrected_in_preview": 2, "no_detected_gap": 3}
    for row in rows.values():
        row["placement_count"] = len(row.pop("placements"))
        row["models"] = sorted(row["models"])
        row["assets"] = sorted(row["assets"])
        row["status"] = min((i["status"] for i in row["issues"]), key=priority.get, default="no_detected_gap")
    ordered = sorted(rows.values(), key=lambda r: (priority[r["status"]], -r["placement_count"], r["material"]))
    return {"schema": "RSDW.WorldMaterialAudit.v1", "version": scene["version"],
            "scope": "Active static-mesh sections and effective component overrides in this region; terrain has its own report",
            "visual_parity": "unverified; no_detected_gap is not a visual correctness certificate",
            "upstream_texture_manifest_available": bool(manifest),
            "summary": {"materials": len(ordered), "mesh_sections": checked_sections,
                        "models": len({a["source"] for a in scene["assets"] if placements.get(a["id"])}),
                        "placements": sum(len(p) for p in placements.values()),
                        "by_status": dict(Counter(r["status"] for r in ordered)),
                        "issues_by_code": dict(Counter(i["code"] for r in ordered for i in r["issues"]))},
            "materials": ordered}


def write_material_audit(args, scene, parameters):
    model_root = Path(__file__).resolve().parents[2] / args.version / "WebAssets"
    audit = audit_materials(scene, parameters, model_root)
    audit["scene_sha256"] = hashlib.sha256((args.out_root / "WorldRegion.json").read_bytes()).hexdigest()
    write_json(args.out_root / "MaterialAudit.json", audit)
    rows = defaultdict(lambda: {"assets": set(), "placements": 0, "materials": set(), "statuses": set()})
    counts = Counter(p["asset"] for p in scene["placements"] if not p.get("baked_road"))
    by_material = {r["material"]: r for r in audit["materials"]}
    for asset in scene["assets"]:
        if not counts[asset["id"]]:
            continue
        row = rows[asset["source"]]
        row["assets"].add(asset["id"])
        row["placements"] += counts[asset["id"]]
        for slot in asset["section_material_slots"] or range(len(asset["default_materials"])):
            override = asset["materials"][slot] if slot < len(asset["materials"]) else ""
            material = override or (asset["default_materials"][slot] if slot < len(asset["default_materials"]) else "")
            row["materials"].add(material)
            row["statuses"].add(by_material.get(material, {}).get("status", "conversion_gap"))
    priority = {"conversion_gap": 0, "needs_review": 1, "corrected_in_preview": 2, "no_detected_gap": 3}
    models = [{"model": model, "placements": row["placements"], "assets": sorted(row["assets"]),
               "materials": sorted(row["materials"]), "status": min(row["statuses"], key=priority.get, default="needs_review")}
              for model, row in rows.items()]
    models.sort(key=lambda r: (priority[r["status"]], -r["placements"], r["model"]))
    write_json(args.out_root / "ModelAudit.json", {"schema": "RSDW.WorldModelAudit.v1", "version": scene["version"],
               "scene_sha256": audit["scene_sha256"], "visual_parity": audit["visual_parity"],
               "summary": dict(Counter(r["status"] for r in models)), "models": models})
    samples = {}
    for placement in scene['placements']:
        if not placement.get('baked_road'):
            samples.setdefault(placement['asset'], placement)
    variants = []
    for asset in scene['assets']:
        if asset['id'] not in samples:
            continue
        refs = set()
        for slot in asset['section_material_slots'] or range(len(asset['default_materials'])):
            override = asset['materials'][slot] if slot < len(asset['materials']) else ''
            refs.add(override or (asset['default_materials'][slot] if slot < len(asset['default_materials']) else ''))
        issues = sorted({i['code'] for ref in refs for i in by_material.get(ref, {}).get('issues', [])})
        notes = []
        for ref in sorted(refs):
            settings = scene['materials'].get(ref, {}).get('settings', {})
            messages = [settings.get('approximation'), settings.get('color_review'), settings.get('manual_review'),
                        settings.get('deformation_review', {}).get('reason')]
            messages = list(dict.fromkeys(message for message in messages if message))
            if messages:
                notes.append({'material':ref, 'notes':messages})
        variants.append({'asset':asset['id'], 'source':asset['source'], 'layer':asset['layer'],
                         'placements':counts[asset['id']], 'sample':samples[asset['id']],
                         'materials':sorted(refs), 'source_issues':issues,
                         'review_notes':notes,
                         'review_status':'manual_review_pending'})
    variants.sort(key=lambda row:(not bool(row['source_issues']), -row['placements'], row['asset']))
    write_json(args.out_root/'VisualAuditIndex.json', {'schema':'RSDW.WorldVisualAuditIndex.v1',
               'version':scene['version'], 'scene_sha256':audit['scene_sha256'], 'variants':variants,
               'scope':'Every active mesh/effective-material variant, including intentionally hidden helpers. Isolated views do not reconstruct environment-dependent shaders.'})
    print("Material audit:", audit["summary"]["by_status"], flush=True)
    return audit["summary"]
