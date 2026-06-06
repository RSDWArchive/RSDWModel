# RSDWModel

RSDWModel is a CLI-first asset pipeline for RuneScape: Dragonwilds model data.
It turns Unreal package data into `.uemodel` exports, builds versioned model
inventories, and converts those models into website-ready `.gltf` / `.bin`
assets with shared WebP textures.

## Source Data

Game data is kept in versioned folders at the repo root:

- `0.11.2.2/` - current extracted/exported data.

The tooling scans the source tree on disk. Asset names are not hardcoded into
the pipeline.

Only the newest versioned folder is kept in this repo. It is a commit-ready
artifact containing extracted data, inventories, manifests, and generated
website assets. Transient logs, pipeline run summaries, caches, `.glb`,
`.blend`, and `.blend1` outputs are ignored.

The upstream package cache is also versioned outside the repo:

- `E:\Github\Retoc\RSDragonwilds\<game-version>\`

The current `.usmap` and game version metadata come from the installed game's
UE4SS folder:

- `F:\SteamLibrary\steamapps\common\RSDragonwilds\RSDragonwilds\Binaries\Win64\ue4ss`

The `.usmap` is expected directly in that folder, and `ProjectVersion` is read
from `ue4ss\UHTHeaderDump\EngineSettings\Private\GeneralProjectSettings.cpp`.

## Pipeline

1. Generate the current `.usmap` and UHT header dump with UE4SS.
2. Convert the game's `Content\Paks` folder with `retoc to-legacy`.
3. Export `SM_` and `SK_` packages to `.uemodel` with the CUE4Parse CLI tool in
   `tools/CueExtract/RsdwCueExtract`.
4. Compile `SM_Data.json` and `SK_Data.json` inventories with
   `tools/ModelData/CompileModelData.py`.
5. Build shared web assets with `tools/ModelData/BuildWebAssets.py`, which
   writes separate `.gltf` / `.bin` model files and a shared WebP texture cache.

This replaces the old manual export step while keeping the existing
Python/Blender conversion path.

## Architecture

This repository owns the RSDW-specific model pipeline:

- versioned RSDW output folders such as `0.11.2.2/`
- model inventories and web asset conversion scripts
- the `RsdwCueExtract` wrapper that selects RSDW packages and exports
  `.uemodel` / material / texture data
- orchestration through `tools/UpdateGameData.py`

Two external/shared dependencies sit beside this repo:

- `E:\Github\CUE4Parse` - the source checkout of FabianFG/CUE4Parse. The local
  extractor references this because the current `.usmap` format needs newer
  source than the NuGet package.
- `E:\Github\Retoc` - the shared output/cache location for `retoc to-legacy`
  data, organized per game/version.

RSDWModel does not own those two shared locations. It calls into them. Other
projects can use the same CUE4Parse checkout and the same Retoc cache pattern,
with their own project-specific extractor/orchestrator.

## One-Command Update

Use `tools/UpdateGameData.py` when the game updates. It detects the installed
game version, uses `E:\Github\Retoc\RSDragonwilds\<version>` as the retoc cache,
copies the `.usmap` from the game's UE4SS folder, runs the CUE4Parse extractor,
compiles model data, and runs one SM plus one SK shared web asset smoke test by
default.

Preview the work without running commands:

```powershell
python tools\UpdateGameData.py --dry-run
```

Normal update run:

```powershell
python tools\UpdateGameData.py
```

Run the full shared web asset build after extraction and inventory:

```powershell
python tools\UpdateGameData.py --web-assets full --web-asset-workers 8 --glb none
```

Run the full update and write a safe commit plan at the end:

```powershell
python tools\UpdateGameData.py --web-assets full --web-asset-workers 8 --glb none --run-git-plan
```

Run the full update, create commit batches, and push after each batch:

```powershell
python tools\UpdateGameData.py --web-assets full --web-asset-workers 8 --glb none --git-commit-batches --git-push-each
```

For the current already-built working tree, skip data rebuild stages and only
plan/commit/push the repo state:

```powershell
python tools\UpdateGameData.py --skip-retoc --skip-extract --skip-inventory --web-assets none --glb none --git-commit-batches --git-push-each
```

Useful update flags:

- `--game-root PATH` - override the detected Steam install.
- `--version VERSION` - override detected `ProjectVersion`.
- `--usmap PATH` - use a specific `.usmap`.
- `--retoc-base PATH` - override the shared retoc cache root.
- `--cue4parse-root PATH` - override the CUE4Parse source checkout.
- `--skip-retoc` - reuse the retoc cache.
- `--force-retoc` - run retoc even if the cache looks populated.
- `--force-extract` - re-export existing `.uemodel` files.
- `--extract-limit N` - smoke-test the CUE extraction.
- `--web-assets none|smoke|full` - choose shared web asset build scope.
- `--web-asset-targets sm|sk|both` - choose SM/SK web asset targets.
- `--web-texture-size N` - max WebP texture dimension, default `1024`.
- `--web-texture-quality N` - WebP quality, default `75`.
- `--glb none|smoke|full` - legacy standalone GLB build scope, default `none`.
- `--run-git-plan` - run commit planning even for a partial/smoke update.
- `--skip-git-plan` - skip the final commit planning stage.
- `--git-commit-batches` - create the planned commits.
- `--git-push-each` - push after each created commit batch.
- `--git-max-batch-gb N` - target max batch size, default `1.9`.
- `--git-file-limit-mb N` - hard per-file warning limit, default `100`.

The script writes logs under `<version>\PipelineLogs\<timestamp>\` and a compact
summary to `<version>\PipelineRun.json`.

Update safeguards:

- Missing game root, missing UE4SS header dump, missing `.usmap`, missing
  `retoc`, or missing CUE4Parse source checkout stops the run with a direct
  error.
- A populated retoc cache is skipped by default.
- A retoc cache with a manifest for a different game/version is treated as a
  conflict and is not overwritten.
- A non-empty but incomplete retoc cache stops the run unless `--force-retoc`
  is passed.
- While retoc is writing, `.retoc.lock` prevents another pipeline from writing
  to the same game/version cache.
- Successful retoc runs write `retoc-manifest.json` into the cache.

## CUE4Parse Extraction

The extractor is documented in `tools/CueExtract/README.md`. For the current
RSDW `.usmap`, use a current CUE4Parse source checkout through
`/p:Cue4ParseRoot=E:\Github\CUE4Parse` or the `CUE4PARSE_ROOT` environment
variable.

Example full export:

```powershell
dotnet run --project tools\CueExtract\RsdwCueExtract `
  /p:Cue4ParseRoot=E:\Github\CUE4Parse `
  -- `
  --retoc-root E:\Github\Retoc\RSDragonwilds\0.11.2.2 `
  --usmap E:\Github\Retoc\RSDragonwilds\0.11.2.2\RSDragonwilds-5.6.1-203193+++dominion+staging-0196ef29.usmap `
  --out E:\Github\RSDWModel\0.11.2.2 `
  --all
