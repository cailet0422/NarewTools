"""Block world / prefab access for the native world editor."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import threading

import numpy as np
from PIL import Image

from narew import brick, voxel
from narew.modfmt import check_rel_path, entry_key

from . import imaging, tables, textcodec
from .core import Studio, StudioError
from .mirror import get_mirror

TREE_NAMES = {1: '과일나무', 2: '소나무', 3: '참나무', 4: '정글나무', 5: '아카시아', 6: '선인장'}


class WorldDoc:
    """An opened .bwr world or .iaf prefab."""

    def __init__(self, name: str, obj, path: str | None = None, products: brick.ProductTable | None = None):
        self.name = name
        self.obj = obj
        self.path = path              # set for a file outside the game packs (e.g. a game save)
        self.products = products or brick.ProductTable()    # the table the world was decoded with
        self.is_world = isinstance(obj, brick.World)
        if self.is_world:
            self.width, self.depth, self.height = obj.width, obj.depth, brick.REGION_HEIGHT
        else:
            self.width, self.depth, self.height = obj.width, obj.depth, obj.height
        self.rx, self.rz = self.width // brick.SIDE, self.depth // brick.SIDE

    def column(self, rx: int, rz: int) -> brick.Column:
        if not (0 <= rx < self.rx and 0 <= rz < self.rz):
            raise StudioError('범위 밖 영역')
        return self.obj.region(rx, rz).column if self.is_world else self.obj.group(rx, rz)

    def region_array(self, rx: int, rz: int, keep: bool = True) -> np.ndarray:
        """Bricks as uint8[H, 16(z), 16(x)] (a copy the editor may change).  keep=False does not leave
        the decompressed region cached in the document."""
        col = self.column(rx, rz)
        raw = bytes(col.data) if keep else col.peek()
        return np.frombuffer(raw, dtype=np.uint8).reshape(col.height, 16, 16).copy()

    def objects(self) -> list[tuple]:
        """(rx, rz, index, kind, type, x, y, z, rot) for every tree/product."""
        out = []
        if self.is_world:
            for r in self.obj.regions:
                for j, o in enumerate(r.objects or ()):
                    out.append((r.rx, r.rz, j, o.kind, o.type_id, o.x, o.y, o.z, o.rot))
        return out

    def start_pos(self):
        return self.obj.tail.start_pos if self.is_world else None

    def objects_decoded(self, rx: int, rz: int) -> bool:
        """False when the region's object block could not be decoded (kept verbatim)."""
        return self.is_world and self.obj.region(rx, rz).objects is not None

    def footprint(self, type_id: int, rot: int) -> list[tuple[int, int, int]]:
        pt = self.products.get(type_id)
        return brick.product_footprint(pt, rot) if pt else [(0, 0, 0)]

    def product_choices(self) -> list[dict]:
        """Products for the placement picker: id, name, and why it cannot be placed (or None)."""
        out = []
        for pid, pt in sorted(self.products.types.items()):
            cells = brick.product_footprint(pt, 0)
            size = tuple(max(c[i] for c in cells) - min(c[i] for c in cells) + 1 for i in (0, 2, 1))
            out.append({'id': pid, 'name': pt.name, 'event': brick.PRODUCT_EVENTS[pt.event]
                        if pt.event < len(brick.PRODUCT_EVENTS) else str(pt.event),
                        'reason': brick.placeable(pt), 'size': size})
        return out


