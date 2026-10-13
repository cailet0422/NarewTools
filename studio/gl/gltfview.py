"""glTF 2.0 (GLB from narew.convert) renderer: node hierarchy, skins, animations, textures."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from narew import gltf as G

from .mathx import quat_to_mat, slerp, translate, scale

MAX_JOINTS = 160
_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}

VS = '''
#version 330
uniform mat4 viewproj; uniform mat4 model; uniform int skinned;
uniform mat4 joints[%d];
uniform vec4 uv_xform;
in vec3 in_pos; in vec3 in_nrm; in vec2 in_uv; in vec4 in_joints; in vec4 in_weights;
out vec3 v_nrm; out vec2 v_uv;
void main() {
    mat4 m = model;
    if (skinned == 1) {
        m = in_weights.x * joints[int(in_joints.x)] + in_weights.y * joints[int(in_joints.y)]
          + in_weights.z * joints[int(in_joints.z)] + in_weights.w * joints[int(in_joints.w)];
    }
    gl_Position = viewproj * m * vec4(in_pos, 1.0);
    v_nrm = mat3(m) * in_nrm;
    v_uv = in_uv * uv_xform.zw + uv_xform.xy;
}
''' % MAX_JOINTS
FS = '''
#version 330
uniform sampler2D tex; uniform int has_tex; uniform vec4 base_color; uniform float alpha_cut; uniform int wire;
in vec3 v_nrm; in vec2 v_uv; out vec4 frag;
void main() {
    vec4 c = base_color;
    if (has_tex == 1) c *= texture(tex, v_uv);
    if (c.a < alpha_cut) discard;
    vec3 n = normalize(v_nrm);
    float l = 0.45 + 0.55 * max(dot(n, normalize(vec3(0.4, 0.8, 0.5))), 0.0) + 0.15 * max(dot(n, vec3(-0.5, 0.2, -0.6)), 0.0);
    frag = wire == 1 ? vec4(0.9, 0.95, 1.0, 1.0) : vec4(c.rgb * l, c.a);
}
'''
LINE_VS = '''
#version 330
uniform mat4 viewproj; in vec3 in_pos;
void main() { gl_Position = viewproj * vec4(in_pos, 1.0); }
'''
LINE_FS = '''
#version 330
uniform vec4 color; out vec4 frag;
void main() { frag = color; }
'''


def accessor(doc: G.Document, index: int) -> np.ndarray:
    acc = doc.json['accessors'][index]
    n = _NCOMP[acc['type']]
    dt = np.dtype(_DTYPE[acc['componentType']])
    count = acc['count']
    if 'bufferView' not in acc:
        arr = np.zeros((count, n), dtype=dt)
    else:
        data, stride = doc.view_bytes(acc['bufferView'])
        off = acc.get('byteOffset', 0)
        elem = n * dt.itemsize
        if not stride or stride == elem:
            arr = np.frombuffer(data, dtype=dt, count=count * n, offset=off).reshape(count, n)
        else:
            raw = np.frombuffer(data, dtype=np.uint8, offset=off)
            arr = np.stack([np.frombuffer(raw[i * stride:i * stride + elem].tobytes(), dtype=dt) for i in range(count)])
    if acc.get('normalized') and dt.kind in 'iu':
        arr = np.maximum(arr.astype(np.float32) / float(np.iinfo(dt).max), -1.0)
    if 'sparse' in acc:
        arr = np.array(doc.read_accessor(index), dtype=np.float32).reshape(count, n)
    return arr.reshape(count, n) if n > 1 else arr.reshape(count)


class Node:
    __slots__ = ('index', 'name', 't', 'r', 's', 'matrix', 'children', 'parent', 'mesh', 'skin', 'world')

    def __init__(self, i, js):
        self.index = i
        self.name = js.get('name', f'node{i}')
        self.matrix = None
        if 'matrix' in js:
            self.matrix = np.array(js['matrix'], dtype=np.float64).reshape(4, 4).T
        self.t = np.array(js.get('translation', [0, 0, 0]), dtype=np.float64)
        self.r = np.array(js.get('rotation', [0, 0, 0, 1]), dtype=np.float64)
        self.s = np.array(js.get('scale', [1, 1, 1]), dtype=np.float64)
        self.children = js.get('children', [])
        self.parent = None
        self.mesh = js.get('mesh')
        self.skin = js.get('skin')
        self.world = np.identity(4)

    def local(self):
        if self.matrix is not None:
            return self.matrix
        return translate(*self.t) @ quat_to_mat(self.r) @ scale(*self.s)


class Animation:
    def __init__(self, doc, js):
        self.name = js.get('name', 'animation')
        self.channels = []
        self.duration = 0.0
        for ch in js.get('channels', []):
            tgt = ch.get('target', {})
            if 'node' not in tgt or tgt.get('path') not in ('translation', 'rotation', 'scale'):
                continue
            smp = js['samplers'][ch['sampler']]
            times = accessor(doc, smp['input']).astype(np.float64).reshape(-1)
            vals = accessor(doc, smp['output']).astype(np.float64)
            interp = smp.get('interpolation', 'LINEAR')
            if interp == 'CUBICSPLINE':
                vals = vals.reshape(len(times), 3, -1)[:, 1, :]
                interp = 'LINEAR'
            self.channels.append((tgt['node'], tgt['path'], times, vals.reshape(len(times), -1), interp))
            if len(times):
                self.duration = max(self.duration, float(times[-1]))

    def apply(self, nodes, t):
        for ni, path, times, vals, interp in self.channels:
            if not len(times):
                continue
            if t <= times[0]:
                v = vals[0]
            elif t >= times[-1]:
                v = vals[-1]
            else:
                k = int(np.searchsorted(times, t) - 1)
                t0, t1 = times[k], times[k + 1]
                u = 0.0 if interp == 'STEP' or t1 <= t0 else (t - t0) / (t1 - t0)
                if path == 'rotation':
                    v = slerp(vals[k], vals[k + 1], u)
                else:
                    v = vals[k] + (vals[k + 1] - vals[k]) * u
            n = nodes[ni]
            if path == 'translation':
                n.t = v
            elif path == 'rotation':
                n.r = v
            else:
                n.s = v


class GltfModel:
    """CPU-side scene from a .glb path."""

    def __init__(self, path: str):
        self.doc = doc = G.load(path)
        js = doc.json
        self.nodes = [Node(i, n) for i, n in enumerate(js.get('nodes', []))]
        for n in self.nodes:
            for c in n.children:
                self.nodes[c].parent = n.index
        scene = js.get('scenes', [{}])[js.get('scene', 0)] if js.get('scenes') else {}
        self.roots = scene.get('nodes') or [n.index for n in self.nodes if n.parent is None]
        self.skins = []
        for sk in js.get('skins', []):
            ibm = accessor(doc, sk['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1) \
                if 'inverseBindMatrices' in sk else np.tile(np.identity(4), (len(sk['joints']), 1, 1))
            self.skins.append((sk['joints'], ibm.astype(np.float64)))
        self.animations = [Animation(doc, a) for a in js.get('animations', [])]
        self.rest = [(n.t.copy(), n.r.copy(), n.s.copy()) for n in self.nodes]
        self.prims = []          # (node index, dict of arrays, material dict)
        for n in self.nodes:
            if n.mesh is None:
                continue
            for p in js['meshes'][n.mesh].get('primitives', []):
                if p.get('mode', 4) != 4:
                    continue
                a = p['attributes']
                pos = accessor(doc, a['POSITION']).astype(np.float32)
                cnt = len(pos)
                nrm = accessor(doc, a['NORMAL']).astype(np.float32) if 'NORMAL' in a else np.tile([0, 1, 0], (cnt, 1)).astype(np.float32)
                uv = accessor(doc, a['TEXCOORD_0']).astype(np.float32) if 'TEXCOORD_0' in a else np.zeros((cnt, 2), np.float32)
                jo = accessor(doc, a['JOINTS_0']).astype(np.float32) if 'JOINTS_0' in a else np.zeros((cnt, 4), np.float32)
                we = accessor(doc, a['WEIGHTS_0']).astype(np.float32) if 'WEIGHTS_0' in a else np.zeros((cnt, 4), np.float32)
                idx = accessor(doc, p['indices']).astype(np.uint32) if 'indices' in p else np.arange(cnt, dtype=np.uint32)
                self.prims.append((n.index, {'pos': pos, 'nrm': nrm, 'uv': uv, 'joints': jo, 'weights': we,
                                             'idx': idx}, self._material(p.get('material'))))
        self.update(None, 0.0)

    def _material(self, mi) -> dict:
        out = {'color': (1, 1, 1, 1), 'image': None, 'cut': 0.05, 'blend': False, 'uv': (0, 0, 1, 1), 'double': True}
        if mi is None:
            return out
        m = self.doc.json['materials'][mi]
        pbr = m.get('pbrMetallicRoughness', {})
        out['color'] = tuple(pbr.get('baseColorFactor', [1, 1, 1, 1]))
        mode = m.get('alphaMode', 'OPAQUE')
        out['cut'] = m.get('alphaCutoff', 0.5) if mode == 'MASK' else (0.02 if mode == 'BLEND' else 0.0)
        out['blend'] = mode == 'BLEND'
        bt = pbr.get('baseColorTexture')
        if bt is not None:
            tx = self.doc.json['textures'][bt['index']]
            src = tx.get('source')
            if src is not None:
                data, _ = self.doc.image_bytes(src)
                if data:
                    try:
                        out['image'] = Image.open(io.BytesIO(data)).convert('RGBA')
                    except Exception:
                        out['image'] = None
            xf = bt.get('extensions', {}).get('KHR_texture_transform')
            if xf:
                o, s = xf.get('offset', [0, 0]), xf.get('scale', [1, 1])
                out['uv'] = (o[0], o[1], s[0], s[1])
        return out

    def update(self, anim: Animation | None, t: float):
        if anim is None:
            for n, (tt, r, s) in zip(self.nodes, self.rest):
                n.t, n.r, n.s = tt, r, s
        else:
            anim.apply(self.nodes, t)

        def walk(i, parent):
            n = self.nodes[i]
            n.world = parent @ n.local()
            for c in n.children:
                walk(c, n.world)
        for r in self.roots:
            walk(r, np.identity(4))

    def joint_matrices(self, skin_index: int) -> np.ndarray:
        joints, ibm = self.skins[skin_index]
        return np.stack([self.nodes[j].world @ ibm[k] for k, j in enumerate(joints)])

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for ni, a, _ in self.prims:
            n = self.nodes[ni]
            if n.skin is not None:
                jm = self.joint_matrices(n.skin)
                w = a['weights']
                j = a['joints'].astype(int)
                p4 = np.c_[a['pos'], np.ones(len(a['pos']))]
                m = sum(w[:, k, None, None] * jm[j[:, k]] for k in range(4))
                pts = np.einsum('nij,nj->ni', m, p4)[:, :3]
            else:
                pts = (np.c_[a['pos'], np.ones(len(a['pos']))] @ n.world.T)[:, :3]
            if len(pts):
                lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
        if not np.isfinite(lo).all():
            return np.array([-1.0, 0, -1]), np.array([1.0, 2, 1])
        return lo, hi


class ModelRenderer:
    def __init__(self, ctx):
        import moderngl
        self.ctx, self.mgl = ctx, moderngl
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.lines = ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.model: GltfModel | None = None
        self.gpu = []
        self.grid = self._grid()
        self.wire = False
        self.show_skeleton = False
        self.bone_buf = ctx.buffer(reserve=MAX_JOINTS * 2 * 12)
        self.bone_vao = ctx.vertex_array(self.lines, [(self.bone_buf, '3f', 'in_pos')])

    def _grid(self):
        pts = []
        for i in range(-10, 11):
            pts += [(i, 0, -10), (i, 0, 10), (-10, 0, i), (10, 0, i)]
        buf = self.ctx.buffer(np.array(pts, dtype='f4').tobytes())
        return self.ctx.vertex_array(self.lines, [(buf, '3f', 'in_pos')]), buf, len(pts)

    def release(self):
        for g in self.gpu:
            for o in (g['vao'], g['vbo'], g['ibo'], g.get('tex')):
                if o is not None:
                    o.release()
        self.gpu = []

    def set_model(self, model: GltfModel | None):
        self.release()
        self.model = model
        if model is None:
            return
        for ni, a, mat in model.prims:
            v = np.concatenate([a['pos'], a['nrm'], a['uv'], a['joints'], a['weights']], axis=1).astype('f4')
            vbo = self.ctx.buffer(v.tobytes())
            ibo = self.ctx.buffer(a['idx'].astype('u4').tobytes())
            vao = self.ctx.vertex_array(self.prog, [(vbo, '3f 3f 2f 4f 4f', 'in_pos', 'in_nrm', 'in_uv',
                                                     'in_joints', 'in_weights')], ibo, index_element_size=4)
            tex = None
            if mat['image'] is not None:
                im = mat['image']
                tex = self.ctx.texture(im.size, 4, im.tobytes())
                tex.build_mipmaps()
                tex.filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR)
                tex.repeat_x = tex.repeat_y = True
            self.gpu.append({'node': ni, 'vao': vao, 'vbo': vbo, 'ibo': ibo, 'tex': tex, 'mat': mat})

    def render(self, viewproj: np.ndarray, grid_y: float = 0.0, grid_scale: float = 1.0):
        ctx = self.ctx
        vp = viewproj.T.astype('f4').tobytes()
        ctx.enable(self.mgl.DEPTH_TEST)
        ctx.disable(self.mgl.CULL_FACE)
        # grid
        self.lines['viewproj'].write((viewproj @ translate(0, grid_y, 0) @ scale(grid_scale, 1, grid_scale)).T.astype('f4').tobytes())
        self.lines['color'].value = (0.25, 0.28, 0.34, 1.0)
        self.grid[0].render(self.mgl.LINES)
        if not self.model:
            return
        p = self.prog
        p['viewproj'].write(vp)
        p['wire'].value = 1 if self.wire else 0
        ctx.wireframe = self.wire
        joint_cache = {}
        order = sorted(self.gpu, key=lambda g: g['mat']['blend'])
        for g in order:
            node = self.model.nodes[g['node']]
            mat = g['mat']
            if node.skin is not None:
                jm = joint_cache.get(node.skin)
                if jm is None:
                    jm = joint_cache[node.skin] = self.model.joint_matrices(node.skin)
                buf = np.zeros((MAX_JOINTS, 4, 4), dtype='f4')
                buf[:min(len(jm), MAX_JOINTS)] = jm[:MAX_JOINTS]
                p['joints'].write(np.ascontiguousarray(buf.transpose(0, 2, 1)).tobytes())
                p['skinned'].value = 1
                p['model'].write(np.identity(4, dtype='f4').tobytes())
            else:
                p['skinned'].value = 0
                p['model'].write(node.world.T.astype('f4').tobytes())
            p['base_color'].value = tuple(mat['color'])
            p['alpha_cut'].value = float(mat['cut'])
            p['uv_xform'].value = tuple(mat['uv'])
            if g['tex'] is not None:
                g['tex'].use(0)
                p['tex'].value = 0
                p['has_tex'].value = 1
            else:
                p['has_tex'].value = 0
            if mat['blend']:
                ctx.enable(self.mgl.BLEND)
                ctx.blend_func = self.mgl.SRC_ALPHA, self.mgl.ONE_MINUS_SRC_ALPHA
            g['vao'].render()
            if mat['blend']:
                ctx.disable(self.mgl.BLEND)
        ctx.wireframe = False
        if self.show_skeleton and self.model.skins:
            segs = []
            for joints, _ in self.model.skins:
                js = set(joints)
                for j in joints:
                    n = self.model.nodes[j]
                    if n.parent is not None and n.parent in js:
                        segs += [self.model.nodes[n.parent].world[:3, 3], n.world[:3, 3]]
            if segs:
                arr = np.array(segs[:MAX_JOINTS * 2], dtype='f4')
                self.bone_buf.write(arr.tobytes())
                ctx.disable(self.mgl.DEPTH_TEST)
                self.lines['viewproj'].write(vp)
                self.lines['color'].value = (1.0, 0.85, 0.2, 1.0)
                self.bone_vao.render(self.mgl.LINES, vertices=len(arr))
                ctx.enable(self.mgl.DEPTH_TEST)

    def stats(self) -> dict:
        if not self.model:
            return {'meshes': 0, 'tris': 0, 'bones': 0}
        tris = sum(len(a['idx']) // 3 for _, a, _ in self.model.prims)
        bones = sum(len(j) for j, _ in self.model.skins)
        return {'meshes': len(self.model.prims), 'tris': tris, 'bones': bones}


def frame_for(model: GltfModel):
    lo, hi = model.bounds()
    return lo, hi, max(float(np.linalg.norm(hi - lo)), 0.1)


def fps_time(anim: Animation | None, t: float, loop: bool = True) -> float:
    if anim is None or anim.duration <= 0:
        return 0.0
    return math.fmod(t, anim.duration) if loop else min(t, anim.duration)
