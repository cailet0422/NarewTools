"""NarewEngine2 model/skeleton/animation <-> glTF 2.0 conversion.

Engine space (observed in NarewEngine2_Release64.dll, see docs/04-model-formats.md) is
left-handed, +Y up, row vectors, characters facing -Z.  glTF is right-handed, +Y up, column
vectors, assets facing +Z.  The mapping used everywhere here is a mirror of Z:

    position (x, y, z)        -> (x, y, -z)
    quaternion (x, y, z, w)   -> (-x, -y, z, w)
    matrix M (row-vector)     -> S*M*S, S = diag(1, 1, -1, 1); written to glTF column-major
                                 storage as the rows of S*M*S (same 16 numbers)
    tangent (x, y, z, w)      -> (x, y, -z, -w)
    triangle (a, b, c)        -> (a, c, b)

The mapping is its own inverse, so the same helpers are used in both directions.
"""
from __future__ import annotations

import copy
import math
import os
from array import array
import sys
import xml.etree.ElementTree as ET

from . import gltf as G
from .nwmodel import (Animation, Bone, Material, Mesh, Model, Skeleton, Track, IDENTITY,
                      animation_to_xml, guess_skeleton, load_animation, load_model,
                      load_skeleton, mesh_bbox, model_to_xml, used_bone_count)
from .nwxml import ENCODING, parse_bytes, serialize

EXT_KEY = 'narew'


def warn(msg: str) -> None:
    print(f'warning: {msg}', file=sys.stderr)


# ----------------------------------------------------------------------------- space mapping

def cv_vec3(p):
    return (p[0], p[1], -p[2])


def cv_quat(q):
    return (-q[0], -q[1], q[2], q[3])


def cv_tangent(t):
    return (t[0], t[1], -t[2], -t[3])


def cv_mat(m):
    s = (1.0, 1.0, -1.0, 1.0)
    return [[m[i][j] * s[i] * s[j] for j in range(4)] for i in range(4)]


def mat_to_gltf(m) -> list:
    """Engine row-major row-vector matrix (already mirrored) -> glTF column-major array."""
    return [float(m[r][c]) for r in range(4) for c in range(4)]


def gltf_to_mat(a) -> list:
    return [[float(a[r * 4 + c]) for c in range(4)] for r in range(4)]


def is_identity(m, eps=1e-6) -> bool:
    return all(abs(m[i][j] - (1.0 if i == j else 0.0)) < eps for i in range(4) for j in range(4))


def _el_to_str(e: ET.Element) -> str:
    return serialize([e], declaration='').decode(ENCODING).strip()


def _str_to_el(s: str) -> ET.Element:
    root = parse_bytes(s.encode(ENCODING, errors='replace'))
    return root[0]


# ----------------------------------------------------------------------------- textures

PNG_SIG = b'\x89PNG\r\n\x1a\n'


def png_has_alpha(data: bytes) -> bool:
    if not data.startswith(PNG_SIG) or len(data) < 26:
        return False
    color_type = data[25]
    if color_type in (4, 6):
        return True
    return b'tRNS' in data[:min(len(data), 1 << 16)]