```

Useful flags:

- `--limit N` - smoke-test a subset.
- `--name ASSET` - export an exact asset name.
- `--asset PATH` - export an exact package path.
- `--prefix SM_,SK_` - choose package filename prefixes.
- `--force` - re-export existing `.uemodel` files.
- `--no-materials` - export meshes only.

The extractor writes `CueExtractManifest.json` and resumes by skipping packages
whose expected `.uemodel` output already exists.

## Model Inventory

`tools/ModelData/CompileModelData.py` walks `--source-root` and writes
`SM_Data.json` plus `SK_Data.json`.

Each entry includes:

- `name` and repo-relative `path`.
- `Materials` discovered from nearby material JSON files with a top-level
  `Textures` block.
- `MaterialsHybrid` fallback texture image paths found from nearby
  `Textures` / `Texture` folders and loose image files.

Example:

```powershell
python tools\ModelData\CompileModelData.py `
  --source-root E:\Github\RSDWModel\0.11.2.2 `
  --output-dir E:\Github\RSDWModel\0.11.2.2\ModelData
```

## Web Asset Build

`tools/ModelData/BuildWebAssets.py` reads model inventories, generates each
unique source texture once as shared WebP, and starts bounded headless Blender
workers using `blender-5.0.0-windows-x64\blender.exe`.

Generated output lives under `<version>\WebAssets\`:

- `models\<kind>\<asset-name>-<path-hash>\model.gltf`
- `models\<kind>\<asset-name>-<path-hash>\model.bin`
- `textures\webp_1024\<hash>.webp`
- `WebAssetManifest.json`
- `WebAssetSizeReport.json`

The glTF export uses `GLTF_SEPARATE`, shared `EXT_texture_webp` texture URIs,
and `KHR_draco_mesh_compression`.

Examples:

```powershell
python tools\ModelData\BuildWebAssets.py `
  --source-root E:\Github\RSDWModel\0.11.2.2 `
  --targets both `
  --limit 1 `
  --prefer-textured `
  --force `
  --workers 1

python tools\ModelData\BuildWebAssets.py `
  --source-root E:\Github\RSDWModel\0.11.2.2 `
  --targets both `
  --texture-size 1024 `
  --texture-quality 75 `
  --workers 8
```

Useful flags:

- `--targets sm|sk|both` - choose inventory targets.
- `--workers N` - parallel Blender processes.
- `--timeout-s N` - per-entry timeout.
- `--limit N` - cap selected entries per target.
- `--only SUBSTR` - substring filter by entry name or path.
- `--only-list PATH` - run specific inventory paths from a text file.
- `--prefer-textured` - pick textured entries first when using `--limit`.
- `--force` - rebuild entries already marked `success`.
- `--texture-size N` - max WebP texture dimension.
- `--texture-quality N` - WebP quality.
- `--texture-recovery-passes N` - automatic follow-up passes for textures first
  discovered by Blender during export, default `1`.
- `--dry-run` - list planned work and exit.

`WebAssetSizeReport.json` is the source of truth for checking generated website
asset size before committing or pushing.

## Website

