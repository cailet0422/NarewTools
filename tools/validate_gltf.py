"""Structural glTF 2.0 / GLB validator (subset of the Khronos validator rules, stdlib only).

    python tools/validate_gltf.py model.glb [more.glb ...]

Checks: GLB container, every index reference, bufferView/accessor byte ranges and strides,
accessor min/max against the stored data, primitive attribute counts and index ranges,
NORMAL/TANGENT unit length, JOINTS/WEIGHTS consistency, skin joint uniqueness and IBM count,
node tree shape, animation input monotonicity / output sizes / unit rotations.
Exit code 1 when an error is found.
"""
from __future__ import annotations

import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from narew import gltf as G  # noqa: E402


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg):
        if len(self.errors) < 200:
            self.errors.append(msg)

    def warn(self, msg):
        if len(self.warnings) < 200:
            self.warnings.append(msg)


def check_glb(path: str, r: Report):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'glTF':
        return
    magic, ver, total = struct.unpack_from('<III', data)
    if ver != 2:
        r.err(f'GLB version {ver}')
    if total != len(data):
        r.err(f'GLB length field {total} != file size {len(data)}')
    off, i = 12, 0
    while off < len(data):
        ln, typ = struct.unpack_from('<II', data, off)
        if ln % 4:
            r.err(f'GLB chunk {i} length {ln} not 4-byte aligned')
        if i == 0 and typ != G.CHUNK_JSON:
            r.err('first GLB chunk is not JSON')
        off += 8 + ln
        i += 1
    if off != len(data):
        r.err('GLB chunks do not end at file end')


