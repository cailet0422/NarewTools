"""Voxel world: storage, numpy face mesher, moderngl renderer, ray picking.

Game space: x right, y up, z forward (left-handed).  Render space mirrors z so the world is not
mirrored on screen: a brick at game (x, y, z) spans render [x, x+1] x [y, y+h] x [-(z+1), -z].
Region arrays are uint8[H, 16(z), 16(x)] (the file layout (y*16 + z)*16 + x).
"""
from __future__ import annotations

import numpy as np

SIDE = 16
TRANSPARENT_CATS = {'glass', 'water', 'leaf', 'plant', 'snow', 'ice'}
FACE_NAMES = ('right', 'left', 'top', 'bottom', 'back', 'front')        # render +X -X +Y -Y +Z -Z
FACE_SHADE = np.array([0.80, 0.80, 1.0, 0.5, 0.70, 0.70], dtype=np.float32)
# unit-cube corners per face in render space (z measured from the brick's min render z), CCW outside
FACE_CORNERS = np.array([
    [[1, 0, 1], [1, 0, 0], [1, 1, 0], [1, 1, 1]],
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
    [[0, 1, 1], [1, 1, 1], [1, 1, 0], [0, 1, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
    [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
    [[1, 0, 0], [0, 0, 0], [0, 1, 0], [1, 1, 0]],
], dtype=np.float32)
CORNER_UV = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
NEIGHBOUR = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, -1), (0, 0, 1)]   # game dx, dy, dz
EPS = 0.5 / 2048


class BrickLUT:
    """Per-id lookup tables built from the brick table."""

    def __init__(self, bricks: list[dict]):
        self.tiles = np.full((256, 6), -1, dtype=np.int16)
        self.height = np.ones(256, dtype=np.float32)
        self.clear = np.zeros(256, dtype=bool)
        self.solid = np.zeros(256, dtype=bool)
        self.color = np.ones((256, 3), dtype=np.float32)
        self.known = np.zeros(256, dtype=bool)
        self.names = {}
        self.color[1:255] = (1.0, 0.2, 1.0)            # unknown ids show magenta
        for b in bricks:
            i = b['id']
            if not 0 < i < 255:
                continue
            self.known[i] = True
            self.names[i] = b.get('label') or b.get('name') or f'#{i}'
            self.tiles[i] = [b['tiles'].get(f, -1) for f in FACE_NAMES]
            self.height[i] = max(0.1, min(1.0, (b.get('height') or 10) / 10))
            try:
                argb = int(b.get('color', '0xFFFFFFFF'), 16)
            except ValueError:
                argb = 0xFFFFFFFF
            self.color[i] = ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255)
            self.color[i] /= 255.0
            cat = (b.get('category') or '').lower()
            transparent = cat in TRANSPARENT_CATS or str(b.get('alpha', '0')) != '0' or 240 <= i <= 254
            self.clear[i] = transparent
            self.solid[i] = not transparent and self.height[i] >= 1
        self.color[255] = (1.0, 0.3, 1.0)
        self.clear[255] = True

    def label(self, i: int) -> str:
        if i == 0:
            return '공기'
        if i == 255:
            return '충돌 채움(보이지 않음)'
        return f'{self.names[i]} (#{i})' if i in self.names else f'알 수 없는 블록 #{i}'


