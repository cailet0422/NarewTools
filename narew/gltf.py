"""Minimal glTF 2.0 writer/reader (GLB or .gltf + .bin), standard library only."""
from __future__ import annotations

import base64
import json
import math
import os
import struct
from array import array

BYTE, UNSIGNED_BYTE, SHORT, UNSIGNED_SHORT, UNSIGNED_INT, FLOAT = 5120, 5121, 5122, 5123, 5125, 5126
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
CTYPE = {BYTE: ('b', 1), UNSIGNED_BYTE: ('B', 1), SHORT: ('h', 2), UNSIGNED_SHORT: ('H', 2),
         UNSIGNED_INT: ('I', 4), FLOAT: ('f', 4)}
GLB_MAGIC, CHUNK_JSON, CHUNK_BIN = 0x46546C67, 0x4E4F534A, 0x004E4942


class GltfError(Exception):
    pass


def _flat(values, ncomp):
    if ncomp == 1:
        return list(values)
    out = []
    for v in values:
        out.extend(v)
    return out


class Builder:
    """Accumulates JSON + one binary buffer."""

    def __init__(self, generator: str = 'NarewTools'):
        self.json: dict = {'asset': {'version': '2.0', 'generator': generator},
                           'scene': 0, 'scenes': [{'nodes': []}], 'nodes': [], 'buffers': [],
                           'bufferViews': [], 'accessors': []}
        self.bin = bytearray()

    # -- low level
    def _list(self, key):
        return self.json.setdefault(key, [])

    def add(self, key: str, obj: dict) -> int:
        lst = self._list(key)
        lst.append(obj)
        return len(lst) - 1

    def view(self, data: bytes, target: int | None = None, stride: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {'buffer': 0, 'byteOffset': len(self.bin), 'byteLength': len(data)}
        if target is not None:
            v['target'] = target
        if stride:
            v['byteStride'] = stride
        self.bin.extend(data)
        return self.add('bufferViews', v)

    def accessor(self, values, type_: str, ctype: int = FLOAT, target: int | None = None,
                 minmax: bool = False, normalized: bool = False) -> int:
        """``values`` is a list of tuples (or scalars for SCALAR)."""
        n = NCOMP[type_]
        flat = _flat(values, n)
        code, size = CTYPE[ctype]
        arr = array(code, flat)
        data = arr.tobytes()
        stride = None
        # vertex attributes must have 4-byte aligned element strides
        if target == ARRAY_BUFFER and (n * size) % 4:
            elem = n * size
            pad = (-elem) % 4
            stride = elem + pad
            out = bytearray()
            for i in range(len(values)):
                out.extend(data[i * elem:(i + 1) * elem])
                out.extend(b'\0' * pad)
            data = bytes(out)
        acc = {'bufferView': self.view(data, target, stride), 'componentType': ctype,
               'count': len(values), 'type': type_}
        if normalized:
            acc['normalized'] = True
        if minmax and len(values):
            if n == 1:
                acc['min'], acc['max'] = [min(flat)], [max(flat)]
            else:
                cols = list(zip(*values))
                acc['min'] = [min(c) for c in cols]
                acc['max'] = [max(c) for c in cols]
            if ctype == FLOAT:   # bounds must match the stored float32 values exactly
                acc['min'] = [_f32(x) for x in acc['min']]
                acc['max'] = [_f32(x) for x in acc['max']]
        return self.add('accessors', acc)

    def image(self, data: bytes, mime: str, name: str) -> int:
        return self.add('images', {'bufferView': self.view(data), 'mimeType': mime, 'name': name})

    # -- output
    def finish(self):
        self.json['buffers'] = [{'byteLength': len(self.bin)}] if self.bin else []
        for key in list(self.json):
            if isinstance(self.json[key], list) and not self.json[key] and key not in ('scenes',):
                del self.json[key]
        if not self.json.get('nodes'):
            self.json['scenes'][0].pop('nodes', None)

    def write_glb(self, path: str) -> None:
        self.finish()
        js = json.dumps(self.json, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        js += b' ' * ((-len(js)) % 4)
        bin_ = bytes(self.bin) + b'\0' * ((-len(self.bin)) % 4)
        total = 12 + 8 + len(js) + (8 + len(bin_) if bin_ else 0)
        with open(path, 'wb') as f:
            f.write(struct.pack('<III', GLB_MAGIC, 2, total))
            f.write(struct.pack('<II', len(js), CHUNK_JSON))
            f.write(js)
            if bin_:
                f.write(struct.pack('<II', len(bin_), CHUNK_BIN))
                f.write(bin_)

    def write_gltf(self, path: str) -> None:
        self.finish()
        if self.bin:
            bin_name = os.path.splitext(os.path.basename(path))[0] + '.bin'
            self.json['buffers'][0]['uri'] = bin_name
            with open(os.path.join(os.path.dirname(os.path.abspath(path)), bin_name), 'wb') as f:
                f.write(self.bin)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.json, f, ensure_ascii=False, indent=1)

    def write(self, path: str) -> None:
        if path.lower().endswith('.gltf'):
            self.write_gltf(path)
        else:
            self.write_glb(path)


def _f32(x: float) -> float:
    return struct.unpack('<f', struct.pack('<f', x))[0]


def f32_list(rows) -> array:
    """Rows of numbers as one float32 array (compare values the way glTF will store them)."""
    return array('f', [x for r in rows for x in r])


# ----------------------------------------------------------------------------- reading

class Document:
    def __init__(self, js: dict, buffers: list[bytes], base_dir: str):
        self.json = js
        self.buffers = buffers
        self.base_dir = base_dir

    def get(self, key: str) -> list:
        return self.json.get(key, [])

    def view_bytes(self, index: int) -> tuple[bytes, int | None]:
        v = self.json['bufferViews'][index]
        buf = self.buffers[v['buffer']]
        off = v.get('byteOffset', 0)
        return buf[off:off + v['byteLength']], v.get('byteStride')

    def read_accessor(self, index: int) -> list:
        """Return a list of tuples (or scalars for SCALAR); normalized ints are converted to floats."""
        acc = self.json['accessors'][index]
        n = NCOMP[acc['type']]
        code, size = CTYPE[acc['componentType']]
        count = acc['count']
        if 'bufferView' in acc:
            data, stride = self.view_bytes(acc['bufferView'])
            off = acc.get('byteOffset', 0)
            elem = n * size
            stride = stride or elem
            if stride == elem:
                arr = array(code)
                arr.frombytes(data[off:off + count * elem])
                flat = arr.tolist()
            else:
                flat = []
                for i in range(count):
                    a = array(code)
                    a.frombytes(data[off + i * stride: off + i * stride + elem])
                    flat.extend(a.tolist())
        else:
            flat = [0] * (count * n)
        sp = acc.get('sparse')
        if sp:
            idx_info, val_info = sp['indices'], sp['values']
            icode, isize = CTYPE[idx_info['componentType']]
            idata, _ = self.view_bytes(idx_info['bufferView'])
            ia = array(icode)
            ia.frombytes(idata[idx_info.get('byteOffset', 0):idx_info.get('byteOffset', 0) + sp['count'] * isize])
            vdata, _ = self.view_bytes(val_info['bufferView'])
            va = array(code)
            va.frombytes(vdata[val_info.get('byteOffset', 0):val_info.get('byteOffset', 0) + sp['count'] * n * size])
            for k, i in enumerate(ia):
                flat[i * n:(i + 1) * n] = va[k * n:(k + 1) * n]
        if acc.get('normalized'):
            scale = {BYTE: 127.0, UNSIGNED_BYTE: 255.0, SHORT: 32767.0, UNSIGNED_SHORT: 65535.0}[acc['componentType']]
            flat = [max(x / scale, -1.0) for x in flat]
        if n == 1:
            return flat
        return [tuple(flat[i:i + n]) for i in range(0, len(flat), n)]

    def accessor_crc(self, index: int) -> int:
        """CRC32 of the accessor's values packed tightly in their component type."""
        import zlib
        acc = self.json['accessors'][index]
        n = NCOMP[acc['type']]
        vals = self.read_accessor(index)
        flat = vals if n == 1 else [x for v in vals for x in v]
        return zlib.crc32(array(CTYPE[acc['componentType']][0], flat).tobytes())

    def image_bytes(self, index: int) -> tuple[bytes | None, str | None]:
        im = self.json['images'][index]
        if 'bufferView' in im:
            data, _ = self.view_bytes(im['bufferView'])
            return data, im.get('mimeType')
        uri = im.get('uri', '')
        if uri.startswith('data:'):
            head, _, payload = uri.partition(',')
            return base64.b64decode(payload), head[5:].split(';')[0]
        p = os.path.join(self.base_dir, _unquote(uri))
        if os.path.exists(p):
            with open(p, 'rb') as f:
                return f.read(), None
        return None, None


def _unquote(uri: str) -> str:
    from urllib.parse import unquote
    return unquote(uri)


def load(path: str) -> Document:
    with open(path, 'rb') as f:
        data = f.read()
    base = os.path.dirname(os.path.abspath(path))
    if data[:4] == b'glTF':
        magic, version, total = struct.unpack_from('<III', data, 0)
        if version != 2:
            raise GltfError(f'unsupported GLB version {version}')
        off = 12
        js, bin_ = None, None
        while off < min(total, len(data)):
            ln, typ = struct.unpack_from('<II', data, off)
            chunk = data[off + 8:off + 8 + ln]
            if typ == CHUNK_JSON:
                js = json.loads(chunk.decode('utf-8'))
            elif typ == CHUNK_BIN and bin_ is None:
                bin_ = chunk
            off += 8 + ln
        if js is None:
            raise GltfError('GLB has no JSON chunk')
    else:
        js = json.loads(data.decode('utf-8'))
        bin_ = None
    buffers = []
    for i, b in enumerate(js.get('buffers', [])):
        uri = b.get('uri')
        if uri is None:
            buffers.append(bin_ or b'')
        elif uri.startswith('data:'):
            buffers.append(base64.b64decode(uri.partition(',')[2]))
        else:
            with open(os.path.join(base, _unquote(uri)), 'rb') as f:
                buffers.append(f.read())
    return Document(js, buffers, base)


# ----------------------------------------------------------------------------- math helpers

def mat_mul(a, b):
    """Row-major 4x4 product a*b."""
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def mat_inverse(m):
    """General 4x4 inverse (Gauss-Jordan)."""
    a = [list(map(float, r)) + [1.0 if i == j else 0.0 for j in range(4)] for i, r in enumerate(m)]
    for col in range(4):
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise GltfError('singular matrix')
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [x / p for x in a[col]]
        for r in range(4):
            if r != col:
                f = a[r][col]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[4:] for row in a]


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q) if n > 1e-12 else (0.0, 0.0, 0.0, 1.0)


