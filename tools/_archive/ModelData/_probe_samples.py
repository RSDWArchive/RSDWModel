"""Randomized sample harness for BuildGLBWorker.py.

Picks a stratified random set of SM_ entries (covering every material-assembly
code path), runs each through a fresh headless Blender worker in parallel, and
inspects the per-slot RESULT JSON for any signs of a bad build.

Does NOT touch BuildProgress.json. Writes .glb / .blend next to each sample's
source as the worker normally would.

Usage:
    python tools/ModelData/_probe_samples.py            # 1 round, default seed
    python tools/ModelData/_probe_samples.py --rounds 3 # 3 rounds, different seeds
    python tools/ModelData/_probe_samples.py --seed 123 --per-bucket 2 --workers 4
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "0.11.0.10"
BLENDER = REPO / "blender-5.0.0-windows-x64" / "blender.exe"
WORKER = HERE / "BuildGLBWorker.py"


# ---------------------------------------------------------------------------
# Stratification
# ---------------------------------------------------------------------------

def _kinds(paths: list[str]) -> set[str]:
    out = set()
    for n in (Path(p).name for p in paths):
        m = re.match(r"^([A-Za-z]+)_", n)
        if m:
            out.add(m.group(1))
    return out


def _has_orm_suffix(paths: list[str]) -> bool:
    return any(Path(p).stem.lower().endswith(("_orm", "_arm", "_mra")) for p in paths)


def _locations(entry: dict) -> set[str]:
    return {i["location"] for i in entry["Materials"]["items"]}


def bucketize(entries: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {
        "mi_same_folder": [],
        "mi_mesh_subfolder": [],
        "mi_parent_materials": [],
        "mt_present": [],
        "m_present": [],
        "hybrid_only": [],
        "hybrid_orm": [],
        "no_material_no_hybrid": [],
        "many_materials": [],
    }
    for e in entries:
        mat_paths = e["Materials"]["material_json_paths"]
        k = _kinds(mat_paths)
        hybrid = e["MaterialsHybrid"]["texture_image_paths"]
        locs = _locations(e)

        if k == {"MI"} and locs == {"same_folder_as_uemodel"}:
            buckets["mi_same_folder"].append(e)
        if k == {"MI"} and locs == {"mesh_folder_materials_subfolder"}:
            buckets["mi_mesh_subfolder"].append(e)
        if k == {"MI"} and locs == {"parent_materials_folder"}:
            buckets["mi_parent_materials"].append(e)
        if "MT" in k:
            buckets["mt_present"].append(e)
        if "M" in k:
            buckets["m_present"].append(e)
        if not mat_paths and hybrid:
            buckets["hybrid_only"].append(e)
        if not mat_paths and not hybrid:
            buckets["no_material_no_hybrid"].append(e)
        if _has_orm_suffix(hybrid):
            buckets["hybrid_orm"].append(e)
        if len(mat_paths) >= 5:
            buckets["many_materials"].append(e)
    return buckets


def stratified_sample(
    entries: list[dict], per_bucket: int, rng: random.Random
) -> list[tuple[str, dict]]:
    buckets = bucketize(entries)
    picks: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for name, pool in buckets.items():
        if not pool:
            continue
        n = min(per_bucket, len(pool))
        chosen = rng.sample(pool, n)
        for e in chosen:
            if e["path"] in seen:
                continue
            seen.add(e["path"])
            picks.append((name, e))
    return picks


# ---------------------------------------------------------------------------
# Worker invocation
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    bucket: str
    entry: dict
    result: dict
    duration_s: float


def _load_known_role_keys() -> set[str]:
    """Parse MI_SLOT_ROLES from BuildGLBWorker.py so the suspicion check stays
    in sync with the worker's actual role map."""
    text = (HERE / "BuildGLBWorker.py").read_text(encoding="utf-8")
    m = re.search(r"MI_SLOT_ROLES\s*=\s*\{([^}]*)\}", text, re.DOTALL)
    if not m:
        return set()
    keys: set[str] = set()
    for km in re.finditer(r'"([^"]+)"\s*:\s*"', m.group(1)):
        keys.add(km.group(1).lower())
    return keys


def _strip_asset(package_path: str) -> str:
    s = (package_path or "").replace("\\", "/").strip()
    if s.startswith("/Game/"):
        s = s[len("/Game/"):]
    if "." in s:
        head, tail = s.rsplit(".", 1)
        if head.rsplit("/", 1)[-1] == tail:
            return head
    return s


