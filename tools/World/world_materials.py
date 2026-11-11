"""Read cooked material parameters; keep shader approximations explicit."""
from world_source import ref_id

GEYSER_MASTER = "RSDragonwilds/Content/Art/VFX/Library/Env/MagicWell/M_VFX_GeyserStones.0"
GEYSER_TEXTURES = {"base_color": "T_Rune_Essence_01_BCC", "normal": "T_Rune_Essence_01_NRM",
                   "emissive": "T_Rune_Essence_01_E"}
WATER_MASTERS = {"M_Water", "M_Water_Translucent", "M_Water_River"}
STANDARD_COLOR_MASTERS = {"M_Standard_Env_MR", "M_Standard_Env_MR_Ageing", "M_Standard_Env_MR_Emissive"}
VINES_MASTER = "RSDragonwilds/Plugins/GameFeatures/ScornedWilderness/Content/Art/Env/Kuldra_Interior/Materials/MasterMaterials/M_TunnelVines_01.0"
VINES_TEXTURES = {"base_color": "T_KuldraVines_BCC", "normal": "T_KuldraVines_NRM"}
SLIME_MASTER = VINES_MASTER.replace('M_TunnelVines_01.0', 'M_Kuldra_Slime.0')
SLIME_TEXTURES = {"base_color": "T_Heart_Base_BCC", "normal": "T_Heart_Base_NRM"}
CRYSTAL_MASTERS = {'M_SW_Crystals', 'M_SW_CrystalsFade'}


def surface_color_settings(parameters):
    """An explicit linear-RGB subset, not a reconstruction of the cooked graph."""
    master = parameters.get("master", "").rsplit("/", 1)[-1].split(".")[0]
    if master not in STANDARD_COLOR_MASTERS and master not in ("M_Withered", "M_DR_Highlight_Ageing"):
        return None
    name = {"M_Withered": "Tint", "M_DR_Highlight_Ageing": "Colour Multiplier"}.get(master, "Color Multiplier")
    tint = parameters.get("vectors", {}).get(name)
    brightness = parameters.get("scalars", {}).get("Brightness", 1) if master != "M_Withered" else 1
    rgb = [max(0, tint.get(c, 1)) for c in "RGB"] if tint else [1, 1, 1]
    if master != "M_Withered" and rgb == [1, 1, 1]:
        return None
    return {"parameter": name, "multiplier": rgb, "unapplied_brightness": brightness,
            "method": "linear_rgb_tint_only", "review": "manual_comparison_required"}


def set_parameter(values, name, value):
    # Unreal parameter names are FNames: a child spelling must override its
    # inherited value even when capitalization differs.
    existing = next((key for key in values if key.casefold() == name.casefold()), name)
    values[existing] = value


