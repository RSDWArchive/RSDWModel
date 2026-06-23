"""
Discover SM_* and SK_* .uemodel assets under the source data tree and emit SM_Data.json / SK_Data.json.

Discovery is fully filesystem-driven (no asset-name lists). Output JSON is a stable snapshot for downstream tooling.
Prefix matching is case-insensitive because game assets occasionally use names
like Sm_Dungeon_*.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UEMODEL_SUFFIX = ".uemodel"
TEXTURE_SUFFIXES = frozenset(
    {
        ".png",
        ".tga",
        ".dds",
        ".jpg",
        ".jpeg",
        ".exr",
        ".bmp",
        ".hdr",
        ".webp",
    }
)
TEXTURE_DIR_NAMES = ("Textures", "Texture")

# Where a material JSON was found relative to the .uemodel (stable strings for importers).
MATERIAL_LOCATION_SAME_FOLDER = "same_folder_as_uemodel"
MATERIAL_LOCATION_MESH_MATERIALS_SUBFOLDER = "mesh_folder_materials_subfolder"
MATERIAL_LOCATION_PARENT_MATERIALS_FOLDER = "parent_materials_folder"
MATERIAL_LOCATION_ANCESTOR_MATERIALS_FOLDER = "ancestor_materials_folder"
MATERIAL_LOCATION_PARENT_FOLDER_LOOSE = "parent_folder_loose"
MATERIAL_LOCATION_ARCHIVE_REF = "archive_material_ref"
MODEL_MATERIAL_REFS_FILENAME = "ModelMaterialRefs.json"

# Content-sniffing: any JSON whose top level is an object with a "Textures" dict is
# treated as a material definition (covers MI_*, MT_*, M_*, and any future prefix).
MATERIAL_JSON_MARKER_KEY = "Textures"

MANIFEST_SCHEMA = "RSDWModel.ModelData.v3"


def repo_root_from_script() -> Path:
    # tools/ModelData/CompileModelData.py -> repository root
    return Path(__file__).resolve().parent.parent.parent


def latest_version_root() -> Path:
    repo = repo_root_from_script()
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for child in repo.iterdir():
        if not child.is_dir():
            continue
        parts = child.name.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            candidates.append((tuple(int(part) for part in parts), child))
    if not candidates:
        return repo / "0.12.0.0"
    return max(candidates, key=lambda item: item[0])[1]


@dataclass(frozen=True)
class TextureDiscovery:
    """How texture paths were found for one model (for auditing layout assumptions)."""

    adjacent_textures_dir: bool
    parent_textures_dir: bool
    ancestor_textures_dirs: list[str]
    loose_in_model_dir: bool


@dataclass(frozen=True)
class ModelEntry:
    name: str
    path: str
    materials: dict
    materials_hybrid: dict


def _is_texture_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TEXTURE_SUFFIXES


def _collect_files_under(dir_path: Path) -> list[Path]:
    out: list[Path] = []
    for p in dir_path.rglob("*"):
        if _is_texture_file(p):
            out.append(p)
    return out


def _loose_textures_in_dir(dir_path: Path) -> list[Path]:
    if not dir_path.is_dir():
        return []
    return [p for p in dir_path.iterdir() if _is_texture_file(p)]


def discover_textures_for_model(
    model_path: Path,
    source_root: Path,
    max_ancestor_hops: int,
) -> tuple[list[str], TextureDiscovery]:
    """
    Collect texture paths relative to source_root.

    Strategy (all structural — no hardcoded asset names):
    - Any folder named Textures or Texture at the model folder, then walking up ancestors
      (bounded by max_ancestor_hops and source_root) — recursive file collect inside those dirs.
    - Loose image files sitting directly in the same folder as the .uemodel.
    """
    source_root = source_root.resolve()
    model_path = model_path.resolve()
    model_dir = model_path.parent

    roots_scanned: list[Path] = []
    seen_roots: set[Path] = set()
    ancestor_rel: list[str] = []

    adjacent = False
    parent_level = False

    cur: Path | None = model_dir
    for hop in range(max_ancestor_hops + 1):
        if cur is None:
            break
        try:
            cur.relative_to(source_root)
        except ValueError:
            break

        for dirname in TEXTURE_DIR_NAMES:
            d = cur / dirname
            if d.is_dir():
                rp = d.resolve()
                if rp not in seen_roots:
                    seen_roots.add(rp)
                    roots_scanned.append(rp)
                    rel = d.resolve().relative_to(source_root).as_posix()
                    if hop == 0:
                        adjacent = True
                    elif hop == 1:
                        parent_level = True
                    else:
                        ancestor_rel.append(rel)

        if cur == source_root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent

    texture_paths: set[str] = set()
    for root in roots_scanned:
        for f in _collect_files_under(root):
            texture_paths.add(f.resolve().relative_to(source_root).as_posix())

    loose = _loose_textures_in_dir(model_dir)
    loose_rel = [p.resolve().relative_to(source_root).as_posix() for p in loose]
    for p in loose_rel:
        texture_paths.add(p)

    discovery = TextureDiscovery(
        adjacent_textures_dir=adjacent,
        parent_textures_dir=parent_level,
        ancestor_textures_dirs=sorted(ancestor_rel),
        loose_in_model_dir=bool(loose_rel),
    )

    return sorted(texture_paths), discovery


def iter_uemodels(root: Path) -> Iterable[Path]:
    root = root.resolve()
    if not root.is_dir():
        return
    for p in root.rglob(f"*{UEMODEL_SUFFIX}"):
        if p.is_file():
            yield p


def classify_name(stem: str) -> str | None:
    normalized = stem.upper()
    if normalized.startswith("SM_"):
        return "SM"
    if normalized.startswith("SK_"):
        return "SK"
    return None


def _is_material_json(path: Path) -> bool:
    """True if `path` is a JSON file whose top level is an object containing a
    `Textures` dict. Covers MI_*, MT_*, M_*, and any future material-definition
    prefix without hardcoding filename patterns."""
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    textures = data.get(MATERIAL_JSON_MARKER_KEY)
    return isinstance(textures, dict)


def discover_material_jsons(
    model_path: Path,
    source_root: Path,
    max_ancestor_hops: int,
    explicit_refs: Iterable[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Find material-definition JSONs (MI_*, MT_*, M_*, ...) near the mesh using
    the same layout rules as the content audit. Membership is determined by
    content (top-level `Textures` dict), not by filename prefix.

    Returns (items, paths) where items are {path, location} and paths is a sorted
    list of relative posix paths (deduped).
    """
    source_root = source_root.resolve()
    model_path = model_path.resolve()
    model_dir = model_path.parent
    parent = model_dir.parent

    seen: set[Path] = set()
    items: list[dict] = []

    def add(abs_p: Path, location: str, extra: dict | None = None) -> None:
        rp = abs_p.resolve()
        if rp in seen:
            return
        if not _is_material_json(rp):
            return
        seen.add(rp)
        rel = rp.relative_to(source_root).as_posix()
        item = {"path": rel, "location": location}
        if extra:
            item.update(extra)
        items.append(item)

    def scan_loose(dir_path: Path, location: str) -> None:
        if not dir_path.is_dir():
            return
        for p in sorted(dir_path.glob("*.json")):
            add(p, location)

    scan_loose(model_dir, MATERIAL_LOCATION_SAME_FOLDER)
    scan_loose(model_dir / "Materials", MATERIAL_LOCATION_MESH_MATERIALS_SUBFOLDER)
    scan_loose(parent / "Materials", MATERIAL_LOCATION_PARENT_MATERIALS_FOLDER)
    scan_loose(parent, MATERIAL_LOCATION_PARENT_FOLDER_LOOSE)
    current = parent.parent
    for _ in range(max_ancestor_hops):
        if source_root not in (current, *current.parents):
            break
        scan_loose(current / "Materials", MATERIAL_LOCATION_ANCESTOR_MATERIALS_FOLDER)
        if current == source_root:
            break
        current = current.parent

    for ref in explicit_refs or []:
        if not isinstance(ref, dict):
            continue
        rel = ref.get("material_json_path") or ref.get("path")
        if not isinstance(rel, str) or not rel.strip():
            continue
        abs_p = (source_root / rel).resolve()
        try:
            abs_p.relative_to(source_root)
        except ValueError:
            continue
        extra: dict = {}
        if ref.get("slot"):
            extra["slot"] = ref["slot"]
        if ref.get("object_path"):
            extra["object_path"] = ref["object_path"]
        add(abs_p, MATERIAL_LOCATION_ARCHIVE_REF, extra)

    items.sort(key=lambda x: x["path"].lower())
    paths = [x["path"] for x in items]
    return items, paths


