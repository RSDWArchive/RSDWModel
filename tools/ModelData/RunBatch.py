"""One-click batch launcher.

Runs `BuildGLB.py --workers 4 --force` against the full SM_ (or SK_) set using
the current Python interpreter, with stdout unbuffered so progress appears live
in the terminal. Resumable: if you interrupt it (Ctrl-C or close the window)
and want to continue later, re-run with --resume:

    python tools/ModelData/RunBatch.py --resume         # SM_ set (default)
    python tools/ModelData/RunBatch.py --sk --resume    # SK_ set

Double-click friendly: if this script is run from Explorer/a file manager
(not a terminal), it keeps the window open at the end so you can read the
summary before it closes.

Flags forwarded to BuildGLB.py (all optional):
    --resume         Drop --force so already-built entries are skipped.
    --sk             Use SK_Data.json instead of SM_Data.json.
    --workers N      Override the default (4).
    --limit N        Process only the first N pending entries.
    --only SUBSTR    Filter entries by substring (name or path).
    --source-root P  Override the default newest version folder.
    --data-file P    Override the default inventory path.
    --progress-file P Override the default progress path.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "BuildGLB.py"


def _is_probably_double_clicked() -> bool:
    """Best-effort: detect Explorer-launched runs so we can pause at end."""
    if os.environ.get("PROMPT"):
        return False
    if sys.stdin is None or not sys.stdin.isatty():
        return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--resume", action="store_true",
                   help="Skip entries already marked success (drops --force).")
    p.add_argument("--sk", action="store_true",
                   help="Build the SK_ set (SK_Data.json) instead of the SM_ set.")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", type=str, default=None)
    p.add_argument("--source-root", type=Path, default=None)
    p.add_argument("--data-file", type=Path, default=None)
    p.add_argument("--progress-file", type=Path, default=None)
    args = p.parse_args()

    if not DRIVER.is_file():
        print(f"driver script not found: {DRIVER}", file=sys.stderr)
        return 2

    cmd: list[str] = [sys.executable, "-u", str(DRIVER), "--workers", str(args.workers)]
    if args.sk:
        cmd.append("--sk")
    if args.source_root is not None:
        cmd += ["--source-root", str(args.source_root)]
    if args.data_file is not None:
        cmd += ["--data-file", str(args.data_file)]
    if args.progress_file is not None:
        cmd += ["--progress-file", str(args.progress_file)]
    if not args.resume:
        cmd.append("--force")
    if args.limit is not None:
        cmd += ["--limit", str(args.limit)]
    if args.only:
        cmd += ["--only", args.only]

    print(">>>", " ".join(cmd), flush=True)
    pause_at_end = _is_probably_double_clicked()
    try:
        rc = subprocess.call(cmd)
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run with --resume to continue from the progress file.")
        rc = 130

    if pause_at_end:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
