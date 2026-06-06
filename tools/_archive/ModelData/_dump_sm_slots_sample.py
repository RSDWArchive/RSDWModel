"""Sample dump of SM material_path prefixes for audit (first N entries only).

Blender batch:
    blender.exe --background --factory-startup --python tools/ModelData/_dump_sm_slots_sample.py -- --n 200
"""
from __future__ import annotations
import argparse, gzip, json, sys
from pathlib import Path
import addon_utils  # type: ignore

def _enable():
    try: addon_utils.enable("io_scene_ueformat", default_set=False, persistent=True)
    except Exception: pass

def _read(p: Path):
    from io_scene_ueformat.importer.classes import EUEFormatVersion, MAGIC, MODEL_IDENTIFIER, UEModel  # type: ignore
    from io_scene_ueformat.importer.reader import FArchiveReader  # type: ignore
    raw = p.read_bytes()
    with FArchiveReader(raw) as ar:
        m = ar.read_string(len(MAGIC))
        if m != MAGIC: return []
        ident = ar.read_fstring()
        fv = EUEFormatVersion(int.from_bytes(ar.read_byte(), byteorder="big"))
        _ = ar.read_fstring()
        ra = ar
        if ar.read_bool():
            ct = ar.read_fstring(); us = ar.read_int(); _cs = ar.read_int()
            if ct == "GZIP": ra = FArchiveReader(gzip.decompress(ar.read_to_end()))
            elif ct == "ZSTD":
                import io_scene_ueformat as pkg  # type: ignore
                ra = FArchiveReader(pkg.zstd_decompressor.decompress(ar.read_to_end(), us))
            else: return []
        ra.file_version = fv; ra.metadata["scale"] = 1.0
        if ident != MODEL_IDENTIFIER: return []
        model = UEModel.from_archive(ra) if fv >= EUEFormatVersion.LevelOfDetailFormatRestructure else UEModel.from_archive_legacy(ra)
    if not model.lods: return []
    return [m.material_path for m in model.lods[0].materials]

def main():
    sep = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=500)
    args = p.parse_args(sys.argv[sep+1:])
    _enable()
    here = Path(__file__).resolve().parent
    repo = here.parent.parent
    root = repo / "0.11.0.10"
    entries = json.loads((here / "SM_Data.json").read_text(encoding="utf-8"))["entries"]
    # Take a stratified sample: top of list + last N + random middle
    import random
    random.seed(0)
    sample = entries[:args.n // 3] + entries[-args.n // 3:] + random.sample(entries, min(args.n // 3, len(entries)))
    from collections import Counter
    prefix = Counter()
    for e in sample:
        try:
            for mp in _read(root / e["path"]):
                if not mp:
                    prefix["<empty>"] += 1; continue
                head = mp.split('/', 2)
                pre = "/" + head[1] if mp.startswith("/") else head[0]
                prefix[pre] += 1
        except Exception as ex:
            prefix[f"ERR:{type(ex).__name__}"] += 1
    for k, n in prefix.most_common():
        print(f"  {k:30} {n}")

if __name__ == "__main__":
    raise SystemExit(main())
