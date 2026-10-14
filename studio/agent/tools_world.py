"""World / prefab editing for agents: open, look (ASCII + pictures), edit, furniture, save, apply to my world."""
from __future__ import annotations

import os

import numpy as np

from narew import brick

from .. import savedworlds
from ..core import StudioError
from ..svc_world import TREE_NAMES
from ..worldedit import (AIR, COLLISION, EditSession, object_id, parse_object_id, rotate_array, rotate_point)
from . import live, render
from .context import Clip
from .registry import ARR, BOOL, BOX, INT, NUM, OBJ, POINT, Result, SESSION, STR, tool

AREA = ARR('Horizontal area [x0, z0, x1, z1] (inclusive, corners in any order).', {'type': 'integer'},
           minItems=4, maxItems=4)
POOL = '#$*+=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789?!<>()[]{}~:;,_|/'
ROT = INT('Turn in 90-degree steps 0-3 (the same turn as the editor\'s R key for furniture).', 0)
KEEP = dict(
    only_air=BOOL('Only fill cells that are air now (never overwrite blocks).', False),
    keep_collision=BOOL('Leave furniture collision cells (255) alone. Keep true unless you know why.', True),
)


# --------------------------------------------------------------------------- helpers

def _sess(ctx, sid) -> EditSession:
    return ctx.session(sid)


def _info(ctx, s: EditSession) -> dict:
    return render.brick_info(ctx, live=bool(s.doc.path))


def _box(s: EditSession, box) -> tuple:
    b = s.clip(*box)
    if b is None:
        raise StudioError(f'범위 {box}가 맵 밖입니다 (맵 크기 x 0..{s.W - 1}, y 0..{s.H - 1}, z 0..{s.D - 1})')
    return b


def _area(s: EditSession, area, y0=0, y1=None) -> tuple:
    x0, z0, x1, z1 = area
    return _box(s, (x0, y0, z0, x1, s.H - 1 if y1 is None else y1, z1))


def _brick_id(info, v: int, what='brick') -> int:
    if not 0 <= v <= 255:
        raise StudioError(f'{what}: 블록 번호는 0~255입니다')
    if v not in (AIR, COLLISION) and v not in info['by_id']:
        raise StudioError(f'{what}: 블록 #{v}은(는) 블록 표에 없습니다 (bricks_list로 찾으세요)')
    return v


def _edit(s: EditSession, label: str, fn):
    s.begin(label)
    try:
        extra = fn()
    except Exception:
        s.abort()
        raise
    r = s.end()
    r['undo_steps'] = len(s.undo_stack)
    if isinstance(extra, dict):
        r.update(extra)
    return r


def _obj_row(s: EditSession, ob) -> dict:
    key, kind, t, x, y, z, rot = ob
    row = {'id': object_id(key), 'kind': 'product' if kind == brick.OBJ_PRODUCT else 'tree', 'type': t,
           'pos': [x, y, z], 'rot': rot}
    if kind == brick.OBJ_PRODUCT:
        pt = s.doc.products.get(t)
        row['name'] = pt.name if pt else ''
        if brick.switchable(pt):
            row['on'] = bool(s.state_of(key) & brick.STATE_ON)
        cells = s.doc.footprint(t, rot)
        row['footprint_box'] = [x + min(c[0] for c in cells), y + min(c[1] for c in cells), z + min(c[2] for c in cells),
                                x + max(c[0] for c in cells), y + max(c[1] for c in cells), z + max(c[2] for c in cells)]
    else:
        row['name'] = TREE_NAMES.get(t, '나무')
    if key[0] == 'new':
        row['new'] = True
    return row


def _charmap(values: np.ndarray, info) -> tuple[dict, dict]:
    vals, counts = np.unique(values, return_counts=True)
    order = sorted(((int(v), int(c)) for v, c in zip(vals, counts) if v not in (AIR, COLLISION)), key=lambda t: -t[1])
    m = {AIR: '.', COLLISION: '%'}
    legend = {}
    for i, (v, c) in enumerate(order):
        m[v] = POOL[i] if i < len(POOL) else '?'
        legend[m[v]] = f'#{v} {render.brick_name(info, v)} ({c})'
    if (values == COLLISION).any():
        legend['%'] = 'collision filler under furniture (255)'
    legend['.'] = 'air'
    return m, legend


def _grid_text(rows: list[str], h0: int, v_values: list[int], hlabel: str, vlabel: str) -> str:
    w = len(rows[0]) if rows else 0
    head = f'{vlabel}/{hlabel}'
    pad = max(len(head) + 1, max(len(str(v)) for v in v_values) + 1 if v_values else 4)
    units = ''.join(str((h0 + i) % 10) for i in range(w))
    tens = [' '] * w
    for i in range(w):
        v = h0 + i
        if v % 10 == 0:
            for j, ch in enumerate(str(v)):
                if i + j < w:
                    tens[i + j] = ch
    out = [head.ljust(pad) + ''.join(tens), ' ' * pad + units]
    out += [str(v).rjust(pad - 1) + ' ' + r for v, r in zip(v_values, rows)]
    return '\n'.join(out)


def _doc_array(doc) -> np.ndarray:
    arr = np.zeros((doc.height, doc.depth, doc.width), dtype=np.uint8)
    for rz in range(doc.rz):
        for rx in range(doc.rx):
            arr[:, rz * 16:rz * 16 + 16, rx * 16:rx * 16 + 16] = doc.region_array(rx, rz)
    return arr


def summary(ctx, sid: str) -> dict:
    s = ctx.sessions[sid]
    d = s.doc
    meta = ctx.session_meta.get(sid, {})
    objs = list(s.live_objects())
    if d.path:
        g = ctx.st.game.root if ctx.st.game else None
        save_to = ('a new file (game saves are never overwritten); world_save picks '
                   f'{savedworlds.default_capture_dir()}\\<world>_edit.bwr or your path') \
            if savedworlds.is_game_save(g, d.path) else f'this file: {d.path}'
    else:
        save_to = f'the mod project (files\\{d.name})'
    out = {'session': sid, 'name': d.name, 'kind': 'world' if d.is_world else 'prefab', 'file': d.path,
           'source': meta.get('source'), 'size_xyz': [s.W, s.H, s.D],
           'start_pos': [round(v, 2) for v in s.start_pos] if s.start_pos else None,
           'objects': {'products': sum(1 for o in objs if o[1] == brick.OBJ_PRODUCT),
                       'trees': sum(1 for o in objs if o[1] == brick.OBJ_TREE)},
           'dirty': s.dirty(), 'undo': [op['label'] for op in s.undo_stack[-10:]], 'redo': len(s.redo_stack),
           'save_goes_to': save_to}
    lights = [bool(s.state_of(o[0]) & brick.STATE_ON) for o in objs
              if o[1] == brick.OBJ_PRODUCT and brick.switchable(d.products.get(o[2]))]
    if lights:
        out['objects']['lights'] = {'on': sum(lights), 'off': len(lights) - sum(lights)}
    if d.is_world:
        sp = s.start_pos
        if sp:
            x, z = int(sp[0]), int(sp[2])
            ty = s.top_y(x, z)
            g = {'top_block_y': ty, 'on_the_brick_y': ty + 1}
            lh = local_height(s, x, z, s.get(x, ty, z)) if ty >= 0 else None
            if lh:
                g['existing_objects_on_this_ground'] = f'{lh[1]} objects {lh[2]} stand at brick y + {lh[0]}'
            out['ground_at_start'] = g
    return out


# --------------------------------------------------------------------------- listing / opening

