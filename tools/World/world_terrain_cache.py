"""Reuse terrain only when inputs, decoder code and generated payloads match."""
import hashlib
import json
from pathlib import Path
import shutil

from world_source import identity_hash, read_json, ref_id, write_json


def terrain_signature(args, plan):
    from BuildWorldRegion import source_fingerprint
    materials = {tile['material']: plan['material_parameters'][tile['material']] for tile in plan['terrain']}
    native = read_json(args.out_root/'source-data/native/Fingerprint.json')['entries']
    refs = set()
    for tile in plan['terrain']:
        refs.add(ref_id(tile['properties']['HeightmapTexture']))
        refs.update(ref_id(r) for r in tile['properties'].get('WeightmapTextures', []))
        refs.update(materials[tile['material']]['roles'].values())
    payload = {'region':plan['region'], 'terrain':plan['terrain'], 'materials':materials,
               'sources':{r:native.get(identity_hash(r)) for r in sorted(refs)}}
    code = source_fingerprint([Path(__file__).parent/p for p in ('world_terrain.py','world_geometry.py','world_texture.py')])
    return hashlib.sha256((code+json.dumps(payload,sort_keys=True)).encode()).hexdigest()


def cache_terrain(args, plan, tiles):
    # Cache untouched base images; roads are composited after this point.
    files = []
    for tile in tiles:
        path = args.out_root/tile['path']
        base = path.with_name(path.stem+'-base.png')
        shutil.copyfile(path.with_suffix('.png'), base)
        files.extend([path, path.with_suffix('.bin'), base])
        if tile.get('overview_path'):
            overview = args.out_root/tile['overview_path']
            files.extend([overview,overview.with_suffix('.bin')])
    files.extend(args.out_root/'source-data'/name for name in ('TerrainTiles.json','TerrainReport.json'))
    write_json(args.out_root/'source-data/TerrainCache.json',{'signature':terrain_signature(args,plan),
               'files':{p.relative_to(args.out_root).as_posix():p.stat().st_size for p in files}})


def restore_terrain(args, plan):
    path = args.out_root/'source-data/TerrainCache.json'
    if not args.resume or not path.is_file():
        return None
    cached = read_json(path)
    if cached['signature'] != terrain_signature(args,plan) or any(
            not (args.out_root/p).is_file() or (args.out_root/p).stat().st_size != size for p,size in cached['files'].items()):
        return None
    tiles = read_json(args.out_root/'source-data/TerrainTiles.json')
    report = read_json(args.out_root/'source-data/TerrainReport.json')
    for tile in tiles:
        path = args.out_root/tile['path']
        shutil.copyfile(path.with_name(path.stem+'-base.png'),path.with_suffix('.png'))
    print('Reusing verified terrain geometry and base-color bakes',flush=True)
    return tiles,report
