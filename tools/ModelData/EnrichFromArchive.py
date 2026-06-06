from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


TEXTURE_EXTENSIONS = (".png", ".hdr", ".tga", ".jpg", ".jpeg", ".webp")
PLUGIN_ROOT_REL = "RSDragonwilds/Plugins/GameFeatures"
GAME_ROOT_REL = "RSDragonwilds/Content"
MODEL_MATERIAL_REFS_FILENAME = "ModelMaterialRefs.json"
MODEL_MATERIAL_REFS_SCHEMA = "RSDWModel.ModelMaterialRefs.v1"


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
    return max(candidates, key=lambda item: item[0])[1] if candidates else _repo_root() / "0.11.2.2"


def _default_archive_root(source_root: Path) -> Path:
    return Path(r"E:\Github\RSDWArchive") / source_root.name


def _strip_object_suffix(path: str) -> str:
    s = path.replace("\\", "/").strip().strip("'\"")
    if "." not in s:
        return s
    head, tail = s.rsplit(".", 1)
    if tail.isdigit() or head.rsplit("/", 1)[-1] == tail:
        return head
    return s


def _package_path_to_relatives(package_path: str) -> list[str]:
    s = _strip_object_suffix(package_path)
    if not s:
        return []
    if s.startswith("RSDragonwilds/"):
        return [s]
    if s.startswith("/Game/"):
        return [f"{GAME_ROOT_REL}/{s[len('/Game/'):]}"]
    if s.startswith("/"):
        without_lead = s[1:]
        if "/" in without_lead:
            plugin, rest = without_lead.split("/", 1)
            return [
                f"{PLUGIN_ROOT_REL}/{plugin}/Content/{rest}",
                without_lead,
            ]
        return [without_lead]
    return [s]


def _first_existing_json(archive_root: Path, package_path: str) -> tuple[Path, str] | None:
    for rel in _package_path_to_relatives(package_path):
        candidate = archive_root / "json" / f"{rel}.json"
        if candidate.is_file():
            return candidate, rel
    return None


def _first_existing_texture(archive_root: Path, rel: str) -> Path | None:
    base = archive_root / "textures" / rel
    for ext in TEXTURE_EXTENSIONS:
        candidate = base.with_suffix(ext)
        if candidate.is_file():
            return candidate
    for ext in TEXTURE_EXTENSIONS:
        candidate = base.parent / f"{base.name}_0{ext}"
        if candidate.is_file():
            return candidate
    return None


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _archive_objects(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        value = data.get("value")
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        return [data]
    return []


def _iter_uemodels(source_root: Path):
    yield from sorted(source_root.rglob("*.uemodel"), key=lambda path: path.as_posix().lower())


def _material_refs_from_mesh_json(path: Path) -> list[dict]:
    data = _load_json(path)
    refs: list[dict] = []
    for obj in _archive_objects(data):
        obj_type = obj.get("Type")
        if obj_type == "StaticMesh":
            for row in ((obj.get("Properties") or {}).get("StaticMaterials") or []):
                material = row.get("MaterialInterface") if isinstance(row, dict) else None
                if isinstance(material, dict) and material.get("ObjectPath"):
                    refs.append(
                        {
                            "slot": row.get("MaterialSlotName"),
                            "object_path": material["ObjectPath"],
                        }
                    )
        elif obj_type == "SkeletalMesh":
            for row in (obj.get("SkeletalMaterials") or []):
                material = row.get("Material") if isinstance(row, dict) else None
                if isinstance(material, dict) and material.get("ObjectPath"):
                    refs.append(
                        {
                            "slot": row.get("MaterialSlotName"),
                            "object_path": material["ObjectPath"],
                        }
                    )
    return refs


def _param_name(row: dict) -> str | None:
    info = row.get("ParameterInfo") if isinstance(row, dict) else None
    name = info.get("Name") if isinstance(info, dict) else None
    return str(name) if name else None


def _texture_value_for_rel(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return f"{rel}.{name}"


def _add_texture_aliases(textures: dict[str, str], name: str, value: str) -> None:
    low = name.lower()
    if low == "basecolor map":
        textures.setdefault("BaseColor", value)
        textures.setdefault("PM_Diffuse", value)
    elif low == "normal":
        textures.setdefault("PM_Normals", value)
    elif low == "orm map":
        textures.setdefault("ORM", value)
        textures.setdefault("PM_SpecularMasks", value)
    elif low == "emissive":
        textures.setdefault("PM_Emissive", value)


def _role_from_referenced_texture(rel: str) -> str:
    stem = Path(rel).name.lower()
    compact = stem.replace("-", "_")
    if any(token in compact for token in ("normal", "_n", "_na")):
        return "Normal"
    if any(token in compact for token in ("orm", "specularmask", "specular_mask", "_mra", "_arm")):
        return "ORM"
    if any(token in compact for token in ("emissive", "_e")):
        return "Emission"
    return "BaseColor"


def _add_texture_role_aliases(textures: dict[str, str], role: str, value: str) -> None:
    if role == "BaseColor":
        textures.setdefault("BaseColor", value)
        textures.setdefault("PM_Diffuse", value)
    elif role == "Normal":
        textures.setdefault("Normal", value)
        textures.setdefault("PM_Normals", value)
    elif role == "ORM":
        textures.setdefault("ORM", value)
        textures.setdefault("PM_SpecularMasks", value)
    elif role == "Emission":
        textures.setdefault("Emissive", value)
        textures.setdefault("PM_Emissive", value)


def _referenced_texture_rels(material: dict) -> list[tuple[str, str]]:
    rows: list[dict] = []
    for value in (material.get("ReferencedTextures"), (material.get("CachedExpressionData") or {}).get("ReferencedTextures")):
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))

    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        object_path = row.get("ObjectPath")
        if not object_path:
            continue
        for rel in _package_path_to_relatives(str(object_path)):
            if rel in seen:
                continue
            seen.add(rel)
            found.append((_role_from_referenced_texture(rel), rel))
            break
    return found


