from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


MODEL_DATA_DIR = Path(__file__).resolve().parent / "ModelData"
if str(MODEL_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DATA_DIR))

from WebTextureRules import is_web_texture_candidate


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _display_name(name: str) -> str:
    for suffix in (".uemodel", ".gltf", ".glb"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _path_hash(asset_dir: str | None) -> str | None:
    if not asset_dir:
        return None
    last = asset_dir.rstrip("/").rsplit("/", 1)[-1]
    if "-" not in last:
        return None
    return last.rsplit("-", 1)[-1] or None


def build_index(manifest_path: Path, output_path: Path, dataset_version: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries") or {}
    if not isinstance(entries, dict):
        raise ValueError(f"Manifest entries must be an object: {manifest_path}")

    rows: list[dict] = []
    for key, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("status") != "success":
            continue
        kind = str(entry.get("kind") or "")
        name = str(entry.get("name") or "")
        source_path = str(entry.get("path") or "")
        gltf_path = str(entry.get("gltf_path") or "")
        if not kind or not name or not source_path or not gltf_path:
            continue
        missing = [
            item for item in (entry.get("missing_textures") or [])
            if isinstance(item, str) and item and is_web_texture_candidate(item)
        ]
        rows.append(
            {
                "id": key,
                "kind": kind,
                "name": name,
                "displayName": _display_name(name),
                "path": source_path,
                "gltfPath": gltf_path,
                "binPath": entry.get("bin_path"),
                "assetDir": entry.get("asset_dir"),
                "pathHash": _path_hash(entry.get("asset_dir")),
                "sourceTextureCount": len(entry.get("source_textures") or []),
                "optimizedTextureCount": len(entry.get("optimized_textures") or []),
                "missingTextureCount": len(missing),
                "missingTextures": missing,
            }
        )

    rows.sort(key=lambda row: (row["kind"], row["displayName"].lower(), row["path"].lower()))
    out = {
        "schema": "RSDWModel.WebsiteModelIndex.v1",
        "datasetVersion": dataset_version,
        "sourceManifest": f"{dataset_version}/WebAssets/WebAssetManifest.json",
        "count": len(rows),
        "models": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the website model search index.")
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument("--dataset-version", default="0.11.2.2")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    manifest = args.manifest or repo_root / args.dataset_version / "WebAssets" / "WebAssetManifest.json"
    output = args.output or repo_root / "website" / "model-index.json"
    result = build_index(manifest.resolve(), output.resolve(), args.dataset_version)
    print(f"wrote {result['count']} model index rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