def mesh_region(arr: np.ndarray, nb: dict, ox: int, oz: int, lut: BrickLUT,
                show_collision: bool = False) -> dict:
    """Faces of one region against its neighbours. nb keys 'xm','xp','zm','zp' -> (H,16) or None.

    Returns {'opaque': (vertices float32[n,8], indices uint32), 'clear': (...)}; vertex = pos3 uv2 rgb3.
    """
    H = arr.shape[0]
    P = np.zeros((H + 2, SIDE + 2, SIDE + 2), dtype=np.uint8)
    P[1:-1, 1:-1, 1:-1] = arr
    if nb.get('xm') is not None:
        P[1:-1, 1:-1, 0] = nb['xm']
    if nb.get('xp') is not None:
        P[1:-1, 1:-1, -1] = nb['xp']
    if nb.get('zm') is not None:
        P[1:-1, 0, 1:-1] = nb['zm']
    if nb.get('zp') is not None:
        P[1:-1, -1, 1:-1] = nb['zp']
    visible = np.ones(256, dtype=bool)
    visible[0] = False
    visible[255] = show_collision
    hides = lut.solid & visible                      # a neighbour that hides the face
    vis = visible[arr]
    clear_cur = lut.clear[arr]
    partial = lut.height[arr] < 1
    out = {False: [], True: []}
    for f, (dx, dy, dz) in enumerate(NEIGHBOUR):
        n = P[1 + dy:1 + dy + H, 1 + dz:1 + dz + SIDE, 1 + dx:1 + dx + SIDE]
        mask = vis & ~hides[n] & ~((n == arr) & clear_cur)
        if f == 2:
            mask |= vis & partial
        if not mask.any():
            continue
        ys, zs, xs = np.nonzero(mask)
        ids = arr[ys, zs, xs]
        h = lut.height[ids]
        x0 = (xs + ox).astype(np.float32)
        y0 = ys.astype(np.float32)
        z0 = -(zs + oz + 1).astype(np.float32)
        c = FACE_CORNERS[f]
        pos = np.empty((len(ids), 4, 3), dtype=np.float32)
        pos[:, :, 0] = x0[:, None] + c[None, :, 0]
        pos[:, :, 1] = y0[:, None] + c[None, :, 1] * h[:, None]
        pos[:, :, 2] = z0[:, None] + c[None, :, 2]
        tile = lut.tiles[ids, f].astype(np.int32)
        textured = tile >= 0
        tx, ty = (tile % 16).astype(np.float32), (tile // 16).astype(np.float32)
        u0, u1 = tx / 16 + EPS, (tx + 1) / 16 - EPS
        vb, vt = (ty + 1) / 16 - EPS, ty / 16 + EPS           # bottom / top of the tile (image rows)
        cu, cv = CORNER_UV[:, 0], CORNER_UV[:, 1]
        uv = np.empty((len(ids), 4, 2), dtype=np.float32)
        uv[:, :, 0] = u0[:, None] + (u1 - u0)[:, None] * cu[None, :]
        vspan = (vt - vb)[:, None] * (h[:, None] if f not in (2, 3) else 1.0)
        uv[:, :, 1] = vb[:, None] + vspan * cv[None, :]
        uv[~textured] = -1.0
        col = np.where(textured[:, None], 1.0, lut.color[ids]) * FACE_SHADE[f]
        col = np.repeat(col[:, None, :], 4, axis=1)
        verts = np.concatenate([pos, uv, col], axis=2).reshape(-1, 8)
        cl = lut.clear[ids]
        for flag in (False, True):
            sel = cl == flag
            if sel.any():
                out[flag].append(verts.reshape(len(ids), 4, 8)[sel].reshape(-1, 8))
    result = {}
    for flag, key in ((False, 'opaque'), (True, 'clear')):
        if out[flag]:
            v = np.concatenate(out[flag])
            nq = len(v) // 4
            b = (np.arange(nq, dtype=np.uint32) * 4)[:, None]
            idx = (b + np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)[None, :]).reshape(-1)
            result[key] = (np.ascontiguousarray(v, dtype=np.float32), idx)
    return result


