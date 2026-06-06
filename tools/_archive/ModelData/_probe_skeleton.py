"""Ad-hoc probe: investigate how SK_ assets under Art/Skeleton/ reference materials.

The Skeleton/ tree is unusual compared to the SM_ tree: the material JSONs live
in a few shared/central folders (Skeleton/Shared/Materials, Skeleton/Player/*/Materials,
Skeleton/Armour/*/*/Materials, Skeleton/NPC/*/*/Materials) and many .uemodel files
in sibling folders reference them by absolute Unreal package path.

This script:
  1. Loads SK_Data.json
  2. For each SK_ entry under Art/Skeleton/, reads the .uemodel header and extracts
     the material_path values via the UEFormat FArchiveReader (if available), falling
     back to a byte-scan for /Game/... strings in the header.
  3. Tallies how many slots resolve to an on-disk material JSON:
       - same-dir                  (next to the mesh)
       - near-dir (walk up to 2)   (typical SM_ pattern)
       - anywhere under Art/Skeleton (shared/centralised)
       - anywhere under RSDragonwilds/Content (absolute UE path match)
       - unresolved
  4. Prints a summary and lists 10 sample unresolved slots so we can see what we miss.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC  = REPO / "0.11.0.10"
CONTENT = SRC / "RSDragonwilds" / "Content"
SK_JSON = HERE / "SK_Data.json"

# Try to use UEFormat's parser for authoritative material_path values.
UEFORMAT_ROOT = REPO / "UEFormat-main" / "Blender"
if (UEFORMAT_ROOT / "io_scene_ueformat").is_dir():
    sys.path.insert(0, str(UEFORMAT_ROOT))

try:
    from io_scene_ueformat.importer.reader import FArchiveReader  # type: ignore
    from io_scene_ueformat.importer.objects import UEModel  # type: ignore
    HAVE_UEFORMAT = True
except Exception as e:
    HAVE_UEFORMAT = False
    _uef_err = e


PACKAGE_RE = re.compile(rb"/Game/[A-Za-z0-9_/\.\-]+")


def _resolve_package_path(pkg: str) -> Path | None:
    """Map an Unreal /Game/Foo/Bar.Bar-style reference to a .json on disk."""
    if not pkg.startswith("/Game/"):
        return None
    rel = pkg[len("/Game/"):]
    rel = rel.split(".")[0]        # strip '.AssetName' tail if present
    candidate = CONTENT / Path(rel + ".json")
    if candidate.is_file():
        return candidate
    return None


def _try_ueformat_material_paths(path: Path) -> list[str]:
    if not HAVE_UEFORMAT:
        return []
    try:
        with path.open("rb") as f:
            data = f.read()
        reader = FArchiveReader(data)
        model = UEModel.from_archive(reader)
        out = []
        for mat in getattr(model, "materials", []) or []:
            mp = getattr(mat, "material_path", None) or getattr(mat, "name", None)
            if mp:
                out.append(str(mp))
        return out
    except Exception:
        return []


def _byte_scan_package_refs(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except Exception:
        return []
    hits = [m.group(0).decode("utf-8", errors="replace") for m in PACKAGE_RE.finditer(raw)]
    # Keep only strings that look like material instances (MI_/MT_/M_ in last segment)
    out = []
    for h in hits:
        last = h.rsplit("/", 1)[-1]
        if last.startswith(("MI_", "MT_", "M_")):
            out.append(h)
    # de-dupe preserving order
    seen = set()
    uniq = []
    for h in out:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


def _index_material_jsons(root: Path) -> dict[str, Path]:
    """Index every *material* JSON (content-sniff for top-level "Textures" key) under root."""
    out: dict[str, Path] = {}
    for p in root.rglob("*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                head = f.read(4096)
            if '"Textures"' not in head:
                continue
        except Exception:
            continue
        out[p.stem.lower()] = p
    return out


def classify_slot(
    slot_ref: str,
    mesh_path: Path,
    skel_index: dict[str, Path],
    content_index: dict[str, Path],
) -> str:
    """Return one of: same-dir, near-dir, skeleton-shared, content-abs, unresolved."""
    # 1. Absolute UE package reference → direct JSON on disk?
    direct = _resolve_package_path(slot_ref)
    if direct is not None:
        # Classify by location
        try:
            rel = direct.relative_to(SRC).as_posix()
        except ValueError:
            rel = str(direct)
        md = mesh_path.parent
        if direct.parent == md:
            return "same-dir"
        # walk up two directories from mesh looking for the json
        cur = md
        for _ in range(2):
            if direct.parent == cur or direct.parent == cur / "Materials":
                return "near-dir"
            cur = cur.parent
        if "Art/Skeleton" in rel:
            return "skeleton-shared"
        return "content-abs"

    # 2. No absolute hit; try last-segment name-match against skeleton index.
    name = slot_ref.rsplit("/", 1)[-1].split(".")[0].lower()
    if name in skel_index:
        return "skeleton-shared"
    if name in content_index:
        return "content-abs"
    return "unresolved"


def main() -> int:
    if not SK_JSON.is_file():
        print("SK_Data.json missing; run CompileModelData.py first.")
        return 2
    data = json.loads(SK_JSON.read_text(encoding="utf-8"))
    entries = data["entries"]
    skeleton = [e for e in entries if "Art/Skeleton" in e["path"]]
    print(f"SK_ total: {len(entries)}, under Art/Skeleton: {len(skeleton)}")
    print(f"UEFormat parser available: {HAVE_UEFORMAT}")
    if not HAVE_UEFORMAT:
        print(f"  (import error: {_uef_err})")

    print("\nIndexing material JSONs under Art/Skeleton/ ...")
    skel_index = _index_material_jsons(CONTENT / "Art" / "Skeleton")
    print(f"  found {len(skel_index)} material JSONs")
    print("Indexing material JSONs under entire Content/ ...")
    content_index = _index_material_jsons(CONTENT)
    print(f"  found {len(content_index)} material JSONs")

    tally = Counter()
    slot_total = 0
    slotless = 0
    unresolved_examples: list[tuple[str, str]] = []

    # Per-subtree aggregation
    subtree_tally: dict[str, Counter] = {}

    for e in skeleton:
        mesh = SRC / e["path"]
        # First: authoritative refs from ueformat
        refs = _try_ueformat_material_paths(mesh)
        if not refs:
            refs = _byte_scan_package_refs(mesh)
        if not refs:
            slotless += 1
            continue
        # Determine subtree bucket (Player, Armour, NPC, Shared, Weapons, Prop, other)
        parts = e["path"].split("/")
        try:
            bucket = parts[parts.index("Skeleton") + 1]
        except Exception:
            bucket = "?"
        sub = subtree_tally.setdefault(bucket, Counter())

        for ref in refs:
            slot_total += 1
            kind = classify_slot(ref, mesh, skel_index, content_index)
            tally[kind] += 1
            sub[kind] += 1
            if kind == "unresolved" and len(unresolved_examples) < 15:
                unresolved_examples.append((e["path"], ref))

    print("\n=== classification across all SK_ slots under Art/Skeleton ===")
    print(f"meshes with zero refs parsed: {slotless}")
    print(f"total slots: {slot_total}")
    for k, n in tally.most_common():
        pct = 100.0 * n / slot_total if slot_total else 0.0
        print(f"  {k:18} {n:5}  ({pct:5.1f}%)")

    print("\n=== per-subtree breakdown ===")
    for bucket, c in sorted(subtree_tally.items()):
        total = sum(c.values())
        print(f"[{bucket}] total={total}")
        for k, n in c.most_common():
            pct = 100.0 * n / total if total else 0.0
            print(f"    {k:18} {n:5}  ({pct:5.1f}%)")

    print("\n=== sample unresolved slots ===")
    for mesh_rel, ref in unresolved_examples:
        print(f"  {mesh_rel}")
        print(f"    -> {ref}")

    # Compare: how many of those slots does the CURRENT SK_Data.json report as material_json_paths?
    # i.e., is our existing pipeline already handling these?
    print("\n=== how SK_Data.json currently reports these ===")
    # Re-walk to tally manifest Materials vs authoritative slot count
    empty_in_manifest = 0
    partial = 0
    full = 0
    for e in skeleton:
        mesh = SRC / e["path"]
        refs = _try_ueformat_material_paths(mesh) or _byte_scan_package_refs(mesh)
        n_slots = len(refs)
        n_mats = len(e["Materials"]["material_json_paths"])
        if n_slots == 0:
            continue
        if n_mats == 0:
            empty_in_manifest += 1
        elif n_mats < n_slots:
            partial += 1
        else:
            full += 1
    print(f"  empty_in_manifest: {empty_in_manifest}")
    print(f"  partial:           {partial}")
    print(f"  full:              {full}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
