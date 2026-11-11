"""Measure every dune placement's atlas footprint without guessing vertex deformation."""
import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from world_source import read_json, write_json
from world_texture import sample_bilinear


def footprint_coverage(bounds, atlas_bounds, alpha, samples=9):
    lo, hi = np.asarray(bounds), np.asarray(atlas_bounds)
    x, y = np.meshgrid(np.linspace(lo[0, 0], lo[1, 0], samples), np.linspace(lo[0, 1], lo[1, 1], samples))
    u, v = (x-hi[0, 0])/(hi[1, 0]-hi[0, 0]), (y-hi[0, 1])/(hi[1, 1]-hi[0, 1])
    inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    coverage = sample_bilinear(alpha, u*alpha.shape[1]-.5, v*alpha.shape[0]-.5)*inside
    return {"probe_count": samples*samples, "mean_atlas_alpha": float(coverage.mean()),
            "uncovered_probe_fraction": float((coverage < .01).mean()),
            "fully_covered_probe_fraction": float((coverage > .99).mean())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if read_json(args.root / "PipelineRun.json").get("status") != "success":
        raise ValueError("Finish the world rebuild before auditing dune placement coverage")
    shared = read_json(args.root / "WorldShared.json")
    index = read_json(args.root / "VisualAuditIndex.json")
    variants = {r["asset"]: r for r in index["variants"] if any(
        shared["materials"].get(ref, {}).get("master", "").rsplit("/", 1)[-1].split(".")[0]
        in ("M_UMS_Dunes", "M_UMS_Dunes_Cheap") for ref in r["materials"])}
    placements, tiles = {}, {}
    for path in sorted((args.root / "sections").glob("*.json")):
        section = read_json(path)
        for p in section["placements"]:
            if p["asset"] in variants:
                placements[p["id"]] = p
        for tile in section["terrain"]:
            tiles[tile["source"]] = tile
    spanning = read_json(args.root / "SpanningPlacements.json")
    for p in spanning:
        if p["asset"] in variants:
            placements[p["id"]] = p
    images, rows = {}, []
    for p in placements.values():
        variant = variants[p["asset"]]
        projected = [shared["materials"][m] for m in variant["materials"]
                     if shared["materials"].get(m, {}).get("projection") == "landscape_color"]
        b = p["bounds"]
        overlap = [t for t in tiles.values() if all(b[0][i] <= t["bounds"][1][i] and b[1][i] >= t["bounds"][0][i] for i in (0, 1))]
        row = {"placement": p["id"], "asset": p["asset"], "model": variant["source"], "bounds_cm": b,
               "span_m": [(b[1][i]-b[0][i])/100 for i in range(3)],
               "overlapping_landscapes_xy": [{"source": t["source"], "material": t["material"], "bounds_cm": t["bounds"]} for t in overlap],
               "manual_status": "needs_game_reference_and_shader_graph", "coverage": []}
        for desc in projected:
            file = desc["textures"]["landscape_color"]
            if file not in images:
                with Image.open(args.root / file) as image:
                    images[file] = np.asarray(image.convert("RGBA"))[:, :, 3].astype(np.float32)/255
            row["coverage"].append(footprint_coverage(b, desc["landscape_bounds_cm"], images[file]))
        rows.append(row)
    actual = Counter(r["asset"] for r in rows)
    missing = {key: {"expected": v["placements"], "actual": actual[key]} for key, v in variants.items() if actual[key] != v["placements"]}
    report = {"schema": "RSDW.DuneFootprintAudit.v1", "scene_sha256": index["scene_sha256"],
              "status": "complete_pending_manual_review" if not missing else "incomplete",
              "method": "9x9 world-XY AABB probes per placement, sampling atlas alpha; NOT visible-triangle coverage, a height test, RVT parity or proof that a texture is missing. No geometry/color is changed.",
              "summary": {"variants": len(variants), "placements": len(rows), "missing": missing,
                          "no_landscape_overlap_xy": sum(not r["overlapping_landscapes_xy"] for r in rows),
                          "over_500m_horizontal_span": sum(max(r["span_m"][:2]) > 500 for r in rows)},
              "placements": sorted(rows, key=lambda r: -max(r["span_m"][:2]))}
    write_json(args.out, report)
    print(report["status"], report["summary"], flush=True)
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