def quat_to_rowmat3(q):
    """Rotation matrix for row vectors (D3DX / Nw::Quaternion::GetMatrix4 layout)."""
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)],
            [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)],
            [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]]


def trs_to_rowmat(t, q, s):
    """Nw::Matrix4::SetWorld: M = S * R * T (row vectors)."""
    r = quat_to_rowmat3(q)
    m = [[r[i][j] * s[i] for j in range(3)] + [0.0] for i in range(3)]
    m.append([t[0], t[1], t[2], 1.0])
    return m


def rowmat_to_trs(m):
    """Decompose an affine row-vector matrix (no shear) into T, R(x,y,z,w), S."""
    t = (m[3][0], m[3][1], m[3][2])
    rows = [m[i][:3] for i in range(3)]
    s = [math.sqrt(sum(c * c for c in r)) for r in rows]
    det = (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
           - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
           + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]))
    if det < 0:
        s[0] = -s[0]
    r = [[rows[i][j] / s[i] if s[i] else 0.0 for j in range(3)] for i in range(3)]
    # r is the row-vector rotation matrix = transpose of the column-vector one
    m00, m01, m02 = r[0][0], r[1][0], r[2][0]
    m10, m11, m12 = r[0][1], r[1][1], r[2][1]
    m20, m21, m22 = r[0][2], r[1][2], r[2][2]
    tr = m00 + m11 + m22
    if tr > 0:
        k = math.sqrt(tr + 1.0) * 2
        q = ((m21 - m12) / k, (m02 - m20) / k, (m10 - m01) / k, 0.25 * k)
    elif m00 > m11 and m00 > m22:
        k = math.sqrt(1.0 + m00 - m11 - m22) * 2
        q = (0.25 * k, (m01 + m10) / k, (m02 + m20) / k, (m21 - m12) / k)
    elif m11 > m22:
        k = math.sqrt(1.0 + m11 - m00 - m22) * 2
        q = ((m01 + m10) / k, 0.25 * k, (m12 + m21) / k, (m02 - m20) / k)
    else:
        k = math.sqrt(1.0 + m22 - m00 - m11) * 2
        q = ((m02 + m20) / k, (m12 + m21) / k, 0.25 * k, (m10 - m01) / k)
    return t, quat_normalize(q), tuple(s)
