from __future__ import annotations

import argparse
import hashlib
import json
import math
import posixpath
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageEnhance, ImageFilter


THREE_AVATAR_SCHEMA = "RSDWModel.WebsiteAvatarIndex.v1"
HAIR_TEXTURE_REVISION = "hair-v2"
BROW_TEXTURE_REVISION = "brow-v1"
PLAYER_PREFIX = "RSDragonwilds/Content/Art/Skeleton/Player/"
ARMOUR_PREFIXES = (
    "RSDragonwilds/Content/Art/Skeleton/Armour/M_MED/",
    "RSDragonwilds/Content/Art/Skeleton/Armour/F_MED/",
    "RSDragonwilds/Content/Art/Skeleton/Armour/U_MED/",
)
HELD_EQUIPMENT_PREFIX = "RSDragonwilds/Content/Gameplay/Character/Player/Equipment/Held/"
HELD_SLOT_NAMES = ("rightHand", "leftHand")
HELD_SLOT_STRATEGIES = {
    "ELoadoutSlotStrategy::HeldOnlyRight": ("rightHand", False),
    "ELoadoutSlotStrategy::HeldOnlyLeft": ("leftHand", False),
    "ELoadoutSlotStrategy::HeldTwoHanded": ("rightHand", True),
}
SKIN_MATERIALS = (
    "DefaultCharacter_Body",
    "MI_F_MED_Head_",
    "MI_M_MED_Head_",
)
HAIR_MATERIALS = (
    "HairCombin",
    "FacialHair",
    "Beard",
)
EYE_MATERIALS = (
    "Universal_Eye",
)


