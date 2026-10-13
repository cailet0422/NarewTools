"""narew - command line tool for Islet Online / NarewEngine2 .pack archives."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time

from .brick import BrickError
from .brick_cli import add_parser as add_brick_parser
from .gltf import GltfError
from .pack import (PackError, PackReader, PackWriter, iter_files, name_key,
                   write_atomic)

MANIFEST_SUFFIX = '.narew.json'


def _packs(target: str) -> list[str]:
    if os.path.isdir(target):
        return sorted(os.path.join(target, f) for f in os.listdir(target)
                      if f.lower().endswith('.pack'))
    return [target]


def _human(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024


def cmd_info(a):
    for path in _packs(a.pack):
        with PackReader(path) as r:
            t = r.trailer
            raw = sum(e.raw_size for e in r)
            comp = sum(1 for e in r if e.compressed)
            exts: dict[str, int] = {}
            for e in r:
                ext = os.path.splitext(e.name)[1].lower() or '(none)'
                exts[ext] = exts.get(ext, 0) + 1
            top = ', '.join(f'{k}:{v}' for k, v in sorted(exts.items(), key=lambda x: -x[1])[:8])
            print(f'{os.path.basename(path)}: {t.count} files, {_human(t.file_size)} on disk, '
                  f'{_human(raw)} unpacked, {comp} zlib / {t.count - comp} stored  [{top}]')


def cmd_list(a):
    with PackReader(a.pack) as r:
        for e in r:
            if a.filter and not fnmatch.fnmatch(name_key(e.name), name_key(a.filter)):
                continue
            kind = 'zlib' if e.compressed else 'raw '
            print(f'{e.raw_size:>11} {kind} a{e.attribute} {e.md5.hex()} {e.name}')


def _safe_join(root: str, name: str) -> str:
    parts = [p for p in name.split('\\') if p not in ('', '.')]
    if any(p == '..' or ':' in p for p in parts):
        raise PackError(f'refusing unsafe entry path: {name!r}')
    return os.path.join(root, *parts)


def cmd_extract(a):
    total_files = total_bytes = 0
    t0 = time.time()
    for path in _packs(a.pack):
        stem = os.path.splitext(os.path.basename(path))[0]
        dest = os.path.join(a.out, stem)
        with PackReader(path) as r:
            manifest = {'pack': os.path.basename(path), 'entries': []}
            for e in r:
                if a.filter and not fnmatch.fnmatch(name_key(e.name), name_key(a.filter)):
                    continue
                data = r.read(e, verify=a.verify)
                fp = _safe_join(dest, e.name)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, 'wb') as f:
                    f.write(data)
                manifest['entries'].append({'name': e.name, 'attribute': e.attribute,
                                            'md5': e.md5.hex()})
                total_files += 1
                total_bytes += len(data)
            if not a.filter:
                with open(os.path.join(a.out, stem + MANIFEST_SUFFIX), 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, indent=1)
        print(f'{os.path.basename(path)} -> {dest} ({len(r)} entries)')
    print(f'extracted {total_files} files, {_human(total_bytes)} in {time.time() - t0:.1f}s')


def cmd_verify(a):
    bad = 0
    for path in _packs(a.pack):
        n = 0
        with PackReader(path) as r:
            for e in r:
                try:
                    r.read(e, verify=True)
                except Exception as ex:  # report and continue
                    bad += 1
                    print(f'  FAIL {e.name}: {ex}')
                n += 1
            keys = {name_key(e.name) for e in r}
            if len(keys) != n:
                bad += 1
                print('  FAIL duplicate names (engine rejects such a pack)')
        print(f'{os.path.basename(path)}: {n} entries checked')
    print('OK' if not bad else f'{bad} problem(s)')
    return 1 if bad else 0


def cmd_build(a):
    folder = a.folder.rstrip('\\/')
    manifest_path = a.manifest or folder + MANIFEST_SUFFIX
    files = dict()
    for name, full in iter_files(folder):
        files[name_key(name)] = (name, full)
    w = PackWriter()
    used = set()
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        missing = 0
        for m in manifest['entries']:
            k = name_key(m['name'])
            if k not in files:
                missing += 1
                continue
            name, full = files[k]
            with open(full, 'rb') as f:
                w.add(m['name'], f.read(), m.get('attribute', 0))
            used.add(k)
        if missing:
            print(f'note: {missing} manifest entries have no file and were dropped')
    added = 0
    for k, (name, full) in sorted(files.items()):
        if k in used:
            continue
        with open(full, 'rb') as f:
            w.add(name, f.read(), 0)
        added += 1
    t = write_atomic(a.out, w)
    print(f'wrote {a.out}: {t.count} entries ({added} new), {_human(t.file_size)}')


def cmd_patch(a):
    src = a.pack
    out = a.out or src
    overlay = [(n, p) for n, p in iter_files(a.files)]
    if not overlay:
        print('nothing to patch: overlay folder is empty')
        return 1
    with PackReader(src) as r:
        w = PackWriter()
        for e in r:
            w.copy_from(r, e)
        replaced = added = 0
        for name, full in overlay:
            existing = r.find(name)
            with open(full, 'rb') as f:
                data = f.read()
            if existing is not None:
                w.add(existing.name, data, existing.attribute)
                replaced += 1
            else:
                w.add(name, data, 0)
                added += 1
            print(f'  {"replace" if existing else "add    "} {name}')
    # every chunk is now in memory, so the source may be replaced
    if out == src:
        bak = src + '.orig'
        if not os.path.exists(bak):
            shutil.copy2(src, bak)
            print(f'backup: {bak}')
    t = write_atomic(out, w)
    print(f'wrote {out}: {replaced} replaced, {added} added, {t.count} entries total')


def cmd_hash(a):
    """Print the MD5 of a file the way the pack directory stores it."""
    with open(a.file, 'rb') as f:
        print(hashlib.md5(f.read()).hexdigest())


def cmd_model2gltf(a):
    from .convert import export_gltf
    inputs = list(a.inputs)
    anims = list(a.anim or [])
    for d in a.anim_dir or []:
        anims += sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith('.nwat'))
    out = a.out or os.path.splitext(inputs[0])[0] + '.glb'
    embed = None if a.embed is None else a.embed == 'yes'
    s = export_gltf(inputs, out, skeleton=a.skeleton, anims=anims, embed_textures=embed,
                    fps=a.fps, engine_timing=a.engine_timing, helpers=not a.no_helpers)
    print(f'wrote {out}: ' + ', '.join(f'{v} {k}' for k, v in s.items()))


def cmd_gltf2model(a):
    from .convert import import_model
    out = a.out or os.path.splitext(a.gltf)[0] + '.nwmt'
    for p in import_model(a.gltf, out, template=a.template, skeleton=a.skeleton, model=a.model,
                          write_textures=not a.no_textures, force=a.force):
        print(f'wrote {p}')


def cmd_gltf2anim(a):
    from .convert import import_animation
    out = a.out or os.path.splitext(a.gltf)[0] + '.nwat'
    for p in import_animation(a.gltf, out, a.skeleton, animation=a.animation, fps=a.fps,
                              template=a.template):
        print(f'wrote {p}')


def main(argv=None):
    p = argparse.ArgumentParser(prog='narew', description='Islet Online / NarewEngine2 modding tool')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('info', help='summarise a pack or every pack in a folder')
    s.add_argument('pack')
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser('list', help='list entries')
    s.add_argument('pack')
    s.add_argument('-f', '--filter', help="glob on entry names, e.g. 'gui\\*.xml'")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser('extract', help='extract a pack (or a folder of packs)')
    s.add_argument('pack')
    s.add_argument('-o', '--out', required=True)
    s.add_argument('-f', '--filter')
    s.add_argument('--verify', action='store_true', help='check MD5 of every file')
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser('verify', help='decompress every entry and check sizes + MD5')
    s.add_argument('pack')
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser('build', help='build a pack from an extracted folder')
    s.add_argument('folder', help='e.g. out/xml (uses out/xml.narew.json for order if present)')
    s.add_argument('-o', '--out', required=True)
    s.add_argument('-m', '--manifest')
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser('patch', help='replace/add files in an existing pack from an overlay folder')
    s.add_argument('pack')
    s.add_argument('files', help='folder whose relative paths match entry names')
    s.add_argument('-o', '--out', help='write a new pack instead of patching in place '
                                       '(in-place keeps <pack>.orig as backup)')
    s.set_defaults(fn=cmd_patch)

    s = sub.add_parser('hash', help='MD5 of a file')
    s.add_argument('file')
    s.set_defaults(fn=cmd_hash)

    s = sub.add_parser('model2gltf', help='.nwmt/.nwbt/.nwat -> glTF 2.0 (.glb or .gltf)')
    s.add_argument('inputs', nargs='+', help='.nwmt models (several = one character), optional .nwbt/.nwat')
    s.add_argument('-o', '--out', help='output .glb (default) or .gltf; default: <first input>.glb')
    s.add_argument('-s', '--skeleton', help='.nwbt for skinned meshes (default: auto-detect)')
    s.add_argument('-a', '--anim', action='append', help='.nwat to include (repeatable)')
    s.add_argument('--anim-dir', action='append', help='include every .nwat in this folder')
    s.add_argument('--fps', type=float, help='seconds per key = 1/fps (default: header framerate)')
    s.add_argument('--engine-timing', action='store_true',
                   help='time keys like the engine plays them (framerate value = ms per key)')
    s.add_argument('--embed', choices=('yes', 'no'), help='embed textures (default: yes for .glb)')
    s.add_argument('--no-helpers', action='store_true', help='do not export helper/dummy nodes')
    s.set_defaults(fn=cmd_model2gltf)

    s = sub.add_parser('gltf2model', help='glTF/GLB -> .nwmt (meshes, skinned meshes, materials)')
    s.add_argument('gltf')
    s.add_argument('-o', '--out', help='.nwmt, or a folder when the glTF holds several models')
    s.add_argument('-t', '--template', help='original .nwmt: copy header/physics/helpers/... from it')
    s.add_argument('-s', '--skeleton', help='.nwbt used to map joint names to bone ids')
    s.add_argument('-m', '--model', help='only this model group (node name or original file name)')
    s.add_argument('--no-textures', action='store_true', help='do not write embedded textures')
    s.add_argument('--force', action='store_true', help='overwrite differing texture files')
    s.set_defaults(fn=cmd_gltf2model)

    s = sub.add_parser('gltf2anim', help='glTF/GLB animation -> .nwat (sampled per frame)')
    s.add_argument('gltf')
    s.add_argument('-s', '--skeleton', help='.nwbt the animation is for (default: bone ids '
                                            'stored by model2gltf)')
    s.add_argument('-o', '--out', help='.nwat, or a folder when exporting several animations')
    s.add_argument('-n', '--animation', help='only the animation with this name')
    s.add_argument('--fps', type=float, help='sampling rate (default: original framerate or 30)')
    s.add_argument('-t', '--template', help='original .nwat: keep its framerate, tags, sounds, '
                                            'effects and mesh tracks (DCC tools drop them)')
    s.set_defaults(fn=cmd_gltf2anim)

    add_brick_parser(sub)

    a = p.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (PackError, BrickError, GltfError, OSError) as ex:
        print(f'error: {ex}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
