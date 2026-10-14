"""Headless editing of a .bwr world or .iaf prefab with the same rules as the 3D world editor.

The AI-agent tools (studio.agent) edit maps through EditSession; ui/worldview.py shares the
furniture rules below so a product placed by an agent and one placed with the mouse are identical:

* product_check     - can a product go at a cell: anchor inside a decoded region, every footprint
                      cell inside and empty (air), no other object anchored there
* collision_to_clear - collision bricks (255) a deleted product leaves that no other product needs
* rotate_check      - can a placed product turn about its anchor, and which collision bricks move
* unreadable_objects - regions whose furniture the tables in use do not know (a mod's), with a Korean hint

Game coordinates: x and z horizontal, y up (0..height-1).  Region arrays are uint8[H, 16(z), 16(x)]
(the file layout (y*16 + z)*16 + x).  Brick 0 = air, 1..254 = brick_table ids, 255 = invisible
collision filler written under placed furniture.
"""
from __future__ import annotations

import os
from collections import OrderedDict

import numpy as np

from narew import brick

from . import savedworlds
from .core import StudioError
from .svc_world import WorldDoc, WorldService

AIR = 0
COLLISION = 255
MAX_CELLS = 8_000_000          # one edit (box, shape, paste); the 3D editor stops at 4M for its box tool
READ_CACHE = 512               # regions kept for reading (64 KiB each); edited regions are always kept


# --------------------------------------------------------------------------- shared furniture rules

def product_check(doc: WorldDoc, product_id: int, cell, rot: int, get, loaded, live_objects):
    """(ok, reason, footprint cells) for placing product_id with its anchor at cell.
    get(x, y, z) -> brick, loaded(x, y, z) -> the cell is inside and editable,
    live_objects: iterable of (key, kind, type, x, y, z, rot)."""
    if not doc or not doc.is_world:
        return False, '설치물은 월드(.bwr)에만 놓을 수 있습니다', []
    pt = doc.products.get(product_id)
    if pt is None:
        return False, '표에 없는 설치물입니다', []
    why = brick.placeable(pt)
    if why:
        return False, why, []
    x, y, z = cell
    cells = [(x + dx, y + dy, z + dz) for dx, dy, dz in brick.product_footprint(pt, rot)]
    inside = 0 <= x < doc.width and 0 <= y < doc.height and 0 <= z < doc.depth
    if not inside or not doc.objects_decoded(x >> 4, z >> 4):
        return False, '이 자리에는 놓을 수 없습니다 (범위 밖이거나 해석되지 않은 영역)', cells
    for c in cells:
        if not loaded(*c):
            return False, '범위 밖이거나 아직 불러오지 않은 곳입니다', cells
        if get(*c) != 0:
            return False, '자리가 막혀 있습니다 (블록이나 다른 설치물)', cells
    if any((o[3], o[4], o[5]) == (x, y, z) for o in live_objects):
        return False, '그 자리에 다른 오브젝트가 있습니다', cells
    return True, '', cells


def _other_cells(doc: WorldDoc, ob, live_objects) -> set:
    """Footprint cells of the live products near ob other than ob itself."""
    key, x, z = ob[0], ob[3], ob[5]
    others = set()
    for o in live_objects:
        if o[0] != key and o[1] == brick.OBJ_PRODUCT and abs(o[3] - x) < 24 and abs(o[5] - z) < 24:
            others.update((o[3] + dx, o[4] + dy, o[5] + dz) for dx, dy, dz in doc.footprint(o[2], o[6]))
    return others


def collision_to_clear(doc: WorldDoc, ob, live_objects) -> list[tuple[int, int, int]]:
    """Footprint cells of the live object ob that no other live product covers (the caller clears
    the ones that still hold collision bricks)."""
    key, kind, t, x, y, z, r = ob
    if kind != brick.OBJ_PRODUCT or not doc:
        return []
    mine = {(x + dx, y + dy, z + dz) for dx, dy, dz in doc.footprint(t, r)}
    return sorted(mine - _other_cells(doc, ob, live_objects))