def material_parameters(resolver, identity, seen=()):
    if not identity:
        return {"textures": {}, "scalars": {}, "vectors": {}, "properties": {}, "switches": {}}
    identity = resolver.canonical(identity)
    if identity in seen:
        raise ValueError(f"Material parent cycle: {identity}")
    obj = resolver.get(identity)
    props = obj.get("Properties", {})
    parent = ref_id(props.get("Parent"))
    try:
        result = material_parameters(resolver, parent, seen+(identity,))
    except FileNotFoundError:
        result = {"textures": {}, "scalars": {}, "vectors": {}, "properties": {}, "switches": {}, "unavailable_defaults": [parent]}
    if not parent:
        result["master"] = identity
        result["properties"].update(BlendMode="EBlendMode::BLEND_Opaque", TwoSided=False, OpacityMaskClipValue=0.333333)
    cached = obj.get("CachedExpressionData", {})
    # Cooked masters can hard-wire texture samples without exposing parameters.
    references = set(result.get("referenced_textures", []))
    references.update(ref_id(r) for r in cached.get("ReferencedTextures", []) + obj.get("ReferencedTextures", []))
    result["referenced_textures"] = sorted(references - {""})
    functions = set(result.get("referenced_functions", []))
    functions.update(ref_id(row.get("Function")) for row in cached.get("FunctionInfos", []))
    result["referenced_functions"] = sorted(functions - {""})
    for key, entry, values in [("scalars", "RuntimeEntries", "ScalarValues"),
                               ("vectors", "RuntimeEntries[1]", "VectorValues"),
                               ("textures", "RuntimeEntries[3]", "TextureValues")]:
        for info, value in zip(cached.get(entry, {}).get("ParameterInfoSet", []), cached.get(values, [])):
            if key == "textures":
                path = value.get("AssetPathName") or ref_id(value)
                if not path or path == "None":
                    continue
                try:
                    value = resolver.canonical(path)
                except FileNotFoundError:
                    result.setdefault("unavailable_defaults", []).append(path)
                    continue
            set_parameter(result[key], info["Name"], value)
    for key, field in [("textures", "TextureParameterValues"), ("scalars", "ScalarParameterValues"), ("vectors", "VectorParameterValues")]:
        for row in props.get(field, []):
            value = row.get("ParameterValue")
            if key == "textures":
                if not value:
                    continue
                value = resolver.canonical(value)
            set_parameter(result[key], row["ParameterInfo"]["Name"], value)
    for info, value in zip(cached.get("RuntimeEntries[8]", {}).get("ParameterInfoSet", []),
                           cached.get("StaticSwitchValues", [])):
        set_parameter(result["switches"], info["Name"], bool(value))
    for field in ("StaticParameters", "StaticParametersRuntime"):
        for row in props.get(field, {}).get("StaticSwitchParameters", []):
            if row.get("bOverride", False):
                set_parameter(result["switches"], row["ParameterInfo"]["Name"], bool(row["Value"]))
    overrides = props.get("BasePropertyOverrides", {})
    for key, value in overrides.items():
        if not key.startswith("bOverride_") and overrides.get("bOverride_"+key, False):
            result["properties"][key] = value
    for key in ("BlendMode", "TwoSided", "OpacityMaskClipValue", "ShadingModel"):
        if key in props:
            result["properties"][key] = props[key]
    result["source"] = identity
    return result


