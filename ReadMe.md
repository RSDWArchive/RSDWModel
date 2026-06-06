# RSDWModel

RSDWModel is a searchable model viewer and asset pipeline for
RuneScape: Dragonwilds. It publishes versioned model data as web-ready glTF
assets and serves them through a static viewer at
[rsdwmodel.com](https://rsdwmodel.com/).

## What This Repo Provides

- A static model browser with search and interactive 3D viewing.
- Versioned exported model data for the current dataset, `0.11.2.2`.
- Optimized web assets: separate `.gltf` / `.bin` models and shared 1024px WebP
  textures.
- CLI tooling used by maintainers to refresh the model data when the game
  updates.

## Website

The viewer lives in `website/` and deploys through GitHub Pages to
[rsdwmodel.com](https://rsdwmodel.com/). The Pages artifact contains the static
site and lightweight search index; model payloads load from this repository's
versioned `WebAssets/` folder through raw GitHub URLs.

## Current Dataset

- Game data version: `0.11.2.2`
- Model inventory: `4,394` static meshes and `500` skeletal meshes
- Web output: `4,894` glTF/bin pairs and `3,483` shared WebP textures
- Latest generated asset corpus: about `784 MiB`, with no generated file over
  `50 MiB`

## Repository Layout

- `0.11.2.2/` - current exported model data, inventories, manifests, and web
  assets.
- `website/` - static GitHub Pages viewer.
- `tools/` - extraction, inventory, web asset, icon, and publishing pipeline
  tools.
- `blender-5.0.0-windows-x64/` - bundled Blender runtime used by the asset
  build workers.
- `docs/` - maintainer-facing development and pipeline notes.

## Maintainers

Detailed development, pipeline, deployment, and commit batching notes live in
[docs/Development.md](docs/Development.md).

Common entry points:

```powershell
python tools\UpdateGameData.py --dry-run
python tools\UpdateGameData.py --web-assets full --web-asset-workers 8 --glb none
python tools\UpdateGameData.py --web-assets full --web-asset-workers 8 --glb none --git-commit-batches --git-push-each
```

This is a fan-made tooling and data project and is not affiliated with Jagex.
