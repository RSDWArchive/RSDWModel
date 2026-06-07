"""
Build browser-ready animated glTF variants for skeletal models.

RSDWArchive is used as the metadata index for skeleton compatibility; CUE4Parse
is used to extract the actual animation curve data from retoc packages as
.ueanim files. Runtime website output remains self-contained in RSDWModel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import BuildWebAssets as web_assets  # noqa: E402


SCHEMA = "RSDWModel.WebsiteAnimationIndex.v1"
ANIMATION_EXPORT_REVISION = "animation-v1"
UNSUPPORTED_ANIMATION_COUNTS: dict[str, int] = {}
RUNTIME_UNSUPPORTED_ANIMATION_COUNTS: dict[str, int] = {}


class UnsupportedAnimationError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _repo_root() -> Path:
    return SCRIPT_DIR.parent.parent


def _default_source_root() -> Path:
    return web_assets._default_source_root()


def _default_blender() -> Path:
    repo_blender = _repo_root() / "blender-5.0.0-windows-x64" / "blender.exe"
    if repo_blender.is_file():
        return repo_blender
    for candidate in (
        Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"),
    ):
        if candidate.is_file():
            return candidate
    return repo_blender


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    print(message, flush=True)


def _hash_text(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _normalize_object_path(value: str | None) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    if "." in text:
        head, tail = text.rsplit(".", 1)
        if tail.isdigit() or head.rsplit("/", 1)[-1].lower() == tail.lower():
            return head
    return text


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_model_index(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"model index not found: {path}")
    data = _load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"model index is not an object: {path}")
    return data


def _load_inventory(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise FileNotFoundError(f"SK inventory not found: {path}")
    data = _load_json(path)
    out: dict[str, dict] = {}
    for entry in data.get("entries") or []:
        if isinstance(entry, dict) and entry.get("path"):
            out[str(entry["path"]).replace("\\", "/")] = entry
    return out


def _first_export(data, export_type: str) -> dict | None:
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("Type") == export_type:
                return row
    elif isinstance(data, dict) and data.get("Type") == export_type:
        return data
    return None


def _archive_json_for_model(archive_json_root: Path, model_path: str) -> Path:
    return archive_json_root / model_path.replace("\\", "/").replace(".uemodel", ".json")


def _model_skeleton_path(archive_json_root: Path, model_path: str) -> str:
    path = _archive_json_for_model(archive_json_root, model_path)
    if not path.is_file():
        return ""
    try:
        export = _first_export(_load_json(path), "SkeletalMesh")
    except Exception:
        return ""
    props = export.get("Properties") if isinstance(export, dict) else None
    skeleton = (props or {}).get("Skeleton") if isinstance(props, dict) else None
    return _normalize_object_path((skeleton or {}).get("ObjectPath") if isinstance(skeleton, dict) else None)


def _discover_models(
    *,
    model_index: dict,
    inventory: dict[str, dict],
    archive_json_root: Path,
    output_root: Path,
    only: str | None,
) -> list[dict]:
    low = (only or "").lower()
    rows: list[dict] = []
    for model in model_index.get("models") or []:
        if not isinstance(model, dict) or model.get("kind") != "SK":
            continue
        model_path = str(model.get("path") or "").replace("\\", "/")
        if not model_path:
            continue
        if low and low not in model_path.lower() and low not in str(model.get("displayName") or model.get("name") or "").lower():
            continue
        entry = inventory.get(model_path)
        if not entry:
            continue
        gltf_path = str(model.get("gltfPath") or "")
        if not gltf_path or not (output_root / gltf_path).is_file():
            continue
        skeleton_path = _model_skeleton_path(archive_json_root, model_path)
        if not skeleton_path:
            continue
        animation_skeleton_paths = [skeleton_path]
        model_path_lower = model_path.lower()
        if "/skeleton/player/body/" in model_path_lower:
            player_control_skeleton = "RSDragonwilds/Content/Art/Skeleton/Player/Invis/SKEL_M_MED_Invis_01"
            if player_control_skeleton not in animation_skeleton_paths:
                animation_skeleton_paths.append(player_control_skeleton)
        rows.append(
            {
                "id": model.get("id") or f"SK:{model_path}",
                "name": model.get("displayName") or model.get("name") or Path(model_path).name,
                "path": model_path,
                "gltfPath": gltf_path,
                "entry": entry,
                "skeletonPath": skeleton_path,
                "animationSkeletonPaths": animation_skeleton_paths,
            }
        )
    return rows


def _animation_package_from_json(archive_json_root: Path, json_path: Path) -> str:
    rel = json_path.resolve().relative_to(archive_json_root.resolve()).as_posix()
    return rel[:-5] + ".uasset" if rel.lower().endswith(".json") else rel


def _unsupported_animation_reason(name: str, props: dict) -> str | None:
    additive_type = str(props.get("AdditiveAnimType") or "")
    if additive_type and "AAT_None" not in additive_type:
        return "additive"
    low = name.lower()
    if low.endswith("_add") or "_add_" in low:
        return "additive-name"
    return None


def _discover_animations(archive_json_root: Path) -> dict[str, list[dict]]:
    UNSUPPORTED_ANIMATION_COUNTS.clear()
    anim_root = archive_json_root / "RSDragonwilds" / "Content" / "Art" / "Animation"
    if not anim_root.is_dir():
        raise FileNotFoundError(f"Archive animation JSON root not found: {anim_root}")
    by_skeleton: dict[str, list[dict]] = {}
    for path in anim_root.rglob("*.json"):
        try:
            export = _first_export(_load_json(path), "AnimSequence")
        except Exception:
            continue
        if not export:
            continue
        props = export.get("Properties") or {}
        skeleton = props.get("Skeleton") if isinstance(props, dict) else None
        skeleton_path = _normalize_object_path((skeleton or {}).get("ObjectPath") if isinstance(skeleton, dict) else None)
        if not skeleton_path:
            continue
        name = str(export.get("Name") or path.stem)
        reason = _unsupported_animation_reason(name, props)
        if reason:
            UNSUPPORTED_ANIMATION_COUNTS[reason] = UNSUPPORTED_ANIMATION_COUNTS.get(reason, 0) + 1
            continue
        package_path = _animation_package_from_json(archive_json_root, path)
        row = {
            "id": f"{name}-{_hash_text(package_path, 10)}",
            "name": name,
            "label": name.replace("A_", "").replace("_", " "),
            "packagePath": package_path,
            "sourceJsonPath": path.resolve().relative_to(archive_json_root.resolve()).as_posix(),
            "skeletonPath": skeleton_path,
            "sequenceLength": props.get("SequenceLength"),
        }
        by_skeleton.setdefault(skeleton_path, []).append(row)
    for rows in by_skeleton.values():
        rows.sort(key=lambda row: (row["name"].lower(), row["packagePath"].lower()))
    return by_skeleton


def _select_pairs(models: list[dict], animations_by_skeleton: dict[str, list[dict]], mode: str, limit: int | None) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []

    def add_pair(model: dict, animation: dict) -> bool:
        key = (model["id"], animation["id"])
        if any(existing_model["id"] == key[0] and existing_animation["id"] == key[1] for existing_model, existing_animation in pairs):
            return False
        pairs.append((model, animation))
        return True

    def animations_for_model(model: dict) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for skeleton_path in model.get("animationSkeletonPaths") or [model["skeletonPath"]]:
            for animation in animations_by_skeleton.get(skeleton_path, []):
                if animation["id"] in seen:
                    continue
                seen.add(animation["id"])
                out.append(animation)
        return out

    if mode == "smoke":
        for model in models:
            if "SK_M_MED_Beast_Druid_01" not in model["path"]:
                continue
            for anim in animations_for_model(model):
                if anim["name"] == "A_MED_Beast_Idle_Breathe":
                    add_pair(model, anim)
                    break
        for model in models:
            if "SK_M_MED_Body_A_01" not in model["path"]:
                continue
            preferred = [
                "A_PlayerM_Walk_Fwd",
                "A_PlayerM_CharacterSelection_Combined",
                "A_Idle_Add_Breathe",
            ]
            anims = animations_for_model(model)
            for name in preferred:
                match = next((anim for anim in anims if anim["name"] == name), None)
                if match:
                    add_pair(model, match)
                    break
            else:
                if anims:
                    add_pair(model, anims[0])
            break
        if pairs:
            return pairs
        for model in models:
            anims = animations_for_model(model)
            if anims:
                return [(model, anims[0])]
        return []

    for model in models:
        for anim in animations_for_model(model):
            add_pair(model, anim)
            if limit is not None and len(pairs) >= limit:
                return pairs
    return pairs


def _ueanim_path(animation_cache_root: Path, package_path: str) -> Path:
    rel = package_path.replace("\\", "/")
    for ext in (".uasset", ".umap"):
        if rel.lower().endswith(ext):
            rel = rel[: -len(ext)]
            break
    return animation_cache_root / f"{rel}.ueanim"


def _extract_animation(
    *,
    repo_root: Path,
    cue4parse_root: Path,
    retoc_root: Path,
    usmap: Path,
    animation_cache_root: Path,
    animation: dict,
    force: bool,
    dry_run: bool,
) -> Path:
    out_path = _ueanim_path(animation_cache_root, animation["packagePath"])
    if out_path.is_file() and not force:
        return out_path
    cmd = [
        "dotnet",
        "run",
        "--project",
        str(repo_root / "tools" / "CueExtract" / "RsdwCueExtract" / "RsdwCueExtract.csproj"),
        f"/p:Cue4ParseRoot={cue4parse_root}",
        "--",
        "--retoc-root",
        str(retoc_root),
        "--usmap",
        str(usmap),
        "--out",
        str(animation_cache_root),
        "--animations-only",
        "--asset",
        animation["packagePath"],
    ]
    if force:
        cmd.append("--force")
    if dry_run:
        _log("DRY RUN extract: " + " ".join(cmd))
        return out_path
    proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-20:])
        if "Unsupported compressed data type" in tail:
            raise UnsupportedAnimationError(
                "unsupported-compression",
                f"CUE4Parse cannot export {animation['name']} with its current animation compression.",
            )
        raise RuntimeError(f"CUE4Parse animation extract failed for {animation['name']}:\n{tail}")
    if not out_path.is_file():
        raise FileNotFoundError(f"CUE4Parse did not produce expected .ueanim: {out_path}")
    return out_path


def _asset_dir(output_root: Path, model: dict, animation: dict) -> Path:
    model_stem = Path(model["path"]).stem
    anim_stem = animation["name"]
    model_hash = _hash_text(f"{ANIMATION_EXPORT_REVISION}\n{model['id']}", 10)
    anim_hash = _hash_text(f"{ANIMATION_EXPORT_REVISION}\n{animation['packagePath']}", 10)
    return output_root / "animations" / f"{model_stem}-{model_hash}" / f"{anim_stem}-{anim_hash}"


def _existing_animation_result(output_root: Path, model: dict, animation: dict, force: bool) -> dict | None:
    if force:
        return None
    asset_dir = _asset_dir(output_root, model, animation)
    gltf_path = asset_dir / "model.gltf"
    bin_path = asset_dir / "model.bin"
    if not (gltf_path.is_file() and bin_path.is_file()):
        return None
    return {
        "status": "success",
        "asset_dir": asset_dir.resolve().relative_to(output_root.resolve()).as_posix(),
        "gltf_path": gltf_path.resolve().relative_to(output_root.resolve()).as_posix(),
        "bin_path": bin_path.resolve().relative_to(output_root.resolve()).as_posix(),
        "skipped": True,
    }


def _load_texture_map(output_root: Path) -> tuple[dict[str, str], dict]:
    manifest_path = output_root / "WebAssetManifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"WebAssetManifest.json not found: {manifest_path}")
    manifest = _load_json(manifest_path)
    texture_map: dict[str, str] = {}
    for source, row in (manifest.get("textures") or {}).items():
        if not isinstance(row, dict):
            continue
        optimized = row.get("optimized")
        if not isinstance(optimized, str) or not optimized:
            continue
        path = output_root / optimized
        if path.is_file():
            texture_map[str(source).replace("\\", "/")] = str(path.resolve())
    return texture_map, manifest


def _run_worker(
    *,
    blender: Path,
    worker: Path,
    source_root: Path,
    output_root: Path,
    model: dict,
    animation: dict,
    ueanim_path: Path,
    texture_map: dict[str, str],
    texture_profile: dict,
    generated_overrides: dict,
    force: bool,
    timeout_s: int,
    dry_run: bool,
) -> dict:
    asset_dir = _asset_dir(output_root, model, animation)
    gltf_path = asset_dir / "model.gltf"
    bin_path = asset_dir / "model.bin"
    if gltf_path.is_file() and bin_path.is_file() and not force:
        return {
            "status": "success",
            "asset_dir": asset_dir.resolve().relative_to(output_root.resolve()).as_posix(),
            "gltf_path": gltf_path.resolve().relative_to(output_root.resolve()).as_posix(),
            "bin_path": bin_path.resolve().relative_to(output_root.resolve()).as_posix(),
            "skipped": True,
        }
    task = {
        "source_root": str(source_root.resolve()),
        "output_root": str(output_root.resolve()),
        "kind": "SK",
        "entry": model["entry"],
        "asset_dir": str(asset_dir.resolve()),
        "texture_map": texture_map,
        "texture_profile": texture_profile,
        "generated_texture_overrides": generated_overrides,
        "animations": [
            {
                "id": animation["id"],
                "name": animation["name"],
                "ueanim_path": str(ueanim_path.resolve()),
            }
        ],
    }
    if dry_run:
        _log(f"DRY RUN worker: {model['name']} + {animation['name']} -> {asset_dir}")
        return {"status": "dry_run", "asset_dir": asset_dir.resolve().relative_to(output_root.resolve()).as_posix()}

    fd, task_path = tempfile.mkstemp(prefix="web_animation_task_", suffix=".json")
    os.close(fd)
    task_file = Path(task_path)
    task_file.write_text(json.dumps(task), encoding="utf-8")
    try:
        proc = subprocess.run(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--python",
                str(worker),
                "--",
                "--task-file",
                str(task_file),
            ],
            cwd=_repo_root(),
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    finally:
        task_file.unlink(missing_ok=True)

    result = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("RESULT:"):
            result = json.loads(line[len("RESULT:"):])
    if result is None:
        tail = "\n".join(((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines()[-30:])
        return {"status": "failed", "error": f"no RESULT line (exit {proc.returncode})\n{tail}"}
    if proc.returncode != 0 and result.get("status") == "success":
        result["status"] = "failed"
        result["error"] = f"worker exited {proc.returncode}"
    return result


def _load_existing_index(path: Path, dataset_version: str, replace: bool) -> dict:
    if replace or not path.is_file():
        return {
            "schema": SCHEMA,
            "datasetVersion": dataset_version,
            "generatedUtc": _now_utc(),
            "exportRevision": ANIMATION_EXPORT_REVISION,
            "byModel": {},
        }
    try:
        data = _load_json(path)
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            return data
    except Exception:
        pass
    return {
        "schema": SCHEMA,
        "datasetVersion": dataset_version,
        "generatedUtc": _now_utc(),
        "exportRevision": ANIMATION_EXPORT_REVISION,
        "byModel": {},
    }


def _upsert_animation(index: dict, model: dict, animation: dict, result: dict) -> None:
    by_model = index.setdefault("byModel", {})
    group = by_model.setdefault(
        model["id"],
        {
            "modelId": model["id"],
            "modelName": model["name"],
            "modelPath": model["path"],
            "skeletonPath": model["skeletonPath"],
            "animations": [],
        },
    )
    row = {
        "id": animation["id"],
        "name": animation["name"],
        "label": animation["label"],
        "packagePath": animation["packagePath"],
        "sourceJsonPath": animation["sourceJsonPath"],
        "skeletonPath": animation["skeletonPath"],
        "sequenceLength": animation.get("sequenceLength"),
        "status": result.get("status"),
        "assetDir": result.get("asset_dir"),
        "gltfPath": result.get("gltf_path"),
        "binPath": result.get("bin_path"),
        "animationName": animation["name"],
        "duration_s": result.get("duration_s"),
        "error": result.get("error"),
        "updatedUtc": _now_utc(),
    }
    animations = [existing for existing in group.get("animations") or [] if existing.get("id") != animation["id"]]
    animations.append(row)
    animations.sort(key=lambda item: str(item.get("label") or item.get("name") or "").lower())
    group["animations"] = animations


def _prune_unsuccessful(index: dict) -> None:
    by_model = index.get("byModel")
    if not isinstance(by_model, dict):
        return
    for model_id in list(by_model.keys()):
        group = by_model.get(model_id)
        if not isinstance(group, dict):
            by_model.pop(model_id, None)
            continue
        animations = [
            animation
            for animation in (group.get("animations") or [])
            if isinstance(animation, dict) and animation.get("status") == "success"
        ]
        if animations:
            group["animations"] = animations
        else:
            by_model.pop(model_id, None)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _mark_webasset_manifest(output_root: Path) -> None:
    path = output_root / "WebAssetManifest.json"
    if not path.is_file():
        return
    try:
        data = _load_json(path)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    data["web_animations"] = "website/animation-index.json"
    data["web_animations_updated_utc"] = _now_utc()
    _write_json(path, data)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build animated web glTF variants for SK models.")
    parser.add_argument("--repo-root", type=Path, default=_repo_root())
    parser.add_argument("--dataset-version", default=None)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--archive-json-root", type=Path, required=True)
    parser.add_argument("--retoc-root", type=Path, required=True)
    parser.add_argument("--usmap", type=Path, required=True)
    parser.add_argument("--cue4parse-root", type=Path, default=Path(r"E:\Github\CUE4Parse"))
    parser.add_argument("--model-index", type=Path, default=None)
    parser.add_argument("--output-index", type=Path, default=None)
    parser.add_argument("--animation-cache-root", type=Path, default=None)
    parser.add_argument("--blender", type=Path, default=_default_blender())
    parser.add_argument("--worker", type=Path, default=SCRIPT_DIR / "BuildWebAssetWorker.py")
    parser.add_argument("--mode", choices=("none", "smoke", "full"), default="smoke")
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-s", type=int, default=360)
    parser.add_argument("--texture-size", type=int, default=1024)
    parser.add_argument("--texture-quality", type=int, default=75)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "none":
        _log("Skipped by --mode none")
        return 0

    repo_root = args.repo_root.resolve()
    source_root = (args.source_root or _default_source_root()).resolve()
    dataset_version = args.dataset_version or source_root.name
    output_root = (args.output_root or (source_root / "WebAssets")).resolve()
    archive_json_root = args.archive_json_root.resolve()
    model_index_path = (args.model_index or (repo_root / "website" / "model-index.json")).resolve()
    output_index = (args.output_index or (repo_root / "website" / "animation-index.json")).resolve()
    animation_cache_root = (
        args.animation_cache_root or (repo_root / "PipelineLogs" / "AnimationExtract" / dataset_version)
    ).resolve()
    texture_profile = {
        "format": "webp",
        "max_dimension": args.texture_size,
        "quality": args.texture_quality,
        "upscale": False,
    }

    for label, path in (
        ("source root", source_root),
        ("output root", output_root),
        ("archive JSON root", archive_json_root),
        ("retoc root", args.retoc_root),
        ("usmap", args.usmap),
        ("CUE4Parse root", args.cue4parse_root),
        ("blender", args.blender),
        ("worker", args.worker),
    ):
        if label in {"usmap", "blender", "worker"}:
            ok = path.is_file()
        else:
            ok = path.is_dir()
        if not ok:
            raise SystemExit(f"{label} not found: {path}")

    model_index = _load_model_index(model_index_path)
    inventory = _load_inventory(source_root / "ModelData" / "SK_Data.json")
    texture_map, _manifest = _load_texture_map(output_root)
    models = _discover_models(
        model_index=model_index,
        inventory=inventory,
        archive_json_root=archive_json_root,
        output_root=output_root,
        only=args.only,
    )
    animations_by_skeleton = _discover_animations(archive_json_root)
    pairs = _select_pairs(models, animations_by_skeleton, args.mode, args.limit)

    _log(f"Animation models: {len(models)} compatible SK model(s)")
    _log(f"Animation skeletons: {len(animations_by_skeleton)} skeleton group(s)")
    if UNSUPPORTED_ANIMATION_COUNTS:
        summary = ", ".join(f"{reason}: {count}" for reason, count in sorted(UNSUPPORTED_ANIMATION_COUNTS.items()))
        _log(f"Skipped unsupported animation clip(s): {summary}")
    _log(f"Selected {len(pairs)} animation build(s)")
    if not pairs:
        return 0

    selected_entries = [("SK", model["entry"]) for model, _animation in pairs]
    generated_overrides_by_entry = web_assets._prepare_generated_material_overrides(
        source_root=source_root,
        output_root=output_root,
        selected=selected_entries,
        texture_size=args.texture_size,
        texture_quality=args.texture_quality,
        dry_run=args.dry_run,
    )

    replace_index = args.mode == "full" and args.limit is None and not args.only
    index = _load_existing_index(output_index, dataset_version, replace=replace_index)
    index.update(
        {
            "schema": SCHEMA,
            "datasetVersion": dataset_version,
            "generatedUtc": _now_utc(),
            "exportRevision": ANIMATION_EXPORT_REVISION,
            "sourceModelIndex": model_index_path.resolve().relative_to(repo_root.resolve()).as_posix(),
            "sourceArchiveJson": str(archive_json_root),
            "sourceWebAssets": f"{dataset_version}/WebAssets",
        }
    )

    ok = 0
    failed = 0
    unsupported = 0
    runtime_unsupported_packages: set[str] = set()
    RUNTIME_UNSUPPORTED_ANIMATION_COUNTS.clear()
    start = time.time()
    for idx, (model, animation) in enumerate(pairs, start=1):
        label = f"{model['name']} + {animation['name']}"
        _log(f"[{idx}/{len(pairs)}] {label}")
        try:
            result = None if args.dry_run else _existing_animation_result(output_root, model, animation, args.force)
            if result is None:
                if animation["packagePath"] in runtime_unsupported_packages:
                    result = {
                        "status": "unsupported",
                        "error": "Animation package was already marked unsupported in this run.",
                        "unsupported_reason": "unsupported-compression",
                    }
                else:
                    ueanim_path = _extract_animation(
                        repo_root=repo_root,
                        cue4parse_root=args.cue4parse_root.resolve(),
                        retoc_root=args.retoc_root.resolve(),
                        usmap=args.usmap.resolve(),
                        animation_cache_root=animation_cache_root,
                        animation=animation,
                        force=args.force,
                        dry_run=args.dry_run,
                    )
                    generated_overrides = generated_overrides_by_entry.get(web_assets._progress_key("SK", model["entry"]), {})
                    result = _run_worker(
                        blender=args.blender.resolve(),
                        worker=args.worker.resolve(),
                        source_root=source_root,
                        output_root=output_root,
                        model=model,
                        animation=animation,
                        ueanim_path=ueanim_path,
                        texture_map=texture_map,
                        texture_profile=texture_profile,
                        generated_overrides=generated_overrides,
                        force=args.force,
                        timeout_s=args.timeout_s,
                        dry_run=args.dry_run,
                    )
        except UnsupportedAnimationError as exc:
            runtime_unsupported_packages.add(animation["packagePath"])
            RUNTIME_UNSUPPORTED_ANIMATION_COUNTS[exc.reason] = (
                RUNTIME_UNSUPPORTED_ANIMATION_COUNTS.get(exc.reason, 0) + 1
            )
            result = {"status": "unsupported", "error": str(exc), "unsupported_reason": exc.reason}
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

        _upsert_animation(index, model, animation, result)
        if result.get("status") in {"success", "dry_run"}:
            ok += 1
            _log(f"  OK {result.get('gltf_path') or result.get('asset_dir')}")
        elif result.get("status") == "unsupported":
            unsupported += 1
            _log(f"  SKIP unsupported {result.get('unsupported_reason')}: {result.get('error')}")
        else:
            failed += 1
            _log(f"  FAIL {result.get('error')}")

    if not args.dry_run:
        _prune_unsuccessful(index)
    index["totals"] = {
        "modelGroups": len(index.get("byModel") or {}),
        "animations": sum(len(group.get("animations") or []) for group in (index.get("byModel") or {}).values()),
        "success": sum(
            1
            for group in (index.get("byModel") or {}).values()
            for animation in group.get("animations") or []
            if animation.get("status") == "success"
        ),
        "unsupported": unsupported,
    }
    if not args.dry_run:
        _write_json(output_index, index)
        _mark_webasset_manifest(output_root)
        web_assets._write_size_report(
            output_root=output_root,
            source_root=source_root,
            texture_records={},
            texture_profile=texture_profile,
        )
        _log(f"Wrote {output_index}")
    if RUNTIME_UNSUPPORTED_ANIMATION_COUNTS:
        summary = ", ".join(
            f"{reason}: {count}" for reason, count in sorted(RUNTIME_UNSUPPORTED_ANIMATION_COUNTS.items())
        )
        _log(f"Runtime skipped unsupported animation clip(s): {summary}")
    _log(f"Done: {ok} ok, {unsupported} unsupported, {failed} failed in {time.time() - start:.1f}s")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