@tool('world_list', 'world',
      'Worlds and prefabs you can open: shipped maps (.bwr) and buildings (.iaf) from the packs (or this mod), '
      'the game\'s own world saves (Local\\, backup_map\\), captured/edited copies in Documents\\NarewStudio\\Worlds.',
      query=STR('Text in the name.', None), kind=STR('Only this kind.', None, enum=('world', 'prefab', 'save')))
def world_list(ctx, query, kind):
    q = (query or '').lower()
    out = {}
    if kind in (None, 'world', 'prefab'):
        for it in ctx.world.list():
            if (kind and it['type'] != kind) or (q and q not in it['name'].lower()):
                continue
            out.setdefault(it['type'] + 's', []).append(
                {'name': it['name'], 'size_xyz': list(it['size']) if it['size'] else None, 'status': it['status']})
    if kind in (None, 'save'):
        g = ctx.st.game.root if ctx.st.game else None
        rows = (savedworlds.list_saved(g) if g else []) + savedworlds.list_captured()
        out['saves'] = [{'path': r['path'], 'name': r['name'], 'where': r['label'], 'bytes': r['size'],
                         'mtime': r['mtime']} for r in rows if not q or q in r['name'].lower()]
    gui = live.gui_state()
    if gui and (gui.get('open') or {}).get('world'):
        out['open_in_window'] = gui['open']['world']
    return out


@tool('world_open', 'world',
      'Open a world/prefab for editing and get a session id. name = pack entry (e.g. "map\\64_0.bwr", '
      '"Architecture\\building\\Choga_big_A01.iaf"); path = a .bwr file (game save or copy). With neither, opens '
      'what the NarewStudio window shows. Opening the same thing again returns the same session.',
      name=STR('Pack/project entry of a .bwr or .iaf.', None), path=STR('A .bwr file on disk.', None))
def world_open(ctx, name, path):
    if not name and not path:
        gui = live.gui_state()
        w = (gui or {}).get('open', {}).get('world') if gui else None
        if not w:
            raise StudioError('name 또는 path를 주세요 (world_list로 찾기). 창에 열린 월드도 없습니다.')
        name, path = w.get('name'), w.get('path')
    g = ctx.st.game.root if ctx.st.game else None
    if path:
        doc = ctx.world.open_file(path)
        source = 'game save (read-only original)' if savedworlds.is_game_save(g, doc.path) else 'file'
    else:
        doc = ctx.world.open(name)
        source = f'pack ({ctx.st.status_of(doc.name)})'
    for sid, s in ctx.sessions.items():
        if s.doc is doc:
            return {**summary(ctx, sid), 'reused': True}
    sid = ctx.new_session_id()
    ctx.sessions[sid] = EditSession(ctx.world, doc, sid)
    ctx.session_meta[sid] = {'project': ctx.project_root(), 'source': source}
    return summary(ctx, sid)


@tool('world_new_prefab', 'world',
      'Create an empty prefab (.iaf building) in the mod project and open it. Width/depth multiples of 16.',
      writes=True, name=STR('Entry, e.g. "Architecture\\building\\my_house.iaf".'),
      width=INT('x size (multiple of 16).', 32), height=INT('y size.', 32), depth=INT('z size (multiple of 16).', 32))
def world_new_prefab(ctx, name, width, height, depth):
    n = ctx.world.new_prefab(name, width, height, depth)
    ctx.notify([n])
    return world_open(ctx, n, None)


@tool('world_info', 'world', 'Session summary: size, start position, object counts, unsaved edits, where save goes.',
      session=SESSION)
def world_info(ctx, session):
    _sess(ctx, session)
    return summary(ctx, session)


@tool('world_close', 'world', 'Forget a session (refuses unsaved edits unless discard=true).',
      session=SESSION, discard=BOOL('Drop unsaved edits.', False))
def world_close(ctx, session, discard):
    s = _sess(ctx, session)
    if s.dirty() and not discard:
        raise StudioError('저장하지 않은 편집이 있습니다. world_save 하거나 discard=true')
    del ctx.sessions[session]
    ctx.session_meta.pop(session, None)
    return {'closed': session}


# --------------------------------------------------------------------------- looking

@tool('world_layer', 'world',
      'ASCII map of one horizontal layer y (x to the right, z downward), with a legend char -> brick. '
      '"." air, "%" furniture collision, "&" furniture anchor, "^" tree, "@" start position. Max 200x200.',
      session=SESSION, y=INT('Layer height.'), area=AREA)
def world_layer(ctx, session, y, area):
    s = _sess(ctx, session)
    x0, y0, z0, x1, _, z1 = _area(s, area, y, y)
    if (x1 - x0 + 1) > 200 or (z1 - z0 + 1) > 200:
        raise StudioError('world_layer는 200×200칸까지입니다')
    info = _info(ctx, s)
    a = s.read_box(x0, y, z0, x1, y, z1)[0]
    m, legend = _charmap(a, info)
    lut = np.array([m.get(v, '?') for v in range(256)])
    rows = [list(r) for r in lut[a]]
    for key, kind, t, ox, oy, oz, rot in s.objects_in(x0, y, z0, x1, y, z1):
        rows[oz - z0][ox - x0] = '&' if kind == brick.OBJ_PRODUCT else '^'
        legend['&' if kind == brick.OBJ_PRODUCT else '^'] = 'furniture anchor' if kind == brick.OBJ_PRODUCT else 'tree'
    sp = s.start_pos
    if sp and int(sp[1]) - 1 <= y <= int(sp[1]) + 1 and x0 <= int(sp[0]) <= x1 and z0 <= int(sp[2]) <= z1:
        rows[int(sp[2]) - z0][int(sp[0]) - x0] = '@'
        legend['@'] = 'start position'
    text = _grid_text([''.join(r) for r in rows], x0, list(range(z0, z1 + 1)), 'x', 'z')
    return {'y': y, 'x': [x0, x1], 'z': [z0, z1], 'grid': text, 'legend': legend}


@tool('world_section', 'world',
      'ASCII vertical cut (side view): axis="z" cuts at z=at and shows x across, axis="x" cuts at x=at and shows '
      'z across; rows go from high y (top) to low y. Default y range = the top 48 layers of what is there.',
      session=SESSION, axis=STR('Which coordinate is fixed.', enum=('x', 'z')), at=INT('Its value.'),
      start=INT('First x (or z) shown.'), end=INT('Last x (or z) shown.'),
      y0=INT('Lowest y.', None), y1=INT('Highest y.', None))
def world_section(ctx, session, axis, at, start, end, y0, y1):
    s = _sess(ctx, session)
    a0, a1 = sorted((start, end))
    if a1 - a0 + 1 > 200:
        raise StudioError('world_section은 가로 200칸까지입니다')
    box = (a0, 0, at, a1, s.H - 1, at) if axis == 'z' else (at, 0, a0, at, s.H - 1, a1)
    bx = _box(s, box)
    arr = s.read_box(*bx)
    plane = arr[:, 0, :] if axis == 'z' else arr[:, :, 0]          # [y, across]
    if y0 is None or y1 is None:                 # default: the top 48 layers of what is there
        ys = np.nonzero((plane != 0).any(axis=1))[0]
        lo, hi = (int(ys[0]), int(ys[-1])) if len(ys) else (0, 15)
        if y1 is None:
            y1 = min(s.H - 1, hi + 2)
        if y0 is None:
            y0 = max(0, lo - 1, y1 - 47)
    y0, y1 = max(0, min(y0, y1)), min(s.H - 1, max(y0, y1))
    if y1 - y0 + 1 > 128:
        y0 = y1 - 127
    sub = plane[y0:y1 + 1]
    info = _info(ctx, s)
    m, legend = _charmap(sub, info)
    lut = np.array([m.get(v, '?') for v in range(256)])
    rows = [''.join(r) for r in lut[sub][::-1]]
    h0 = bx[0] if axis == 'z' else bx[2]
    text = _grid_text(rows, h0, list(range(y1, y0 - 1, -1)), 'x' if axis == 'z' else 'z', 'y')
    return {'cut': f'{axis}={at}', 'across': 'x' if axis == 'z' else 'z', 'y': [y0, y1], 'grid': text,
            'legend': legend}


