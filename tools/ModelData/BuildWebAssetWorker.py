"""
Blender-side worker for shared website assets.

Imports one .uemodel, reuses the existing material reconstruction code, loads
pre-optimized shared WebP textures, then exports model.gltf + model.bin with
Draco mesh compression.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy  # type: ignore


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import BuildGLBWorker as base  # noqa: E402
from WebTextureRules import is_web_texture_candidate  # noqa: E402


def _parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    return parser.parse_args(argv)


def _emit_result(obj: dict) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    sys.stdout.write("RESULT:" + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _norm_key(value: str) -> str:
    return str(value).replace(" ", "").replace("_", "").lower()


def _first_texture(textures: dict, *names: str) -> str | None:
    normalized = {_norm_key(key): value for key, value in textures.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_scalar(parameters: dict, *names: str) -> float | None:
    scalars = (parameters.get("Scalars") or {}) if isinstance(parameters, dict) else {}
    normalized = {_norm_key(key): value for key, value in scalars.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _first_color(parameters: dict, *names: str) -> tuple[float, float, float, float] | None:
    colors = (parameters.get("Colors") or {}) if isinstance(parameters, dict) else {}
    normalized = {_norm_key(key): value for key, value in colors.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if not isinstance(value, dict):
            continue
        try:
            return (
                float(value.get("R", 1.0)),
                float(value.get("G", 1.0)),
                float(value.get("B", 1.0)),
                float(value.get("A", 1.0)),
            )
        except (TypeError, ValueError):
            return None
    return None


def _source_rel(source_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(source_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _resolve_source_texture(source_root: Path, package_path: str | None) -> tuple[Path, str] | None:
    if not package_path:
        return None
    path = base._resolve_texture_on_disk(source_root, package_path)
    if path is None or not Path(path).is_file():
        return None
    path = Path(path)
    return path, _source_rel(source_root, path)


def _hash_generated_texture(
    *,
    source_root: Path,
    label: str,
    sources: list[Path],
    params: dict,
    texture_size: int,
    texture_quality: int,
) -> str:
    parts = ["material-v6", label, str(texture_size), str(texture_quality), json.dumps(params, sort_keys=True)]
    for path in sources:
        stat = path.stat()
        parts.extend([_source_rel(source_root, path), str(stat.st_size), str(stat.st_mtime_ns)])
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:24]


def _generated_texture_path(output_root: Path, texture_size: int, texture_hash: str) -> Path:
    return output_root / "textures" / f"webp_{texture_size}" / f"{texture_hash}.webp"


def _resize_max(img, max_size: int):
    if max(img.size) <= max_size:
        return img.copy()
    scale = max_size / max(img.size)
    size = (max(1, round(img.size[0] * scale)), max(1, round(img.size[1] * scale)))
    return img.resize(size, resample=1)


def _apply_rgb_adjust(img, *, tint: tuple[float, float, float, float] | None, brightness: float | None):
    img = img.convert("RGBA")
    factors = [1.0, 1.0, 1.0]
    if tint is not None:
        factors = [max(0.0, tint[0]), max(0.0, tint[1]), max(0.0, tint[2])]
    if brightness is not None:
        factors = [factor * max(0.0, brightness) for factor in factors]
    channels = img.split()
    adjusted = [
        channels[idx].point(lambda px, factor=factors[idx]: max(0, min(255, round(px * factor))))
        for idx in range(3)
    ]
    return __import__("PIL.Image", fromlist=["Image"]).merge("RGBA", (*adjusted, channels[3]))


def _shift_hue(img, amount: float | None):
    if amount is None:
        return img
    shift = int((amount % 1.0) * 255)
    if shift == 0:
        return img
    alpha = img.getchannel("A")
    hsv = img.convert("RGB").convert("HSV")
    h, s, v = hsv.split()
    h = h.point(lambda px: (px + shift) % 255)
    rgb = __import__("PIL.Image", fromlist=["Image"]).merge("HSV", (h, s, v)).convert("RGB")
    rgb.putalpha(alpha)
    return rgb


def _save_generated_webp(img, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    img.save(tmp, format="WEBP", quality=quality, method=4)
    os.replace(tmp, path)


def _record_generated_texture(
    *,
    output_root: Path,
    records: dict[str, dict],
    source_label: str,
    out_abs: Path,
    texture_hash: str,
    source_rels: list[str],
    generated_type: str,
) -> dict:
    rel = _rel(output_root, out_abs)
    try:
        from PIL import Image
        with Image.open(out_abs) as img:
            width, height = img.size
            has_alpha = "A" in img.getbands() or "a" in img.getbands()
    except Exception:
        width = height = None
        has_alpha = None
    rec = {
        "source": source_label,
        "source_textures": source_rels,
        "optimized": rel,
        "optimized_bytes": out_abs.stat().st_size if out_abs.is_file() else 0,
        "hash": texture_hash,
        "status": "generated",
        "generated_type": generated_type,
        "optimized_width": width,
        "optimized_height": height,
        "has_alpha": has_alpha,
        "error": None,
    }
    records[rel] = rec
    return rec


def _best_role_package(textures: dict, parameters: dict, role: str) -> str | None:
    best: tuple[int, int, str] | None = None
    for index, (slot_name, pkg_value) in enumerate(textures.items()):
        if not isinstance(pkg_value, str) or not pkg_value.strip():
            continue
        if base._role_for_texture_slot(str(slot_name)) != role:
            continue
        if base._is_default_texture_for_role(pkg_value, role):
            continue
        score = base._texture_candidate_score(str(slot_name), role, pkg_value, parameters)
        if best is None or (score, -index) > (best[0], -best[1]):
            best = (score, index, pkg_value)
    return best[2] if best is not None else None


def _build_eye_mask(size: tuple[int, int], border: float, bleed: float, mask_width: float):
    from PIL import Image

    width, height = size
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    radius = max(1.0, min(width, height) / 2.0)
    fade = max(0.03, min(0.5, float(border or 0.0) + float(bleed or 0.0) + float(mask_width or 0.0)))
    inner = 1.0 - fade
    pixels = []
    for y in range(height):
        for x in range(width):
            dist = (((x - cx) ** 2 + ((y - cy) ** 2)) ** 0.5) / radius
            if dist <= inner:
                alpha = 255
            elif dist <= 1.0:
                alpha = round(255 * (1.0 - dist) / fade)
            else:
                alpha = 0
            pixels.append(max(0, min(255, alpha)))
    mask = Image.new("L", size, 0)
    mask.putdata(pixels)
    return mask


def _generate_eye_composite(
    *,
    mi_json_abs: Path,
    textures: dict,
    parameters: dict,
    source_root: Path,
    output_root: Path,
    texture_size: int,
    texture_quality: int,
    generated_records: dict[str, dict],
) -> dict | None:
    iris_pkg = _first_texture(textures, "IrisColor")
    sclera_pkg = _first_texture(textures, "ScleraColor")
    iris_resolved = _resolve_source_texture(source_root, iris_pkg)
    sclera_resolved = _resolve_source_texture(source_root, sclera_pkg)
    if iris_resolved is None or sclera_resolved is None:
        return None

    iris_abs, iris_rel = iris_resolved
    sclera_abs, sclera_rel = sclera_resolved
    params = {
        "iris_radius": _first_scalar(parameters, "Iris UV Radius") or 0.15,
        "iris_brightness": _first_scalar(parameters, "IrisBrightness") or 1.0,
        "sclera_brightness": _first_scalar(parameters, "ScleraBrightness") or 1.0,
        "iris_hue_shift": _first_scalar(parameters, "IrisHueShift") or 0.0,
        "iris_border": _first_scalar(parameters, "IrisBorderWidth") or 0.024,
        "iris_bleed": _first_scalar(parameters, "IrisBleedWidth") or 0.035,
        "iris_mask": _first_scalar(parameters, "IrisMaskWidth") or 0.045,
        "pupil_scale": _first_scalar(parameters, "PupilScale") or 1.0,
        "iris_tint": _first_color(parameters, "IrisHueTint"),
        "sclera_tint": _first_color(parameters, "ScleraTint"),
    }
    texture_hash = _hash_generated_texture(
        source_root=source_root,
        label=f"eye:{_source_rel(source_root, mi_json_abs)}",
        sources=[iris_abs, sclera_abs],
        params=params,
        texture_size=texture_size,
        texture_quality=texture_quality,
    )
    out_abs = _generated_texture_path(output_root, texture_size, texture_hash)
    if not out_abs.is_file():
        from PIL import Image, ImageDraw, ImageOps

        with Image.open(sclera_abs) as img:
            base_img = _resize_max(img.convert("RGBA"), texture_size)
        base_img = _apply_rgb_adjust(
            base_img,
            tint=params["sclera_tint"],
            brightness=params["sclera_brightness"],
        )

        diameter = max(4, round(min(base_img.size) * float(params["iris_radius"]) * 2.0))
        with Image.open(iris_abs) as img:
            iris_img = ImageOps.fit(img.convert("RGBA"), (diameter, diameter), method=Image.Resampling.LANCZOS)
        iris_img = _shift_hue(iris_img, params["iris_hue_shift"])
        iris_img = _apply_rgb_adjust(
            iris_img,
            tint=params["iris_tint"],
            brightness=params["iris_brightness"],
        )
        mask = _build_eye_mask(
            iris_img.size,
            float(params["iris_border"]),
            float(params["iris_bleed"]),
            float(params["iris_mask"]),
        )
        iris_img.putalpha(mask)

        pupil_radius = max(1, round(diameter * 0.055 * max(0.2, float(params["pupil_scale"]))))
        pupil = Image.new("RGBA", iris_img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(pupil)
        cx = diameter // 2
        cy = diameter // 2
        draw.ellipse(
            (cx - pupil_radius, cy - pupil_radius, cx + pupil_radius, cy + pupil_radius),
            fill=(0, 0, 0, 160),
        )
        iris_img.alpha_composite(pupil)

        x = (base_img.size[0] - diameter) // 2
        y = (base_img.size[1] - diameter) // 2
        base_img.alpha_composite(iris_img, (x, y))
        _save_generated_webp(base_img, out_abs, texture_quality)

    source_label = f"generated:eye:{_source_rel(source_root, mi_json_abs)}"
    rec = _record_generated_texture(
        output_root=output_root,
        records=generated_records,
        source_label=source_label,
        out_abs=out_abs,
        texture_hash=texture_hash,
        source_rels=[iris_rel, sclera_rel],
        generated_type="eye_composite",
    )
    return {
        "role": "BaseColor",
        "path": out_abs,
        "source": source_label,
        "params": ["EyeComposite", "Metallic(default0)", "Roughness(eye0.5)"],
        "suppress_scalar_roles": ["Metallic", "Roughness"],
        "record": rec,
        "diagnostics": [
            {
                "code": "EyeCompositeGenerated",
                "sources": [iris_rel, sclera_rel],
                "optimized": rec["optimized"],
            }
        ],
    }


def _generate_dragon_basecolor(
    *,
    mi_json_abs: Path,
    textures: dict,
    parameters: dict,
    source_root: Path,
    output_root: Path,
    texture_size: int,
    texture_quality: int,
    generated_records: dict[str, dict],
) -> dict | None:
    if "dragon" not in mi_json_abs.as_posix().lower():
        return None
    has_character_shader_markers = any(
        _first_texture(textures, name)
        for name in ("CurveAtlas", "TintMask", "TintMask_VT", "EmissiveCurveAtlas")
    )
    if not has_character_shader_markers:
        return None
    pkg = _best_role_package(textures, parameters, "BaseColor")
    resolved = _resolve_source_texture(source_root, pkg)
    if resolved is None:
        return None
    base_abs, base_rel = resolved
    params = {
        "color_scale": _first_scalar(parameters, "Color_Scale") or 1.0,
        "lighting_correction": _first_scalar(parameters, "Lighting correction") or 1.0,
        "contrast": _first_scalar(parameters, "Contrast") or 1.0,
    }
    texture_hash = _hash_generated_texture(
        source_root=source_root,
        label=f"dragon-base:{_source_rel(source_root, mi_json_abs)}",
        sources=[base_abs],
        params=params,
        texture_size=texture_size,
        texture_quality=texture_quality,
    )
    out_abs = _generated_texture_path(output_root, texture_size, texture_hash)
    if not out_abs.is_file():
        from PIL import Image, ImageEnhance

        with Image.open(base_abs) as img:
            baked = _resize_max(img.convert("RGBA"), texture_size)
        brightness = max(0.0, float(params["color_scale"]) * float(params["lighting_correction"]))
        baked = _apply_rgb_adjust(baked, tint=None, brightness=brightness)
        if float(params["contrast"]) != 1.0:
            baked = ImageEnhance.Contrast(baked).enhance(max(0.0, float(params["contrast"])))
        _save_generated_webp(baked, out_abs, texture_quality)

    source_label = f"generated:dragon-basecolor:{_source_rel(source_root, mi_json_abs)}"
    rec = _record_generated_texture(
        output_root=output_root,
        records=generated_records,
        source_label=source_label,
        out_abs=out_abs,
        texture_hash=texture_hash,
        source_rels=[base_rel],
        generated_type="dragon_basecolor_bake",
    )
    return {
        "role": "BaseColor",
        "path": out_abs,
        "source": source_label,
        "params": ["DragonBaseColorBake"],
        "record": rec,
    }


def _dragon_diagnostics(textures: dict, parameters: dict) -> list[dict]:
    diagnostics: list[dict] = []
    for name in ("CurveAtlas", "EmissiveCurveAtlas", "TintMask", "TintMask_VT"):
        value = _first_texture(textures, name)
        if value:
            diagnostics.append({"code": f"Unsupported{name}", "source": value})
    for name in ("CurveID", "EmissiveCurveID", "SA_BaseRedness", "SA_BaseSaturation", "SA_BaseLightness"):
        value = _first_scalar(parameters, name)
        if value is not None:
            diagnostics.append({"code": f"Unsupported{name}", "value": value})
    return diagnostics


def _install_material_texture_provider(
    *,
    source_root: Path,
    generated_overrides: dict,
    generated_records: dict[str, dict],
) -> None:
    normalized_overrides = {
        str(key).replace("\\", "/"): value
        for key, value in (generated_overrides or {}).items()
        if isinstance(value, dict)
    }

    def _provider(*, mi_json_abs: Path, textures: dict, parameters: dict, source_root: Path) -> dict:
        result: dict = {"textures": {}, "diagnostics": []}
        rel_key = _source_rel(source_root, mi_json_abs).replace("\\", "/")
        override = (
            normalized_overrides.get(rel_key)
            or normalized_overrides.get(mi_json_abs.as_posix())
            or normalized_overrides.get(mi_json_abs.name)
        )
        if not isinstance(override, dict):
            return result

        for role, replacement in (override.get("textures") or {}).items():
            if not isinstance(replacement, dict):
                continue
            row = dict(replacement)
            if row.get("path"):
                row["path"] = Path(row["path"])
            result["textures"][role] = row
        result["diagnostics"].extend(
            diagnostic
            for diagnostic in (override.get("diagnostics") or [])
            if isinstance(diagnostic, dict)
        )
        for rec in override.get("records") or []:
            if not isinstance(rec, dict):
                continue
            optimized = rec.get("optimized")
            if isinstance(optimized, str) and optimized:
                generated_records[optimized] = rec
        return result

    base.MATERIAL_TEXTURE_PROVIDER = _provider


def _clean_asset_dir(asset_dir: Path) -> None:
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name in ("model.gltf", "model.bin"):
        try:
            (asset_dir / name).unlink(missing_ok=True)
        except Exception:
            pass
    # Remove stale image copies from older experiments. The intended output is
    # only model.gltf/model.bin; textures live in the shared cache.
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for path in asset_dir.glob(pattern):
            try:
                path.unlink()
            except Exception:
                pass


def _patch_image_loader(
    *,
    source_root: Path,
    output_root: Path,
    texture_map: dict[str, str],
    generated_textures: dict[str, dict],
    used_textures: list[dict],
    missing_textures: set[str],
) -> None:
    original_load_image = base._load_image
    normalized_map = {key.replace("\\", "/"): Path(value) for key, value in texture_map.items()}

    def _load_web_image(abs_path: Path, non_color: bool):
        try:
            output_rel = abs_path.resolve().relative_to(output_root.resolve()).as_posix()
        except ValueError:
            output_rel = None
        if output_rel is not None and output_rel.lower().endswith(".webp"):
            rec = generated_textures.get(output_rel) or {}
            image = original_load_image(abs_path, non_color)
            if image is not None:
                used_textures.append(
                    {
                        "source": str(rec.get("source") or f"generated:{output_rel}"),
                        "webp": output_rel,
                    }
                )
            return image

        try:
            source_rel = abs_path.resolve().relative_to(source_root.resolve()).as_posix()
        except ValueError:
            source_rel = abs_path.as_posix()
        if not is_web_texture_candidate(source_rel):
            return None
        mapped = normalized_map.get(source_rel)
        if mapped is None or not mapped.is_file():
            missing_textures.add(source_rel)
            return None
        image = original_load_image(mapped, non_color)
        if image is not None:
            used_textures.append(
                {
                    "source": source_rel,
                    "webp": _rel(output_root, mapped),
                }
            )
        return image

    base._load_image = _load_web_image


def _export_gltf(gltf_path: Path) -> None:
    gltf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.object.select_all(action="SELECT")
    except RuntimeError:
        pass
    bpy.ops.export_scene.gltf(
        filepath=str(gltf_path),
        export_format="GLTF_SEPARATE",
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_keep_originals=True,
        export_yup=True,
        use_selection=False,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
    )


def _remove_flat_white_color_attributes(*, remove_all: bool) -> list[dict]:
    removed: list[dict] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        for attr in list(getattr(mesh, "color_attributes", []) or []):
            data = list(attr.data)
            if not data:
                continue
            if not remove_all:
                is_white = True
                for row in data:
                    color = row.color
                    if any(abs(float(color[idx]) - 1.0) > 0.001 for idx in range(min(4, len(color)))):
                        is_white = False
                        break
                if not is_white:
                    continue
            removed.append({"object": obj.name, "attribute": attr.name, "count": len(data)})
            mesh.color_attributes.remove(attr)
    return removed


def _uses_unreal_vertex_color_masks(source_root: Path, mi_paths_rel: list[str]) -> bool:
    mask_texture_slots = {"curvemask", "curveatlas", "tintmask", "tintmask_vt", "tint mask"}
    mask_scalar_slots = {
        "curveid",
        "emissivecurveid",
        "sa_baseredness",
        "sa_basesaturation",
        "sa_baselightness",
        "player color atlas",
    }
    for rel in mi_paths_rel:
        path = source_root / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        textures = {str(key).replace(" ", "").lower() for key in (data.get("Textures") or {}).keys()}
        scalars = {str(key).replace(" ", "").lower() for key in ((data.get("Parameters") or {}).get("Scalars") or {}).keys()}
        if textures.intersection({key.replace(" ", "").lower() for key in mask_texture_slots}):
            return True
        if scalars.intersection({key.replace(" ", "").lower() for key in mask_scalar_slots}):
            return True
    return False


def _patch_gltf_alpha_modes(gltf_path: Path, reports: list[dict]) -> None:
    mask_materials = {
        str(row.get("slot") or "")
        for row in reports
        if "AlphaMask" in (row.get("surface") or [])
    }
    blend_materials = {
        str(row.get("slot") or "")
        for row in reports
        if "AlphaBlend" in (row.get("surface") or [])
    }
    if not mask_materials and not blend_materials:
        return

    data = json.loads(gltf_path.read_text(encoding="utf-8"))
    changed = False
    for material in data.get("materials") or []:
        name = str(material.get("name") or "")
        if name in mask_materials:
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.3333
            changed = True
        elif name in blend_materials and material.get("alphaMode") != "MASK":
            material["alphaMode"] = "BLEND"
            changed = True
    if changed:
        gltf_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _patch_gltf_webp_textures(gltf_path: Path) -> None:
    data = json.loads(gltf_path.read_text(encoding="utf-8"))
    images = data.get("images") or []
    textures = data.get("textures") or []
    changed = False

    for texture in textures:
        if not isinstance(texture, dict):
            continue
        source = texture.get("source")
        if source is None:
            webp_ext = (texture.get("extensions") or {}).get("EXT_texture_webp") or {}
            source = webp_ext.get("source")
        if not isinstance(source, int) or source < 0 or source >= len(images):
            continue

        image = images[source]
        uri = str(image.get("uri") or "").lower().split("?", 1)[0]
        if not uri.endswith(".webp"):
            continue

        if image.get("mimeType") != "image/webp":
            image["mimeType"] = "image/webp"
            changed = True

        extensions = texture.setdefault("extensions", {})
        webp_ext = extensions.setdefault("EXT_texture_webp", {})
        if webp_ext.get("source") != source:
            webp_ext["source"] = source
            changed = True
        if "source" in texture:
            del texture["source"]
            changed = True

    if changed:
        for key in ("extensionsUsed", "extensionsRequired"):
            values = data.setdefault(key, [])
            if "EXT_texture_webp" not in values:
                values.append("EXT_texture_webp")
        gltf_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    t0 = time.time()
    try:
        args = _parse_args()
        task = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
        source_root = Path(task["source_root"]).resolve()
        output_root = Path(task["output_root"]).resolve()
        asset_dir = Path(task["asset_dir"]).resolve()
        kind = str(task.get("kind") or "")
        entry = task["entry"]
        texture_map = dict(task.get("texture_map") or {})
        texture_profile = dict(task.get("texture_profile") or {})
        generated_texture_overrides = dict(task.get("generated_texture_overrides") or {})

        model_rel = entry["path"]
        uemodel_abs = (source_root / model_rel).resolve()
        if not uemodel_abs.is_file():
            raise FileNotFoundError(f".uemodel not found: {uemodel_abs}")

        used_textures: list[dict] = []
        missing_textures: set[str] = set()
        generated_textures: dict[str, dict] = {}
        _patch_image_loader(
            source_root=source_root,
            output_root=output_root,
            texture_map=texture_map,
            generated_textures=generated_textures,
            used_textures=used_textures,
            missing_textures=missing_textures,
        )

        materials_block = entry.get("Materials", {}) or {}
        mi_paths_rel = list(
            materials_block.get("material_json_paths")
            or materials_block.get("material_instance_json_paths")
            or []
        )
        hybrid_paths_rel = list(entry.get("MaterialsHybrid", {}).get("texture_image_paths", []))

        base._enable_required_addons()
        base._clear_scene()
        _install_material_texture_provider(
            source_root=source_root,
            generated_overrides=generated_texture_overrides,
            generated_records=generated_textures,
        )

        mat_slots = base._read_uemodel_materials(uemodel_abs)
        base._import_uemodel(uemodel_abs)
        overall, reports = base._build_materials(mat_slots, mi_paths_rel, hybrid_paths_rel, source_root)
        remove_all_color_attributes = any(
            "BaseColor(color)" in (row.get("params") or [])
            for row in reports
        ) or _uses_unreal_vertex_color_masks(source_root, mi_paths_rel)
        removed_color_attributes = _remove_flat_white_color_attributes(remove_all=remove_all_color_attributes)

        _clean_asset_dir(asset_dir)
        gltf_abs = asset_dir / "model.gltf"
        bin_abs = asset_dir / "model.bin"
        _export_gltf(gltf_abs)
        _patch_gltf_alpha_modes(gltf_abs, reports)
        _patch_gltf_webp_textures(gltf_abs)

        material_diagnostics: list[dict] = []
        for row in reports:
            for diagnostic in row.get("diagnostics") or []:
                if not isinstance(diagnostic, dict):
                    continue
                material_diagnostics.append(
                    {
                        "slot": row.get("slot"),
                        "mi": row.get("mi"),
                        **diagnostic,
                    }
                )

        _emit_result(
            {
                "status": "success",
                "kind": kind,
                "model_rel": model_rel,
                "asset_dir": _rel(output_root, asset_dir),
                "gltf_path": _rel(output_root, gltf_abs),
                "bin_path": _rel(output_root, bin_abs),
                "materials_source": overall,
                "slot_count": len(mat_slots),
                "slots": reports,
                "removed_color_attributes": removed_color_attributes,
                "generated_textures": sorted(generated_textures.values(), key=lambda row: row["optimized"]),
                "material_diagnostics": material_diagnostics,
                "used_textures": sorted(
                    {f"{row['source']} -> {row['webp']}" for row in used_textures}
                ),
                "missing_textures": sorted(missing_textures),
                "duration_s": round(time.time() - t0, 3),
            }
        )
        return 0
    except Exception as e:
        _emit_result(
            {
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
                "duration_s": round(time.time() - t0, 3),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
