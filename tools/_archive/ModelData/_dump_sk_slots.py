"""Blender batch script: walk SK_Data.json, parse every .uemodel with UEFormat's
reader, and dump {path: [{"name":..., "path":...}, ...]} to a sibling JSON.

Run:
    blender.exe --background --factory-startup --python tools/ModelData/_dump_sk_slots.py \
        -- --out tools/ModelData/SK_Slots.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import bpy  # type: ignore
import addon_utils  # type: ignore


def _enable_ueformat() -> None:
    try:
        addon_utils.enable("io_scene_ueformat", default_set=False, persistent=True)
    except Exception:
        pass


def _read_material_paths(uemodel_abs: Path) -> list[dict]:
    from io_scene_ueformat.importer.classes import (  # type: ignore
        EUEFormatVersion, MAGIC, MODEL_IDENTIFIER, UEModel,
    )
    from io_scene_ueformat.importer.reader import FArchiveReader  # type: ignore

    raw = uemodel_abs.read_bytes()
    with FArchiveReader(raw) as ar:
        magic = ar.read_string(len(MAGIC))
        if magic != MAGIC:
            return []
        identifier = ar.read_fstring()
        file_version = EUEFormatVersion(int.from_bytes(ar.read_byte(), byteorder="big"))
        _object_name = ar.read_fstring()
        read_archive = ar
        is_compressed = ar.read_bool()
        if is_compressed:
            compression_type = ar.read_fstring()
            uncompressed_size = ar.read_int()
            _ = ar.read_int()
            if compression_type == "GZIP":
                read_archive = FArchiveReader(gzip.decompress(ar.read_to_end()))
            elif compression_type == "ZSTD":
                import io_scene_ueformat as ueformat_pkg  # type: ignore
                read_archive = FArchiveReader(
                    ueformat_pkg.zstd_decompressor.decompress(
                        ar.read_to_end(), uncompressed_size
                    )
                )
            else:
                return []
        read_archive.file_version = file_version
        read_archive.metadata["scale"] = 1.0
        if identifier != MODEL_IDENTIFIER:
            return []
        if file_version >= EUEFormatVersion.LevelOfDetailFormatRestructure:
            model = UEModel.from_archive(read_archive)
        else:
            model = UEModel.from_archive_legacy(read_archive)
    if not model.lods:
        return []
    lod0 = model.lods[0]
    return [{"name": m.material_name, "path": m.material_path} for m in lod0.materials]


def main() -> int:
    argv = sys.argv
    sep = argv.index("--") if "--" in argv else len(argv)
    cli = argv[sep + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--sk-json", default=None,
                   help="Path to SK_Data.json (default: tools/ModelData/SK_Data.json).")
    p.add_argument("--sm-json", default=None,
                   help="Optional path to SM_Data.json to include too.")
    p.add_argument("--out", required=True)
    p.add_argument("--source-root", default=None,
                   help="Absolute path to data root (default: <repo>/0.11.0.10).")
    args = p.parse_args(cli)

    _enable_ueformat()

    script = Path(__file__).resolve()
    repo = script.parent.parent.parent
    source_root = Path(args.source_root) if args.source_root else (repo / "0.11.0.10")
    sk_json_path = Path(args.sk_json) if args.sk_json else (script.parent / "SK_Data.json")
    if not sk_json_path.is_file():
        print(f"SK_Data.json not found: {sk_json_path}", file=sys.stderr)
        return 2

    entries = json.loads(sk_json_path.read_text(encoding="utf-8"))["entries"]
    if args.sm_json:
        sm_entries = json.loads(Path(args.sm_json).read_text(encoding="utf-8"))["entries"]
        entries = entries + sm_entries

    out: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    total = len(entries)
    for i, e in enumerate(entries, 1):
        rel = e["path"]
        abs_path = source_root / rel
        try:
            mats = _read_material_paths(abs_path)
        except Exception as ex:
            errors[rel] = f"{type(ex).__name__}: {ex}"
            mats = []
        out[rel] = mats
        if i % 50 == 0 or i == total:
            print(f"[{i}/{total}] {rel}  ({len(mats)} slots)")

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({
            "source_root": str(source_root),
            "count": len(out),
            "errors": errors,
            "slots_by_path": out,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out_path}  ({len(out)} models, {len(errors)} errors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