@tool('world_heightmap', 'world',
      'Surface heights of an area: top solid brick y per column (rows = z, columns = x) and an ASCII map of the '
      'top bricks. "Place on the ground" = top y + 1. Max 128x128.',
      session=SESSION, area=AREA, below=INT('Ignore bricks above this y (e.g. look under a roof).', None))
def world_heightmap(ctx, session, area, below):
    s = _sess(ctx, session)
    x0, y0, z0, x1, y1, z1 = _area(s, area, 0, below)
    if (x1 - x0 + 1) > 128 or (z1 - z0 + 1) > 128:
        raise StudioError('world_heightmap은 128×128칸까지입니다')
    arr = s.read_box(x0, y0, z0, x1, y1, z1)
    solid = (arr != AIR) & (arr != COLLISION)
    has = solid.any(axis=0)
    top = arr.shape[0] - 1 - np.argmax(solid[::-1], axis=0)
    ids = np.where(has, np.take_along_axis(arr, top[None], 0)[0], 0)
    heights = np.where(has, top + y0, -1)
    info = _info(ctx, s)
    m, legend = _charmap(ids, info)
    lut = np.array([m.get(v, '?') for v in range(256)])
    text = _grid_text([''.join(r) for r in lut[ids]], x0, list(range(z0, z1 + 1)), 'x', 'z')
    hv = heights[has]
    w = len(str(max(int(heights.max()), z1))) if heights.size else 3
    rows = '\n'.join(f'z={z:<{w}} ' + ' '.join(str(int(v)).rjust(w) for v in r)
                     for z, r in zip(range(z0, z1 + 1), heights))
    return {'x': [x0, x1], 'z': [z0, z1], 'min_y': int(hv.min()) if len(hv) else None,
            'max_y': int(hv.max()) if len(hv) else None,
            'heights': rows, 'heights_note': f'one row per z; numbers = top y for x = {x0}, {x0 + 1}, ... ; -1 = empty',
            'top_bricks': text, 'legend': legend}


@tool('world_ground', 'world',
      'One column: the top solid brick, the y to place things on it, every floor (air above a solid brick, '
      'highest first - e.g. a roof and the room under it), and what is anchored there.',
      session=SESSION, x=INT('x.'), z=INT('z.'), below=INT('Ignore bricks above this y.', None))
def world_ground(ctx, session, x, z, below):
    s = _sess(ctx, session)
    ty = s.top_y(x, z, below)
    info = _info(ctx, s)
    v = s.get(x, ty, z) if ty >= 0 else 0
    col = s.read_box(*(_box(s, (x, 0, z, x, s.H - 1, z))))[:, 0, 0]
    layers = [{'y': int(y), 'brick': int(col[y]), 'name': render.brick_name(info, int(col[y]))}
              for y in np.nonzero(col)[0][-12:]]
    return {'x': x, 'z': z, 'top_y': ty, 'top_brick': {'id': v, 'name': render.brick_name(info, v)},
            'place_at_y': ty + 1, 'floors': floors(s, x, z)[:8], 'column_bricks_top12': layers[::-1],
            'objects_here': [_obj_row(s, o) for o in s.live_objects() if o[3] == x and o[5] == z]}


@tool('world_blocks', 'world', 'Every non-air brick in a box as [x, y, z, id] (max 4000) plus counts per id.',
      session=SESSION, box=BOX(), limit=INT('Max bricks listed.', 2000))
def world_blocks(ctx, session, box, limit):
    s = _sess(ctx, session)
    b = _box(s, box)
    arr = s.read_box(*b)
    yy, zz, xx = np.nonzero(arr)
    vals = arr[yy, zz, xx]
    info = _info(ctx, s)
    v, c = np.unique(vals, return_counts=True)
    lim = min(limit, 4000)
    return {'box': list(b), 'non_air': int(len(vals)),
            'counts': {f'#{int(a)} {render.brick_name(info, int(a))}': int(n) for a, n in zip(v, c)},
            'bricks': [[int(x + b[0]), int(y + b[1]), int(z + b[2]), int(t)] for x, y, z, t in
                       zip(xx[:lim], yy[:lim], zz[:lim], vals[:lim])],
            'truncated': len(vals) > lim}


@tool('world_stats', 'world',
      'What is in a box: brick counts by type, bounds of the non-air content, object counts. Default box: the whole '
      'prefab, or 64 bricks around the start position of a world.',
      session=SESSION, box=BOX(default=None))
def world_stats(ctx, session, box):
    s = _sess(ctx, session)
    if box is None:
        if s.doc.is_world and s.start_pos:
            x, z = int(s.start_pos[0]), int(s.start_pos[2])
            box = (x - 32, 0, z - 32, x + 32, s.H - 1, z + 32)
        else:
            box = (0, 0, 0, s.W - 1, s.H - 1, s.D - 1)
    b = _box(s, box)
    if (b[3] - b[0] + 1) * (b[5] - b[2] + 1) > 512 * 512:
        raise StudioError('world_stats는 512×512칸까지입니다')
    arr = s.read_box(*b)
    info = _info(ctx, s)
    nz = np.nonzero(arr)
    v, c = np.unique(arr[nz], return_counts=True)
    bounds = None
    if len(nz[0]):
        bounds = [int(nz[2].min() + b[0]), int(nz[0].min() + b[1]), int(nz[1].min() + b[2]),
                  int(nz[2].max() + b[0]), int(nz[0].max() + b[1]), int(nz[1].max() + b[2])]
    objs = s.objects_in(*b)
    return {'box': list(b), 'content_bounds': bounds,
            'bricks': sorted(({'id': int(a), 'name': render.brick_name(info, int(a)), 'count': int(n)}
                              for a, n in zip(v, c)), key=lambda r: -r['count']),
            'products': sum(1 for o in objs if o[1] == brick.OBJ_PRODUCT),
            'trees': sum(1 for o in objs if o[1] == brick.OBJ_TREE)}


@tool('world_find', 'world', 'Positions of given brick ids (in a box, or the whole map; max 500 listed).',
      session=SESSION, bricks=ARR('Brick ids.', {'type': 'integer'}), box=BOX(default=None),
      limit=INT('Max positions listed.', 200))
def world_find(ctx, session, bricks, box, limit):
    s = _sess(ctx, session)
    b = _box(s, box or (0, 0, 0, s.W - 1, s.H - 1, s.D - 1))
    want = np.asarray(bricks, dtype=np.uint8)
    found, total = [], 0
    lim = min(limit, 500)
    for rz in range(b[2] >> 4, (b[5] >> 4) + 1):
        for rx in range(b[0] >> 4, (b[3] >> 4) + 1):
            sub = s.read_box(*_box(s, (max(b[0], rx * 16), b[1], max(b[2], rz * 16),
                                       min(b[3], rx * 16 + 15), b[4], min(b[5], rz * 16 + 15))))
            yy, zz, xx = np.nonzero(np.isin(sub, want))
            total += len(yy)
            for y, z, x in zip(yy, zz, xx):
                if len(found) >= lim:
                    break
                found.append([int(x + max(b[0], rx * 16)), int(y + b[1]), int(z + max(b[2], rz * 16)),
                              int(sub[y, z, x])])
    return {'total': total, 'positions': found, 'truncated': total > len(found)}


