"""
Build website-ready shared glTF assets from the current RSDWModel inventories.

This is the web delivery path: it writes separate .gltf/.bin model files that
reference a shared WebP texture cache instead of embedding duplicate textures
inside thousands of standalone GLBs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from WebTextureRules import is_web_texture_candidate


PROGRESS_SCHEMA = "RSDWModel.WebAssetProgress.v1"
MANIFEST_SCHEMA = "RSDWModel.WebAssetManifest.v1"
SIZE_REPORT_SCHEMA = "RSDWModel.WebAssetSizeReport.v1"
TEXTURE_EXTENSIONS = (".png", ".tga", ".dds", ".jpg", ".jpeg", ".exr", ".bmp", ".hdr", ".webp")
WEB_ASSET_EXPORT_REVISION = "material-v6"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _default_source_root() -> Path:
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for child in _repo_root().iterdir():
        if not child.is_dir():
            continue
        parts = child.name.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            candidates.append((tuple(int(part) for part in parts), child))
    if not candidates:
        return _repo_root() / "0.11.2.2"
    return max(candidates, key=lambda item: item[0])[1]


def _default_blender() -> Path:
    return _repo_root() / "blender-5.0.0-windows-x64" / "blender.exe"


def _default_worker() -> Path:
    return Path(__file__).resolve().parent / "BuildWebAssetWorker.py"


def _inventory_path(source_root: Path, kind: str) -> Path:
    return source_root / "ModelData" / f"{kind}_Data.json"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _write_json_atomic(path: Path, data: dict, *, attempts: int = 40, delay_s: float = 0.25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    last_error: PermissionError | None = None
    for _ in range(max(1, attempts)):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(delay_s)
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    if last_error is not None:
        raise last_error


def _rel_to_repo(p: Path) -> str:
    try:
        return p.resolve().relative_to(_repo_root().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def _rel_to_root(root: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def _hash_text(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _asset_dir(output_root: Path, kind: str, entry: dict) -> Path:
    stem = Path(entry["path"]).stem
    hash_seed = "\n".join([WEB_ASSET_EXPORT_REVISION, kind, entry["path"]])
    return output_root / "models" / kind / f"{stem}-{_hash_text(hash_seed, 10)}"


def _texture_cache_rel(texture_hash: str, texture_size: int) -> str:
    return f"textures/webp_{texture_size}/{texture_hash}.webp"


def _texture_hash(source_root: Path, rel_path: str, texture_size: int, texture_quality: int) -> str:
    src = source_root / rel_path
    stat = src.stat()
    payload = "\n".join(
        [
            rel_path.replace("\\", "/"),
            str(stat.st_size),
            str(stat.st_mtime_ns),
            str(texture_size),
            str(texture_quality),
            "webp-alpha-v3",
        ]
    )
    return _hash_text(payload, 24)


# ---------------------------------------------------------------------------
# Unreal package path helpers, mirrored from BuildGLBWorker without importing bpy.
# ---------------------------------------------------------------------------

_PLUGIN_ROOT_REL = "RSDragonwilds/Plugins/GameFeatures"
_GAME_ROOT_REL = "RSDragonwilds/Content"


def _strip_trailing_asset_suffix(package_path: str) -> str:
    s = package_path.replace("\\", "/").strip()
    if not s:
        return s
    if "." in s:
        head, tail = s.rsplit(".", 1)
        if head.rsplit("/", 1)[-1] == tail:
            return head
    return s


def _package_path_to_disk_relatives(package_path: str) -> list[str]:
    s = (package_path or "").replace("\\", "/").strip()
    if not s:
        return []
    if s.startswith(("RSDragonwilds/", "RSDragonwilds\\")):
        return [s]
    if s.startswith("/Game/"):
        return [f"{_GAME_ROOT_REL}/{s[len('/Game/'):]}"]
    if s.startswith("/"):
        without_lead = s[1:]
        if "/" in without_lead:
            plugin, rest = without_lead.split("/", 1)
            return [
                f"{_PLUGIN_ROOT_REL}/{plugin}/Content/{rest}",
                without_lead,
            ]
        return [without_lead]
    return [s]


def _resolve_texture_on_disk(source_root: Path, package_path: str) -> Path | None:
    pkg = _strip_trailing_asset_suffix(package_path)
    if not pkg:
        return None
    for rel in _package_path_to_disk_relatives(pkg):
        base = source_root / rel
        for ext in TEXTURE_EXTENSIONS:
            candidate = base.with_suffix(ext)
            if candidate.is_file():
                return candidate
        parent = base.parent
        stem = base.name
        if parent.is_dir():
            for ext in TEXTURE_EXTENSIONS:
                candidate = parent / f"{stem}{ext}"
                if candidate.is_file():
                    return candidate
            for ext in TEXTURE_EXTENSIONS:
                candidate = parent / f"{stem}_0{ext}"
                if candidate.is_file():
                    return candidate
    return None


def _load_inventory(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"Inventory has no entries list: {path}")
    return entries


def _entry_texture_rels(source_root: Path, entry: dict) -> list[str]:
    out: set[str] = set()

    for rel in (entry.get("MaterialsHybrid") or {}).get("texture_image_paths") or []:
        p = source_root / rel
        if p.is_file():
            rel_norm = p.resolve().relative_to(source_root.resolve()).as_posix()
            if is_web_texture_candidate(rel_norm):
                out.add(rel_norm)

    materials_block = entry.get("Materials") or {}
    material_rels = (
        materials_block.get("material_json_paths")
        or materials_block.get("material_instance_json_paths")
        or []
    )
    for mat_rel in material_rels:
        mat_path = source_root / mat_rel
        if not mat_path.is_file():
            continue
        try:
            data = json.loads(mat_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        textures = data.get("Textures") if isinstance(data, dict) else None
        if not isinstance(textures, dict):
            continue
        for value in textures.values():
            if not isinstance(value, str) or not value.strip():
                continue
            tex_path = _resolve_texture_on_disk(source_root, value)
            if tex_path is not None:
                rel_norm = tex_path.resolve().relative_to(source_root.resolve()).as_posix()
                if is_web_texture_candidate(rel_norm):
                    out.add(rel_norm)

    return sorted(out)


def _select_entries(
    entries_by_kind: dict[str, list[dict]],
    *,
    only_substr: str | None,
    only_paths: set[str] | None,
    limit_per_kind: int | None,
    prefer_textured: bool,
    source_root: Path,
) -> list[tuple[str, dict]]:
    selected: list[tuple[str, dict]] = []
    for kind, entries in entries_by_kind.items():
        filtered = entries
        if only_substr:
            low = only_substr.lower()
            filtered = [
                e for e in filtered
                if low in e.get("name", "").lower() or low in e.get("path", "").lower()
            ]
        if only_paths is not None:
            filtered = [e for e in filtered if e.get("path", "") in only_paths]
        if prefer_textured and limit_per_kind is not None and limit_per_kind >= 0:
            textured: list[dict] = []
            fallback: list[dict] = []
            for entry in filtered:
                if _entry_texture_rels(source_root, entry):
                    textured.append(entry)
                    if len(textured) >= limit_per_kind:
                        break
                elif len(fallback) < limit_per_kind:
                    fallback.append(entry)
            filtered = textured + fallback[: max(0, limit_per_kind - len(textured))]
        elif prefer_textured:
            filtered = sorted(
                filtered,
                key=lambda e: 0 if _entry_texture_rels(source_root, e) else 1,
            )
        if limit_per_kind is not None and limit_per_kind >= 0:
            filtered = filtered[:limit_per_kind]
        selected.extend((kind, e) for e in filtered)
    return selected


# ---------------------------------------------------------------------------
# Texture cache
# ---------------------------------------------------------------------------

def _optimize_one_texture(
    *,
    source_root: Path,
    output_root: Path,
    rel_path: str,
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> dict:
    src = source_root / rel_path
    texture_hash = _texture_hash(source_root, rel_path, texture_size, texture_quality)
    out_rel = _texture_cache_rel(texture_hash, texture_size)
    out_abs = output_root / out_rel
    rec = {
        "source": rel_path,
        "source_bytes": src.stat().st_size,
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
    if out_abs.is_file():
        try:
            from PIL import Image
            with Image.open(out_abs) as saved:
                rec.update(
                    {
                        "optimized_bytes": out_abs.stat().st_size,
                        "optimized_width": saved.size[0],
                        "optimized_height": saved.size[1],
                        "has_alpha": bool("A" in saved.getbands() or "a" in saved.getbands()),
                    }
                )
        except Exception:
            pass
        return rec
    if dry_run:
        return rec

    try:
        from PIL import Image
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = f"Pillow is required for WebP texture generation: {e}"
        return rec

    try:
        with Image.open(src) as img:
            img.load()
            source_width, source_height = img.size
            source_has_alpha_channel = (
                "A" in img.getbands()
                or "a" in img.getbands()
                or "transparency" in img.info
            )
            if source_has_alpha_channel:
                img = img.convert("RGBA")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            scale = min(1.0, texture_size / max(source_width, source_height))
            if scale < 1.0:
                new_size = (
                    max(1, round(source_width * scale)),
                    max(1, round(source_height * scale)),
                )
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            out_abs.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_abs.with_suffix(out_abs.suffix + ".tmp")
            img.save(tmp, format="WEBP", quality=texture_quality, method=4)
            os.replace(tmp, out_abs)
            with Image.open(out_abs) as saved:
                output_has_alpha = "A" in saved.getbands() or "a" in saved.getbands()
            rec.update(
                {
                    "status": "optimized",
                    "optimized_bytes": out_abs.stat().st_size,
                    "source_width": source_width,
                    "source_height": source_height,
                    "optimized_width": img.size[0],
                    "optimized_height": img.size[1],
                    "source_alpha_channel": bool(source_has_alpha_channel),
                    "has_alpha": bool(output_has_alpha),
                }
            )
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
        try:
            out_abs.with_suffix(out_abs.suffix + ".tmp").unlink(missing_ok=True)
        except Exception:
            pass
    return rec


def _prepare_texture_cache(
    *,
    source_root: Path,
    output_root: Path,
    selected: list[tuple[str, dict]],
    progress: "ProgressManifest | None",
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> tuple[dict[str, dict], dict[str, list[str]]]:
    entry_textures: dict[str, list[str]] = {}
    needed: set[str] = set()
    for kind, entry in selected:
        key = _progress_key(kind, entry)
        rels = set(_entry_texture_rels(source_root, entry))
        if progress is not None:
            previous = progress.get(key) or {}
            previous_rels = []
            previous_rels.extend(previous.get("source_textures") or [])
            previous_rels.extend(previous.get("used_textures") or [])
            previous_rels.extend(previous.get("missing_textures") or [])
            for rel in previous_rels:
                if not isinstance(rel, str):
                    continue
                rel_norm = rel.replace("\\", "/")
                if is_web_texture_candidate(rel_norm) and (source_root / rel_norm).is_file():
                    rels.add(rel_norm)
        rels = sorted(rels)
        entry_textures[key] = rels
        needed.update(rels)

    _log(f"Texture cache: {len(needed)} unique source texture(s)")
    texture_records: dict[str, dict] = {}
    for idx, rel in enumerate(sorted(needed), start=1):
        rec = _optimize_one_texture(
            source_root=source_root,
            output_root=output_root,
            rel_path=rel,
            texture_size=texture_size,
            texture_quality=texture_quality,
            dry_run=dry_run,
        )
        texture_records[rel] = rec
        if idx % 50 == 0 or rec.get("status") == "failed":
            _log(f"  textures {idx}/{len(needed)}  {rec['status']}  {rel}")
    return texture_records, entry_textures


# ---------------------------------------------------------------------------
# Generated material texture bakes
# ---------------------------------------------------------------------------

MI_SLOT_ROLES = {
    "basecolor": "BaseColor",
    "basecolor map": "BaseColor",
    "basecolor array": "BaseColor",
    "basecolor_vt": "BaseColor",
    "basecolor vt": "BaseColor",
    "iriscolor": "BaseColor",
    "scleracolor": "BaseColor",
    "pm_diffuse": "BaseColor",
    "diffuse": "BaseColor",
    "albedo": "BaseColor",
    "normal": "Normal",
    "normal map": "Normal",
    "normal array": "Normal",
    "normal map array": "Normal",
    "normal_vt": "Normal",
    "normal vt": "Normal",
    "pm_normals": "Normal",
    "normalmap": "Normal",
    "metallic": "Metallic",
    "pm_metallic": "Metallic",
    "roughness": "Roughness",
    "pm_roughness": "Roughness",
    "emissive": "Emission",
    "emission": "Emission",
    "pm_emissive": "Emission",
    "ao": "AO",
    "ambientocclusion": "AO",
    "orm": "ORM",
    "orm map": "ORM",
    "orm_vt": "ORM",
    "orm vt": "ORM",
    "pm_specularmasks": "ORM",
    "specularmasks": "ORM",
    "specular masks": "ORM",
    "occlusionroughnessmetal": "ORM",
    "emissive_vt": "Emission",
    "emissive vt": "Emission",
}


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


def _first_switch(parameters: dict, *names: str) -> bool | None:
    switches = (parameters.get("Switches") or {}) if isinstance(parameters, dict) else {}
    normalized = {_norm_key(key): value for key, value in switches.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        low = str(value).strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
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


def _is_default_base_texture(package_path: str) -> bool:
    low = package_path.replace("\\", "/").lower()
    defaults = (
        "/defaulttextures/t_default_white_d",
        "/enginematerials/defaultdiffuse",
        "/enginematerials/t_default_basecolor",
        "/engineresources/defaulttexture",
        "/character/defaultvt/t_defaultvt_d",
    )
    return any(marker in low for marker in defaults)


def _is_default_texture_for_role(package_path: str, role: str) -> bool:
    low = package_path.replace("\\", "/").lower()
    common = (
        "/engineresources/defaulttexture",
        "/enginematerials/defaultdiffuse",
    )
    if any(marker in low for marker in common):
        return True
    if role == "BaseColor":
        return _is_default_base_texture(package_path)
    if role == "Normal":
        return any(
            marker in low
            for marker in (
                "/defaulttextures/t_default_n",
                "/defaulttextures/t_default_na",
                "/character/defaultvt/t_defaultvt_n",
            )
        )
    if role == "ORM":
        return any(
            marker in low
            for marker in (
                "/defaulttextures/t_default_orm",
                "/character/defaultvt/t_defaultvt_orm",
            )
        )
    if role == "Emission":
        return any(
            marker in low
            for marker in (
                "/defaulttextures/t_default_white_d",
                "/defaulttextures/t_default_gray_d",
                "/defaulttextures/t_default_linear_gray",
                "/character/defaultvt/t_defaultvt_e",
                "/character/defaultvt/t_defaultvt_d",
            )
        )
    return False


def _role_for_texture_slot(slot_name: str) -> str | None:
    low = slot_name.lower()
    role = MI_SLOT_ROLES.get(low)
    if role is not None:
        return role
    normalized = low.replace("_", " ")
    role = MI_SLOT_ROLES.get(normalized)
    if role is not None:
        return role
    compact = _norm_key(low)
    for key, value in MI_SLOT_ROLES.items():
        if _norm_key(key) == compact:
            return value
    return None


def _texture_candidate_score(slot_name: str, role: str, package_path: str, parameters: dict) -> int:
    slot = _norm_key(slot_name)
    score = 0
    if not _is_default_texture_for_role(package_path, role):
        score += 100

    use_vt = _first_switch(parameters, "UseVT", "Use VT")
    if "vt" in slot:
        score += 8 if use_vt else -8

    if role == "BaseColor":
        if slot in {"basecolormap", "basecolor", "basecolorvt"}:
            score += 20
        elif slot in {"pmdiffuse", "diffuse", "albedo"}:
            score += 18
        elif slot == "scleracolor":
            score += 14
        elif slot == "iriscolor":
            score += 10
    elif role == "Normal":
        if slot in {"normal", "normalmap", "normalvt"}:
            score += 10
        elif slot == "pmnormals":
            score += 8
    elif role == "ORM":
        if slot in {"orm", "ormmap", "ormvt"}:
            score += 10
        elif slot in {"pmspecularmasks", "specularmasks"}:
            score += 8
    elif role == "Emission":
        if slot in {"emissive", "emission", "emissivevt"}:
            score += 10
        elif slot == "pmemissive":
            score += 8
    return score


def _best_role_package(textures: dict, parameters: dict, role: str) -> str | None:
    best: tuple[int, int, str] | None = None
    for index, (slot_name, pkg_value) in enumerate(textures.items()):
        if not isinstance(pkg_value, str) or not pkg_value.strip():
            continue
        if _role_for_texture_slot(str(slot_name)) != role:
            continue
        if _is_default_texture_for_role(pkg_value, role):
            continue
        score = _texture_candidate_score(str(slot_name), role, pkg_value, parameters)
        if best is None or (score, -index) > (best[0], -best[1]):
            best = (score, index, pkg_value)
    return best[2] if best is not None else None


def _resolve_source_texture(source_root: Path, package_path: str | None) -> tuple[Path, str] | None:
    if not package_path:
        return None
    path = _resolve_texture_on_disk(source_root, package_path)
    if path is None or not path.is_file():
        return None
    return path, _rel_to_root(source_root, path)


def _generated_texture_hash(
    *,
    source_root: Path,
    label: str,
    sources: list[Path],
    params: dict,
    texture_size: int,
    texture_quality: int,
) -> str:
    parts = [
        WEB_ASSET_EXPORT_REVISION,
        label,
        str(texture_size),
        str(texture_quality),
        json.dumps(params, sort_keys=True),
    ]
    for path in sources:
        stat = path.stat()
        parts.extend([_rel_to_root(source_root, path), str(stat.st_size), str(stat.st_mtime_ns)])
    return _hash_text("\n".join(parts), 24)


def _generated_texture_rel(texture_hash: str, texture_size: int) -> str:
    return f"textures/webp_{texture_size}/{texture_hash}.webp"


def _resize_max(img, max_size: int):
    from PIL import Image

    if max(img.size) <= max_size:
        return img.copy()
    scale = max_size / max(img.size)
    size = (max(1, round(img.size[0] * scale)), max(1, round(img.size[1] * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _apply_rgb_adjust(
    img,
    *,
    tint: tuple[float, float, float, float] | None,
    brightness: float | None,
):
    from PIL import Image

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
    return Image.merge("RGBA", (*adjusted, channels[3]))


def _shift_hue(img, amount: float | None):
    from PIL import Image

    if amount is None:
        return img
    shift = int((amount % 1.0) * 255)
    if shift == 0:
        return img
    alpha = img.getchannel("A")
    hsv = img.convert("RGB").convert("HSV")
    h, s, v = hsv.split()
    h = h.point(lambda px: (px + shift) % 255)
    rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
    rgb.putalpha(alpha)
    return rgb


def _save_generated_webp(img, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    img.save(tmp, format="WEBP", quality=quality, method=4)
    os.replace(tmp, path)


def _record_generated_texture(
    *,
    source_root: Path,
    output_root: Path,
    source_label: str,
    out_abs: Path,
    texture_hash: str,
    source_paths: list[Path],
    source_rels: list[str],
    generated_type: str,
) -> dict:
    try:
        from PIL import Image
        with Image.open(out_abs) as img:
            width, height = img.size
            has_alpha = "A" in img.getbands() or "a" in img.getbands()
    except Exception:
        width = height = None
        has_alpha = None
    return {
        "source": source_label,
        "source_textures": source_rels,
        "source_bytes": sum(path.stat().st_size for path in source_paths if path.is_file()),
        "optimized": _rel_to_root(output_root, out_abs),
        "optimized_bytes": out_abs.stat().st_size if out_abs.is_file() else 0,
        "hash": texture_hash,
        "status": "generated",
        "generated_type": generated_type,
        "optimized_width": width,
        "optimized_height": height,
        "has_alpha": has_alpha,
        "error": None,
    }


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


def _eye_composite_override(
    *,
    mat_rel: str,
    mat_path: Path,
    textures: dict,
    parameters: dict,
    source_root: Path,
    output_root: Path,
    texture_size: int,
    texture_quality: int,
) -> dict | None:
    iris_pkg = _first_texture(textures, "IrisColor")
    sclera_pkg = _first_texture(textures, "ScleraColor")
    if not iris_pkg or not sclera_pkg:
        return None

    iris_resolved = _resolve_source_texture(source_root, iris_pkg)
    sclera_resolved = _resolve_source_texture(source_root, sclera_pkg)
    if iris_resolved is None or sclera_resolved is None:
        diagnostics = [{"code": "EyeCompositeSourceMissing", "iris": iris_pkg, "sclera": sclera_pkg}]
        return {"textures": {}, "diagnostics": diagnostics, "records": []}

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
    texture_hash = _generated_texture_hash(
        source_root=source_root,
        label=f"eye:{mat_rel}",
        sources=[iris_abs, sclera_abs],
        params=params,
        texture_size=texture_size,
        texture_quality=texture_quality,
    )
    out_abs = output_root / _generated_texture_rel(texture_hash, texture_size)
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

    source_label = f"generated:eye:{mat_rel}"
    rec = _record_generated_texture(
        source_root=source_root,
        output_root=output_root,
        source_label=source_label,
        out_abs=out_abs,
        texture_hash=texture_hash,
        source_paths=[iris_abs, sclera_abs],
        source_rels=[iris_rel, sclera_rel],
        generated_type="eye_composite",
    )
    return {
        "textures": {
            "BaseColor": {
                "role": "BaseColor",
                "path": str(out_abs.resolve()),
                "source": source_label,
                "params": ["EyeComposite", "Metallic(default0)", "Roughness(eye0.5)"],
                "suppress_scalar_roles": ["Metallic", "Roughness"],
            }
        },
        "diagnostics": [
            {
                "code": "EyeCompositeGenerated",
                "sources": [iris_rel, sclera_rel],
                "optimized": rec["optimized"],
            }
        ],
        "records": [rec],
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


def _dragon_basecolor_override(
    *,
    mat_rel: str,
    mat_path: Path,
    textures: dict,
    parameters: dict,
    source_root: Path,
    output_root: Path,
    texture_size: int,
    texture_quality: int,
) -> dict | None:
    rel_low = mat_rel.replace("\\", "/").lower()
    if "/npc/reptilian/" not in rel_low or "dragon" not in rel_low:
        return None
    has_character_shader_markers = any(
        _first_texture(textures, name)
        for name in ("CurveAtlas", "TintMask", "TintMask_VT", "EmissiveCurveAtlas")
    )
    if not has_character_shader_markers:
        return None

    diagnostics = _dragon_diagnostics(textures, parameters)
    pkg = _best_role_package(textures, parameters, "BaseColor")
    resolved = _resolve_source_texture(source_root, pkg)
    if resolved is None:
        diagnostics.append({"code": "DragonBaseColorSourceMissing", "source": pkg})
        return {"textures": {}, "diagnostics": diagnostics, "records": []}

    base_abs, base_rel = resolved
    glow = _first_color(parameters, "EmissiveTint", "GlowColor")
    emissive_strength = max(
        0.0,
        *[
            value
            for value in (
                _first_scalar(parameters, "BaseEmissiveMultiply", "Base Emissive Multiply"),
                _first_scalar(parameters, "BasicEmissive", "Basic Emissive"),
                _first_scalar(parameters, "Glow Intensity", "GlowIntensity"),
            )
            if value is not None
        ],
    )
    params = {
        "color_scale": _first_scalar(parameters, "Color_Scale") or 1.0,
        "lighting_correction": _first_scalar(parameters, "Lighting correction") or 1.0,
        "contrast": _first_scalar(parameters, "Contrast") or 1.0,
        "hue_shift": _first_scalar(parameters, "Hue Shift Percentage (S)") or 0.0,
        "emissive_tint": glow,
        "emissive_strength": emissive_strength,
    }
    texture_hash = _generated_texture_hash(
        source_root=source_root,
        label=f"dragon-base:{mat_rel}",
        sources=[base_abs],
        params=params,
        texture_size=texture_size,
        texture_quality=texture_quality,
    )
    out_abs = output_root / _generated_texture_rel(texture_hash, texture_size)
    if not out_abs.is_file():
        from PIL import Image, ImageEnhance

        with Image.open(base_abs) as img:
            baked = _resize_max(img.convert("RGBA"), texture_size)
        baked = _shift_hue(baked, params["hue_shift"])
        brightness = max(0.0, float(params["color_scale"]) * float(params["lighting_correction"]))
        baked = _apply_rgb_adjust(baked, tint=None, brightness=brightness)
        if glow is not None and emissive_strength > 0.0:
            emissive_factor = min(0.25, emissive_strength * 0.1)
            tint = (
                1.0 + max(0.0, glow[0]) * emissive_factor,
                1.0 + max(0.0, glow[1]) * emissive_factor,
                1.0 + max(0.0, glow[2]) * emissive_factor,
                1.0,
            )
            baked = _apply_rgb_adjust(baked, tint=tint, brightness=None)
        if float(params["contrast"]) != 1.0:
            baked = ImageEnhance.Contrast(baked).enhance(max(0.0, float(params["contrast"])))
        _save_generated_webp(baked, out_abs, texture_quality)

    source_label = f"generated:dragon-basecolor:{mat_rel}"
    rec = _record_generated_texture(
        source_root=source_root,
        output_root=output_root,
        source_label=source_label,
        out_abs=out_abs,
        texture_hash=texture_hash,
        source_paths=[base_abs],
        source_rels=[base_rel],
        generated_type="dragon_basecolor_bake",
    )
    return {
        "textures": {
            "BaseColor": {
                "role": "BaseColor",
                "path": str(out_abs.resolve()),
                "source": source_label,
                "params": ["DragonBaseColorBake"],
            }
        },
        "diagnostics": [
            {
                "code": "DragonBaseColorBakeGenerated",
                "source": base_rel,
                "optimized": rec["optimized"],
            },
            *diagnostics,
        ],
        "records": [rec],
    }


def _merge_material_override(current: dict | None, incoming: dict | None) -> dict | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    merged = {
        "textures": dict(current.get("textures") or {}),
        "diagnostics": list(current.get("diagnostics") or []),
        "records": list(current.get("records") or []),
    }
    merged["textures"].update(incoming.get("textures") or {})
    merged["diagnostics"].extend(incoming.get("diagnostics") or [])
    merged["records"].extend(incoming.get("records") or [])
    return merged


def _generated_override_for_material(
    *,
    mat_rel: str,
    source_root: Path,
    output_root: Path,
    texture_size: int,
    texture_quality: int,
) -> dict | None:
    mat_path = source_root / mat_rel
    if not mat_path.is_file():
        return None
    try:
        data = json.loads(mat_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    textures = data.get("Textures") or {}
    parameters = data.get("Parameters") or {}
    if not isinstance(textures, dict):
        return None
    if not isinstance(parameters, dict):
        parameters = {}

    override: dict | None = None
    override = _merge_material_override(
        override,
        _eye_composite_override(
            mat_rel=mat_rel,
            mat_path=mat_path,
            textures=textures,
            parameters=parameters,
            source_root=source_root,
            output_root=output_root,
            texture_size=texture_size,
            texture_quality=texture_quality,
        ),
    )
    override = _merge_material_override(
        override,
        _dragon_basecolor_override(
            mat_rel=mat_rel,
            mat_path=mat_path,
            textures=textures,
            parameters=parameters,
            source_root=source_root,
            output_root=output_root,
            texture_size=texture_size,
            texture_quality=texture_quality,
        ),
    )
    return override


def _prepare_generated_material_overrides(
    *,
    source_root: Path,
    output_root: Path,
    selected: list[tuple[str, dict]],
    texture_size: int,
    texture_quality: int,
    dry_run: bool,
) -> dict[str, dict[str, dict]]:
    if dry_run:
        return {}

    material_overrides: dict[str, dict] = {}
    entry_overrides: dict[str, dict[str, dict]] = {}
    material_to_entries: dict[str, set[str]] = {}

    for kind, entry in selected:
        key = _progress_key(kind, entry)
        material_rels = (
            (entry.get("Materials") or {}).get("material_json_paths")
            or (entry.get("Materials") or {}).get("material_instance_json_paths")
            or []
        )
        for mat_rel in material_rels:
            if not isinstance(mat_rel, str) or not mat_rel.strip():
                continue
            mat_key = mat_rel.replace("\\", "/")
            material_to_entries.setdefault(mat_key, set()).add(key)

    for idx, mat_rel in enumerate(sorted(material_to_entries), start=1):
        try:
            override = _generated_override_for_material(
                mat_rel=mat_rel,
                source_root=source_root,
                output_root=output_root,
                texture_size=texture_size,
                texture_quality=texture_quality,
            )
        except Exception as e:
            override = {
                "textures": {},
                "diagnostics": [
                    {
                        "code": "GeneratedMaterialBakeError",
                        "message": f"{type(e).__name__}: {e}",
                    }
                ],
                "records": [],
            }
        if override is None:
            continue
        material_overrides[mat_rel] = override
        for entry_key in material_to_entries[mat_rel]:
            entry_overrides.setdefault(entry_key, {})[mat_rel] = override
        if idx % 100 == 0:
            _log(f"  material bakes scanned {idx}/{len(material_to_entries)}")

    generated_count = sum(
        1
        for override in material_overrides.values()
        if override.get("records")
    )
    diagnostic_count = sum(len(override.get("diagnostics") or []) for override in material_overrides.values())
    _log(
        f"Material bakes: {generated_count} generated texture(s), "
        f"{diagnostic_count} diagnostic(s)"
    )
    return entry_overrides


# ---------------------------------------------------------------------------
# Progress and reports
# ---------------------------------------------------------------------------

def _progress_key(kind: str, entry: dict) -> str:
    return f"{kind}:{entry['path']}"


class ProgressManifest:
    def __init__(self, path: Path, source_root: Path, output_root: Path, texture_profile: dict) -> None:
        self.path = path
        self.source_root = source_root
        self.output_root = output_root
        self.texture_profile = texture_profile
        self.lock = threading.Lock()
        self.data: dict = {
            "manifest_schema": PROGRESS_SCHEMA,
            "started_utc": _now_utc(),
            "updated_utc": _now_utc(),
            "source_root": _rel_to_repo(source_root),
            "output_root": _rel_to_repo(output_root),
            "texture_profile": texture_profile,
            "totals": {"success": 0, "failed": 0, "skipped": 0, "pending": 0},
            "entries": {},
        }
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if (
                    isinstance(loaded, dict)
                    and loaded.get("manifest_schema") == PROGRESS_SCHEMA
                    and loaded.get("texture_profile") == texture_profile
                ):
                    self.data = loaded
                    self.data["started_utc"] = self.data.get("started_utc") or _now_utc()
            except Exception:
                pass
        self._recount()

    def _recount(self) -> None:
        totals = {"success": 0, "failed": 0, "skipped": 0, "pending": 0}
        for row in self.data.get("entries", {}).values():
            status = row.get("status", "pending")
            totals[status if status in totals else "pending"] += 1
        self.data["totals"] = totals

    def get(self, key: str) -> dict | None:
        return self.data["entries"].get(key)

    def update(self, key: str, record: dict) -> None:
        with self.lock:
            self.data["entries"][key] = record
            self.data["updated_utc"] = _now_utc()
            self._recount()
            self._write_atomic()

    def _write_atomic(self) -> None:
        _write_json_atomic(self.path, self.data)


def _should_skip(
    *,
    key: str,
    progress: ProgressManifest,
    output_root: Path,
    texture_profile: dict,
    required_optimized_textures: Iterable[str],
    force: bool,
) -> dict | None:
    if force:
        return None
    rec = progress.get(key)
    if not rec or rec.get("status") != "success":
        return None
    if rec.get("texture_profile") != texture_profile:
        return None
    if rec.get("export_revision") != WEB_ASSET_EXPORT_REVISION:
        return None
    for rel_name in ("gltf_path", "bin_path"):
        rel = rec.get(rel_name)
        if not rel or not (output_root / rel).is_file():
            return None
    rec_optimized = set(rec.get("optimized_textures", []))
    required_optimized = set(required_optimized_textures)
    if required_optimized - rec_optimized:
        return None
    for rel in rec_optimized:
        if not (output_root / rel).is_file():
            return None
    return rec


def _scan_file_counts(root: Path) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    if not root.is_dir():
        return counts
    for path in root.rglob("*"):
        if not path.is_file():
            continue
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
            rows.append(
                {
                    "path": path.resolve().relative_to(root.resolve()).as_posix(),
                    "bytes": path.stat().st_size,
                }
            )
    rows.sort(key=lambda row: row["bytes"], reverse=True)
    return rows


def _write_web_manifest(
    *,
    output_root: Path,
    source_root: Path,
    progress: ProgressManifest,
    texture_records: dict[str, dict],
    texture_profile: dict,
) -> None:
    path = output_root / "WebAssetManifest.json"
    merged_texture_records: dict[str, dict] = {}
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            previous_textures = previous.get("textures") if isinstance(previous, dict) else None
            if isinstance(previous_textures, dict):
                merged_texture_records.update(
                    {
                        str(rel): row
                        for rel, row in previous_textures.items()
                        if (
                            isinstance(row, dict)
                            and not str(rel).startswith("generated:")
                            and is_web_texture_candidate(str(rel))
                        )
                    }
                )
        except Exception:
            pass
    merged_texture_records.update(texture_records)

    entries: dict[str, dict] = {}
    for key, row in (progress.data.get("entries") or {}).items():
        if not isinstance(row, dict):
            continue
        cleaned = dict(row)
        for field in ("source_textures", "missing_textures"):
            cleaned[field] = [
                rel for rel in cleaned.get(field, [])
                if isinstance(rel, str) and is_web_texture_candidate(rel)
            ]
        for generated in cleaned.get("generated_textures") or []:
            if not isinstance(generated, dict):
                continue
            source = generated.get("source")
            if isinstance(source, str) and source:
                merged_texture_records[source] = generated
        entries[key] = cleaned
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "updated_utc": _now_utc(),
        "source_root": _rel_to_repo(source_root),
        "output_root": _rel_to_repo(output_root),
        "texture_profile": texture_profile,
        "entries": entries,
        "textures": merged_texture_records,
    }
    _write_json_atomic(path, manifest)


def _write_size_report(
    *,
    output_root: Path,
    source_root: Path,
    texture_records: dict[str, dict],
    texture_profile: dict,
) -> None:
    all_texture_records = dict(texture_records)
    manifest_path = output_root / "WebAssetManifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_textures = manifest.get("textures") if isinstance(manifest, dict) else None
            if isinstance(manifest_textures, dict):
                all_texture_records.update(
                    {
                        str(source): row
                        for source, row in manifest_textures.items()
                        if isinstance(row, dict)
                    }
                )
        except Exception:
            pass

    source_texture_bytes = sum(int(row.get("source_bytes") or 0) for row in all_texture_records.values())
    optimized_texture_bytes = sum(int(row.get("optimized_bytes") or 0) for row in all_texture_records.values())
    model_bytes = 0
    models_root = output_root / "models"
    if models_root.is_dir():
        model_bytes = sum(path.stat().st_size for path in models_root.rglob("*") if path.is_file())
    report = {
        "schema": SIZE_REPORT_SCHEMA,
        "updated_utc": _now_utc(),
        "source_root": _rel_to_repo(source_root),
        "output_root": _rel_to_repo(output_root),
        "texture_profile": texture_profile,
        "source_texture_bytes": source_texture_bytes,
        "optimized_texture_bytes": optimized_texture_bytes,
        "model_bytes": model_bytes,
        "total_web_asset_bytes": sum(
            path.stat().st_size for path in output_root.rglob("*") if path.is_file()
        ) if output_root.is_dir() else 0,
        "file_counts": _scan_file_counts(output_root),
        "output_files_over_50_mib": _large_files(output_root, 50 * 1024 * 1024),
        "output_files_over_100_mib": _large_files(output_root, 100 * 1024 * 1024),
        "texture_failures": [
            row for row in all_texture_records.values() if row.get("status") == "failed"
        ],
    }
    path = output_root / "WebAssetSizeReport.json"
    _write_json_atomic(path, report)


# ---------------------------------------------------------------------------
# Worker invocation
# ---------------------------------------------------------------------------

def _parse_result_line(stdout: str) -> dict | None:
    last = None
    for line in stdout.splitlines():
        if line.startswith("RESULT:"):
            last = line[len("RESULT:"):].strip()
    if last is None:
        return None
    try:
        return json.loads(last)
    except Exception:
        return None


def _run_one(
    *,
    blender_exe: Path,
    worker_script: Path,
    source_root: Path,
    output_root: Path,
    kind: str,
    entry: dict,
    texture_map: dict[str, str],
    texture_profile: dict,
    generated_texture_overrides: dict[str, dict],
    timeout_s: int,
) -> dict:
    asset_dir = _asset_dir(output_root, kind, entry)
    task = {
        "source_root": str(source_root.resolve()),
        "output_root": str(output_root.resolve()),
        "kind": kind,
        "entry": entry,
        "asset_dir": str(asset_dir.resolve()),
        "texture_map": texture_map,
        "texture_profile": texture_profile,
        "generated_texture_overrides": generated_texture_overrides,
    }
    t0 = time.time()
    tf: Path | None = None
    try:
        fd, task_path = tempfile.mkstemp(prefix="web_asset_task_", suffix=".json")
        os.close(fd)
        tf = Path(task_path)
        tf.write_text(json.dumps(task), encoding="utf-8")
        cmd = [
            str(blender_exe),
            "--background",
            "--factory-startup",
            "--python",
            str(worker_script),
            "--",
            "--task-file",
            str(tf),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed = round(time.time() - t0, 3)
        result = _parse_result_line(proc.stdout or "")
        if result is None:
            stderr_tail = "\n".join((proc.stderr or "").splitlines()[-20:])
            stdout_tail = "\n".join((proc.stdout or "").splitlines()[-20:])
            return {
                "status": "failed",
                "error": f"no RESULT line (exit {proc.returncode})",
                "stderr_tail": stderr_tail,
                "stdout_tail": stdout_tail,
                "duration_s": elapsed,
            }
        if proc.returncode != 0 and result.get("status") == "success":
            result = dict(result)
            result["status"] = "failed"
            result["error"] = f"{result.get('error', '')} (exit {proc.returncode})".strip()
        return result
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "error": f"timeout after {timeout_s}s",
            "duration_s": round(time.time() - t0, 3),
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - t0, 3),
        }
    finally:
        if tf is not None:
            try:
                tf.unlink(missing_ok=True)
            except Exception:
                pass


def _load_only_list(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(f"--only-list file not found: {path}")
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def _target_kinds(targets: str) -> list[str]:
    if targets == "sm":
        return ["SM"]
    if targets == "sk":
        return ["SK"]
    return ["SM", "SK"]


def _pending_missing_texture_rels(
    *,
    progress: ProgressManifest,
    texture_records: dict[str, dict],
    source_root: Path,
) -> list[str]:
    pending: set[str] = set()
    failed = {
        rel
        for rel, row in texture_records.items()
        if row.get("status") == "failed"
    }
    for entry in progress.data.get("entries", {}).values():
        if entry.get("status") != "success":
            continue
        for rel in entry.get("missing_textures") or []:
            if not isinstance(rel, str) or not rel.strip():
                continue
            rel_norm = rel.replace("\\", "/")
            if not is_web_texture_candidate(rel_norm):
                continue
            if rel_norm in failed:
                continue
            if not (source_root / rel_norm).is_file():
                continue
            if rel_norm not in texture_records:
                pending.add(rel_norm)
    return sorted(pending)


def _recovery_argv(
    args: argparse.Namespace,
    *,
    source_root: Path,
    output_root: Path,
    progress_file: Path,
) -> list[str]:
    out = [
        "--source-root", str(source_root),
        "--output-root", str(output_root),
        "--targets", args.targets,
        "--blender", str(args.blender),
        "--worker", str(args.worker),
        "--progress-file", str(progress_file),
        "--workers", str(args.workers),
        "--timeout-s", str(args.timeout_s),
        "--texture-size", str(args.texture_size),
        "--texture-quality", str(args.texture_quality),
        "--texture-recovery-passes", str(max(0, args.texture_recovery_passes - 1)),
    ]
    if args.data_file is not None:
        out.extend(["--data-file", str(args.data_file)])
    if args.limit is not None:
        out.extend(["--limit", str(args.limit)])
    if args.only is not None:
        out.extend(["--only", args.only])
    if args.only_list is not None:
        out.extend(["--only-list", str(args.only_list)])
    if args.prefer_textured:
        out.append("--prefer-textured")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build shared website glTF assets for SM_/SK_ entries.")
    parser.add_argument("--source-root", type=Path, default=_default_source_root())
    parser.add_argument("--output-root", type=Path, default=None, help="Default: <source-root>/WebAssets")
    parser.add_argument("--targets", choices=("sm", "sk", "both"), default="both")
    parser.add_argument("--data-file", type=Path, default=None, help="Optional single inventory override.")
    parser.add_argument("--blender", type=Path, default=_default_blender())
    parser.add_argument("--worker", type=Path, default=_default_worker())
    parser.add_argument("--progress-file", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--limit", type=int, default=None, help="Limit per selected kind.")
    parser.add_argument("--only", type=str, default=None, help="Substring filter on name or path.")
    parser.add_argument("--only-list", type=Path, default=None)
    parser.add_argument("--prefer-textured", action="store_true", help="Pick textured entries first when limiting.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--texture-size", type=int, default=1024)
    parser.add_argument("--texture-quality", type=int, default=75)
    parser.add_argument(
        "--texture-recovery-passes",
        type=int,
        default=1,
        help="Automatic follow-up passes for textures first discovered during Blender export.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve()
    output_root = (args.output_root or (source_root / "WebAssets")).resolve()
    progress_file = args.progress_file or (output_root / "WebAssetProgress.json")
    texture_profile = {
        "format": "webp",
        "max_dimension": args.texture_size,
        "quality": args.texture_quality,
        "upscale": False,
    }

    if not source_root.is_dir():
        _log(f"source root not found: {source_root}")
        return 2
    if not args.blender.is_file():
        _log(f"blender.exe not found: {args.blender}")
        return 2
    if not args.worker.is_file():
        _log(f"worker script not found: {args.worker}")
        return 2

    entries_by_kind: dict[str, list[dict]] = {}
    if args.data_file is not None:
        data_file = args.data_file.resolve()
        if not data_file.is_file():
            _log(f"data inventory not found: {data_file}")
            return 2
        kind = "SK" if data_file.name.upper().startswith("SK_") else "SM"
        entries_by_kind[kind] = _load_inventory(data_file)
    else:
        for kind in _target_kinds(args.targets):
            path = _inventory_path(source_root, kind)
            if not path.is_file():
                _log(f"data inventory not found: {path}")
                return 2
            entries_by_kind[kind] = _load_inventory(path)

    for kind, entries in entries_by_kind.items():
        _log(f"Using {kind}_Data.json ({len(entries)} entries)")

    try:
        only_paths = _load_only_list(args.only_list)
    except FileNotFoundError as e:
        _log(str(e))
        return 2

    selected = _select_entries(
        entries_by_kind,
        only_substr=args.only,
        only_paths=only_paths,
        limit_per_kind=args.limit,
        prefer_textured=args.prefer_textured,
        source_root=source_root,
    )
    _log(f"Selected {len(selected)} web asset entr{'y' if len(selected) == 1 else 'ies'}")

    progress = ProgressManifest(progress_file, source_root, output_root, texture_profile)

    texture_records, entry_textures = _prepare_texture_cache(
        source_root=source_root,
        output_root=output_root,
        selected=selected,
        progress=progress,
        texture_size=args.texture_size,
        texture_quality=args.texture_quality,
        dry_run=args.dry_run,
    )
    texture_map_all = {
        rel: str((output_root / rec["optimized"]).resolve())
        for rel, rec in texture_records.items()
        if rec.get("status") != "failed" and not args.dry_run
    }
    to_run: list[tuple[str, dict]] = []
    pre_skipped = 0
    for kind, entry in selected:
        key = _progress_key(kind, entry)
        required_optimized_textures = [
            texture_records[rel]["optimized"]
            for rel in entry_textures.get(key, [])
            if rel in texture_records and texture_records[rel].get("status") != "failed"
        ]
        skip = _should_skip(
            key=key,
            progress=progress,
            output_root=output_root,
            texture_profile=texture_profile,
            required_optimized_textures=required_optimized_textures,
            force=args.force,
        )
        if skip is not None:
            pre_skipped += 1
            continue
        to_run.append((kind, entry))

    _log(
        f"Models: {len(to_run)} to run, {pre_skipped} already built, "
        f"{args.workers} worker(s)"
    )
    if args.dry_run:
        for kind, entry in to_run:
            _log(f"  RUN {kind} {entry['path']}")
        return 0
    if not to_run:
        _write_web_manifest(
            output_root=output_root,
            source_root=source_root,
            progress=progress,
            texture_records=texture_records,
            texture_profile=texture_profile,
        )
        _write_size_report(
            output_root=output_root,
            source_root=source_root,
            texture_records=texture_records,
            texture_profile=texture_profile,
        )
        _log("Nothing to do.")
        return 0

    generated_overrides_by_entry = _prepare_generated_material_overrides(
        source_root=source_root,
        output_root=output_root,
        selected=to_run,
        texture_size=args.texture_size,
        texture_quality=args.texture_quality,
        dry_run=args.dry_run,
    )

    done = 0
    ok = 0
    fail = 0
    start = time.time()
    in_flight: dict[str, float] = {}
    in_flight_lock = threading.Lock()
    stop_heartbeat = threading.Event()

    def _task(kind: str, entry: dict) -> tuple[str, dict, dict]:
        key = _progress_key(kind, entry)
        texture_map = {
            rel: texture_map_all[rel]
            for rel in entry_textures.get(key, [])
            if rel in texture_map_all
        }
        with in_flight_lock:
            in_flight[key] = time.time()
        try:
            res = _run_one(
                blender_exe=args.blender,
                worker_script=args.worker,
                source_root=source_root,
                output_root=output_root,
                kind=kind,
                entry=entry,
                texture_map=texture_map,
                texture_profile=texture_profile,
                generated_texture_overrides=generated_overrides_by_entry.get(key, {}),
                timeout_s=args.timeout_s,
            )
        finally:
            with in_flight_lock:
                in_flight.pop(key, None)
        return kind, entry, res

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(10.0):
            with in_flight_lock:
                snapshot = list(in_flight.items())
            now = time.time()
            active = len(snapshot)
            oldest_name = "-"
            oldest_age = 0.0
            if snapshot:
                oldest_key, oldest_t0 = max(snapshot, key=lambda kv: now - kv[1])
                oldest_age = now - oldest_t0
                oldest_name = oldest_key.split(":", 1)[-1].rsplit("/", 1)[-1]
            elapsed = now - start
            _log(
                f"[heartbeat] {done}/{len(to_run)} active={active} "
                f"elapsed={elapsed:.0f}s oldest={oldest_name} ({oldest_age:.0f}s)"
            )

    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat_thread.start()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_task, kind, entry) for kind, entry in to_run]
        for fut in as_completed(futures):
            kind, entry, res = fut.result()
            done += 1
            key = _progress_key(kind, entry)
            status = res.get("status", "failed")
            optimized_textures = [
                texture_records[rel]["optimized"]
                for rel in entry_textures.get(key, [])
                if rel in texture_records and texture_records[rel].get("status") != "failed"
            ]
            generated_textures = [
                row
                for row in (res.get("generated_textures") or [])
                if isinstance(row, dict)
            ]
            for row in generated_textures:
                optimized = row.get("optimized")
                if isinstance(optimized, str) and optimized:
                    optimized_textures.append(optimized)
            record = {
                "kind": kind,
                "name": entry.get("name"),
                "path": entry.get("path"),
                "status": status,
                "export_revision": WEB_ASSET_EXPORT_REVISION,
                "texture_profile": texture_profile,
                "source_textures": entry_textures.get(key, []),
                "optimized_textures": optimized_textures,
                "gltf_path": res.get("gltf_path"),
                "bin_path": res.get("bin_path"),
                "asset_dir": res.get("asset_dir"),
                "materials_source": res.get("materials_source"),
                "slot_count": res.get("slot_count"),
                "used_textures": res.get("used_textures", []),
                "generated_textures": generated_textures,
                "material_diagnostics": res.get("material_diagnostics", []),
                "missing_textures": res.get("missing_textures", []),
                "duration_s": res.get("duration_s"),
                "finished_utc": _now_utc(),
                "error": res.get("error"),
            }
            progress.update(key, record)
            if status == "success":
                ok += 1
                _log(f"[{done}/{len(to_run)}] OK   {kind} {entry['name']} ({record['duration_s']}s)")
            else:
                fail += 1
                _log(f"[{done}/{len(to_run)}] FAIL {kind} {entry['name']} -> {res.get('error')}")

    stop_heartbeat.set()
    heartbeat_thread.join(timeout=2.0)

    _write_web_manifest(
        output_root=output_root,
        source_root=source_root,
        progress=progress,
        texture_records=texture_records,
        texture_profile=texture_profile,
    )
    _write_size_report(
        output_root=output_root,
        source_root=source_root,
        texture_records=texture_records,
        texture_profile=texture_profile,
    )

    elapsed = time.time() - start
    texture_failures = sum(1 for row in texture_records.values() if row.get("status") == "failed")
    _log(
        f"Done: {ok} ok, {fail} failed, {pre_skipped} pre-skipped, "
        f"{texture_failures} texture failures in {elapsed:.1f}s"
    )
    pending_missing = _pending_missing_texture_rels(
        progress=progress,
        texture_records=texture_records,
        source_root=source_root,
    )
    if fail == 0 and args.texture_recovery_passes > 0 and pending_missing:
        _log(
            f"Recovering {len(pending_missing)} texture(s) first discovered during export; "
            f"{args.texture_recovery_passes} recovery pass(es) left"
        )
        return main(
            _recovery_argv(
                args,
                source_root=source_root,
                output_root=output_root,
                progress_file=progress_file,
            )
        )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
