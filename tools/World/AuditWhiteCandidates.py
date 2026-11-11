"""Close a baseline review queue without confusing source inspection with visual approval."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from world_source import Resolver, read_json, write_json
from world_material_audit import surface_role_findings


FAMILY_REVIEW = {
    "M_Standard_Env_MR": "Compare the bound diffuse image/tint against game lighting; pale cloth, bones, paper and sand are not automatically missing textures. Packed surface channels, wetness and brightness math remain unverified.",
    "M_Standard_Env_MR_Ageing": "Base image is checked independently of ageing; moss, mould, vertex masks, packed channels and wetness still require game comparison.",
    "M_DR_Highlight_Ageing": "Named base/normal and source Colour Multiplier proxy; ageing, highlight, debug-interior branch and gradients need comparison.",
    "M_Character_Standard": "Check active regular/virtual maps and disabled emission. Dye, custom primitive data, packed surface channels and live character shading are not reconstructed.",
    "M_ShellFur": "UseTexture=false forbids the inherited Ram diffuse. MixColor-only proxy is not the shell/overlay/AO/thickness shader; compare fur detail and brightness.",
    "M_UMS_Dunes": "Baked atlas and source SandColourA-derived fallback are proxies. Inspect per-placement footprint coverage; no source shader graph establishes RVT height, vertex-mask semantics or skirt flattening.",
    "M_Tool_ProcGrid": "Source checker colors, not missing diffuse. First-color/roughness proxy omits checker lines and object alignment; confirm whether this proxy geometry is visible in-game.",
    "M_LevelDesignMarker": "Source Color is white. Helper marker visibility and unlit/emissive behavior need in-game verification; do not recolor to remove a flag.",
    "BasicShapeMaterial": "Source Color is white; this is basic/helper geometry. Confirm source actor visibility, not a replacement texture.",
    "M_Cube": "No exposed color texture or color parameter. Source constant shader expressions are cooked out; compare helper/fog mesh visibility before inventing a material.",
    "WorldGridMaterial": "Engine debug material, not an absent asset diffuse. Checker expression graph is unavailable; confirm helper visibility in-game.",
    "unassigned": "Both source slot and component override are null. No source material exists to recover; need game appearance and actor/runtime material assignment evidence.",
    "M_Standard_MR_Notex": "Source constant BaseColor/PBR; no diffuse texture is expected. Verify wetness, exposure and final brightness.",
    "M_NPC_Ghost": "Disabled base/normal branches are enforced; opacity textures are not diffuse. Color A-only proxy still lacks caustics, rim emission, masks, displacement and fades.",
    "M_Flowmap_Main_nosky": "SkyTexture and Sky are separate inputs, not interchangeable diffuse. Flow, alpha, noise, day/night lighting and emissive composition need the graph or game reference.",
    "M_Shader_SimpleTranslucent": "Engine translucent helper with reflection/opacity inputs. Do not infer invisibility from its name; verify opacity hookups and actual game visibility.",
    "M_UniversalEye_P": "Eye shading includes specialized iris/refraction inputs; source image presence alone does not validate UVs, pupil, wetness or transmission.",
    "M_KuldraTunnel": "Ground_BCR/NMM, basic/directional wall BCR/NMM and a color-curve atlas exist. Missing floor/wall mask, segment UV routing and curve lookup prevent a verified combined shader. Do not cover all walls with the ground map.",
    "M_AnimaCloudLayer": "Source Opacity is used only as a static scalar proxy. Color curves, two cloud layers, erosion, center mask, WPO and motion remain unresolved.",
    "M_AnimaEnergy_01": "Source Opacity proxy only. Tendril masks, UV distortion, curve gradient, center mask, spin and emission remain unresolved.",
    "M_AnimaEnergy_02": "Source Opacity proxy only. Basin mask combination, curve IDs, caustics, color fade and emission remain unresolved.",
    "M_Disabled": "No exposed surface input. Name alone does not prove visibility; cooked opacity-mask expressions/runtime assignment require evidence before suppressing geometry.",
    "M_Decal_Roads": "RVT/decal shader has no exposed color inputs here. Verify actor VirtualTextureRenderPassType and terrain-only contribution before treating this as an ordinary mesh surface.",
    "M_SimpleGlass": "Source TintGlass/minimum-opacity proxy and disabled normal respected. Fresnel tint/opacity, reflection and refraction remain manual checks.",
    "M_VFX_SW_AnimaDoor_Air_Persist": "Edge smoke is not diffuse. BaseColour-only proxy omits layered masks, opacity, refraction, spawn state and day/night behavior.",
    "M_VFX_VaultFinalDoor": "Scene-depth/raymarch distortion shader, not an ordinary diffuse surface. OpacityMaskController alone does not establish visibility; game/reference shader evidence required.",
    "M_VFX_SpectralPath_Cobbles": "Source ColourA-only proxy; path mask, Wither fade, depth culling, emission and animation need comparison.",
}


def texture_evidence(root, descriptor):
    evidence = {}
    for role, relative in descriptor.get("textures", {}).items():
        path = root / relative
        row = {"path": relative, "exists": path.is_file()}
        if row["exists"]:
            row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with Image.open(path) as image:
                row["size"] = list(image.size)
                # Source texture samples, not a claim about rendered pixel coverage.
                image = image.convert("RGBA")
                image.thumbnail((64, 64))
                pixels = np.asarray(image)
                rgb = pixels[:, :, :3][pixels[:, :, 3] > 200]
                row["opaque_samples"] = len(rgb)
                if len(rgb):
                    row["encoded_rgb_mean"] = rgb.mean(axis=0).tolist()
                    row["near_white_fraction"] = float((rgb.min(axis=1) > 235).mean())
        evidence[role] = row
    return evidence


def source_evidence(resolver, ref):
    try:
        identity = resolver.canonical(ref)
        obj = resolver.get(identity)
        package = resolver.package(identity.rsplit(".", 1)[0])
        path = resolver.root / (identity.rsplit(".", 1)[0] + ".json")
        return {"source": identity, "type": obj.get("Type"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "expression_exports": sum(o.get("Type", "").startswith("MaterialExpression") for o in package),
                "size": [obj.get("SizeX"), obj.get("SizeY")],
                "gradient_curves": obj.get("Properties", {}).get("GradientCurves", [])}
    except (FileNotFoundError, ValueError, IndexError) as error:
        return {"source": ref, "unavailable": str(error)}


def material_review(ref, sources, descriptors, resolver, root, cache):
    p, desc = sources.get(ref, {}), descriptors.get(ref, {})
    master = p.get("master", "").rsplit("/", 1)[-1].split(".")[0] or "unassigned"
    def source(value):
        if value not in cache:
            cache[value] = source_evidence(resolver, value)
        return cache[value]
    dependencies = sorted(set(p.get("textures", {}).values()) | set(p.get("referenced_textures", [])) |
                          set(p.get("referenced_functions", [])))
    return {"material": ref, "master": master, "family_reviewed": master in FAMILY_REVIEW,
            "research_status": "source_inspected" if master in FAMILY_REVIEW else "unclassified",
            "manual_verification": FAMILY_REVIEW.get(master, "New shader family requires source inspection."),
            "master_evidence": source(p["master"]) if p.get("master") else None,
            "source_textures": p.get("textures", {}), "roles": p.get("roles", {}),
            "source_switches": p.get("switches", {}), "source_vectors": p.get("vectors", {}),
            "source_scalars": p.get("scalars", {}), "settings": desc.get("settings", {}),
            "role_findings": surface_role_findings(p), "texture_gaps": desc.get("texture_gaps", []),
            "bound_textures": texture_evidence(root, desc), "dependencies": [source(r) for r in dependencies],
            "visual_status": "manual_review_pending"}


def build_review(baseline, current, materials):
    rows = {r["asset"]: r for r in current["variants"]}
    variants, missing = [], []
    for before in baseline["variants"]:
        row = rows.get(before["asset"])
        if row is None:
            missing.append(before["asset"])
            continue
        refs = list(dict.fromkeys(row["materials"]))
        variants.append({"asset": row["asset"], "source": row["source"], "placements": row["placements"],
                         "flags_before": before["flags"], "flags_after": row.get("flags", []),
                         "errors": row["errors"], "materials": refs, "section": row.get("section"),
                         "bookmark": row["bookmark"], "research_status": "source_inspected",
                         "visual_status": "manual_review_pending"})
    unclassified = [ref for ref, row in materials.items() if not row["family_reviewed"]]
    gaps = [ref for ref, row in materials.items() if any(f[1] == "conversion_gap" for f in row["role_findings"])
            or any(not t["exists"] for t in row["bound_textures"].values())]
    errors = [r["asset"] for r in variants if r["errors"]]
    return {"schema": "RSDW.WorldSourceCompletion.v1", "fingerprint": current["fingerprint"],
            "scene_sha256": current["scene_sha256"],
            "status": "source_review_complete_manual_verification_pending" if not (missing or unclassified or gaps or errors) else "incomplete",
            "scope": "Every candidate in the supplied baseline, including candidates that cleared pixel flags. Source inspection is not shader or in-game visual approval.",
            "summary": {"expected": len(baseline["variants"]), "inspected": len(variants), "materials": len(materials),
                        "families": len({m["master"] for m in materials.values()}), "missing_candidates": missing,
                        "unclassified_materials": unclassified, "confirmed_mapping_gaps": gaps, "render_errors": errors},
            "variants": variants, "materials": materials}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="Preserved WhiteMaterialReview.json")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--annotate-gallery", action="store_true", help="Attach source review notes to the generated audit JSON")
    args = parser.parse_args()
    baseline, current = read_json(args.baseline), read_json(args.audit)
    index = read_json(args.root / "VisualAuditIndex.json")
    if current.get("status") != "completed_pending_manual_review" or len(current["variants"]) != len(index["variants"]):
        raise ValueError("Source completion requires a completed full render audit, not a partial/failed run")
    if index["scene_sha256"] != current["scene_sha256"]:
        raise ValueError("Finish the current render audit before source completion")
    sources, shared = read_json(args.root / "MaterialSources.json"), read_json(args.root / "WorldShared.json")
    refs = {m["material"] for r in baseline["variants"] for m in r["review"]["materials"]}
    resolver, cache = Resolver(args.archive_root), {}
    materials = {ref: material_review(ref, sources, shared["materials"], resolver, args.root, cache) for ref in sorted(refs)}
    report = build_review(baseline, current, materials)
    write_json(args.out, report)
    print(report["status"], report["summary"], flush=True)
    if report["status"] == "incomplete":
        raise SystemExit(1)
    if args.annotate_gallery:
        inspected = {r["asset"]: r for r in report["variants"]}
        for row in current["variants"]:
            if row["asset"] in inspected:
                row["source_review"] = {"research_status": "source_inspected", "visual_status": "manual_review_pending",
                    "baseline_flags": inspected[row["asset"]]["flags_before"],
                    "materials": [{"material": ref, "note": materials[ref]["manual_verification"],
                                   "bound_roles": list(materials[ref]["bound_textures"]),
                                   "expression_exports": (materials[ref]["master_evidence"] or {}).get("expression_exports")}
                                  for ref in inspected[row["asset"]]["materials"]]}
        current["source_review_provenance"] = {"scene_sha256": report["scene_sha256"], "summary": report["summary"],
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        write_json(args.audit, current)


if __name__ == "__main__":
    main()