class VoxelStore:
    """Loaded region arrays with edit tracking."""

    def __init__(self, width: int, depth: int, height: int):
        self.W, self.D, self.H = width, depth, height
        self.RX, self.RZ = width // SIDE, depth // SIDE
        self.regions: dict[tuple[int, int], np.ndarray] = {}
        self.modified: set[tuple[int, int]] = set()
        self.dirty: set[tuple[int, int]] = set()

    def inside(self, x, y, z) -> bool:
        return 0 <= x < self.W and 0 <= z < self.D and 0 <= y < self.H

    def put(self, key, arr):
        self.regions[key] = arr
        self.dirty.add(key)
        rx, rz = key
        for k in ((rx - 1, rz), (rx + 1, rz), (rx, rz - 1), (rx, rz + 1)):
            if k in self.regions:
                self.dirty.add(k)

    def drop(self, key):
        self.regions.pop(key, None)
        self.dirty.discard(key)

    def get(self, x, y, z) -> int:
        if not self.inside(x, y, z):
            return 0
        a = self.regions.get((x >> 4, z >> 4))
        return 0 if a is None else int(a[y, z & 15, x & 15])

    def set(self, x, y, z, v) -> int:
        """Returns the old value, or -1 if outside / not loaded."""
        if not self.inside(x, y, z):
            return -1
        key = (x >> 4, z >> 4)
        a = self.regions.get(key)
        if a is None:
            return -1
        old = int(a[y, z & 15, x & 15])
        if old != v:
            a[y, z & 15, x & 15] = v
            self.modified.add(key)
            self.dirty.add(key)
            lx, lz = x & 15, z & 15
            for cond, k in ((lx == 0, (key[0] - 1, key[1])), (lx == 15, (key[0] + 1, key[1])),
                            (lz == 0, (key[0], key[1] - 1)), (lz == 15, (key[0], key[1] + 1))):
                if cond and k in self.regions:
                    self.dirty.add(k)
        return old

    def neighbours(self, key) -> dict:
        rx, rz = key
        g = self.regions.get
        xm, xp, zm, zp = g((rx - 1, rz)), g((rx + 1, rz)), g((rx, rz - 1)), g((rx, rz + 1))
        return {'xm': xm[:, :, 15] if xm is not None else None, 'xp': xp[:, :, 0] if xp is not None else None,
                'zm': zm[:, 15, :] if zm is not None else None, 'zp': zp[:, 0, :] if zp is not None else None}

    def top_y(self, x, z) -> int:
        a = self.regions.get((x >> 4, z >> 4))
        if a is None:
            return -1
        col = a[:, z & 15, x & 15]
        nz = np.nonzero(col)[0]
        return int(nz[-1]) if len(nz) else -1


def raycast(store: VoxelStore, origin_r, dir_r, max_dist: float = 600.0, solid_test=None):
    """First non-air brick along a render-space ray.  Returns ((x,y,z), (nx,ny,nz)) in game space
    with the normal of the face that was entered, or None."""
    o = np.array([origin_r[0], origin_r[1], -origin_r[2]], dtype=np.float64)
    d = np.array([dir_r[0], dir_r[1], -dir_r[2]], dtype=np.float64)
    # advance to the volume
    lo, hi = np.zeros(3), np.array([store.W, store.H, store.D], dtype=np.float64)
    tmin, tmax = 0.0, max_dist
    entry_axis = -1
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if not lo[i] <= o[i] <= hi[i]:
                return None
        else:
            t1, t2 = (lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]
            if min(t1, t2) > tmin:
                tmin, entry_axis = min(t1, t2), i
            tmax = min(tmax, max(t1, t2))
    if tmin > tmax:
        return None
    p = o + d * (tmin + 1e-6)
    cell = np.clip(np.floor(p).astype(int), 0, hi.astype(int) - 1)
    step = np.where(d > 0, 1, -1)
    with np.errstate(divide='ignore'):
        tdelta = np.where(d != 0, np.abs(1.0 / d), np.inf)
        nxt = np.where(d > 0, cell + 1 - p, p - cell)
        tmax_ax = np.where(d != 0, nxt * tdelta, np.inf) + tmin
    normal = [0, 0, 0]
    if entry_axis >= 0:
        normal[entry_axis] = -int(step[entry_axis])
    normal = tuple(normal)
    t = tmin
    test = solid_test or (lambda v: v != 0)
    for _ in range(4096):
        x, y, z = int(cell[0]), int(cell[1]), int(cell[2])
        if store.inside(x, y, z) and test(store.get(x, y, z)):
            return (x, y, z), normal
        ax = int(np.argmin(tmax_ax))
        t = tmax_ax[ax]
        if t > tmax:
            return None
        cell[ax] += step[ax]
        tmax_ax[ax] += tdelta[ax]
        n = [0, 0, 0]
        n[ax] = -int(step[ax])
        normal = tuple(n)
    return None


