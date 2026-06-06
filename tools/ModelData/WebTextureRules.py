from __future__ import annotations

from pathlib import Path


def normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_unreal_helper_texture(rel_path: str) -> bool:
    rel = normalize_rel_path(rel_path)
    rel_low = rel.lower()
    if Path(rel_low).suffix != ".hdr":
        return False
    return (
        "/curveatlases/" in rel_low
        or "/engine_materialfunctions02/" in rel_low
        or "/haircolourcurves/" in rel_low
        or "/pivpaintertextures/" in rel_low
    )


def is_web_texture_candidate(rel_path: str) -> bool:
    return bool(normalize_rel_path(rel_path)) and not is_unreal_helper_texture(rel_path)