@tool('world_objects', 'world',
      'Furniture (products) and trees: id (for world_delete_objects / world_switch / world_rotate), type, name, '
      'anchor position, '
      'rotation, footprint box, and on (true/false) for lamps and other switchable products. Filter by area/box, '
      'kind, product id.',
      session=SESSION, box=BOX(default=None), kind=STR('Only this kind.', None, enum=('product', 'tree')),
      product=INT('Only this product id.', None), limit=INT('Max rows.', 200))
def world_objects(ctx, session, box, kind, product, limit):
    s = _sess(ctx, session)
    b = _box(s, box) if box else (0, 0, 0, s.W - 1, s.H - 1, s.D - 1)
    rows = []
    for ob in s.objects_in(*b):
        if kind and (kind == 'product') != (ob[1] == brick.OBJ_PRODUCT):
            continue
        if product is not None and (ob[1] != brick.OBJ_PRODUCT or ob[2] != product):
            continue
        rows.append(ob)
    return {'count': len(rows), 'objects': [_obj_row(s, o) for o in rows[:limit]]}


@tool('world_render', 'world',
      'Picture of the world (unsaved edits included). view="top": map from above of an area, y_max cuts away '
      'everything higher (a floor plan; furniture = orange boxes, trees = green rings, start = red, marks = cyan X). '
      'view="iso": 3D view of a box (yaw/pitch degrees; furniture as orange boxes; arrows show +x/+z). '
      'view="map": the whole world from above (saved state only).',
      session=SESSION, view=STR('Kind of picture.', 'top', enum=('top', 'iso', 'map')),
      area=ARR('top: [x0, z0, x1, z1]. Default: 48 bricks around the start position / whole prefab.',
               {'type': 'integer'}, None, minItems=4, maxItems=4),
      box=BOX('iso: [x0, y0, z0, x1, y1, z1]. Default: the area with y 0..255.', None),
      y_min=INT('top: ignore bricks below.', 0), y_max=INT('top: ignore bricks above (floor plan).', None),
      yaw=NUM('iso: camera turn around the box in degrees (0 looks toward +z).', 35),
      pitch=NUM('iso: camera height angle in degrees (90 = straight down).', 35),
      context=INT('iso: also draw this many bricks around the box.', 0),
      marks=ARR('top: cells to mark, [[x, z], ...].', {'type': 'array'}, None),
      width=INT('iso: image width.', 960), height=INT('iso: image height.', 640))
def world_render(ctx, session, view, area, box, y_min, y_max, yaw, pitch, context, marks, width, height):
    s = _sess(ctx, session)
    info = _info(ctx, s)
    if area is None and box is None:
        if s.doc.is_world and s.start_pos:
            x, z = int(s.start_pos[0]), int(s.start_pos[2])
            area = [x - 24, z - 24, x + 24, z + 24]
        else:
            area = [0, 0, s.W - 1, s.D - 1]
    if view == 'map':
        p = ctx.world.topview(s.doc)
        return Result({'image': p, 'note': 'saved state of the whole map, 1 pixel = 1 brick, x right, z down'}, [p])
    if view == 'top':
        if area is None:
            area = [box[0], box[2], box[3], box[5]]
        img, legend, meta = render.top_view(s, info, area[0], area[1], area[2], area[3], y_min, y_max,
                                            marks=marks or ())
        p = render.save(img, ctx.out_path(f'{session}_top'))
        return Result({'image': p, **meta, 'legend_top_bricks': legend}, [p])
    if box is None:
        box = [area[0], 0, area[1], area[2], s.H - 1, area[3]]
    b = _box(s, box)
    img, meta = render.iso_view(ctx, s, info, b, yaw, pitch, (max(160, min(1600, width)), max(120, min(1200, height))),
                                context=max(0, context))
    p = render.save(img, ctx.out_path(f'{session}_iso'))
    return Result({'image': p, 'box': list(b), **meta}, [p])


# --------------------------------------------------------------------------- editing bricks

@tool('world_set_blocks', 'world', 'Set individual bricks: [[x, y, z, id], ...] (0 = air). One undo step.',
      session=SESSION, blocks=ARR('[[x, y, z, brick id], ...]', {'type': 'array'}), **KEEP)
def world_set_blocks(ctx, session, blocks, only_air, keep_collision):
    s = _sess(ctx, session)
    info = _info(ctx, s)
    for b in blocks:
        if len(b) != 4:
            raise StudioError(f'블록은 [x, y, z, id] 4개 값: {b}')
        _brick_id(info, int(b[3]))
    return _edit(s, f'set {len(blocks)} blocks', lambda: s.write_cells(blocks, only_air, None, keep_collision))


@tool('world_fill', 'world',
      'Fill a box with a brick. mode: solid | hollow (1-thick shell, inside emptied) | outline (shell, inside kept) | '
      'walls (4 vertical sides, no floor/roof) | frame (12 edges). replace: only change cells holding these ids.',
      session=SESSION, box=BOX(), brick=INT('Brick id (0 = air).'),
      mode=STR('Shape.', 'solid', enum=('solid', 'hollow', 'outline', 'walls', 'frame')),
      replace=ARR('Only overwrite these brick ids.', {'type': 'integer'}, None), **KEEP)
def world_fill(ctx, session, box, brick, mode, replace, only_air, keep_collision):
    s = _sess(ctx, session)
    v = _brick_id(_info(ctx, s), brick)
    b = _box(s, box)
    return _edit(s, f'fill {mode} #{v}', lambda: s.fill(*b, v, mode, only_air=only_air, replace=replace,
                                                        keep_collision=keep_collision))


@tool('world_clear', 'world',
      'Empty a box (air). By default furniture/trees anchored inside are deleted too (with their collision cells); '
      'collision of furniture standing outside the box is kept.',
      session=SESSION, box=BOX(), delete_objects=BOOL('Also delete objects anchored in the box.', True))
def world_clear(ctx, session, box, delete_objects):
    s = _sess(ctx, session)
    b = _box(s, box)

    def run():
        n = 0
        if delete_objects:
            for ob in list(s.objects_in(*b)):
                s.delete_object(ob[0])
                n += 1
        s.fill(*b, AIR, 'solid', keep_collision=True)
        return {'objects_deleted': n}
    return _edit(s, 'clear', run)


@tool('world_replace', 'world', 'Replace bricks of some ids with another id inside a box.',
      session=SESSION, box=BOX(), old=ARR('Brick ids to replace.', {'type': 'integer'}), new=INT('New brick id.'))
def world_replace(ctx, session, box, old, new):
    s = _sess(ctx, session)
    v = _brick_id(_info(ctx, s), new)
    b = _box(s, box)
    if COLLISION in old:
        raise StudioError('충돌 칸(255)은 바꾸지 않습니다')
    return _edit(s, f'replace {old} -> #{v}', lambda: s.fill(*b, v, 'solid', replace=old))


@tool('world_shape', 'world',
      'Draw a shape with a brick. sphere/dome: center at, radius. cylinder/cone/pyramid: base center at, radius, '
      'height (vertical). line: from at to "to". hollow=true draws only the surface (cylinder: walls only).',
      session=SESSION, shape=STR('Shape.', enum=('sphere', 'dome', 'cylinder', 'cone', 'pyramid', 'line')),
      at=POINT('Center (sphere/dome) or base center (cylinder/cone/pyramid) or line start [x, y, z].'),
      brick=INT('Brick id.'), radius=NUM('Radius in bricks.', 4), height=INT('Height (cylinder/cone/pyramid).', 6),
      to=POINT('line: end point.', None), hollow=BOOL('Surface only.', False), **KEEP)
