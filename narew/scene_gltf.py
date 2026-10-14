"""glTF 2.0 / GLB / VRM (0.x and 1.0) -> :class:`narew.scene3d.Scene`.

Skinned meshes are evaluated in the file's rest pose (joint world x inverse bind matrix), which
is what any viewer shows, and the joints' rest world matrices become the bind pose.  VRM files
carry a humanoid bone map that is used instead of guessing from names.
"""
from __future__ import annotations

import os

import numpy as np

from . import gltf as G
from .scene3d import Scene, SBone, SMaterial, SMesh, SceneError

_DT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
_NORM = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}


def read(doc: G.Document, index: int) -> np.ndarray:
    acc = doc.json['accessors'][index]
    n = _NC[acc['type']]
    dt = np.dtype(_DT[acc['componentType']])
    count = acc['count']
    if 'bufferView' in acc:
        data, stride = doc.view_bytes(acc['bufferView'])
        off = acc.get('byteOffset', 0)
        elem = n * dt.itemsize
        if not stride or stride == elem:
            arr = np.frombuffer(data, dtype=dt, count=count * n, offset=off).reshape(count, n)
        else:
            raw = np.frombuffer(data, dtype=np.uint8, count=stride * (count - 1) + elem, offset=off)
            rows = np.lib.stride_tricks.as_strided(raw, shape=(count, elem), strides=(stride, 1))
            arr = np.frombuffer(rows.copy().tobytes(), dtype=dt).reshape(count, n)
        arr = arr.astype(np.float64 if dt.kind == 'f' else np.int64)
    else:
        arr = np.zeros((count, n), dtype=np.float64 if dt.kind == 'f' else np.int64)
    sp = acc.get('sparse')
    if sp:
        it = np.dtype(_DT[sp['indices']['componentType']])
        idata, _ = doc.view_bytes(sp['indices']['bufferView'])
        idx = np.frombuffer(idata, dtype=it, count=sp['count'], offset=sp['indices'].get('byteOffset', 0))
        vdata, _ = doc.view_bytes(sp['values']['bufferView'])
        vals = np.frombuffer(vdata, dtype=dt, count=sp['count'] * n, offset=sp['values'].get('byteOffset', 0))
        arr = arr.copy()
        arr[idx.astype(np.int64)] = vals.reshape(-1, n)
    if acc.get('normalized') and acc['componentType'] in _NORM:
        arr = np.maximum(arr / _NORM[acc['componentType']], -1.0)
    return arr