def load_model_material_refs(source_root: Path, refs_path: Path | None = None) -> dict[str, list[dict]]:
    path = refs_path or (source_root / MODEL_MATERIAL_REFS_FILENAME)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for model_rel, row in models.items():
        if not isinstance(model_rel, str) or not isinstance(row, dict):
            continue
        refs = row.get("materials")
        if isinstance(refs, list):
            out[model_rel] = [ref for ref in refs if isinstance(ref, dict)]
    return out


def build_manifest(
    source_root: Path,
    max_ancestor_hops: int,
    model_material_refs_path: Path | None = None,
) -> tuple[list[ModelEntry], list[ModelEntry]]:
    sm: list[ModelEntry] = []
    sk: list[ModelEntry] = []
    source_root = source_root.resolve()
    explicit_material_refs = load_model_material_refs(source_root, model_material_refs_path)

    for abs_path in sorted(iter_uemodels(source_root), key=lambda p: str(p).lower()):
        rel = abs_path.relative_to(source_root).as_posix()
        kind = classify_name(abs_path.stem)
        if kind is None:
            continue
        mat_items, mat_paths = discover_material_jsons(
            abs_path,
            source_root,
            max_ancestor_hops,
            explicit_refs=explicit_material_refs.get(rel),
        )
        tex_paths, disc = discover_textures_for_model(abs_path, source_root, max_ancestor_hops)
        entry = ModelEntry(
            name=abs_path.name,
            path=rel,
            materials={
                "material_json_paths": mat_paths,
                "items": mat_items,
            },
            materials_hybrid={
                "texture_image_paths": tex_paths,
                "discovery": asdict(disc),
            },
        )
        if kind == "SM":
            sm.append(entry)
        else:
            sk.append(entry)

    return sm, sk