@dataclass(frozen=True)
class ColorOption:
    id: str
    label: str
    hex: str
    rgb: tuple[int, int, int]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _display_name(name: str) -> str:
    for suffix in (".uemodel", ".gltf", ".glb"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _asset_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _color_from_hex(value: str) -> tuple[int, int, int]:
    rgb = ImageColor.getrgb(value)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def _curve_value(keys: list[dict], t: float, default: float = 0.0) -> float:
    if not keys:
        return default
    rows = sorted(
        (
            (float(row.get("Time", 0.0)), float(row.get("Value", default)))
            for row in keys
            if isinstance(row, dict)
        ),
        key=lambda item: item[0],
    )
    if not rows:
        return default
    if t <= rows[0][0]:
        return rows[0][1]
    if t >= rows[-1][0]:
        return rows[-1][1]
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if t0 <= t <= t1:
            if math.isclose(t0, t1):
                return v1
            alpha = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * alpha
    return rows[-1][1]


def _curve_rgb(path: Path, sample_t: float = 0.5, fallback: str = "#FFFFFF") -> tuple[int, int, int]:
    if not path.is_file():
        return _color_from_hex(fallback)
    try:
        data = _read_json(path)
        row = data[0] if isinstance(data, list) and data else data
        curves = row.get("FloatCurves") if isinstance(row, dict) else None
        if not isinstance(curves, list):
            props = row.get("Properties", {}) if isinstance(row, dict) else {}
            raw = props.get("FloatCurves")
            curves = [raw, props.get("FloatCurves[1]"), props.get("FloatCurves[2]")]
        channels: list[int] = []
        for idx in range(3):
            curve = curves[idx] if idx < len(curves) and isinstance(curves[idx], dict) else {}
            value = _curve_value(curve.get("Keys") or [], sample_t, 1.0)
            channels.append(_clamp_byte(value * 255.0))
        return channels[0], channels[1], channels[2]
    except Exception:
        return _color_from_hex(fallback)


def _sample_skin_palette(source_root: Path) -> list[ColorOption]:
    path = source_root / "RSDragonwilds" / "Content" / "Art" / "Skeleton" / "Shared" / "Skin" / "T_SkinTone_ColorBar_D.png"
    fallback = [
        "#3D2419", "#56311F", "#70442D", "#875B3D",
        "#9E704E", "#B58161", "#C7926C", "#D8A57E",
        "#E2B48E", "#E8C19C", "#EFCDAE", "#F3D7BD",
        "#F6DEC9", "#FAE7D6", "#FDEFE2", "#FFF5EC",
    ]
    if not path.is_file():
        return [ColorOption(f"skin{i + 1:02d}", f"Skin {i + 1}", color, _color_from_hex(color)) for i, color in enumerate(fallback)]

    img = Image.open(path).convert("RGB")
    y = max(0, min(img.height - 1, img.height // 2))
    colors: list[ColorOption] = []
    for i in range(16):
        x = round((img.width - 1) * (i / 15 if i else 0))
        rgb = img.getpixel((x, y))
        if isinstance(rgb, int):
            rgb = (rgb, rgb, rgb)
        colors.append(ColorOption(f"skin{i + 1:02d}", f"Skin {i + 1}", _hex(rgb[:3]), rgb[:3]))
    return colors


def _curve_palette(
    archive_json_root: Path | None,
    rel_dir: str,
    prefix: str,
    count: int,
    label: str,
    fallbacks: list[str],
    start: int = 0,
) -> list[ColorOption]:
    out: list[ColorOption] = []
    for i in range(count):
        curve_num = i + start
        path = archive_json_root / rel_dir / f"{prefix}_{curve_num:02d}.json" if archive_json_root else Path()
        fallback = fallbacks[i % len(fallbacks)]
        rgb = _curve_rgb(path, sample_t=0.5, fallback=fallback)
        out.append(ColorOption(f"{label.lower()}{i + 1:02d}", f"{label} {i + 1}", _hex(rgb), rgb))
    return out


def _build_palettes(source_root: Path, archive_json_root: Path | None) -> dict[str, list[ColorOption]]:
    hair_fallbacks = ["#2E2118", "#5B3824", "#8A5632", "#B27A45", "#D6B06A", "#A33D2D", "#D7D7D3", "#1D1B1C", "#6C5D48"]
    eye_fallbacks = ["#5C3923", "#2E6A54", "#3D6D9F", "#6E8A3A", "#8B6B38", "#737373", "#6B4A8A", "#A4462E"]
    return {
        "skin": [ColorOption("skinOriginal", "Original", "#D8A58E", _color_from_hex("#D8A58E"))] + _sample_skin_palette(source_root),
        "hair": _curve_palette(
            archive_json_root,
            "RSDragonwilds/Content/Materials/Character/CurveAtlases/HairColourCurves",
            "Curve_HairColor",
            9,
            "Hair",
            hair_fallbacks,
            start=0,
        ),
        "eyes": _curve_palette(
            archive_json_root,
            "RSDragonwilds/Content/Materials/Character/Eyes_Materials/Curves",
            "Curve_EyeColor",
            8,
            "Eye",
            eye_fallbacks,
            start=1,
        ),
    }


def _slot_for(name: str, path: str) -> str | None:
    if path.startswith(PLAYER_PREFIX):
        rest = path[len(PLAYER_PREFIX):]
        folder = rest.split("/", 1)[0]
        if folder == "Body":
            return "baseBody"
        if folder == "Heads":
            return "baseHead"
        if folder == "Hair":
            return "hair"
        if folder == "Beard":
            return "beard"
        return None

    if not path.startswith(ARMOUR_PREFIXES):
        return None

    if any(token in name for token in ("_BODY_", "_Body_", "_UpperHalf", "_Upperhalf", "StarterOutfit_01_Top", "LightArmour_01_Body")):
        return "torso"
    if any(token in name for token in ("_LEGS_", "_Legs_", "_LowerHalf", "_Lowerhalf", "StarterOutfit_01_Pants", "LightArmour_01_Legs")):
        return "legs"
    if any(token in name for token in ("_HEAD_", "_Head_")):
        return "helmet"
    if "_CAPE_" in name or "_Cape_" in name:
        return "cape"
    return None


def _sex_for(name: str, path: str) -> str:
    combined = f"{name} {path}"
    if "SK_F_MED" in combined or "/F_MED/" in combined:
        return "F_MED"
    if "SK_M_MED" in combined or "/M_MED/" in combined:
        return "M_MED"
    if "SK_U_MED" in combined or "/U_MED/" in combined or name.startswith("SM_"):
        return "U_MED"
    return "U_MED"


def _head_family(name: str) -> str | None:
    import re

    match = re.search(r"_Head_([A-D])(?:_|$)", name)
    return match.group(1) if match else None


def _label_for(name: str, slot: str) -> str:
    label = _display_name(name)
    prefixes = (
        "SK_F_MED_",
        "SK_M_MED_",
        "SK_U_MED_",
        "SK_F_",
        "SK_M_",
        "SM_",
    )
    for prefix in prefixes:
        if label.startswith(prefix):
            label = label[len(prefix):]
            break
    for token in ("BODY_", "HEAD_", "LEGS_", "CAPE_", "Body_", "Head_", "Legs_", "Cape_"):
        label = label.replace(token, "")
    if slot in {"baseBody", "baseHead"}:
        label = label.replace("Body_", "").replace("Head_", "")
    return label.replace("_", " ").strip() or _display_name(name)


def _clean_item_label(value: str) -> str:
    label = _display_name(value)
    for prefix in ("ITEM_", "BP_", "SK_", "SM_"):
        if label.startswith(prefix):
            label = label[len(prefix):]
            break
    return label.replace("_", " ").strip() or value


def _localized_label(props: dict, fallback: str) -> str:
    name = props.get("Name") if isinstance(props, dict) else None
    if isinstance(name, dict):
        for key in ("LocalizedString", "SourceString", "Key"):
            value = name.get(key)
            if value:
                return str(value)
    return _clean_item_label(fallback)


def _archive_rel(path: Path, archive_json_root: Path) -> str:
    return path.relative_to(archive_json_root).as_posix()


def _asset_path_to_archive_rel(asset_path: str) -> str | None:
    value = str(asset_path or "").replace("\\", "/")
    if not value:
        return None
    if value.startswith("/Game/"):
        value = "RSDragonwilds/Content/" + value[len("/Game/"):]
    if "." in value:
        value = value.split(".", 1)[0]
    if value.endswith("_C"):
        value = value[:-2]
    if not value.startswith("RSDragonwilds/Content/"):
        return None
    return value


def _asset_path_to_json_path(asset_path: str, archive_json_root: Path) -> Path | None:
    rel = _asset_path_to_archive_rel(asset_path)
    if not rel:
        return None
    return archive_json_root / Path(rel + ".json")


def _asset_path_to_model_id(asset_path: str, kind: str | None = None) -> str | None:
    rel = _asset_path_to_archive_rel(asset_path)
    if not rel:
        return None
    name = posixpath.basename(rel)
    inferred = kind
    if inferred is None:
        if name.startswith("SK_"):
            inferred = "SK"
        elif name.startswith("SM_"):
            inferred = "SM"
    if inferred not in {"SK", "SM"}:
        return None
    return f"{inferred}:{rel}.uemodel"


def _object_path_to_model_id(object_path: str, kind: str | None = None) -> str | None:
    value = str(object_path or "").replace("\\", "/")
    if "." in value:
        value = value.rsplit(".", 1)[0]
    return _asset_path_to_model_id(value, kind)


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _find_actor_skeletal_model_id(actor_json_path: Path) -> tuple[str | None, list[str]]:
    diagnostics: list[str] = []
    if not actor_json_path or not actor_json_path.is_file():
        return None, ["missing_actor_json"]
    try:
        data = _read_json(actor_json_path)
    except Exception as exc:
        return None, [f"actor_json_error:{exc}"]

    for row in data if isinstance(data, list) else [data]:
        if not isinstance(row, dict):
            continue
        props = row.get("Properties") or {}
        for key in ("SkeletalMesh", "SkinnedAsset"):
            ref = props.get(key)
            if isinstance(ref, dict):
                model_id = _object_path_to_model_id(ref.get("ObjectPath") or ref.get("AssetPathName") or "", "SK")
                if model_id:
                    return model_id, diagnostics

    for item in _iter_dicts(data):
        for key in ("SkeletalMesh", "SkinnedAsset"):
            ref = item.get(key)
            if isinstance(ref, dict):
                model_id = _object_path_to_model_id(ref.get("ObjectPath") or ref.get("AssetPathName") or "", "SK")
                if model_id:
                    return model_id, diagnostics
    return None, ["missing_actor_skeletal_mesh"]


def _webasset_rel_from_uri(gltf_rel: str, uri: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(gltf_rel), uri)).replace("\\", "/")


def _texture_rel_for_material(gltf: dict, gltf_rel: str, material: dict) -> str | None:
    pbr = material.get("pbrMetallicRoughness") or {}
    tex_ref = pbr.get("baseColorTexture") or {}
    tex_idx = tex_ref.get("index")
    if tex_idx is None:
        return None
    textures = gltf.get("textures") or []
    images = gltf.get("images") or []
    if not isinstance(tex_idx, int) or tex_idx >= len(textures):
        return None
    tex = textures[tex_idx] or {}
    ext = ((tex.get("extensions") or {}).get("EXT_texture_webp") or {})
    source_idx = ext.get("source", tex.get("source"))
    if not isinstance(source_idx, int) or source_idx >= len(images):
        return None
    uri = (images[source_idx] or {}).get("uri")
    if not uri:
        return None
    return _webasset_rel_from_uri(gltf_rel, uri)


def _material_role(name: str) -> str | None:
    if any(token in name for token in EYE_MATERIALS):
        return "eyes"
    if any(token in name for token in HAIR_MATERIALS):
        return "hair"
    if any(token in name for token in SKIN_MATERIALS):
        return "skin"
    return None


def _colorize_image(img: Image.Image, color: tuple[int, int, int], role: str, variant_style: str | None = None) -> Image.Image:
    rgba = img.convert("RGBA")
    gray = rgba.convert("L")
    if role == "hair" and variant_style == "brow":
        return _brow_tint_image(rgba, color)
    if role == "skin":
        return _skin_tint_image(rgba, color)
    if role == "hair":
        return _hair_tint_image(rgba, color)
    if role == "eyes":
        # Tint only saturated/non-white pixels so sclera stays readable.
        pixels = rgba.load()
        out = rgba.copy()
        out_pixels = out.load()
        for y in range(out.height):
            for x in range(out.width):
                r, g, b, a = pixels[x, y]
                if a == 0:
                    continue
                mx, mn = max(r, g, b), min(r, g, b)
                sat = 0 if mx == 0 else (mx - mn) / mx
                white_distance = (abs(r - 255) + abs(g - 255) + abs(b - 255)) / 765
                weight = max(0.0, min(1.0, sat * 1.8 + white_distance * 0.35 - 0.18))
                lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
                tr = _clamp_byte(color[0] * (0.45 + lum * 0.8))
                tg = _clamp_byte(color[1] * (0.45 + lum * 0.8))
                tb = _clamp_byte(color[2] * (0.45 + lum * 0.8))
                out_pixels[x, y] = (
                    _clamp_byte(r * (1 - weight) + tr * weight),
                    _clamp_byte(g * (1 - weight) + tg * weight),
                    _clamp_byte(b * (1 - weight) + tb * weight),
                    a,
                )
        return out

    low = tuple(_clamp_byte(c * 0.18) for c in color)
    high = tuple(_clamp_byte(255 - (255 - c) * 0.08) for c in color)
    tinted_rgb = ImageOps_colorize(gray, low, high)
    tinted = Image.merge("RGBA", (*tinted_rgb.split(), rgba.getchannel("A")))
    tinted = ImageEnhance.Contrast(tinted).enhance(1.08)
    return tinted


def _hair_tint_image(rgba: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    try:
        import numpy as np
    except Exception:
        gray = rgba.convert("L")
        low = tuple(_clamp_byte(c * 0.22) for c in color)
        high = tuple(_clamp_byte(c * 1.42 + 18) for c in color)
        tinted = Image.merge("RGBA", (*ImageOps_colorize(gray, low, high).split(), rgba.getchannel("A")))
        return ImageEnhance.Contrast(tinted).enhance(0.96)

    arr = np.asarray(rgba).astype(np.float32)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3:4]
    lum = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    shade = np.clip((lum / 128.0) ** 0.88, 0.26, 1.58)
    target = np.array(color, dtype=np.float32)
    tinted = np.clip(target[None, None, :] * shade[:, :, None], 0, 255)

    # Keep a little original atlas variation so strand cards do not become flat,
    # but do not let bright gray atlas highlights wash the hair back to white.
    detail = np.clip((rgb - lum[:, :, None]) * 0.18, -18.0, 18.0)
    out_rgb = np.clip(tinted + detail, 0, 255)
    out = np.concatenate([out_rgb, alpha], axis=2).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _brow_tint_image(rgba: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    blur_radius = max(4, min(rgba.size) // 28)
    smooth = rgba.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    try:
        import numpy as np
    except Exception:
        gray = smooth.convert("L")
        low = tuple(_clamp_byte(c * 0.46) for c in color)
        high = tuple(_clamp_byte(c * 1.16 + 8) for c in color)
        tinted = Image.merge("RGBA", (*ImageOps_colorize(gray, low, high).split(), rgba.getchannel("A")))
        return ImageEnhance.Contrast(tinted).enhance(0.72)

    arr = np.asarray(rgba).astype(np.float32)
    smooth_arr = np.asarray(smooth).astype(np.float32)
    rgb = arr[:, :, :3]
    smooth_rgb = smooth_arr[:, :, :3]
    alpha = arr[:, :, 3:4]
    lum = smooth_rgb[:, :, 0] * 0.2126 + smooth_rgb[:, :, 1] * 0.7152 + smooth_rgb[:, :, 2] * 0.0722
    avg = max(float(lum.mean()), 1.0)
    shade = np.clip((lum / avg) ** 0.55, 0.58, 1.22)
    target = np.array(color, dtype=np.float32)
    tinted = np.clip(target[None, None, :] * shade[:, :, None], 0, 255)

    # Head brows use the hair atlas, but they should read as compact brows, not
    # full hair-card strand strips. Keep only a whisper of local detail.
    detail = np.clip((rgb - smooth_rgb) * 0.035, -5.0, 5.0)
    out_rgb = np.clip(tinted + detail, 0, 255)
    out = np.concatenate([out_rgb, alpha], axis=2).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _skin_tint_image(rgba: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    try:
        import numpy as np
    except Exception:
        low = tuple(_clamp_byte(c * 0.42) for c in color)
        high = tuple(_clamp_byte(255 - (255 - c) * 0.22) for c in color)
        gray = rgba.convert("L")
        tinted = Image.merge("RGBA", (*ImageOps_colorize(gray, low, high).split(), rgba.getchannel("A")))
        return Image.blend(rgba, tinted, 0.58)

    arr = np.asarray(rgba).astype(np.float32)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3:4]
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    mx = np.maximum.reduce([r, g, b])
    mn = np.minimum.reduce([r, g, b])
    sat = np.divide(mx - mn, np.maximum(mx, 1.0))

    # The player body texture includes fabric and shadowed areas. Weight only
    # warm skin-like pixels so color swatches do not recolor underwear/details.
    warm_rg = np.clip((r - g * 0.82) / 52.0, 0.0, 1.0)
    warm_gb = np.clip((g - b * 0.76 + 8.0) / 44.0, 0.0, 1.0)
    red_blue = np.clip((r - b - 4.0) / 64.0, 0.0, 1.0)
    bright = np.clip((mx - 45.0) / 120.0, 0.0, 1.0)
    not_gray = np.clip((sat - 0.035) / 0.22, 0.0, 1.0)
    weight = warm_rg * warm_gb * red_blue * bright
    weight = np.maximum(weight, np.minimum(weight, not_gray * warm_rg * red_blue * 0.65))
    weight = np.where(alpha[:, :, 0] > 0, weight, 0.0)

    if float(weight.max()) <= 0.0:
        return rgba.copy()

    lum = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    avg_lum = float((lum * weight).sum() / max(float(weight.sum()), 1.0))
    shade = np.clip(lum / max(avg_lum, 1.0), 0.35, 1.85)
    target = np.array(color, dtype=np.float32)
    tinted = np.clip(shade[:, :, None] * target[None, None, :], 0, 255)
    blend = np.clip(weight * 0.92, 0.0, 0.92)[:, :, None]
    out_rgb = rgb * (1.0 - blend) + tinted * blend
    out = np.concatenate([out_rgb, alpha], axis=2).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def ImageOps_colorize(gray: Image.Image, black: tuple[int, int, int], white: tuple[int, int, int]) -> Image.Image:
    # Tiny wrapper keeps the import list obvious for generated environments.
    from PIL import ImageOps

    return ImageOps.colorize(gray, black=black, white=white)


def _save_variant(
    *,
    webassets_root: Path,
    source_rel: str,
    role: str,
    color: ColorOption,
    quality: int,
    cache: dict[tuple[str, str, str], str],
    force: bool,
    variant_style: str | None = None,
) -> str | None:
    key = (source_rel, f"{role}:{variant_style or 'default'}", color.id)
    if key in cache:
        return cache[key]
    source_abs = webassets_root / Path(source_rel)
    if not source_abs.is_file():
        return None
    if role == "hair" and variant_style == "brow":
        hash_source = f"{BROW_TEXTURE_REVISION}:{source_rel}"
    elif role == "hair":
        hash_source = f"{HAIR_TEXTURE_REVISION}:{source_rel}"
    else:
        hash_source = source_rel
    out_rel = f"avatar/textures/{role}/{_asset_hash(hash_source)}/{color.id}.webp"
    out_abs = webassets_root / Path(out_rel)
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    if force or not out_abs.is_file():
        img = Image.open(source_abs)
        baked = _colorize_image(img, color.rgb, role, variant_style)
        baked.save(out_abs, "WEBP", quality=quality, method=6)
    cache[key] = out_rel
    return out_rel


def _material_variants(
    *,
    repo_root: Path,
    webassets_root: Path,
    gltf_rel: str,
    palettes: dict[str, list[ColorOption]],
    quality: int,
    cache: dict[tuple[str, str, str], str],
    force_textures: bool,
    slot: str | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    gltf_abs = webassets_root / Path(gltf_rel)
    if not gltf_abs.is_file():
        return {}
    try:
        gltf = _read_json(gltf_abs)
    except Exception:
        return {}

    out: dict[str, dict[str, dict[str, str]]] = {}
    for material in gltf.get("materials") or []:
        if not isinstance(material, dict):
            continue
        mat_name = str(material.get("name") or "")
        role = _material_role(mat_name)
        if not role:
            continue
        source_rel = _texture_rel_for_material(gltf, gltf_rel, material)
        if not source_rel:
            continue
        variant_style = "brow" if slot == "baseHead" and role == "hair" else None
        role_variants: dict[str, str] = {}
        for color in palettes.get(role, []):
            if color.id == "skinOriginal":
                continue
            variant_rel = _save_variant(
                webassets_root=webassets_root,
                source_rel=source_rel,
                role=role,
                color=color,
                quality=quality,
                cache=cache,
                force=force_textures,
                variant_style=variant_style,
            )
            if variant_rel:
                role_variants[color.id] = variant_rel
        if role_variants:
            out.setdefault(role, {})[mat_name] = role_variants
    return out


def _candidate_rows(
    *,
    repo_root: Path,
    webassets_root: Path,
    model_index: dict,
    palettes: dict[str, list[ColorOption]],
    quality: int,
    force_textures: bool,
) -> dict[str, list[dict]]:
    slots = {key: [] for key in ("baseBody", "baseHead", "hair", "beard", "torso", "legs", "helmet", "cape", *HELD_SLOT_NAMES)}
    variant_cache: dict[tuple[str, str, str], str] = {}

    for model in model_index.get("models") or []:
        if not isinstance(model, dict) or model.get("kind") != "SK":
            continue
        name = str(model.get("displayName") or _display_name(str(model.get("name") or "")))
        path = str(model.get("path") or "")
        slot = _slot_for(name, path)
        if not slot:
            continue
        gltf_path = str(model.get("gltfPath") or "")
        if not gltf_path or not (webassets_root / Path(gltf_path)).is_file():
            continue
        row = {
            "id": model.get("id"),
            "name": model.get("name"),
            "displayName": name,
            "label": _label_for(name, slot),
            "slot": slot,
            "sex": _sex_for(name, path),
            "headFamily": _head_family(name),
            "path": path,
            "gltfPath": gltf_path,
            "assetDir": model.get("assetDir"),
            "materialVariants": _material_variants(
                repo_root=repo_root,
                webassets_root=webassets_root,
                gltf_rel=gltf_path,
                palettes=palettes,
                quality=quality,
                cache=variant_cache,
                force_textures=force_textures,
                slot=slot,
            ),
        }
        slots[slot].append(row)

    for slot, rows in slots.items():
        rows.sort(key=lambda item: (item["sex"], item["label"].lower(), item["path"].lower()))
    return slots


def _model_lookup(model_index: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for model in model_index.get("models") or []:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id")
        gltf_path = model.get("gltfPath")
        if model_id and gltf_path:
            out[str(model_id)] = model
    return out


def _category_label(props: dict) -> str | None:
    category = props.get("Category") if isinstance(props, dict) else None
    tag = category.get("TagName") if isinstance(category, dict) else None
    if not tag:
        return None
    parts = [part for part in str(tag).split(".") if part and part not in {"Item", "Equipment", "Weapon"}]
    return " ".join(parts) if parts else None


def _is_left_hand_two_hander(category: str | None, item_path: Path) -> bool:
    category_text = str(category or "").lower()
    item_text = item_path.as_posix().lower()
    return ("bow" in category_text and "crossbow" not in category_text) or "/held/bow/" in item_text


def _build_held_equipment_rows(
    *,
    archive_json_root: Path | None,
    webassets_root: Path,
    model_index: dict,
) -> dict[str, list[dict]]:
    slots = {key: [] for key in HELD_SLOT_NAMES}
    if archive_json_root is None or not archive_json_root.is_dir():
        return slots

    held_root = archive_json_root / Path(HELD_EQUIPMENT_PREFIX)
    if not held_root.is_dir():
        return slots

    lookup = _model_lookup(model_index)
    seen_ids: set[str] = set()
    item_paths = sorted(held_root.rglob("ITEM_*.json"), key=lambda path: path.as_posix().lower())
    for item_path in item_paths:
        try:
            data = _read_json(item_path)
        except Exception:
            continue
        rows = data if isinstance(data, list) else [data]
        item = next((row for row in rows if isinstance(row, dict) and isinstance(row.get("Properties"), dict)), None)
        if not item:
            continue
        props = item.get("Properties") or {}
        slot_strategy = str(props.get("Slot") or "")
        hand_slot, is_two_handed = HELD_SLOT_STRATEGIES.get(slot_strategy, (None, False))
        if not hand_slot:
            continue
        category = _category_label(props)
        if is_two_handed and _is_left_hand_two_hander(category, item_path):
            hand_slot = "leftHand"

        diagnostics: list[str] = []
        model_id = None
        actor_ref = props.get("HeldEquipmentActorClass")
        actor_json_path = None
        if isinstance(actor_ref, dict):
            actor_json_path = _asset_path_to_json_path(str(actor_ref.get("AssetPathName") or ""), archive_json_root)
            actor_model_id, actor_diagnostics = _find_actor_skeletal_model_id(actor_json_path) if actor_json_path else (None, ["missing_actor_path"])
            diagnostics.extend(actor_diagnostics)
            if actor_model_id in lookup:
                model_id = actor_model_id

        static_ref = props.get("StaticMesh")
        static_model_id = None
        if isinstance(static_ref, dict):
            static_model_id = _asset_path_to_model_id(str(static_ref.get("AssetPathName") or ""), "SM")
        if not model_id and static_model_id in lookup:
            model_id = static_model_id
        if not model_id:
            continue

        model = lookup.get(model_id)
        if not model:
            continue
        gltf_path = str(model.get("gltfPath") or "")
        if not gltf_path or not (webassets_root / Path(gltf_path)).is_file():
            continue

        item_name = str(item.get("Name") or item_path.stem)
        label = _localized_label(props, item_name)
        item_hash = _asset_hash(_archive_rel(item_path, archive_json_root), 12)
        for row_slot in HELD_SLOT_NAMES:
            is_natural_slot = row_slot == hand_slot
            row_id = f"held:{item_hash}:{model_id}" if is_natural_slot else f"held:{item_hash}:{row_slot}:{model_id}"
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            attach_side = "right" if row_slot == "rightHand" else "left"
            row = {
                "id": row_id,
                "name": item_name,
                "displayName": str(model.get("displayName") or model.get("name") or item_name),
                "label": label,
                "slot": row_slot,
                "sex": "U_MED",
                "path": str(model.get("path") or ""),
                "gltfPath": gltf_path,
                "assetDir": model.get("assetDir"),
                "baseModelId": model_id,
                "heldItemDataPath": _archive_rel(item_path, archive_json_root),
                "heldActorDataPath": _archive_rel(actor_json_path, archive_json_root) if actor_json_path and actor_json_path.is_file() else None,
                "slotStrategy": slot_strategy,
                "defaultSlot": hand_slot,
                "isMirroredHand": not is_natural_slot,
                "isTwoHanded": is_two_handed,
                "attachSide": attach_side,
                "attachFallbacks": ["prop_r", "hand_r"] if attach_side == "right" else ["prop_l", "hand_l"],
                "category": category,
                "animationPoseType": props.get("AnimationPoseType"),
                "animationPoseSequence": (props.get("AnimationPosesSequence") or {}).get("ObjectPath") if isinstance(props.get("AnimationPosesSequence"), dict) else None,
                "itemFilterTags": [
                    tag.get("TagName") if isinstance(tag, dict) else str(tag)
                    for tag in (props.get("ItemFilterTags") or [])
                ],
                "diagnostics": diagnostics,
                "materialVariants": {},
            }
            slots[row_slot].append(row)

    for slot, rows in slots.items():
        rows.sort(key=lambda item: (item.get("category") or "", item["label"].lower(), item["path"].lower()))
    return slots


def _append_equipment_variant_rows(
    *,
    repo_root: Path,
    webassets_root: Path,
    slots: dict[str, list[dict]],
    equipment_variants_path: Path | None,
    palettes: dict[str, list[ColorOption]],
    quality: int,
    force_textures: bool,
) -> None:
    if equipment_variants_path is None or not equipment_variants_path.is_file():
        return
    try:
        variants_index = _read_json(equipment_variants_path)
    except Exception:
        return
    variant_cache: dict[tuple[str, str, str], str] = {}
    valid_slots = set(slots)
    seen_ids = {str(row.get("id")) for rows in slots.values() for row in rows}
    for group in (variants_index.get("byModel") or {}).values():
        if not isinstance(group, dict):
            continue
        for variant in group.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            slot = str(variant.get("slot") or "")
            if slot not in valid_slots:
                continue
            variant_id = str(variant.get("id") or "")
            gltf_path = str(variant.get("gltfPath") or "")
            if not variant_id or variant_id in seen_ids or not gltf_path:
                continue
            if not (webassets_root / Path(gltf_path)).is_file():
                continue
            label = str(variant.get("label") or variant_id)
            row = {
                "id": variant_id,
                "name": label,
                "displayName": label,
                "label": label,
                "slot": slot,
                "sex": variant.get("sex") or "U_MED",
                "headFamily": None,
                "path": variant.get("baseModelPath") or variant.get("meshDataPath") or "",
                "gltfPath": gltf_path,
                "assetDir": str(Path(gltf_path).parent).replace("\\", "/"),
                "baseModelId": variant.get("baseModelId"),
                "equipmentVariantId": variant_id,
                "equipmentMeshDataPath": variant.get("meshDataPath"),
                "materialOverridePaths": variant.get("materialOverridePaths") or [],
                "missingTextureCount": variant.get("missingTextureCount") or 0,
                "materialVariants": _material_variants(
                    repo_root=repo_root,
                    webassets_root=webassets_root,
                    gltf_rel=gltf_path,
                    palettes=palettes,
                    quality=quality,
                    cache=variant_cache,
                    force_textures=force_textures,
                    slot=slot,
                ),
            }
            slots[slot].append(row)
            seen_ids.add(variant_id)

    for slot, rows in slots.items():
        rows.sort(key=lambda item: (item["sex"], item["label"].lower(), item["path"].lower()))


def _default_for(slots: dict[str, list[dict]], slot: str, display_name: str | None = None) -> str | None:
    if display_name:
        for row in slots.get(slot, []):
            if row.get("displayName") == display_name:
                return row.get("id")
    rows = slots.get(slot) or []
    return rows[0].get("id") if rows else None


def _serialize_palettes(palettes: dict[str, list[ColorOption]]) -> dict[str, list[dict]]:
    return {
        role: [{"id": color.id, "label": color.label, "hex": color.hex} for color in colors]
        for role, colors in palettes.items()
    }


def build_avatar_index(
    *,
    repo_root: Path,
    dataset_version: str,
    model_index_path: Path,
    output_path: Path,
    archive_json_root: Path | None,
    equipment_variants_path: Path | None,
    texture_quality: int,
    force_textures: bool,
) -> dict:
    source_root = repo_root / dataset_version
    webassets_root = source_root / "WebAssets"
    model_index = _read_json(model_index_path)
    palettes = _build_palettes(source_root, archive_json_root)
    slots = _candidate_rows(
        repo_root=repo_root,
        webassets_root=webassets_root,
        model_index=model_index,
        palettes=palettes,
        quality=texture_quality,
        force_textures=force_textures,
    )
    held_slots = _build_held_equipment_rows(
        archive_json_root=archive_json_root,
        webassets_root=webassets_root,
        model_index=model_index,
    )
    for slot, rows in held_slots.items():
        slots.setdefault(slot, []).extend(rows)
    _append_equipment_variant_rows(
        repo_root=repo_root,
        webassets_root=webassets_root,
        slots=slots,
        equipment_variants_path=equipment_variants_path,
        palettes=palettes,
        quality=texture_quality,
        force_textures=force_textures,
    )

    out = {
        "schema": THREE_AVATAR_SCHEMA,
        "datasetVersion": dataset_version,
        "generatedUtc": _utc_now(),
        "sourceModelIndex": "website/model-index.json",
        "sourceEquipmentVariants": "website/equipment-variants.json" if equipment_variants_path and equipment_variants_path.is_file() else None,
        "sourceWebAssets": f"{dataset_version}/WebAssets",
        "defaults": {
            "sex": "M_MED",
            "bodyVisible": True,
            "headVisible": True,
            "slots": {
                "baseBody": _default_for(slots, "baseBody", "SK_M_MED_Body_A_01"),
                "baseHead": _default_for(slots, "baseHead", "SK_M_MED_Head_A_01"),
                "hair": None,
                "beard": None,
                "torso": None,
                "legs": None,
                "helmet": None,
                "cape": None,
                "rightHand": None,
                "leftHand": None,
            },
            "colors": {
                "skin": palettes["skin"][0].id,
                "hair": palettes["hair"][0].id,
                "eyes": palettes["eyes"][0].id,
            },
        },
        "colors": _serialize_palettes(palettes),
        "slots": slots,
        "counts": {slot: len(rows) for slot, rows in slots.items()},
    }
    _write_json(output_path, out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Avatar page index and avatar-only color texture variants.")
    parser.add_argument("--repo-root", type=Path, default=_repo_root())
    parser.add_argument("--dataset-version", default="0.11.2.2")
    parser.add_argument("--model-index", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--archive-json-root", type=Path, default=None)
    parser.add_argument("--equipment-variants", type=Path, default=None)
    parser.add_argument("--texture-quality", type=int, default=75)
    parser.add_argument("--force-textures", action="store_true", help="Rewrite existing generated avatar texture variants.")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    model_index = args.model_index or repo / "website" / "model-index.json"
    output = args.output or repo / "website" / "avatar-index.json"
    archive_json_root = args.archive_json_root
    if archive_json_root is None:
        candidate = Path(r"E:\Github\RSDWArchive") / args.dataset_version / "json"
        archive_json_root = candidate if candidate.is_dir() else None
    elif not archive_json_root.is_dir():
        archive_json_root = None
    equipment_variants = args.equipment_variants
    if equipment_variants is None:
        candidate = repo / "website" / "equipment-variants.json"
        equipment_variants = candidate if candidate.is_file() else None
    elif not equipment_variants.is_file():
        equipment_variants = None

    result = build_avatar_index(
        repo_root=repo,
        dataset_version=args.dataset_version,
        model_index_path=model_index.resolve(),
        output_path=output.resolve(),
        archive_json_root=archive_json_root.resolve() if archive_json_root else None,
        equipment_variants_path=equipment_variants.resolve() if equipment_variants else None,
        texture_quality=args.texture_quality,
        force_textures=args.force_textures,
    )
    print(f"wrote avatar index to {output}")
    print("slot counts:")
    for slot, count in result["counts"].items():
        print(f"  {slot}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
