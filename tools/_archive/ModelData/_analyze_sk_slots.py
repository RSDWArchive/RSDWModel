"""Classify every slot in SK_Slots.json by how easily its material_path resolves.

Categories:
  direct_on_disk      : /Game/<path> -> <source_root>/<path>.json exists
  content_by_name     : failed direct, but <last-segment>.json exists somewhere under Content/
  skeleton_shared     : resolves under Art/Skeleton/ (any of Shared/, Player/*/Materials, etc.)
  no_material_path    : the slot has an empty material_path (nothing to resolve)
  unresolved          : resolved neither directly nor by name match

This is a read-only audit — no files are modified.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SLOTS = HERE / "SK_Slots.json"
SM_SLOTS = HERE / "SM_Slots.json"


def _index_material_jsons(root: Path) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in root.rglob("*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                head = f.read(4096)
            if '"Textures"' not in head:
                continue
        except Exception:
            continue
        idx.setdefault(p.stem.lower(), p)
    return idx


def _strip_asset_tail(pkg: str) -> str:
    """Unreal `/Game/Foo/Bar.Bar` -> `/Game/Foo/Bar`."""
    if "." in pkg.split("/")[-1]:
        return pkg.rsplit(".", 1)[0]
    return pkg


def _direct_on_disk(pkg: str, source_root: Path) -> Path | None:
    if not pkg.startswith("/Game/"):
        return None
    rel = _strip_asset_tail(pkg)[len("/Game/"):]
    cand = source_root / "RSDragonwilds" / "Content" / Path(rel + ".json")
    if cand.is_file():
        return cand
    # Also try plugin-relative roots (GameFeatures plugins have /Game/... packaged)
    # Scan plugin content folders for a <rel>.json match.
    plug = source_root / "RSDragonwilds" / "Plugins" / "GameFeatures"
    if plug.is_dir():
        for p in plug.iterdir():
            inner = p / "Content" / Path(rel + ".json")
            if inner.is_file():
                return inner
    return None


def classify(pkg: str, source_root: Path, skel_idx: dict[str, Path], content_idx: dict[str, Path]) -> tuple[str, Path | None]:
    if not pkg:
        return ("no_material_path", None)
    direct = _direct_on_disk(pkg, source_root)
    if direct is not None:
        rel = direct.relative_to(source_root).as_posix()
        if "Art/Skeleton" in rel:
            return ("skeleton_shared", direct)
        return ("direct_on_disk", direct)
    # name-match fallback
    name = pkg.rsplit("/", 1)[-1].split(".")[0].lower()
    if name in skel_idx:
        return ("skeleton_shared", skel_idx[name])
    if name in content_idx:
        return ("content_by_name", content_idx[name])
    return ("unresolved", None)


def main() -> int:
    data = json.loads(SLOTS.read_text(encoding="utf-8"))
    source_root = Path(data["source_root"])
    content = source_root / "RSDragonwilds" / "Content"
    skeleton = content / "Art" / "Skeleton"

    print("indexing Art/Skeleton material JSONs...")
    skel_idx = _index_material_jsons(skeleton)
    print(f"  {len(skel_idx)} material JSONs")
    print("indexing full Content material JSONs...")
    content_idx = _index_material_jsons(content)
    # Also include plugin content
    plug = source_root / "RSDragonwilds" / "Plugins" / "GameFeatures"
    if plug.is_dir():
        for sub in plug.iterdir():
            inner = sub / "Content"
            if inner.is_dir():
                for k, v in _index_material_jsons(inner).items():
                    content_idx.setdefault(k, v)
    print(f"  {len(content_idx)} material JSONs")

    overall = Counter()
    per_subtree: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for rel, slots in data["slots_by_path"].items():
        # bucket by subtree
        parts = rel.split("/")
        if "Skeleton" in parts:
            bucket = f"Skeleton/{parts[parts.index('Skeleton')+1]}" if parts.index('Skeleton')+1 < len(parts) else "Skeleton/?"
        else:
            bucket = parts[3] if len(parts) > 3 else "?"

        for s in slots:
            pkg = s.get("path") or ""
            kind, _ = classify(pkg, source_root, skel_idx, content_idx)
            overall[kind] += 1
            per_subtree[bucket][kind] += 1
            if len(examples[kind]) < 10:
                examples[kind].append((rel, pkg))

    total = sum(overall.values())
    print(f"\n=== SK_ slot classification (n={total}) ===")
    for k, n in overall.most_common():
        pct = 100.0 * n / total if total else 0.0
        print(f"  {k:18} {n:5}  ({pct:5.1f}%)")

    print("\n=== per subtree ===")
    for bucket, c in sorted(per_subtree.items()):
        t = sum(c.values())
        print(f"\n[{bucket}] total={t}")
        for k, n in c.most_common():
            pct = 100.0 * n / t if t else 0.0
            print(f"   {k:18} {n:5}  ({pct:5.1f}%)")

    print("\n=== examples per category ===")
    for k in ("unresolved", "content_by_name", "skeleton_shared", "no_material_path"):
        if not examples[k]:
            continue
        print(f"\n-- {k} --")
        for rel, pkg in examples[k]:
            print(f"   {rel}")
            print(f"     -> {pkg!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
