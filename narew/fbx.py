"""Autodesk FBX reader (binary 7.x and ASCII 7.x) -> :class:`narew.scene3d.Scene`.

Only what a character swap needs is decoded: meshes (positions, normals, UVs, per-polygon
materials), the skeleton (LimbNode/Null models with their bind matrices), skin clusters and
materials with their diffuse textures (embedded ``Video`` content or a file next to the .fbx).

Standard library + numpy.  References: the public FBX binary layout (node records with
32-bit offsets before version 7500, 64-bit after; typed properties, zlib-compressed arrays)
and the FBX SDK's documented property names (Lcl Translation, PreRotation, ...).
"""
from __future__ import annotations

import math
import os
import re
import struct
import zlib

import numpy as np

from .scene3d import Scene, SBone, SMaterial, SMesh, SceneError, read_image_file

MAGIC = b'Kaydara FBX Binary  \x00'


class FbxNode:
    __slots__ = ('name', 'props', 'children')

    def __init__(self, name: str, props: list, children: list):
        self.name = name
        self.props = props
        self.children = children

    def find(self, name: str) -> 'FbxNode | None':
        for c in self.children:
            if c.name == name:
                return c
        return None

    def findall(self, name: str) -> list['FbxNode']:
        return [c for c in self.children if c.name == name]

    def value(self, name: str, default=None):
        c = self.find(name)
        if c is None or not c.props:
            return default
        return c.props[0]

    def __repr__(self):
        return f'FbxNode({self.name!r}, {len(self.props)} props, {len(self.children)} children)'


# --------------------------------------------------------------------------- binary

_ARRAY_TYPES = {ord('f'): ('f', 4), ord('d'): ('d', 8), ord('l'): ('q', 8), ord('i'): ('i', 4),
                ord('b'): ('b', 1), ord('c'): ('b', 1)}
_NP_TYPES = {'f': np.float32, 'd': np.float64, 'q': np.int64, 'i': np.int32, 'b': np.int8}


def _read_props(data: bytes, pos: int, count: int) -> tuple[list, int]:
    out = []
    for _ in range(count):
        t = data[pos]
        pos += 1
        if t == ord('Y'):
            out.append(struct.unpack_from('<h', data, pos)[0]); pos += 2
        elif t == ord('C'):
            out.append(bool(data[pos])); pos += 1
        elif t == ord('I'):
            out.append(struct.unpack_from('<i', data, pos)[0]); pos += 4
        elif t == ord('F'):
            out.append(struct.unpack_from('<f', data, pos)[0]); pos += 4
        elif t == ord('D'):
            out.append(struct.unpack_from('<d', data, pos)[0]); pos += 8
        elif t == ord('L'):
            out.append(struct.unpack_from('<q', data, pos)[0]); pos += 8
        elif t in (ord('S'), ord('R')):
            n = struct.unpack_from('<I', data, pos)[0]; pos += 4
            raw = data[pos:pos + n]; pos += n
            if t == ord('S'):
                out.append(raw.decode('utf-8', errors='replace'))
            else:
                out.append(bytes(raw))
        elif t in _ARRAY_TYPES:
            n, enc, clen = struct.unpack_from('<III', data, pos); pos += 12
            raw = data[pos:pos + clen]; pos += clen
            if enc == 1:
                raw = zlib.decompress(raw)
            code, size = _ARRAY_TYPES[t]
            arr = np.frombuffer(raw, dtype=_NP_TYPES[code], count=n).copy()
            if t == ord('c'):
                arr = arr.astype(bool)
            out.append(arr)
        else:
            raise SceneError(f'FBX: 알 수 없는 속성 형식 {chr(t)!r} (위치 {pos - 1})')
    return out, pos


def _read_node(data: bytes, pos: int, wide: bool) -> tuple[FbxNode | None, int]:
    if wide:
        end, nprops, plen = struct.unpack_from('<QQQ', data, pos); pos += 24
    else:
        end, nprops, plen = struct.unpack_from('<III', data, pos); pos += 12
    nlen = data[pos]; pos += 1
    if end == 0:
        return None, pos + nlen
    name = data[pos:pos + nlen].decode('ascii', errors='replace'); pos += nlen
    props, ppos = _read_props(data, pos, nprops)
    pos += plen
    children = []
    null_len = 25 if wide else 13
    while pos < end:
        if end - pos == null_len and data[pos:end] == b'\0' * null_len:
            pos = end
            break
        child, pos = _read_node(data, pos, wide)
        if child is None:
            break
        children.append(child)
    return FbxNode(name, props, children), end


