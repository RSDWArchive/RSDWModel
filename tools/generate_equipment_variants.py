from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MODEL_DATA_DIR = Path(__file__).resolve().parent / "ModelData"
if str(MODEL_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DATA_DIR))

from EnrichFromArchive import (  # noqa: E402
    _archive_objects,
    _convert_archive_material,
    _first_existing_json,
    _first_existing_texture,
    _package_path_to_relatives,
)
from WebTextureRules import is_web_texture_candidate  # noqa: E402


SCHEMA = "RSDWModel.EquipmentVariants.v1"
TEXTURE_EXTENSIONS = (".png", ".hdr", ".tga", ".jpg", ".jpeg", ".webp", ".dds", ".bmp")
WEBP_ALPHA_REVISION = "equipment-variant-webp-v1"
VARIANT_GLTF_REVISION = "equipment-variant-gltf-v1"
DEFAULT_DATASET_VERSION = "0.12.0.0"
PLAYER_EQUIPMENT_REL = "RSDragonwilds/Content/Gameplay/Character/Player/Equipment"
ITEM_NAMES_REL = "RSDragonwilds/Content/Gameplay/Items/ST_ItemNames.json"

ROLE_TEXTURE_KEYS = {
    "BaseColor": ("BaseColor", "PM_Diffuse", "Diffuse", "BaseColor Map", "Base Color"),
    "Normal": ("Normal", "PM_Normals", "Normals"),
    "ORM": ("ORM", "PM_SpecularMasks", "SpecularMasks", "ORM Map"),
}

MATERIAL_SLOT_BY_EQUIPMENT_FOLDER = {
    "Body": "torso",
    "Legs": "legs",
    "Head": "helmet",
    "Cape": "cape",
}

COLOR_NAME_RGB = {
    "black": (22, 21, 21),
    "blue": (32, 69, 170),
    "green": (48, 118, 56),
    "orange": (196, 89, 26),
    "pink": (214, 89, 157),
    "purple": (123, 67, 165),
    "red": (167, 38, 28),
    "white": (210, 205, 190),
    "yellow": (206, 160, 35),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_archive_root(dataset_version: str) -> Path:
    return Path(r"E:\Github\RSDWArchive") / dataset_version


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _safe_slug(value: str, fallback: str = "variant") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72].strip("-") or fallback


def _clean_words(value: str) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _mesh_data_key(stem: str) -> str:
    name = re.sub(r"^ITEM_", "", stem)
    name = re.sub(r"_MeshData(?:_[A-Za-z0-9]+)?$", "", name)
    return name


def _candidate_item_keys(mesh_stem: str) -> list[str]:
    key = _mesh_data_key(mesh_stem)
    parts = key.split("_")
    candidates: list[str] = []

    if key == "Cape_Adventurers":
        candidates.append("AdventurersCape")
    elif key.startswith("Cape_Adventurers_") and len(parts) >= 3:
        candidates.append(f"{parts[-1]}CapeAdventurers")
    elif key.startswith("Cape_Trimmed_Skillcape_") and len(parts) >= 4:
        candidates.append(f"{parts[-1]}SkillcapeTrimmed")
    elif key.startswith("Cape_Skillcape_") and len(parts) >= 3:
        candidates.append(f"{parts[-1]}Skillcape")
    elif key.startswith("Cape_") and len(parts) >= 2:
        special = {
            "AlphaTest": "AlphaCape",
            "EarlyAdopter": "EarlyAdopterCape",
            "Garou": "GarouCape",
            "Goblin": "GoblinCape",
        }
        candidates.append(special.get(parts[1], f"{parts[1]}Cape"))

    if len(parts) >= 3 and parts[0] == "Armour":
        kind = parts[1]
        item = parts[-1]
        if kind == "Body":
            candidates.extend([f"{item}Platebody", f"{item}Body", f"{item}Robe"])
        elif kind == "Legs":
            candidates.extend([f"{item}Platelegs", f"{item}Leggings", f"{item}Chaps"])
        elif kind == "Head":
            candidates.extend([f"{item}Helmet", f"{item}Hat", f"{item}Cowl"])

    candidates.append("".join(parts))
    return list(dict.fromkeys(candidates))


def _load_item_names(archive_root: Path) -> dict[str, str]:
    path = archive_root / "json" / ITEM_NAMES_REL
    if not path.is_file():
        return {}
    data = _read_json(path)
    first = _archive_objects(data)[0] if _archive_objects(data) else {}
    names = ((first.get("StringTable") or {}).get("KeysToEntries") or {})
    return {str(key): str(value) for key, value in names.items()}


def _display_label(mesh_stem: str, item_names: dict[str, str]) -> str:
    for key in _candidate_item_keys(mesh_stem):
        value = item_names.get(key)
        if value:
            return value
    key = _mesh_data_key(mesh_stem)
    if key.startswith("Armour_"):
        key = key[len("Armour_"):]
    return _clean_words(key)


