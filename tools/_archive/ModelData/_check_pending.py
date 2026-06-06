import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "0.11.0.10"

sm = json.load((HERE / "SM_Data.json").open("r", encoding="utf-8"))["entries"]
prog = json.load((HERE / "BuildProgress.json").open("r", encoding="utf-8"))["entries"]
done = set(prog.keys())
pending = [e for e in sm if e["path"] not in done][:15]
for e in pending:
    p = SRC / e["path"]
    size_mb = p.stat().st_size / (1024 * 1024) if p.is_file() else 0
    mi_count = len(e["Materials"]["material_json_paths"])
    hyb_count = len(e["MaterialsHybrid"]["texture_image_paths"])
    print(f"{size_mb:>7.2f} MB  mi={mi_count:>2} hyb={hyb_count:>3}  {e['name']}")