def _display_path(path: Path, anchor: Path) -> str:
    try:
        return path.resolve().relative_to(anchor.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _entry_to_json_dict(e: ModelEntry) -> dict:
    return {
        "name": e.name,
        "path": e.path,
        "Materials": e.materials,
        "MaterialsHybrid": e.materials_hybrid,
    }


def write_json(path: Path, source_root: Path, entries: list[ModelEntry]) -> None:
    anchor = repo_root_from_script()
    payload = {
        "manifest_schema": MANIFEST_SCHEMA,
        "source_root": _display_path(source_root, anchor),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(entries),
        "entries": [_entry_to_json_dict(e) for e in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover SM_/SK_ .uemodel files and write SM_Data.json / SK_Data.json.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=latest_version_root(),
        help="Root folder to scan (default: newest version folder at repo root)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write JSON files (default: <source-root>/ModelData)",
    )
    parser.add_argument(
        "--max-ancestor-hops",
        type=int,
        default=8,
        help="Walk up at most this many parent folders from the .uemodel when looking for Textures/Texture dirs",
    )
    parser.add_argument(
        "--model-material-refs",
        type=Path,
        default=None,
        help=f"Optional model-to-material ref manifest. Defaults to <source-root>/{MODEL_MATERIAL_REFS_FILENAME}.",
    )
    args = parser.parse_args(argv)

    source_root = args.source_root
    if not source_root.is_dir():
        print(f"Source root is not a directory: {source_root}", file=sys.stderr)
        return 1

    sm, sk = build_manifest(source_root, args.max_ancestor_hops, args.model_material_refs)
    out_dir = args.output_dir or (source_root / "ModelData")
    write_json(out_dir / "SM_Data.json", source_root, sm)
    write_json(out_dir / "SK_Data.json", source_root, sk)

    print(f"Source: {source_root.resolve()}")
    print(f"Wrote {len(sm)} SM_ entries -> {(out_dir / 'SM_Data.json').resolve()}")
    print(f"Wrote {len(sk)} SK_ entries -> {(out_dir / 'SK_Data.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