class WorldService:
    def __init__(self, st: Studio):
        self.st = st
        self.mirror = get_mirror(st)
        self.sessions: dict[str, tuple] = {}
        self.lock = threading.Lock()
        self.top_dir = os.path.join(st.cache_dir, 'top')
        os.makedirs(self.top_dir, exist_ok=True)

    def product_table(self) -> brick.ProductTable:
        p = self.mirror.ensure(tables.PRODUCT_TABLE)
        return brick.ProductTable.read(p) if p else brick.ProductTable()

    # A game save was written by the game with the mods applied at that time, while the studio reads
    # the originals (+ the open project).  For saves, add what the live packs know so furniture and
    # blocks added by an applied mod are decoded and drawn too.
    def _live(self, name: str) -> bytes | None:
        g = self.st.game
        return g.read_live(name) if g else None

    def save_product_table(self) -> brick.ProductTable:
        t = self.product_table()
        data = self._live(tables.PRODUCT_TABLE)
        if data:
            live = brick.ProductTable.parse(textcodec.decode(data)[0])
            t = brick.ProductTable({**t.types, **live.types})
        return t

    def save_bricks(self) -> list[dict]:
        by_id = {b['id']: b for b in tables.bricks(self.st.read(tables.BRICK_TABLE))}
        data = self._live(tables.BRICK_TABLE)
        if data:
            by_id.update({b['id']: b for b in tables.bricks(data)})
        return [by_id[k] for k in sorted(by_id)]

    def save_atlas(self) -> Image.Image:
        data = self._live(tables.ATLAS['diffuse'])
        if data:
            return imaging.open_image(data)
        return imaging.open_image(self.st.read(tables.ATLAS['diffuse']))

    def save_product_names(self) -> dict[int, str]:
        names = {p['id']: p['name'] for p in tables.products(self.st.read(tables.PRODUCT_TABLE))}
        data = self._live(tables.PRODUCT_TABLE)
        if data:
            names.update({p['id']: p['name'] for p in tables.products(data)})
        return names

    def list(self) -> list[dict]:
        st = self.st
        g = st.need_game()
        names = {k: loc.entry.name for k, loc in g.index.items() if k.endswith(('.bwr', '.iaf'))}
        for k, (n, _) in st.project_files().items():
            if k.endswith(('.bwr', '.iaf')):
                names[k] = n
        out = []
        for k, n in sorted(names.items()):
            try:
                head = st.read(n, 'base')[:20]
                if k.endswith('.bwr'):
                    _, w, d, _ = struct.unpack_from('<I3i', head)
                    size = (w, brick.REGION_HEIGHT, d)
                else:
                    _, w, hgt, d, _ = struct.unpack_from('<5i', head)
                    size = (w, hgt, d)
            except Exception:
                size = None
            out.append({'name': n, 'type': 'world' if k.endswith('.bwr') else 'prefab', 'size': size,
                        'status': st.status_of(n)})
        return out

    def open(self, name: str) -> WorldDoc:
        check_rel_path(name)
        name = self.st.canonical_name(name)
        k = entry_key(name)
        sig = self.mirror.signature(name)
        if sig is None:
            raise StudioError(f'없는 파일: {name}')
        with self.lock:
            hit = self.sessions.get(k)
            if hit and hit[0] == sig:
                return hit[1]
            data = self.st.read(name, 'base')
            products = self.product_table() if k.endswith('.bwr') else None
            try:
                obj = brick.World.from_bytes(data, products) if k.endswith('.bwr') \
                    else brick.Architecture.from_bytes(data)
            except brick.BrickError as ex:
                raise StudioError(f'블록 파일을 읽을 수 없습니다: {ex}')
            doc = WorldDoc(name, obj, products=products)
            self.sessions[k] = (sig, doc)
            return doc

    def open_file(self, path: str) -> WorldDoc:
        """A .bwr outside the packs, e.g. the game's own save (read safely while the game writes it)."""
        from . import savedworlds
        path = os.path.abspath(path)
        s = os.stat(path)
        sig = ('file', path, s.st_size, s.st_mtime_ns)
        with self.lock:
            hit = self.sessions.get(path)
            if hit and hit[0] == sig:
                return hit[1]
        data = savedworlds.read_stable(path)
        products = self.save_product_table()
        try:
            obj = brick.World.from_bytes(data, products)
        except brick.BrickError as ex:
            raise StudioError(f'월드 파일을 읽을 수 없습니다: {ex}')
        doc = WorldDoc(path, obj, path=path, products=products)
        with self.lock:
            self.sessions[path] = (sig, doc)
        return doc

    def _apply_edits(self, doc: WorldDoc, regions, deleted_objects, start_pos, added_objects=()):
        # check new objects before changing anything
        new_objs = []
        for type_id, x, y, z, rot in added_objects:
            if not doc.is_world:
                raise StudioError('설치물은 월드(.bwr)에만 놓을 수 있습니다')
            pt = doc.products.get(type_id)
            if pt is None:
                raise StudioError(f'설치물 #{type_id}이(가) 표에 없습니다')
            if not doc.objects_decoded(x // brick.SIDE, z // brick.SIDE):
                raise StudioError(f'({x}, {z}) 영역의 오브젝트는 해석되지 않아 설치물을 더할 수 없습니다')
            try:
                new_objs.append(brick.new_product(pt, x, y, z, rot))
            except brick.BrickError as ex:
                raise StudioError(str(ex))
        for (rx, rz), arr in regions.items():
            col = doc.column(rx, rz)
            raw = np.ascontiguousarray(arr, dtype=np.uint8).tobytes()
            if len(raw) != col.height * 256:
                raise StudioError(f'영역 ({rx},{rz}) 데이터 크기가 맞지 않습니다')
            new = brick.Column(col.height, data=raw, level=col.level)
            if doc.is_world:
                doc.obj.region(rx, rz).column = new
            else:
                doc.obj.groups[rz * doc.rx + rx] = new
        if doc.is_world:
            for rx, rz, j in sorted(deleted_objects, key=lambda t: -t[2]):
                r = doc.obj.region(rx, rz)
                if r.objects is None:
                    raise StudioError('이 영역의 오브젝트는 해석되지 않아 지울 수 없습니다')
                if 0 <= j < len(r.objects):
                    del r.objects[j]
            for o in new_objs:                  # after the deletions so their indices stay valid
                doc.obj.region(o.x // brick.SIDE, o.z // brick.SIDE).objects.append(o)
            if start_pos is not None:
                doc.obj.tail.set_start_pos(*map(float, start_pos))

    def save(self, doc: WorldDoc, regions: dict[tuple[int, int], np.ndarray],
             deleted_objects: list[tuple[int, int, int]] = (), start_pos=None, added_objects=()) -> int:
        """Save into the mod project (worlds and prefabs from the game packs)."""
        if doc.path:
            raise StudioError('게임 저장본은 "다른 이름으로 저장"으로 새 파일에 저장하세요')
        self.st.need_project()
        with self.lock:
            self._apply_edits(doc, regions, deleted_objects, start_pos, added_objects)
            data = doc.obj.to_bytes()
            self.st.write(doc.name, data)
            self.sessions[entry_key(doc.name)] = (self.mirror.signature(doc.name), doc)
        return len(data)

    def save_as(self, doc: WorldDoc, path: str, regions: dict[tuple[int, int], np.ndarray],
                deleted_objects: list[tuple[int, int, int]] = (), start_pos=None, added_objects=()) -> int:
        """Write the (edited) world to a new .bwr file.  The game's save folders are refused."""
        from . import savedworlds
        path = os.path.abspath(path)
        if not path.lower().endswith('.bwr') or not doc.is_world:
            raise StudioError('월드는 .bwr 파일로 저장합니다')
        game = self.st.game.root if self.st.game else None
        if savedworlds.is_game_save(game, path) or (game and os.path.normcase(path).startswith(
                os.path.normcase(os.path.join(game, 'data')) + os.sep)):
            raise StudioError('게임 폴더의 저장본·팩 자리에는 저장하지 않습니다. 다른 폴더를 고르세요.')
        with self.lock:
            self._apply_edits(doc, regions, deleted_objects, start_pos, added_objects)
            data = doc.obj.to_bytes()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + '.tmp', 'wb') as f:
                f.write(data)
            os.replace(path + '.tmp', path)
            # the open document is now that file; the original is read again from disk next time
            self.sessions.pop(doc.path, None)
            doc.path = doc.name = path
            s = os.stat(path)
            self.sessions[path] = (('file', path, s.st_size, s.st_mtime_ns), doc)
        return len(data)

    def new_prefab(self, name: str, w: int, h: int, d: int) -> str:
        self.st.need_project()
        check_rel_path(name)
        if not name.lower().endswith('.iaf'):
            raise StudioError('프리팹 이름은 .iaf로 끝나야 합니다')
        if w * h * d > 256 ** 3:
            raise StudioError('너무 큽니다')
        try:
            arch = brick.Architecture.new(w, h, d)
        except brick.BrickError as ex:
            raise StudioError(f'크기가 잘못됐습니다 (가로·세로는 16의 배수): {ex}')
        self.st.write(name, arch.to_bytes())
        return self.st.canonical_name(name)

    # ------------------------------------------------------------------ minimap
    def palette(self) -> voxel.BrickPalette:
        """Brick colours = average of the top atlas tile, tinted by the table colour."""
        atlas = imaging.open_image(self.st.read(tables.ATLAS['diffuse'])).convert('RGB')
        size = imaging.atlas_tile_size(atlas)
        avg, out = {}, {}
        for b in tables.bricks(self.st.read(tables.BRICK_TABLE)):
            t = b['tiles'].get('top', -1)
            if t not in avg:
                avg[t] = atlas.crop(imaging.atlas_box(t, size)).resize((1, 1), Image.BOX).getpixel((0, 0)) \
                    if 0 <= t < 256 else (200, 200, 200)
            try:
                argb = int(b['color'], 16)
            except ValueError:
                argb = 0xFFFFFFFF
            tint = ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255)
            rgb = tuple(c * k // 255 for c, k in zip(avg[t], tint))
            out[b['id']] = voxel.BrickInfo(b['id'], b['name'], b['category'].lower(), b['height'], rgb + (255,))
        return voxel.BrickPalette(out)

    def topview(self, doc: WorldDoc) -> str:
        sig = [repr(self.sessions.get(doc.path, (None,))[0] if doc.path else self.mirror.signature(doc.name))]
        sig += [repr(self.mirror.signature(n)) for n in (tables.BRICK_TABLE, tables.ATLAS['diffuse'])]
        path = os.path.join(self.top_dir, hashlib.sha1(json.dumps(sig).encode()).hexdigest()[:20] + '.png')
        if not os.path.isfile(path):
            with self.lock:
                voxel.render_top(voxel.Volume(doc.obj), self.palette(), path + '.tmp.png')
            os.replace(path + '.tmp.png', path)
        return path

    def object_label(self, kind: int, type_id: int, names: dict | None = None) -> str:
        if kind == 1:
            return f'나무: {TREE_NAMES.get(type_id, type_id)}'
        if names is None:
            names = {p['id']: p['name'] for p in tables.products(self.st.read(tables.PRODUCT_TABLE))}
        return f'설치물: {names.get(type_id, "")} (#{type_id})'
