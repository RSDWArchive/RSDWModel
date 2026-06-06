"""Targeted SK_ probe harness.

Picks one representative sample from each SK_ material-resolution scenario
(shared-materials Player set, cross-sibling Armour set, Prop template set,
plugin-scoped unresolved set) and runs each through BuildGLBWorker.py in a
fresh headless Blender. Prints the RESULT JSON for each sample so we can
eyeball how plugin-mount-aware resolution behaves.

Does NOT touch BuildProgress.json; .glb + .blend land next to the source as
usual (so you can open them and verify).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "0.11.0.10"
BLENDER = REPO / "blender-5.0.0-windows-x64" / "blender.exe"
WORKER = HERE / "BuildGLBWorker.py"


# Curated list of SK_ sample paths + what we expect each to exercise.
# (relative to SRC; see SK_Data.json)
SAMPLES: list[tuple[str, str]] = [
    # --- Player set: relies on centralized per-body-part Materials/ folders ---
    ("RSDragonwilds/Content/Art/Skeleton/Player/Body/F_MED_Body_A_01/SK_F_MED_Body_A_01.uemodel",
     "Player/Body variant -> MI_F_DefaultCharacter_Body.json (centralized)"),
    ("RSDragonwilds/Content/Art/Skeleton/Player/Body/M_MED_Body_C_01/SK_M_MED_Body_C_01.uemodel",
     "Player/Body male variant -> MI_M_DefaultCharacter_Body.json"),
    ("RSDragonwilds/Content/Art/Skeleton/Player/Heads/F_MED_Head_A_01/SK_F_MED_Head_A_01.uemodel",
     "Player/Heads multi-slot -> head + eyes + teeth (Universal shared)"),
    ("RSDragonwilds/Content/Art/Skeleton/Player/Hair/M_MED_FacialHair_A_01/SK_M_MED_FacialHair_A_01.uemodel",
     "Player/Hair -> local Materials folder"),
    # --- Shared materials (eye glow, ghost, teeth) ---
    ("RSDragonwilds/Content/Art/Skeleton/NPC/Humanoid/U_MED_Skeleton_Human_Archer_01/SK_U_MED_Skeleton_Human_Archer_01.uemodel",
     "NPC armour with refs to Skeleton/Shared/..."),
    # --- Armour: F_MED sourcing M_MED owner ---
    ("RSDragonwilds/Content/Art/Skeleton/Armour/F_MED/BronzeArmour_01/SK_F_MED_Upperhalf_BronzeArmour_01.uemodel",
     "F_MED Bronze armour -> M_MED's MI (cross-sibling)"),
    # --- Prop template (MI_Steel, MI_Wood etc. under Prop/Template) ---
    ("RSDragonwilds/Content/Art/Animation/Ranged_Beast/Bow/SK_Beast_Bow_01.uemodel",
     "Prop/Template references (Steel/Leather/Wood/Rag)"),
    # --- Plugin-scoped unresolved: should fail gracefully, not crash ---
    ("RSDragonwilds/Content/Art/Skeleton/NPC/Humanoid/M_MED_Zombie_01/SK_M_MED_Zombie_01.uemodel",
     "Plugin-scoped /FutureMajorVersion/... refs (no JSONs shipped)"),
    ("RSDragonwilds/Plugins/GameFeatures/DowdunReach/Content/Art/Skeleton/Armour/F_MED/Black_Knight_Melee_01/SK_F_MED_BODY_Black_Knight_Melee_01.uemodel",
     "Plugin-internal /DowdunReach/... refs (no JSONs shipped)"),
]


def _load_entry(rel: str, sk_data: dict) -> dict | None:
    for e in sk_data["entries"]:
        if e["path"] == rel:
            return e
    return None


def _run_worker(entry: dict) -> dict:
    task = {"source_root": str(SRC), "save_blend": True, "entry": entry}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".task.json", delete=False,
                                      encoding="utf-8") as tf:
        json.dump(task, tf)
        tf.flush()
        task_path = Path(tf.name)

    start = time.time()
    try:
        cmd = [
            str(BLENDER), "--background", "--factory-startup",
            "--python", str(WORKER), "--",
            "--task-file", str(task_path),
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        result: dict | None = None
        for line in out.splitlines():
            if line.startswith("RESULT:"):
                try:
                    result = json.loads(line[len("RESULT:"):])
                except Exception:
                    pass
        if result is None:
            result = {"status": "failed", "error": "no RESULT: line parsed",
                      "stdout_tail": out[-400:], "stderr_tail": err[-400:]}
        result["_elapsed_s"] = round(time.time() - start, 2)
        return result
    finally:
        try:
            task_path.unlink()
        except Exception:
            pass


def _fmt_slot(s: dict) -> str:
    """One-line slot summary: slot name -> MI path or 'hybrid'/'empty' + role/param counts."""
    slot = s.get("slot", "?")
    # Worker RESULT emits `mi` as the MI filename (see _build_material_from_mi).
    mi = s.get("mi") or s.get("mi_json") or s.get("mi_json_path")
    source = s.get("source", "?")
    roles = s.get("roles") or []
    params = s.get("params") or []
    mi_tail = Path(mi).name if mi else "<none>"
    return (f"    {slot:40}  src={source:8}  "
            f"mi={mi_tail:48}  roles={len(roles)}  params={len(params)}"
            + (f"  [{'|'.join(params)}]" if params else ""))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()

    if not BLENDER.is_file():
        print(f"blender not found: {BLENDER}", file=sys.stderr)
        return 1
    if not WORKER.is_file():
        print(f"worker not found: {WORKER}", file=sys.stderr)
        return 1

    sk_data = json.loads((HERE / "SK_Data.json").read_text(encoding="utf-8"))
    tasks: list[tuple[str, str, dict]] = []
    for rel, desc in SAMPLES:
        e = _load_entry(rel, sk_data)
        if e is None:
            print(f"[WARN] not in SK_Data.json: {rel}")
            continue
        tasks.append((rel, desc, e))

    print(f"Running {len(tasks)} SK_ samples with {args.workers} workers...")
    results: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(_run_worker, e): rel for (rel, _, e) in tasks}
        for fut in cf.as_completed(futs):
            rel = futs[fut]
            results[rel] = fut.result()

    print("\n============================================================")
    for rel, desc, _ in tasks:
        r = results[rel]
        status = r.get("status")
        elapsed = r.get("_elapsed_s")
        print(f"\n[{status}] ({elapsed}s)  {rel}")
        print(f"   intent: {desc}")
        if status != "success":
            print(f"   error: {r.get('error','?')}")
            if "stdout_tail" in r:
                print(f"   stdout-tail: ...{r['stdout_tail']}")
            if "stderr_tail" in r:
                print(f"   stderr-tail: ...{r['stderr_tail']}")
            continue
        slots = r.get("slots") or r.get("material_slots") or []
        print(f"   slot_count={len(slots)}  materials_source={r.get('materials_source','?')}")
        for s in slots:
            print(_fmt_slot(s))

    bad = sum(1 for r in results.values() if r.get("status") != "success")
    print(f"\nDone: {len(results)-bad} ok, {bad} failed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
