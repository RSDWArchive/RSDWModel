"""
Driver: batch-build .glb (and optionally .blend) for SM_*.uemodel or SK_*.uemodel
entries from the current version inventory. Spawns one headless Blender per entry
through a bounded worker pool, collects each worker's RESULT: line, and writes a
version-local progress manifest so the job is resumable.

Example:

    python tools/ModelData/BuildGLB.py                      # full run, auto workers
    python tools/ModelData/BuildGLB.py --limit 1 --only Ranged_Fire_Oil
    python tools/ModelData/BuildGLB.py --workers 8 --force
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


PROGRESS_SCHEMA = "RSDWModel.BuildProgress.v1"


def _repo_root() -> Path:
    # tools/ModelData/BuildGLB.py -> repo root
    return Path(__file__).resolve().parent.parent.parent


def _default_blender() -> Path:
    return _repo_root() / "blender-5.0.0-windows-x64" / "blender.exe"


def _default_source_root() -> Path:
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for child in _repo_root().iterdir():
        if not child.is_dir():
            continue
        parts = child.name.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            candidates.append((tuple(int(part) for part in parts), child))
    if not candidates:
        return _repo_root() / "0.12.0.0"
    return max(candidates, key=lambda item: item[0])[1]


def _inventory_path(source_root: Path, kind: str) -> Path:
    return source_root / "ModelData" / f"{kind}_Data.json"


def _progress_path(source_root: Path, kind: str) -> Path:
    return source_root / f"BuildProgress_{kind}.json"


def _default_worker() -> Path:
    return Path(__file__).resolve().parent / "BuildGLBWorker.py"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Progress manifest
# ---------------------------------------------------------------------------

class ProgressManifest:
    """JSON manifest keyed by entry.path. Only this process writes it."""

    def __init__(self, path: Path, source_root: Path) -> None:
        self.path = path
        self.source_root = source_root
        self.lock = threading.Lock()
        self.data: dict = {
            "manifest_schema": PROGRESS_SCHEMA,
            "started_utc": _now_utc(),
            "updated_utc": _now_utc(),
            "source_root": _rel_to_repo(source_root),
            "totals": {"success": 0, "failed": 0, "skipped": 0, "pending": 0},
            "entries": {},
        }
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("manifest_schema") == PROGRESS_SCHEMA:
                    self.data = loaded
                    self.data["started_utc"] = self.data.get("started_utc") or _now_utc()
            except Exception:
                pass
        self._recount()

    def _recount(self) -> None:
        totals = {"success": 0, "failed": 0, "skipped": 0, "pending": 0}
        for e in self.data.get("entries", {}).values():
            status = e.get("status", "pending")
            if status in totals:
                totals[status] += 1
            else:
                totals["pending"] += 1
        self.data["totals"] = totals

    def get(self, key: str) -> dict | None:
        return self.data["entries"].get(key)

    def update(self, key: str, record: dict) -> None:
        with self.lock:
            self.data["entries"][key] = record
            self.data["updated_utc"] = _now_utc()
            self._recount()
            self._write_atomic()

    def totals(self) -> dict:
        return dict(self.data["totals"])

    def _write_atomic(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # On Windows, an editor (VS Code / Cursor) with the progress file open
        # can briefly deny the atomic replace with WinError 5. Retry with small
        # backoff before giving up.
        last_err: Exception | None = None
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.1 * (attempt + 1))
        # Best-effort: if the replace still fails, clean up the tmp file and
        # surface a warning instead of crashing the whole batch. We'll retry
        # the write on the next update.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        sys.stderr.write(
            f"[progress] warning: could not update {self.path.name} "
            f"(locked by another process): {last_err}\n"
        )
        sys.stderr.flush()


def _rel_to_repo(p: Path) -> str:
    try:
        return p.resolve().relative_to(_repo_root().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


# ---------------------------------------------------------------------------
# Worker invocation
# ---------------------------------------------------------------------------

def _parse_result_line(stdout: str) -> dict | None:
    """Scan stdout for the last line starting with 'RESULT:' and parse its JSON."""
    last = None
    for line in stdout.splitlines():
        if line.startswith("RESULT:"):
            last = line[len("RESULT:"):].strip()
    if last is None:
        return None
    try:
        return json.loads(last)
    except Exception:
        return None


def _run_one(
    *,
    blender_exe: Path,
    worker_script: Path,
    source_root: Path,
    entry: dict,
    save_blend: bool,
    timeout_s: int,
) -> dict:
    task = {
        "source_root": str(source_root.resolve()),
        "save_blend": save_blend,
        "entry": entry,
    }
    t0 = time.time()
    tf = None
    try:
        fd, task_path = tempfile.mkstemp(prefix="glb_task_", suffix=".json")
        os.close(fd)
        tf = Path(task_path)
        tf.write_text(json.dumps(task), encoding="utf-8")

        cmd = [
            str(blender_exe),
            "--background",
            "--factory-startup",
            "--python",
            str(worker_script),
            "--",
            "--task-file",
            str(tf),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed = round(time.time() - t0, 3)
        result = _parse_result_line(proc.stdout or "")
        if result is None:
            # Worker died before emitting RESULT. Preserve a short tail of stderr for debugging.
            stderr_tail = "\n".join((proc.stderr or "").splitlines()[-20:])
            return {
                "status": "failed",
                "error": f"no RESULT line (exit {proc.returncode})",
                "stderr_tail": stderr_tail,
                "duration_s": elapsed,
            }
        # Trust the worker's result; if it claims success but exit code is non-zero, downgrade.
        if proc.returncode != 0 and result.get("status") == "success":
            result = dict(result)
            result["status"] = "failed"
            result["error"] = f"{result.get('error', '')} (exit {proc.returncode})".strip()
        return result
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "error": f"timeout after {timeout_s}s",
            "duration_s": round(time.time() - t0, 3),
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - t0, 3),
        }
    finally:
        if tf is not None:
            try:
                tf.unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _select_entries(
    all_entries: list[dict],
    *,
    only_substr: str | None,
    only_paths: set[str] | None,
    limit: int | None,
) -> list[dict]:
    filtered = all_entries
    if only_substr:
        low = only_substr.lower()
        filtered = [e for e in filtered if low in e.get("name", "").lower() or low in e.get("path", "").lower()]
    if only_paths is not None:
        # Exact-path match (case-sensitive; `path` values in the data files are
        # the authoritative repo-relative paths used everywhere else).
        filtered = [e for e in filtered if e.get("path", "") in only_paths]
    if limit is not None and limit >= 0:
        filtered = filtered[:limit]
    return filtered


def _should_skip(entry: dict, progress: ProgressManifest, source_root: Path, force: bool) -> dict | None:
    if force:
        return None
    rec = progress.get(entry["path"])
    if not rec or rec.get("status") != "success":
        return None
    glb_rel = rec.get("glb_path")
    if glb_rel:
        glb_abs = source_root / glb_rel
        if glb_abs.is_file():
            return rec
    return None


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch compile .glb for SM_/SK_ uemodel entries.")
    # --data-file is the new canonical flag (accepts SM_Data.json, SK_Data.json, or any
    # other v3-schema inventory). --sm-data is kept as a back-compat alias.
    p.add_argument("--data-file", type=Path, default=None,
                   help="Path to a v3 data inventory (e.g. SM_Data.json or SK_Data.json). "
                        "If omitted, defaults to SM_Data.json unless --sk is set.")
    p.add_argument("--sm-data", type=Path, default=None,
                   help="Alias for --data-file (pointing at SM_Data.json).")
    p.add_argument("--sk", action="store_true",
                   help="Shortcut: use the default SK_Data.json instead of SM_Data.json.")
    p.add_argument("--source-root", type=Path, default=_default_source_root())
    p.add_argument("--blender", type=Path, default=_default_blender())
    p.add_argument("--worker", type=Path, default=_default_worker())
    p.add_argument("--progress-file", type=Path, default=None)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    p.add_argument("--timeout-s", type=int, default=300)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", type=str, default=None, help="Substring filter on name or path")
    p.add_argument("--only-list", type=Path, default=None,
                   help="Path to a text file containing one entry path per line. "
                        "Only entries whose `path` matches a line are processed. "
                        "Blank lines and lines starting with # are ignored. "
                        "Combines with --only (both filters applied).")
    p.add_argument("--force", action="store_true", help="Rebuild even if marked success")
    p.add_argument("--no-blend", action="store_true", help="Deprecated; .blend output has been removed")
    p.add_argument("--dry-run", action="store_true", help="List what would run and exit")
    args = p.parse_args(argv)

    # Resolve the data-file argument (precedence: --data-file > --sm-data > --sk > SM default).
    data_file: Path
    if args.data_file is not None:
        data_file = args.data_file
        inventory_kind = "SK" if data_file.name.upper().startswith("SK_") else "SM"
    elif args.sm_data is not None:
        data_file = args.sm_data
        inventory_kind = "SK" if data_file.name.upper().startswith("SK_") else "SM"
    elif args.sk:
        inventory_kind = "SK"
        data_file = _inventory_path(args.source_root, inventory_kind)
    else:
        inventory_kind = "SM"
        data_file = _inventory_path(args.source_root, inventory_kind)
    args.sm_data = data_file  # keep downstream code paths backwards-compatible
    if args.progress_file is None:
        args.progress_file = _progress_path(args.source_root, inventory_kind)

    if not args.blender.is_file():
        _log(f"blender.exe not found at: {args.blender}")
        return 2
    if not args.worker.is_file():
        _log(f"worker script not found at: {args.worker}")
        return 2
    if not args.source_root.is_dir():
        _log(f"source root not found: {args.source_root}")
        return 2
    if not data_file.is_file():
        _log(f"data inventory not found: {data_file}")
        return 2

    manifest = json.loads(data_file.read_text(encoding="utf-8"))
    entries: list[dict] = list(manifest.get("entries", []))
    if not entries:
        _log(f"{data_file.name} has no entries; nothing to do.")
        return 0
    _log(f"Using data inventory: {data_file.name} ({len(entries)} entries)")

    only_paths: set[str] | None = None
    if args.only_list is not None:
        if not args.only_list.is_file():
            _log(f"--only-list file not found: {args.only_list}")
            return 2
        only_paths = set()
        for raw in args.only_list.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            only_paths.add(line)
        _log(f"--only-list loaded {len(only_paths)} path(s) from {args.only_list.name}")

    selected = _select_entries(
        entries, only_substr=args.only, only_paths=only_paths, limit=args.limit,
    )
    progress = ProgressManifest(args.progress_file, args.source_root)

    to_run: list[dict] = []
    pre_skipped = 0
    for e in selected:
        skip_rec = _should_skip(e, progress, args.source_root, args.force)
        if skip_rec is not None:
            pre_skipped += 1
            continue
        to_run.append(e)

    _log(
        f"Selected {len(selected)} (skipped already-built: {pre_skipped}), "
        f"running {len(to_run)} with {args.workers} workers"
    )
    if args.dry_run:
        for e in to_run:
            _log(f"  RUN  {e['path']}")
        return 0
    if not to_run:
        _log("Nothing to do.")
        return 0

    save_blend = False
    total = len(to_run)
    done = 0
    ok = 0
    fail = 0
    start = time.time()

    # Track what's currently in flight so the heartbeat can show the user the
    # driver is alive during the silent stretches where every worker is busy
    # on a big model (some .uemodel files take 10–30s to import + export).
    in_flight: dict[str, float] = {}
    in_flight_lock = threading.Lock()
    stop_heartbeat = threading.Event()

    def _task(entry: dict) -> tuple[dict, dict]:
        key = entry["path"]
        with in_flight_lock:
            in_flight[key] = time.time()
        try:
            res = _run_one(
                blender_exe=args.blender,
                worker_script=args.worker,
                source_root=args.source_root,
                entry=entry,
                save_blend=save_blend,
                timeout_s=args.timeout_s,
            )
        finally:
            with in_flight_lock:
                in_flight.pop(key, None)
        return entry, res

    def _heartbeat() -> None:
        # Fires every ~10s; shows done/total, elapsed, and the slowest live task
        # so a stretch of "silence" in the log never looks like a deadlock.
        interval = 10.0
        while not stop_heartbeat.wait(interval):
            with in_flight_lock:
                snapshot = list(in_flight.items())
            now = time.time()
            active = len(snapshot)
            elapsed = now - start
            rate = done / elapsed if elapsed > 0 else 0.0
            eta_s = (total - done) / rate if rate > 0 else 0
            oldest_name = "-"
            oldest_age = 0.0
            if snapshot:
                oldest_key, oldest_t0 = max(snapshot, key=lambda kv: now - kv[1])
                oldest_age = now - oldest_t0
                oldest_name = Path(oldest_key).name
            _log(
                f"[heartbeat] {done}/{total}  active={active}  "
                f"elapsed={elapsed:.0f}s  eta={eta_s:.0f}s  "
                f"oldest-inflight={oldest_name} ({oldest_age:.0f}s)"
            )

    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat_thread.start()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_task, e) for e in to_run]
        for fut in as_completed(futures):
            entry, res = fut.result()
            done += 1
            key = entry["path"]
            status = res.get("status", "failed")
            record = {
                "name": entry.get("name"),
                "path": key,
                "status": status,
                "materials_source": res.get("materials_source"),
                "glb_path": res.get("glb_path"),
                "blend_path": res.get("blend_path"),
                "slot_count": res.get("slot_count"),
                "duration_s": res.get("duration_s"),
                "finished_utc": _now_utc(),
                "error": res.get("error"),
            }
            progress.update(key, record)

            if status == "success":
                ok += 1
                tag = res.get("materials_source") or "?"
                _log(f"[{done}/{total}] OK   {entry['name']}  ({tag}, {record['duration_s']}s)")
            else:
                fail += 1
                err = res.get("error") or "unknown error"
                _log(f"[{done}/{total}] FAIL {entry['name']}  -> {err}")

    stop_heartbeat.set()
    heartbeat_thread.join(timeout=2.0)

    elapsed = time.time() - start
    totals = progress.totals()
    _log(
        f"Done: {ok} ok, {fail} failed, {pre_skipped} pre-skipped in {elapsed:.1f}s. "
        f"Manifest totals: {totals}"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
