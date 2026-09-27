from __future__ import annotations

import json
from pathlib import Path


BASE_EQUIPMENT_REL = Path("RSDragonwilds/Content/Gameplay/Character/Player/Equipment")
FEATURES_REL = Path("RSDragonwilds/Plugins/GameFeatures")
DEFAULT_EXCLUDED_FEATURES = frozenset({"FutureMajorVersion"})


def configured_dataset_version(repo_root: Path) -> str:
    config_path = repo_root / "website" / "data.config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Could not read the current dataset version from {config_path}. "
            "Pass --dataset-version explicitly."
        ) from exc
    version = str(data.get("datasetVersion") or "").strip()
    if not version:
        raise SystemExit(
            f"Current dataset version is missing from {config_path}. "
            "Pass --dataset-version explicitly."
        )
    return version


def iter_player_equipment_roots(
    archive_json_root: Path,
    *,
    excluded_features: frozenset[str] = DEFAULT_EXCLUDED_FEATURES,
) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    base = archive_json_root / BASE_EQUIPMENT_REL
    if base.is_dir():
        roots.append(("Base", base))

    features = archive_json_root / FEATURES_REL
    if features.is_dir():
        for feature in sorted(features.iterdir(), key=lambda path: path.name.lower()):
            if not feature.is_dir() or feature.name in excluded_features:
                continue
            equipment = feature / "Content/Gameplay/Character/Player/Equipment"
            if equipment.is_dir():
                roots.append((feature.name, equipment))
    return roots


def feature_from_archive_path(path: Path, archive_json_root: Path) -> str:
    try:
        parts = path.resolve().relative_to(archive_json_root.resolve()).parts
    except ValueError:
        return "Base"
    try:
        marker = parts.index("GameFeatures")
    except ValueError:
        return "Base"
    return parts[marker + 1] if marker + 1 < len(parts) else "Base"


def feature_from_model_path(path: str) -> str:
    marker = "/Plugins/GameFeatures/"
    value = str(path or "").replace("\\", "/")
    if marker not in value:
        return "Base"
    remainder = value.split(marker, 1)[1]
    return remainder.split("/", 1)[0] or "Base"


def is_excluded_feature_path(
    path: str,
    *,
    excluded_features: frozenset[str] = DEFAULT_EXCLUDED_FEATURES,
) -> bool:
    return feature_from_model_path(path) in excluded_features
