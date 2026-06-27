"""
Run the RSDWModel update pipeline for a new RuneScape: Dragonwilds game build.

Default behavior:
- Detect the installed game version from the UE4SS header dump.
- Use E:\\Github\\Retoc\\RSDragonwilds\\<version> as the retoc cache.
- Locate the current .usmap from the game's UE4SS folder and copy it into that
  cache.
- Run retoc if the cache does not already look populated.
- Run the CUE4Parse extractor, which resumes existing .uemodel exports.
- Compile SM_Data.json and SK_Data.json into the version folder.
- Build one SM and one SK shared web asset as smoke tests.
- Generate the website model index and Avatar assembly index.
- For full web-asset runs, generate a safe Git commit batch plan. Optional
  flags can create and push those batches.

Use --web-assets full when you intentionally want to build all website assets.
Use --glb full only for legacy standalone GLB generation.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


GAME_NAME = "RSDragonwilds"
DEFAULT_RETOC_BASE = Path(r"E:\Github\Retoc\RSDragonwilds")
DEFAULT_CUE4PARSE_ROOT = Path(r"E:\Github\CUE4Parse")
DEFAULT_ARCHIVE_BASE = Path(r"E:\Github\RSDWArchive")
PROJECT_VERSION_RE = re.compile(r'ProjectVersion\s*=\s*TEXT\("([^"]+)"\)')
RETOC_MANIFEST_NAME = "retoc-manifest.json"
RETOC_LOCK_NAME = ".retoc.lock"
DEFAULT_GIT_BATCH_GB = 1.9
DEFAULT_GIT_FILE_LIMIT_MB = 100.0
DEFAULT_GIT_PLAN_OUTPUT = Path("PipelineLogs") / "GitCommitPlan.json"
DEFAULT_BLENDER = Path("blender-5.0.0-windows-x64") / "blender.exe"
CUE_WARNING_POLICY = Path("tools") / "PipelinePolicies" / "CueWarningPolicy.json"


@dataclass(frozen=True)
class RetocCacheStatus:
    state: str
    detail: str
    manifest: dict | None = None


@dataclass(frozen=True)
class ResourceSettings:
    profile: str
    cpu_count: int
    web_asset_workers: int
    glb_workers: int
    web_animation_shards: int


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_section(title: str) -> None:
    print(f"\n== {title} ==")


def parse_positive_int_or_max(value: str) -> int | str:
    text = str(value).strip().lower()
    if text == "max":
        return "max"
    try:
        parsed = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer or 'max'") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def default_blender(root: Path) -> Path:
    return root / DEFAULT_BLENDER


def resolve_count(value: int | str | None, *, default: int, max_value: int) -> int:
    if value == "max":
        return max(1, max_value)
    if isinstance(value, int):
        return max(1, value)
    return max(1, default)


def resolve_resource_settings(args: argparse.Namespace) -> ResourceSettings:
    cpu_count = os.cpu_count() or 4
    profile_defaults = {
        "conservative": {
            "web_asset_workers": max(1, min(2, cpu_count)),
            "glb_workers": max(1, min(2, cpu_count)),
            "web_animation_shards": 1,
        },
        "balanced": {
            "web_asset_workers": max(1, cpu_count // 2),
            "glb_workers": max(1, cpu_count // 2),
            "web_animation_shards": max(1, min(4, cpu_count // 2)),
        },
        "max": {
            "web_asset_workers": max(1, cpu_count),
            "glb_workers": max(1, cpu_count),
            # Eight Blender animation shards is the current proven high-throughput
            # ceiling for this project. Explicit numeric input can override it.
            "web_animation_shards": max(1, min(8, cpu_count)),
        },
    }
    defaults = profile_defaults[args.resource_profile]
    return ResourceSettings(
        profile=args.resource_profile,
        cpu_count=cpu_count,
        web_asset_workers=resolve_count(
            args.web_asset_workers,
            default=defaults["web_asset_workers"],
            max_value=cpu_count,
        ),
        glb_workers=resolve_count(
            args.glb_workers,
            default=defaults["glb_workers"],
            max_value=cpu_count,
        ),
        web_animation_shards=resolve_count(
            args.web_animation_shards,
            default=defaults["web_animation_shards"],
            max_value=profile_defaults["max"]["web_animation_shards"],
        ),
    )


def candidate_game_roots() -> list[Path]:
    out: list[Path] = []
    env_root = os.environ.get("RSDW_GAME_ROOT")
    if env_root:
        out.append(Path(env_root))
    out.extend(
        [
            Path(r"F:\SteamLibrary\steamapps\common\RSDragonwilds\RSDragonwilds"),
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\RSDragonwilds\RSDragonwilds"),
            Path(r"C:\Program Files\Steam\steamapps\common\RSDragonwilds\RSDragonwilds"),
        ]
    )
    return out


def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def detect_game_root(explicit: Path | None) -> Path:
    if explicit:
        root = explicit.resolve()
        if not root.is_dir():
            raise SystemExit(f"Game root does not exist: {root}")
        return root

    root = first_existing(candidate_game_roots())
    if root is None:
        candidates = "\n  ".join(str(p) for p in candidate_game_roots())
        raise SystemExit(
            "Could not detect the game root. Pass --game-root or set RSDW_GAME_ROOT.\n"
            f"Tried:\n  {candidates}"
        )
    return root.resolve()


def ue4ss_root(game_root: Path) -> Path:
    return game_root / "Binaries" / "Win64" / "ue4ss"


def detect_game_version(game_root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit

    version_file = (
        ue4ss_root(game_root)
        / "UHTHeaderDump"
        / "EngineSettings"
        / "Private"
        / "GeneralProjectSettings.cpp"
    )
    if not version_file.is_file():
        raise SystemExit(
            "Could not detect game version because the UE4SS header dump was not found.\n"
            f"Expected: {version_file}\n"
            "Pass --version explicitly after generating/updating the dump."
        )

    text = version_file.read_text(encoding="utf-8", errors="replace")
    match = PROJECT_VERSION_RE.search(text)
    if not match:
        raise SystemExit(
            "Could not parse ProjectVersion from UE4SS header dump.\n"
            f"File: {version_file}\n"
            "Pass --version explicitly."
        )
    return match.group(1)


def find_usmap(args: argparse.Namespace, game_root: Path, retoc_version_root: Path) -> Path:
    if args.usmap:
        path = args.usmap.resolve()
        if not path.is_file():
            raise SystemExit(f"--usmap does not exist: {path}")
        return path

    search_roots: list[tuple[str, Path]] = [("ue4ss", ue4ss_root(game_root))]
    search_roots.extend(("user", p) for p in (args.usmap_search_root or []))
    env_root = os.environ.get("RSDW_USMAP_ROOT")
    if env_root:
        search_roots.append(("env", Path(env_root)))
    search_roots.append(("retoc cache", retoc_version_root))

    candidates: list[tuple[float, int, str, Path]] = []
    seen: set[Path] = set()
    for priority, (label, root) in enumerate(search_roots):
        if not root.exists():
            continue
        for path in root.rglob("*.usmap"):
            resolved = path.resolve()
            if resolved not in seen and resolved.is_file():
                seen.add(resolved)
                candidates.append((resolved.stat().st_mtime, priority, label, resolved))

    if not candidates:
        roots = "\n  ".join(f"{label}: {path}" for label, path in search_roots)
        raise SystemExit(
            "Could not find a .usmap file. Generate one with UE4SS, then pass --usmap if needed.\n"
            f"Searched:\n  {roots}"
        )

    candidates.sort(key=lambda item: (-item[0], item[1], str(item[3]).lower()))
    _, _, label, chosen = candidates[0]
    if len(candidates) > 1:
        print(f"Found {len(candidates)} .usmap files; using newest/preferred {label} source: {chosen}")
    return chosen


def copy_usmap_to_retoc(usmap: Path, retoc_version_root: Path, dry_run: bool) -> Path:
    dest = retoc_version_root / usmap.name
    if usmap.resolve() == dest.resolve():
        return dest

    if dest.is_file() and filecmp.cmp(usmap, dest, shallow=False):
        print(f"usmap already current in retoc cache: {dest}")
        return dest

    print(f"Copy usmap: {usmap} -> {dest}")
    if not dry_run:
        retoc_version_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(usmap, dest)
    return dest


def load_json_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def retoc_manifest_path(retoc_version_root: Path) -> Path:
    return retoc_version_root / RETOC_MANIFEST_NAME


def retoc_cache_has_package_data(retoc_version_root: Path) -> bool:
    rsdw = retoc_version_root / GAME_NAME
    engine = retoc_version_root / "Engine"
    if not (rsdw.is_dir() and engine.is_dir()):
        return False
    try:
        next(rsdw.rglob("*.uasset"))
        return True
    except StopIteration:
        return False


def retoc_cache_status(retoc_version_root: Path, version: str) -> RetocCacheStatus:
    if not retoc_version_root.exists():
        return RetocCacheStatus("missing", "cache folder does not exist")
    if not retoc_version_root.is_dir():
        return RetocCacheStatus("conflict", f"cache path exists but is not a directory: {retoc_version_root}")

    lock_path = retoc_version_root / RETOC_LOCK_NAME
    if lock_path.exists():
        return RetocCacheStatus("locked", f"retoc lock exists: {lock_path}")

    manifest = load_json_file(retoc_manifest_path(retoc_version_root))
    if manifest:
        manifest_game = manifest.get("game")
        manifest_version = manifest.get("version")
        if manifest_game and manifest_game != GAME_NAME:
            return RetocCacheStatus(
                "conflict",
                f"manifest game is {manifest_game!r}, expected {GAME_NAME!r}",
                manifest,
            )
        if manifest_version and manifest_version != version:
            return RetocCacheStatus(
                "conflict",
                f"manifest version is {manifest_version!r}, expected {version!r}",
                manifest,
            )

    if retoc_cache_has_package_data(retoc_version_root):
        return RetocCacheStatus("ready", "Engine/ and RSDragonwilds/ package data found", manifest)

    children = list(retoc_version_root.iterdir())
    allowed_partial = {
        RETOC_MANIFEST_NAME,
    }
    unexpected = [
        child
        for child in children
        if child.name not in allowed_partial and child.suffix.lower() != ".usmap"
    ]
    if not unexpected:
        return RetocCacheStatus("missing", "cache folder exists but only contains metadata/usmap files", manifest)

    preview = ", ".join(child.name for child in unexpected[:5])
    if len(unexpected) > 5:
        preview += ", ..."
    return RetocCacheStatus(
        "incomplete",
        f"cache folder is not ready and contains non-metadata files: {preview}",
        manifest,
    )


def write_retoc_manifest(
    *,
    retoc_version_root: Path,
    version: str,
    game_root: Path,
    paks_root: Path,
    usmap: Path,
    retoc_command: Sequence[str],
) -> None:
    manifest = {
        "schema": "RSDWModel.RetocCache.v1",
        "game": GAME_NAME,
        "version": version,
        "source_paks": str(paks_root),
        "game_root": str(game_root),
        "retoc_root": str(retoc_version_root),
        "usmap": str(usmap),
        "updated_at_utc": now_iso(),
        "generated_by": "RSDWModel/tools/UpdateGameData.py",
        "retoc_command": command_text(retoc_command),
    }
    retoc_manifest_path(retoc_version_root).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def acquire_retoc_lock(retoc_version_root: Path) -> Path:
    retoc_version_root.mkdir(parents=True, exist_ok=True)
    lock_path = retoc_version_root / RETOC_LOCK_NAME
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(
            f"Retoc cache is locked: {lock_path}\n"
            "Another pipeline may be running. If this is stale, delete the lock after verifying no retoc job is active."
        )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        payload = {
            "created_at_utc": now_iso(),
            "pid": os.getpid(),
            "script": str(Path(__file__).resolve()),
        }
        json.dump(payload, f, indent=2)
    return lock_path


def release_retoc_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def command_text(cmd: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(c) for c in cmd])


def run_command(
    title: str,
    cmd: Sequence[str],
    *,
    cwd: Path,
    log_path: Path | None,
    dry_run: bool,
    allow_failure: bool = False,
) -> int:
    print_section(title)
    print(command_text(cmd))
    if dry_run:
        return 0

    assert log_path is not None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"# {title}\n")
        log.write(f"# cwd: {cwd}\n")
        log.write(f"# started_utc: {now_iso()}\n")
        log.write(f"$ {command_text(cmd)}\n\n")
        log.flush()

        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        rc = proc.wait()
        log.write(f"\n# finished_utc: {now_iso()}\n")
        log.write(f"# exit_code: {rc}\n")
        if rc != 0 and not allow_failure:
            raise SystemExit(f"{title} failed with exit code {rc}. Log: {log_path}")
        return rc


def require_tool(name: str, *, allow_missing: bool = False) -> str | None:
    found = shutil.which(name)
    if found is None and not allow_missing:
        raise SystemExit(f"Required tool not found on PATH: {name}")
    return found


def load_inventory_count(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries")
        return len(entries) if isinstance(entries, list) else None
    except Exception:
        return None


def extension_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not root.is_dir():
        return counts
    for path in root.rglob("*"):
        if path.is_file():
            suffix = path.suffix.lower() or "<none>"
            counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def summarize_cue_manifest(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("Results") or []
    errors: dict[str, int] = {}
    exceptions: list[str] = []
    for row in results:
        err = row.get("Error")
        if not err:
            continue
        errors[err] = errors.get(err, 0) + 1
        if err not in {
            "skipped existing output",
            "no static or skeletal mesh exports found",
            "no supported exports found",
        }:
            package_path = row.get("PackagePath")
            if package_path:
                exceptions.append(f"{err}: {package_path}")
    return {
        "results": len(results),
        "errors": dict(sorted(errors.items(), key=lambda item: (-item[1], item[0]))),
        "exceptions": exceptions,
    }


def load_cue_warning_policy(root: Path) -> dict:
    path = root / CUE_WARNING_POLICY
    if not path.is_file():
        return {"rules": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"CUE warning policy is not an object: {path}")
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise SystemExit(f"CUE warning policy has no rules list: {path}")
    return data


def cue_policy_rule_for(policy: dict, *, error: str, version: str) -> dict | None:
    for rule in policy.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("error") != error:
            continue
        scope = rule.get("scope", "global")
        if scope == "global":
            return rule
        if scope == "version" and str(rule.get("version") or "") == version:
            return rule
    return None


def classify_cue_manifest(path: Path, *, root: Path, version: str) -> dict:
    if not path.is_file():
        return {
            "policy": str((root / CUE_WARNING_POLICY).resolve()),
            "status": "missing_manifest",
            "errors": {},
            "known_nonfatal": {},
            "unknown_warning": {},
            "blocking": {},
        }

    data = json.loads(path.read_text(encoding="utf-8"))
    policy = load_cue_warning_policy(root)
    known_nonfatal: dict[str, list[str]] = {}
    unknown_warning: dict[str, list[str]] = {}
    blocking: dict[str, list[str]] = {}
    errors: dict[str, int] = {}
    for row in data.get("Results") or []:
        error = row.get("Error")
        if not error:
            continue
        error = str(error)
        package = str(row.get("PackagePath") or "")
        errors[error] = errors.get(error, 0) + 1
        rule = cue_policy_rule_for(policy, error=error, version=version)
        classification = str((rule or {}).get("classification") or "unknown_warning")
        target = (
            known_nonfatal
            if classification == "known_nonfatal"
            else blocking
            if classification == "blocking"
            else unknown_warning
        )
        target.setdefault(error, []).append(package)

    status = "pass"
    if blocking:
        status = "blocking"
    elif unknown_warning:
        status = "unknown_warning"
    return {
        "policy": str((root / CUE_WARNING_POLICY).resolve()),
        "status": status,
        "errors": dict(sorted(errors.items(), key=lambda item: (-item[1], item[0]))),
        "known_nonfatal": {key: value[:10] for key, value in sorted(known_nonfatal.items())},
        "unknown_warning": {key: value[:10] for key, value in sorted(unknown_warning.items())},
        "blocking": {key: value[:10] for key, value in sorted(blocking.items())},
    }


def enforce_cue_policy(path: Path, *, root: Path, version: str, command_rc: int) -> dict:
    result = classify_cue_manifest(path, root=root, version=version)
    if result["status"] == "missing_manifest":
        if command_rc != 0:
            raise SystemExit(
                "CUE4Parse extract failed and did not produce CueExtractManifest.json.\n"
                f"Manifest expected: {path}"
            )
        return result
    if result["status"] != "pass":
        unknown = result.get("unknown_warning") or {}
        blocking = result.get("blocking") or {}
        details = []
        for label, rows in (("blocking", blocking), ("unknown", unknown)):
            for error, packages in rows.items():
                preview = "; ".join(packages[:3])
                details.append(f"{label}: {error} ({len(packages)} shown: {preview})")
        raise SystemExit(
            "CUE4Parse extract produced unapproved warning/error categories.\n"
            + "\n".join(details)
            + f"\nReview or update {root / CUE_WARNING_POLICY} before unattended release."
        )
    if command_rc != 0:
        print("CUE4Parse returned nonzero, but all manifest errors are classified known_nonfatal.")
    return result


def load_git_plan_summary(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    batches = data.get("batches") if isinstance(data.get("batches"), list) else []
    return {
        "changed_path_count": data.get("changed_path_count"),
        "allowed_path_count": data.get("allowed_path_count"),
        "blocked_path_count": data.get("blocked_path_count"),
        "batch_count": len(batches),
        "max_batch_bytes": data.get("max_batch_bytes"),
        "file_limit_bytes": data.get("file_limit_bytes"),
    }


def run_parallel_commands(
    title: str,
    jobs: list[tuple[str, Sequence[str], Path | None]],
    *,
    cwd: Path,
    dry_run: bool,
) -> None:
    print_section(title)
    for label, cmd, _log_path in jobs:
        print(f"[{label}] {command_text(cmd)}")
    if dry_run:
        return

    def task(label: str, cmd: Sequence[str], log_path: Path | None) -> tuple[str, int]:
        assert log_path is not None
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"# {title} {label}\n")
            log.write(f"# cwd: {cwd}\n")
            log.write(f"# started_utc: {now_iso()}\n")
            log.write(f"$ {command_text(cmd)}\n\n")
            log.flush()
            proc = subprocess.Popen(
                [str(c) for c in cmd],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"[{label}] {line}", end="")
                log.write(line)
            rc = proc.wait()
            log.write(f"\n# finished_utc: {now_iso()}\n")
            log.write(f"# exit_code: {rc}\n")
            return label, rc

    failed: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(task, label, cmd, log_path) for label, cmd, log_path in jobs]
        for future in as_completed(futures):
            label, rc = future.result()
            if rc != 0:
                failed.append((label, rc))
    if failed:
        detail = ", ".join(f"{label}={rc}" for label, rc in failed)
        raise SystemExit(f"{title} failed: {detail}")


def merge_animation_indexes(
    *,
    shard_paths: list[Path],
    output_index: Path,
    dataset_version: str,
    source_root: Path,
    output_root: Path,
    archive_json_root: Path,
    texture_size: int,
    texture_quality: int,
) -> None:
    merged = {
        "schema": "RSDWModel.WebsiteAnimationIndex.v1",
        "datasetVersion": dataset_version,
        "generatedUtc": now_iso(),
        "exportRevision": "animation-v1",
        "sourceModelIndex": "website/model-index.json",
        "sourceArchiveJson": str(archive_json_root),
        "sourceWebAssets": f"{dataset_version}/WebAssets",
        "sourceShards": [str(path.resolve()) for path in shard_paths],
        "byModel": {},
    }
    for path in shard_paths:
        if not path.is_file():
            raise SystemExit(f"Animation shard index missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        by_model = data.get("byModel") if isinstance(data, dict) else None
        if not isinstance(by_model, dict):
            continue
        for model_id, group in by_model.items():
            if not isinstance(group, dict):
                continue
            target = merged["byModel"].setdefault(
                model_id,
                {
                    "modelId": group.get("modelId") or model_id,
                    "modelName": group.get("modelName"),
                    "modelPath": group.get("modelPath"),
                    "skeletonPath": group.get("skeletonPath"),
                    "animations": [],
                },
            )
            existing_ids = {row.get("id") for row in target["animations"] if isinstance(row, dict)}
            for animation in group.get("animations") or []:
                if not isinstance(animation, dict) or animation.get("status") != "success":
                    continue
                if animation.get("id") in existing_ids:
                    continue
                target["animations"].append(animation)
                existing_ids.add(animation.get("id"))
    for group in merged["byModel"].values():
        group["animations"].sort(key=lambda item: str(item.get("label") or item.get("name") or "").lower())
    merged["totals"] = {
        "modelGroups": len(merged["byModel"]),
        "animations": sum(len(group.get("animations") or []) for group in merged["byModel"].values()),
        "success": sum(len(group.get("animations") or []) for group in merged["byModel"].values()),
        "unsupported": 0,
    }
    output_index.parent.mkdir(parents=True, exist_ok=True)
    output_index.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    modeldata_dir = repo_root() / "tools" / "ModelData"
    if str(modeldata_dir) not in sys.path:
        sys.path.insert(0, str(modeldata_dir))
    import BuildWebAssets as web_assets  # type: ignore

    manifest_path = output_root / "WebAssetManifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                manifest["web_animations"] = "website/animation-index.json"
                manifest["web_animations_updated_utc"] = now_iso()
                manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass
    web_assets._write_size_report(
        output_root=output_root,
        source_root=source_root,
        texture_records={},
        texture_profile={"format": "webp", "max_dimension": texture_size, "quality": texture_quality, "upscale": False},
    )


def normalized_prefixes(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def should_run_completion_stages(args: argparse.Namespace) -> bool:
    return (
        not args.skip_extract
        and not args.skip_inventory
        and not args.no_materials
        and args.extract_limit is None
        and not args.asset
        and not args.name
        and normalized_prefixes(args.prefix) == ("SM_", "SK_")
        and args.web_assets == "full"
        and args.web_asset_targets == "both"
    )


def resolve_equipment_variants_mode(args: argparse.Namespace) -> str:
    if args.equipment_variants != "auto":
        return args.equipment_variants
    return {"none": "none", "smoke": "smoke", "full": "full"}[args.web_assets]


def resolve_web_animations_mode(args: argparse.Namespace) -> str:
    if args.web_animations != "auto":
        return args.web_animations
    return {"none": "none", "smoke": "smoke", "full": "full"}[args.web_assets]


def write_pipeline_summary(
    *,
    path: Path,
    args: argparse.Namespace,
    version: str,
    game_root: Path,
    retoc_version_root: Path,
    usmap: Path,
    output_root: Path,
    archive_root: Path,
    equipment_variants_mode: str,
    web_animations_mode: str,
    log_dir: Path | None,
    dry_run: bool,
    completion_stages: bool,
    git_commit_plan: dict | None = None,
    resource_settings: ResourceSettings | None = None,
    blender: Path | None = None,
) -> None:
    cue_manifest_path = output_root / "CueExtractManifest.json"
    summary = {
        "schema": "RSDWModel.UpdatePipeline.v1",
        "updated_utc": now_iso(),
        "dry_run": dry_run,
        "completion_stages": completion_stages,
        "version": version,
        "game_version": version,
        "dataset_version": version,
        "archive_version": archive_root.name,
        "retoc_version": retoc_version_root.name,
        "game_root": str(game_root),
        "retoc_root": str(retoc_version_root),
        "usmap": str(usmap),
        "output_root": str(output_root),
        "archive_root": str(archive_root),
        "log_dir": str(log_dir) if log_dir else None,
        "blender": str(blender) if blender else None,
        "resource_settings": (
            {
                "profile": resource_settings.profile,
                "cpu_count": resource_settings.cpu_count,
                "web_asset_workers": resource_settings.web_asset_workers,
                "glb_workers": resource_settings.glb_workers,
                "web_animation_shards": resource_settings.web_animation_shards,
            }
            if resource_settings
            else None
        ),
        "web_assets": args.web_assets,
        "equipment_variants": equipment_variants_mode,
        "web_animations": web_animations_mode,
        "web_animation_shards": resource_settings.web_animation_shards if resource_settings else 1,
        "web_asset_targets": args.web_asset_targets,
        "web_texture_size": args.web_texture_size,
        "web_texture_quality": args.web_texture_quality,
        "website_index": {
            "skipped": args.skip_website_index,
            "archive_json_root": str(args.archive_json_root) if args.archive_json_root else str(archive_root / "json"),
        },
        "glb": args.glb,
        "counts_by_extension": extension_counts(output_root),
        "inventory": {
            "SM_Data.json": load_inventory_count(output_root / "ModelData" / "SM_Data.json"),
            "SK_Data.json": load_inventory_count(output_root / "ModelData" / "SK_Data.json"),
        },
        "cue_extract": summarize_cue_manifest(cue_manifest_path),
        "cue_extract_policy": classify_cue_manifest(cue_manifest_path, root=repo_root().resolve(), version=version),
        "git_commit_plan": git_commit_plan or {"skipped": True},
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the retoc -> CUE4Parse -> inventory -> web asset update pipeline.",
    )
    parser.add_argument("--game-root", type=Path, default=None, help="RSDragonwilds game root.")
    parser.add_argument("--version", default=None, help="Game version folder name. Defaults to detected ProjectVersion.")
    parser.add_argument("--retoc-base", type=Path, default=DEFAULT_RETOC_BASE)
    parser.add_argument("--output-root", type=Path, default=None, help="Output data root. Defaults to <repo>/<version>.")
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help=r"RSDWArchive version root. Defaults to E:\Github\RSDWArchive\<version>.",
    )
    parser.add_argument("--usmap", type=Path, default=None, help="Explicit .usmap path.")
    parser.add_argument(
        "--usmap-search-root",
        type=Path,
        action="append",
        default=[],
        help="Additional folder to search recursively for .usmap files. Repeatable.",
    )
    parser.add_argument("--cue4parse-root", type=Path, default=None)
    parser.add_argument(
        "--blender",
        type=Path,
        default=None,
        help="Blender executable used by web assets, animations, and legacy GLB stages.",
    )
    parser.add_argument(
        "--resource-profile",
        choices=("conservative", "balanced", "max"),
        default="balanced",
        help="Default worker/shard policy when a stage count is not set explicitly.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned work without running commands.")

    parser.add_argument("--skip-retoc", action="store_true")
    parser.add_argument("--force-retoc", action="store_true", help="Run retoc even if the cache looks populated.")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--force-extract", action="store_true", help="Re-export .uemodel files instead of resuming.")
    parser.add_argument(
        "--skip-archive-enrich",
        action="store_true",
        help="Skip Archive-backed material reference enrichment before inventory compilation.",
    )
    parser.add_argument("--skip-inventory", action="store_true")

    parser.add_argument("--extract-limit", type=int, default=None, help="Limit CUE extraction for smoke tests.")
    parser.add_argument("--asset", action="append", default=[], help="Exact package path for CUE extraction. Repeatable.")
    parser.add_argument("--name", action="append", default=[], help="Exact asset name for CUE extraction. Repeatable.")
    parser.add_argument("--prefix", default="SM_,SK_", help="Comma-separated CUE extraction filename prefixes.")
    parser.add_argument("--no-materials", action="store_true", help="Export .uemodel files only.")

    parser.add_argument(
        "--web-assets",
        choices=("none", "smoke", "full"),
        default="smoke",
        help="Shared web asset build mode. Default smoke builds one textured SM and one textured SK.",
    )
    parser.add_argument("--web-asset-targets", choices=("sm", "sk", "both"), default="both")
    parser.add_argument("--web-asset-workers", type=parse_positive_int_or_max, default=None)
    parser.add_argument("--web-smoke-limit", type=int, default=1)
    parser.add_argument("--web-texture-size", type=int, default=1024)
    parser.add_argument("--web-texture-quality", type=int, default=75)
    parser.add_argument(
        "--equipment-variants",
        choices=("auto", "none", "smoke", "full"),
        default="auto",
        help="Generate equipment material variant glTFs/index. Auto follows --web-assets.",
    )
    parser.add_argument(
        "--web-animations",
        choices=("auto", "none", "smoke", "full"),
        default="auto",
        help="Generate animated SK glTF variants/index. Auto follows --web-assets.",
    )
    parser.add_argument(
        "--web-animation-limit",
        type=int,
        default=None,
        help="Optional total animation build limit for --web-animations full.",
    )
    parser.add_argument(
        "--web-animation-shards",
        type=parse_positive_int_or_max,
        default=None,
        help="Parallel animation shard count for full animation builds. Use 'max' for the profile maximum.",
    )
    parser.add_argument(
        "--skip-website-index",
        action="store_true",
        help="Skip regenerating website/model-index.json and website/avatar-index.json.",
    )
    parser.add_argument(
        "--archive-json-root",
        type=Path,
        default=None,
        help="Optional legacy RSDWArchive <version>\\json root used for Avatar color curve palettes.",
    )

    parser.add_argument(
        "--glb",
        choices=("none", "smoke", "full"),
        default="none",
        help="Legacy standalone GLB build mode. Default is none.",
    )
    parser.add_argument("--glb-targets", choices=("sm", "sk", "both"), default="both")
    parser.add_argument("--glb-workers", type=parse_positive_int_or_max, default=None)
    parser.add_argument("--glb-smoke-limit", type=int, default=1)
    parser.add_argument("--no-blend", action="store_true", help="Deprecated legacy GLB option; .blend output is disabled.")

    parser.add_argument("--skip-git-plan", action="store_true", help="Skip final Git commit batch planning.")
    parser.add_argument("--run-git-plan", action="store_true", help="Run Git commit planning even for a partial run.")
    parser.add_argument("--git-plan-output", type=Path, default=None, help="Git commit plan JSON output path.")
    parser.add_argument("--git-max-batch-gb", type=float, default=DEFAULT_GIT_BATCH_GB)
    parser.add_argument("--git-file-limit-mb", type=float, default=DEFAULT_GIT_FILE_LIMIT_MB)
    parser.add_argument(
        "--git-commit-batches",
        action="store_true",
        help="Create Git commits from the final batch plan. This stages and commits files.",
    )
    parser.add_argument(
        "--git-push-each",
        action="store_true",
        help="Push after each Git commit batch. Requires --git-commit-batches.",
    )

    return parser.parse_args(argv)


def parse_publish_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish an already-built RSDWModel dataset through git planning/commit/push.",
    )
    parser.add_argument(
        "--git-mode",
        choices=("plan-only", "commit-only", "push-each", "project-commit", "project-push"),
        required=True,
        help="Master-facing git behavior. project-* aliases are accepted for compatibility.",
    )
    parser.add_argument("--version", required=True, help="Already-built dataset version to validate before publishing.")
    parser.add_argument("--repo", type=Path, default=None, help="Repo root. Defaults to this checkout.")
    parser.add_argument("--git-plan-output", type=Path, default=None, help="Git commit plan JSON output path.")
    parser.add_argument("--git-max-batch-gb", type=float, default=DEFAULT_GIT_BATCH_GB)
    parser.add_argument("--git-file-limit-mb", type=float, default=DEFAULT_GIT_FILE_LIMIT_MB)
    parser.add_argument("--dry-run", action="store_true", help="Print validation and git command without executing it.")
    return parser.parse_args(argv)


def normalize_publish_git_mode(mode: str) -> tuple[str, bool, bool]:
    aliases = {
        "plan-only": ("plan", False, False),
        "commit-only": ("commit-batches", True, False),
        "push-each": ("commit-batches", True, True),
        "project-commit": ("commit-batches", True, False),
        "project-push": ("commit-batches", True, True),
    }
    return aliases[mode]


def validate_publish_existing(*, root: Path, version: str, file_limit_mb: float) -> dict:
    output_root = root / version
    required = [
        output_root / "PipelineRun.json",
        output_root / "WebAssets" / "WebAssetManifest.json",
        output_root / "WebAssets" / "WebAssetSizeReport.json",
        root / "website" / "model-index.json",
        root / "website" / "avatar-index.json",
        root / "website" / "equipment-variants.json",
        root / "website" / "animation-index.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Publish-existing validation failed; missing required artifact(s):\n" + "\n".join(missing))

    pipeline_run = json.loads((output_root / "PipelineRun.json").read_text(encoding="utf-8"))
    dataset_version = pipeline_run.get("dataset_version") or pipeline_run.get("version")
    if dataset_version != version:
        raise SystemExit(
            f"Publish-existing validation failed; PipelineRun dataset version {dataset_version!r} "
            f"does not match requested {version!r}."
        )

    size_report_path = output_root / "WebAssets" / "WebAssetSizeReport.json"
    size_report = json.loads(size_report_path.read_text(encoding="utf-8"))
    oversized = size_report.get("output_files_over_100_mib") or []
    if oversized:
        preview = "\n".join(f"{row.get('path')} ({row.get('bytes')} bytes)" for row in oversized[:20])
        raise SystemExit(
            "Publish-existing validation failed; generated WebAssets include files over GitHub's "
            f"{file_limit_mb:g} MiB file limit:\n{preview}"
        )
    return {
        "output_root": str(output_root),
        "pipeline_run": str(output_root / "PipelineRun.json"),
        "size_report": str(size_report_path),
        "total_web_asset_bytes": size_report.get("total_web_asset_bytes"),
        "file_counts": size_report.get("file_counts"),
    }


def publish_existing_main(argv: list[str] | None = None) -> int:
    args = parse_publish_args(argv)
    root = (args.repo or repo_root()).resolve()
    git_plan_output = args.git_plan_output or (root / DEFAULT_GIT_PLAN_OUTPUT)
    if not git_plan_output.is_absolute():
        git_plan_output = (root / git_plan_output).resolve()
    git_command, execute, push_each = normalize_publish_git_mode(args.git_mode)

    print_section("Publish Existing Validation")
    validation = validate_publish_existing(root=root, version=args.version, file_limit_mb=args.git_file_limit_mb)
    print(json.dumps(validation, indent=2))

    require_tool("git")
    git_cmd = [
        sys.executable,
        str(root / "tools" / "PlanGitCommits.py"),
        git_command,
        "--repo",
        str(root),
        "--out",
        str(git_plan_output),
        "--max-batch-gb",
        str(args.git_max_batch_gb),
        "--file-limit-mb",
        str(args.git_file_limit_mb),
        "--message-prefix",
        f"Update RSDWModel {args.version}",
    ]
    if execute:
        git_cmd.append("--execute")
    if push_each:
        git_cmd.append("--push-each")

    run_command(
        "Publish existing git plan" if not execute else "Publish existing git batches",
        git_cmd,
        cwd=root,
        log_path=root / "PipelineLogs" / f"publish-existing-{utc_stamp()}.log",
        dry_run=args.dry_run,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "publish":
        return publish_existing_main(argv[1:])
    args = parse_args(argv)
    root = repo_root().resolve()
    game_root = detect_game_root(args.game_root)
    version = detect_game_version(game_root, args.version)
    retoc_version_root = (args.retoc_base / version).resolve()
    output_root = (args.output_root or (root / version)).resolve()
    archive_root = (args.archive_root or (DEFAULT_ARCHIVE_BASE / version)).resolve()
    archive_json_root = args.archive_json_root.resolve() if args.archive_json_root else (archive_root / "json").resolve()
    equipment_variants_mode = resolve_equipment_variants_mode(args)
    web_animations_mode = resolve_web_animations_mode(args)
    web_animations_stage = web_animations_mode != "none" and not args.skip_website_index
    resource_settings = resolve_resource_settings(args)
    blender = (args.blender or default_blender(root)).resolve()
    cue4parse_root = (
        args.cue4parse_root
        or (Path(os.environ["CUE4PARSE_ROOT"]) if os.environ.get("CUE4PARSE_ROOT") else None)
        or DEFAULT_CUE4PARSE_ROOT
    ).resolve()

    paks_root = game_root / "Content" / "Paks"
    if not paks_root.is_dir():
        raise SystemExit(f"Game Paks folder not found: {paks_root}")

    usmap_source = find_usmap(args, game_root, retoc_version_root)
    usmap = copy_usmap_to_retoc(usmap_source, retoc_version_root, args.dry_run)
    log_dir = None if args.dry_run else output_root / "PipelineLogs" / utc_stamp()
    completion_stages = should_run_completion_stages(args)
    git_plan_stage = (completion_stages or args.run_git_plan or args.git_commit_batches) and not args.skip_git_plan
    if args.git_push_each and not args.git_commit_batches:
        raise SystemExit("--git-push-each requires --git-commit-batches.")
    git_plan_output = args.git_plan_output or (root / DEFAULT_GIT_PLAN_OUTPUT)
    if not git_plan_output.is_absolute():
        git_plan_output = (root / git_plan_output).resolve()

    print_section("Resolved Pipeline")
    print(f"repo:       {root}")
    print(f"game:       {game_root}")
    print(f"ue4ss:      {ue4ss_root(game_root)}")
    print(f"version:    {version}")
    print(f"retoc:      {retoc_version_root}")
    print(f"usmap:      {usmap}")
    print(f"output:     {output_root}")
    print(f"archive:    {archive_root}")
    print(f"cue4parse:  {cue4parse_root}")
    print(f"blender:    {blender}")
    print(
        "resources:  "
        f"profile={resource_settings.profile}, cpu={resource_settings.cpu_count}, "
        f"web_workers={resource_settings.web_asset_workers}, "
        f"animation_shards={resource_settings.web_animation_shards}, "
        f"glb_workers={resource_settings.glb_workers}"
    )
    print(f"variants:   {equipment_variants_mode}")
    print(f"animations: {web_animations_mode}")
    print(f"completion: {completion_stages}")
    print(f"git plan:   {git_plan_output if git_plan_stage else '<skipped>'}")
    if log_dir:
        print(f"logs:       {log_dir}")

    if not args.skip_retoc:
        require_tool("retoc")
    if not args.skip_extract or web_animations_stage:
        require_tool("dotnet")
    if not args.skip_extract:
        if not cue4parse_root.is_dir():
            raise SystemExit(
                f"CUE4Parse source checkout not found: {cue4parse_root}\n"
                "Clone FabianFG/CUE4Parse there or pass --cue4parse-root."
            )
    if web_animations_stage and not cue4parse_root.is_dir():
        raise SystemExit(
            f"CUE4Parse source checkout not found: {cue4parse_root}\n"
            "Clone FabianFG/CUE4Parse there or pass --cue4parse-root."
        )
    if git_plan_stage:
        require_tool("git")
    if any((args.web_assets != "none", web_animations_stage, args.glb != "none")) and not args.dry_run:
        if not blender.is_file():
            raise SystemExit(f"blender.exe not found: {blender}")
    if equipment_variants_mode != "none" and not args.skip_website_index:
        if not (archive_root / "json").is_dir() or not (archive_root / "textures").is_dir():
            raise SystemExit(
                "Equipment variants require an RSDWArchive version root with json/ and textures/.\n"
                f"Expected: {archive_root}\n"
                "Pass --archive-root, use --equipment-variants none, or build/sync RSDWArchive first."
            )
    if web_animations_mode != "none" and not args.skip_website_index:
        if not archive_json_root.is_dir():
            raise SystemExit(
                "Web animations require an RSDWArchive json/ root for skeleton/animation metadata.\n"
                f"Expected: {archive_json_root}\n"
                "Pass --archive-json-root, use --web-animations none, or build/sync RSDWArchive first."
            )

    if not args.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)

    cue_project = root / "tools" / "CueExtract" / "RsdwCueExtract" / "RsdwCueExtract.csproj"
    cue_prebuilt = not args.skip_extract or web_animations_stage
    if cue_prebuilt:
        run_command(
            "CUE4Parse extractor build",
            [
                "dotnet",
                "build",
                str(cue_project),
                f"/p:Cue4ParseRoot={cue4parse_root}",
            ],
            cwd=root,
            log_path=log_dir / "00_cue_extract_build.log" if log_dir else None,
            dry_run=args.dry_run,
        )

    if args.skip_retoc:
        print_section("retoc")
        print("Skipped by --skip-retoc")
    else:
        status = retoc_cache_status(retoc_version_root, version)
        print_section("retoc")
        print(f"Cache status: {status.state} ({status.detail})")
        if status.state == "ready" and not args.force_retoc:
            print(f"Skipping retoc; cache already populated: {retoc_version_root}")
        elif status.state in {"conflict", "locked"}:
            raise SystemExit(f"Refusing to run retoc: {status.detail}")
        elif status.state == "incomplete" and not args.force_retoc:
            raise SystemExit(
                "Refusing to run retoc into an incomplete non-empty cache.\n"
                f"{status.detail}\n"
                f"Inspect or clean this folder first: {retoc_version_root}\n"
                "Use --force-retoc only if you intentionally want retoc to write into it."
            )
        else:
            retoc_cmd = ["retoc", "to-legacy", str(paks_root), str(retoc_version_root)]
            lock_path: Path | None = None
            try:
                if not args.dry_run:
                    lock_path = acquire_retoc_lock(retoc_version_root)
                run_command(
                    "retoc to-legacy",
                    retoc_cmd,
                    cwd=root,
                    log_path=log_dir / "01_retoc.log" if log_dir else None,
                    dry_run=args.dry_run,
                )
                if not args.dry_run:
                    write_retoc_manifest(
                        retoc_version_root=retoc_version_root,
                        version=version,
                        game_root=game_root,
                        paks_root=paks_root,
                        usmap=usmap,
                        retoc_command=retoc_cmd,
                    )
            finally:
                if lock_path is not None:
                    release_retoc_lock(lock_path)

    if args.skip_extract:
        print_section("CUE4Parse extract")
        print("Skipped by --skip-extract")
    else:
        cmd: list[str] = [
            "dotnet",
            "run",
            "--no-build",
            "--project",
            str(cue_project),
            f"/p:Cue4ParseRoot={cue4parse_root}",
            "--",
            "--retoc-root",
            str(retoc_version_root),
            "--usmap",
            str(usmap),
            "--out",
            str(output_root),
            "--prefix",
            args.prefix,
        ]
        for asset in args.asset:
            cmd.extend(["--asset", asset])
        for name in args.name:
            cmd.extend(["--name", name])
        if args.extract_limit is not None:
            cmd.extend(["--limit", str(args.extract_limit)])
        elif not args.asset and not args.name:
            cmd.append("--all")
        if args.force_extract:
            cmd.append("--force")
        if args.no_materials:
            cmd.append("--no-materials")

        cue_rc = run_command(
            "CUE4Parse extract",
            cmd,
            cwd=root,
            log_path=log_dir / "02_cue_extract.log" if log_dir else None,
            dry_run=args.dry_run,
            allow_failure=True,
        )
        enforce_cue_policy(output_root / "CueExtractManifest.json", root=root, version=version, command_rc=cue_rc)

    print_section("Archive material enrichment")
    archive_available = (archive_root / "json").is_dir() and (archive_root / "textures").is_dir()
    if args.skip_archive_enrich:
        print("Skipped by --skip-archive-enrich")
    elif not archive_available:
        print(f"Skipped; archive json/ and textures/ not found at: {archive_root}")
    else:
        run_command(
            "Enrich material refs from Archive",
            [
                sys.executable,
                str(root / "tools" / "ModelData" / "EnrichFromArchive.py"),
                "--source-root",
                str(output_root),
                "--archive-root",
                str(archive_root),
            ],
            cwd=root,
            log_path=log_dir / "02b_archive_enrich.log" if log_dir else None,
            dry_run=args.dry_run,
        )

    if args.skip_inventory:
        print_section("Model inventory")
        print("Skipped by --skip-inventory")
    else:
        run_command(
            "Compile model inventory",
            [
                sys.executable,
                str(root / "tools" / "ModelData" / "CompileModelData.py"),
                "--source-root",
                str(output_root),
                "--output-dir",
                str(output_root / "ModelData"),
            ],
            cwd=root,
            log_path=log_dir / "03_compile_model_data.log" if log_dir else None,
            dry_run=args.dry_run,
        )

    if args.web_assets == "none":
        print_section("Web asset build")
        print("Skipped by --web-assets none")
    else:
        cmd = [
            sys.executable,
            str(root / "tools" / "ModelData" / "BuildWebAssets.py"),
            "--source-root",
            str(output_root),
            "--output-root",
            str(output_root / "WebAssets"),
            "--targets",
            args.web_asset_targets,
            "--texture-size",
            str(args.web_texture_size),
            "--texture-quality",
            str(args.web_texture_quality),
            "--blender",
            str(blender),
        ]
        if args.web_assets == "smoke":
            cmd.extend(["--limit", str(args.web_smoke_limit), "--force", "--workers", "1", "--prefer-textured"])
        else:
            cmd.extend(["--workers", str(resource_settings.web_asset_workers)])

        run_command(
            f"Web asset build ({args.web_assets})",
            cmd,
            cwd=root,
            log_path=log_dir / "04_web_assets.log" if log_dir else None,
            dry_run=args.dry_run,
        )

    if args.skip_website_index:
        print_section("Website indexes")
        print("Skipped by --skip-website-index")
    else:
        run_command(
            "Generate website model index",
            [
                sys.executable,
                str(root / "tools" / "generate_model_index.py"),
                "--repo-root",
                str(root),
                "--dataset-version",
                version,
            ],
            cwd=root,
            log_path=log_dir / "05a_website_model_index.log" if log_dir else None,
            dry_run=args.dry_run,
        )

        if equipment_variants_mode == "none":
            print_section("Equipment variants")
            print("Skipped by --equipment-variants none")
        else:
            run_command(
                f"Generate equipment variants ({equipment_variants_mode})",
                [
                    sys.executable,
                    str(root / "tools" / "generate_equipment_variants.py"),
                    "--repo-root",
                    str(root),
                    "--dataset-version",
                    version,
                    "--archive-root",
                    str(archive_root),
                    "--mode",
                    equipment_variants_mode,
                    "--texture-size",
                    str(args.web_texture_size),
                    "--texture-quality",
                    str(args.web_texture_quality),
                ],
                cwd=root,
                log_path=log_dir / "05b_equipment_variants.log" if log_dir else None,
                dry_run=args.dry_run,
            )

        if web_animations_mode == "none":
            print_section("Web animations")
            print("Skipped by --web-animations none")
        else:
            animation_cmd = [
                sys.executable,
                str(root / "tools" / "ModelData" / "BuildWebAnimations.py"),
                "--repo-root",
                str(root),
                "--dataset-version",
                version,
                "--source-root",
                str(output_root),
                "--output-root",
                str(output_root / "WebAssets"),
                "--archive-json-root",
                str(archive_json_root),
                "--retoc-root",
                str(retoc_version_root),
                "--usmap",
                str(usmap),
                "--cue4parse-root",
                str(cue4parse_root),
                "--mode",
                web_animations_mode,
                "--texture-size",
                str(args.web_texture_size),
                "--texture-quality",
                str(args.web_texture_quality),
                "--blender",
                str(blender),
            ]
            if cue_prebuilt:
                animation_cmd.append("--cue-no-build")
            if args.web_animation_limit is not None:
                animation_cmd.extend(["--limit", str(args.web_animation_limit)])
            if web_animations_mode == "smoke":
                animation_cmd.append("--force")

            animation_shards = (
                resource_settings.web_animation_shards
                if web_animations_mode == "full" and args.web_animation_limit is None
                else 1
            )
            if animation_shards <= 1:
                run_command(
                    f"Build web animations ({web_animations_mode})",
                    animation_cmd,
                    cwd=root,
                    log_path=log_dir / "05c_web_animations.log" if log_dir else None,
                    dry_run=args.dry_run,
                )
            else:
                shard_root = root / "PipelineLogs" / "AnimationShards" / version / utc_stamp()
                shard_jobs: list[tuple[str, Sequence[str], Path | None]] = []
                shard_paths: list[Path] = []
                for shard_index in range(animation_shards):
                    shard_dir = shard_root / f"shard-{shard_index:02d}"
                    shard_index_path = shard_dir / "animation-index.json"
                    shard_paths.append(shard_index_path)
                    shard_cmd = [
                        *animation_cmd,
                        "--shard-index",
                        str(shard_index),
                        "--shard-count",
                        str(animation_shards),
                        "--output-index",
                        str(shard_index_path),
                        "--animation-cache-root",
                        str(shard_dir / "AnimationExtract"),
                        "--skip-manifest-mark",
                        "--skip-size-report",
                    ]
                    shard_jobs.append(
                        (
                            f"shard-{shard_index:02d}",
                            shard_cmd,
                            log_dir / f"05c_web_animations_shard_{shard_index:02d}.log" if log_dir else None,
                        )
                    )
                run_parallel_commands(
                    f"Build web animations ({web_animations_mode}, {animation_shards} shards)",
                    shard_jobs,
                    cwd=root,
                    dry_run=args.dry_run,
                )
                if args.dry_run:
                    print_section("Merge web animation shards")
                    print(f"Would merge {animation_shards} shard index file(s) into {root / 'website' / 'animation-index.json'}")
                else:
                    merge_animation_indexes(
                        shard_paths=shard_paths,
                        output_index=root / "website" / "animation-index.json",
                        dataset_version=version,
                        source_root=output_root,
                        output_root=output_root / "WebAssets",
                        archive_json_root=archive_json_root,
                        texture_size=args.web_texture_size,
                        texture_quality=args.web_texture_quality,
                    )

        avatar_cmd = [
            sys.executable,
            str(root / "tools" / "generate_avatar_index.py"),
            "--repo-root",
            str(root),
            "--dataset-version",
            version,
            "--texture-quality",
            str(args.web_texture_quality),
            "--force-textures",
        ]
        if archive_json_root.is_dir():
            avatar_cmd.extend(["--archive-json-root", str(archive_json_root)])
        if equipment_variants_mode != "none":
            avatar_cmd.extend(["--equipment-variants", str(root / "website" / "equipment-variants.json")])

        run_command(
            "Generate website avatar index",
            avatar_cmd,
            cwd=root,
            log_path=log_dir / "05d_website_avatar_index.log" if log_dir else None,
            dry_run=args.dry_run,
        )

    if args.glb == "none":
        print_section("GLB build")
        print("Skipped by --glb none")
    else:
        targets = ["SM"] if args.glb_targets == "sm" else ["SK"] if args.glb_targets == "sk" else ["SM", "SK"]
        for index, target in enumerate(targets, start=1):
            data_file = output_root / "ModelData" / f"{target}_Data.json"
            progress_file = output_root / f"BuildProgress_{target}.json"
            cmd = [
                sys.executable,
                str(root / "tools" / "ModelData" / "BuildGLB.py"),
                "--source-root",
                str(output_root),
                "--data-file",
                str(data_file),
                "--progress-file",
                str(progress_file),
                "--blender",
                str(blender),
            ]
            if args.glb == "smoke":
                cmd.extend(["--limit", str(args.glb_smoke_limit), "--force", "--workers", "1"])
            else:
                cmd.extend(["--workers", str(resource_settings.glb_workers)])
            if args.no_blend:
                cmd.append("--no-blend")

            run_command(
                f"GLB build {target} ({args.glb})",
                cmd,
                cwd=root,
                log_path=log_dir / f"05{index}_{target.lower()}_glb.log" if log_dir else None,
                dry_run=args.dry_run,
            )

    git_commit_plan_summary: dict | None = None
    if git_plan_stage:
        git_mode = "commit-batches" if args.git_commit_batches else "plan"
        git_cmd = [
            sys.executable,
            str(root / "tools" / "PlanGitCommits.py"),
            git_mode,
            "--repo",
            str(root),
            "--out",
            str(git_plan_output),
            "--max-batch-gb",
            str(args.git_max_batch_gb),
            "--file-limit-mb",
            str(args.git_file_limit_mb),
            "--message-prefix",
            f"Update RSDWModel {version}",
        ]
        if args.git_commit_batches:
            git_cmd.append("--execute")
        if args.git_push_each:
            git_cmd.append("--push-each")

        run_command(
            "Git commit plan" if not args.git_commit_batches else "Git commit batches",
            git_cmd,
            cwd=root,
            log_path=log_dir / "06_git_commit_plan.log" if log_dir else None,
            dry_run=args.dry_run,
        )
        git_commit_plan_summary = {
            "skipped": False,
            "mode": git_mode,
            "plan": str(git_plan_output),
            "commit_batches": args.git_commit_batches,
            "push_each": args.git_push_each,
            **({} if args.dry_run else load_git_plan_summary(git_plan_output)),
        }
    else:
        print_section("Git commit plan")
        print("Skipped by --skip-git-plan or partial/smoke pipeline run.")
        git_commit_plan_summary = {
            "skipped": True,
            "reason": "--skip-git-plan" if args.skip_git_plan else "partial/smoke pipeline run",
        }

    if args.dry_run:
        print_section("Dry Run Complete")
        print("No files were written and no commands were executed.")
    else:
        summary_path = output_root / "PipelineRun.json"
        write_pipeline_summary(
            path=summary_path,
            args=args,
            version=version,
            game_root=game_root,
            retoc_version_root=retoc_version_root,
            usmap=usmap,
            output_root=output_root,
            archive_root=archive_root,
            equipment_variants_mode=equipment_variants_mode,
            web_animations_mode=web_animations_mode,
            log_dir=log_dir,
            dry_run=False,
            completion_stages=completion_stages,
            git_commit_plan=git_commit_plan_summary,
            resource_settings=resource_settings,
            blender=blender,
        )
        print_section("Summary")
        print(f"Wrote {summary_path}")
        print(f"SM entries: {load_inventory_count(output_root / 'ModelData' / 'SM_Data.json')}")
        print(f"SK entries: {load_inventory_count(output_root / 'ModelData' / 'SK_Data.json')}")
        web_report = output_root / "WebAssets" / "WebAssetSizeReport.json"
        if web_report.is_file():
            print(f"Web asset size report: {web_report}")
        counts = extension_counts(output_root)
        print("File counts:")
        for ext, count in counts.items():
            print(f"  {ext}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