def render_settings(parameters):
    master = parameters.get("master", "").rsplit("/", 1)[-1].split(".")[0]
    switches = parameters.get("switches", {})
    settings = {}
    if parameters.get("master") == GEYSER_MASTER:
        settings.update(family="geyser_stones", emissive_enabled=True,
                        required_texture_roles=list(GEYSER_TEXTURES),
                        approximation="Static source textures; shader-driven rotation and glow animation omitted")
    if "CanBeEmissive" in switches:
        settings["emissive_enabled"] = switches["CanBeEmissive"]
    if master == "M_Standard_Env_MR_Emissive":
        settings.update(family="standard_emissive", emissive_enabled=parameters.get("scalars", {}).get("IsOff", 0)<1)
    if master in ("M_Foliage_Small", "M_Foliage_Tree", "M_Grass", "M_TreeTrunk"):
        settings["family"] = "foliage"
        settings["vertex_color_mode"] = "ao_red" if switches.get("AO from VertexCol Red") else "ignore"
    if master in ("M_DR_Highlight_Ageing", "M_FlattenMaterial_Mechanised", "M_dreamstone", "M_Basic_VertPaint"):
        settings.update(family="surface_maps", approximation=
                        "Source base/normal maps; vertex/moss layers, packed emission, procedural effects and custom UV animation remain unimplemented")
    if master == "M_ShellFur":
        settings["approximation"] = "Static shell geometry/color; source UseTexture branch is respected, fur shader behavior is not reproduced"
        if switches.get("UseTexture?") is False:
            settings.update(family="surface_maps", base_color_enabled=False, base_color_parameter="MixColor",
                            approximation="Source UseTexture=false: cached Ram/default diffuse is removed. MixColor is a base-only proxy; "
                            "OverlayColor, shell masks, AO, thickness and advanced color operations require in-game comparison.")
    if master == "M_Tool_ProcGrid":
        settings.update(family="surface_maps", base_color_enabled=False, base_color_parameter="Checker Colour 1",
                        surface_pbr={"roughness": min(1, max(0, parameters.get("scalars", {}).get("Checker Rough 1", .5))), "metalness": 0},
                        approximation="First source checker color/roughness only; procedural checker lines, second color, "
                        "object alignment, side/water tint remain unreconstructed. This is a proxy material, not missing albedo.")
    if master == "M_NPC_Ghost":
        settings.update(family="surface_maps", approximation="Static source branch selection; Color A is a base-only proxy when "
                        "UseBaseColour=false. Caustics, opacity masks, rim emission, depth/distance fade and displacement remain unreconstructed.")
        if "UseBaseColour" in switches:
            settings["base_color_enabled"] = switches["UseBaseColour"]
        if switches.get("UseBaseColour") is False:
            settings["base_color_parameter"] = "Color A"
        if "UseNormal" in switches:
            settings["normal_enabled"] = switches["UseNormal"]
    if master == "M_SimpleGlass":
        settings.update(family="surface_maps", base_color_enabled=False, base_color_parameter="TintGlass",
                        approximation="Source TintGlass and minimum opacity form a static proxy, not refractive glass. "
                        "Second tint, view-angle opacity, reflections and refraction remain pending in-game comparison.")
        if "UseNormal" in switches:
            settings["normal_enabled"] = switches["UseNormal"]
        if "OpacityMin" in parameters.get("scalars", {}):
            settings["surface_opacity"] = min(1, max(0, parameters["scalars"]["OpacityMin"]))
    if master == "M_VFX_SW_AnimaDoor_Air_Persist":
        settings.update(family="surface_maps", base_color_enabled=False, base_color_parameter="BaseColour",
                        approximation="Source BaseColour only; Edge_Texture_02 is NOT diffuse albedo. Edge masks, cubemap, "
                        "spawn/day-night opacity, distortion, refraction and animation are unreconstructed.")
    if master == "M_VFX_SpectralPath_Cobbles":
        settings.update(family="surface_maps", base_color_enabled=False, base_color_parameter="ColourA",
                        approximation="Static source ColourA only; Wither fade, depth culling, path masks, emission and animation "
                        "are unreconstructed. No invented mask or forced invisibility.")
    if master in ("M_AnimaCloudLayer", "M_AnimaEnergy_01", "M_AnimaEnergy_02"):
        settings.update(family="surface_maps", approximation="Static source Opacity scalar only, not full opacity shader math. "
                        "Curve colors, layered masks, UV distortion, caustics, emission, WPO and animation remain unreconstructed.")
        if "Opacity" in parameters.get("scalars", {}):
            settings["surface_opacity"] = min(1, max(0, parameters["scalars"]["Opacity"]))
    if master == "M_Standard_Env_MR_BackgroundMountains":
        settings.update(family="surface_maps", base_color_parameter="Color Base", approximation=
                        "Source Color Base tint and normal map; mountain splat/contrast blending remains unverified")
    if master == "M_Rock":
        settings.update(family="rock", base_color_parameter="Rock BaseColor Multiplier",
                        detail_layer_enabled=bool(switches.get("Use Detail Layer")), approximation=
                        "Source Rock BaseColor/Normal and multiplier; optional moss/detail blend, curvature, wetness and landscape blending need in-game comparison")
        if parameters.get("scalars", {}).get("Emissive Strength", 0) == 0:
            settings["emissive_enabled"] = False
    if master.startswith('M_Standard_Env_MR') and switches.get('Is VT') is True:
        settings.setdefault('family', 'surface_maps')
        settings.update(virtual_texture_enabled=True, approximation=
                        'Enabled virtual base/normal texture branch decoded to static maps; RVT blending, wetness and virtual streaming remain unreconstructed')
    if master == 'M_Decal' and switches.get('Use R as Alpha') is False:
        settings.update(family='surface_maps', approximation=
                        'Static BCO color/alpha and NR normal on source mesh; deferred decal projection, packed roughness and emissive hookup remain unreconstructed')
    if master == 'M_Master':
        settings.update(family='surface_maps', approximation=
                        'Primary Albedo00/Normal00 surface; layer blending, tint masks, custom UVs and wetness remain unverified')
    if master == 'M_Character_Standard':
        settings.update(family='surface_maps', approximation=
                        'Active regular or virtual base/normal maps; dye, subsurface, custom primitive data and character effects remain unreconstructed')
        if 'HasOpacity' in switches:
            settings['alpha_test_enabled'] = switches['HasOpacity']
        if switches.get('HasEmissive') is False:
            settings['emissive_enabled'] = False
    color = surface_color_settings(parameters)
    if color:
        settings['surface_color'] = color
        settings['color_review'] = (
            'Source RGB tint applied once to the recovered base texture in linear space. '
            'Brightness is not applied: direct multiplication made rune essence too pale in regression comparison. '
            'Tint alpha is not treated as opacity. Cooked brightness math/order, AO, desaturation, '
            'ageing, wetness and in-game lighting need manual comparison.')
    if master in STANDARD_COLOR_MASTERS and parameters.get('scalars', {}).get('Brightness', 1) != 1:
        settings['manual_review'] = (
            f"Source Brightness={parameters['scalars']['Brightness']} is unapplied. "
            'Its cooked operation is not recovered; compare with the game before enabling any brightness transform.')
    if master == 'M_Withered':
        settings.update(family='surface_maps', required_texture_roles=['base_color'],
                        approximation='Static RockFractures BCC map and Tint proxy; source roughness/metallic. '
                        'Fracture channel routing, world-space normals, presence/height masks, fresnel, '
                        'inner flow, outline emission, refraction and vertex animation remain unreconstructed.')
        settings['surface_pbr'] = {name: min(1, max(0, parameters.get('scalars', {}).get(source, default)))
                                   for name, source, default in [('roughness', 'Roughness', .7), ('metalness', 'Metallic', 0)]}
    if parameters.get('master') == VINES_MASTER:
        settings.update(family='surface_maps', required_texture_roles=list(VINES_TEXTURES), approximation=
                        'Named hard-wired KuldraVines BCC/NRM surface maps; packed masks, moving vein emission '
                        'and depth blending remain unreconstructed. Requires in-game comparison.')
    if master in ('M_Kuldra_Basic', 'M_BasicMasked'):
        settings.update(family='surface_maps', required_texture_roles=['base_color', 'normal'], approximation=
                        'Named BCR/NM or BCA/NR surface maps with source alpha cutoff; packed roughness, '
                        'normal packing, tint gradients, parallax and custom UVs remain unverified.')
    if master in CRYSTAL_MASTERS:
        settings.update(family='surface_maps', base_color_parameter='Base',
                        required_texture_roles=['normal'], approximation=
                        'Source Base RGB, Crystal Normal and scalar Roughness/Metalic form a static base-only proxy. '
                        'Base Var, Concave/Convex masks, gradient, emissive, subsurface, parallax and fade '
                        'are NOT reconstructed. The green base is source-derived, not visual approval. '
                        'Compare color distribution, bright tips/glow and fade in-game before acceptance.')
        settings['surface_pbr'] = {name:min(1, max(0, parameters.get('scalars', {}).get(source, default)))
                                   for name,source,default in [('roughness','Roughness',.4),('metalness','Metalic',.4)]}
    if master == 'M_Standard_MR_Notex':
        settings.update(family='surface_maps', base_color_parameter='BaseColor', approximation=
                        'Source constant BaseColor and Roughness/Metallic, not a missing diffuse map. '
                        'Glint, wetness, exposure, specular and emission remain unverified; source alpha is not opacity.')
        settings['surface_pbr'] = {name:min(1, max(0, parameters.get('scalars', {}).get(source, default)))
                                   for name,source,default in [('roughness','Roughness',.9),('metalness','Metallic',0)]}
    if master == 'M_StainedGlassOpaque':
        settings.update(family='surface_maps', required_texture_roles=['base_color','normal'], approximation=
                        'Source opaque stained-glass BC/N maps and sidedness; packed roughness, subsurface '
                        'lighting and stains remain unverified. This source is opaque, not transmissive glass.')
    if parameters.get('master') == SLIME_MASTER:
        settings.update(family='surface_maps', required_texture_roles=list(SLIME_TEXTURES), approximation=
                        'Named hard-wired Heart Base BCC/NRM surface maps. Slime subsurface and emissive '
                        'shader math remain unreconstructed; compare the heart base in-game.')
    if master == 'M_KuldraTunnel':
        settings['manual_review'] = (
            'Ground_BCR/Ground_NMM are only one branch of this floor/wall shader. Wall color atlas, '
            'segment UV mapping, direction masks and animated veins remain unreconstructed. '
            'Do not apply the ground texture to the whole tunnel as a claimed fix; compare in-game.')
    if master in WATER_MASTERS:
        settings.update(family="water", water=water_settings(parameters), approximation=
                        "Static source normal layers and roughness; deep-water scattering color proxy or enabled flat color. "
                        "Linear source-distance shoreline fade (PDO surrogate), river shallow/deep blend. "
                        "No refraction, flow animation, interaction foam, caustics, cloudiness, oil, duckweed or souls shader reconstruction")
    if is_foam(parameters):
        settings.update(family="foam", foam=foam_settings(parameters), approximation=
                        "Static TexA mask, channel weights, tiling, opacity and enabled constant color; depth fade. "
                        "Particle data, UV distortion, radial/shape masks, day/night and animation remain unverified")
    if master in ("M_UMS_Dunes", "M_UMS_Dunes_Cheap"):
        settings['manual_review'] = (
            'Terrain atlas color is a static RVT proxy. Outside its coverage the source SandColourA '
            'fallback is very pale; this is not a failed diffuse download. Isolated audit views measure '
            'the visible atlas/fallback fraction. Check the same placement in-world: missing deformation '
            'can expose large skirts that Unreal reshapes. No arbitrary darkening or terrain flattening is applied.')
        settings["deformation_review"] = {
            "status": "needs_game_reference_and_shader_graph",
            "source_functions": [ref for ref in parameters.get("referenced_functions", [])
                                 if any(word in ref for word in ("RVT", "Sand", "Height"))],
            "parameters": {key:value for key,value in parameters.get("scalars", {}).items()
                           if any(word in key for word in ("GroundVertex", "Flattening"))},
            "reason": "Cooked function metadata has no expression graph; RVT height, vertex mask semantics and camera-dependent deformation are not reconstructed. Geometry is not guessed or flattened."}
    return settings


