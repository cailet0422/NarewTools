"""``narew brick ...`` subcommands: inspect, verify, export and edit .iaf/.bwr files."""
from __future__ import annotations

import json
import os
import time
from collections import Counter

from .brick import (AIR, COLLISION, IAF_MAGIC, OBJ_PRODUCT, OBJ_TREE, PRODUCT_EVENTS,
                    SIDE, Architecture, BrickError, ProductTable, World, find_xml_dir)
from .voxel import (BrickPalette, Volume, iter_voxels, read_vox, render_side,
                    render_top, write_vox)

TREE_KINDS = {1: 'fruit', 2: 'pine', 3: 'oak', 4: 'jungle', 5: 'acacia', 6: 'cactus'}


def _xml_dir(a, *paths):
    d = getattr(a, 'xml', None) or find_xml_dir(*paths)
    return d if d and os.path.isdir(d) else None


def _tables(a, path):
    xd = _xml_dir(a, path)
    products = ProductTable.read(os.path.join(xd, 'product.xml')) if xd else None
    palette = BrickPalette.load(xd)
    if xd is None:
        print('note: XML folder (product.xml / brick_table.xml) not found; '
              'use --xml D:\\isletre\\extracted\\xml\\XML for names and object data')
    return products, palette


def _load(path, products):
    with open(path, 'rb') as f:
        head = f.read(4)
    if int.from_bytes(head, 'little') == IAF_MAGIC:
        return Architecture.read(path)
    return World.read(path, products)


def _histogram(src) -> Counter:
    c = Counter()
    cols = src.groups if isinstance(src, Architecture) else [r.column for r in src.regions]
    for col in cols:
        c.update(col.data)
    return c


def _object_dict(o, products):
    d = {'kind': 'tree' if o.kind == OBJ_TREE else 'product' if o.kind == OBJ_PRODUCT
         else o.kind, 'type_id': o.type_id, 'pos': [o.x, o.y, o.z], 'rot': o.rot,
         'state': o.state, 'age': o.age, 'hp': o.hp, 'owner': o.owner,
         'extra': o.extra.hex()}
    if o.kind == OBJ_TREE:
        d['tree'] = TREE_KINDS.get(o.type_id, '?')
        if len(o.extra) >= 3:
            d['level'] = o.extra[2]
    elif products:
        pt = products.get(o.type_id)
        if pt:
            d['name'] = pt.name
            d['event'] = PRODUCT_EVENTS[pt.event]
    return d


def cmd_info(a):
    products, palette = _tables(a, a.file)
    src = _load(a.file, products)
    if isinstance(src, Architecture):
        print(f'{os.path.basename(a.file)}: architecture (.iaf) {src.width} x {src.height} x '
              f'{src.depth} (x, y-up, z), {len(src.groups)} groups of 16x{src.height}x16')
    else:
        objs = list(src.iter_objects())
        raw = sum(1 for r in src.regions if r.objects is None)
        print(f'{os.path.basename(a.file)}: world (.bwr) {src.width} x 256 x {src.depth}, '
              f'{len(src.regions)} regions ({src.regions_x} x {src.regions_z})')
        kinds = Counter('tree' if o.kind == OBJ_TREE else 'product' for _, o in objs)
        print(f'  objects: {len(objs)} ({dict(kinds)})' +
              (f', {raw} regions kept raw (unknown object data)' if raw else ''))
        if products:
            ev = Counter(PRODUCT_EVENTS[products.get(o.type_id).event]
                         for _, o in objs if o.kind == OBJ_PRODUCT and products.get(o.type_id))
            if ev:
                print('  products by event: ' + ', '.join(f'{k}:{v}' for k, v in ev.most_common()))
        specials = sum(len(r.specials) for r in src.regions)
        print(f'  special (update) entries: {specials}')
        print(f'  tail: {json.dumps(src.tail.view(), ensure_ascii=False)}')
    hist = _histogram(src)
    total = sum(hist.values())
    solid = total - hist.get(AIR, 0)
    print(f'  bricks: {solid:,} non-air of {total:,}')
    for v, n in sorted(((v, n) for v, n in hist.items() if v != AIR),
                       key=lambda x: -x[1])[:a.top]:
        print(f'    {v:>3} {palette.name(v):<36} {n:>12,}')