def _convert_archive_material(path: Path, archive_root: Path) -> tuple[dict, list[str]]:
    data = _load_json(path)
    material = next(
        (
            row for row in _archive_objects(data)
            if isinstance(row, dict) and "Material" in str(row.get("Type", ""))
        ),
        None,
    )
    if not isinstance(material, dict):
        raise ValueError(f"No material export found: {path}")
    props = material.get("Properties") or {}
    if not isinstance(props, dict):
        props = {}

    textures: dict[str, str] = {}
    texture_rels: list[str] = []
    for row in props.get("TextureParameterValues") or []:
        if not isinstance(row, dict):
            continue
        name = _param_name(row)
        value = row.get("ParameterValue")
        object_path = value.get("ObjectPath") if isinstance(value, dict) else None
        if not name or not object_path:
            continue
        resolved = _first_existing_json(archive_root, str(object_path))
        rel = resolved[1] if resolved else (_package_path_to_relatives(str(object_path)) or [""])[0]
        if not rel:
            continue
        texture_value = _texture_value_for_rel(rel)
        textures[name] = texture_value
        _add_texture_aliases(textures, name, texture_value)
        texture_rels.append(rel)

    if not textures:
        for role, rel in _referenced_texture_rels(material):
            if not rel:
                continue
            texture_value = _texture_value_for_rel(rel)
            _add_texture_role_aliases(textures, role, texture_value)
            texture_rels.append(rel)

    parameters: dict = {
        "Colors": {},
        "Scalars": {},
        "Switches": {},
        "Properties": {},
    }
    for row in props.get("ScalarParameterValues") or []:
        name = _param_name(row)
        if name and isinstance(row, dict) and "ParameterValue" in row:
            parameters["Scalars"][name] = row["ParameterValue"]
    for row in props.get("VectorParameterValues") or []:
        name = _param_name(row)
        value = row.get("ParameterValue") if isinstance(row, dict) else None
        if name and isinstance(value, dict):
            parameters["Colors"][name] = value
    runtime = props.get("StaticParametersRuntime") or {}
    for row in runtime.get("StaticSwitchParameters") or []:
        name = _param_name(row)
        if name and isinstance(row, dict) and "Value" in row:
            parameters["Switches"][name] = row["Value"]
    if "BasePropertyOverrides" in props:
        parameters["Properties"]["BasePropertyOverrides"] = props["BasePropertyOverrides"]
    for key in ("BlendMode", "ShadingModel", "TwoSided"):
        if key in props:
            parameters["Properties"][key] = props[key]

    return {
        "Textures": dict(sorted(textures.items())),
        "Parameters": parameters,
        "ArchiveSource": path.resolve().as_posix(),
    }, sorted(set(texture_rels))


def _copy_file(src: Path, dest: Path, *, force: bool, dry_run: bool) -> bool:
    if dest.is_file() and not force:
        return False
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return True


def _existing_material_has_textures(path: Path) -> bool:
    try:
        data = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    textures = data.get("Textures") if isinstance(data, dict) else None
    return bool(textures) if isinstance(textures, dict) else False


def _load_existing_model_material_refs(source_root: Path) -> dict[str, dict]:
    path = source_root / MODEL_MATERIAL_REFS_FILENAME
    if not path.is_file():
        return {}
    try:
        data = _load_json(path)
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return dict(models) if isinstance(models, dict) else {}


