"""MikuMikuDance PMX 2.0/2.1 -> :class:`narew.scene3d.Scene`.

PMX is left-handed (+Y up, models face -Z, about 8 cm per unit); it is mirrored into the
scene's right-handed space.  SDEF/QDEF weights are read as BDEF2/BDEF4.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from .scene3d import Scene, SBone, SMaterial, SMesh, SceneError, read_image_file


class _R:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, fmt: str):
        v = struct.unpack_from('<' + fmt, self.d, self.p)
        self.p += struct.calcsize('<' + fmt)
        return v

    def i(self, size: int, signed: bool = True) -> int:
        code = {1: 'b', 2: 'h', 4: 'i'}[size]
        if not signed:
            code = code.upper()
        return self.take(code)[0]

    def text(self, enc: int) -> str:
        n = self.take('i')[0]
        raw = self.d[self.p:self.p + n]
        self.p += n
        return raw.decode('utf-16-le' if enc == 0 else 'utf-8', errors='replace')


def load_scene(path: str) -> Scene:
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'PMX ':
        raise SceneError('PMX 파일이 아닙니다 (PMD는 PMX로 변환해서 쓰세요)')
    r = _R(data)
    r.p = 4
    version = r.take('f')[0]
    n = r.take('B')[0]
    g = list(r.take(f'{n}B'))
    enc, add_uv, vsz, tsz, msz, bsz = g[0], g[1], g[2], g[3], g[4], g[5]
    for _ in range(4):
        r.text(enc)
    sc = Scene(source=path, fmt=f'PMX {version:.1f}')
    base = os.path.dirname(os.path.abspath(path))
    # vertices
    nv = r.take('i')[0]
    pos = np.zeros((nv, 3))
    nrm = np.zeros((nv, 3))
    uv = np.zeros((nv, 2))
    jt = np.zeros((nv, 4), dtype=np.int64)
    wt = np.zeros((nv, 4))
    for v in range(nv):
        vals = r.take('8f')
        pos[v], nrm[v], uv[v] = vals[0:3], vals[3:6], vals[6:8]
        r.p += 16 * add_uv
        kind = r.take('B')[0]
        if kind == 0:
            jt[v, 0], wt[v, 0] = r.i(bsz), 1.0
        elif kind == 1:
            a, b = r.i(bsz), r.i(bsz)
            w = r.take('f')[0]
            jt[v, :2], wt[v, :2] = (a, b), (w, 1 - w)
        elif kind in (2, 4):
            jt[v] = [r.i(bsz) for _ in range(4)]
            wt[v] = r.take('4f')
        elif kind == 3:
            a, b = r.i(bsz), r.i(bsz)
            w = r.take('f')[0]
            r.p += 36
            jt[v, :2], wt[v, :2] = (a, b), (w, 1 - w)
        else:
            raise SceneError(f'PMX: 알 수 없는 가중치 형식 {kind}')
        r.p += 4                      # edge scale
    # faces
    ni = r.take('i')[0]
    code = {1: 'B', 2: 'H', 4: 'i'}[vsz]
    idx = np.array(struct.unpack_from(f'<{ni}{code}', data, r.p), dtype=np.int64)
    r.p += ni * vsz
    faces = idx[:ni // 3 * 3].reshape(-1, 3)
    # textures
    nt = r.take('i')[0]
    tex = [r.text(enc) for _ in range(nt)]
    # materials
    nm = r.take('i')[0]
    fmat = np.zeros(len(faces), dtype=np.int64)
    start = 0
    cache = {}
    for mi in range(nm):
        name = r.text(enc)
        r.text(enc)
        diffuse = r.take('4f')
        r.take('3f'); r.take('f'); r.take('3f')
        flags = r.take('B')[0]
        r.take('4f'); r.take('f')
        ti = r.i(tsz)
        r.i(tsz)
        r.take('B')
        toon_shared = r.take('B')[0]
        if toon_shared == 0:
            r.i(tsz)
        else:
            r.take('B')
        r.text(enc)
        count = r.take('i')[0]
        fmat[start // 3:(start + count) // 3] = mi
        start += count
        img = fn = None
        if 0 <= ti < len(tex):
            if ti not in cache:
                cache[ti] = read_image_file(tex[ti], base)
            img, fn = cache[ti]
        sc.materials.append(SMaterial(name or f'material{mi}', tuple(diffuse), img, fn, bool(flags & 1)))
    # bones
    nb = r.take('i')[0]
    for bi in range(nb):
        name = r.text(enc)
        name_en = r.text(enc)
        p = r.take('3f')
        parent = r.i(bsz)
        r.take('i')
        flags = r.take('H')[0]
        if flags & 0x0001:
            r.i(bsz)
        else:
            r.take('3f')
        if flags & 0x0300:
            r.i(bsz)
            r.take('f')
        if flags & 0x0400:
            r.take('3f')
        if flags & 0x0800:
            r.take('6f')
        if flags & 0x2000:
            r.take('i')
        if flags & 0x0020:
            r.i(bsz)
            r.take('i'); r.take('f')
            links = r.take('i')[0]
            for _ in range(links):
                r.i(bsz)
                if r.take('B')[0]:
                    r.take('6f')
        m = np.eye(4)
        m[:3, 3] = p
        sc.bones.append(SBone(name if name.strip() else name_en, parent if 0 <= parent < nb else -1, m))
    # bones must come parents-first for the rest of the pipeline
    jt = np.clip(jt, 0, max(nb - 1, 0))
    sc.meshes.append(SMesh(os.path.splitext(os.path.basename(path))[0], pos, nrm, uv, faces, fmat,
                           jt if nb else None, wt if nb else None))
    _reorder_bones(sc)
    # PMX is left-handed (DirectX): mirror z into the right-handed scene space
    sc.transform(np.diag([1.0, 1.0, -1.0, 1.0]))
    return sc


def _reorder_bones(sc: Scene) -> None:
    n = len(sc.bones)
    order, seen = [], set()

    def visit(i, guard=0):
        if i in seen or guard > n:
            return
        p = sc.bones[i].parent
        if p >= 0:
            visit(p, guard + 1)
        seen.add(i)
        order.append(i)

    for i in range(n):
        visit(i)
    if order == list(range(n)):
        return
    new_index = {old: k for k, old in enumerate(order)}
    sc.bones = [sc.bones[i] for i in order]
    for b in sc.bones:
        if b.parent >= 0:
            b.parent = new_index[b.parent]
    for m in sc.meshes:
        if m.joints is not None:
            m.joints = np.vectorize(new_index.get)(m.joints)