def is_foam(parameters):
    return (parameters.get("master", "").endswith("/M_VFX_Master_TransSimple.0")
            and "foam" in parameters.get("source", "").rsplit("/", 1)[-1].lower()
            and "TexA" in parameters.get("textures", {}))


def foam_settings(parameters):
    s, v, switches = (parameters.get(key, {}) for key in ("scalars", "vectors", "switches"))
    def rgba(name, default):
        return [v.get(name, {}).get(c, d) for c,d in zip("RGBA", default)]
    return {"opacity":min(1, max(0, s.get("OpacityScale", 1)*s.get("OpacityFactor", 1))),
            "uv_scale":rgba("TexScalePan", [1,1,0,0])[:2],
            "mask_mult":rgba("OpacityTexMult", [1,0,0,0]),
            "mask_sub":rgba("OpacityTexSub", [0,0,0,0]),
            "mask_gain":s.get("MaskMult", 1), "mask_bias":s.get("MaskSub", 0),
            "rgb_mask":switches.get("UseRGBOrAlpha?", True),
            "color":rgba("BaseColour", [1,1,1,1])[:3] if switches.get("UseBaseColourParameter") else [1,1,1],
            "depth_fade_m":max(0, s.get("DepthFade", 0))/100 if switches.get("UseDepthFade") else 0}