def ground_hit(origin_r, dir_r):
    """Hit on the y=0 plane (for empty prefabs): returns game cell below the plane and +y normal."""
    if abs(dir_r[1]) < 1e-9:
        return None
    t = -origin_r[1] / dir_r[1]
    if t <= 0:
        return None
    p = np.asarray(origin_r) + np.asarray(dir_r) * t
    return (int(np.floor(p[0])), -1, int(np.floor(-p[2]))), (0, 1, 0)


# --------------------------------------------------------------------------- GPU side

VOXEL_VS = '''
#version 330
uniform mat4 mvp;
in vec3 in_pos; in vec2 in_uv; in vec3 in_col;
out vec2 v_uv; out vec3 v_col; out float v_depth;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); v_uv = in_uv; v_col = in_col; v_depth = gl_Position.w; }
'''
VOXEL_FS = '''
#version 330
uniform sampler2D atlas; uniform int clear_pass; uniform vec3 fog_color; uniform vec2 fog_range;
in vec2 v_uv; in vec3 v_col; in float v_depth; out vec4 frag;
void main() {
    vec4 c = v_uv.x < 0.0 ? vec4(1.0) : texture(atlas, v_uv);
    float a = 1.0;
    if (clear_pass == 0) { if (c.a < 0.5) discard; }
    else { a = c.a * 0.75; if (a < 0.03) discard; }
    vec3 rgb = c.rgb * v_col;
    float f = clamp((v_depth - fog_range.x) / max(fog_range.y - fog_range.x, 1.0), 0.0, 1.0);
    frag = vec4(mix(rgb, fog_color, f), a);
}
'''
LINE_VS = '''
#version 330
uniform mat4 mvp; in vec3 in_pos;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); }
'''
LINE_FS = '''
#version 330
uniform vec4 color; out vec4 frag;
void main() { frag = color; }
'''
INST_VS = '''
#version 330
uniform mat4 mvp; uniform float size;
in vec3 in_pos; in vec3 in_off; in vec3 in_color;
out vec3 v_color;
void main() { gl_Position = mvp * vec4(in_pos * size + in_off, 1.0); v_color = in_color; }
'''
INST_FS = '''
#version 330
in vec3 v_color; out vec4 frag;
void main() { frag = vec4(v_color, 1.0); }
'''