def cmd_verify(a):
    paths = []
    for p in a.paths:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                paths += [os.path.join(dp, f) for f in sorted(fns)
                          if f.lower().endswith(('.iaf', '.bwr'))]
        else:
            paths.append(p)
    xd = getattr(a, 'xml', None) or (find_xml_dir(*paths) if paths else None)
    products = ProductTable.read(os.path.join(xd, 'product.xml')) if xd else None
    bad = 0
    for p in paths:
        t0 = time.time()
        try:
            with open(p, 'rb') as f:
                d = f.read()
            src = _load(p, products)
            cols = src.groups if isinstance(src, Architecture) else [r.column for r in src.regions]
            for c in cols:
                _ = c.data                             # inflate every brick stream
            same = src.to_bytes() == d
            note = ''
            if a.recompress:
                for c in cols:
                    c.touch()
                note = ', recompressed ' + ('identical' if src.to_bytes() == d else 'DIFFERENT')
            raw = 0 if isinstance(src, Architecture) else sum(
                1 for r in src.regions if r.objects is None)
            if raw:
                note += f', {raw} regions with raw object data'
            ok = same and 'DIFFERENT' not in note
            bad += not ok
            print(f'{"OK  " if ok else "FAIL"} {p}  ({"identical" if same else "DIFFERENT"}{note}, '
                  f'{time.time() - t0:.1f}s)')
        except (BrickError, OSError, ValueError) as ex:
            bad += 1
            print(f'FAIL {p}: {ex}')
    print('OK' if not bad else f'{bad} problem(s)')
    return 1 if bad else 0


def _parse_box(a, vol):
    x0, z0, x1, z1 = 0, 0, vol.sx, vol.sz
    y0, y1 = 0, vol.sy
    if a.box:
        x0, z0, x1, z1 = (int(v) for v in a.box.split(','))
    if a.y:
        y0, y1 = (int(v) for v in a.y.split(','))
    x0, z0, y0 = max(0, x0), max(0, z0), max(0, y0)
    x1, z1, y1 = min(vol.sx, x1), min(vol.sz, z1), min(vol.sy, y1)
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise BrickError('empty export box')
    return x0, y0, z0, x1, y1, z1


def cmd_export(a):
    products, palette = _tables(a, a.file)
    src = _load(a.file, products)
    vol = Volume(src)
    box = _parse_box(a, vol)
    ext = os.path.splitext(a.out)[1].lower()
    t0 = time.time()
    if ext == '.vox':
        surface = a.surface or (not a.full and box[3] - box[0] > 256 and box[5] - box[2] > 256)
        if surface and not a.surface:
            print('large area: exporting visible bricks only (pass --full to export everything)')
        info = write_vox(a.out, vol, palette, box, surface, not a.keep_collision)
        print(f'wrote {a.out}: {info["voxels"]:,} voxels in {info["models"]} model(s), '
              f'size {info["size"]} (vox x, y, z = game x, z, y)')
    elif ext == '.png':
        if a.view == 'top':
            w, h = render_top(vol, palette, a.out, box, not a.keep_collision)
        else:
            w, h = render_side(vol, palette, a.out, 'z' if a.view == 'front' else 'x',
                               not a.keep_collision)
        print(f'wrote {a.out}: {w} x {h} {a.view} view')
    elif ext == '.json':
        doc = {'file': os.path.basename(a.file)}
        if isinstance(src, Architecture):
            doc.update(format='iaf', size=[src.width, src.height, src.depth],
                       trailer=src.trailer.hex())
        else:
            doc.update(format='bwr', size=[src.width, 256, src.depth],
                       regions=[src.regions_x, src.regions_z], tail=src.tail.view(),
                       objects=[dict(_object_dict(o, products), region=[r.rx, r.rz])
                                for r, o in src.iter_objects()
                                if box[0] <= o.x < box[3] and box[2] <= o.z < box[5]])
        if a.bricks or isinstance(src, Architecture):
            doc['palette'] = {str(v): palette.name(v) for v in sorted(set(
                v for v in range(1, 256) if v in palette.bricks or v == COLLISION))}
            doc['bricks'] = [[x, y, z, v] for x, y, z, v in
                             iter_voxels(vol, box, a.surface, palette, not a.keep_collision)]
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, separators=(',', ':') if a.bricks else None,
                      indent=None if a.bricks else 1)
        print(f'wrote {a.out}')
    else:
        raise BrickError('output must end in .vox, .png or .json')
    print(f'done in {time.time() - t0:.1f}s')


def _vox_to_bricks(path, palette, match_colors):
    models, pal, _ = read_vox(path)
    if not models:
        raise BrickError('no models in .vox')
    if len(models) > 1:
        print(f'note: {len(models)} models found; merging them at the origin')
    remap = list(range(256))
    if match_colors and pal:
        cands = [(v, b.color) for v, b in palette.bricks.items() if palette.occludes(v)]
        for i in range(1, 256):
            r, g, b, _ = pal[i - 1]
            remap[i] = min(cands, key=lambda c: (c[1][0] - r) ** 2 + (c[1][1] - g) ** 2
                           + (c[1][2] - b) ** 2)[0]
    sx = max(m[0][0] for m in models)
    sy = max(m[0][1] for m in models)
    sz = max(m[0][2] for m in models)
    vox = [(x, y, z, remap[i]) for _, vs in models for x, y, z, i in vs]
    return (sx, sy, sz), vox