def _strip_package_object_suffix(value: str) -> str:
    s = value.replace("\\", "/").strip()
    if "." not in s:
        return s
    head, tail = s.rsplit(".", 1)
    if tail.isdigit() or head.rsplit("/", 1)[-1] == tail:
        return head
    return s


def _mesh_asset_to_model_path(asset_path: str) -> str | None:
    package = _strip_package_object_suffix(asset_path)
    for rel in _package_path_to_relatives(package):
        if rel:
            return f"{rel}.uemodel"
    return None


def _sex_from_mesh_data(stem: str, mesh_path: str) -> str:
    low = f"{stem} {mesh_path}".lower()
    if "_female" in low or "/f_med/" in low:
        return "F_MED"
    if "_male" in low or "/m_med/" in low:
        return "M_MED"
    return "U_MED"


def _slot_from_mesh_data(path: Path) -> str | None:
    for folder, slot in MATERIAL_SLOT_BY_EQUIPMENT_FOLDER.items():
        if path.parent.name == folder:
            return slot
    return None


def _material_name_from_archive_json(path: Path) -> str:
    try:
        objects = _archive_objects(_read_json(path))
    except Exception:
        return path.stem
    for obj in objects:
        if "Material" in str(obj.get("Type", "")):
            return str(obj.get("Name") or path.stem)
    return path.stem


def _first_value(mapping: dict, names: Iterable[str]) -> str | None:
    normalized = {str(key).replace(" ", "").replace("_", "").lower(): value for key, value in mapping.items()}
    for name in names:
        value = normalized.get(name.replace(" ", "").replace("_", "").lower())
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_scalar(parameters: dict, *names: str) -> float | None:
    scalars = (parameters.get("Scalars") or {}) if isinstance(parameters, dict) else {}
    normalized = {str(key).replace(" ", "").replace("_", "").lower(): value for key, value in scalars.items()}
    for name in names:
        value = normalized.get(name.replace(" ", "").replace("_", "").lower())
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _first_switch(parameters: dict, *names: str) -> bool | None:
    switches = (parameters.get("Switches") or {}) if isinstance(parameters, dict) else {}
    normalized = {str(key).replace(" ", "").replace("_", "").lower(): value for key, value in switches.items()}
    for name in names:
        value = normalized.get(name.replace(" ", "").replace("_", "").lower())
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in {"true", "1", "yes", "on"}:
                return True
            if low in {"false", "0", "no", "off"}:
                return False
    return None


def _is_default_texture(package_path: str, role: str) -> bool:
    low = package_path.replace("\\", "/").lower()
    if "/engineresources/defaulttexture" in low or "/enginematerials/defaultdiffuse" in low:
        return True
    if role == "BaseColor":
        return (
            "/defaulttextures/t_default_white_d" in low
            or "/enginematerials/t_default_basecolor" in low
            or "/character/defaultvt/t_defaultvt_d" in low
        )
    if role == "Normal":
        return "/defaulttextures/t_default_n" in low or "/character/defaultvt/t_defaultvt_n" in low
    if role == "ORM":
        return "/defaulttextures/t_default_orm" in low or "/character/defaultvt/t_defaultvt_orm" in low
    return False


def _resolve_archive_texture(archive_root: Path, package_path: str) -> tuple[Path, str] | None:
    for rel in _package_path_to_relatives(package_path):
        if not rel:
            continue
        found = _first_existing_texture(archive_root, rel)
        if found is not None:
            return found, rel
        base = archive_root / "textures" / rel
        for ext in TEXTURE_EXTENSIONS:
            candidate = base.with_suffix(ext)
            if candidate.is_file():
                return candidate, rel
    return None


def _texture_cache_rel(texture_hash: str, texture_size: int) -> str:
    return f"textures/webp_{texture_size}/{texture_hash}.webp"


def _texture_hash(source_label: str, source_paths: Iterable[Path], texture_size: int, texture_quality: int, extra: dict | None = None) -> str:
    parts = [source_label.replace("\\", "/"), str(texture_size), str(texture_quality), WEBP_ALPHA_REVISION]
    for path in source_paths:
        if path.is_file():
            stat = path.stat()
            parts.extend([path.resolve().as_posix(), str(stat.st_size), str(stat.st_mtime_ns)])
    if extra:
        parts.append(json.dumps(extra, sort_keys=True, ensure_ascii=False))
    return _hash_text("\n".join(parts), 24)


def _image_metadata(path: Path) -> dict:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return {
                "optimized_width": img.size[0],
                "optimized_height": img.size[1],
                "has_alpha": bool("A" in img.getbands() or "a" in img.getbands()),
            }
    except Exception:
        return {"optimized_width": None, "optimized_height": None, "has_alpha": None}