def water_settings(parameters):
    scalars, vectors = parameters.get('scalars', {}), parameters.get('vectors', {})
    switches = parameters.get('switches', {})
    master = parameters.get('master', '').rsplit('/', 1)[-1].split('.')[0]
    def rgb(name, default=0):
        return [max(0, vectors.get(name, {}).get(channel, default)) for channel in 'RGB']
    flat = switches.get('UseFlatBaseColour?', False)
    if flat:
        color, method = rgb('BaseColour'), 'enabled_flat_color'
    elif master == 'M_Water_River':
        color = [a*b for a,b in zip(rgb('DeepWaterColor'), rgb('Extra Tint', 1))]
        method = 'deep_river_color_without_depth_blend'
    else:
        scatter, absorb = rgb('ScatteringCoefficientBase'), rgb('AbsorptionCoefficientBase')
        color = [s/max(s+a, 1e-8) for s,a in zip(scatter, absorb)]
        method = 'scattering_albedo_proxy_without_depth_or_phase'
    river = master == 'M_Water_River'
    single_layer = 'SingleLayerWater' in parameters.get('properties', {}).get('ShadingModel', '') or master == 'M_Water'
    depth = None
    if river and scalars.get('Depth_Distance', 0) > 0 and scalars.get('ColorDepth_Distance', 0) > 0:
        depth = {'mode':'river', 'fade_m':scalars['Depth_Distance']/100,
                 'color_m':scalars['ColorDepth_Distance']/100,
                 'shallow_color':[a*b for a,b in zip(rgb('ShalowWaterColor'), rgb('Extra Tint', 1))],
                 'shallow_opacity':min(1, max(0, scalars.get('Opacity_Shallow', 0)))}
    elif switches.get('UsePDO?') and scalars.get('PDOBlend', 0) > 0:
        depth = {'mode':'shoreline_proxy', 'fade_m':scalars['PDOBlend']/100}
    return {'color':color, 'color_method':method,
            'depth':depth,
            'roughness':min(1, max(0, scalars.get('Roughness', .1))),
            # SingleLayerWater opacity controls surface/volume mixing, not alpha.
            'opacity':1 if single_layer else min(1, max(0, scalars.get('Opacity_Deep' if river else 'Opacity', 1))),
            'emission':max(0, scalars.get('OverallEmissiveStrength', 0)) if flat and switches.get('UseBaseAsEmissive') else 0,
            'uv_mode':'mesh' if river else 'world',
            'normal_strengths':[max(0, scalars.get(name, .3)) for name in
                                (('Normal1_Strength','Normal2_Strength') if river else ('Normal1Strength','Normal2Strength'))],
            'normal_scales_m':[max(.01, abs(scalars.get(name, 400))/100) for name in ('NormalScaleA','NormalScaleB')],
            'normal_uv_scales':[[scalars.get(f'Normal{i}_UTiling', 1), scalars.get(f'Normal{i}_VTiling', 1)] for i in (1,2)],
            'unimplemented_switches':[key for key,value in switches.items() if value and key not in ('UseFlatBaseColour?','UseBaseAsEmissive')]}