def parse_binary(data: bytes) -> tuple[FbxNode, int]:
    if not data.startswith(MAGIC):
        raise SceneError('FBX 바이너리 파일이 아닙니다')
    version = struct.unpack_from('<I', data, 23)[0]
    wide = version >= 7500
    pos = 27
    top = []
    while pos < len(data):
        try:
            node, pos = _read_node(data, pos, wide)
        except struct.error:
            break
        if node is None:
            break
        top.append(node)
    return FbxNode('', [], top), version


# --------------------------------------------------------------------------- ASCII

_TOKEN = re.compile(r'\s*(?:;[^\n]*\n|"((?:[^"\\]|\\.)*)"|([A-Za-z_][\w|.\-]*)\s*:|(\{)|(\})|(,)|'
                    r'(\*\d+)|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|([A-Za-z_][\w]*))', re.S)


def parse_ascii(text: str) -> tuple[FbxNode, int]:
    pos = 0
    n = len(text)
    root = FbxNode('', [], [])
    stack = [root]
    cur: FbxNode | None = None          # node whose properties are being read
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            pos += 1
            continue
        pos = m.end()
        s, key, lb, rb, comma, star, num, word = m.groups()
        if key is not None:
            cur = FbxNode(key, [], [])
            stack[-1].children.append(cur)
        elif lb:
            if cur is None:
                cur = FbxNode('', [], [])
                stack[-1].children.append(cur)
            stack.append(cur)
            cur = None
        elif rb:
            if len(stack) > 1:
                node = stack.pop()
                if node.name == 'a' and len(stack) > 0:
                    # "Vertices: *123 { a: 1,2,3 }" -> array property on the parent node
                    parent = stack[-1]
                    owner = parent.children[-1] if parent.children else None
                    if owner is not None and owner.children and owner.children[-1] is node:
                        pass
            cur = None
        elif s is not None:
            if cur is not None:
                cur.props.append(s.replace('\\"', '"'))
        elif num is not None:
            if cur is not None:
                v = float(num) if any(c in num for c in '.eE') else int(num)
                cur.props.append(v)
        elif word is not None:
            if cur is not None:
                cur.props.append(word)
        # commas and *N counts are separators
    _fold_ascii_arrays(root)
    version = 7400
    hdr = root.find('FBXHeaderExtension')
    if hdr is not None and hdr.value('FBXVersion') is not None:
        version = int(hdr.value('FBXVersion'))
    return root, version


def _fold_ascii_arrays(node: FbxNode) -> None:
    """ASCII 7.x writes arrays as ``Name: *N { a: v,v,v }``; turn them into one numpy prop."""
    for c in node.children:
        _fold_ascii_arrays(c)
        if len(c.children) == 1 and c.children[0].name == 'a' and not c.children[0].children:
            vals = c.children[0].props
            if all(isinstance(v, int) for v in vals):
                arr = np.array(vals, dtype=np.int64)
            else:
                arr = np.array(vals, dtype=np.float64)
            c.props = [arr]
            c.children = []


def parse(data: bytes) -> tuple[FbxNode, int]:
    if data.startswith(MAGIC):
        return parse_binary(data)
    head = data[:200].lstrip()
    if head.startswith(b'; FBX') or b'FBXHeaderExtension' in data[:4096]:
        return parse_ascii(data.decode('utf-8', errors='replace'))
    raise SceneError('FBX 파일 형식을 알 수 없습니다 (FBX 6 이전 형식은 지원하지 않음)')


# --------------------------------------------------------------------------- properties

def props70(node: FbxNode | None) -> dict:
    """``Properties70`` (7.x) / ``Properties60`` -> {name: value or tuple}."""
    out = {}
    if node is None:
        return out
    p = node.find('Properties70') or node.find('Properties60')
    if p is None:
        return out
    for c in p.children:
        if not c.props:
            continue
        name = c.props[0]
        vals = c.props[4:] if c.name == 'P' else c.props[3:]
        if len(vals) == 1:
            out[name] = vals[0]
        else:
            out[name] = tuple(vals)
    return out