def cmd_import(a):
    palette = _tables(a, a.vox)[1] if a.match_colors else None
    (sx, sy, sz), vox = _vox_to_bricks(a.vox, palette, a.match_colors)
    w = -(-sx // SIDE) * SIDE
    d = -(-sy // SIDE) * SIDE
    arch = Architecture.new(w, sz, d)
    for x, y, z, v in vox:                     # vox (x, y, z) -> game (x, z, y)
        arch.set(x, z, y, v)
    arch.write(a.out)
    print(f'wrote {a.out}: {w} x {sz} x {d} (x, y-up, z), {len(vox):,} bricks')


def cmd_paste(a):
    products, _ = _tables(a, a.world)
    world = World.read(a.world, products)
    x0, y0, z0 = (int(v) for v in a.at.split(','))
    if a.prefab.lower().endswith('.vox'):
        palette = BrickPalette.load(_xml_dir(a, a.world)) if a.match_colors else None
        _, vox = _vox_to_bricks(a.prefab, palette, a.match_colors)
        cells = [(x, z, y, v) for x, y, z, v in vox]
        size = None
    else:
        arch = Architecture.read(a.prefab)
        size = arch.size
        cells = list(arch.iter_bricks())
        if a.clear:                            # also write the prefab's air
            cells = [(x, y, z, arch.get(x, y, z)) for z in range(arch.depth)
                     for y in range(arch.height) for x in range(arch.width)]
    n = skipped = 0
    for x, y, z, v in cells:
        wx, wy, wz = x0 + x, y0 + y, z0 + z
        if 0 <= wx < world.width and 0 <= wy < 256 and 0 <= wz < world.depth:
            world.set(wx, wy, wz, v)
            n += 1
        else:
            skipped += 1
    out = a.out or a.world
    if out == a.world and not os.path.exists(a.world + '.orig'):
        import shutil
        shutil.copy2(a.world, a.world + '.orig')
        print(f'backup: {a.world}.orig')
    world.write(out)
    print(f'wrote {out}: {n:,} bricks set' + (f', {skipped} outside the world' if skipped else '')
          + (f' (prefab {size[0]}x{size[1]}x{size[2]})' if size else ''))


def add_parser(sub):
    p = sub.add_parser('brick', help='.iaf / .bwr brick (voxel) files')
    bs = p.add_subparsers(dest='brick_cmd', required=True)

    def common(s):
        s.add_argument('--xml', help='extracted XML folder (product.xml, brick_table.xml); '
                                     'found automatically next to an extracted tree')

    s = bs.add_parser('info', help='summarise an .iaf or .bwr')
    s.add_argument('file')
    s.add_argument('--top', type=int, default=15, help='number of brick types to list')
    common(s)
    s.set_defaults(fn=cmd_info)

    s = bs.add_parser('verify', help='decode files and check the rebuild is byte-identical')
    s.add_argument('paths', nargs='+', help='files or folders')
    s.add_argument('--recompress', action='store_true',
                   help='also recompress every brick stream and compare')
    common(s)
    s.set_defaults(fn=cmd_verify)

    s = bs.add_parser('export', help='export to MagicaVoxel .vox, .json or a .png preview')
    s.add_argument('file')
    s.add_argument('-o', '--out', required=True, help='output .vox / .json / .png')
    s.add_argument('--box', help='x0,z0,x1,z1 area (world coordinates)')
    s.add_argument('--y', help='y0,y1 height range')
    s.add_argument('--surface', action='store_true', help='only bricks touching air')
    s.add_argument('--full', action='store_true', help='never switch to --surface automatically')
    s.add_argument('--keep-collision', action='store_true',
                   help='keep invisible collision bricks (255)')
    s.add_argument('--view', choices=('top', 'front', 'side'), default='top',
                   help='PNG view (front looks along +z, side along +x)')
    s.add_argument('--bricks', action='store_true', help='include brick list in .bwr JSON')
    common(s)
    s.set_defaults(fn=cmd_export)

    s = bs.add_parser('import', help='build an .iaf prefab from a MagicaVoxel .vox')
    s.add_argument('vox')
    s.add_argument('-o', '--out', required=True)
    s.add_argument('--match-colors', action='store_true',
                   help='map .vox colours to the nearest brick colour '
                        '(default: palette index = brick id)')
    common(s)
    s.set_defaults(fn=cmd_import)

    s = bs.add_parser('paste', help='place an .iaf/.vox structure into a .bwr world')
    s.add_argument('world')
    s.add_argument('prefab')
    s.add_argument('--at', required=True, help='x,y,z world position of the prefab corner')
    s.add_argument('-o', '--out', help='write a new .bwr (default: in place, keeps .orig)')
    s.add_argument('--clear', action='store_true', help='also copy the prefab\'s air bricks')
    s.add_argument('--match-colors', action='store_true')
    common(s)
    s.set_defaults(fn=cmd_paste)