def texture_roles(parameters):
    textures = parameters["textures"]
    master = parameters.get("master", "").rsplit("/", 1)[-1].split(".")[0]
    out = {}
    for key, source in textures.items():
        k = key.lower().replace(" ", "").replace("_", "")
        leaf = source.rsplit("/", 1)[-1].lower()
        if k in ("basecolor", "basecolour", "diffuse", "albedo", "color", "colour", "colortexture", "basetexture", "basecolortexture"):
            out.setdefault("base_color", source)
        elif k in ("normal", "normalmap", "normaltexture"):
            out.setdefault("normal", source)
    # Explicit surface parameters outrank incidental overlay texture filenames.
    for key, source in textures.items():
        k = key.lower()
        leaf = source.rsplit("/", 1)[-1].lower()
        if leaf.rsplit(".", 1)[0].endswith(("_bc", "_d", "_albedo")) and not any(word in k for word in ("noise", "moss")):
            out.setdefault("base_color", source)
    for key in ("Rock BC (A=roughness)", "Rock Base Colour", "Rock BaseColor"):
        if key in textures:
            out["base_color"] = textures[key]
            break
    for key in ("Rock Normal (A=height)", "Rock Normal"):
        if key in textures:
            out["normal"] = textures[key]
            break
    if render_settings(parameters).get("emissive_enabled") is True:
        source = textures.get("EmissiveMap") or textures.get("Emissive")
        if source and not source.startswith("Engine/Content/EngineResources/DefaultTexture."):
            out["emissive"] = source
    if parameters.get("master") == GEYSER_MASTER:
        # This master's three hard-wired maps also match the parameterized
        # MI_Rune_Essence_01 sibling. Do not guess arbitrary shader connections.
        for role, name in GEYSER_TEXTURES.items():
            matches = [ref for ref in parameters.get("referenced_textures", [])
                       if ref.rsplit("/", 1)[-1].rsplit(".", 1)[0] == name]
            if len(matches) == 1:
                out[role] = matches[0]
    if parameters.get('master') == SLIME_MASTER:
        for role, name in SLIME_TEXTURES.items():
            matches = [ref for ref in parameters.get('referenced_textures', [])
                       if ref.rsplit('/', 1)[-1].split('.')[0] == name]
            if len(matches) == 1:
                out[role] = matches[0]
    if master in CRYSTAL_MASTERS:
        # The packed Crystal Mask is not RGB albedo, opacity or an emissive map.
        out = {'normal':textures['Crystal Normal']} if textures.get('Crystal Normal') else {}
    if master == 'M_StainedGlassOpaque':
        for role, name in (('base_color', 'BC'), ('normal', 'N')):
            if textures.get(name):
                out[role] = textures[name]
    if parameters.get("master", "").endswith("/M_Cave_Landscape.0"):
        for layer in range(1, 4):
            if f"L{layer}_D" in textures:
                out[f"cave_layer_{layer}"] = textures[f"L{layer}_D"]
    if master.startswith('M_Standard_Env_MR') and parameters.get('switches', {}).get('Is VT') is True:
        normal = 'Virtual Normal Map' if parameters.get('switches', {}).get('Use new Packing') else 'Virtual Normal'
        for role, name in (('base_color', 'Virtual BaseColor'), ('normal', normal)):
            if textures.get(name):
                out[role] = textures[name]
    if master == 'M_Decal' and parameters.get('switches', {}).get('Use R as Alpha') is False:
        for role, name in (('base_color', 'BCO'), ('normal', 'NR')):
            if textures.get(name):
                out[role] = textures[name]
    if master == 'M_Master':
        for role, name in (('base_color', 'Albedo00'), ('normal', 'Normal00')):
            if textures.get(name):
                out[role] = textures[name]
    if master == 'M_Character_Standard':
        virtual = parameters.get('switches', {}).get('UseVT') is True
        for role, name in (('base_color', 'BaseColor_VT' if virtual else 'BaseColor Map'),
                           ('normal', 'Normal_VT' if virtual else 'Normal')):
            if textures.get(name):
                out[role] = textures[name]
    if master == 'M_Withered' and textures.get('RockFractures'):
        # This is the master's named BCC surface input, not its flow/noise maps.
        # The dynamic fracture/height blend stays explicitly marked as a proxy.
        out['base_color'] = textures['RockFractures']
    if parameters.get('master') == VINES_MASTER:
        for role, name in VINES_TEXTURES.items():
            matches = [ref for ref in parameters.get('referenced_textures', [])
                       if ref.rsplit('/', 1)[-1].split('.')[0] == name]
            if len(matches) == 1:
                out[role] = matches[0]
    if master in ('M_Kuldra_Basic', 'M_BasicMasked'):
        names = ('BCR', 'NM') if master == 'M_Kuldra_Basic' else ('BCA', 'NR')
        for role, name in zip(('base_color', 'normal'), names):
            if textures.get(name):
                out[role] = textures[name]
    if master in ("M_dreamstone", "M_Basic_VertPaint"):
        names = ("BC", "N") if master == "M_dreamstone" else ("Vert Base BC", "Vert Base NRM")
        for role, name in zip(("base_color", "normal"), names):
            if name in textures:
                out[role] = textures[name]
    if master in WATER_MASTERS:
        # Duckweed is an optional overlay, not the water's diffuse surface.
        out.pop("base_color", None)
        for role, name in (("normal", "Normal1_map"), ("water_normal_2", "Normal2_map")):
            if name in textures:
                out[role] = textures[name]
    if is_foam(parameters):
        out = {"foam_mask":textures["TexA"]}
    if master == "M_ShellFur" and parameters.get("switches", {}).get("UseTexture?") is False:
        out.pop("base_color", None)
    if master == "M_NPC_Ghost":
        # MeshOpacityVT and effect masks are not a base-color branch.
        out.pop("base_color", None)
        if parameters.get("switches", {}).get("UseBaseColour") is True and textures.get("BaseColor Map"):
            out["base_color"] = textures["BaseColor Map"]
        out.pop("normal", None)
        if parameters.get("switches", {}).get("UseNormal") is True:
            name = "NormalVT" if parameters.get("switches", {}).get("UseVT") else "Normal"
            if textures.get(name):
                out["normal"] = textures[name]
    if master == "M_SimpleGlass" and parameters.get("switches", {}).get("UseNormal") is False:
        out.pop("normal", None)
    if master == "M_VFX_SW_AnimaDoor_Air_Persist":
        out.pop("base_color", None)
    if master == "M_FlattenMaterial_Mechanised" and parameters.get("switches", {}).get("UseBaseColor") is False:
        out.pop("base_color", None)
    if master == "M_Standard_Env_MR_BackgroundMountains" and out.get("base_color", "").endswith("/T_Default_White_D.0"):
        out.pop("base_color", None)
    return out