The static model viewer lives in `website/`. It is designed for GitHub Pages
and loads `website/model-index.json` from the Pages artifact. Model payloads are
loaded from the configured asset base in `website/data.config.json`.

Current publish config:

- Repository: `RSDWArchive/RSDWModel`
- Branch: `main`
- Dataset: `0.11.2.2`
- Custom domain: `rsdwmodel.com`

The Pages workflow deploys only `website/`; it does not copy generated model
assets into the Pages artifact. On localhost, the site reads
`../0.11.2.2/WebAssets`. When deployed, `assetBaseUrl: "auto"` resolves model
URLs to raw GitHub content under
`https://raw.githubusercontent.com/RSDWArchive/RSDWModel/main/0.11.2.2/WebAssets`.

The optimized `WebAssets/` corpus is tracked in git so the deployed static site
can load model payloads from raw GitHub URLs. The latest measured generated
corpus is about `784 MiB`, with no generated file over `50 MiB`. Keep checking
`WebAssetSizeReport.json` after clean rebuilds before pushing.

The oversized extracted source normal
`T_Dragon_Imaru_01_N.png` is intentionally excluded from git because it is over
GitHub's normal file limit. The optimized website output can still carry its
current baked texture as long as the generated `WebAssets/` cache is preserved.

## Git Commit Planning

Large data updates should be planned before staging or pushing:

```powershell
python tools\PlanGitCommits.py --out PipelineLogs\GitCommitPlan.json
```

The planner flags changed files over `100 MiB` and splits the remaining changed
paths into conservative batches under `1.9 GiB`. It is a working-tree planner,
so it does not replace a history scan when preparing an unpushed repo whose
existing commits may already contain oversized blobs.

`tools/UpdateGameData.py` calls this planner automatically for full web-asset
updates, and can optionally run `commit-batches` through `--git-commit-batches`.

### Custom Domain

`website/CNAME` contains `rsdwmodel.com` as the repo-visible domain marker. This
site deploys through GitHub Actions, so the GitHub Pages custom domain setting
is still the source of truth.

In GitHub, configure Pages for the repository with GitHub Actions as the
deployment source and set the custom domain to `rsdwmodel.com`. In Namecheap
Advanced DNS, point the apex domain at GitHub Pages and point `www` back to the
apex domain:

```text
Type   Host  Value
A      @     185.199.108.153
A      @     185.199.109.153
A      @     185.199.110.153
A      @     185.199.111.153
CNAME  www   rsdwmodel.com
```

Remove any conflicting parking, redirect, A, ALIAS, or CNAME records for `@`
or `www`. Once DNS has propagated and GitHub has issued the certificate, enable
`Enforce HTTPS` in Pages settings.

## Legacy GLB Build

`tools/ModelData/BuildGLB.py` reads a model inventory and starts bounded
headless Blender workers using `blender-5.0.0-windows-x64\blender.exe`.
Standalone GLB output is retained as a manual legacy path and is not the default
pipeline target.

Examples:

```powershell
python tools\ModelData\BuildGLB.py `
  --source-root E:\Github\RSDWModel\0.11.2.2 `
  --data-file E:\Github\RSDWModel\0.11.2.2\ModelData\SM_Data.json `
  --progress-file E:\Github\RSDWModel\0.11.2.2\BuildProgress_SM.json `
  --workers 8

python tools\ModelData\BuildGLB.py `
  --source-root E:\Github\RSDWModel\0.11.2.2 `
  --data-file E:\Github\RSDWModel\0.11.2.2\ModelData\SK_Data.json `
  --progress-file E:\Github\RSDWModel\0.11.2.2\BuildProgress_SK.json `
  --limit 1 `
  --workers 1
```

Useful flags:

- `--workers N` - parallel Blender processes.
- `--timeout-s N` - per-entry timeout.
- `--limit N` - cap selected entries.
- `--only SUBSTR` - substring filter by entry name or path.
- `--only-list PATH` - run specific inventory paths from a text file.
- `--force` - rebuild entries already marked `success`.
- `--no-blend` - deprecated; `.blend` output has been removed.
- `--dry-run` - list planned work and exit.

Progress manifests use schema `RSDWModel.BuildProgress.v1` and allow reruns to
resume from completed entries.

## Current 0.11.2.2 Status

The current local game install reports `ProjectVersion = "0.11.2.2"` from the
UE4SS header dump. The corresponding retoc cache and CUE4Parse export have been
created side-by-side with the old data set, not as an overwrite.

Latest smoke-test results:

- CUE4Parse export: `4,894` `.uemodel` files in `0.11.2.2/`.
- Inventory: `4,394` `SM_` entries and `500` `SK_` entries.
- Full web asset build: all `4,894` inventory entries successfully converted to
  shared web assets.
- Full size report: `4,894` `.gltf`, `4,894` `.bin`, `2,923` `.webp`, about
  `728.76 MB` total, with no generated files over `50 MiB`.
- Known texture caveat: five tiny `.hdr` helper/curve-atlas files are reported
  as texture conversion failures because Pillow cannot identify them as normal
  images.