def world_shape(ctx, session, shape, at, brick, radius, height, to, hollow, only_air, keep_collision):
    s = _sess(ctx, session)
    v = _brick_id(_info(ctx, s), brick)
    cx, cy, cz = (int(round(a)) for a in at)
    keep = dict(only_air=only_air, keep_collision=keep_collision)
    r = float(radius)
    if shape == 'line':
        if to is None:
            raise StudioError('line은 to가 필요합니다')
        p0, p1 = np.array([cx, cy, cz], float), np.array(to, float)
        n = int(np.abs(p1 - p0).max()) + 1
        pts = np.rint(np.linspace(p0, p1, n)).astype(int)
        return _edit(s, 'line', lambda: s.write_cells([(*p, v) for p in pts], **keep))
    R = int(np.ceil(r))
    if shape in ('sphere', 'dome'):
        y0 = cy if shape == 'dome' else cy - R
        y, z, x = np.ogrid[y0 - cy:R + 1, -R:R + 1, -R:R + 1]
        d = np.sqrt(x * x + y * y + z * z)
        mask = d <= r + 0.5
        if hollow:
            mask &= d > r - 0.5
        org = (cx - R, y0, cz - R)
    else:
        h = max(1, height)
        y, z, x = np.ogrid[0:h, -R:R + 1, -R:R + 1]
        if shape == 'cylinder':
            d = np.sqrt(x * x + z * z) + 0 * y
            mask = d <= r + 0.5
            if hollow:
                mask &= d > r - 0.5
        elif shape == 'cone':
            ry = r * (1 - y / h)
            d = np.sqrt(x * x + z * z)
            mask = d <= ry + 0.5
            if hollow:
                mask &= (d > ry - 0.5) | (y == 0)
        else:                                                   # stepped square pyramid
            ry = r - y * (r / h) if h > 1 else r + 0 * y
            m = np.maximum(np.abs(x), np.abs(z))
            mask = m <= np.rint(ry)
            if hollow:
                mask &= (m >= np.rint(ry)) | (y == 0)
        org = (cx - R, cy, cz - R)
    mask = np.ascontiguousarray(np.broadcast_to(mask, (mask.shape[0], 2 * R + 1, 2 * R + 1)))
    return _edit(s, shape, lambda: s.write_box(*org, v, mask, **keep))


@tool('world_build', 'world',
      'Build from an ASCII blueprint. layers[0] is the lowest layer at origin y; in each layer row j is z = '
      'origin z + j and character i is x = origin x + i. legend maps characters to brick ids (0 = air); '
      'a space leaves the cell unchanged. Example: {"origin": [100, 2, 100], "legend": {"#": 5, "G": 20, ".": 0}, '
      '"layers": [["#####", "#...#", "#####"], ["#G#G#", "#...#", "##.##"]]}.',
      session=SESSION, origin=POINT('Low corner [x, y, z].'),
      layers=ARR('Layers bottom-up; each a list of row strings.', {'type': 'array'}),
      legend=OBJ('Character -> brick id.'), rotate=ROT, **KEEP)
def world_build(ctx, session, origin, layers, legend, rotate, only_air, keep_collision):
    s = _sess(ctx, session)
    info = _info(ctx, s)
    lut = {}
    for ch, v in legend.items():
        if len(ch) != 1:
            raise StudioError(f'legend 키는 한 글자여야 합니다: {ch!r}')
        lut[ch] = _brick_id(info, int(v), f'legend {ch!r}')
    h = len(layers)
    d = max((len(L) for L in layers), default=0)
    w = max((len(r) for L in layers for r in L), default=0)
    if not h or not d or not w:
        raise StudioError('빈 설계도입니다')
    vals = np.zeros((h, d, w), np.uint8)
    mask = np.zeros((h, d, w), bool)
    for yi, L in enumerate(layers):
        if isinstance(L, str):
            L = L.split('\n')
        for zi, row in enumerate(L):
            for xi, ch in enumerate(row):
                if ch == ' ' and ' ' not in lut:
                    continue
                if ch not in lut:
                    raise StudioError(f'legend에 없는 글자 {ch!r} (층 {yi}, 줄 {zi}, 칸 {xi})')
                vals[yi, zi, xi] = lut[ch]
                mask[yi, zi, xi] = True
    vals, mask = rotate_array(vals, rotate), rotate_array(mask, rotate)
    ox, oy, oz = (int(round(a)) for a in origin)
    hh, dd, ww = vals.shape
    def run():
        s.write_box(ox, oy, oz, vals, mask, only_air=only_air, keep_collision=keep_collision)
        return {'size_xyz': [ww, hh, dd], 'box': [ox, oy, oz, ox + ww - 1, oy + hh - 1, oz + dd - 1]}
    return _edit(s, 'build', run)


# --------------------------------------------------------------------------- copy / paste / prefabs

@tool('world_copy', 'world',
      'Copy a box (bricks + furniture anchored inside) to a clipboard id for world_paste or world_save_prefab. '
      'Works across sessions (copy from a shipped map, paste into your world).',
      session=SESSION, box=BOX(), with_objects=BOOL('Copy furniture too.', True))
def world_copy(ctx, session, box, with_objects):
    s = _sess(ctx, session)
    b = _box(s, box)
    if (b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1) > 8_000_000:
        raise StudioError('복사할 수 있는 칸은 800만 개까지입니다. 높이(y) 범위를 줄이세요')
    arr = s.read_box(*b)
    arr[arr == COLLISION] = AIR            # collision filler belongs to furniture; pasting re-creates it
    prods = []
    if with_objects:
        for key, kind, t, x, y, z, rot in s.objects_in(*b):
            if kind == brick.OBJ_PRODUCT:
                prods.append((t, x - b[0], y - b[1], z - b[2], rot, s.state_of(key)))
    cid = ctx.new_clip(Clip(arr, prods, f'{s.doc.name} {list(b)}'))
    return {'clip': cid, 'size_xyz': ctx.clips[cid].size, 'products': len(prods),
            'non_air': int(np.count_nonzero(arr))}


@tool('world_paste', 'world',
      'Paste a clipboard (world_copy) or a prefab .iaf with its low corner at "at". rotate turns it about y; '
      'skip_air leaves cells where the source is air unchanged. Copied furniture is placed again, lamps on/off as '
      'copied (failures listed).',
      session=SESSION, at=POINT('Low corner [x, y, z] of the pasted block.'),
      clip=STR('Clipboard id from world_copy.', None), prefab=STR('Or a prefab entry (.iaf).', None),
      rotate=ROT, skip_air=BOOL('Do not copy air.', True), with_objects=BOOL('Place copied furniture.', True),
      **KEEP)