def rotate_check(doc: WorldDoc, ob, rot: int, get, loaded, live_objects):
    """(ok, reason, cells to clear, cells to fill) for turning the live product ob to rot about its
    anchor, as the game's R key does.  The footprint for the new rotation must be inside, loaded and
    free (air, or the product's own collision); collision cells of the old footprint that neither the
    new one nor another product needs are cleared."""
    key, kind, t, x, y, z, old = ob
    if not doc or kind != brick.OBJ_PRODUCT:
        return False, '나무는 돌릴 수 없습니다 (설치물만)', [], []
    pt = doc.products.get(t)
    if pt is None:
        return False, '표에 없는 설치물입니다', [], []
    rot &= 3
    new = [(x + dx, y + dy, z + dz) for dx, dy, dz in brick.product_footprint(pt, rot)]
    if rot == old:
        return True, '', [], new
    mine = {(x + dx, y + dy, z + dz) for dx, dy, dz in doc.footprint(t, old)}
    others = _other_cells(doc, ob, live_objects)
    for c in new:
        if not loaded(*c):
            return False, '돌린 자리가 범위 밖이거나 아직 불러오지 않은 곳입니다', [], new
        v = get(*c)
        if v != 0 and not (v == COLLISION and c in mine and c not in others):
            return False, '돌린 자리가 막혀 있습니다 (블록이나 다른 설치물)', [], new
    return True, '', sorted(mine - set(new) - others), new


# --------------------------------------------------------------------------- furniture the tables do not know

def unreadable_objects(doc: WorldDoc, game_root: str | None = None, limit: int = 6) -> dict | None:
    """What could not be read of a world's objects, or None when every region was decoded.

    An object record's length depends on its product's type, so a region holding furniture that the
    product table in use does not know (a mod's chair in a save played with that mod) cannot be split
    into objects: its trees and furniture are not listed, cannot be deleted/turned/added to, and the
    block is saved back verbatim.  {'count', 'regions' (first few [rx, rz]), 'objects' (declared),
    'unknown_products' [{'product', 'pos'}], 'played_in' (the save's own install when it is another
    one), 'hint' (Korean)}."""
    regs = doc.unreadable_regions() if doc and doc.is_world else []
    if not regs:
        return None
    total, unknown = 0, []
    for rx, rz in regs:
        n, stop = brick.scan_objects_raw(doc.obj.region(rx, rz).objects_raw or b'', doc.products)
        total += max(0, n)
        if stop and stop[0] == brick.OBJ_PRODUCT and doc.products.get(stop[1]) is None:
            unknown.append({'product': stop[1], 'pos': list(stop[2:5])})
    out = {'count': len(regs), 'regions': [list(r) for r in regs[:limit]], 'objects': total}
    if unknown:
        out['unknown_products'] = unknown[:limit]
    where = ', '.join(f'({rx},{rz})' for rx, rz in regs[:3]) + (' 등' if len(regs) > 3 else '')
    ids = sorted({u['product'] for u in unknown})
    what = (f'표에 없는 설치물({", ".join(f"#{i}" for i in ids[:4])}{" 등" if len(ids) > 4 else ""})' if ids
            else '표와 맞지 않는 설치물')
    hint = (f'영역 {len(regs)}곳 {where}의 오브젝트(가구·나무 {total}개)를 읽지 못했습니다: 지금 게임 폴더의 '
            f'{what}이 있습니다 (모드로 더한 가구?). 그 영역의 오브젝트는 목록·개수에서 빠지고 지우거나 돌릴 수 '
            '없지만, 저장하면 그대로 남습니다. ')
    played = savedworlds.save_game_root(doc.path) if doc.path else None
    same = bool(played and game_root) and os.path.normcase(os.path.abspath(played)) == \
        os.path.normcase(os.path.abspath(game_root))
    if played and not same:
        out['played_in'] = played
        hint += f'이 세이브는 다른 설치 폴더({played})의 것이니 그 게임 폴더로 여세요.'
    elif played:
        hint += '그 가구를 더한 모드가 지금 이 게임 폴더에 적용되어 있지 않습니다. 플레이할 때의 모드를 다시 ' \
                '적용하거나, 그때의 게임 폴더로 여세요.'
    else:
        hint += '그 세이브를 플레이한 게임 폴더(모드가 적용된 설치 폴더)로 여세요.'
    out['hint'] = hint
    return out


