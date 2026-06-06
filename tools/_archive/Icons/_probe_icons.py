"""Probe icon rendering on a curated sample of .glb models.

Purpose: generate a small, representative batch of icons so you can eyeball
framing / lighting / material quality before kicking off a full-library run.

Picks a mix deliberately:
    * SM single-slot small prop (FireOil)
    * SM multi-part architectural (a big building piece)
    * SM weapon
    * SM with materials_source=none (flat-white)
    * SM base-building piece
    * SK skeletal mesh (character/armour) rendered in default pose

Samples are loaded from tools/ModelData/BuildProgress.json; if that doesn't
exist the script falls back to SM_Data.json / SK_Data.json. For each pick it
invokes RenderIconsWorker.py as a fresh Blender subprocess, pointing PNGs to
`tools/Icons/probe_out/<model_name>/`. Finally it prints a compact summary and
the absolute paths you can open in Explorer.

Usage:
    python tools/Icons/_probe_icons.py
    python tools/Icons/_probe_icons.py --cameras "Perspective Front,Orthographic Front"
    python tools/Icons/_probe_icons.py --engine CYCLES --samples 64
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "0.11.0.10"
BLENDER_EXE = REPO_ROOT / "blender-5.0.0-windows-x64" / "blender.exe"
WORKER = Path(__file__).with_name("RenderIconsWorker.py")
PROBE_OUT = Path(__file__).with_name("probe_out")
BUILD_PROGRESS = REPO_ROOT / "tools" / "ModelData" / "BuildProgress.json"


# Hand-picked "representative" targets. Each entry is a repo-relative .uemodel
# path (matches the key in BuildProgress.json); the .glb sits next to it.
# Keep these grouped so the printout helps us reason about results.
SAMPLE_TARGETS: list[tuple[str, str]] = [
    # (label, uemodel-relative-path)
    ("SM single-slot prop",
     "RSDragonwilds/Content/Gameplay/Character/Player/Equipment/Held/Thrown/FireOil/SM_Ranged_Fire_Oil.uemodel"),
    ("SM multi-slot building",
     "RSDragonwilds/Content/Art/Architecture/Misc/GreatHall/SM_DR_Gateway_Center.uemodel"),
    ("SM weapon/metal (post-param-fix)",
     "RSDragonwilds/Content/Art/BaseBuilding/Crafting/SM_WeaponcraftingBench_01.uemodel"),
    ("SM with materials_source=none",
     "Engine/Content/EditorMeshes/Camera/SM_CraneRig_Mount.uemodel"),
    ("SM campfire prop",
     "RSDragonwilds/Content/Art/BaseBuilding/Prop/Misc/SM_Campfire_01.uemodel"),
    ("SK skeletal mesh (default pose)",
     None),  # resolved dynamically below from SK_Data.json
]


def _resolve_sk_sample() -> str | None:
    """Pick any SK_ entry with a reasonably short name; prefer character shirts/armour."""
    sk_json = REPO_ROOT / "tools" / "ModelData" / "SK_Data.json"
    if not sk_json.is_file():
        return None
    try:
        data = json.loads(sk_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    entries = data.get("entries", [])
    if not entries:
        return None
    # Prefer a Player/Armour mesh since those are the 'interesting' case.
    preferred_markers = ("Armour/", "Player/", "Hero/")
    for e in entries:
        p = e.get("path", "")
        if any(m in p for m in preferred_markers) and p.endswith(".uemodel"):
            return p
    return entries[0].get("path")


def _load_build_progress() -> dict:
    if not BUILD_PROGRESS.is_file():
        return {"entries": {}}
    return json.loads(BUILD_PROGRESS.read_text(encoding="utf-8"))


def _glb_for_uemodel(uemodel_rel: str, progress: dict) -> Path | None:
    """Given a uemodel repo-rel path, return the .glb abs path if built."""
    ent = progress.get("entries", {}).get(uemodel_rel)
    if ent and ent.get("status") == "success" and ent.get("glb_path"):
        glb_abs = SOURCE_ROOT / ent["glb_path"]
        return glb_abs if glb_abs.is_file() else None
    # fallback: same folder as uemodel, replace extension
    guess = (SOURCE_ROOT / uemodel_rel).with_suffix(".glb")
    return guess if guess.is_file() else None


def _run_worker_once(
    glb: Path,
    out_dir: Path,
    cameras: str,
    resolution: int,
    engine: str,
    samples: int,
    transparent: int,
    save_blend: int,
) -> dict:
    cmd = [
        str(BLENDER_EXE),
        "--background",
        "--factory-startup",  # defence-in-depth: ignore any custom user startup.blend
        "--python", str(WORKER),
        "--",
        "--glb", str(glb),
        "--out-dir", str(out_dir),
        "--cameras", cameras,
        "--resolution", str(resolution),
        "--engine", engine,
        "--samples", str(samples),
        "--transparent", str(transparent),
        "--save-blend", str(save_blend),
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0

    # Extract RESULT: line
    result: dict | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            try:
                result = json.loads(line[len("RESULT:"):])
            except Exception:
                result = None
    return {
        "returncode": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "result": result,
        "stderr_tail": proc.stderr[-400:] if proc.stderr else "",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cameras", type=str, default="",
                   help="Comma-separated camera names. Empty = all 8 cameras.")
    p.add_argument("--resolution", type=int, default=512)
    p.add_argument("--engine", type=str, default="BLENDER_EEVEE",
                   choices=["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"])
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--transparent", type=int, default=1, choices=[0, 1])
    p.add_argument("--save-blend", type=int, default=0, choices=[0, 1],
                   help="Also save a .blend snapshot per sample (for framing debug).")
    args = p.parse_args()

    if not BLENDER_EXE.is_file():
        print(f"ERR blender not found: {BLENDER_EXE}", file=sys.stderr)
        return 2
    if not WORKER.is_file():
        print(f"ERR worker not found: {WORKER}", file=sys.stderr)
        return 2

    progress = _load_build_progress()

    targets: list[tuple[str, str]] = []
    for label, ue in SAMPLE_TARGETS:
        if ue is None:
            sk = _resolve_sk_sample()
            if sk:
                targets.append((label, sk))
        else:
            targets.append((label, ue))

    print(f"[probe] targets: {len(targets)}")
    PROBE_OUT.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    for label, ue in targets:
        glb = _glb_for_uemodel(ue, progress)
        slot_dir = PROBE_OUT / Path(ue).stem
        print("")
        print(f"[probe] === {label} ===")
        print(f"        uemodel : {ue}")
        print(f"        glb     : {glb}")
        print(f"        out_dir : {slot_dir}")
        if glb is None:
            print(f"[probe]   SKIP (glb missing)")
            summary.append({"label": label, "uemodel": ue, "glb": None, "ok": False,
                            "reason": "glb_missing"})
            continue
        out = _run_worker_once(
            glb=glb,
            out_dir=slot_dir,
            cameras=args.cameras,
            resolution=args.resolution,
            engine=args.engine,
            samples=args.samples,
            transparent=args.transparent,
            save_blend=args.save_blend,
        )
        r = out.get("result") or {}
        ok = r.get("ok", False)
        renders = r.get("renders", [])
        fit = r.get("fit") or {}
        print(f"[probe]   rc={out['returncode']}  ok={ok}  "
              f"renders={sum(1 for x in renders if x.get('ok'))}/{len(renders)}  "
              f"fit={fit.get('fit')}  mesh_count={fit.get('mesh_count')}  "
              f"scale={fit.get('scale_factor')}  total={out['elapsed_s']}s")
        if not ok and out.get("stderr_tail"):
            print(f"[probe]   stderr_tail: {out['stderr_tail'].strip()}")
        summary.append({
            "label": label,
            "uemodel": ue,
            "glb": str(glb),
            "out_dir": str(slot_dir),
            "ok": ok,
            "fit": fit,
            "renders": [{"camera": x.get("camera"), "ok": x.get("ok"), "png": x.get("png"),
                         "elapsed_s": x.get("elapsed_s"), "err": x.get("err")}
                        for x in renders],
            "worker_rc": out["returncode"],
            "worker_elapsed_s": out["elapsed_s"],
        })

    summary_path = PROBE_OUT / "_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("")
    print(f"[probe] summary written: {summary_path}")
    print(f"[probe] open this folder to inspect icons: {PROBE_OUT}")
    return 0 if all(s.get("ok") for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