def color_texture_srgb(parameters, resolver):
    refs = {ref for role, ref in parameters['roles'].items()
            if role in ('base_color', 'emissive') or role.startswith('cave_layer_')}
    return {ref:bool(resolver.get(ref).get('Properties', {}).get('SRGB', True)) for ref in refs}


def scene_materials(plan, resolver):
    out = {}
    for asset in plan["assets"]:
        mesh = resolver.get(asset["source"])
        defaults = [ref_id(row.get("MaterialInterface")) for row in mesh.get("Properties", {}).get("StaticMaterials", [])]
        asset["default_materials"] = defaults
        lods = mesh.get("RenderData", {}).get("LODs", [])
        asset["section_material_slots"] = [section["MaterialIndex"] for section in lods[0]["Sections"]
                                           if section["NumTriangles"] > 0] if lods else []
        used = []
        for slot in asset["section_material_slots"] or range(len(defaults)):
            override = asset["materials"][slot] if slot < len(asset["materials"]) else ""
            used.append(override or (defaults[slot] if slot < len(defaults) else ""))
        for source in sorted(set(used)):
            if source and source not in out:
                params = material_parameters(resolver, source)
                params["roles"] = texture_roles(params)
                out[source] = params
    for tile in plan["terrain"]:
        source = tile["material"]
        if source not in out:
            out[source] = material_parameters(resolver, source)
            out[source]["roles"] = texture_roles(out[source])
    for params in out.values():
        # Cooked hard-wired plugin references can omit the export index; the
        # native recovery contract requires the canonical version-bound identity.
        params['roles'] = {role:resolver.canonical(ref) for role, ref in params['roles'].items()}
        params['texture_srgb'] = color_texture_srgb(params, resolver)
    return out
