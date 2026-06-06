"""Run BuildGLBWorker on one entry for diagnostic purposes. Prints the raw
Blender stdout/stderr and the RESULT line if any."""
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


def main() -> int:
    target = sys.argv[1]
    entries = json.loads((HERE / "SM_Data.json").read_text(encoding="utf-8"))["entries"]
    entry = next((e for e in entries if e["path"] == target or e["name"] == target), None)
    if entry is None:
        print(f"not found: {target}", file=sys.stderr)
        return 2

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
        json.dump(task, tf)
        tf_path = Path(tf.name)

    cmd = [
        str(BLENDER),
        "--background",
        "--factory-startup",
        "--python", str(WORKER),
        "--",
        "--task-file", str(tf_path),
    ]
    print("CMD:", " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    elapsed = time.time() - t0
    try:
        tf_path.unlink()
    except FileNotFoundError:
        pass

    print(f"--- stdout ({elapsed:.2f}s, exit={proc.returncode}) ---")
    print(proc.stdout)
    print("--- stderr ---")
    print(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