# --------------------------------------------------------------------------- object ids

def object_id(key) -> str:
    """Stable text id of a live object: 'o:<rx>:<rz>:<index>' (in the file) or 'n:<n>' (placed now)."""
    return f'n:{key[1]}' if key[0] == 'new' else f'o:{key[0]}:{key[1]}:{key[2]}'


def parse_object_id(text: str):
    parts = str(text).split(':')
    try:
        if parts[0] == 'n' and len(parts) == 2:
            return ('new', int(parts[1]))
        if parts[0] == 'o' and len(parts) == 4:
            return (int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        pass
    raise StudioError(f'오브젝트 id 형식이 아닙니다: {text!r} (예: "o:32:31:0", "n:1")')


def rotate_array(arr: np.ndarray, k: int) -> np.ndarray:
    """Turn a [y, z, x] block k x 90 degrees about y, the same turn as furniture rotation:
    a cell at (x, z) of a w-wide block goes to (z, w-1-x) per step."""
    for _ in range(k & 3):
        arr = np.flip(arr.transpose(0, 2, 1), axis=1)
    return np.ascontiguousarray(arr)


def rotate_point(x: int, z: int, w: int, d: int, k: int) -> tuple[int, int]:
    """Where cell (x, z) of a w x d block lands after rotate_array(..., k)."""
    for _ in range(k & 3):
        x, z, w, d = z, w - 1 - x, d, w
    return x, z


# --------------------------------------------------------------------------- session

class EditSession:
    """An opened world/prefab with an undo history.  Region arrays are copied from the document on
    first use; save() writes the edited regions and objects through WorldService (into the mod
    project, or a .bwr file for game saves) exactly like the editor's save button."""

    def __init__(self, svc: WorldService, doc: WorldDoc, sid: str):
        self.svc, self.doc, self.id = svc, doc, sid
        self.W, self.D, self.H = doc.width, doc.depth, doc.height
        self.arrays: dict[tuple[int, int], np.ndarray] = {}         # regions this session writes to
        self._ro: OrderedDict = OrderedDict()                           # recently read regions (LRU)
        self.modified: set[tuple[int, int]] = set()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.added: dict[tuple, tuple] = {}         # ('new', n) -> (type, x, y, z, rot)
        self.deleted: set[tuple] = set()
        self.states: dict[tuple, int] = {}          # object key -> state byte set in this session
        self.rots: dict[tuple, int] = {}            # object key -> rotation set in this session (turned)
        self._n = 0
        self._op: dict | None = None
        self.objects = doc.objects()
        sp = doc.start_pos()
        self.start_pos = list(sp) if sp else None
        self.start_changed = False

    # ------------------------------------------------------------------ reading
    def inside(self, x, y, z) -> bool:
        return 0 <= x < self.W and 0 <= y < self.H and 0 <= z < self.D

    def region(self, rx: int, rz: int) -> np.ndarray:
        """A region's bricks to change (kept by the session until it is saved or closed)."""
        a = self.arrays.get((rx, rz))
        if a is None:
            a = self._ro.pop((rx, rz), None)
            if a is None:
                a = np.ascontiguousarray(self.doc.region_array(rx, rz, keep=False))
            self.arrays[(rx, rz)] = a
        return a

    def peek(self, rx: int, rz: int) -> np.ndarray:
        """A region's bricks to read (do not modify); big worlds are read without filling memory."""
        a = self.arrays.get((rx, rz))
        if a is not None:
            return a
        a = self._ro.get((rx, rz))
        if a is None:
            a = self._ro[(rx, rz)] = np.ascontiguousarray(self.doc.region_array(rx, rz, keep=False))
            while len(self._ro) > READ_CACHE:
                self._ro.popitem(last=False)
        else:
            self._ro.move_to_end((rx, rz))
        return a

    def get(self, x, y, z) -> int:
        if not self.inside(x, y, z):
            return 0
        return int(self.peek(x >> 4, z >> 4)[y, z & 15, x & 15])

    def clip(self, x0, y0, z0, x1, y1, z1):
        """Inclusive box clipped to the volume (corners in any order), or None when outside."""
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        z0, z1 = sorted((int(z0), int(z1)))
        x0, y0, z0 = max(x0, 0), max(y0, 0), max(z0, 0)
        x1, y1, z1 = min(x1, self.W - 1), min(y1, self.H - 1), min(z1, self.D - 1)
        if x0 > x1 or y0 > y1 or z0 > z1:
            return None
        return x0, y0, z0, x1, y1, z1

    def read_box(self, x0, y0, z0, x1, y1, z1) -> np.ndarray:
        """Bricks of an inclusive, already clipped box as uint8[y, z, x] (a copy)."""
        out = np.zeros((y1 - y0 + 1, z1 - z0 + 1, x1 - x0 + 1), dtype=np.uint8)
        for rz in range(z0 >> 4, (z1 >> 4) + 1):
            for rx in range(x0 >> 4, (x1 >> 4) + 1):
                a = self.peek(rx, rz)
                lx0, lx1 = max(x0, rx * 16), min(x1, rx * 16 + 15)
                lz0, lz1 = max(z0, rz * 16), min(z1, rz * 16 + 15)
                out[:, lz0 - z0:lz1 - z0 + 1, lx0 - x0:lx1 - x0 + 1] = \
                    a[y0:y1 + 1, lz0 & 15:(lz1 & 15) + 1, lx0 & 15:(lx1 & 15) + 1]
        return out

    def top_y(self, x: int, z: int, below: int | None = None, solid_only: bool = True) -> int:
        """Highest y (<= below) holding a brick; collision filler is skipped when solid_only. -1 = none."""
        if not (0 <= x < self.W and 0 <= z < self.D):
            return -1
        col = self.peek(x >> 4, z >> 4)[:, z & 15, x & 15]
        if below is not None:
            col = col[:max(0, min(self.H, below + 1))]
        m = (col != 0) & (col != COLLISION) if solid_only else col != 0
        nz = np.nonzero(m)[0]
        return int(nz[-1]) if len(nz) else -1

    def live_objects(self):
        """(key, kind, type, x, y, z, rot) of the objects in the world now."""
        rots = self.rots
        for ob in self.objects:
            key = (ob[0], ob[1], ob[2])
            if key not in self.deleted:
                yield (key, *ob[3:8], rots.get(key, ob[8]))
        for key, (t, x, y, z, r) in self.added.items():
            if key not in self.deleted:
                yield key, brick.OBJ_PRODUCT, t, x, y, z, rots.get(key, r)

    def find_object(self, key):
        return next((o for o in self.live_objects() if o[0] == key), None)

    def state_of(self, key) -> int:
        """State byte of a live object (bit brick.STATE_ON = a lamp is on)."""
        if key in self.states:
            return self.states[key]
        return 0 if key[0] == 'new' else self.doc.object_state(*key)

    # ------------------------------------------------------------------ recording
    def begin(self, label: str):
        self._op = {'label': label, 'cells': [], 'added': [], 'deleted': [], 'start': None, 'states': [],
                    'rots': []}

    def end(self) -> dict:
        """Close the operation: push it on the undo history when it changed anything."""
        op, self._op = self._op, None
        n = sum(len(c[1]) for c in op['cells'])
        if n or op['added'] or op['deleted'] or op['start'] or op['states'] or op['rots']:
            self.undo_stack.append(op)
            self.redo_stack.clear()
        return {'changed': n, 'placed': len(op['added']), 'deleted': len(op['deleted'])}

    def abort(self):
        """Undo what the open operation did so far (an error in the middle of an edit)."""
        op, self._op = self._op, None
        if op:
            self._apply(op, back=True)

    def _record(self, key, idx, old, new):
        if self._op is None:
            raise RuntimeError('edit outside begin()/end()')
        self._op['cells'].append((key, idx.astype(np.int32), old, new))
        self.modified.add(key)

    def _keep_mask(self, old: np.ndarray, only_air: bool, replace, keep_collision: bool):
        m = np.ones(old.shape, dtype=bool)
        if only_air:
            m &= old == AIR
        if replace is not None:
            m &= np.isin(old, np.asarray(list(replace), dtype=np.uint8))
        if keep_collision:
            m &= old != COLLISION
        return m

    # ------------------------------------------------------------------ writing bricks
    def write_box(self, x0, y0, z0, values, mask=None, only_air=False, replace=None, keep_collision=True) -> int:
        """Write a block of values (scalar or uint8[h, d, w]) with its low corner at (x0, y0, z0).
        mask (bool, same shape) picks the cells to write; the block is clipped to the volume.
        Returns the number of bricks changed."""
        vals = np.asarray(values, dtype=np.uint8)
        if mask is not None:
            shape = mask.shape
        elif vals.ndim == 3:
            shape = vals.shape
        else:
            raise ValueError('a scalar write needs a mask (or use fill)')
        h, d, w = shape
        if h * d * w > MAX_CELLS:
            raise StudioError(f'한 번에 바꿀 수 있는 칸은 {MAX_CELLS:,}개까지입니다 ({h * d * w:,}칸)')
        box = self.clip(x0, y0, z0, x0 + w - 1, y0 + h - 1, z0 + d - 1)
        if box is None:
            return 0
        bx0, by0, bz0, bx1, by1, bz1 = box
        if vals.ndim == 3:
            vals = vals[by0 - y0:by1 - y0 + 1, bz0 - z0:bz1 - z0 + 1, bx0 - x0:bx1 - x0 + 1]
        if mask is not None:
            mask = mask[by0 - y0:by1 - y0 + 1, bz0 - z0:bz1 - z0 + 1, bx0 - x0:bx1 - x0 + 1]
        changed = 0
        for rz in range(bz0 >> 4, (bz1 >> 4) + 1):
            for rx in range(bx0 >> 4, (bx1 >> 4) + 1):
                lx0, lx1 = max(bx0, rx * 16), min(bx1, rx * 16 + 15)
                lz0, lz1 = max(bz0, rz * 16), min(bz1, rz * 16 + 15)
                a = self.region(rx, rz)
                sub = a[by0:by1 + 1, lz0 & 15:(lz1 & 15) + 1, lx0 & 15:(lx1 & 15) + 1]
                sl = (slice(None), slice(lz0 - bz0, lz1 - bz0 + 1), slice(lx0 - bx0, lx1 - bx0 + 1))
                new = np.broadcast_to(vals[sl] if vals.ndim == 3 else vals, sub.shape)
                m = self._keep_mask(sub, only_air, replace, keep_collision) & (sub != new)
                if mask is not None:
                    m &= mask[sl]
                if not m.any():
                    continue
                yy, zz, xx = np.nonzero(m)
                idx = ((yy + by0) * 16 + (zz + (lz0 & 15))) * 16 + (xx + (lx0 & 15))
                old, nv = sub[m].copy(), new[m].copy()
                sub[m] = nv
                self._record((rx, rz), idx, old, nv)
                changed += len(idx)
        return changed

    def fill(self, x0, y0, z0, x1, y1, z1, value: int, mode: str = 'solid', **keep) -> int:
        """Fill an inclusive box.  mode: solid | hollow (shell, inside cleared) | outline (shell
        only, inside untouched) | walls (the four vertical sides) | frame (the 12 edges)."""
        box = self.clip(x0, y0, z0, x1, y1, z1)
        if box is None:
            return 0
        x0, y0, z0, x1, y1, z1 = box
        shape = (y1 - y0 + 1, z1 - z0 + 1, x1 - x0 + 1)
        if shape[0] * shape[1] * shape[2] > MAX_CELLS:
            raise StudioError(f'한 번에 바꿀 수 있는 칸은 {MAX_CELLS:,}개까지입니다')
        y, z, x = np.ogrid[:shape[0], :shape[1], :shape[2]]
        ex = (x == 0) | (x == shape[2] - 1)
        ey = (y == 0) | (y == shape[0] - 1)
        ez = (z == 0) | (z == shape[1] - 1)
        if mode == 'solid':
            return self.write_box(x0, y0, z0, value, np.ones(shape, bool), **keep)
        if mode in ('hollow', 'outline'):
            shell = np.broadcast_to(ex | ey | ez, shape)
            n = self.write_box(x0, y0, z0, value, shell, **keep)
            if mode == 'hollow':
                n += self.write_box(x0, y0, z0, AIR, ~shell, **keep)
            return n
        if mode == 'walls':
            return self.write_box(x0, y0, z0, value, np.broadcast_to(ex | ez, shape), **keep)
        if mode == 'frame':
            edges = (ex & ey) | (ey & ez) | (ex & ez)
            return self.write_box(x0, y0, z0, value, np.broadcast_to(edges, shape), **keep)
        raise StudioError(f'모르는 채우기 방식: {mode} (solid, hollow, outline, walls, frame)')

    def write_cells(self, cells, only_air=False, replace=None, keep_collision=True) -> int:
        """cells: iterable of (x, y, z, brick); outside cells are skipped, the last write of a cell wins."""
        a = np.asarray(list(cells), dtype=np.int64).reshape(-1, 4)
        if not len(a):
            return 0
        if len(a) > MAX_CELLS:
            raise StudioError(f'한 번에 바꿀 수 있는 칸은 {MAX_CELLS:,}개까지입니다')
        xs, ys, zs, vs = a.T
        if ((vs < 0) | (vs > 255)).any():
            raise StudioError('블록 번호는 0~255입니다')
        ok = (xs >= 0) & (xs < self.W) & (ys >= 0) & (ys < self.H) & (zs >= 0) & (zs < self.D)
        xs, ys, zs, vs = xs[ok], ys[ok], zs[ok], vs[ok].astype(np.uint8)
        rkey = (zs >> 4) * (self.W >> 4) + (xs >> 4)
        order = np.argsort(rkey, kind='stable')
        xs, ys, zs, vs, rkey = xs[order], ys[order], zs[order], vs[order], rkey[order]
        bounds = np.flatnonzero(np.diff(rkey)) + 1
        changed = 0
        for s, e in zip(np.r_[0, bounds], np.r_[bounds, len(rkey)]):
            if s == e:
                continue
            rx, rz = int(xs[s] >> 4), int(zs[s] >> 4)
            idx = (ys[s:e] * 16 + (zs[s:e] & 15)) * 16 + (xs[s:e] & 15)
            v = vs[s:e]
            if len(np.unique(idx)) != len(idx):          # keep the last write of each cell
                _, first_rev = np.unique(idx[::-1], return_index=True)
                pos = len(idx) - 1 - first_rev
                idx, v = idx[pos], v[pos]
            flat = self.region(rx, rz).reshape(-1)
            old = flat[idx]
            m = self._keep_mask(old, only_air, replace, keep_collision) & (old != v)
            if not m.any():
                continue
            idx, old, v = idx[m], old[m].copy(), v[m]
            flat[idx] = v
            self._record((rx, rz), idx, old, v.copy())
            changed += len(idx)
        return changed

    # ------------------------------------------------------------------ objects
    def check_product(self, product_id: int, cell, rot: int = 0):
        return product_check(self.doc, product_id, cell, rot & 3, self.get,
                             lambda x, y, z: self.inside(x, y, z), list(self.live_objects()))

    def place_product(self, product_id: int, x: int, y: int, z: int, rot: int = 0, state: int | None = None) -> tuple:
        """Place a product (object + collision bricks). Returns its key.  state None = as freshly
        placed in game (brick.placed_state: a lamp is on)."""
        ok, why, cells = self.check_product(product_id, (x, y, z), rot)
        if not ok:
            raise StudioError(f'설치물 #{product_id}을(를) ({x}, {y}, {z})에 놓을 수 없습니다: {why}')
        if state is None:
            state = brick.placed_state(self.doc.products.get(product_id))
        if not 0 <= state <= 255:
            raise StudioError(f'오브젝트 상태는 0~255입니다: {state}')
        self.write_cells([(*c, COLLISION) for c in cells], keep_collision=False)
        self._n += 1
        key = ('new', self._n)
        info = (int(product_id), int(x), int(y), int(z), rot & 3)
        self.added[key] = info
        self._op['added'].append((key, info))
        if state:
            self.set_state(key, state)
        return key

    def set_state(self, key, state: int) -> bool:
        """Change the state byte of a live object (part of the open operation). False = it already had it."""
        if self.find_object(key) is None:
            raise StudioError(f'없는 오브젝트입니다: {object_id(key) if isinstance(key, tuple) else key}')
        state = int(state)
        if not 0 <= state <= 255:
            raise StudioError(f'오브젝트 상태는 0~255입니다: {state}')
        if self.state_of(key) == state:
            return False
        self._op['states'].append((key, self.states.get(key), state))
        self.states[key] = state
        return True

    def check_rotate(self, key, rot: int):
        """(ok, reason, cells to clear, cells to fill) for turning the product key to rot."""
        ob = self.find_object(key)
        if ob is None:
            raise StudioError(f'없는 오브젝트입니다: {object_id(key) if isinstance(key, tuple) else key}')
        return rotate_check(self.doc, ob, rot, self.get, lambda x, y, z: self.inside(x, y, z),
                            list(self.live_objects()))

    def rotate_object(self, key, rot: int) -> bool:
        """Turn a product about its anchor to rot 0-3 with its collision bricks (part of the open
        operation); the object itself stays (a container keeps its items).  False = it already had it."""
        ok, why, clear, fill = self.check_rotate(key, rot)
        if not ok:
            raise StudioError(f'{object_id(key)}을(를) {(rot & 3) * 90}°로 돌릴 수 없습니다: {why}')
        rot &= 3
        if self.find_object(key)[6] == rot:
            return False
        self.write_cells([(*c, AIR) for c in clear], replace=(COLLISION,), keep_collision=False)
        self.write_cells([(*c, COLLISION) for c in fill], keep_collision=False)
        self._op['rots'].append((key, self.rots.get(key), rot))
        self.rots[key] = rot
        return True

    def delete_object(self, key) -> tuple:
        """Delete a tree or product; a product's collision bricks no other product needs are cleared."""
        ob = self.find_object(key)
        if ob is None:
            raise StudioError(f'없는 오브젝트입니다: {object_id(key) if isinstance(key, tuple) else key}')
        live = list(self.live_objects())
        cells = [c for c in collision_to_clear(self.doc, ob, live) if self.inside(*c)]
        self.write_cells([(*c, AIR) for c in cells], replace=(COLLISION,), keep_collision=False)
        self.deleted.add(key)
        self._op['deleted'].append(key)
        return ob

    def objects_in(self, x0, y0, z0, x1, y1, z1):
        return [o for o in self.live_objects() if x0 <= o[3] <= x1 and y0 <= o[4] <= y1 and z0 <= o[5] <= z1]

    def unreadable_in(self, x0, z0, x1, z1) -> list[tuple[int, int]]:
        """Regions touching the horizontal box whose objects could not be read (see unreadable_objects)."""
        return [(rx, rz) for rx, rz in self.doc.unreadable_regions()
                if x0 >> 4 <= rx <= x1 >> 4 and z0 >> 4 <= rz <= z1 >> 4]

    def set_start(self, x: float, y: float, z: float):
        if not self.doc.is_world:
            raise StudioError('시작 위치는 월드(.bwr)에만 있습니다')
        if self.start_pos is None:
            raise StudioError('이 월드 파일에서는 시작 위치를 찾지 못했습니다')
        old = list(self.start_pos)
        self.start_pos = [float(x), float(y), float(z)]
        self.start_changed = True
        self._op['start'] = (old, list(self.start_pos))

    # ------------------------------------------------------------------ history
    def _apply(self, op: dict, back: bool):
        cells = reversed(op['cells']) if back else op['cells']
        for key, idx, old, new in cells:
            self.region(*key).reshape(-1)[idx] = old if back else new
            self.modified.add(key)
        for key, info in op['added']:
            if back:
                self.added.pop(key, None)
            else:
                self.added[key] = info
        for key in op['deleted']:
            if back:
                self.deleted.discard(key)
            else:
                self.deleted.add(key)
        for name in ('states', 'rots'):
            d = getattr(self, name)
            for key, old, new in reversed(op[name]) if back else op[name]:
                v = old if back else new
                if v is None:
                    d.pop(key, None)
                else:
                    d[key] = v
        if op['start']:
            self.start_pos = list(op['start'][0] if back else op['start'][1])
            self.start_changed = True

    def undo(self, steps: int = 1) -> list[str]:
        done = []
        for _ in range(max(0, steps)):
            if not self.undo_stack:
                break
            op = self.undo_stack.pop()
            self._apply(op, back=True)
            self.redo_stack.append(op)
            done.append(op['label'])
        return done

    def redo(self, steps: int = 1) -> list[str]:
        done = []
        for _ in range(max(0, steps)):
            if not self.redo_stack:
                break
            op = self.redo_stack.pop()
            self._apply(op, back=False)
            self.undo_stack.append(op)
            done.append(op['label'])
        return done

    def dirty(self) -> bool:
        """Unsaved changes = edits on the undo history since open/save (as in the 3D editor)."""
        return bool(self.undo_stack)

    # ------------------------------------------------------------------ saving
    def _edits(self):
        regions = {k: self.arrays[k] for k in self.modified if k in self.arrays}
        deleted = [k for k in self.deleted if k[0] != 'new']
        added = [(t, x, y, z, self.rots.get(k, r), self.state_of(k))
                 for k, (t, x, y, z, r) in self.added.items() if k not in self.deleted]
        states = {k: v for k, v in self.states.items() if k[0] != 'new' and k not in self.deleted}
        rots = {k: v for k, v in self.rots.items() if k[0] != 'new' and k not in self.deleted}
        return regions, deleted, (self.start_pos if self.start_changed else None), added, states, rots

    def save(self, path: str | None = None) -> int:
        """Into the mod project (worlds/prefabs from the packs) or to a .bwr file (path, or the
        document's own file).  Game saves (Local\\, backup_map\\) are refused by WorldService."""
        regions, deleted, start, added, states, rots = self._edits()
        if path or self.doc.path:
            n = self.svc.save_as(self.doc, path or self.doc.path, regions, deleted, start, added, states, rots)
        else:
            n = self.svc.save(self.doc, regions, deleted, start, added, states, rots)
        self.mark_saved()
        return n

    def mark_saved(self):
        """The document holds the edits now: take its objects again and start a fresh history."""
        self.modified.clear()
        self.deleted.clear()
        self.added.clear()
        self.states.clear()
        self.rots.clear()
        self.start_changed = False
        self.objects = self.doc.objects()
        sp = self.doc.start_pos()
        self.start_pos = list(sp) if sp else None
        self.undo_stack, self.redo_stack = [], []
        for k in [k for k in self.arrays if k not in self.modified]:
            self._ro[k] = self.arrays.pop(k)             # saved regions only need to be readable now