def _vec(p: dict, key: str, default=(0.0, 0.0, 0.0)) -> np.ndarray:
    v = p.get(key, default)
    if not isinstance(v, (tuple, list)):
        v = default
    return np.array([float(x) for x in v[:3]], dtype=np.float64)


def _euler(deg, order: int = 0) -> np.ndarray:
    """FBX euler (degrees) -> 3x3, column vectors. order 0 = XYZ (x applied first)."""
    x, y, z = (math.radians(a) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    seq = {0: (rx, ry, rz), 1: (rx, rz, ry), 2: (ry, rz, rx), 3: (ry, rx, rz), 4: (rz, rx, ry),
           5: (rz, ry, rx)}.get(int(order), (rx, ry, rz))
    m = np.eye(3)
    for r in seq:            # first rotation applied first -> leftmost multiplication last
        m = r @ m
    return m


def _m4(r3=None, t=None, s=None) -> np.ndarray:
    m = np.eye(4)
    if r3 is not None:
        m[:3, :3] = r3
    if s is not None:
        m[:3, :3] = m[:3, :3] @ np.diag(s)
    if t is not None:
        m[:3, 3] = t
    return m


def local_matrix(p: dict) -> np.ndarray:
    """FBX node transform: T * Roff * Rp * Rpre * R * Rpost^-1 * Rp^-1 * Soff * Sp * S * Sp^-1."""
    order = int(p.get('RotationOrder', 0) or 0)
    T = _m4(t=_vec(p, 'Lcl Translation'))
    Roff = _m4(t=_vec(p, 'RotationOffset'))
    Rp = _m4(t=_vec(p, 'RotationPivot'))
    Rpre = _m4(_euler(_vec(p, 'PreRotation')))
    R = _m4(_euler(_vec(p, 'Lcl Rotation'), order))
    Rpost = _m4(_euler(_vec(p, 'PostRotation')))
    Soff = _m4(t=_vec(p, 'ScalingOffset'))
    Sp = _m4(t=_vec(p, 'ScalingPivot'))
    S = _m4(s=_vec(p, 'Lcl Scaling', (1.0, 1.0, 1.0)))
    return T @ Roff @ Rp @ Rpre @ R @ np.linalg.inv(Rpost) @ np.linalg.inv(Rp) @ Soff @ Sp @ S @ np.linalg.inv(Sp)


def geometric_matrix(p: dict) -> np.ndarray:
    return (_m4(t=_vec(p, 'GeometricTranslation')) @ _m4(_euler(_vec(p, 'GeometricRotation'))) @
            _m4(s=_vec(p, 'GeometricScaling', (1.0, 1.0, 1.0))))


def _is16(arr) -> bool:
    return arr is not None and hasattr(arr, '__len__') and len(arr) == 16


def _mat16(arr) -> np.ndarray:
    """FBX matrices are 16 doubles, column-major for column vectors (translation at 12..14)."""
    return np.array(arr, dtype=np.float64).reshape(4, 4).T


# --------------------------------------------------------------------------- layers

def _layer(geom: FbxNode, kind: str, data_key: str, index_key: str, poly_vertex: np.ndarray,
           polys: list, nverts: int, width: int) -> np.ndarray | None:
    """Expand a LayerElement to one value per polygon-vertex (len(poly_vertex))."""
    el = geom.find(kind)
    if el is None:
        return None
    data = el.value(data_key)
    if data is None:
        return None
    data = np.asarray(data, dtype=np.float64).reshape(-1, width)
    mapping = str(el.value('MappingInformationType', 'ByPolygonVertex'))
    ref = str(el.value('ReferenceInformationType', 'Direct'))
    idx = el.value(index_key)
    count = len(poly_vertex)
    if mapping in ('ByPolygonVertex',):
        sel = np.arange(count)
    elif mapping in ('ByVertice', 'ByVertex', 'ByControlPoint'):
        sel = poly_vertex
    elif mapping == 'ByPolygon':
        sel = np.concatenate([np.full(len(p), i) for i, p in enumerate(polys)]) if polys else np.zeros(0, int)
    elif mapping == 'AllSame':
        sel = np.zeros(count, dtype=np.int64)
    else:
        return None
    if ref in ('IndexToDirect', 'Index') and idx is not None:
        idx = np.asarray(idx, dtype=np.int64)
        sel = idx[np.clip(sel, 0, len(idx) - 1)]
    sel = np.clip(sel, 0, len(data) - 1)
    return data[sel]


def _poly_materials(geom: FbxNode, polys: list) -> np.ndarray:
    el = geom.find('LayerElementMaterial')
    n = len(polys)
    if el is None:
        return np.zeros(n, dtype=np.int64)
    mats = el.value('Materials')
    if mats is None:
        return np.zeros(n, dtype=np.int64)
    mats = np.asarray(mats, dtype=np.int64)
    mapping = str(el.value('MappingInformationType', 'AllSame'))
    if mapping == 'AllSame' or len(mats) < n:
        return np.full(n, int(mats[0]) if len(mats) else 0, dtype=np.int64)
    return mats[:n]


# --------------------------------------------------------------------------- scene

def _obj_name(raw: str) -> str:
    # binary: "name\x00\x01Class"; ASCII: "Class::name"
    if '\x00\x01' in raw:
        return raw.split('\x00\x01')[0]
    if '::' in raw:
        return raw.split('::', 1)[1]
    return raw


def load_scene(path: str) -> Scene:
    with open(path, 'rb') as f:
        data = f.read()
    root, version = parse(data)
    base = os.path.dirname(os.path.abspath(path))
    sc = Scene(source=path, fmt=f'FBX {version // 1000}.{version % 1000 // 100}')

    objects = root.find('Objects')
    if objects is None:
        raise SceneError('FBX 안에 Objects 섹션이 없습니다')
    objs: dict[int, FbxNode] = {}
    for o in objects.children:
        if o.props:
            try:
                objs[int(o.props[0])] = o
            except (TypeError, ValueError):
                continue
    # connections: child -> parents, parent -> children (with property names for OP)
    parents: dict[int, list] = {}
    children: dict[int, list] = {}
    conns = root.find('Connections')
    for c in (conns.children if conns is not None else []):
        if len(c.props) < 3:
            continue
        kind, a, b = c.props[0], c.props[1], c.props[2]
        try:
            a, b = int(a), int(b)
        except (TypeError, ValueError):
            continue
        prop = c.props[3] if len(c.props) > 3 else None
        parents.setdefault(a, []).append((b, prop))
        children.setdefault(b, []).append((a, prop))

    def kids(oid, name=None):
        return [objs[c] for c, _ in children.get(oid, []) if c in objs and (name is None or objs[c].name == name)]

    # ---- axis system -> right-handed, +Y up
    gs = props70(root.find('GlobalSettings'))
    up, up_s = int(gs.get('UpAxis', 1)), int(gs.get('UpAxisSign', 1))
    fr, fr_s = int(gs.get('FrontAxis', 2)), int(gs.get('FrontAxisSign', 1))
    co, co_s = int(gs.get('CoordAxis', 0)), int(gs.get('CoordAxisSign', 1))
    if len({up, fr, co}) != 3:
        up, up_s, fr, fr_s, co, co_s = 1, 1, 2, 1, 0, 1
    axes = np.zeros((3, 3))
    axes[0, co] = co_s
    axes[1, up] = up_s
    axes[2, fr] = fr_s
    conv = np.eye(4)
    conv[:3, :3] = axes                     # p_new = axes @ p_file
    if np.linalg.det(axes) < 0:             # left-handed file: mirror x
        conv[0, :3] *= -1
    unit = float(gs.get('UnitScaleFactor', 1.0) or 1.0)
    conv = np.diag([unit / 100.0] * 3 + [1.0]) @ conv          # -> metres
    sc.notes.append(f'축: up={"XYZ"[up]}{"+" if up_s > 0 else "-"}, front={"XYZ"[fr]}{"+" if fr_s > 0 else "-"}, '
                    f'단위 {unit:g}cm')

    # ---- models and their global transforms (current pose)
    models = {oid: o for oid, o in objs.items() if o.name == 'Model'}
    model_props = {oid: props70(o) for oid, o in models.items()}
    model_parent = {}
    for oid in models:
        for p, prop in parents.get(oid, []):
            if p in models:
                model_parent[oid] = p
                break
    glob_cache: dict[int, np.ndarray] = {}

    def glob(oid):
        if oid in glob_cache:
            return glob_cache[oid]
        m = local_matrix(model_props[oid])
        p = model_parent.get(oid)
        g = (glob(p) @ m) if p is not None else m
        glob_cache[oid] = g
        return g

    # bind pose matrices (Pose / BindPose)
    bindpose: dict[int, np.ndarray] = {}
    for o in objs.values():
        if o.name == 'Pose' and len(o.props) > 2 and str(o.props[2]) == 'BindPose':
            for pn in o.findall('PoseNode'):
                nid, mat = pn.value('Node'), pn.value('Matrix')
                if nid is not None and _is16(mat):
                    bindpose[int(nid)] = _mat16(mat)

    # ---- skin clusters: bone model id -> TransformLink
    cluster_link: dict[int, np.ndarray] = {}
    mesh_skins: dict[int, list] = {}          # geometry id -> [(bone model id, indexes, weights, transform)]
    for oid, o in objs.items():
        if o.name != 'Deformer' or len(o.props) < 3 or str(o.props[2]) != 'Skin':
            continue
        geoms = [p for p, _ in parents.get(oid, []) if p in objs and objs[p].name == 'Geometry']
        clusters = []
        for cid, _ in children.get(oid, []):
            c = objs.get(cid)
            if c is None or c.name != 'Deformer':
                continue
            bones = [b for b, _ in children.get(cid, []) if b in models]
            if not bones:
                continue
            idx = c.value('Indexes')
            w = c.value('Weights')
            tl = c.value('TransformLink')
            tr = c.value('Transform')
            link = _mat16(tl) if _is16(tl) else None
            clusters.append((bones[0], np.asarray(idx if idx is not None else [], dtype=np.int64),
                             np.asarray(w if w is not None else [], dtype=np.float64),
                             _mat16(tr) if _is16(tr) else np.eye(4), link))
            if link is not None:
                cluster_link.setdefault(bones[0], link)
        for g in geoms:
            mesh_skins.setdefault(g, []).extend(clusters)

    # ---- skeleton: LimbNode / Null / Root models that are bones or ancestors of bones
    def model_type(oid):
        o = models[oid]
        return str(o.props[2]) if len(o.props) > 2 else ''

    bone_ids = [oid for oid in models if model_type(oid) in ('LimbNode', 'Limb', 'Root', 'Skeleton')]
    bone_set = set(bone_ids) | set(cluster_link)
    # ancestors of skinned bones that are Null nodes (armature roots) become bones too
    for oid in list(bone_set):
        p = model_parent.get(oid)
        while p is not None and p not in bone_set and model_type(p) in ('Null', 'LimbNode', 'Root'):
            bone_set.add(p)
            p = model_parent.get(p)
    # bind world of every bone: cluster TransformLink > BindPose > current global pose.
    # Bones without a cluster keep their offset to the nearest bound ancestor.
    bind_world: dict[int, np.ndarray] = {}

    def bone_bind(oid):
        if oid in bind_world:
            return bind_world[oid]
        if oid in cluster_link:
            m = cluster_link[oid]
        elif oid in bindpose:
            m = bindpose[oid]
        else:
            p = model_parent.get(oid)
            if p is not None and p in bone_set:
                m = bone_bind(p) @ local_matrix(model_props[oid])
            else:
                m = glob(oid)
        bind_world[oid] = m
        return m

    order = []
    seen = set()

    def visit(oid):
        if oid in seen:
            return
        p = model_parent.get(oid)
        if p is not None and p in bone_set:
            visit(p)
        seen.add(oid)
        order.append(oid)

    for oid in sorted(bone_set, key=lambda i: list(models).index(i)):
        visit(oid)
    bone_index = {}
    for oid in order:
        p = model_parent.get(oid)
        pi = bone_index.get(p, -1) if p is not None else -1
        w = conv @ bone_bind(oid)
        bone_index[oid] = len(sc.bones)
        sc.bones.append(SBone(_obj_name(models[oid].props[1]), pi, w))

    # ---- materials / textures
    mat_index: dict[int, int] = {}

    def texture_of(mid) -> tuple[bytes | None, str | None]:
        best = None
        for tid, prop in children.get(mid, []):
            t = objs.get(tid)
            if t is None or t.name != 'Texture':
                continue
            pname = str(prop or '')
            score = 0 if 'Diffuse' in pname or 'Base' in pname or 'Albedo' in pname else 1
            if best is None or score < best[0]:
                best = (score, tid, t)
        if best is None:
            return None, None
        _, tid, t = best
        # embedded content (Video)
        for vid, _ in children.get(tid, []):
            v = objs.get(vid)
            if v is not None and v.name == 'Video':
                content = v.value('Content')
                fname = v.value('RelativeFilename') or v.value('FileName') or ''
                if isinstance(content, (bytes, bytearray)) and len(content) > 16:
                    return bytes(content), os.path.basename(str(fname).replace('\\', '/')) or 'texture.png'
        for key in ('RelativeFilename', 'FileName', 'Filename'):
            fname = t.value(key)
            if fname:
                data, name = read_image_file(str(fname), base)
                if data:
                    return data, name
        fname = t.value('RelativeFilename') or t.value('FileName') or ''
        return None, os.path.basename(str(fname).replace('\\', '/')) or None

    def material(mid) -> int:
        if mid in mat_index:
            return mat_index[mid]
        o = objs[mid]
        p = props70(o)
        col = p.get('DiffuseColor') or p.get('Diffuse') or (0.8, 0.8, 0.8)
        if not isinstance(col, tuple):
            col = (0.8, 0.8, 0.8)
        data, name = texture_of(mid)
        alpha = 1.0 - float(p.get('TransparencyFactor', 0.0) or 0.0) if 'Opacity' not in p else float(p['Opacity'])
        m = SMaterial(_obj_name(o.props[1]) if len(o.props) > 1 else f'mat{mid}',
                      tuple(float(c) for c in col[:3]) + (max(0.0, min(1.0, alpha)),), data, name)
        mat_index[mid] = len(sc.materials)
        sc.materials.append(m)
        return mat_index[mid]

    # ---- meshes
    for gid, g in objs.items():
        if g.name != 'Geometry' or (len(g.props) > 2 and str(g.props[2]) != 'Mesh'):
            continue
        verts = g.value('Vertices')
        pvi = g.value('PolygonVertexIndex')
        if verts is None or pvi is None:
            continue
        verts = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
        pvi = np.asarray(pvi, dtype=np.int64)
        owners = [p for p, _ in parents.get(gid, []) if p in models]
        if not owners:
            continue
        mid = owners[0]
        # polygons
        polys = []
        start = 0
        ends = np.nonzero(pvi < 0)[0]
        for e in ends:
            polys.append(np.arange(start, e + 1))
            start = e + 1
        cp = np.where(pvi < 0, -pvi - 1, pvi)          # control point per polygon-vertex
        normals = _layer(g, 'LayerElementNormal', 'Normals', 'NormalsIndex', cp, polys, len(verts), 3)
        if normals is None:
            normals = _layer(g, 'LayerElementNormal', 'Normals', 'NormalIndex', cp, polys, len(verts), 3)
        uvs = _layer(g, 'LayerElementUV', 'UV', 'UVIndex', cp, polys, len(verts), 2)
        pmat = _poly_materials(g, polys)
        mats = [m for m, _ in children.get(mid, []) if m in objs and objs[m].name == 'Material']
        mat_ids = [material(m) for m in mats] or [None]

        # unique corners (control point, normal, uv) -> vertices
        key_parts = [cp[:, None].astype(np.float64)]
        if normals is not None:
            key_parts.append(np.round(normals, 5))
        if uvs is not None:
            key_parts.append(np.round(uvs, 6))
        keys = np.concatenate(key_parts, axis=1)
        uniq, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        inverse = inverse.reshape(-1)
        vcp = cp[first]

        # world transform of the geometry at bind time
        skins = mesh_skins.get(gid, [])
        geo = geometric_matrix(model_props[mid])
        linked = [c for c in skins if c[4] is not None]
        if linked:
            # the file's cluster "Transform" is relative to its bone: mesh bind world =
            # TransformLink * Transform (what Blender's importer does as well)
            mesh_world = linked[0][4] @ linked[0][3] @ geo
        elif skins:
            mesh_world = skins[0][3] @ geo
        elif mid in bindpose:
            mesh_world = bindpose[mid] @ geo
        else:
            mesh_world = glob(mid) @ geo
        to_scene = conv @ mesh_world
        p4 = np.concatenate([verts[vcp], np.ones((len(vcp), 1))], axis=1)
        pos = (to_scene @ p4.T).T[:, :3]
        nrm = None
        if normals is not None:
            nm = np.linalg.inv(to_scene[:3, :3]).T
            nrm = (nm @ normals[first].T).T
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = nrm / np.where(ln > 1e-12, ln, 1.0)
        uv = None
        if uvs is not None:
            uv = uvs[first].copy()
            uv[:, 1] = 1.0 - uv[:, 1]          # FBX: origin bottom-left -> top-left (glTF/D3D)
        # triangulate (fan) per polygon, grouped by material
        flip = np.linalg.det(to_scene[:3, :3]) < 0
        tris, tmat = [], []
        for pi_, poly in enumerate(polys):
            vids = inverse[poly]
            for k in range(1, len(vids) - 1):
                t = (vids[0], vids[k + 1], vids[k]) if flip else (vids[0], vids[k], vids[k + 1])
                tris.append(t)
                tmat.append(int(pmat[pi_]) if pi_ < len(pmat) else 0)
        if not tris:
            continue
        faces = np.array(tris, dtype=np.int64)
        fm = np.array([mat_ids[m] if 0 <= m < len(mat_ids) and mat_ids[m] is not None else -1
                       for m in tmat], dtype=np.int64)
        # skin: control point -> [(bone, w)]
        joints = weights = None
        if skins:
            per_cp: list[list] = [[] for _ in range(len(verts))]
            for bone_mid, idx, w, _tr, _ln in skins:
                bi = bone_index.get(bone_mid)
                if bi is None:
                    continue
                for i, ww in zip(idx, w):
                    if 0 <= i < len(per_cp) and ww > 0:
                        per_cp[i].append((bi, float(ww)))
            k = max(1, max((len(x) for x in per_cp), default=1))
            jt = np.zeros((len(verts), k), dtype=np.int64)
            wt = np.zeros((len(verts), k), dtype=np.float64)
            for i, lst in enumerate(per_cp):
                lst.sort(key=lambda t: -t[1])
                for j, (b, ww) in enumerate(lst):
                    jt[i, j], wt[i, j] = b, ww
            joints, weights = jt[vcp], wt[vcp]
        elif mid in bone_set or model_parent.get(mid) in bone_set:
            # mesh parented to a bone: rigid
            b = bone_index.get(mid if mid in bone_set else model_parent[mid])
            joints = np.full((len(vcp), 1), b, dtype=np.int64)
            weights = np.ones((len(vcp), 1))
        sc.meshes.append(SMesh(_obj_name(models[mid].props[1]), pos, nrm, uv, faces, fm, joints, weights))
    if not sc.meshes:
        raise SceneError('FBX 안에 메시가 없습니다')
    return sc


def dump(path: str, depth: int = 3) -> str:
    """Debug: the node tree as text."""
    with open(path, 'rb') as f:
        root, version = parse(f.read())
    out = [f'FBX version {version}']

    def rec(n: FbxNode, d: int):
        if d > depth:
            return
        ps = []
        for p in n.props[:4]:
            if isinstance(p, np.ndarray):
                ps.append(f'array[{len(p)}]')
            elif isinstance(p, bytes):
                ps.append(f'raw[{len(p)}]')
            else:
                ps.append(repr(p)[:60])
        out.append('  ' * d + f'{n.name}: {", ".join(ps)}' + (f'  ({len(n.children)})' if n.children else ''))
        for c in n.children:
            rec(c, d + 1)

    for c in root.children:
        rec(c, 0)
    return '\n'.join(out)