def run_one(bucket: str, entry: dict) -> ProbeResult:
    model_rel = entry["path"]
    model_abs = (SRC / model_rel).resolve()
    stem = model_abs.stem
    glb = model_abs.parent / f"{stem}.glb"
    blend = model_abs.parent / f"{stem}.blend"
    for p in (glb, blend):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    task = {
        "source_root": str(SRC.resolve()),
        "save_blend": True,
        "entry": {
            "name": entry["name"],
            "path": entry["path"],
            "Materials": entry["Materials"],
            "MaterialsHybrid": entry["MaterialsHybrid"],
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(task, tf, ensure_ascii=False)
        tf_path = Path(tf.name)

    t0 = time.time()
    try:
        proc = subprocess.run(
            [
                str(BLENDER),
                "--background",
                "--factory-startup",
                "--python", str(WORKER),
                "--",
                "--task-file", str(tf_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        try:
            tf_path.unlink()
        except FileNotFoundError:
            pass

    elapsed = time.time() - t0
    last = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("RESULT:"):
            last = line[len("RESULT:"):].strip()
    if last is None:
        tail = "\n".join((proc.stderr or "").splitlines()[-20:])
        res = {"status": "failed", "error": f"no RESULT (exit {proc.returncode})",
               "stderr_tail": tail}
    else:
        try:
            res = json.loads(last)
        except Exception as e:
            res = {"status": "failed", "error": f"bad RESULT json: {e}"}

    res["_glb_exists"] = glb.is_file()
    res["_glb_bytes"] = glb.stat().st_size if glb.is_file() else 0
    res["_blend_exists"] = blend.is_file()
    return ProbeResult(bucket=bucket, entry=entry, result=res, duration_s=elapsed)


# ---------------------------------------------------------------------------
# Suspicion checks
# ---------------------------------------------------------------------------

def find_issues(pr: ProbeResult) -> list[str]:
    """Return a list of suspicion strings. Empty list means the probe is clean."""
    issues: list[str] = []
    res = pr.result

    if res.get("status") != "success":
        issues.append(f"status={res.get('status')}: {res.get('error')}")
        return issues

    glb_bytes = res.get("_glb_bytes", 0)
    slots = res.get("slots") or []
    if glb_bytes < 1024 and slots:
        issues.append(f"tiny .glb ({glb_bytes} bytes) despite {len(slots)} slots")

    # 1) Wrong MI picked: compare slot.material_path to picked MI stem.
    for i, s in enumerate(slots):
        picked = s.get("mi")
        mp = s.get("material_path") or ""
        if not picked or not mp:
            continue
        stripped = _strip_asset(mp)
        expected_stem = Path(stripped).name.lower()
        picked_stem = Path(picked).stem.lower()
        if expected_stem and picked_stem and expected_stem != picked_stem:
            issues.append(
                f"slot[{i}] '{s.get('slot')}' material_path points to "
                f"'{expected_stem}' but picked '{picked_stem}'"
            )

    # 2) Cross-contaminated hybrid: same texture wired to multiple slots
    #    (only for multi-slot meshes; single-slot meshes can legitimately
    #    use whatever is nearby).
    if len(slots) > 1:
        by_file: dict[str, list[int]] = {}
        for i, s in enumerate(slots):
            files: list[str] = []
            if s.get("source") == "hybrid":
                files.extend(s.get("files") or [])
            hf = s.get("hybrid_fallback")
            if isinstance(hf, dict) and hf.get("source") == "hybrid":
                files.extend(hf.get("files") or [])
            for f in files:
                by_file.setdefault(f, []).append(i)
        for fpath, indices in by_file.items():
            if len(indices) > 1:
                issues.append(
                    f"hybrid texture '{Path(fpath).name}' wired to "
                    f"{len(indices)} slots {indices}"
                )

    # 3) MI picked with zero roles even though the MI JSON has a Texture entry
    #    that (a) maps to a known PBR role the worker understands AND (b) whose
    #    referenced file exists on disk. Both are needed — a sky material with
    #    unknown keys like `T_Sky_Stars`, or an MI referencing missing landscape
    #    textures, is correctly unwirable and not a bug.
    tex_exts = (".png", ".tga", ".dds", ".jpg", ".jpeg", ".exr", ".bmp", ".hdr", ".webp")
    known_role_keys = _load_known_role_keys()
    for i, s in enumerate(slots):
        if s.get("source") != "mi":
            continue
        if s.get("roles"):
            continue
        mp = s.get("material_path") or ""
        stripped_mp = _strip_asset(mp)
        if not stripped_mp:
            continue
        candidate = SRC / f"{stripped_mp}.json"
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        textures = data.get("Textures") or {}
        missed = False
        for key, v in textures.items():
            if key.lower() not in known_role_keys:
                continue
            if not isinstance(v, str) or not v.strip():
                continue
            t_stripped = _strip_asset(v)
            if not t_stripped:
                continue
            base = SRC / t_stripped
            if any(base.with_suffix(ext).is_file() for ext in tex_exts):
                missed = True
                break
        if missed:
            issues.append(
                f"slot[{i}] '{s.get('slot')}' MI has a known-role texture that "
                f"exists on disk but wired 0 roles"
            )

    # 4) GLB missing.
    if not res.get("_glb_exists"):
        issues.append(".glb not written")

    return issues


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"


def print_result(pr: ProbeResult, issues: list[str]) -> None:
    r = pr.result
    bucket = pr.bucket
    path = pr.entry["path"]
    if r.get("status") != "success":
        print(f"  [FAIL] ({bucket}) {path}")
        print(f"         {r.get('error')}")
        return

    src = r.get("materials_source")
    slots = r.get("slot_count")
    size = _fmt_bytes(r.get("_glb_bytes", 0))
    tag = "OK  " if not issues else "WARN"
    print(f"  [{tag}] ({bucket}) {path}")
    print(f"         overall={src}  slots={slots}  glb={size}  t={pr.duration_s:.2f}s")
    for s in r.get("slots") or []:
        ss = s.get("source")
        roles = ",".join(s.get("roles") or []) or "-"
        picked = s.get("mi") or ",".join(Path(x).name for x in (s.get("files") or [])) or "-"
        print(f"           slot={s.get('slot'):<40}  src={ss:<6}  roles={roles:<30}  picked={picked}")
        hf = s.get("hybrid_fallback")
        if hf and hf.get("source") == "hybrid":
            hf_roles = ",".join(hf.get("roles") or []) or "-"
            hf_files = ",".join(Path(x).name for x in (hf.get("files") or [])) or "-"
            print(f"              fallback: roles={hf_roles}  files={hf_files}")
    for iss in issues:
        print(f"         ! {iss}")


def run_round(
    entries: list[dict],
    seed: int,
    per_bucket: int,
    workers: int,
    round_idx: int,
    rounds_total: int,
) -> tuple[int, int]:
    rng = random.Random(seed)
    picks = stratified_sample(entries, per_bucket, rng)
    rng.shuffle(picks)
    print(f"\n============================================================")
    print(f"Round {round_idx}/{rounds_total}  seed={seed}  samples={len(picks)}  workers={workers}")
    print(f"============================================================")

    t0 = time.time()
    results: list[ProbeResult] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one, bucket, entry) for bucket, entry in picks]
        for fut in cf.as_completed(futs):
            results.append(fut.result())

    by_path = {e["path"]: i for i, (_, e) in enumerate(picks)}
    results.sort(key=lambda r: by_path.get(r.entry["path"], 0))

    clean = 0
    warn = 0
    fail = 0
    for pr in results:
        issues = find_issues(pr)
        print_result(pr, issues)
        if pr.result.get("status") != "success":
            fail += 1
        elif issues:
            warn += 1
        else:
            clean += 1

    elapsed = time.time() - t0
    print(f"\nRound {round_idx} summary: {clean} clean / {warn} warn / {fail} fail   "
          f"({elapsed:.1f}s wall)")
    return clean, warn + fail


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--seed", type=int, default=None,
                   help="Seed for round 1; subsequent rounds use seed+i")
    p.add_argument("--per-bucket", type=int, default=1,
                   help="Samples per bucket per round (default 1)")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    if not BLENDER.is_file():
        print(f"blender not found at {BLENDER}", file=sys.stderr)
        return 1
    if not WORKER.is_file():
        print(f"worker not found at {WORKER}", file=sys.stderr)
        return 1

    sm_path = HERE / "SM_Data.json"
    entries = json.loads(sm_path.read_text(encoding="utf-8"))["entries"]

    base_seed = args.seed if args.seed is not None else int(time.time())
    round_totals = []
    for i in range(args.rounds):
        clean, bad = run_round(
            entries, base_seed + i, args.per_bucket, args.workers,
            round_idx=i + 1, rounds_total=args.rounds,
        )
        round_totals.append((clean, bad))

    print("\n============================================================")
    print("Overall:")
    consec_clean = 0
    best_streak = 0
    for i, (c, b) in enumerate(round_totals, 1):
        mark = "clean" if b == 0 else f"{b} issue{'s' if b != 1 else ''}"
        print(f"  Round {i}: {c} clean, {mark}")
        if b == 0:
            consec_clean += 1
            best_streak = max(best_streak, consec_clean)
        else:
            consec_clean = 0
    print(f"  Longest clean streak: {best_streak}/{len(round_totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
