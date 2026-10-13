"""Export helpers for brick data: MagicaVoxel .vox, JSON and PNG previews.

Axis convention: the game is Y-up (D3D, left-handed); MagicaVoxel is Z-up
(right-handed).  Swapping Y and Z keeps the physical layout:
``vox (x, y, z) = game (x, z, y)``.
"""
from __future__ import annotations

import os
import re
import struct
import zlib
from dataclasses import dataclass

from .brick import AIR, COLLISION, SIDE, Architecture, Column, World

VOX_MAX = 256


# ---------------------------------------------------------------------- PNG I/O

def read_png(path: str) -> tuple[int, int, bytes]:
    """Minimal PNG decoder (8-bit grey/RGB/RGBA/palette, non-interlaced) -> RGBA."""
    with open(path, 'rb') as f:
        d = f.read()
    if d[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path}: not a PNG')
    p = 8
    idat = []
    plte = trns = None
    w = h = depth = ctype = interlace = 0
    while p < len(d):
        n, t = struct.unpack_from('>I4s', d, p)
        body = d[p + 8:p + 8 + n]
        p += 12 + n
        if t == b'IHDR':
            w, h, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', body)
        elif t == b'PLTE':
            plte = body
        elif t == b'tRNS':
            trns = body
        elif t == b'IDAT':
            idat.append(body)
        elif t == b'IEND':
            break
    if depth != 8 or interlace:
        raise ValueError(f'{path}: unsupported PNG (depth {depth}, interlace {interlace})')
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(b''.join(idat))
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    q = 0
    for y in range(h):
        ft = raw[q]
        line = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    if ctype == 6:
        return w, h, bytes(out)
    rgba = bytearray(w * h * 4)
    for i in range(w * h):
        if ctype == 2:
            rgba[i * 4:i * 4 + 3] = out[i * 3:i * 3 + 3]
            rgba[i * 4 + 3] = 255
        elif ctype == 0:
            rgba[i * 4:i * 4 + 4] = bytes((out[i],) * 3 + (255,))
        elif ctype == 4:
            rgba[i * 4:i * 4 + 4] = bytes((out[i * 2],) * 3 + (out[i * 2 + 1],))
        else:
            k = out[i]
            rgba[i * 4:i * 4 + 3] = plte[k * 3:k * 3 + 3]
            rgba[i * 4 + 3] = trns[k] if trns and k < len(trns) else 255
    return w, h, bytes(rgba)


def write_png(path: str, w: int, h: int, rgb: bytes | bytearray):
    """Write an 8-bit RGB PNG."""
    rows = b''.join(b'\0' + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(t, b):
        return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b))
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(data)


# ---------------------------------------------------------------------- palette

@dataclass
class BrickInfo:
    id: int
    name: str
    category: str
    height: int          # 10 = full block, 5 = slab, 1..9 = water/snow level
    color: tuple[int, int, int, int]