CUBE_LINES = np.array([
    [0, 0, 0], [1, 0, 0], [1, 0, 0], [1, 0, 1], [1, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 0],
    [0, 1, 0], [1, 1, 0], [1, 1, 0], [1, 1, 1], [1, 1, 1], [0, 1, 1], [0, 1, 1], [0, 1, 0],
    [0, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [1, 0, 1], [1, 1, 1], [0, 0, 1], [0, 1, 1]], dtype=np.float32)


def cube_triangles() -> np.ndarray:
    tris = []
    for f in range(6):
        c = FACE_CORNERS[f] - 0.5
        tris += [c[0], c[1], c[2], c[0], c[2], c[3]]
    return np.array(tris, dtype=np.float32)


class VoxelRenderer:
    def __init__(self, ctx):
        import moderngl
        self.ctx = ctx
        self.mgl = moderngl
        self.prog = ctx.program(vertex_shader=VOXEL_VS, fragment_shader=VOXEL_FS)
        self.lines = ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.inst = ctx.program(vertex_shader=INST_VS, fragment_shader=INST_FS)
        self.atlas = None
        self.meshes: dict = {}                 # key -> {'opaque': (vao, vbo, ibo), 'clear': ...}
        self.cube_vbo = ctx.buffer(cube_triangles().tobytes())
        self.line_vbo = ctx.buffer(reserve=24 * 12)
        self.line_vao = ctx.vertex_array(self.lines, [(self.line_vbo, '3f', 'in_pos')])
        self.obj_vao = None
        self.obj_count = 0

    def set_atlas(self, rgba_image):
        if self.atlas:
            self.atlas.release()
        w, h = rgba_image.size
        self.atlas = self.ctx.texture((w, h), 4, rgba_image.convert('RGBA').tobytes())
        self.atlas.filter = (self.mgl.NEAREST, self.mgl.NEAREST)
        self.atlas.repeat_x = self.atlas.repeat_y = False

    def upload(self, key, mesh: dict):
        self.remove(key)
        entry = {}
        for part in ('opaque', 'clear'):
            if part not in mesh:
                continue
            verts, idx = mesh[part]
            vbo = self.ctx.buffer(verts.tobytes())
            ibo = self.ctx.buffer(idx.tobytes())
            vao = self.ctx.vertex_array(self.prog, [(vbo, '3f 2f 3f', 'in_pos', 'in_uv', 'in_col')], ibo,
                                        index_element_size=4)
            entry[part] = (vao, vbo, ibo)
        self.meshes[key] = entry

    def remove(self, key):
        e = self.meshes.pop(key, None)
        if e:
            for vao, vbo, ibo in e.values():
                vao.release()
                vbo.release()
                ibo.release()

    def clear(self):
        for k in list(self.meshes):
            self.remove(k)
        self.set_objects(np.zeros((0, 6), np.float32))

    def set_objects(self, inst: np.ndarray):
        """inst rows: render-space center xyz + rgb."""
        if self.obj_vao:
            self.obj_vao.release()
            self.obj_buf.release()
            self.obj_vao = None
        self.obj_count = len(inst)
        if not len(inst):
            return
        self.obj_buf = self.ctx.buffer(np.ascontiguousarray(inst, dtype=np.float32).tobytes())
        self.obj_vao = self.ctx.vertex_array(self.inst, [(self.cube_vbo, '3f', 'in_pos'),
                                                         (self.obj_buf, '3f 3f/i', 'in_off', 'in_color')])

    def render(self, mvp: np.ndarray, fog_color=(0.61, 0.78, 0.92), fog_range=(1e6, 2e6),
               show_objects=True, boxes=()):
        ctx = self.ctx
        m = mvp.T.astype('f4').tobytes()
        ctx.enable(self.mgl.DEPTH_TEST | self.mgl.CULL_FACE)
        if self.atlas:
            self.atlas.use(0)
        self.prog['mvp'].write(m)
        self.prog['atlas'].value = 0
        self.prog['fog_color'].value = tuple(fog_color)
        self.prog['fog_range'].value = tuple(fog_range)
        self.prog['clear_pass'].value = 0
        ctx.disable(self.mgl.BLEND)
        for e in self.meshes.values():
            if 'opaque' in e:
                e['opaque'][0].render()
        if show_objects and self.obj_vao:
            self.inst['mvp'].write(m)
            self.inst['size'].value = 0.6
            self.obj_vao.render(instances=self.obj_count)
        ctx.enable(self.mgl.BLEND)
        ctx.blend_func = self.mgl.SRC_ALPHA, self.mgl.ONE_MINUS_SRC_ALPHA
        ctx.depth_mask = False
        self.prog['clear_pass'].value = 1
        for e in self.meshes.values():
            if 'clear' in e:
                e['clear'][0].render()
        ctx.depth_mask = True
        # boxes: (lo_render xyz, size xyz, rgba)
        if boxes:
            ctx.disable(self.mgl.DEPTH_TEST)
            self.lines['mvp'].write(m)
            for lo, size, rgba in boxes:
                pts = CUBE_LINES * np.asarray(size, np.float32) + np.asarray(lo, np.float32)
                self.line_vbo.write(pts.astype('f4').tobytes())
                self.lines['color'].value = tuple(rgba)
                self.line_vao.render(self.mgl.LINES)
            ctx.enable(self.mgl.DEPTH_TEST)
        ctx.disable(self.mgl.BLEND)


def game_box_to_render(x0, y0, z0, x1, y1, z1):
    """Inclusive game cell box -> (render lo, size)."""
    return (x0, y0, -(z1 + 1)), (x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1)