def load_texture_bytes(path: str) -> tuple[bytes | None, str | None]:
    """Return (bytes, mime) of a texture usable in glTF (PNG/JPEG); converts others via Pillow."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, 'rb') as f:
        data = f.read()
    if data.startswith(PNG_SIG):
        return data, 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return data, 'image/jpeg'
    try:
        from PIL import Image  # optional dependency
        import io
        im = Image.open(io.BytesIO(data))
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGBA' if 'A' in im.getbands() or im.mode == 'P' else 'RGB')
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        return buf.getvalue(), 'image/png'
    except ImportError:
        warn(f'{os.path.basename(path)}: {ext} needs Pillow (pip install pillow) to embed; skipped')
    except Exception as ex:  # unreadable / unsupported image
        warn(f'{os.path.basename(path)}: cannot convert ({ex}); skipped')
    return None, None


def tile_transform(tile: ET.Element | None) -> dict | None:
    """<tile row col frame> as KHR_texture_transform (IMaterial::SetTile @0x180163440/0x180163470).

    Engine: row/col clamped to 1..128, scale = (1/row, 1/col),
    offset = ((frame % row) / row, (frame // row) / col).
    """
    if tile is None:
        return None
    def num(k, d):
        try:
            return int(tile.get(k, d))
        except ValueError:
            return d
    row = min(max(num('row', 1), 1), 128)
    col = min(max(num('col', 1), 1), 128)
    if row == 1 and col == 1:
        return None
    frame = max(0, min(num('frame', 0), row * col))
    return {'offset': [(frame % row) / row, (frame // row) / col], 'scale': [1.0 / row, 1.0 / col]}


def resolve_texture(model_dir: str, name: str) -> str | None:
    """Engine rule (IMaterial::LoadTexture @0x180163750): '' or ':...' -> none, else dir+name."""
    name = (name or '').strip()
    if not name or name.startswith(':'):
        return None
    p = os.path.join(model_dir, name.replace('\\', os.sep))
    return p if os.path.exists(p) else None


# ----------------------------------------------------------------------------- export

class Exporter:
    def __init__(self, embed_textures=True, fps=None, engine_timing=False, helpers=True,
                 bone_meshes=False, gltf_uri_textures=False):
        self.b = G.Builder('NarewTools model2gltf')
        self.embed = embed_textures
        self.fps = fps
        self.engine_timing = engine_timing
        self.helpers = helpers
        self.gltf_uri_textures = gltf_uri_textures
        self.images: dict[str, int] = {}
        self.textures: dict[int, int] = {}
        self.skeleton: Skeleton | None = None
        self.bone_nodes: dict[int, int] = {}
        self.bone_world: dict[int, list] = {}
        self.mesh_nodes: dict[tuple[int, str, int], int] = {}   # (model idx, kind, id) -> node
        self.mesh_nodes_by_name: dict[str, int] = {}
        self.input_cache: dict[tuple, int] = {}
        self.helper_keys: set = set()
        self.out_dir = '.'

    # -- nodes
    def node(self, obj: dict, parent: int | None = None) -> int:
        idx = self.b.add('nodes', obj)
        if parent is None:
            self.b.json['scenes'][0]['nodes'].append(idx)
        else:
            self.b.json['nodes'][parent].setdefault('children', []).append(idx)
        return idx

    @staticmethod
    def trs(t, q, s) -> dict:
        d = {}
        if any(abs(x) > 0 for x in t):
            d['translation'] = list(cv_vec3(t))
        q = G.quat_normalize(cv_quat(q))
        if abs(q[3] - 1.0) > 1e-9:
            d['rotation'] = list(q)
        if any(abs(x - 1.0) > 0 for x in s):
            d['scale'] = list(s)
        return d

    # -- skeleton
    def add_skeleton(self, skel: Skeleton):
        self.skeleton = skel
        stem = os.path.splitext(os.path.basename(skel.path or 'skeleton'))[0]
        group = self.node({'name': stem, 'extras': {EXT_KEY: {
            'kind': 'nwbt', 'file': os.path.basename(skel.path or ''), 'bone_count': len(skel.bones)}}})
        by_id = {b.id: b for b in skel.bones}
        for b in skel.bones:
            self.bone_world[b.id] = b.world
        pending = list(skel.bones)
        while pending:
            progressed = False
            for b in list(pending):
                parent = by_id.get(b.parent_id)
                if parent is not None and parent.id not in self.bone_nodes:
                    continue
                # rest pose = bind pose (<transform>), so the inverse bind matrices match exactly;
                # init_key (4-decimal copy of the same pose) is kept in extras
                local = b.world if parent is None else G.mat_mul(b.world, G.mat_inverse(parent.world))
                t, q, s = G.rowmat_to_trs(local)
                nx = {'bone_id': b.id}
                if b.init_pos is not None:
                    nx['init_key'] = {'pos': b.init_pos, 'rot': b.init_rot, 'scale': b.init_scale}
                obj = {'name': b.name, **self.trs(t, q, s), 'extras': {EXT_KEY: nx}}
                self.bone_nodes[b.id] = self.node(obj, self.bone_nodes[parent.id] if parent else group)
                pending.remove(b)
                progressed = True
            if not progressed:   # cycle / missing parent: attach the rest to the group
                for b in pending:
                    t, q, s = G.rowmat_to_trs(b.world)
                    self.bone_nodes[b.id] = self.node({'name': b.name, **self.trs(t, q, s),
                                                       'extras': {EXT_KEY: {'bone_id': b.id}}}, group)
                pending = []
        if self.helpers and skel.helpers is not None:
            self.add_helpers(skel.helpers, group, None)

    def add_helpers(self, helper_list: ET.Element, group: int, model_idx: int | None):
        from .nwxml import read_tm
        for h in helper_list:
            link = h.find('link')
            world = read_tm(h.find('transform')) or IDENTITY
            parent, local = group, world
            if link is not None:
                bid = link.get('bone_id')
                mid = link.get('mesh_id')
                ltm = read_tm(link.find('transform'))
                if bid is not None and int(bid) in self.bone_nodes and ltm is not None:
                    parent, local = self.bone_nodes[int(bid)], ltm
                elif mid is not None and int(mid) >= 0 and model_idx is not None and ltm is not None:
                    key = (model_idx, 'mesh', int(mid))
                    if key in self.mesh_nodes:
                        parent, local = self.mesh_nodes[key], ltm
            key = (parent, h.get('name', 'helper'))
            if key in self.helper_keys:       # part files repeat the skeleton's helpers
                continue
            self.helper_keys.add(key)
            self.node({'name': h.get('name', 'helper'), 'matrix': mat_to_gltf(cv_mat(local)),
                       'extras': {EXT_KEY: {'helper': h.get('type', '')}}}, parent)

    # -- materials
    def image_index(self, path: str) -> int | None:
        key = os.path.normcase(os.path.abspath(path))
        if key in self.images:
            return self.images[key]
        name = os.path.basename(path)
        idx = None
        if self.embed:
            data, mime = load_texture_bytes(path)
            if data is not None:
                idx = self.b.image(data, mime, os.path.splitext(name)[0])
        else:
            from urllib.parse import quote
            try:
                uri = quote(os.path.relpath(path, self.out_dir).replace(os.sep, '/'))
            except ValueError:   # different drive: absolute file URI
                uri = 'file:///' + quote(os.path.abspath(path).replace(os.sep, '/'))
            idx = self.b.add('images', {'uri': uri, 'name': os.path.splitext(name)[0]})
        self.images[key] = idx
        return idx

    def texture_index(self, path: str) -> int | None:
        im = self.image_index(path)
        if im is None:
            return None
        if im not in self.textures:
            if 'samplers' not in self.b.json:
                self.b.add('samplers', {'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497})
            self.textures[im] = self.b.add('textures', {'source': im, 'sampler': 0})
        return self.textures[im]

    def add_material(self, mat: Material, model_dir: str) -> int:
        pbr = {'metallicFactor': 0.0, 'roughnessFactor': 1.0}
        col = mat.color('diffuse')
        if col:
            try:
                v = int(col, 16)
                rgba = [((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0,
                        ((v >> 24) & 255) / 255.0]
                if rgba != [1.0, 1.0, 1.0, 1.0]:
                    pbr['baseColorFactor'] = rgba
            except ValueError:
                pass
        out = {'name': mat.name, 'pbrMetallicRoughness': pbr}
        diffuse = resolve_texture(model_dir, mat.map('diffuse_map'))
        if diffuse:
            t = self.texture_index(diffuse)
            if t is not None:
                pbr['baseColorTexture'] = {'index': t}
                xf = tile_transform(mat.element.find('tile'))
                if xf:
                    pbr['baseColorTexture']['extensions'] = {'KHR_texture_transform': xf}
                    used = self.b.json.setdefault('extensionsUsed', [])
                    if 'KHR_texture_transform' not in used:
                        used.append('KHR_texture_transform')
                if diffuse.lower().endswith('.png'):
                    with open(diffuse, 'rb') as f:
                        if png_has_alpha(f.read(1 << 16)):
                            out['alphaMode'] = 'MASK'
                            out['alphaCutoff'] = 0.5
        normal = resolve_texture(model_dir, mat.map('normal_map'))
        if normal and normal != diffuse:
            t = self.texture_index(normal)
            if t is not None:
                out['normalTexture'] = {'index': t}
        out['extras'] = {EXT_KEY: {'id': mat.id, 'xml': _el_to_str(mat.element)}}
        return self.b.add('materials', out)

    # -- meshes
    def exact(self, values, type_: str, ctype: int = G.FLOAT) -> list:
        """Accessor (not used by any primitive) holding exact engine values + its CRC32.

        Custom vertex attributes (_NAME) would be simpler, but Blender 4.2's importer pairs
        several of them with the wrong names (it iterates a set), so the data lives in
        accessors referenced from node extras; the CRC lets gltf2model ignore stale references
        after another tool rewrote the file.
        """
        import zlib
        idx = self.b.accessor(values, type_, ctype)
        flat = values if type_ == 'SCALAR' else [x for v in values for x in v]
        return [idx, zlib.crc32(array(G.CTYPE[ctype][0], flat).tobytes())]

    def add_primitive_mesh(self, m: Mesh, material: int | None, joints_ok: bool) -> int:
        b = self.b
        attrs = {}
        self.last_exact = exact = {}
        pos = [cv_vec3(p) for p in m.positions]
        attrs['POSITION'] = b.accessor(pos, 'VEC3', target=G.ARRAY_BUFFER, minmax=True)
        if m.normals:
            nrm = []
            for n in m.normals:
                ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
                nrm.append(cv_vec3((n[0] / ln, n[1] / ln, n[2] / ln)) if ln > 1e-12 else (0.0, 1.0, 0.0))
            attrs['NORMAL'] = b.accessor(nrm, 'VEC3', target=G.ARRAY_BUFFER)
            if any(a != b for n, u in zip(m.normals, nrm) for a, b in zip(cv_vec3(n), u)):
                # NORMAL must be unit length; keep the file's values (5 decimals, some exports
                # even unnormalised) so unmodified meshes round-trip exactly
                exact['normal'] = self.exact([cv_vec3(n) for n in m.normals], 'VEC3')
        if m.tangents:
            tan = []
            nrm_src = m.normals or [(0.0, 1.0, 0.0)] * len(m.tangents)
            for t, n in zip(m.tangents, nrm_src):
                ln = math.sqrt(t[0] * t[0] + t[1] * t[1] + t[2] * t[2])
                w = -1.0 if t[3] < 0 else 1.0
                if ln > 1e-12:
                    v = (t[0] / ln, t[1] / ln, t[2] / ln)
                else:          # degenerate: any unit vector perpendicular to the normal
                    v = _perpendicular(n)
                tan.append(cv_tangent((v[0], v[1], v[2], w)))
            if m.normals:
                attrs['TANGENT'] = b.accessor(tan, 'VEC4', target=G.ARRAY_BUFFER)
            # exact engine values (unnormalised) for lossless round trips
            if G.f32_list(cv_tangent(t) for t in m.tangents) != G.f32_list(tan):
                exact['tangent'] = self.exact([cv_tangent(t) for t in m.tangents], 'VEC4')
        if m.uvs:
            attrs['TEXCOORD_0'] = b.accessor([(u, v) for u, v in m.uvs], 'VEC2', target=G.ARRAY_BUFFER)
        if m.skin:
            joints = [tuple(bn for bn, _ in infl) for infl in m.skin]
            weights = []
            for infl in m.skin:
                ws = [max(0.0, w) for _, w in infl]
                s = sum(ws)
                weights.append(tuple(w / s for w in ws) if s > 0 else (1.0, 0.0, 0.0, 0.0))
            ctype = G.UNSIGNED_BYTE if max(max(j) for j in joints) < 256 else G.UNSIGNED_SHORT
            if joints_ok:
                attrs['JOINTS_0'] = b.accessor(joints, 'VEC4', ctype, target=G.ARRAY_BUFFER)
                attrs['WEIGHTS_0'] = b.accessor(weights, 'VEC4', target=G.ARRAY_BUFFER)
                raw = [tuple(w for _, w in infl) for infl in m.skin]
                if G.f32_list(raw) != G.f32_list(weights):   # sums slightly off 1: keep exact values
                    exact['weights'] = self.exact(raw, 'VEC4')
            else:   # no skeleton: keep the skin data for gltf2model only
                exact['joints'] = self.exact(joints, 'VEC4', G.UNSIGNED_SHORT)
                exact['weights'] = self.exact([tuple(w for _, w in infl) for infl in m.skin], 'VEC4')
        nv = len(m.positions)
        idx = []
        for a, bb, c in m.faces:
            idx.extend((a, c, bb))
        prim = {'attributes': attrs, 'mode': 4}
        if idx:
            ctype = G.UNSIGNED_SHORT if nv <= 65535 else G.UNSIGNED_INT
            prim['indices'] = b.accessor(idx, 'SCALAR', ctype, target=G.ELEMENT_ARRAY_BUFFER)
        if material is not None:
            prim['material'] = material
        return b.add('meshes', {'name': m.name, 'primitives': [prim]})

    def add_model(self, model: Model, model_idx: int):
        model_dir = os.path.dirname(os.path.abspath(model.path))
        stem = os.path.splitext(os.path.basename(model.path))[0]
        sections = {t: model.raw.get(t) or _el_to_str(e) for t, e in model.sections if e is not None}
        for tag in ('bound_box', 'material_list'):     # decoded, but kept for exact rewrites
            if tag in model.raw:
                sections[tag] = model.raw[tag]
        group = self.node({'name': stem, 'extras': {EXT_KEY: {
            'kind': 'nwmt', 'file': os.path.basename(model.path), 'indent': model.indent,
            'exp3': model.exp3,
            'header': _el_to_str(model.header) if model.header is not None else None,
            'list_counts': model.list_counts,
            'order': [t for t, _ in model.sections], 'sections': sections}}})
        mat_index = {m.id: self.add_material(m, model_dir) for m in model.materials}
        self.b.json['nodes'][group]['extras'][EXT_KEY]['materials'] = list(mat_index.values())

        skinned = [m for m in model.meshes if m.kind == 'skinmesh']
        skin_idx = None
        joints_ok = False
        if skinned:
            if self.skeleton is None:
                warn(f'{stem}: skinned mesh but no skeleton (.nwbt); exporting in bind pose without a skin')
            else:
                if len(self.skeleton.bones) != model.bone_count:
                    warn(f'{stem}: model expects {model.bone_count} bones, skeleton has '
                         f'{len(self.skeleton.bones)}')
                skin_idx = self.add_skin(model, stem, group)
                joints_ok = skin_idx is not None

        statics = {m.name: m for m in model.meshes if m.kind == 'mesh'}
        for m in _parents_first(model.meshes, statics):
            mesh_idx = self.add_primitive_mesh(m, mat_index.get(m.material), joints_ok)
            extras = {EXT_KEY: {'kind': m.kind, 'id': m.id, 'material': m.material,
                                'model': os.path.basename(model.path),
                                'parent': [m.parent_id, m.parent_name],
                                'transform': m.transform, 'bound_box': m.bound_box,
                                'use_bone': m.use_bone, 'attrs': m.attrs,
                                'index_attrs': m.index_attrs, 'list_order': m.list_order,
                                'uv_width': m.uv_width, 'exact': self.last_exact}}
            obj = {'name': m.name, 'mesh': mesh_idx, 'extras': extras}
            parent_node = group
            if m.kind == 'mesh':
                tm = m.transform
                par = statics.get(m.parent_name) if m.parent_id >= 0 else None
                pkey = (model_idx, 'mesh', par.id) if par is not None else None
                if par is not None and pkey in self.mesh_nodes:
                    tm = G.mat_mul(tm, G.mat_inverse(par.transform))
                    parent_node = self.mesh_nodes[pkey]
                if not is_identity(tm):
                    obj.update(node_transform(cv_mat(tm)))
            elif skin_idx is not None:
                obj['skin'] = skin_idx
            node = self.node(obj, parent_node)
            self.mesh_nodes[(model_idx, m.kind, m.id)] = node
            self.mesh_nodes_by_name.setdefault(m.name, node)
        hl = model.section('helper_list')
        if self.helpers and hl is not None:
            self.add_helpers(hl, group, model_idx)

    def add_skin(self, model: Model, stem: str, group: int) -> int | None:
        skel = self.skeleton
        nb = model.bone_count or len(skel.bones)
        specials = sorted(model.special_bones(), key=lambda x: x.id)
        total = nb + (specials[-1].id + 1 if specials else 0)
        joints: list = [None] * total
        worlds: list = [None] * total
        for i in range(nb):
            if i not in self.bone_nodes:
                warn(f'{stem}: skeleton has no bone {i}')
                return None
            joints[i], worlds[i] = self.bone_nodes[i], self.bone_world[i]
        by_name = {b.name: b for b in specials}
        by_id = {b.id: b for b in specials}
        skel_names = {b.name: b for b in skel.bones}
        created: dict[int, int] = {}

        def make(sb: Bone, depth=0) -> int:
            if sb.id in created:
                return created[sb.id]
            # parent id is the global bone index (skeleton id, or nb + special id); names
            # repeat across physics chains (f1_look_joseon_01 has two "physics_001")
            pid = sb.parent_id
            pb = by_id.get(pid - nb) if pid >= nb else None
            if pb is None and pid < 0:
                pb = by_name.get(sb.parent_name)
            if pb is not None and pb is not sb and depth < 64:
                pnode, pworld = make(pb, depth + 1), pb.world
            elif 0 <= pid < nb and pid in self.bone_nodes:
                pnode, pworld = self.bone_nodes[pid], self.bone_world[pid]
            elif sb.parent_name in skel_names:
                p = skel_names[sb.parent_name]
                pnode, pworld = self.bone_nodes[p.id], p.world
            else:
                pnode, pworld = group, IDENTITY
            t, q, s = G.rowmat_to_trs(G.mat_mul(sb.world, G.mat_inverse(pworld)))
            created[sb.id] = self.node({'name': sb.name, **self.trs(t, q, s),
                                        'extras': {EXT_KEY: {'bone_id': nb + sb.id, 'special': True}}},
                                       pnode)
            return created[sb.id]
        for sb in specials:
            joints[nb + sb.id], worlds[nb + sb.id] = make(sb), sb.world
        for i in range(total):           # ids never referenced by the file: unique placeholders
            if joints[i] is None:
                joints[i] = self.node({'name': f'unused_bone_{i}'}, group)
                worlds[i] = IDENTITY
        ibm = [tuple(mat_to_gltf(cv_mat(G.mat_inverse(w)))) for w in worlds]
        acc = self.b.accessor(ibm, 'MAT4')
        return self.b.add('skins', {'name': stem, 'joints': joints, 'inverseBindMatrices': acc})

    # -- animation
    def time_of(self, n: int, framerate: int) -> float:
        if self.engine_timing:            # engine: framerate value is used as ms per key
            return n * framerate / 1000.0
        return n / float(self.fps or framerate)

    def input_accessor(self, ns: tuple, framerate: int) -> int:
        key = (ns, framerate)
        if key not in self.input_cache:
            self.input_cache[key] = self.b.accessor([self.time_of(n, framerate) for n in ns], 'SCALAR',
                                                    minmax=True)
        return self.input_cache[key]

    def add_animation(self, anim: Animation):
        name = os.path.splitext(os.path.basename(anim.path))[0]
        channels, samplers = [], []
        used_targets = set()

        def add_track(node: int, track: Track):
            for path, keys, conv in (('translation', track.pos, cv_vec3), ('rotation', track.rot, None),
                                     ('scale', track.scale, None)):
                if not keys:
                    continue
                if (node, path) in used_targets:
                    continue
                used_targets.add((node, path))
                keys = sorted(dict(keys).items())       # one key per n (strictly increasing times)
                ns = tuple(n for n, _ in keys)
                if path == 'rotation':
                    vals, prev = [], None
                    for _, q in keys:
                        q = G.quat_normalize(cv_quat(q))
                        if prev is not None and sum(a * b for a, b in zip(q, prev)) < 0:
                            q = tuple(-c for c in q)   # same rotation, continuous for slerp
                        vals.append(q)
                        prev = q
                    otype = 'VEC4'
                else:
                    vals = [conv(v) if conv else v for _, v in keys]
                    otype = 'VEC3'
                inp = self.input_accessor(ns, anim.framerate)
                out = self.b.accessor(vals, otype)
                samplers.append({'input': inp, 'output': out, 'interpolation': 'LINEAR'})
                channels.append({'sampler': len(samplers) - 1, 'target': {'node': node, 'path': path}})

        skipped = 0
        name_to_bone = {b.name: b.id for b in self.skeleton.bones} if self.skeleton else {}
        for t in anim.bones:
            bid = t.id
            if t.name and t.name in name_to_bone and name_to_bone[t.name] != bid:
                bid = name_to_bone[t.name]
            if bid in self.bone_nodes:
                add_track(self.bone_nodes[bid], t)
            else:
                skipped += 1
        mesh_skipped = 0
        mapped = {}
        for t in anim.meshes:
            node = self.mesh_nodes_by_name.get(t.name) if t.name else self.mesh_nodes.get((0, 'mesh', t.id))
            if node is not None and 'skin' not in self.b.json['nodes'][node]:
                add_track(node, t)
                mapped[t.id] = True
            else:
                mesh_skipped += 1
        if skipped:
            warn(f'{name}: {skipped} bone track(s) have no matching bone')
        if not channels:
            warn(f'{name}: nothing to animate (load the matching skeleton/model); skipped')
            return
        extras = {EXT_KEY: {'file': os.path.basename(anim.path), 'framerate': anim.framerate,
                            'start': anim.start, 'end': anim.end, 'ticksperframe': anim.ticksperframe,
                            'flavour': anim.flavour, 'unmapped_mesh_tracks': mesh_skipped,
                            'mesh_tracks': [[t.id, t.name] + ([] if mapped.get(t.id) else
                                                              [[[n, *v] for n, v in t.pos],
                                                               [[n, *v] for n, v in t.rot],
                                                               [[n, *v] for n, v in t.scale]])
                                            for t in anim.meshes],
                            'keys': max([n for t in anim.bones + anim.meshes
                                         for n, _ in t.pos + t.rot + t.scale] + [-1]) + 1,
                            'seconds_per_key': self.time_of(1, anim.framerate),
                            'sections': {tag: _el_to_str(e) for tag, e in anim.extra}}}
        self.b.add('animations', {'name': name, 'channels': channels, 'samplers': samplers,
                                  'extras': extras})


def node_transform(m) -> dict:
    """TRS for a glTF node (animatable) when the matrix has no shear, else 'matrix'."""
    t, q, s = G.rowmat_to_trs(m)
    back = G.trs_to_rowmat(t, q, s)
    if all(abs(back[i][j] - m[i][j]) < 1e-7 * max(1.0, abs(m[i][j])) for i in range(4) for j in range(4)):
        d = {}
        if any(t):
            d['translation'] = list(t)
        if abs(q[3] - 1.0) > 1e-12:
            d['rotation'] = list(q)
        if any(abs(x - 1.0) > 1e-12 for x in s):
            d['scale'] = list(s)
        return d
    return {'matrix': mat_to_gltf(m)}


def _parents_first(meshes: list[Mesh], statics: dict) -> list[Mesh]:
    out, done = [], set()
    pending = [m for m in meshes if m.kind == 'mesh']
    while pending:
        rest = []
        for m in pending:
            par = statics.get(m.parent_name) if m.parent_id >= 0 else None
            if par is None or par is m or id(par) in done:
                out.append(m)
                done.add(id(m))
            else:
                rest.append(m)
        if len(rest) == len(pending):     # cycle: emit as is
            out += rest
            break
        pending = rest
    return out + [m for m in meshes if m.kind != 'mesh']


def _perpendicular(n):
    x, y, z = n[:3]
    if abs(x) < 0.9:
        v = (0.0, z, -y)          # n x (1,0,0)
    else:
        v = (-z, 0.0, x)          # n x (0,1,0)
    ln = math.sqrt(sum(c * c for c in v))
    if ln < 1e-12:                    # zero-length normal
        return (1.0, 0.0, 0.0)
    return tuple(c / ln for c in v)


def export_gltf(inputs: list[str], out: str, skeleton: str | None = None, anims: list[str] = (),
                embed_textures: bool | None = None, fps: float | None = None,
                engine_timing: bool = False, helpers: bool = True) -> dict:
    """Convert .nwmt/.nwbt (+ .nwat) files to one glTF/GLB. Returns a small summary."""
    is_glb = not out.lower().endswith('.gltf')
    ex = Exporter(embed_textures=is_glb if embed_textures is None else embed_textures, fps=fps,
                  engine_timing=engine_timing, helpers=helpers)
    ex.out_dir = os.path.dirname(os.path.abspath(out))
    models = [p for p in inputs if p.lower().endswith('.nwmt')]
    skels = [p for p in inputs if p.lower().endswith('.nwbt')]
    anims = list(anims) + [p for p in inputs if p.lower().endswith('.nwat')]
    loaded = [load_model(p) for p in models]
    skel_path = skeleton or (skels[0] if skels else None)
    if skel_path is None:
        for m in loaded:
            if any(x.kind == 'skinmesh' for x in m.meshes):
                skel_path = guess_skeleton(m.path, m.bone_count)
                if skel_path:
                    print(f'skeleton: {skel_path}')
                    break
    if skel_path is None and anims:
        skel_path = guess_skeleton(anims[0])
        if skel_path:
            print(f'skeleton: {skel_path}')
    if skel_path:
        ex.add_skeleton(load_skeleton(skel_path))
    for i, m in enumerate(loaded):
        ex.add_model(m, i)
    for a in anims:
        ex.add_animation(load_animation(a))
    ex.b.write(out)
    j = ex.b.json
    return {'nodes': len(j.get('nodes', [])), 'meshes': len(j.get('meshes', [])),
            'materials': len(j.get('materials', [])), 'images': len(j.get('images', [])),
            'skins': len(j.get('skins', [])), 'animations': len(j.get('animations', []))}


# ----------------------------------------------------------------------------- import (glTF -> engine)

def node_local(n: dict) -> list:
    """glTF node local transform as a row-vector (engine-style) matrix in glTF space."""
    if 'matrix' in n:
        return gltf_to_mat(n['matrix'])
    t = n.get('translation', [0.0, 0.0, 0.0])
    q = n.get('rotation', [0.0, 0.0, 0.0, 1.0])
    s = n.get('scale', [1.0, 1.0, 1.0])
    return G.trs_to_rowmat(t, q, s)


class SceneGraph:
    def __init__(self, doc: G.Document):
        self.doc = doc
        self.nodes = doc.get('nodes')
        self.parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get('children', []):
                self.parent[c] = i
        self._world = {}

    def world(self, i: int) -> list:
        if i not in self._world:
            local = node_local(self.nodes[i])
            p = self.parent.get(i)
            self._world[i] = local if p is None else G.mat_mul(local, self.world(p))
        return self._world[i]

    def relative(self, i: int, ancestor: int | None) -> list:
        w = self.world(i)
        return w if ancestor is None else G.mat_mul(w, G.mat_inverse(self.world(ancestor)))

    def descendants(self, i: int):
        for c in self.nodes[i].get('children', []):
            yield c
            yield from self.descendants(c)

    def roots(self):
        sc = self.doc.json.get('scenes', [{}])[self.doc.json.get('scene', 0)]
        return sc.get('nodes', [i for i in range(len(self.nodes)) if i not in self.parent])


def _narew(obj: dict) -> dict:
    ex = obj.get('extras') or {}
    v = ex.get(EXT_KEY)
    if isinstance(v, str):
        # Blender re-exports custom properties holding nested lists as Python repr strings
        import ast
        import json
        try:
            v = json.loads(v)
        except ValueError:
            try:
                v = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                v = None
    return v if isinstance(v, dict) else {}


def _base_name(name: str) -> str:
    """Strip Blender's duplicate suffix (".001")."""
    if len(name) > 4 and name[-4] == '.' and name[-3:].isdigit():
        return name[:-4]
    return name


def compute_normals(pos, faces):
    acc = [[0.0, 0.0, 0.0] for _ in pos]
    for a, b, c in faces:
        pa, pb, pc = pos[a], pos[b], pos[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        for k in (a, b, c):
            for i in range(3):
                acc[k][i] += n[i]
    out = []
    for n in acc:
        ln = math.sqrt(sum(x * x for x in n))
        out.append(tuple(x / ln for x in n) if ln > 1e-20 else (0.0, 1.0, 0.0))
    return out


def compute_tangents(pos, nrm, uvs, faces):
    """Per-vertex tangents from UV derivatives (engine winding, w by handedness)."""
    tan = [[0.0, 0.0, 0.0] for _ in pos]
    bit = [[0.0, 0.0, 0.0] for _ in pos]
    for a, b, c in faces:
        p0, p1, p2 = pos[a], pos[b], pos[c]
        t0, t1, t2 = uvs[a], uvs[b], uvs[c]
        e1 = [p1[i] - p0[i] for i in range(3)]
        e2 = [p2[i] - p0[i] for i in range(3)]
        du1, dv1 = t1[0] - t0[0], t1[1] - t0[1]
        du2, dv2 = t2[0] - t0[0], t2[1] - t0[1]
        r = du1 * dv2 - du2 * dv1
        if abs(r) < 1e-20:
            continue
        r = 1.0 / r
        sdir = [(e1[i] * dv2 - e2[i] * dv1) * r for i in range(3)]
        tdir = [(e2[i] * du1 - e1[i] * du2) * r for i in range(3)]
        for k in (a, b, c):
            for i in range(3):
                tan[k][i] += sdir[i]
                bit[k][i] += tdir[i]
    out = []
    for t, bt, n in zip(tan, bit, nrm):
        d = sum(t[i] * n[i] for i in range(3))
        v = [t[i] - n[i] * d for i in range(3)]
        ln = math.sqrt(sum(x * x for x in v))
        if ln < 1e-20:
            v = list(_perpendicular(n))
        else:
            v = [x / ln for x in v]
        cr = (n[1] * v[2] - n[2] * v[1], n[2] * v[0] - n[0] * v[2], n[0] * v[1] - n[1] * v[0])
        w = -1.0 if sum(cr[i] * bt[i] for i in range(3)) < 0 else 1.0
        out.append((v[0], v[1], v[2], w))
    return out


class Importer:
    def __init__(self, doc: G.Document, skeleton: Skeleton | None = None):
        self.doc = doc
        self.g = SceneGraph(doc)
        self.skeleton = skeleton

    def model_groups(self) -> list[tuple[int | None, dict]]:
        groups = [(i, _narew(n)) for i, n in enumerate(self.g.nodes) if _narew(n).get('kind') == 'nwmt']
        return groups or [(None, {})]

    def joint_bone_ids(self, skin: dict, info: dict, special_names: dict[str, int]) -> list[int]:
        skel_names = {b.name: b.id for b in self.skeleton.bones} if self.skeleton else {}
        ids = []
        for j in skin['joints']:
            n = self.g.nodes[j]
            nx = _narew(n)
            name = _base_name(n.get('name', ''))
            if 'bone_id' in nx:                   # written by model2gltf: exact id
                ids.append(int(nx['bone_id']))
            elif name in skel_names:
                ids.append(skel_names[name])
            elif name in special_names:
                ids.append(special_names[name])
            else:
                raise G.GltfError(f'joint node {n.get("name")!r} matches no engine bone '
                                  '(pass --skeleton with the .nwbt the model uses)')
        return ids

    def build(self, group: int | None, info: dict, template: Model | None = None) -> Model:
        g = self.g
        doc = self.doc
        if group is None:
            members = list(range(len(g.nodes)))
        else:
            # DCC tools re-parent skinned meshes under the armature: also take meshes tagged
            # with this model's file, and untagged meshes when there is only one model
            members = set(g.descendants(group))
            groups = self.model_groups()
            for i, n in enumerate(g.nodes):
                if 'mesh' not in n or i in members:
                    continue
                tag = _narew(n).get('model')
                if (tag and tag == info.get('file')) or (not tag and len(groups) == 1):
                    members.add(i)
            members = sorted(members)
        header = None
        sections: list[tuple[str, ET.Element | None]] = []
        if template is not None:
            header = template.header
            sections = template.sections
        raw_text = {}
        if template is not None:
            raw_text = dict(template.raw)
        elif info:
            header = _str_to_el(info['header']) if info.get('header') else None
            raw_text = dict(info.get('sections') or {})
            raw = {k: _str_to_el(v) for k, v in raw_text.items()
                   if k not in ('bound_box', 'material_list')}
            decoded = ('header', 'material_list', 'mesh_list', 'skinmesh_list', 'bound_box')
            sections = [(t, None if t in decoded else raw.get(t)) for t in info.get('order', [])]
        special_names = {}
        nb = int(header.get('bone', 0)) if header is not None else (len(self.skeleton.bones) if self.skeleton else 0)
        for t, e in sections:
            if t == 'special_bone_list' and e is not None:
                for b in e:
                    special_names[b.get('name', '')] = nb + int(b.get('id', 0))

        materials: list[Material] = []
        mat_map: dict[int, int] = {}
        used_ids = set()

        def material_for(gi: int | None, skinned: bool) -> int:
            if gi is None:
                return -1
            if gi in mat_map:
                return mat_map[gi]
            gm = doc.get('materials')[gi]
            nx = _narew(gm)
            el = _str_to_el(nx['xml']) if nx.get('xml') else None
            mid = int(nx['id']) if 'id' in nx and int(nx['id']) not in used_ids else len(materials)
            while mid in used_ids:
                mid += 1
            used_ids.add(mid)
            img = self.base_color_image(gm)
            if el is None:
                el = ET.Element('material', {'id': str(mid), 'name': gm.get('name', f'material{mid}')})
                ET.SubElement(el, 'shader', {'file': 'DefaultSkinMesh.fx' if skinned else 'DefaultMesh.fx',
                                             'technique': 'RenderDefault'})
                ET.SubElement(el, 'tile', {'row': '1', 'col': '1', 'frame': '0'})
                f = gm.get('pbrMetallicRoughness', {}).get('baseColorFactor', [1, 1, 1, 1])
                argb = '0x%02x%02x%02x%02x' % tuple(max(0, min(255, round(c * 255))) for c in (f[3], f[0], f[1], f[2]))
                ET.SubElement(el, 'color', {'ambient': '0xff808080', 'diffuse': argb,
                                            'specular': '0xffffffff', 'result': '0xffffffff'})
                ET.SubElement(el, 'specular', {'power': '1', 'gloss': '10'})
                for k in ('diffuse_map', 'normal_map', 'specular_map', 'mask_map'):
                    ET.SubElement(el, k).text = ''
            el.set('id', str(mid))
            mat = Material(mid, el.get('name', ''), el)
            if img is not None:
                fname = self.image_filename(img)
                if fname and os.path.basename(mat.map('diffuse_map')).lower() != fname.lower():
                    mat.set_map('diffuse_map', fname)
            materials.append(mat)
            mat_map[gi] = mid
            return mid

        meshes: list[Mesh] = []
        node_mesh_ids: dict[int, tuple[int, str]] = {}
        counters = {'mesh': 0, 'skinmesh': 0}
        taken = {'mesh': set(), 'skinmesh': set()}
        mesh_nodes = [i for i in members if 'mesh' in g.nodes[i] and not _narew(g.nodes[i]).get('helper')]
        # parents first so children can reference them
        mesh_nodes.sort(key=lambda i: self._depth(i))
        for ni in mesh_nodes:
            node = g.nodes[ni]
            nx = _narew(node)
            gmesh = doc.get('meshes')[node['mesh']]
            prims = [p for p in gmesh['primitives'] if p.get('mode', 4) == 4]
            skin = doc.get('skins')[node['skin']] if 'skin' in node else None
            for pi, prim in enumerate(prims):
                kind = 'skinmesh' if (skin is not None or (nx.get('kind') == 'skinmesh'
                                                           and 'joints' in (nx.get('exact') or {}))) else 'mesh'
                if 'name' in node:                  # may legitimately be '' (two shipped files)
                    name = _base_name(node['name'])
                else:
                    name = gmesh.get('name') or f'{kind}{len(meshes)}'
                if len(prims) > 1:
                    name = f'{name}_{pi}'
                want = nx.get('id') if pi == 0 and nx.get('kind') == kind else None
                if want is not None and want not in taken[kind]:
                    mid = int(want)
                else:
                    while counters[kind] in taken[kind]:
                        counters[kind] += 1
                    mid = counters[kind]
                taken[kind].add(mid)
                m = Mesh(kind=kind, id=mid, name=name)
                m.attrs = dict(nx.get('attrs') or {})
                m.index_attrs = dict(nx.get('index_attrs') or {})
                m.list_order = list(nx.get('list_order') or [])
                self.fill_geometry(m, prim, skin, ni, group, nx, special_names)
                m.material = material_for(prim.get('material'), kind == 'skinmesh')
                if prim.get('material') is None and pi == 0 and nx.get('material') is not None:
                    m.material = int(nx['material'])    # id of a material the file never defined
                if kind == 'mesh':
                    p = g.parent.get(ni)
                    if p is not None and p in node_mesh_ids:
                        pid, pname = node_mesh_ids[p]
                        m.parent_id, m.parent_name = pid, pname
                    elif nx.get('parent') and nx['parent'][0] < 0:
                        m.parent_id, m.parent_name = -1, nx['parent'][1]
                    elif nx.get('parent'):
                        m.parent_id, m.parent_name = nx['parent'][0], nx['parent'][1]
                else:
                    m.parent_id, m.parent_name = (nx.get('parent') or [-1, 'Scene Root'])
                    ub = nx.get('use_bone')
                    m.use_bone = ub if ub is not None and ub >= used_bone_count(m) else used_bone_count(m)
                if pi == 0:
                    node_mesh_ids[ni] = (m.id, m.name)
                meshes.append(m)
        for gi in info.get('materials') or []:      # materials no mesh uses survive too
            if 0 <= gi < len(doc.get('materials')):
                material_for(gi, False)
        materials.sort(key=lambda x: x.id)
        if not all(m.bbox_from_file for m in meshes):
            raw_text.pop('bound_box', None)        # geometry changed: recompute the model box
        mdl = Model(None, header, materials, meshes, sections, raw_text)
        if template is not None:
            mdl.indent, mdl.exp3 = template.indent, template.exp3
        elif info.get('indent'):
            mdl.indent, mdl.exp3 = info['indent'], bool(info.get('exp3'))
        if template is not None:
            mdl.list_counts = template.list_counts
        elif info.get('list_counts'):
            mdl.list_counts = info['list_counts']
        if mdl.list_counts:
            now = {'material': len(materials), 'mesh': sum(1 for m in meshes if m.kind == 'mesh'),
                   'skinmesh': sum(1 for m in meshes if m.kind == 'skinmesh')}
            if any(now[k] != mdl.list_counts.get(k) for k in now):
                mdl.list_counts = {}                # lists changed: write true counts
        return mdl

    def _depth(self, i: int) -> int:
        d = 0
        while i in self.g.parent:
            i = self.g.parent[i]
            d += 1
        return d

    def fill_geometry(self, m: Mesh, prim: dict, skin: dict | None, ni: int, group: int | None,
                      nx: dict, special_names: dict):
        doc, g = self.doc, self.g
        a = prim['attributes']
        pos = doc.read_accessor(a['POSITION'])
        nrm = doc.read_accessor(a['NORMAL']) if 'NORMAL' in a else None
        uvs = doc.read_accessor(a['TEXCOORD_0']) if 'TEXCOORD_0' in a else None
        if 'indices' in prim:
            idx = doc.read_accessor(prim['indices'])
        else:
            idx = list(range(len(pos)))
        tris = [(idx[i], idx[i + 2], idx[i + 1]) for i in range(0, len(idx) - 2, 3)]   # back to engine winding

        if skin is not None:
            joints = doc.read_accessor(a['JOINTS_0'])
            weights = doc.read_accessor(a['WEIGHTS_0'])
            extra_j = doc.read_accessor(a['JOINTS_1']) if 'JOINTS_1' in a else None
            extra_w = doc.read_accessor(a['WEIGHTS_1']) if 'WEIGHTS_1' in a else None
            bone_ids = self.joint_bone_ids(skin, nx, special_names)
            ibm = [gltf_to_mat(x) for x in doc.read_accessor(skin['inverseBindMatrices'])] \
                if 'inverseBindMatrices' in skin else [copy.deepcopy(IDENTITY)] * len(skin['joints'])
            # rest-pose skinning matrix per joint; identity for files we wrote ourselves
            rest = [G.mat_mul(ibm[k], g.world(j)) for k, j in enumerate(skin['joints'])]
            if group is not None:
                ginv = G.mat_inverse(g.world(group))
                rest = [G.mat_mul(r, ginv) for r in rest]
            all_ident = all(is_identity(r, 1e-4) for r in rest)
            out_pos, out_nrm, sk = [], [], []
            for vi, p in enumerate(pos):
                infl = list(zip(joints[vi], weights[vi]))
                if extra_j is not None:
                    infl += list(zip(extra_j[vi], extra_w[vi]))
                    infl.sort(key=lambda x: -x[1])
                    infl = infl[:4]
                s = sum(w for _, w in infl)
                if all_ident:
                    out_pos.append(p)
                    if nrm is not None:
                        out_nrm.append(nrm[vi])
                else:
                    acc = [0.0, 0.0, 0.0]
                    accn = [0.0, 0.0, 0.0]
                    for j, w in infl:
                        if w <= 0:
                            continue
                        r = rest[j]
                        for c in range(3):
                            acc[c] += w * (p[0] * r[0][c] + p[1] * r[1][c] + p[2] * r[2][c] + r[3][c])
                            if nrm is not None:
                                accn[c] += w * (nrm[vi][0] * r[0][c] + nrm[vi][1] * r[1][c] + nrm[vi][2] * r[2][c])
                    out_pos.append(tuple(x / s for x in acc) if s > 0 else p)
                    if nrm is not None:
                        ln = math.sqrt(sum(x * x for x in accn)) or 1.0
                        out_nrm.append(tuple(x / ln for x in accn))
                sk.append([(bone_ids[j] if j < len(bone_ids) else 0, w) for j, w in infl]
                          + [(0, 0.0)] * (4 - len(infl)))
            pos = out_pos
            nrm = out_nrm if nrm is not None else None
            m.skin = [[(b, w) for b, w in v] for v in sk]
            raw = self.exact_values(nx, 'weights', len(pos))
            if raw is not None and extra_j is None:
                m.skin = [[(b, rw) for (b, _), rw in zip(v, r)] for v, r in zip(m.skin, raw)]
            m.transform = nx.get('transform') or copy.deepcopy(IDENTITY)
        elif self.exact_values(nx, 'joints', len(pos)) is not None:
            joints = self.exact_values(nx, 'joints', len(pos))
            weights = self.exact_values(nx, 'weights', len(pos))
            m.skin = [list(zip(map(int, j), w)) for j, w in zip(joints, weights)]
            m.transform = nx.get('transform') or copy.deepcopy(IDENTITY)
        else:
            rel = cv_mat(g.relative(ni, group))
            orig = nx.get('transform')
            if orig and all(abs(rel[i][j] - orig[i][j]) < 1e-5 for i in range(4) for j in range(4)):
                rel = orig                        # unchanged: keep the file's exact digits
            m.transform = rel
        m.positions = [cv_vec3(p) for p in pos]
        raw_n = self.exact_values(nx, 'normal', len(pos))
        if raw_n is not None:
            m.normals = [cv_vec3(n) for n in raw_n]
        else:
            m.normals = [cv_vec3(n) for n in nrm] if nrm is not None else compute_normals(m.positions, tris)
        m.uvs = [tuple(u) for u in uvs] if uvs is not None else [(0.0, 0.0)] * len(pos)
        m.uv_width = 1 if nx.get('uv_width') == 1 and all(v == 0.0 for _, v in m.uvs) else 2
        m.faces = tris
        raw_t = self.exact_values(nx, 'tangent', len(pos))
        if raw_t is not None:
            m.tangents = [cv_tangent(t) for t in raw_t]
        elif 'TANGENT' in a:
            m.tangents = [cv_tangent(t) for t in doc.read_accessor(a['TANGENT'])]
        else:
            m.tangents = compute_tangents(m.positions, m.normals, m.uvs, m.faces)
        if not m.list_order:
            m.list_order = (['point_list', 'skin_list', 'normal_list', 'tangent_list', 'uv_list']
                            if m.kind == 'skinmesh' else ['point_list', 'normal_list', 'tangent_list', 'uv_list'])
        bb = nx.get('bound_box')
        m.write_bbox = not ('bound_box' in nx and bb is None)
        m.bbox_from_file = bool(bb and _same_bbox(bb, mesh_bbox(m))) or not m.write_bbox
        m.bound_box = bb if m.bbox_from_file else mesh_bbox(m)

    def exact_values(self, nx: dict, key: str, count: int):
        """Exact engine values stored by model2gltf, if still valid for this primitive."""
        ref = (nx.get('exact') or {}).get(key)
        accs = self.doc.get('accessors')
        if not ref or not isinstance(ref, list) or len(ref) != 2:
            return None
        idx, crc = ref
        if not (isinstance(idx, int) and 0 <= idx < len(accs)) or accs[idx].get('count') != count:
            return None
        if self.doc.accessor_crc(idx) != crc:
            return None
        return self.doc.read_accessor(idx)

    def base_color_image(self, gm: dict) -> int | None:
        t = gm.get('pbrMetallicRoughness', {}).get('baseColorTexture')
        if not t:
            return None
        tex = self.doc.get('textures')[t['index']]
        return tex.get('source')

    def image_filename(self, img: int) -> str | None:
        im = self.doc.get('images')[img]
        uri = im.get('uri')
        if uri and not uri.startswith('data:'):
            from urllib.parse import unquote
            return os.path.basename(unquote(uri))
        name = im.get('name')
        if not name:
            return None
        if os.path.splitext(name)[1].lower() in ('.png', '.jpg', '.jpeg', '.tga', '.bmp', '.dds'):
            return name
        mime = im.get('mimeType', 'image/png')
        return name + ('.jpg' if mime == 'image/jpeg' else '.png')

    def write_images(self, model: Model, out_dir: str, force: bool) -> list[str]:
        written = []
        wanted = {}
        for gi, gm in enumerate(self.doc.get('materials')):
            img = self.base_color_image(gm)
            if img is not None:
                fn = self.image_filename(img)
                if fn:
                    wanted[fn.lower()] = (fn, img)
        for mat in model.materials:
            name = os.path.basename(mat.map('diffuse_map'))
            if name.lower() not in wanted:
                continue
            fn, img = wanted[name.lower()]
            data, _ = self.doc.image_bytes(img)
            if data is None:
                continue
            target = os.path.join(out_dir, fn)
            if os.path.exists(target):
                with open(target, 'rb') as f:
                    same = f.read() == data
                if same:
                    continue
                if not force:
                    if os.path.splitext(fn)[1].lower() not in ('.png', '.jpg', '.jpeg'):
                        continue      # embedded copy is a PNG conversion of the original
                    warn(f'{target} exists and differs; use --force to overwrite')
                    continue
            with open(target, 'wb') as f:
                f.write(data)
            written.append(target)
        return written


def _same_bbox(a, b, eps=2e-5) -> bool:
    return all(abs(x - y) <= eps * max(1.0, abs(y)) for x, y in zip(a, b))


def import_model(gltf_path: str, out: str, template: str | None = None, skeleton: str | None = None,
                 model: str | None = None, write_textures: bool = True, force: bool = False) -> list[str]:
    """Convert the model group(s) of a glTF back to .nwmt. Returns written paths."""
    doc = G.load(gltf_path)
    skel = load_skeleton(skeleton) if skeleton else None
    imp = Importer(doc, skel)
    groups = imp.model_groups()
    if model:
        groups = [(i, info) for i, info in groups
                  if i is not None and (imp.g.nodes[i].get('name') == model or info.get('file') == model)]
        if not groups:
            raise G.GltfError(f'no model group named {model!r}')
    tmpl = load_model(template) if template else None
    written = []
    out_is_dir = os.path.isdir(out) or len(groups) > 1 or not out.lower().endswith('.nwmt')
    for gi, info in groups:
        mdl = imp.build(gi, info, tmpl)
        if out_is_dir:
            os.makedirs(out, exist_ok=True)
            name = info.get('file') or (imp.g.nodes[gi].get('name', 'model') + '.nwmt' if gi is not None
                                        else os.path.splitext(os.path.basename(gltf_path))[0] + '.nwmt')
            path = os.path.join(out, name)
        else:
            path = out
        with open(path, 'wb') as f:
            f.write(model_to_xml(mdl))
        written.append(path)
        if write_textures:
            written += imp.write_images(mdl, os.path.dirname(os.path.abspath(path)), force)
    return written


# ----------------------------------------------------------------------------- animation import

def _sample(times, values, interp, t):
    if t <= times[0]:
        return values[0] if interp != 'CUBICSPLINE' else values[1]
    if t >= times[-1]:
        return values[-1] if interp != 'CUBICSPLINE' else values[-2]
    lo, hi = 0, len(times) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    t0, t1 = times[lo], times[hi]
    u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    if interp == 'STEP':
        return values[lo]
    if interp == 'CUBICSPLINE':
        dt = t1 - t0
        p0, m0 = values[lo * 3 + 1], values[lo * 3 + 2]
        p1, m1 = values[hi * 3 + 1], values[hi * 3]
        u2, u3 = u * u, u * u * u
        return tuple((2 * u3 - 3 * u2 + 1) * a + (u3 - 2 * u2 + u) * dt * b + (-2 * u3 + 3 * u2) * c
                     + (u3 - u2) * dt * d for a, b, c, d in zip(p0, m0, p1, m1))
    a, b = values[lo], values[hi]
    if len(a) == 4:
        return _slerp(a, b, u)
    return tuple(x + (y - x) * u for x, y in zip(a, b))


def _slerp(a, b, u):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b, d = tuple(-x for x in b), -d
    if 1.0 - d > 0.001:
        th = math.acos(min(1.0, d))
        s = math.sin(th)
        wa, wb = math.sin((1 - u) * th) / s, math.sin(u * th) / s
    else:
        wa, wb = 1 - u, u
    return G.quat_normalize(tuple(wa * x + wb * y for x, y in zip(a, b)))


def _sample_track(chans, ni, rest, spk, nframes):
    """Sample one node's TRS channels at frames 0..nframes-1 and convert to engine space."""
    keys = {}
    for path in ('translation', 'rotation', 'scale'):
        ch = chans.get((ni, path))
        if ch is None:
            seq = [tuple(rest[path])]
        else:
            seq = [_sample(ch[0], ch[1], ch[2], f * spk) for f in range(nframes)]
        if path == 'translation':
            seq = [cv_vec3(v) for v in seq]
        elif path == 'rotation':
            seq = [cv_quat(G.quat_normalize(v)) for v in seq]
        if all(max(abs(x - y) for x, y in zip(v, seq[0])) < 5e-6 for v in seq):
            seq = seq[:1]                     # constant track -> one key, like the 3ds Max exporter
        keys[path] = list(enumerate(seq))
    return keys


def _rest(node: dict) -> dict:
    return {'translation': node.get('translation', [0.0, 0.0, 0.0]),
            'rotation': node.get('rotation', [0.0, 0.0, 0.0, 1.0]),
            'scale': node.get('scale', [1.0, 1.0, 1.0])}


def import_animation(gltf_path: str, out: str, skeleton: str | None = None,
                     animation: str | None = None, fps: float | None = None,
                     template: str | None = None) -> list[str]:
    """Sample glTF animation(s) at whole frames and write .nwat (compact <root> flavour).

    Bone tracks: one per skeleton bone (from ``skeleton``, else from joint nodes that carry
    a narew bone_id).  Mesh tracks: resampled onto mesh nodes of the same name when they
    are animated, otherwise the original keys kept in the animation extras are written back.
    """
    doc = G.load(gltf_path)
    g = SceneGraph(doc)
    tmpl = load_animation(template) if template else None
    if tmpl is not None and not fps:
        fps = tmpl.framerate
    anims = doc.get('animations')
    if animation:
        anims = [a for a in anims if a.get('name') == animation]
        if not anims:
            raise G.GltfError(f'no animation named {animation!r}')
    if not anims:
        raise G.GltfError('the file has no animations')
    by_id, name_to_node, mesh_nodes = {}, {}, {}
    for i, n in enumerate(g.nodes):
        nx = _narew(n)
        if 'bone_id' in nx and not nx.get('special'):
            by_id.setdefault(int(nx['bone_id']), i)
        name_to_node.setdefault(_base_name(n.get('name', '')), i)
        if 'mesh' in n and 'skin' not in n:
            mesh_nodes.setdefault(_base_name(n.get('name', '')), i)
    if skeleton:
        bones = [(b.id, b.name) for b in load_skeleton(skeleton).bones]
    else:
        bones = sorted((bid, _base_name(g.nodes[ni].get('name', ''))) for bid, ni in by_id.items())
        if not bones and not any(_narew(a).get('mesh_tracks') for a in anims):
            raise G.GltfError('no skeleton: pass --skeleton with the .nwbt the animation is for')
    bone_node = {}
    for bid, name in bones:
        if bid in by_id and _base_name(g.nodes[by_id[bid]].get('name', '')) == name:
            bone_node[bid] = by_id[bid]
        elif name in name_to_node:
            bone_node[bid] = name_to_node[name]
        elif bid in by_id:
            bone_node[bid] = by_id[bid]
    written = []
    multiple = len(anims) > 1
    for a in anims:
        info = _narew(a)
        rate = fps or info.get('framerate') or 30
        spk = 1.0 / fps if fps else (info.get('seconds_per_key') or 1.0 / rate)
        chans = {}
        tmax = 0.0
        for ch in a['channels']:
            tgt = ch['target']
            if 'node' not in tgt or tgt['path'] not in ('translation', 'rotation', 'scale'):
                continue
            smp = a['samplers'][ch['sampler']]
            times = doc.read_accessor(smp['input'])
            vals = doc.read_accessor(smp['output'])
            chans[(tgt['node'], tgt['path'])] = (times, vals, smp.get('interpolation', 'LINEAR'))
            tmax = max(tmax, times[-1] if times else 0.0)
        nframes = int(round(tmax / spk)) + 1
        tracks = []
        for bid, _ in bones:
            ni = bone_node.get(bid)
            k = _sample_track(chans, ni, _rest(g.nodes[ni] if ni is not None else {}), spk, nframes)
            tracks.append(Track(bid, None, None, k['translation'], k['rotation'], k['scale']))
        mesh_tracks = []
        for mt in info.get('mesh_tracks') or []:
            mid, mname = mt[0], mt[1]
            ni = mesh_nodes.get(mname) if mname else None
            if ni is not None and any((ni, p) in chans for p in ('translation', 'rotation', 'scale')):
                k = _sample_track(chans, ni, _rest(g.nodes[ni]), spk, nframes)
                mesh_tracks.append(Track(mid, mname, None, k['translation'], k['rotation'], k['scale']))
            elif len(mt) > 2:      # original keys kept by model2gltf
                mesh_tracks.append(Track(mid, mname, None,
                                         [(int(x[0]), tuple(x[1:])) for x in mt[2]],
                                         [(int(x[0]), tuple(x[1:])) for x in mt[3]],
                                         [(int(x[0]), tuple(x[1:])) for x in mt[4]]))
        start, end = 0, nframes
        if info.get('end') is not None and nframes == info.get('keys', -1):
            start, end = info.get('start', 0), info['end']      # same length: keep timing
        elif tmpl is not None:
            tkeys = max([n for t in tmpl.bones + tmpl.meshes for n, _ in t.pos + t.rot + t.scale]
                        + [-1]) + 1
            if nframes == tkeys:
                start, end = tmpl.start, tmpl.end
        extra = [(tag, _str_to_el(x)) for tag, x in (info.get('sections') or {}).items()]
        if tmpl is not None:        # tags / sounds / effects / UV anims of the original file
            extra = extra or list(tmpl.extra)
            if not info:
                info = {'framerate': tmpl.framerate}
            if not mesh_tracks:
                mesh_tracks = list(tmpl.meshes)
        anim = Animation(None, int(info.get('framerate') or round(rate)), start, end, None,
                         tracks, mesh_tracks, extra, 'root')
        if multiple or os.path.isdir(out):
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, (info.get('file') or a.get('name', 'animation') + '.nwat'))
        else:
            path = out
        with open(path, 'wb') as f:
            f.write(animation_to_xml(anim))
        written.append(path)
    return written