def _write_model_material_refs(
    source_root: Path,
    refs_by_model: dict[str, dict],
    *,
    replace: bool,
    dry_run: bool,
) -> Path:
    path = source_root / MODEL_MATERIAL_REFS_FILENAME
    models = {} if replace else _load_existing_model_material_refs(source_root)
    for model_rel, row in refs_by_model.items():
        if row.get("materials"):
            models[model_rel] = row
        elif model_rel in models:
            del models[model_rel]

    payload = {
        "schema": MODEL_MATERIAL_REFS_SCHEMA,
        "model_count": len(models),
        "models": dict(sorted(models.items())),
    }
    if not dry_run:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def enrich(source_root: Path, archive_root: Path, only: str | None, limit: int | None, force: bool, dry_run: bool) -> dict:
    source_root = source_root.resolve()
    archive_root = archive_root.resolve()
    report = {
        "source_root": source_root.as_posix(),
        "archive_root": archive_root.as_posix(),
        "models_seen": 0,
        "models_with_archive_json": 0,
        "models_with_material_refs": 0,
        "materials_written": 0,
        "materials_skipped": 0,
        "textures_copied": 0,
        "textures_skipped": 0,
        "texture_sources_missing": 0,
        "model_material_ref_models": 0,
        "model_material_ref_path": None,
        "examples": [],
    }

    processed = 0
    refs_by_model: dict[str, dict] = {}
    for model_path in _iter_uemodels(source_root):
        rel = model_path.relative_to(source_root).as_posix()
        if only and only.lower() not in rel.lower():
            continue
        if limit is not None and processed >= limit:
            break
        processed += 1
        report["models_seen"] += 1

        mesh_json = archive_root / "json" / f"{rel[:-len('.uemodel')]}.json"
        if not mesh_json.is_file():
            continue
        report["models_with_archive_json"] += 1
        material_refs = _material_refs_from_mesh_json(mesh_json)
        if not material_refs:
            continue
        report["models_with_material_refs"] += 1

        model_example = {"model": rel, "materials": []}
        model_refs: list[dict] = []
        for material_ref in material_refs:
            resolved = _first_existing_json(archive_root, material_ref["object_path"])
            if resolved is None:
                continue
            material_json, material_rel = resolved
            converted, texture_rels = _convert_archive_material(material_json, archive_root)
            material_json_rel = f"{material_rel}.json"
            model_refs.append(
                {
                    "slot": material_ref.get("slot"),
                    "object_path": material_ref["object_path"],
                    "material_json_path": material_json_rel,
                }
            )
            dest_json = source_root / f"{material_rel}.json"
            should_write = force or not dest_json.is_file()
            if (
                not should_write
                and converted.get("Textures")
                and not _existing_material_has_textures(dest_json)
            ):
                should_write = True

            if not should_write:
                report["materials_skipped"] += 1
            else:
                if not dry_run:
                    dest_json.parent.mkdir(parents=True, exist_ok=True)
                    dest_json.write_text(json.dumps(converted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                report["materials_written"] += 1

            material_example = {"material": material_rel, "textures": []}
            for texture_rel in texture_rels:
                src_texture = _first_existing_texture(archive_root, texture_rel)
                if src_texture is None:
                    report["texture_sources_missing"] += 1
                    continue
                dest_texture = source_root / texture_rel
                if src_texture.stem.startswith(dest_texture.name):
                    dest_texture = dest_texture.parent / f"{src_texture.stem}{src_texture.suffix}"
                else:
                    dest_texture = dest_texture.with_suffix(src_texture.suffix)
                if _copy_file(src_texture, dest_texture, force=force, dry_run=dry_run):
                    report["textures_copied"] += 1
                else:
                    report["textures_skipped"] += 1
                material_example["textures"].append(dest_texture.relative_to(source_root).as_posix())
            model_example["materials"].append(material_example)
        if model_refs:
            refs_by_model[rel] = {"materials": model_refs}
        if model_example["materials"] and len(report["examples"]) < 10:
            report["examples"].append(model_example)

    replace_refs = only is None and limit is None
    ref_path = _write_model_material_refs(source_root, refs_by_model, replace=replace_refs, dry_run=dry_run)
    report["model_material_ref_models"] = len(refs_by_model)
    report["model_material_ref_path"] = ref_path.as_posix()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich RSDWModel sidecars from an RSDWArchive dataset.")
    parser.add_argument("--source-root", type=Path, default=_default_source_root())
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--only", default=None, help="Only process model paths containing this substring.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    archive_root = (args.archive_root or _default_archive_root(source_root)).resolve()
    if not source_root.is_dir():
        raise SystemExit(f"source root not found: {source_root}")
    if not (archive_root / "json").is_dir() or not (archive_root / "textures").is_dir():
        raise SystemExit(f"archive root must contain json/ and textures/: {archive_root}")

    report = enrich(source_root, archive_root, args.only, args.limit, args.force, args.dry_run)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