def world_paste(ctx, session, at, clip, prefab, rotate, skip_air, with_objects, only_air, keep_collision):
    s = _sess(ctx, session)
    if bool(clip) == bool(prefab):
        raise StudioError('clip과 prefab 중 하나만 주세요')
    if clip:
        c = ctx.clips.get(clip)
        if c is None:
            raise StudioError(f'없는 클립보드 {clip!r} (있는 것: {list(ctx.clips)})')
        arr, prods = c.arr, c.products if with_objects else []
    else:
        d = ctx.world.open(prefab)
        if d.is_world:
            raise StudioError('prefab에는 .iaf를 주세요 (월드 일부는 world_copy로)')
        arr, prods = _doc_array(d), []
    h, dep, w = arr.shape
    rarr = rotate_array(arr, rotate)
    mask = rarr != AIR if skip_air else np.ones(rarr.shape, bool)
    ox, oy, oz = (int(round(a)) for a in at)

    def run():
        s.write_box(ox, oy, oz, rarr, mask, only_air=only_air, keep_collision=keep_collision)
        placed, failed = [], []
        for t, dx, dy, dz, rot, state in prods:
            nx, nz = rotate_point(dx, dz, w, dep, rotate)
            pos = (ox + nx, oy + dy, oz + nz)
            try:
                key = s.place_product(t, *pos, (rot + rotate) & 3, state)
                placed.append(object_id(key))
            except StudioError as ex:
                failed.append({'product': t, 'pos': list(pos), 'why': str(ex)})
        rh, rd, rw = rarr.shape
        return {'box': [ox, oy, oz, ox + rw - 1, oy + rh - 1, oz + rd - 1], 'placed': placed, 'failed': failed}
    return _edit(s, 'paste', run)


@tool('world_save_prefab', 'world',
      'Save a box of a session (or a clipboard) as a prefab .iaf building in the mod project (x/z padded to '
      'multiples of 16). Prefabs hold bricks only.', writes=True,
      name=STR('Entry, e.g. "Architecture\\building\\my_house.iaf".'),
      session=STR('Session to copy from (with box).', None), box=BOX(default=None),
      clip=STR('Or a clipboard id.', None))