class BrickPalette:
    """Brick id -> name/colour, from XML/brick_table.xml (+ item icons for textured bricks)."""

    TRANSPARENT = {'glass', 'water', 'leaf', 'plant', 'snow', 'ice'}

    def __init__(self, bricks: dict[int, BrickInfo] | None = None):
        self.bricks = bricks or {}

    @classmethod
    def load(cls, xml_dir: str | None, icon_dir: str | None = None) -> 'BrickPalette':
        bricks: dict[int, BrickInfo] = {}
        path = os.path.join(xml_dir, 'brick_table.xml') if xml_dir else None
        if path and os.path.isfile(path):
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            for bid, body in re.findall(r'<brick id="(\d+)"[^>]*>(.*?)</brick>', text, re.S):
                name = re.search(r'<name>(.*?)</name>', body)
                cat = re.search(r'<category>(.*?)</category>', body)
                geo = re.search(r'<geometry\b([^>]*)/?>', body)
                g = geo.group(1) if geo else ''
                hm = re.search(r'height="(\d+)"', g)
                cm = re.search(r'color="0x([0-9A-Fa-f]{8})"', g)
                argb = int(cm.group(1), 16) if cm else 0xFFFFFFFF
                bricks[int(bid)] = BrickInfo(
                    int(bid), name.group(1).strip() if name else f'brick {bid}',
                    cat.group(1).strip().lower() if cat else '', int(hm.group(1)) if hm else 10,
                    ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255, 255))
            if icon_dir is None:
                icon_dir = _guess_icon_dir(xml_dir)
            if icon_dir:
                _colours_from_icons(bricks, xml_dir, icon_dir)
        return cls(bricks)

    def name(self, v: int) -> str:
        if v == COLLISION:
            return 'collision'
        b = self.bricks.get(v)
        return b.name if b else f'brick {v}'

    def color(self, v: int) -> tuple[int, int, int, int]:
        if v == COLLISION:
            return (255, 0, 255, 255)
        b = self.bricks.get(v)
        if b:
            return b.color
        # unknown id: stable pseudo-random colour
        h = (v * 2654435761) & 0xFFFFFF
        return (h >> 16 & 255, h >> 8 & 255, h & 255, 255)

    def occludes(self, v: int) -> bool:
        if v == AIR or v == COLLISION:
            return False
        b = self.bricks.get(v)
        return not b or (b.height >= 10 and b.category not in self.TRANSPARENT)


def _guess_icon_dir(xml_dir: str) -> str | None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(xml_dir)))   # .../extracted
    cand = os.path.join(root, 'item', 'Item', 'Icon')
    return cand if os.path.isdir(cand) else None


