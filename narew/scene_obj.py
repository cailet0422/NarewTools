"""Wavefront OBJ (+ MTL) -> :class:`narew.scene3d.Scene` (a static mesh, no skeleton)."""
from __future__ import annotations

import os

import numpy as np

from .scene3d import Scene, SMaterial, SMesh, SceneError, read_image_file


def _read_mtl(path: str, base: str) -> dict:
    mats = {}
    cur = None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return mats
    for line in lines:
        parts = line.strip().split(None, 1)
        if not parts or parts[0].startswith('#'):
            continue
        key = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ''
        if key == 'newmtl':
            cur = mats.setdefault(rest, {'Kd': (1.0, 1.0, 1.0), 'd': 1.0, 'map': None})
        elif cur is None:
            continue
        elif key == 'kd':
            try:
                cur['Kd'] = tuple(float(x) for x in rest.split()[:3])
            except ValueError:
                pass
        elif key == 'd':
            try:
                cur['d'] = float(rest.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == 'map_kd':
            # options like "-s 1 1 1 file.png": the file name is the last token(s)
            toks = rest.split()
            name = rest
            while toks and toks[0].startswith('-'):
                toks = toks[2:] if toks[0] in ('-bm', '-boost', '-mm', '-texres', '-imfchan', '-blendu', '-blendv',
                                               '-clamp', '-cc') else toks[4:]
                name = ' '.join(toks)
            cur['map'] = name
    return mats


def load_scene(path: str) -> Scene:
    base = os.path.dirname(os.path.abspath(path))
    sc = Scene(source=path, fmt='OBJ')
    V, VT, VN = [], [], []
    groups: dict[str, list] = {}
    order: list[str] = []
    mtl = 'default'
    mtl_files = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if not line or line[0] == '#':
                continue
            parts = line.split()
            if not parts:
                continue
            k = parts[0]
            if k == 'v':
                V.append([float(x) for x in parts[1:4]])
            elif k == 'vt':
                VT.append([float(x) for x in parts[1:3]] + [0.0] * (2 - len(parts[1:3])))
            elif k == 'vn':
                VN.append([float(x) for x in parts[1:4]])
            elif k == 'f':
                corners = []
                for c in parts[1:]:
                    s = c.split('/')
                    vi = int(s[0])
                    ti = int(s[1]) if len(s) > 1 and s[1] else 0
                    ni = int(s[2]) if len(s) > 2 and s[2] else 0
                    corners.append((vi - 1 if vi > 0 else len(V) + vi,
                                    (ti - 1 if ti > 0 else len(VT) + ti) if ti else -1,
                                    (ni - 1 if ni > 0 else len(VN) + ni) if ni else -1))
                if mtl not in groups:
                    groups[mtl] = []
                    order.append(mtl)
                for i in range(1, len(corners) - 1):
                    groups[mtl].append((corners[0], corners[i], corners[i + 1]))
            elif k == 'usemtl':
                mtl = ' '.join(parts[1:]) or 'default'
            elif k == 'mtllib':
                mtl_files.append(' '.join(parts[1:]))
    if not V or not groups:
        raise SceneError('OBJ 안에 면이 없습니다')
    mats = {}
    for m in mtl_files:
        p = m if os.path.isabs(m) else os.path.join(base, m)
        mats.update(_read_mtl(p, base))
    V = np.array(V, dtype=np.float64)
    VT = np.array(VT, dtype=np.float64) if VT else None
    VN = np.array(VN, dtype=np.float64) if VN else None
    mat_index = {}
    for name in order:
        info = mats.get(name, {})
        data, fn = read_image_file(info.get('map') or '', base) if info.get('map') else (None, None)
        mat_index[name] = len(sc.materials)
        sc.materials.append(SMaterial(name, tuple(info.get('Kd', (1.0, 1.0, 1.0))) + (info.get('d', 1.0),), data, fn))
    for name in order:
        tris = groups[name]
        corners = np.array([c for t in tris for c in t], dtype=np.int64)
        uniq, inv = np.unique(corners, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        pos = V[np.clip(uniq[:, 0], 0, len(V) - 1)]
        uv = None
        if VT is not None and (uniq[:, 1] >= 0).any():
            uv = VT[np.clip(uniq[:, 1], 0, len(VT) - 1)].copy()
            uv[:, 1] = 1.0 - uv[:, 1]          # OBJ: origin bottom-left
        nrm = None
        if VN is not None and (uniq[:, 2] >= 0).all():
            nrm = VN[uniq[:, 2]]
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = nrm / np.where(ln > 1e-12, ln, 1.0)
        faces = inv.reshape(-1, 3)
        sc.meshes.append(SMesh(name, pos, nrm, uv, faces, np.full(len(faces), mat_index[name], dtype=np.int64)))
    sc.notes.append('OBJ는 뼈대가 없는 형식입니다')
    return sc