def _node_local(n: dict) -> np.ndarray:
    if 'matrix' in n:
        return np.array(n['matrix'], dtype=np.float64).reshape(4, 4).T
    t = n.get('translation', [0, 0, 0])
    x, y, z, w = n.get('rotation', [0, 0, 0, 1])
    s = n.get('scale', [1, 1, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r @ np.diag(s)
    m[:3, 3] = t
    return m


VRM0 = {'hips': 'hips', 'spine': None, 'chest': None, 'upperChest': None, 'neck': 'neck', 'head': 'head',
        'leftShoulder': 'clavicle.L', 'leftUpperArm': 'upperarm.L', 'leftLowerArm': 'forearm.L',
        'leftHand': 'hand.L', 'leftUpperLeg': 'thigh.L', 'leftLowerLeg': 'calf.L', 'leftFoot': 'foot.L',
        'leftToes': 'toe.L'}
for _k, _v in list(VRM0.items()):
    if _k.startswith('left'):
        VRM0['right' + _k[4:]] = _v.replace('.L', '.R') if _v else None


def _vrm_humanoid(js: dict) -> dict:
    """VRM humanBones -> {slot: node index}."""
    ext = js.get('extensions', {})
    out = {}
    bones = {}
    if 'VRMC_vrm' in ext:
        hb = ext['VRMC_vrm'].get('humanoid', {}).get('humanBones', {})
        bones = {k: v.get('node') for k, v in hb.items() if isinstance(v, dict)}
    elif 'VRM' in ext:
        for b in ext['VRM'].get('humanoid', {}).get('humanBones', []):
            bones[b.get('bone')] = b.get('node')
    for k, node in bones.items():
        slot = VRM0.get(k)
        if slot and node is not None and node >= 0:
            out[slot] = node
    return out


def load_scene(path: str) -> Scene:
    try:
        doc = G.load(path)
    except Exception as ex:
        raise SceneError(f'glTF를 읽을 수 없습니다: {ex}')
    js = doc.json
    nodes = js.get('nodes', [])
    ext = os.path.splitext(path)[1].lower()
    fmt = 'VRM' if ('VRM' in js.get('extensions', {}) or 'VRMC_vrm' in js.get('extensions', {})) else 'glTF'
    sc = Scene(source=path, fmt=fmt if ext != '.vrm' else 'VRM')
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            parent[c] = i
    world: dict[int, np.ndarray] = {}

    def W(i):
        if i not in world:
            m = _node_local(nodes[i])
            world[i] = W(parent[i]) @ m if i in parent else m
        return world[i]

    # bones = joints of all skins (+ ancestors that are joints)
    joint_nodes = []
    for sk in js.get('skins', []):
        for j in sk.get('joints', []):
            if j not in joint_nodes:
                joint_nodes.append(j)
    jset = set(joint_nodes)
    # add non-joint ancestors between joints so the hierarchy stays connected
    for j in list(joint_nodes):
        p = parent.get(j)
        chain = []
        while p is not None and p not in jset:
            chain.append(p)
            p = parent.get(p)
        if p is not None:
            for c in chain:
                jset.add(c)
                joint_nodes.append(c)

    def depth(i):
        d = 0
        while i in parent:
            i = parent[i]
            d += 1
        return d

    joint_nodes.sort(key=depth)
    bone_of = {}
    for j in joint_nodes:
        p = parent.get(j)
        while p is not None and p not in jset:
            p = parent.get(p)
        bone_of[j] = len(sc.bones)
        sc.bones.append(SBone(nodes[j].get('name') or f'node{j}', bone_of.get(p, -1) if p is not None else -1, W(j)))
    hum = _vrm_humanoid(js)
    if hum:
        sc.humanoid = {k: bone_of[v] for k, v in hum.items() if v in bone_of}

    # materials
    images = {}
    for mi, m in enumerate(js.get('materials', [])):
        pbr = m.get('pbrMetallicRoughness', {})
        col = tuple(pbr.get('baseColorFactor', [1, 1, 1, 1]))
        data = name = None
        bt = pbr.get('baseColorTexture')
        if bt is not None:
            tex = js.get('textures', [])[bt['index']]
            src = tex.get('source')
            if src is None:
                for e in tex.get('extensions', {}).values():
                    src = e.get('source', src)
            if src is not None:
                if src not in images:
                    images[src] = doc.image_bytes(src)
                data = images[src][0]
                im = js['images'][src]
                name = im.get('name') or os.path.basename(im.get('uri', '')) or f'image{src}'
        sc.materials.append(SMaterial(m.get('name') or f'material{mi}', col, data, name, bool(m.get('doubleSided'))))

    # meshes
    for ni, n in enumerate(nodes):
        if 'mesh' not in n:
            continue
        mesh = js['meshes'][n['mesh']]
        skin = js['skins'][n['skin']] if 'skin' in n else None
        if skin is not None:
            jm = [W(j) for j in skin['joints']]
            ibm = (read(doc, skin['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1)
                   if 'inverseBindMatrices' in skin else np.repeat(np.eye(4)[None], len(jm), axis=0))
            skin_m = np.array([a @ b for a, b in zip(jm, ibm)])
            jbone = np.array([bone_of[j] for j in skin['joints']])
        for pi, prim in enumerate(mesh.get('primitives', [])):
            mode = prim.get('mode', 4)
            if mode not in (4, 5, 6):
                continue
            at = prim['attributes']
            if 'POSITION' not in at:
                continue
            pos = read(doc, at['POSITION'])[:, :3]
            nrm = read(doc, at['NORMAL'])[:, :3] if 'NORMAL' in at else None
            uv = read(doc, at['TEXCOORD_0'])[:, :2] if 'TEXCOORD_0' in at else None
            if 'indices' in prim:
                idx = read(doc, prim['indices']).reshape(-1).astype(np.int64)
            else:
                idx = np.arange(len(pos))
            if mode == 4:
                faces = idx[:len(idx) // 3 * 3].reshape(-1, 3)
            elif mode == 5:
                faces = np.array([(idx[k], idx[k + 1], idx[k + 2]) if k % 2 == 0 else (idx[k + 1], idx[k], idx[k + 2])
                                  for k in range(len(idx) - 2)], dtype=np.int64).reshape(-1, 3)
            else:
                faces = np.array([(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)],
                                 dtype=np.int64).reshape(-1, 3)
            joints = weights = None
            if skin is not None and 'JOINTS_0' in at and 'WEIGHTS_0' in at:
                js_ = [read(doc, at['JOINTS_0']).astype(np.int64)]
                ws_ = [read(doc, at['WEIGHTS_0'])]
                if 'JOINTS_1' in at and 'WEIGHTS_1' in at:
                    js_.append(read(doc, at['JOINTS_1']).astype(np.int64))
                    ws_.append(read(doc, at['WEIGHTS_1']))
                jl = np.clip(np.concatenate(js_, axis=1), 0, len(jbone) - 1)
                weights = np.concatenate(ws_, axis=1).astype(np.float64)
                tot = weights.sum(axis=1, keepdims=True)
                wn = weights / np.where(tot > 1e-12, tot, 1.0)
                M = np.einsum('vk,vkij->vij', wn, skin_m[jl])
                p4 = np.concatenate([pos, np.ones((len(pos), 1))], axis=1)
                pos = np.einsum('vij,vj->vi', M, p4)[:, :3]
                if nrm is not None:
                    nrm = np.einsum('vij,vj->vi', M[:, :3, :3], nrm)
                joints = jbone[jl]
            else:
                w = W(ni)
                pos = (w[:3, :3] @ pos.T).T + w[:3, 3]
                if nrm is not None:
                    nrm = (np.linalg.inv(w[:3, :3]).T @ nrm.T).T
                if np.linalg.det(w[:3, :3]) < 0:
                    faces = faces[:, [0, 2, 1]]
                # a mesh hanging under a joint follows that joint rigidly
                p = ni
                while p is not None and p not in bone_of:
                    p = parent.get(p)
                if p is not None:
                    joints = np.full((len(pos), 1), bone_of[p], dtype=np.int64)
                    weights = np.ones((len(pos), 1))
            if nrm is not None:
                ln = np.linalg.norm(nrm, axis=1, keepdims=True)
                nrm = nrm / np.where(ln > 1e-12, ln, 1.0)
            mat = prim.get('material', -1)
            sc.meshes.append(SMesh(f"{mesh.get('name') or n.get('name') or 'mesh'}" + (f'_{pi}' if pi else ''),
                                   pos, nrm, uv, faces, np.full(len(faces), mat, dtype=np.int64), joints, weights))
    if not sc.meshes:
        raise SceneError('glTF 안에 메시가 없습니다')
    return sc