def world_save_prefab(ctx, name, session, box, clip):
    if clip:
        c = ctx.clips.get(clip)
        if c is None:
            raise StudioError(f'없는 클립보드 {clip!r}')
        arr = c.arr
    elif session and box:
        s = _sess(ctx, session)
        arr = s.read_box(*_box(s, box))
        arr[arr == COLLISION] = AIR
    else:
        raise StudioError('session+box 또는 clip을 주세요')
    nz = np.nonzero(arr)
    if not len(nz[0]):
        raise StudioError('빈 영역입니다')
    h, d, w = arr.shape
    W, D = -(-w // 16) * 16, -(-d // 16) * 16
    name = ctx.world.new_prefab(name, W, h, D)
    doc = ctx.world.open(name)
    full = np.zeros((h, D, W), np.uint8)
    full[:, :d, :w] = arr
    regions = {(rx, rz): np.ascontiguousarray(full[:, rz * 16:rz * 16 + 16, rx * 16:rx * 16 + 16])
               for rz in range(D // 16) for rx in range(W // 16)}
    n = ctx.world.save(doc, regions)
    ctx.notify([name])
    return {'name': name, 'size_xyz': [W, h, D], 'bytes': n}


# --------------------------------------------------------------------------- furniture

def floors(s: EditSession, x: int, z: int) -> list[int]:
    """Heights where something can stand in a column (air with a solid brick below), highest first."""
    if not (0 <= x < s.W and 0 <= z < s.D):
        return []
    col = s.peek(x >> 4, z >> 4)[:, z & 15, x & 15]
    solid = (col != AIR) & (col != COLLISION)
    return [int(y) for y in range(s.H - 1, 0, -1) if col[y] == AIR and solid[y - 1]]


def _refresh_products(ctx, s: EditSession, product: int):
    """Furniture created after the world was opened (content_new_furniture) is placeable at once."""
    if s.doc.products.get(product) is None:
        t = ctx.world.save_product_table() if s.doc.path else ctx.world.product_table()
        s.doc.products.types.update({k: v for k, v in t.types.items() if k not in s.doc.products.types})


def local_height(s: EditSession, x: int, z: int, support: int, radius: int = 48):
    """How high objects that were already in the file stand above the same kind of ground brick, near (x, z)
    or else anywhere in the world: (offset, count, 'nearby' | 'in this world') of the most common case, or None.
    Shipped maps mostly use 1 (right on the brick); the flat single-play world (#60 ground) has its
    game-placed objects 2 above."""
    from collections import Counter
    near, far = Counter(), Counter()
    for n, (rx, rz, j, kind, t, ox, oy, oz, rot) in enumerate(s.objects):
        if n > 4000:
            break
        if kind != brick.OBJ_PRODUCT or (rx, rz, j) in s.deleted:
            continue
        col = s.peek(ox >> 4, oz >> 4)[:, oz & 15, ox & 15]
        below = next((yy for yy in range(oy - 1, -1, -1) if col[yy] not in (AIR, COLLISION)), None)
        if below is not None and int(col[below]) == support:
            (near if abs(ox - x) <= radius and abs(oz - z) <= radius else far)[oy - below] += 1
    for c, scope in ((near, 'nearby'), (near + far, 'in this world')):
        if c:
            k, n = c.most_common(1)[0]
            return k, n, scope
    return None


def _place_one(s, product, x, z, y, rot, dry_run, on=True):
    other = []
    defaulted = y is None
    if y is None:
        fl = floors(s, x, z)
        if not fl:
            raise StudioError(f'({x}, {z})에 설 자리가 없습니다. y를 주세요')
        y, other = fl[0], fl[1:6]
    ok, why, cells = s.check_product(product, (x, y, z), rot)
    pt = s.doc.products.get(product)
    base = {'product': product, 'name': pt.name if pt else None, 'pos': [x, y, z], 'rot': rot & 3,
            'footprint_cells': [list(c) for c in cells]}
    if brick.switchable(pt):
        base['on'] = bool(on)
    notes = []
    if other:
        notes.append(f'y={y} is the highest floor in this column (e.g. a roof); lower floors: {other} - '
                     'pass y to place there')
    if defaulted and y > 0:
        support = s.get(x, y - 1, z)
        lh = local_height(s, x, z, support)
        if lh and lh[0] != 1 and lh[1] >= 2:
            notes.append(f'{lh[1]} objects already {lh[2]} stand {lh[0]} layers above this kind of ground brick '
                         f'(#{support}), i.e. y={y - 1 + lh[0]} here; y={y} puts it right on the brick. '
                         'Match the existing ones if unsure (pass y).')
    if notes:
        base['note'] = ' | '.join(notes)
    if dry_run or not ok:
        return {**base, 'ok': ok, 'why': why or None}
    key = s.place_product(product, x, y, z, rot, brick.placed_state(pt, on))
    return {**base, 'ok': True, 'id': object_id(key)}


ON = BOOL('Lamps and other switchable products (kind "on"): place it switched on (it shines in game). '
          'Ignored for other furniture.', True)


@tool('world_place', 'world',
      'Place a furniture product (worlds only): the object + its collision cells, exactly like the editor. '
      'y defaults to the highest floor of the column (inside a roofed building pass y = floor + 1; the result '
      'notes lower floors). Every footprint cell must be air. Lamps are placed switched on (on=false: off). '
      'Wall lamps hang on the wall at +z for rot 0, +x rot 1, -z rot 2, -x rot 3. '
      'dry_run=true only checks. Find ids/sizes with products_list.',
      session=SESSION, product=INT('Product id.'), x=INT('Anchor x.'), z=INT('Anchor z.'),
      y=INT('Anchor y (default: on the ground).', None), rot=ROT, on=ON, dry_run=BOOL('Only check.', False))
def world_place(ctx, session, product, x, z, y, rot, on, dry_run):
    s = _sess(ctx, session)
    _refresh_products(ctx, s, product)
    if dry_run:
        return _place_one(s, product, x, z, y, rot, True, on)
    out = {}
    r = _edit(s, f'place #{product}', lambda: out.update(_place_one(s, product, x, z, y, rot, False, on)))
    if not out.get('ok'):
        raise StudioError(f"놓을 수 없습니다: {out.get('why')} (칸 {out.get('footprint_cells')})")
    return {**out, 'undo_steps': r['undo_steps']}


@tool('world_place_many', 'world',
      'Place several products in one undo step: items = [{"product": id, "x": .., "z": .., "y"?: .., "rot"?: .., '
      '"on"?: true}] ("on" = lamp switched on, default true). '
      'Blocked ones are skipped and reported (stop_on_error=true aborts all instead).',
      session=SESSION, items=ARR('Placements.', {'type': 'object'}),
      stop_on_error=BOOL('Abort everything on the first failure.', False))
def world_place_many(ctx, session, items, stop_on_error):
    s = _sess(ctx, session)

    def run():
        done, failed = [], []
        for it in items:
            try:
                _refresh_products(ctx, s, int(it['product']))
                on = it.get('on', True)
                if not isinstance(on, bool):
                    raise TypeError('"on" must be true or false')
                r = _place_one(s, int(it['product']), int(it['x']), int(it['z']),
                               None if it.get('y') is None else int(it['y']), int(it.get('rot', 0)), False, on)
            except (KeyError, ValueError, TypeError) as ex:
                r = {'ok': False, 'product': it.get('product'), 'why': f'bad item {it}: {ex}'}
            except StudioError as ex:
                r = {'ok': False, 'product': it.get('product'), 'pos': [it.get('x'), it.get('y'), it.get('z')],
                     'why': str(ex)}
            if r.get('ok'):
                done.append(r)
            else:
                failed.append(r)
                if stop_on_error:
                    raise StudioError(f"놓을 수 없습니다: {r}")
        return {'placed': [{'id': r['id'], 'product': r['product'], 'pos': r['pos'],
                            **({'on': r['on']} if 'on' in r else {})} for r in done],
                'failed': [{'product': r.get('product'), 'pos': r.get('pos'), 'why': r.get('why')} for r in failed]}
    return _edit(s, f'place {len(items)}', run)


@tool('world_delete_objects', 'world',
      'Delete furniture/trees by id (from world_objects) or everything matching a box/kind/product filter. '
      'A product\'s collision cells that no other product needs are cleared.',
      session=SESSION, ids=ARR('Object ids, e.g. ["o:32:31:0", "n:3"].', {'type': 'string'}, None),
      box=BOX(default=None), kind=STR('With box: only this kind.', None, enum=('product', 'tree')),
      product=INT('With box: only this product id.', None))
def world_delete_objects(ctx, session, ids, box, kind, product):
    s = _sess(ctx, session)
    keys = [parse_object_id(i) for i in ids or []]
    if box:
        for ob in s.objects_in(*_box(s, box)):
            if kind and (kind == 'product') != (ob[1] == brick.OBJ_PRODUCT):
                continue
            if product is not None and ob[2] != product:
                continue
            keys.append(ob[0])
    if not keys:
        raise StudioError('지울 오브젝트가 없습니다 (ids 또는 box)')

    def run():
        gone = []
        for k in dict.fromkeys(keys):
            ob = s.delete_object(k)
            gone.append({'id': object_id(k), 'type': ob[2], 'pos': list(ob[3:6])})
        return {'deleted_objects': gone}
    return _edit(s, f'delete {len(keys)} objects', run)


@tool('world_switch', 'world',
      'Turn lamps (and other switchable products, kind "on") on or off: by id (from world_objects) or every one in '
      'a box (optionally only one product id). A lamp only shines in game when it is on (object state bit 0x10). '
      'Other furniture is skipped. One undo step.',
      session=SESSION, ids=ARR('Object ids, e.g. ["o:32:31:0", "n:3"].', {'type': 'string'}, None),
      box=BOX(default=None), product=INT('With box: only this product id.', None),
      on=BOOL('true = on, false = off.', True))
def world_switch(ctx, session, ids, box, product, on):
    s = _sess(ctx, session)
    keys = [parse_object_id(i) for i in ids or []]
    if box:                                      # in a box only the switchable ones (ids report the others)
        keys += [ob[0] for ob in s.objects_in(*_box(s, box)) if ob[1] == brick.OBJ_PRODUCT
                 and (product is None or ob[2] == product) and brick.switchable(s.doc.products.get(ob[2]))]
    if not ids and not box:
        raise StudioError('켜거나 끌 오브젝트를 ids 또는 box로 주세요')
    obs = []
    for k in dict.fromkeys(keys):
        ob = s.find_object(k)
        if ob is None:
            raise StudioError(f'없는 오브젝트입니다: {object_id(k)}')
        obs.append(ob)
    lamps = [ob for ob in obs if ob[1] == brick.OBJ_PRODUCT and brick.switchable(s.doc.products.get(ob[2]))]
    skipped = [{'id': object_id(ob[0]), 'type': ob[2], 'why': 'not switchable (kind is not "on")'}
               for ob in obs if ob not in lamps]
    if not lamps:
        raise StudioError('켜고 끌 수 있는 설치물(조명 등, 종류 "on")이 없습니다 (world_objects로 확인)')

    def run():
        switched, already = [], 0
        for ob in lamps:
            old = s.state_of(ob[0])
            if s.set_state(ob[0], old | brick.STATE_ON if on else old & ~brick.STATE_ON):
                switched.append({'id': object_id(ob[0]), 'type': ob[2], 'pos': list(ob[3:6])})
            else:
                already += 1
        return {'on': on, 'switched': switched, 'already': already, 'skipped': skipped}
    return _edit(s, f'switch {len(lamps)} {"on" if on else "off"}', run)


@tool('world_rotate', 'world',
      'Turn placed furniture about its anchor cell, like the game\'s R key: by id (from world_objects) or every '
      'product in a box (optionally only one product id). rot = the new rotation 0-3, or turn = 90-degree steps '
      'from the current one (default 1). The collision cells move with it; the turned footprint must be free '
      '(blocked ones are listed in "failed"). The object itself stays (a container keeps its items, a lamp its '
      'on/off). Wall lamps hang on the wall at +z for rot 0, +x rot 1, -z rot 2, -x rot 3. One undo step.',
      session=SESSION, ids=ARR('Object ids, e.g. ["o:32:31:0", "n:3"].', {'type': 'string'}, None),
      box=BOX(default=None), product=INT('With box: only this product id.', None),
      rot=INT('New rotation 0-3 (instead of turn).', None),
      turn=INT('90-degree steps from the current rotation (used when rot is not given).', 1))
def world_rotate(ctx, session, ids, box, product, rot, turn):
    s = _sess(ctx, session)
    if rot is not None and not 0 <= rot <= 3:
        raise StudioError('rot은 0~3입니다 (90° 단위)')
    keys = [parse_object_id(i) for i in ids or []]
    if box:                                      # in a box only products (ids report trees)
        keys += [ob[0] for ob in s.objects_in(*_box(s, box)) if ob[1] == brick.OBJ_PRODUCT
                 and (product is None or ob[2] == product)]
    if not ids and not box:
        raise StudioError('돌릴 설치물을 ids 또는 box로 주세요')
    obs = []
    for k in dict.fromkeys(keys):
        ob = s.find_object(k)
        if ob is None:
            raise StudioError(f'없는 오브젝트입니다: {object_id(k)}')
        obs.append(ob)
    if not obs:
        raise StudioError('그 범위에 돌릴 설치물이 없습니다 (world_objects로 확인)')

    def run():
        turned, failed, already = [], [], 0
        for ob in obs:
            new = rot if rot is not None else (ob[6] + turn) & 3
            row = {'id': object_id(ob[0]), 'type': ob[2], 'pos': list(ob[3:6]), 'rot': new}
            if ob[1] != brick.OBJ_PRODUCT:
                failed.append({**row, 'why': 'a tree, not a product'})
                continue
            try:
                if s.rotate_object(ob[0], new):
                    turned.append({**row, 'from': ob[6]})
                else:
                    already += 1
            except StudioError as ex:
                failed.append({**row, 'why': str(ex)})
        return {'turned': turned, 'already': already, 'failed': failed}
    return _edit(s, f'rotate {len(obs)}', run)


@tool('world_set_start', 'world', 'Move the spawn point (worlds). y defaults to standing on the ground there.',
      session=SESSION, x=NUM('x (brick centre = x + 0.5).'), z=NUM('z.'), y=NUM('Feet height.', None))
def world_set_start(ctx, session, x, z, y):
    s = _sess(ctx, session)
    if y is None:
        ty = s.top_y(int(x), int(z))
        if ty < 0:
            raise StudioError('그 자리에 바닥이 없습니다. y를 주세요')
        y = ty + 1.1
    if float(x).is_integer():
        x += 0.5
    if float(z).is_integer():
        z += 0.5
    r = _edit(s, 'start position', lambda: s.set_start(x, y, z))
    return {**r, 'start_pos': [x, y, z]}


# --------------------------------------------------------------------------- history / saving

@tool('world_undo', 'world', 'Undo the last edit(s) of a session.', session=SESSION, steps=INT('How many.', 1))
def world_undo(ctx, session, steps):
    s = _sess(ctx, session)
    return {'undone': s.undo(steps), 'undo_left': len(s.undo_stack), 'redo': len(s.redo_stack)}


@tool('world_redo', 'world', 'Redo undone edit(s).', session=SESSION, steps=INT('How many.', 1))
def world_redo(ctx, session, steps):
    s = _sess(ctx, session)
    return {'redone': s.redo(steps), 'undo': len(s.undo_stack), 'redo_left': len(s.redo_stack)}


def _edit_path(src: str) -> str:
    stem = os.path.splitext(os.path.basename(src))[0]
    d = savedworlds.default_capture_dir()
    p = os.path.join(d, f'{stem}_edit.bwr')
    n = 2
    while os.path.exists(p):
        p = os.path.join(d, f'{stem}_edit{n}.bwr')
        n += 1
    return p


@tool('world_save', 'world',
      'Save a session: pack worlds/prefabs go into the mod project; a game save is written to a NEW .bwr '
      '(default Documents\\NarewStudio\\Worlds\\<world>_edit.bwr; originals are never overwritten); other files '
      'are saved in place unless path is given. A running NarewStudio window reloads it.', writes=True,
      session=SESSION, path=STR('Write to this .bwr instead (worlds only).', None))
def world_save(ctx, session, path):
    s = _sess(ctx, session)
    d = s.doc
    meta = ctx.session_meta.get(session, {})
    if not d.path and not path:
        ctx.st.need_project()
        if meta.get('project') and os.path.normcase(meta['project']) != os.path.normcase(ctx.project_root() or ''):
            raise StudioError('이 월드를 연 뒤 모드 프로젝트가 바뀌었습니다. 원래 프로젝트를 다시 열거나 path로 저장하세요.')
    g = ctx.st.game.root if ctx.st.game else None
    if not path and d.path and savedworlds.is_game_save(g, d.path):
        path = _edit_path(d.path)
    old = d.path or d.name
    n = s.save(path)
    ctx.notify([d.path or d.name] + ([old] if old != (d.path or d.name) else []))
    return {'saved': d.path or d.name, 'bytes': n, 'into': 'file' if d.path else 'mod project',
            'next': 'world_apply_to_game puts a saved copy of your own world into the game (asks first)'
            if d.path and d.is_world else None}


# --------------------------------------------------------------------------- the game's own worlds

@tool('world_saves', 'world',
      'The game\'s world saves (Local\\ = current world, backup_map\\), captured/edited copies, and backups made '
      'before "apply to my world".')
def world_saves(ctx):
    g = ctx.st.need_game().root
    return {'game_saves': savedworlds.list_saved(g), 'copies': savedworlds.list_captured(),
            'backups': savedworlds.list_backups(), 'game_running': savedworlds.game_running()}


@tool('world_capture', 'world',
      'Copy the world the game saved last (Local\\<world>.bwr + .lnd/.npc) to Documents\\NarewStudio\\Worlds. '
      'Read-only for the game.')
def world_capture(ctx):
    return savedworlds.capture(ctx.st.need_game().root)


@tool('world_apply_to_game', 'world',
      'Put an edited copy of the user\'s own world into the game (replaces Local\\<world>.bwr after backing up '
      '.bwr/.lnd/.npc/.db; the game must be closed; sizes must match). Without confirm=true it only reports what '
      'would happen. Ask the user before confirming.', game=True,
      path=STR('The edited .bwr (default: the file of session).', None), session=STR('Session whose file to use.', None),
      world=STR('Which Local world, e.g. "lj.bwr" (default: guessed from the file name).', None),
      confirm=BOOL('Really do it.', False))
def world_apply_to_game(ctx, path, session, world, confirm):
    g = ctx.st.need_game().root
    if not path:
        if not session:
            raise StudioError('path 또는 session을 주세요')
        s = _sess(ctx, session)
        if s.dirty():
            raise StudioError('저장하지 않은 편집이 있습니다. 먼저 world_save')
        path = s.doc.path
        if not path:
            raise StudioError('이 세션은 팩 월드입니다. 내 월드 저장본(world_list kind=save)을 열어 고친 것만 적용합니다.')
    if savedworlds.is_game_save(g, path):
        raise StudioError('게임 저장본 자체입니다. 고친 사본(world_save가 만든 파일)을 주세요.')
    if not world:
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        names = [w['name'] for w in savedworlds.local_worlds(g)]
        guess = [n for n in names if stem.startswith(os.path.splitext(n)[0].lower())]
        world = guess[0] if guess else (names[0] if len(names) == 1 else None)
        if not world:
            raise StudioError(f'어느 월드에 넣을지 world로 정하세요: {names}')
    plan = savedworlds.check_apply(g, path, world)
    if not confirm:
        return {'dry_run': True, **plan, 'game_running': savedworlds.game_running(),
                'backup_to': savedworlds.backup_root(),
                'warning': 'the game saved after this file: applying drops that progress (kept in the backup)'
                if plan['save_newer'] else None,
                'next': 'ask the user, then call again with confirm=true'}
    r = savedworlds.apply_to_game(g, path, world)
    return {'applied': True, **r}


@tool('world_restore_backup', 'world',
      'Put a world backup (world_saves -> backups[].dir) back into the game\'s Local\\ (current save backed up '
      'first; game must be closed). Ask the user first.', game=True,
      backup=STR('Backup folder.'), confirm=BOOL('Really do it.', False))
def world_restore_backup(ctx, backup, confirm):
    g = ctx.st.need_game().root
    if not os.path.isfile(os.path.join(backup, 'backup.json')):
        raise StudioError('백업 폴더가 아닙니다 (backup.json 없음)')
    if not confirm:
        return {'dry_run': True, 'backup': backup, 'files': sorted(os.listdir(backup)),
                'next': 'ask the user, then call again with confirm=true'}
    return savedworlds.restore_backup(g, backup)