def validate(path: str) -> Report:
    r = Report()
    check_glb(path, r)
    doc = G.load(path)
    j = doc.json
    if j.get('asset', {}).get('version') != '2.0':
        r.err('asset.version must be "2.0"')
    n = {k: len(j.get(k, [])) for k in ('nodes', 'meshes', 'accessors', 'bufferViews', 'buffers',
                                         'materials', 'textures', 'images', 'samplers', 'skins',
                                         'animations', 'scenes')}

    def ref(kind, idx, where):
        if not isinstance(idx, int) or not 0 <= idx < n[kind]:
            r.err(f'{where}: {kind}[{idx}] out of range')
            return False
        return True

    for i, b in enumerate(j.get('buffers', [])):
        if i < len(doc.buffers) and len(doc.buffers[i]) < b['byteLength']:
            r.err(f'buffers[{i}]: data shorter than byteLength')
    for i, v in enumerate(j.get('bufferViews', [])):
        if ref('buffers', v.get('buffer'), f'bufferViews[{i}]'):
            if v.get('byteOffset', 0) + v['byteLength'] > j['buffers'][v['buffer']]['byteLength']:
                r.err(f'bufferViews[{i}]: exceeds buffer')
        st = v.get('byteStride')
        if st is not None and (st < 4 or st > 252 or st % 4):
            r.err(f'bufferViews[{i}]: invalid byteStride {st}')

    acc_data = {}
    for i, a in enumerate(j.get('accessors', [])):
        where = f'accessors[{i}]'
        if a.get('componentType') not in G.CTYPE:
            r.err(f'{where}: bad componentType')
            continue
        if a.get('type') not in G.NCOMP:
            r.err(f'{where}: bad type')
            continue
        if 'bufferView' in a and ref('bufferViews', a['bufferView'], where):
            v = j['bufferViews'][a['bufferView']]
            size = G.CTYPE[a['componentType']][1]
            elem = G.NCOMP[a['type']] * size
            stride = v.get('byteStride', elem)
            need = a.get('byteOffset', 0) + (a['count'] - 1) * stride + elem if a['count'] else 0
            if need > v['byteLength']:
                r.err(f'{where}: data exceeds bufferView ({need} > {v["byteLength"]})')
                continue
            if (a.get('byteOffset', 0) + v.get('byteOffset', 0)) % size:
                r.err(f'{where}: misaligned')
        try:
            vals = doc.read_accessor(i)
        except Exception as ex:
            r.err(f'{where}: unreadable ({ex})')
            continue
        acc_data[i] = vals
        if 'min' in a or 'max' in a:
            flat = [vals] if G.NCOMP[a['type']] == 1 else None
            cols = [vals] if G.NCOMP[a['type']] == 1 else list(zip(*vals)) if vals else []
            if a.get('normalized'):
                continue
            for k, col in enumerate(cols):
                lo, hi = min(col), max(col)
                cast = G._f32 if a['componentType'] == G.FLOAT else (lambda x: x)
                if 'min' in a and cast(a['min'][k]) != lo:
                    r.err(f'{where}: min[{k}] {a["min"][k]} != actual {lo}')
                if 'max' in a and cast(a['max'][k]) != hi:
                    r.err(f'{where}: max[{k}] {a["max"][k]} != actual {hi}')

    def unit(v):
        return math.sqrt(sum(c * c for c in v))

    for mi, m in enumerate(j.get('meshes', [])):
        for pi, p in enumerate(m['primitives']):
            where = f'meshes[{mi}].primitives[{pi}]'
            attrs = p.get('attributes', {})
            if 'POSITION' not in attrs:
                r.warn(f'{where}: no POSITION')
            counts = set()
            for name, ai in attrs.items():
                if ref('accessors', ai, f'{where}.{name}'):
                    counts.add(j['accessors'][ai]['count'])
                    if name == 'POSITION' and ('min' not in j['accessors'][ai] or 'max' not in j['accessors'][ai]):
                        r.err(f'{where}: POSITION accessor needs min/max')
            if len(counts) > 1:
                r.err(f'{where}: attribute counts differ {sorted(counts)}')
            nv = max(counts) if counts else 0
            if 'indices' in p and ref('accessors', p['indices'], where):
                idx = acc_data.get(p['indices'], [])
                if idx and max(idx) >= nv:
                    r.err(f'{where}: index {max(idx)} >= vertex count {nv}')
                if len(idx) % 3 and p.get('mode', 4) == 4:
                    r.err(f'{where}: triangle index count not a multiple of 3')
                if j['accessors'][p['indices']]['componentType'] not in (G.UNSIGNED_BYTE, G.UNSIGNED_SHORT, G.UNSIGNED_INT):
                    r.err(f'{where}: indices must be unsigned')
            if 'material' in p:
                ref('materials', p['material'], where)
            bad = 0
            for v in acc_data.get(attrs.get('NORMAL'), []):
                if abs(unit(v) - 1) > 5e-4:
                    bad += 1
            if bad:
                r.err(f'{where}: {bad} NORMAL(s) not unit length')
            bad = 0
            for v in acc_data.get(attrs.get('TANGENT'), []):
                if abs(unit(v[:3]) - 1) > 5e-4 or v[3] not in (1.0, -1.0):
                    bad += 1
            if bad:
                r.err(f'{where}: {bad} TANGENT(s) invalid')
            if 'JOINTS_0' in attrs or 'WEIGHTS_0' in attrs:
                if not ('JOINTS_0' in attrs and 'WEIGHTS_0' in attrs):
                    r.err(f'{where}: JOINTS_0 and WEIGHTS_0 must come together')
                ws = acc_data.get(attrs.get('WEIGHTS_0'), [])
                bad = sum(1 for w in ws if abs(sum(w) - 1) > 2e-6 * 4 + 1e-6 or min(w) < 0)
                if bad:
                    r.err(f'{where}: {bad} vertex weight sum(s) != 1')
    using_skin = {}
    for ni, node in enumerate(j.get('nodes', [])):
        where = f'nodes[{ni}]'
        if 'mesh' in node:
            ref('meshes', node['mesh'], where)
        if 'skin' in node and ref('skins', node['skin'], where):
            using_skin.setdefault(node['skin'], []).append(ni)
            if 'mesh' not in node:
                r.err(f'{where}: skin without mesh')
        if 'matrix' in node and any(k in node for k in ('translation', 'rotation', 'scale')):
            r.err(f'{where}: matrix together with TRS')
        if 'rotation' in node and abs(unit(node['rotation']) - 1) > 5e-6:
            r.err(f'{where}: rotation not unit')
        for c in node.get('children', []):
            ref('nodes', c, where)
    parents = {}
    for ni, node in enumerate(j.get('nodes', [])):
        for c in node.get('children', []):
            if c in parents:
                r.err(f'nodes[{c}] has two parents')
            parents[c] = ni
    for ni in range(n['nodes']):            # cycles
        seen, cur = set(), ni
        while cur in parents:
            if cur in seen:
                r.err(f'node cycle at {ni}')
                break
            seen.add(cur)
            cur = parents[cur]
    for si, sc in enumerate(j.get('scenes', [])):
        for ni in sc.get('nodes', []):
            if ref('nodes', ni, f'scenes[{si}]') and ni in parents:
                r.err(f'scenes[{si}]: node {ni} is not a root')
    for si, sk in enumerate(j.get('skins', [])):
        where = f'skins[{si}]'
        joints = sk.get('joints', [])
        if len(set(joints)) != len(joints):
            r.err(f'{where}: joints not unique')
        for jn in joints:
            ref('nodes', jn, where)
        if 'inverseBindMatrices' in sk and ref('accessors', sk['inverseBindMatrices'], where):
            a = j['accessors'][sk['inverseBindMatrices']]
            if a['count'] != len(joints) or a['type'] != 'MAT4':
                r.err(f'{where}: inverseBindMatrices count/type mismatch')
        for ni in using_skin.get(si, []):
            mesh = j['meshes'][j['nodes'][ni]['mesh']]
            for p in mesh['primitives']:
                ja = p['attributes'].get('JOINTS_0')
                if ja is not None:
                    mx = max(max(v) for v in acc_data.get(ja, [(0,)]))
                    if mx >= len(joints):
                        r.err(f'{where}: JOINTS_0 value {mx} >= joint count {len(joints)}')
            if ni in parents:
                r.warn(f'nodes[{ni}]: skinned mesh node is not a root (allowed; transform ignored)')
    for ai, an in enumerate(j.get('animations', [])):
        where = f'animations[{ai}]'
        if not an.get('channels'):
            r.err(f'{where}: no channels')
        targets = set()
        for ch in an.get('channels', []):
            t = ch['target']
            key = (t.get('node'), t.get('path'))
            if key in targets:
                r.err(f'{where}: duplicate target {key}')
            targets.add(key)
            if 'node' in t and ref('nodes', t['node'], where) and 'matrix' in j['nodes'][t['node']]:
                r.err(f'{where}: animated node {t["node"]} uses matrix')
            s = an['samplers'][ch['sampler']]
            inp, out = acc_data.get(s['input'], []), acc_data.get(s['output'], [])
            ia = j['accessors'][s['input']]
            if 'min' not in ia or 'max' not in ia:
                r.err(f'{where}: input accessor needs min/max')
            if any(b <= a for a, b in zip(inp, inp[1:])):
                r.err(f'{where}: input times not strictly increasing')
            mult = 3 if s.get('interpolation') == 'CUBICSPLINE' else 1
            if len(out) != len(inp) * mult:
                r.err(f'{where}: output count {len(out)} != input count {len(inp)}')
            if t.get('path') == 'rotation':
                bad = sum(1 for q in out if abs(unit(q) - 1) > 5e-6)
                if bad:
                    r.err(f'{where}: {bad} non-unit rotation(s)')
    for i, t in enumerate(j.get('textures', [])):
        if 'source' in t:
            ref('images', t['source'], f'textures[{i}]')
        if 'sampler' in t:
            ref('samplers', t['sampler'], f'textures[{i}]')
    for i, im in enumerate(j.get('images', [])):
        if 'bufferView' in im:
            ref('bufferViews', im['bufferView'], f'images[{i}]')
            if 'mimeType' not in im:
                r.err(f'images[{i}]: bufferView image needs mimeType')
    return r


def main():
    bad = 0
    for path in sys.argv[1:]:
        r = validate(path)
        status = 'OK' if not r.errors else f'{len(r.errors)} error(s)'
        print(f'{path}: {status}, {len(r.warnings)} warning(s)')
        for e in r.errors[:30]:
            print('  ERROR', e)
        for w in sorted(set(r.warnings))[:5]:
            print('  warn ', w)
        bad += bool(r.errors)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
