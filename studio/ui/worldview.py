"""World editor 3D view: region streaming, meshing, tools, undo/redo."""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import Qt, Signal

from narew import brick

from ..gl.voxels import (BrickLUT, VoxelRenderer, VoxelStore, game_box_to_render, ground_hit, mesh_region,
                         raycast)
from ..svc_world import WorldDoc
from ..worldedit import collision_to_clear, product_check
from .glview import GLView

OBJ_COLORS = {1: (0.33, 0.8, 0.33), 2: (1.0, 0.6, 0.2)}
NEW_OBJ_COLOR = (1.0, 0.45, 0.8)          # products placed in this session (not saved yet)
COLLISION = 255


class WorldView(GLView):
    hover_text = Signal(str)
    history_changed = Signal(bool, bool)
    picked = Signal(int)
    camera_moved = Signal(float, float)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ren: VoxelRenderer | None = None
        self.doc: WorldDoc | None = None
        self.store: VoxelStore | None = None
        self.lut: BrickLUT | None = None
        self.atlas = None
        self.tool = 'view'
        self.selected = 1
        self.box_mode = 'fill'
        self.skip_air = True
        self.radius = 6
        self.show_objects = True
        self.show_collision = False
        self.prefab = None            # (w, h, d, {(gx, gz): arr})
        self.hover = None             # (x, y, z) target cell
        self.box_start = None
        self.undo_stack, self.redo_stack = [], []
        self.deleted: dict[tuple, tuple] = {}
        self.objects = []
        self.product = None           # product id for the 'product' tool
        self.rot = 0                  # its rotation (x 90 degrees), R turns it
        self.added: dict[tuple, tuple] = {}     # ('new', n) -> (type_id, x, y, z, rot)
        self._new_n = 0
        self.start_pos = None
        self.start_changed = False
        self.keys = set()
        self._down = None
        self._pending_open = None
        self.set_tool('view')

    # ------------------------------------------------------------------ GL
    def init_gl(self, ctx):
        self.ren = VoxelRenderer(ctx)
        if self._pending_open:
            self._open_now(*self._pending_open)

    def open(self, doc: WorldDoc, lut: BrickLUT, atlas_image):
        if self.ren is None:
            self._pending_open = (doc, lut, atlas_image)
            return
        self.makeCurrent()
        self._open_now(doc, lut, atlas_image)
        self.doneCurrent()
        self.update()

    def _open_now(self, doc, lut, atlas_image):
        self._pending_open = None
        self.doc, self.lut = doc, lut
        self.ren.clear()
        self.ren.set_atlas(atlas_image)
        self.store = VoxelStore(doc.width, doc.depth, doc.height)
        self.undo_stack, self.redo_stack = [], []
        self.deleted, self.box_start, self.hover = {}, None, None
        self.added, self.rot = {}, 0
        self.start_pos = list(doc.start_pos()) if doc.start_pos() else None
        self.start_changed = False
        self.objects = doc.objects()
        self._upload_objects()
        self.history_changed.emit(False, False)
        cam = self.cam
        if doc.is_world:
            sp = self.start_pos or [doc.width / 2, 80, doc.depth / 2]
            cam.target = np.array([sp[0], sp[1], -sp[2]])
            cam.dist, cam.yaw, cam.pitch = 60.0, math.radians(35), math.radians(30)
            cam.near, cam.far = 0.1, 3000
            self.ensure_regions()
        else:
            for rz in range(doc.rz):
                for rx in range(doc.rx):
                    self.store.put((rx, rz), doc.region_array(rx, rz))
            top = max([self.store.top_y(x, z) for x in range(0, doc.width, 2) for z in range(0, doc.depth, 2)] + [4])
            cam.frame((0, 0, -doc.depth), (doc.width, top + 1, 0))
            cam.yaw, cam.pitch = math.radians(35), math.radians(30)

    def live_objects(self):
        """(key, kind, type, x, y, z, rot) of the objects in the world now: the document's objects
        that were not deleted plus the products placed in this session (key ('new', n))."""
        for ob in self.objects:
            key = (ob[0], ob[1], ob[2])
            if key not in self.deleted:
                yield (key, *ob[3:9])
        for key, (t, x, y, z, r) in self.added.items():
            if key not in self.deleted:
                yield key, brick.OBJ_PRODUCT, t, x, y, z, r

    def _upload_objects(self):
        """Queue the object markers for the GPU; the upload happens in draw(), where the GL context
        is current (clicks and undo arrive outside paintGL)."""
        self._objects_dirty = True
        self.update()

    def _flush_objects(self):
        if not self.ren or not getattr(self, '_objects_dirty', False):
            return
        self._objects_dirty = False
        rows = [[x + 0.5, y + 0.5, -(z + 0.5), *(NEW_OBJ_COLOR if key[0] == 'new' else
                                                  OBJ_COLORS.get(kind, (0.8, 0.8, 0.8)))]
                for key, kind, _t, x, y, z, _r in self.live_objects()]
        self.ren.set_objects(np.array(rows, dtype=np.float32).reshape(-1, 6))

    def product_name(self, type_id) -> str:
        pt = self.doc.products.get(type_id) if self.doc else None
        return f'{pt.name} (#{type_id})' if pt else f'#{type_id}'

    def draw(self, ctx, w, h):
        sky = (0.61, 0.78, 0.92)
        ctx.clear(*sky, 1.0, depth=1.0)
        if not self.ren or not self.doc:
            return
        self._flush_objects()
        self._rebuild(budget=0.012)
        mvp = self.cam.proj(w / max(h, 1)) @ self.cam.view()
        boxes = []
        b = self._target_box()
        if b:
            boxes.append(b)
        if self.start_pos and self.doc.is_world:
            sp = self.start_pos
            boxes.append(((sp[0] - 0.3, sp[1], -sp[2] - 0.3), (0.6, 2.2, 0.6), (0.2, 1.0, 0.4, 1.0)))
        boxes.append(((0, 0, -self.doc.depth), (self.doc.width, self.doc.height, self.doc.depth), (0.35, 0.47, 0.67, 1)))
        fog = (16 * self.radius, 16 * self.radius * 2.5) if self.doc.is_world else (1e6, 2e6)
        self.ren.render(mvp, sky, fog, self.show_objects, boxes)

    def _rebuild(self, budget: float):
        st = self.store
        if not st.dirty:
            return
        t0 = time.perf_counter()
        tx, tz = self.cam.target[0] / 16, -self.cam.target[2] / 16
        order = sorted(st.dirty, key=lambda k: (k[0] - tx) ** 2 + (k[1] - tz) ** 2)
        for key in order:
            arr = st.regions.get(key)
            st.dirty.discard(key)
            if arr is None:
                self.ren.remove(key)
                continue
            mesh = mesh_region(arr, st.neighbours(key), key[0] * 16, key[1] * 16, self.lut, self.show_collision)
            self.ren.upload(key, mesh)
            if time.perf_counter() - t0 > budget:
                break

    def tick(self, dt):
        moved = self._move_keys(dt)
        busy = bool(self.store and self.store.dirty)
        return moved or busy

    # ------------------------------------------------------------------ streaming
    def ensure_regions(self):
        if not self.doc or not self.doc.is_world:
            return
        cx, cz = int(self.cam.target[0] // 16), int(-self.cam.target[2] // 16)
        R = self.radius
        want = []
        for dz in range(-R, R + 1):
            for dx in range(-R, R + 1):
                rx, rz = cx + dx, cz + dz
                if 0 <= rx < self.doc.rx and 0 <= rz < self.doc.rz and dx * dx + dz * dz <= R * R + 1:
                    if (rx, rz) not in self.store.regions:
                        want.append((dx * dx + dz * dz, rx, rz))
        for _, rx, rz in sorted(want)[:64]:
            self.store.put((rx, rz), self.doc.region_array(rx, rz))
        for key in list(self.store.regions):
            dx, dz = key[0] - cx, key[1] - cz
            if key not in self.store.modified and dx * dx + dz * dz > (R + 3) ** 2:
                self.store.drop(key)
                if self.ren:
                    self.ren.remove(key)
        if want:
            self.update()

    def camera_changed(self):
        self.ensure_regions()
        self.camera_moved.emit(self.cam.target[0], -self.cam.target[2])

    def set_radius(self, r):
        self.radius = r
        self.ensure_regions()
        self.update()

    def set_show_collision(self, v):
        self.show_collision = v
        if self.store:
            self.store.dirty.update(self.store.regions)
        self.update()

    def jump_to(self, x, z):
        y = self.store.top_y(int(x), int(z)) if self.store else -1
        self.cam.target = np.array([x, y + 1 if y >= 0 else self.cam.target[1], -z])
        self.camera_changed()
        self.update()

    # ------------------------------------------------------------------ input
    def set_tool(self, tool):
        self.tool = tool
        self.box_start = None
        if tool == 'view':
            self.rotate_button, self.pan_button = Qt.LeftButton, Qt.RightButton
        else:
            self.rotate_button, self.pan_button = Qt.RightButton, Qt.MiddleButton
        self.update()

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            if e.key() == Qt.Key_Z:
                self.undo()
            elif e.key() == Qt.Key_Y:
                self.redo()
            return
        if e.key() == Qt.Key_R and self.tool == 'product':
            self.rotate_product()
            return
        self.keys.add(e.key())

    def rotate_product(self):
        self.rot = (self.rot + 1) % 4
        self.message.emit(f'설치물 방향: {self.rot * 90}°')
        self.update()

    def keyReleaseEvent(self, e):
        self.keys.discard(e.key())

    def focusOutEvent(self, e):
        self.keys.clear()
        super().focusOutEvent(e)

    def _move_keys(self, dt) -> bool:
        if not self.keys:
            return False
        fwd = self.cam.forward_flat()
        right = np.cross(fwd, [0, 1, 0])
        mv = np.zeros(3)
        k = self.keys
        if Qt.Key_W in k: mv += fwd
        if Qt.Key_S in k: mv -= fwd
        if Qt.Key_D in k: mv += right
        if Qt.Key_A in k: mv -= right
        if Qt.Key_E in k: mv[1] += 1
        if Qt.Key_Q in k: mv[1] -= 1
        n = np.linalg.norm(mv)
        if not n:
            return False
        self.cam.target = self.cam.target + mv / n * max(8.0, self.cam.dist) * dt
        self.camera_changed()
        return True

    def on_press(self, e):
        if e.button() == Qt.LeftButton:
            self._down = e.position()

    def on_release(self, e):
        if e.button() == Qt.LeftButton and self._down is not None:
            moved = (e.position() - self._down).manhattanLength()
            self._down = None
            if moved < 5 and self.tool != 'view':
                self.act(e.position())

    def on_move(self, e):
        self._hover(e.position())

    def _pick(self, pos):
        if not self.store:
            return None
        dpr = 1.0
        o, d = self.cam.ray(pos.x() * dpr, pos.y() * dpr, self.width() * dpr, self.height() * dpr)
        hit = raycast(self.store, o, d)
        if hit is None and not self.doc.is_world:
            hit = ground_hit(o, d)
        return hit

    def _pick_object(self, pos):
        """The live object (tuple from live_objects) under the mouse, or None."""
        o, d = self.cam.ray(pos.x(), pos.y(), self.width(), self.height())
        best, best_t = None, 1e9
        for ob in self.live_objects():
            c = np.array([ob[3] + 0.5, ob[4] + 0.5, -(ob[5] + 0.5)])
            t = float(np.dot(c - o, d))
            if t <= 0:
                continue
            if np.linalg.norm(o + d * t - c) < 0.5 and t < best_t:
                best, best_t = ob, t
        return best

    def _object_text(self, ob) -> str:
        key, kind, t, x, y, z, r = ob
        what = '나무' if kind == brick.OBJ_TREE else '설치물 ' + self.product_name(t)
        return f'{what} ({x}, {y}, {z}) {r * 90}°' + ('  · 새로 놓음' if key[0] == 'new' else '')

    def _hover(self, pos):
        if not self.doc:
            return
        if self.tool == 'object':
            ob = self._pick_object(pos)
            self.hover = None
            if ob is not None:
                self.hover_text.emit(self._object_text(ob))
            self.update()
            return
        hit = self._pick(pos)
        if not hit:
            self.hover = None
            self.update()
            return
        (x, y, z), n = hit
        outward = self.tool in ('place', 'prefab', 'product') or (self.tool == 'box' and self.box_mode == 'fill')
        self.hover = (x + n[0], y + n[1], z + n[2]) if outward else (x, y, z)
        if self.tool == 'product':
            if self.product is None:
                self.hover_text.emit('오른쪽 "설치물" 목록에서 놓을 것을 고르세요')
            else:
                ok, why, _ = self._product_check(self.hover)
                self.hover_text.emit(f'{self.product_name(self.product)}  {self.rot * 90}° (R: 돌리기)  '
                                     + ('· 클릭해서 놓기' if ok else '· ' + why))
            self.update()
            return
        v = self.store.get(x, y, z) if y >= 0 else 0
        self.hover_text.emit(f'({x}, {y}, {z})  {self.lut.label(v) if y >= 0 else "바닥"}'
                             + ('  · 상자: 두 번째 점을 클릭' if self.box_start else ''))
        self.update()

    def _target_box(self):
        h = self.hover
        if h is None or self.tool in ('view', 'object'):
            return None
        x0, y0, z0 = x1, y1, z1 = h
        if self.tool == 'prefab' and self.prefab:
            pw, ph, pd, _ = self.prefab
            x1, y1, z1 = x0 + pw - 1, y0 + ph - 1, z0 + pd - 1
        if self.tool == 'product' and self.product is not None and self.doc and self.doc.is_world:
            ok, _, cells = self._product_check(h)
            cells = cells or [h]
            x0, y0, z0 = (min(c[i] for c in cells) for i in range(3))
            x1, y1, z1 = (max(c[i] for c in cells) for i in range(3))
            lo, size = game_box_to_render(x0, y0, z0, x1, y1, z1)
            return lo, size, (0.4, 1.0, 0.5, 1.0) if ok else (1.0, 0.35, 0.35, 1.0)
        if self.tool == 'box' and self.box_start:
            s = self.box_start
            x0, x1 = min(s[0], h[0]), max(s[0], h[0])
            y0, y1 = min(s[1], h[1]), max(s[1], h[1])
            z0, z1 = min(s[2], h[2]), max(s[2], h[2])
        lo, size = game_box_to_render(x0, y0, z0, x1, y1, z1)
        red = self.tool == 'erase' or (self.tool == 'box' and self.box_mode == 'erase')
        return lo, size, (1.0, 0.35, 0.35, 1.0) if red else (1.0, 1.0, 0.2, 1.0)

    # ------------------------------------------------------------------ actions
    def act(self, pos):
        if self.tool == 'object':
            ob = self._pick_object(pos)
            if ob is not None:
                self.delete_object(ob[0])
            return
        hit = self._pick(pos)
        if not hit:
            return
        (x, y, z), n = hit
        adj = (x + n[0], y + n[1], z + n[2])
        t = self.tool
        if t == 'product':
            self.place_product(adj)
        elif t == 'place':
            self.commit([(*adj, self.selected)])
        elif t == 'erase' and y >= 0:
            self.commit([(x, y, z, 0)])
        elif t == 'paint' and y >= 0 and self.store.get(x, y, z):
            self.commit([(x, y, z, self.selected)])
        elif t == 'pick' and y >= 0:
            v = self.store.get(x, y, z)
            if v:
                self.selected = v
                self.picked.emit(v)
        elif t == 'box':
            cell = adj if self.box_mode == 'fill' else (x, y, z)
            if self.box_start is None:
                self.box_start = cell
                return
            s, self.box_start = self.box_start, None
            xs, ys, zs = sorted((s[0], cell[0])), sorted((s[1], cell[1])), sorted((s[2], cell[2]))
            val = 0 if self.box_mode == 'erase' else self.selected
            n_cells = (xs[1] - xs[0] + 1) * (ys[1] - ys[0] + 1) * (zs[1] - zs[0] + 1)
            if n_cells > 4_000_000:
                self.message.emit('상자가 너무 큽니다 (400만 칸 이하)')
                return
            self.commit([(xx, yy, zz, val) for yy in range(ys[0], ys[1] + 1)
                         for zz in range(zs[0], zs[1] + 1) for xx in range(xs[0], xs[1] + 1)])
        elif t == 'prefab':
            if not self.prefab:
                self.message.emit('먼저 붙일 프리팹을 고르세요')
                return
            pw, ph, pd, groups = self.prefab
            ch = []
            for (gx, gz), arr in groups.items():
                ys_, zs_, xs_ = np.nonzero(arr) if self.skip_air else np.indices(arr.shape).reshape(3, -1)
                vals = arr[ys_, zs_, xs_]
                ch += [(adj[0] + gx * 16 + int(a), adj[1] + int(b), adj[2] + gz * 16 + int(c), int(v))
                       for b, c, a, v in zip(ys_, zs_, xs_, vals)]
            self.commit(ch)
        elif t == 'start' and self.doc.is_world and y >= 0:
            old = list(self.start_pos) if self.start_pos else None
            new = [x + 0.5, y + 1.1, z + 0.5]
            self._set_start(new)
            self._push(('start', old, new))
            self.message.emit('시작 위치: ({:.1f}, {:.1f}, {:.1f})'.format(*new))
        self._hover(pos)

    def commit(self, changes):
        done, skipped = [], 0
        for x, y, z, v in changes:
            old = self.store.set(x, y, z, v)
            if old < 0:
                skipped += 1
            elif old != v:
                done.append((x, y, z, old, v))
        if skipped:
            self.message.emit(f'{skipped:,}칸은 범위 밖이거나 아직 불러오지 않은 영역이라 건너뛰었습니다')
        if done:
            self._push(('bricks', done))
        self.update()

    def _set_start(self, p):
        self.start_pos = p
        self.start_changed = True
        self.update()

    # ------------------------------------------------------------------ products
    def _loaded(self, x, y, z) -> bool:
        return self.store.inside(x, y, z) and (x >> 4, z >> 4) in self.store.regions

    def _product_check(self, cell):
        """(ok, reason, footprint cells) for placing self.product at cell with self.rot."""
        if self.product is None:
            return False, '놓을 설치물을 고르세요', []
        return product_check(self.doc, self.product, cell, self.rot, self.store.get, self._loaded,
                             list(self.live_objects()))

    def place_product(self, cell) -> bool:
        """Place self.product at cell: the object plus its collision bricks, as one undo step."""
        if self.product is None:
            self.message.emit('오른쪽 "설치물" 목록에서 놓을 것을 고르세요')
            return False
        ok, why, cells = self._product_check(cell)
        if not ok:
            self.message.emit(why)
            return False
        changes = []
        for c in cells:
            old = self.store.set(*c, COLLISION)
            if old >= 0 and old != COLLISION:
                changes.append((*c, old, COLLISION))
        self._new_n += 1
        key = ('new', self._new_n)
        info = (self.product, *cell, self.rot)
        self.added[key] = info
        self._upload_objects()
        self._push(('place', key, info, changes))
        self.message.emit(f'놓음: {self.product_name(self.product)} ({cell[0]}, {cell[1]}, {cell[2]}) {self.rot * 90}°')
        self.update()
        return True

    def delete_object(self, key):
        """Delete a tree/product; a product's collision bricks no other object needs are cleared."""
        ob = next((o for o in self.live_objects() if o[0] == key), None)
        if ob is None:
            return
        changes = []
        for c in collision_to_clear(self.doc, ob, list(self.live_objects())):
            if self._loaded(*c) and self.store.get(*c) == COLLISION:
                self.store.set(*c, 0)
                changes.append((*c, COLLISION, 0))
        self.deleted[key] = key
        self._upload_objects()
        self._push(('obj', key, changes))
        self.message.emit('지움: ' + self._object_text(ob))
        self.update()

    def _push(self, op):
        self.undo_stack.append(op)
        self.redo_stack.clear()
        self.history_changed.emit(True, False)

    def _apply(self, op, back):
        kind = op[0]
        if kind == 'bricks':
            for x, y, z, o, n in op[1]:
                self.store.set(x, y, z, o if back else n)
        elif kind == 'start':
            p = op[1] if back else op[2]
            if p:
                self._set_start(p)
        elif kind == 'obj':                    # ('obj', key, brick changes)
            if back:
                self.deleted.pop(op[1], None)
            else:
                self.deleted[op[1]] = op[1]
            for x, y, z, o, n in op[2]:
                self.store.set(x, y, z, o if back else n)
            self._upload_objects()
        elif kind == 'place':                  # ('place', key, info, brick changes)
            if back:
                self.added.pop(op[1], None)
            else:
                self.added[op[1]] = op[2]
            for x, y, z, o, n in op[3]:
                self.store.set(x, y, z, o if back else n)
            self._upload_objects()
        self.update()

    def undo(self):
        if self.undo_stack:
            op = self.undo_stack.pop()
            self._apply(op, True)
            self.redo_stack.append(op)
            self.history_changed.emit(bool(self.undo_stack), True)

    def redo(self):
        if self.redo_stack:
            op = self.redo_stack.pop()
            self._apply(op, False)
            self.undo_stack.append(op)
            self.history_changed.emit(True, bool(self.redo_stack))

    def dirty(self) -> bool:
        """Unsaved changes = edits on the undo history since the last open/save (every edit goes
        through _push; undoing them all brings the world back to the saved state)."""
        return bool(self.store and self.undo_stack)

    def object_edits(self):
        """(deleted (rx, rz, index) of document objects, added (type, x, y, z, rot)) for saving."""
        deleted = [k for k in self.deleted if k[0] != 'new']
        added = [info for k, info in self.added.items() if k not in self.deleted]
        return deleted, added

    def mark_saved(self):
        """After a save the document holds the edits: object indices changed, so take the objects
        from it again and start a fresh undo history."""
        if self.store:
            self.store.modified.clear()
        self.deleted.clear()
        self.added.clear()
        self.start_changed = False
        if self.doc:
            self.objects = self.doc.objects()
            self._upload_objects()
        self.undo_stack, self.redo_stack = [], []
        self.history_changed.emit(False, False)
        self.update()