def _save_webp_from_source(source_abs: Path, out_abs: Path, texture_size: int, texture_quality: int) -> tuple[int | None, int | None, bool | None]:
    from PIL import Image

    with Image.open(source_abs) as img:
        img.load()
        source_size = img.size
        has_alpha = "A" in img.getbands() or "a" in img.getbands() or "transparency" in img.info
        if has_alpha:
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        scale = min(1.0, texture_size / max(source_size))
        if scale < 1.0:
            img = img.resize(
                (max(1, round(source_size[0] * scale)), max(1, round(source_size[1] * scale))),
                Image.Resampling.LANCZOS,
            )
        out_abs.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_abs.with_suffix(out_abs.suffix + ".tmp")
        img.save(tmp, "WEBP", quality=texture_quality, method=4)
        os.replace(tmp, out_abs)
        return img.size[0], img.size[1], has_alpha


def _optimize_texture(
    *,
    archive_root: Path,
    output_root: Path,
    package_path: str,
    role: str,
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> tuple[dict | None, dict | None]:
    if not package_path or _is_default_texture(package_path, role):
        return None, None
    resolved = _resolve_archive_texture(archive_root, package_path)
    if resolved is None:
        return None, {"code": "TextureMissing", "role": role, "source": package_path}
    source_abs, source_rel = resolved
    if not is_web_texture_candidate(source_rel):
        return None, {"code": "TextureSkippedHelper", "role": role, "source": source_rel}
    texture_hash = _texture_hash(f"archive:{source_rel}", [source_abs], texture_size, texture_quality)
    out_rel = _texture_cache_rel(texture_hash, texture_size)
    out_abs = output_root / out_rel
    rec = {
        "source": f"archive:{source_rel}",
        "source_rel": source_rel,
        "source_bytes": source_abs.stat().st_size,
        "optimized": out_rel,
        "optimized_bytes": out_abs.stat().st_size if out_abs.is_file() else 0,
        "hash": texture_hash,
        "status": "cached" if out_abs.is_file() else ("dry_run" if dry_run else "pending"),
        "source_width": None,
        "source_height": None,
        "optimized_width": None,
        "optimized_height": None,
        "has_alpha": None,
        "error": None,
    }
    try:
        from PIL import Image

        with Image.open(source_abs) as img:
            rec["source_width"], rec["source_height"] = img.size
    except Exception:
        pass
    if out_abs.is_file():
        rec.update(_image_metadata(out_abs))
        return rec, None
    if dry_run:
        return rec, None
    try:
        width, height, has_alpha = _save_webp_from_source(source_abs, out_abs, texture_size, texture_quality)
        rec.update(
            {
                "status": "optimized",
                "optimized_bytes": out_abs.stat().st_size,
                "optimized_width": width,
                "optimized_height": height,
                "has_alpha": has_alpha,
            }
        )
        return rec, None
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec, {"code": "TextureOptimizeFailed", "role": role, "source": source_rel, "error": rec["error"]}


def _sample_curve_value(keys: list[dict], t: float = 0.5) -> float:
    if not keys:
        return 1.0
    rows = sorted(keys, key=lambda row: float(row.get("Time") or 0.0))
    if t <= float(rows[0].get("Time") or 0.0):
        return float(rows[0].get("Value") or 0.0)
    for a, b in zip(rows, rows[1:]):
        at = float(a.get("Time") or 0.0)
        bt = float(b.get("Time") or at)
        if t <= bt:
            av = float(a.get("Value") or 0.0)
            bv = float(b.get("Value") or 0.0)
            u = 0.0 if math.isclose(at, bt) else (t - at) / (bt - at)
            return av * (1.0 - u) + bv * u
    return float(rows[-1].get("Value") or 0.0)


def _curve_rgb(archive_root: Path, curve_id: float | None) -> tuple[int, int, int] | None:
    if curve_id is None:
        return None
    idx = max(0, min(11, int(round(curve_id))))
    path = archive_root / "json" / "RSDragonwilds/Content/Materials/Character/CurveAtlases/ArmourCurves" / f"Curve_ArmourColor_{idx:02d}.json"
    if not path.is_file():
        return None
    try:
        obj = _archive_objects(_read_json(path))[0]
        props = obj.get("Properties") or {}
        rgb = [
            _sample_curve_value((props.get(key) or {}).get("Keys") or [], 0.5)
            for key in ("FloatCurves", "FloatCurves[1]", "FloatCurves[2]")
        ]
        return tuple(max(0, min(255, round(value * 255))) for value in rgb)  # type: ignore[return-value]
    except Exception:
        return None


def _label_rgb(label: str) -> tuple[int, int, int] | None:
    low = label.lower()
    for name, rgb in COLOR_NAME_RGB.items():
        if re.search(rf"\b{name}\b", low):
            return rgb
    return None


def _dye_rgb(archive_root: Path, label: str, curve_id: float | None) -> tuple[tuple[int, int, int], str] | tuple[None, str]:
    label_color = _label_rgb(label)
    curve_color = _curve_rgb(archive_root, curve_id)
    if label_color is not None:
        return label_color, "label_color_with_curve_diagnostic" if curve_color is not None else "label_color"
    if curve_color is not None:
        return curve_color, "curve_atlas"
    return None, "missing_color"


def _bake_dye_texture(
    *,
    archive_root: Path,
    output_root: Path,
    base_package: str,
    tint_package: str,
    label: str,
    curve_id: float | None,
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> tuple[dict | None, list[dict]]:
    diagnostics: list[dict] = []
    base = _resolve_archive_texture(archive_root, base_package)
    tint = _resolve_archive_texture(archive_root, tint_package)
    if base is None:
        return None, [{"code": "DyeBaseColorMissing", "source": base_package}]
    if tint is None:
        return None, [{"code": "DyeTintMaskMissing", "source": tint_package}]
    base_abs, base_rel = base
    tint_abs, tint_rel = tint
    dye_color, color_source = _dye_rgb(archive_root, label, curve_id)
    if dye_color is None:
        return None, [{"code": "DyeColorMissing", "curve_id": curve_id}]

    extra = {"role": "BaseColor", "label": label, "curve_id": curve_id, "dye_color": dye_color, "color_source": color_source}
    texture_hash = _texture_hash(f"generated:equipment-dye:{base_rel}:{tint_rel}", [base_abs, tint_abs], texture_size, texture_quality, extra)
    out_rel = _texture_cache_rel(texture_hash, texture_size)
    out_abs = output_root / out_rel
    rec = {
        "source": f"generated:equipment-dye:{base_rel}:{tint_rel}",
        "source_rel": base_rel,
        "source_bytes": base_abs.stat().st_size + tint_abs.stat().st_size,
        "optimized": out_rel,
        "optimized_bytes": out_abs.stat().st_size if out_abs.is_file() else 0,
        "hash": texture_hash,
        "status": "cached" if out_abs.is_file() else ("dry_run" if dry_run else "pending"),
        "generated_type": "equipment_dye_basecolor",
        "source_paths": [base_rel, tint_rel],
        "params": extra,
        "optimized_width": None,
        "optimized_height": None,
        "has_alpha": None,
        "error": None,
    }
    diagnostics.append({"code": "EquipmentDyeBaseColorGenerated", "curve_id": curve_id, "dye_color": dye_color, "color_source": color_source, "optimized": out_rel})
    if out_abs.is_file():
        rec.update(_image_metadata(out_abs))
        return rec, diagnostics
    if dry_run:
        return rec, diagnostics

    try:
        from PIL import Image

        with Image.open(base_abs) as base_img:
            base_img.load()
            base_img = base_img.convert("RGBA")
        with Image.open(tint_abs) as mask_img:
            mask_img.load()
            mask_img = mask_img.convert("RGBA").resize(base_img.size, Image.Resampling.LANCZOS)

        scale = min(1.0, texture_size / max(base_img.size))
        if scale < 1.0:
            next_size = (max(1, round(base_img.size[0] * scale)), max(1, round(base_img.size[1] * scale)))
            base_img = base_img.resize(next_size, Image.Resampling.LANCZOS)
            mask_img = mask_img.resize(next_size, Image.Resampling.LANCZOS)

        try:
            import numpy as np

            base_arr = np.asarray(base_img).astype("float32")
            mask_arr = np.asarray(mask_img).astype("float32")
            rgb = base_arr[:, :, :3]
            alpha = base_arr[:, :, 3:4]
            mask = np.max(mask_arr[:, :, :3], axis=2) / 255.0
            mask = np.where(mask_arr[:, :, 3] > 0, mask, 0.0)
            mask = np.clip((mask - 0.06) / 0.74, 0.0, 1.0)
            lum = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
            weighted = float((lum * np.maximum(mask, 0.001)).sum() / max(float(np.maximum(mask, 0.001).sum()), 1.0))
            shade = np.clip(lum / max(weighted, 1.0), 0.28, 1.95)
            target = np.array(dye_color, dtype="float32")
            tinted = np.clip(shade[:, :, None] * target[None, None, :] * 1.15, 0, 255)
            blend = np.clip(mask * 0.92, 0.0, 0.92)[:, :, None]
            out_rgb = rgb * (1.0 - blend) + tinted * blend
            out = np.concatenate([out_rgb, alpha], axis=2).clip(0, 255).astype("uint8")
            baked = Image.fromarray(out, "RGBA")
        except Exception:
            pixels = []
            dye = tuple(channel / 255.0 for channel in dye_color)
            for base_px, mask_px in zip(base_img.getdata(), mask_img.getdata()):
                w = max(mask_px[:3]) / 255.0 if mask_px[3] else 0.0
                w = max(0.0, min(1.0, (w - 0.06) / 0.74)) * 0.92
                lum = (base_px[0] * 0.2126 + base_px[1] * 0.7152 + base_px[2] * 0.0722) / 128.0
                tint_rgb = tuple(max(0, min(255, round(channel * lum * 255 * 1.15))) for channel in dye)
                pixels.append(tuple(round(base_px[i] * (1.0 - w) + tint_rgb[i] * w) for i in range(3)) + (base_px[3],))
            baked = Image.new("RGBA", base_img.size)
            baked.putdata(pixels)

        out_abs.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_abs.with_suffix(out_abs.suffix + ".tmp")
        baked.save(tmp, "WEBP", quality=texture_quality, method=6)
        os.replace(tmp, out_abs)
        rec.update(
            {
                "status": "optimized",
                "optimized_bytes": out_abs.stat().st_size,
                "optimized_width": baked.size[0],
                "optimized_height": baked.size[1],
                "has_alpha": True,
            }
        )
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
        diagnostics.append({"code": "DyeBakeFailed", "error": rec["error"]})
    return rec, diagnostics


def _texture_from_material(
    material: dict,
    *,
    archive_root: Path,
    output_root: Path,
    label: str,
    role: str,
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> tuple[dict | None, list[dict]]:
    textures = material.get("Textures") or {}
    parameters = material.get("Parameters") or {}
    package = _first_value(textures, ROLE_TEXTURE_KEYS[role])
    if not package:
        return None, []
    diagnostics: list[dict] = []
    if role == "BaseColor" and _first_switch(parameters, "UseDye") is True:
        tint = _first_value(textures, ("TintMask", "DiyngMask", "DyeingMask", "TintMask_VT"))
        curve_id = _first_scalar(parameters, "CurveID")
        if tint and not _is_default_texture(tint, "BaseColor"):
            rec, dye_diag = _bake_dye_texture(
                archive_root=archive_root,
                output_root=output_root,
                base_package=package,
                tint_package=tint,
                label=label,
                curve_id=curve_id,
                texture_size=texture_size,
                texture_quality=texture_quality,
                dry_run=dry_run,
            )
            diagnostics.extend(dye_diag)
            if rec is not None:
                return rec, diagnostics
    rec, diagnostic = _optimize_texture(
        archive_root=archive_root,
        output_root=output_root,
        package_path=package,
        role=role,
        texture_size=texture_size,
        texture_quality=texture_quality,
        dry_run=dry_run,
    )
    if diagnostic:
        diagnostics.append(diagnostic)
    return rec, diagnostics


def _relative_uri(from_dir: Path, to_file: Path) -> str:
    return os.path.relpath(to_file, from_dir).replace("\\", "/")


def _ensure_webp_texture(gltf: dict, variant_dir: Path, output_root: Path, texture_rel: str) -> int:
    image_idx = len(gltf.setdefault("images", []))
    image_uri = _relative_uri(variant_dir, output_root / texture_rel)
    gltf["images"].append(
        {
            "mimeType": "image/webp",
            "name": Path(texture_rel).name,
            "uri": image_uri,
        }
    )
    texture_idx = len(gltf.setdefault("textures", []))
    gltf["textures"].append(
        {
            "sampler": 0,
            "extensions": {"EXT_texture_webp": {"source": image_idx}},
        }
    )
    extensions = set(gltf.get("extensionsUsed") or [])
    extensions.add("EXT_texture_webp")
    gltf["extensionsUsed"] = sorted(extensions)
    return texture_idx


def _apply_material_properties(target: dict, source_material: dict) -> None:
    parameters = source_material.get("Parameters") or {}
    props = parameters.get("Properties") or {}
    base_overrides = props.get("BasePropertyOverrides") if isinstance(props, dict) else None
    if isinstance(base_overrides, dict):
        blend = str(base_overrides.get("BlendMode") or "")
        if "MASK" in blend.upper():
            target["alphaMode"] = "MASK"
            try:
                target["alphaCutoff"] = float(base_overrides.get("OpacityMaskClipValue") or target.get("alphaCutoff") or 0.3333)
            except (TypeError, ValueError):
                target["alphaCutoff"] = 0.3333
        if base_overrides.get("TwoSided") is True:
            target["doubleSided"] = True
    if props.get("TwoSided") is True:
        target["doubleSided"] = True


def _write_variant_gltf(
    *,
    base_gltf: Path,
    variant_gltf: Path,
    output_root: Path,
    material_overrides: list[dict],
    material_texture_records: list[dict[str, dict]],
    dry_run: bool,
) -> tuple[list[str], list[dict]]:
    data = copy.deepcopy(_read_json(base_gltf))
    variant_dir = variant_gltf.parent
    diagnostics: list[dict] = []
    material_names: list[str] = []

    for buffer in data.get("buffers") or []:
        uri = str(buffer.get("uri") or "")
        if uri:
            buffer["uri"] = _relative_uri(variant_dir, base_gltf.parent / uri)
    for image in data.get("images") or []:
        uri = str(image.get("uri") or "")
        if uri:
            image["uri"] = _relative_uri(variant_dir, base_gltf.parent / uri)

    materials = data.setdefault("materials", [])
    for idx, override in enumerate(material_overrides):
        if idx >= len(materials):
            diagnostics.append({"code": "MaterialSlotMissing", "slot_index": idx, "material": override.get("name")})
            continue
        target = materials[idx]
        mat_name = str(override.get("name") or target.get("name") or f"Material {idx + 1}")
        target["name"] = mat_name
        material_names.append(mat_name)
        pbr = target.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorFactor"] = [1, 1, 1, 1]
        _apply_material_properties(target, override)

        records = material_texture_records[idx] if idx < len(material_texture_records) else {}
        if records.get("BaseColor"):
            tex_idx = _ensure_webp_texture(data, variant_dir, output_root, records["BaseColor"]["optimized"])
            pbr["baseColorTexture"] = {"index": tex_idx}
        if records.get("ORM"):
            tex_idx = _ensure_webp_texture(data, variant_dir, output_root, records["ORM"]["optimized"])
            pbr["metallicRoughnessTexture"] = {"index": tex_idx}
        if records.get("Normal"):
            tex_idx = _ensure_webp_texture(data, variant_dir, output_root, records["Normal"]["optimized"])
            target["normalTexture"] = {"index": tex_idx}

    if not dry_run:
        variant_gltf.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(variant_gltf, data)
    return material_names, diagnostics


def _iter_equipment_mesh_data(archive_root: Path) -> list[Path]:
    root = archive_root / "json" / PLAYER_EQUIPMENT_REL
    if not root.is_dir():
        return []
    return sorted(root.rglob("*MeshData*.json"), key=lambda path: path.as_posix().lower())


def _mesh_data_record(path: Path, archive_root: Path) -> dict | None:
    objects = _archive_objects(_read_json(path))
    obj = next((row for row in objects if row.get("Type") == "WearableEquipmentMeshData"), None)
    if not obj:
        obj = objects[0] if objects else None
    if not obj:
        return None
    props = obj.get("Properties") or {}
    mesh = props.get("SoftSkeletalMesh") or props.get("SoftStaticMesh") or {}
    mesh_path = mesh.get("AssetPathName") if isinstance(mesh, dict) else None
    if not mesh_path:
        return None
    mats = [
        row.get("AssetPathName")
        for row in (props.get("Materials") or [])
        if isinstance(row, dict) and row.get("AssetPathName")
    ]
    rel = path.resolve().relative_to((archive_root / "json").resolve()).as_posix()
    return {
        "name": str(obj.get("Name") or path.stem),
        "stem": path.stem,
        "mesh_asset": str(mesh_path),
        "materials": [str(mat) for mat in mats],
        "archive_json_path": rel,
    }


def _load_model_index(path: Path) -> dict[str, dict]:
    data = _read_json(path)
    out: dict[str, dict] = {}
    for row in data.get("models") or []:
        if isinstance(row, dict) and row.get("path") and row.get("id"):
            out[str(row["path"]).replace("\\", "/")] = row
    return out


def _selected_records(records: list[tuple[Path, dict]], mode: str, limit: int | None) -> list[tuple[Path, dict]]:
    if mode == "none":
        return []
    filtered = [
        (path, rec)
        for path, rec in records
        if rec.get("materials")
    ]
    if mode == "smoke":
        filtered = [
            row for row in filtered
            if any(marker in str(row[1].get("mesh_asset") or "") for marker in ("Cape_01", "CapeOfAccomplishment_01"))
        ]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def _load_existing_manifest_textures(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    textures = data.get("textures") if isinstance(data, dict) else None
    return {str(key): row for key, row in textures.items() if isinstance(row, dict)} if isinstance(textures, dict) else {}


def _update_manifest(output_root: Path, texture_records: dict[str, dict], dry_run: bool) -> None:
    path = output_root / "WebAssetManifest.json"
    if dry_run or not path.is_file():
        return
    data = _read_json(path)
    textures = data.setdefault("textures", {})
    textures.update(texture_records)
    data["equipment_variants"] = "website/equipment-variants.json"
    data["equipment_variants_updated_utc"] = _now_utc()
    _write_json_atomic(path, data)


def _scan_file_counts(root: Path) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    if not root.is_dir():
        return counts
    for path in root.rglob("*"):
        if path.is_file():
            ext = path.suffix.lower() or "<none>"
            row = counts.setdefault(ext, {"count": 0, "bytes": 0})
            row["count"] += 1
            row["bytes"] += path.stat().st_size
    return dict(sorted(counts.items(), key=lambda item: (-item[1]["bytes"], item[0])))


def _large_files(root: Path, threshold_bytes: int) -> list[dict]:
    if not root.is_dir():
        return []
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_size >= threshold_bytes:
            rows.append({"path": path.resolve().relative_to(root.resolve()).as_posix(), "bytes": path.stat().st_size})
    rows.sort(key=lambda row: row["bytes"], reverse=True)
    return rows


def _update_size_report(output_root: Path, texture_records: dict[str, dict], dry_run: bool) -> None:
    if dry_run:
        return
    path = output_root / "WebAssetSizeReport.json"
    existing = _read_json(path) if path.is_file() else {}
    manifest_textures = _load_existing_manifest_textures(output_root / "WebAssetManifest.json")
    all_textures = {**manifest_textures, **texture_records}
    models_root = output_root / "models"
    report = {
        **existing,
        "updated_utc": _now_utc(),
        "source_texture_bytes": sum(int(row.get("source_bytes") or 0) for row in all_textures.values()),
        "optimized_texture_bytes": sum(int(row.get("optimized_bytes") or 0) for row in all_textures.values()),
        "model_bytes": sum(path.stat().st_size for path in models_root.rglob("*") if path.is_file()) if models_root.is_dir() else 0,
        "total_web_asset_bytes": sum(path.stat().st_size for path in output_root.rglob("*") if path.is_file()) if output_root.is_dir() else 0,
        "file_counts": _scan_file_counts(output_root),
        "output_files_over_50_mib": _large_files(output_root, 50 * 1024 * 1024),
        "output_files_over_100_mib": _large_files(output_root, 100 * 1024 * 1024),
        "equipment_variants_included": True,
    }
    _write_json_atomic(path, report)


def generate_equipment_variants(
    *,
    repo_root: Path,
    dataset_version: str,
    archive_root: Path,
    model_index_path: Path,
    output_path: Path,
    mode: str,
    texture_size: int,
    texture_quality: int,
    limit: int | None,
    dry_run: bool,
) -> dict:
    source_root = repo_root / dataset_version
    webassets_root = source_root / "WebAssets"
    if mode != "none":
        if not (archive_root / "json").is_dir() or not (archive_root / "textures").is_dir():
            raise SystemExit(f"Archive root must contain json/ and textures/: {archive_root}")
        if not webassets_root.is_dir():
            raise SystemExit(f"WebAssets root not found: {webassets_root}")
    model_by_path = _load_model_index(model_index_path)
    item_names = _load_item_names(archive_root) if archive_root.is_dir() else {}

    records: list[tuple[Path, dict]] = []
    for path in _iter_equipment_mesh_data(archive_root):
        rec = _mesh_data_record(path, archive_root)
        if rec:
            records.append((path, rec))
    selected = _selected_records(records, mode, limit)

    by_model: dict[str, dict] = {}
    texture_records: dict[str, dict] = {}
    variant_count = 0
    skipped: list[dict] = []

    for mesh_data_path, rec in selected:
        model_path = _mesh_asset_to_model_path(str(rec["mesh_asset"]))
        if not model_path:
            skipped.append({"mesh_data": rec["archive_json_path"], "reason": "mesh_path_unresolved", "mesh": rec["mesh_asset"]})
            continue
        base_model = model_by_path.get(model_path)
        if not base_model:
            skipped.append({"mesh_data": rec["archive_json_path"], "reason": "base_model_missing", "model_path": model_path})
            continue
        base_gltf_rel = str(base_model.get("gltfPath") or "")
        base_asset_dir = str(base_model.get("assetDir") or "")
        if not base_gltf_rel or not base_asset_dir:
            skipped.append({"mesh_data": rec["archive_json_path"], "reason": "base_model_no_gltf", "model_path": model_path})
            continue
        base_gltf = webassets_root / base_gltf_rel
        if not base_gltf.is_file():
            skipped.append({"mesh_data": rec["archive_json_path"], "reason": "base_gltf_missing", "gltf": base_gltf_rel})
            continue

        label = _display_label(mesh_data_path.stem, item_names)
        slot = _slot_from_mesh_data(mesh_data_path)
        sex = _sex_from_mesh_data(mesh_data_path.stem, model_path)
        material_overrides: list[dict] = []
        material_texture_records: list[dict[str, dict]] = []
        material_json_paths: list[str] = []
        diagnostics: list[dict] = []
        variant_optimized_textures: set[str] = set()

        for material_asset_path in rec["materials"]:
            resolved = _first_existing_json(archive_root, material_asset_path)
            if resolved is None:
                diagnostics.append({"code": "MaterialJsonMissing", "source": material_asset_path})
                continue
            material_json, material_rel = resolved
            try:
                material, _texture_rels = _convert_archive_material(material_json, archive_root)
            except Exception as e:
                diagnostics.append({"code": "MaterialConvertFailed", "source": material_asset_path, "error": f"{type(e).__name__}: {e}"})
                continue
            material["name"] = _material_name_from_archive_json(material_json)
            material_json_paths.append(f"{material_rel}.json")
            role_records: dict[str, dict] = {}
            for role in ("BaseColor", "Normal", "ORM"):
                tex_rec, tex_diagnostics = _texture_from_material(
                    material,
                    archive_root=archive_root,
                    output_root=webassets_root,
                    label=label,
                    role=role,
                    texture_size=texture_size,
                    texture_quality=texture_quality,
                    dry_run=dry_run,
                )
                diagnostics.extend(tex_diagnostics)
                if tex_rec and tex_rec.get("status") != "failed":
                    role_records[role] = tex_rec
                    texture_records[str(tex_rec["source"])] = tex_rec
                    if isinstance(tex_rec.get("optimized"), str):
                        variant_optimized_textures.add(str(tex_rec["optimized"]))
            material_overrides.append(material)
            material_texture_records.append(role_records)

        if not material_overrides:
            skipped.append({"mesh_data": rec["archive_json_path"], "reason": "no_material_overrides_resolved"})
            continue

        variant_hash = _hash_text(
            "\n".join([VARIANT_GLTF_REVISION, rec["archive_json_path"], model_path, "\n".join(rec["materials"])]),
            12,
        )
        variant_slug = f"{_safe_slug(label)}-{variant_hash}"
        variant_gltf_rel = f"{base_asset_dir}/variants/{variant_slug}/model.gltf"
        variant_gltf = webassets_root / variant_gltf_rel
        material_names, gltf_diagnostics = _write_variant_gltf(
            base_gltf=base_gltf,
            variant_gltf=variant_gltf,
            output_root=webassets_root,
            material_overrides=material_overrides,
            material_texture_records=material_texture_records,
            dry_run=dry_run,
        )
        diagnostics.extend(gltf_diagnostics)
        missing = [row for row in diagnostics if str(row.get("code") or "").lower().endswith("missing")]
        variant = {
            "id": f"EV:{variant_hash}",
            "label": label,
            "slot": slot,
            "sex": sex,
            "meshDataPath": rec["archive_json_path"],
            "baseModelId": base_model["id"],
            "baseModelPath": model_path,
            "baseGltfPath": base_gltf_rel,
            "gltfPath": variant_gltf_rel,
            "materialOverridePaths": material_json_paths,
            "materialNames": material_names,
            "optimizedTextures": sorted(variant_optimized_textures),
            "missingTextureCount": len(missing),
            "diagnostics": diagnostics,
        }
        group = by_model.setdefault(
            str(base_model["id"]),
            {
                "modelId": base_model["id"],
                "path": model_path,
                "displayName": base_model.get("displayName") or base_model.get("name") or Path(model_path).stem,
                "baseGltfPath": base_gltf_rel,
                "variants": [],
            },
        )
        group["variants"].append(variant)
        variant_count += 1

    for group in by_model.values():
        group["variants"].sort(key=lambda row: (str(row.get("slot") or ""), str(row.get("sex") or ""), str(row.get("label") or "").lower()))

    output = {
        "schema": SCHEMA,
        "datasetVersion": dataset_version,
        "generatedUtc": _now_utc(),
        "mode": mode,
        "sourceArchiveRoot": archive_root.as_posix(),
        "sourceModelIndex": model_index_path.resolve().relative_to(repo_root.resolve()).as_posix(),
        "textureProfile": {"maxSize": texture_size, "quality": texture_quality, "format": "webp"},
        "baseModelCount": len(by_model),
        "variantCount": variant_count,
        "skippedCount": len(skipped),
        "byModel": dict(sorted(by_model.items(), key=lambda item: item[0])),
        "skipped": skipped[:250],
    }

    if not dry_run:
        _write_json_atomic(output_path, output)
        _update_manifest(webassets_root, texture_records, dry_run=dry_run)
        _update_size_report(webassets_root, texture_records, dry_run=dry_run)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate website equipment material variants from RSDWArchive mesh-data records.")
    parser.add_argument("--repo-root", type=Path, default=_repo_root())
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--model-index", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--mode", choices=("none", "smoke", "full"), default="smoke")
    parser.add_argument("--texture-size", type=int, default=1024)
    parser.add_argument("--texture-quality", type=int, default=75)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    archive_root = (args.archive_root or _default_archive_root(args.dataset_version)).resolve()
    model_index = (args.model_index or repo_root / "website" / "model-index.json").resolve()
    output = (args.output or repo_root / "website" / "equipment-variants.json").resolve()
    result = generate_equipment_variants(
        repo_root=repo_root,
        dataset_version=args.dataset_version,
        archive_root=archive_root,
        model_index_path=model_index,
        output_path=output,
        mode=args.mode,
        texture_size=args.texture_size,
        texture_quality=args.texture_quality,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    action = "would write" if args.dry_run else "wrote"
    print(
        f"{action} {result['variantCount']} equipment variant(s) across "
        f"{result['baseModelCount']} base model(s) to {output}"
    )
    if result["skippedCount"]:
        print(f"skipped {result['skippedCount']} equipment record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