def _colours_from_icons(bricks: dict[int, BrickInfo], xml_dir: str, icon_dir: str):
    """Textured bricks have colour 0xFFFFFFFF in the table; average their item icon."""
    path = os.path.join(xml_dir, 'item_table.xml')
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    icons: dict[int, str] = {}
    for body in re.findall(r'<item id="\d+">(.*?)</item>', text, re.S):
        if 'value="Brick"' not in body:
            continue
        m = re.search(r'<model>(\d+)</model>', body)
        ic = re.search(r'<icon>(.*?)</icon>', body)
        if m and ic:
            icons.setdefault(int(m.group(1)), ic.group(1).replace('\\', os.sep))
    for bid, b in bricks.items():
        if b.color[:3] != (255, 255, 255) or bid not in icons:
            continue
        fp = os.path.join(icon_dir, icons[bid])
        try:
            w, h, px = read_png(fp)
        except (OSError, ValueError, KeyError):
            continue
        r = g = bl = n = 0
        for y in range(h // 4, h * 3 // 4):           # centre of the icon, opaque pixels
            for x in range(w // 4, w * 3 // 4):
                i = (y * w + x) * 4
                if px[i + 3] > 200:
                    r += px[i]
                    g += px[i + 1]
                    bl += px[i + 2]
                    n += 1
        if n:
            b.color = (r // n, g // n, bl // n, 255)


# ---------------------------------------------------------------------- volumes

class Volume:
    """Uniform read access to an Architecture or World as 16-wide columns."""

    def __init__(self, src: Architecture | World):
        self.src = src
        if isinstance(src, Architecture):
            self.sx, self.sy, self.sz = src.width, src.height, src.depth
            self.gw = src.width // SIDE
            self._cols = src.groups
        else:
            self.sx, self.sy, self.sz = src.width, 256, src.depth
            self.gw = src.regions_x
            self._cols = [r.column for r in src.regions]
        self.gd = self.sz // SIDE

    def column(self, gx: int, gz: int) -> Column | None:
        if 0 <= gx < self.gw and 0 <= gz < self.gd:
            return self._cols[gz * self.gw + gx]
        return None


def _bits(data: bytes | bytearray, table: bytes) -> int:
    return int.from_bytes(bytes(data).translate(table), 'little')


def _occluder_table(pal: BrickPalette) -> bytes:
    return bytes(1 if pal.occludes(v) else 0 for v in range(256))


def iter_voxels(vol: Volume, box=None, surface: bool = False,
                palette: BrickPalette | None = None, skip_collision: bool = True):
    """Yield (x, y, z, brick) in game coordinates, optionally only visible bricks."""
    x0, y0, z0, x1, y1, z1 = box or (0, 0, 0, vol.sx, vol.sy, vol.sz)
    occ = _occluder_table(palette or BrickPalette()) if surface else None
    keep = bytes(0 if (v == AIR or (skip_collision and v == COLLISION)) else 1
                 for v in range(256))
    h = vol.sy
    layer = SIDE * SIDE
    n = layer * h
    nz = re.compile(rb'[^\x00]')
    for gz in range(z0 // SIDE, (z1 + SIDE - 1) // SIDE):
        for gx in range(x0 // SIDE, (x1 + SIDE - 1) // SIDE):
            col = vol.column(gx, gz)
            if col is None:
                continue
            data = col.data
            if not any(data):
                continue
            mask = _bits(data, keep)
            if surface:
                full = (1 << (8 * n)) - 1

                def neighbour(other: Column | None, shift: int, edge):
                    # occluder bits of the cell at -shift (shift>0) / +shift (shift<0)
                    s = bytearray(n)
                    src = data.translate(occ)
                    if shift > 0:
                        s[shift:] = src[:-shift]
                    else:
                        s[:shift] = src[-shift:]
                    edge(s, other.data.translate(occ) if other is not None else None)
                    return int.from_bytes(s, 'little')

                def ex_lo(s, od):   # x-1 at x=0 comes from left column's x=15
                    s[0::SIDE] = od[SIDE - 1::SIDE] if od else bytes(n // SIDE)

                def ex_hi(s, od):
                    s[SIDE - 1::SIDE] = od[0::SIDE] if od else bytes(n // SIDE)

                def ez_lo(s, od):
                    for y in range(h):
                        b = y * layer
                        s[b:b + SIDE] = od[b + layer - SIDE:b + layer] if od else bytes(SIDE)

                def ez_hi(s, od):
                    for y in range(h):
                        b = y * layer
                        s[b + layer - SIDE:b + layer] = od[b:b + SIDE] if od else bytes(SIDE)

                def ey_lo(s, od):
                    s[0:layer] = b'\x01' * layer          # below the world counts as solid

                def ey_hi(s, od):
                    s[n - layer:] = bytes(layer)

                covered = (neighbour(vol.column(gx - 1, gz), 1, ex_lo)
                           & neighbour(vol.column(gx + 1, gz), -1, ex_hi)
                           & neighbour(vol.column(gx, gz - 1), SIDE, ez_lo)
                           & neighbour(vol.column(gx, gz + 1), -SIDE, ez_hi)
                           & neighbour(None, layer, ey_lo)
                           & neighbour(None, -layer, ey_hi))
                mask &= ~covered & full
            if not mask:
                continue
            sel = mask.to_bytes(n, 'little')
            ox, oz = gx * SIDE, gz * SIDE
            for m in nz.finditer(sel):
                i = m.start()
                y, r = divmod(i, layer)
                z, x = divmod(r, SIDE)
                x += ox
                z += oz
                if x0 <= x < x1 and y0 <= y < y1 and z0 <= z < z1:
                    yield x, y, z, data[i]


# --------------------------------------------------------------------- .vox out

def _chunk(cid: bytes, content: bytes, children: bytes = b'') -> bytes:
    return cid + struct.pack('<ii', len(content), len(children)) + content + children


def _dict(d: dict[str, str]) -> bytes:
    out = [struct.pack('<i', len(d))]
    for k, v in d.items():
        kb, vb = k.encode(), v.encode()
        out += [struct.pack('<i', len(kb)), kb, struct.pack('<i', len(vb)), vb]
    return b''.join(out)


def write_vox(path: str, vol: Volume, palette: BrickPalette, box=None,
              surface: bool = False, skip_collision: bool = True) -> dict:
    """Write a MagicaVoxel .vox (version 150).  Brick id == palette index.

    The volume is cut into <= 256^3 models placed with a scene graph, so whole
    1024 x 1024 worlds can be exported (use ``surface=True`` to keep it small).
    """
    x0, y0, z0, x1, y1, z1 = box or (0, 0, 0, vol.sx, vol.sy, vol.sz)
    tiles: dict[tuple[int, int, int], list[bytes]] = {}
    count = 0
    for x, y, z, v in iter_voxels(vol, (x0, y0, z0, x1, y1, z1), surface, palette,
                                  skip_collision):
        vx, vy, vz = x - x0, z - z0, y - y0                    # game -> vox axes
        key = (vx // VOX_MAX, vy // VOX_MAX, vz // VOX_MAX)
        tiles.setdefault(key, []).append(
            bytes((vx % VOX_MAX, vy % VOX_MAX, vz % VOX_MAX, v)))
        count += 1
    ex, ey, ez = x1 - x0, z1 - z0, y1 - y0
    models = []
    for key in sorted(tiles):
        tx, ty, tz = key
        size = (min(VOX_MAX, ex - tx * VOX_MAX), min(VOX_MAX, ey - ty * VOX_MAX),
                min(VOX_MAX, ez - tz * VOX_MAX))
        models.append((key, size, tiles[key]))
    if not models:                                              # MagicaVoxel needs >= 1 model
        models.append(((0, 0, 0), (1, 1, 1), []))
    body = []
    for _, size, vox in models:
        body.append(_chunk(b'SIZE', struct.pack('<3i', *size)))
        body.append(_chunk(b'XYZI', struct.pack('<i', len(vox)) + b''.join(vox)))
    # scene graph: root transform -> group -> (transform -> shape) per model
    nodes = []
    root, group = 0, 1
    child_ids = [2 + 2 * i for i in range(len(models))]
    nodes.append(_chunk(b'nTRN', struct.pack('<i', root) + _dict({})
                        + struct.pack('<iiii', group, -1, -1, 1) + _dict({})))
    nodes.append(_chunk(b'nGRP', struct.pack('<i', group) + _dict({})
                        + struct.pack('<i', len(child_ids))
                        + struct.pack(f'<{len(child_ids)}i', *child_ids)))
    for i, (key, size, _) in enumerate(models):
        tid, sid = child_ids[i], child_ids[i] + 1
        # MagicaVoxel positions a model by its centre (integer division of size)
        cx = key[0] * VOX_MAX + size[0] // 2 - ex // 2
        cy = key[1] * VOX_MAX + size[1] // 2 - ey // 2
        cz = key[2] * VOX_MAX + size[2] // 2
        nodes.append(_chunk(b'nTRN', struct.pack('<i', tid) + _dict({})
                            + struct.pack('<iiii', sid, -1, 0, 1)
                            + _dict({'_t': f'{cx} {cy} {cz}'})))
        nodes.append(_chunk(b'nSHP', struct.pack('<i', sid) + _dict({})
                            + struct.pack('<i', 1) + struct.pack('<i', i) + _dict({})))
    pal = bytearray()
    for i in range(1, 257):                    # RGBA entry k is colour index k + 1
        pal += bytes(palette.color(i if i < 256 else 0))
    body += nodes
    body.append(_chunk(b'RGBA', bytes(pal)))
    content = b''.join(body)
    with open(path, 'wb') as f:
        f.write(b'VOX ' + struct.pack('<i', 150) + _chunk(b'MAIN', b'', content))
    return {'voxels': count, 'models': len(models), 'size': (ex, ey, ez)}


def read_vox(path: str):
    """Tiny .vox reader (for tests): returns [(size, [(x, y, z, i), ...])], palette, nodes."""
    with open(path, 'rb') as f:
        d = f.read()
    if d[:4] != b'VOX ':
        raise ValueError('not a .vox file')
    p = 8
    cid, n, m = struct.unpack_from('<4sii', d, p)
    if cid != b'MAIN':
        raise ValueError('missing MAIN chunk')
    p += 12 + n
    end = p + m
    models, size, pal, nodes = [], None, None, []
    while p < end:
        cid, n, m = struct.unpack_from('<4sii', d, p)
        body = d[p + 12:p + 12 + n]
        p += 12 + n + m
        if cid == b'SIZE':
            size = struct.unpack('<3i', body)
        elif cid == b'XYZI':
            k, = struct.unpack_from('<i', body)
            models.append((size, [tuple(body[4 + 4 * j:8 + 4 * j]) for j in range(k)]))
        elif cid == b'RGBA':
            pal = [tuple(body[4 * j:4 * j + 4]) for j in range(256)]
        else:
            nodes.append(cid.decode())
    return models, pal, nodes


# ---------------------------------------------------------------------- previews

def render_top(vol: Volume, palette: BrickPalette, path: str, box=None,
               skip_collision: bool = True) -> tuple[int, int]:
    """Top-down PNG: colour of the highest brick per column, shaded by height."""
    x0, _, z0, x1, _, z1 = box or (0, 0, 0, vol.sx, vol.sy, vol.sz)
    w, h = x1 - x0, z1 - z0
    img = bytearray(w * h * 3)
    layer = SIDE * SIDE
    hy = vol.sy
    keep = bytes(0 if (v == AIR or (skip_collision and v == COLLISION)) else 1
                 for v in range(256))
    empty = bytes(layer)
    nz = re.compile(rb'[^\x00]')
    shade = [0.45 + 0.55 * (y + 1) / hy for y in range(hy)]
    for gz in range(z0 // SIDE, (z1 + SIDE - 1) // SIDE):
        for gx in range(x0 // SIDE, (x1 + SIDE - 1) // SIDE):
            col = vol.column(gx, gz)
            if col is None:
                continue
            data = col.data
            done = bytearray(layer)
            left = layer
            for y in range(hy - 1, -1, -1):
                lay = data[y * layer:(y + 1) * layer].translate(keep)
                if lay == empty:
                    continue
                for m in nz.finditer(lay):
                    i = m.start()
                    if done[i]:
                        continue
                    done[i] = 1
                    left -= 1
                    x, z = gx * SIDE + (i & 15), gz * SIDE + (i >> 4)
                    if x0 <= x < x1 and z0 <= z < z1:
                        r, g, b, _ = palette.color(data[y * layer + i])
                        s = shade[y]
                        o = ((z - z0) * w + (x - x0)) * 3
                        img[o:o + 3] = bytes((int(r * s), int(g * s), int(b * s)))
                if not left:
                    break
    write_png(path, w, h, img)
    return w, h


def render_side(vol: Volume, palette: BrickPalette, path: str, axis: str = 'z',
                skip_collision: bool = True) -> tuple[int, int]:
    """Side PNG looking along +z (front, axis='z') or +x (axis='x'); first brick wins."""
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    w = sx if axis == 'z' else sz
    img = bytearray(w * sy * 3)
    depth = {}
    for x, y, z, v in iter_voxels(vol, surface=True, palette=palette,
                                  skip_collision=skip_collision):
        u, d = (x, z) if axis == 'z' else (z, x)
        k = (u, y)
        if k not in depth or d < depth[k][0]:
            depth[k] = (d, v)
    far = sz if axis == 'z' else sx
    for (u, y), (d, v) in depth.items():
        r, g, b, _ = palette.color(v)
        s = 1.0 - 0.55 * d / max(1, far)
        o = ((sy - 1 - y) * w + u) * 3
        img[o:o + 3] = bytes((int(r * s), int(g * s), int(b * s)))
    write_png(path, w, sy, img)
    return w, sy
