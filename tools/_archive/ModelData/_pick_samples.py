"""One-shot helper: find SM_ entries that exercise each worker code path."""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "0.11.0.10"
sm = json.loads((HERE / "SM_Data.json").read_text(encoding="utf-8"))["entries"]


def names_only(paths: list[str]) -> list[str]:
    return [Path(p).name for p in paths]


def kinds(paths: list[str]) -> set[str]:
    out = set()
    for n in names_only(paths):
        m = re.match(r"^([A-Za-z]+)_", n)
        if m:
            out.add(m.group(1))
    return out


def has_orm_suffix(paths: list[str]) -> bool:
    return any(Path(p).stem.lower().endswith("_orm") for p in paths)


def locations(entry: dict) -> set[str]:
    return {i["location"] for i in entry["Materials"]["items"]}


buckets: dict[str, list[str]] = {
    "mi_only_same_folder": [],
    "mi_only_mesh_materials_subfolder": [],
    "mi_only_parent_materials": [],
    "mt_in_candidates": [],
    "m_prefix_in_candidates": [],
    "no_material_json_has_hybrid": [],
    "no_material_json_no_hybrid": [],
    "hybrid_has_orm": [],
}

for e in sm:
    mat_paths = e["Materials"]["material_json_paths"]
    mat_kinds = kinds(mat_paths)
    hybrid = e["MaterialsHybrid"]["texture_image_paths"]
    locs = locations(e)

    if mat_kinds == {"MI"} and locs == {"same_folder_as_uemodel"}:
        buckets["mi_only_same_folder"].append(e["path"])
    if mat_kinds == {"MI"} and locs == {"mesh_folder_materials_subfolder"}:
        buckets["mi_only_mesh_materials_subfolder"].append(e["path"])
    if mat_kinds == {"MI"} and locs == {"parent_materials_folder"}:
        buckets["mi_only_parent_materials"].append(e["path"])
    if "MT" in mat_kinds:
        buckets["mt_in_candidates"].append(e["path"])
    if "M" in mat_kinds:
        buckets["m_prefix_in_candidates"].append(e["path"])
    if not mat_paths and hybrid:
        buckets["no_material_json_has_hybrid"].append(e["path"])
    if not mat_paths and not hybrid:
        buckets["no_material_json_no_hybrid"].append(e["path"])
    if has_orm_suffix(hybrid):
        buckets["hybrid_has_orm"].append(e["path"])

for name, items in buckets.items():
    print(f"== {name}: {len(items)} ==")
    for p in items[:3]:
        print(f"  {p}")
    print()
