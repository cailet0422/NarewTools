"""Pictures as brick pixel art: match an image's colours to bricks and lay it out as a wall or a floor.

Shared by the world editor's "🖼 이미지 도트" tool and the agent tool world_image.  A brick's colour is
the average of its visible tile(s) times the table tint (what the face shows without shading); each
pixel takes the nearest brick in CIE Lab.  Verified in game 2026-10-14: a 128x128 double-sided wall built
this way (plain palette, saturation 1.3) in the user's single-player world reads as intended.

Game space is left-handed (x right, y up, z forward, docs/04): someone looking toward +z has +x on the
right, looking toward +x has -z on the right.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .core import StudioError

# natural ground ('dirty': grass, soil, sand) is left out too: grass shows a different top, and ground blocks
# may change in game - pictures use built blocks
SKIP_CATEGORIES = {'plant', 'tree', 'tree_root', 'leaf', 'water', 'snow', 'glass', 'ice', 'half', 'dirty'}
SKIP_IDS = {13, 60}               # unused snow, the unbreakable floor block of single-player worlds
PLAIN_NOISE = 16.0                # luminance std of a tile up to which it counts as plain
PLAIN_EXTRA = {22, 24}            # iron and gold blocks: framed but clean (the only light blue / warm yellow)
SIDE_FACES = ('front', 'back', 'left', 'right')
FACINGS = ('-z', '+z', '-x', '+x', 'up')
FACING_LABELS = {
    '-z': '벽: -z 쪽에서 보기 (+z를 바라보는 사람에게 바로 보임)',
    '+z': '벽: +z 쪽에서 보기',
    '-x': '벽: -x 쪽에서 보기',
    '+x': '벽: +x 쪽에서 보기',
    'up': '바닥: 위에서 보기 (+z 쪽이 그림 위)',
}
PRESETS = ('plain', 'all')
MAX_SIDE = 256
LUMA = np.array([0.299, 0.587, 0.114], np.float32)
_TRANSPARENT = {'glass', 'water', 'leaf', 'plant', 'snow', 'ice'}          # as gl.voxels.TRANSPARENT_CATS


def _tint(b: dict) -> np.ndarray:
    try:
        argb = int(b.get('color', '0xFFFFFFFF'), 16)
    except (TypeError, ValueError):
        argb = 0xFFFFFFFF
    return np.array([(argb >> 16) & 255, (argb >> 8) & 255, argb & 255], np.float32) / 255


def candidates(bricks: list[dict], atlas: Image.Image, faces=SIDE_FACES) -> list[dict]:
    """Bricks a picture can use: opaque full blocks with a texture.  Each row: id, name, category,
    rgb (average of the faces' tiles x tint) and noise (luminance std: how patterned the tile is)."""
    arr = np.asarray(atlas.convert('RGB'))
    n = arr.shape[1] // 16
    out = []
    for b in bricks:
        i = b['id']
        cat = (b.get('category') or '').lower()
        if not 0 < i < 240 or i in SKIP_IDS or cat in SKIP_CATEGORIES or cat in _TRANSPARENT:
            continue
        if str(b.get('alpha', '0')) != '0' or (b.get('height') or 10) < 10:
            continue
        tiles = sorted({t for t in (b['tiles'].get(f, -1) for f in faces) if 0 <= t < 256})
        if not tiles:
            continue
        px = np.concatenate([arr[(t // 16) * n:(t // 16 + 1) * n, (t % 16) * n:(t % 16 + 1) * n].reshape(-1, 3)
                             for t in tiles]).astype(np.float32) * _tint(b)
        out.append({'id': i, 'name': b.get('label') or b.get('name') or f'#{i}', 'category': cat,
                    'rgb': tuple(int(round(v)) for v in px.mean(0)), 'noise': round(float((px @ LUMA).std()), 1)})
    return out


def preset_ids(cands: list[dict], preset: str = 'plain') -> list[int]:
    """'plain': bricks with little pattern (plus iron/gold) - clean pictures; 'all': every candidate."""
    if preset == 'all':
        return [c['id'] for c in cands]
    if preset == 'plain':
        return [c['id'] for c in cands if c['noise'] <= PLAIN_NOISE or c['id'] in PLAIN_EXTRA]
    raise StudioError(f'모르는 블록 묶음: {preset} (plain, all)')


def srgb_to_lab(rgb) -> np.ndarray:
    c = np.asarray(rgb, np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _nearest(lab: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Index of the nearest palette colour for every row of lab (n, 3)."""
    out = np.empty(len(lab), np.int64)
    p2 = (pal ** 2).sum(1)
    for s in range(0, len(lab), 16384):
        a = lab[s:s + 16384]
        out[s:s + 16384] = (p2[None] - 2 * a @ pal.T).argmin(1)
    return out


def _dither(lab: np.ndarray, opaque: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Floyd-Steinberg in Lab; transparent pixels neither take nor pass on error."""
    h, w, _ = lab.shape
    work = lab.copy()
    idx = np.zeros((h, w), np.int64)
    for y in range(h):
        for x in range(w):
            if not opaque[y, x]:
                continue
            v = work[y, x]
            k = int(((pal - v) ** 2).sum(1).argmin())
            idx[y, x] = k
            err = v - pal[k]
            for dx, dy, f in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and yy < h and opaque[yy, xx]:
                    work[yy, xx] += err * f
    return idx


def fit_size(img_w: int, img_h: int, width: int | None = None, height: int | None = None,
             max_side: int = MAX_SIDE) -> tuple[int, int]:
    """Bricks across x down; a missing side keeps the picture's aspect ratio; both at most max_side."""
    if width is None and height is None:
        width = min(64, img_w)
    if width is None:
        width = round(height * img_w / max(img_h, 1))
    if height is None:
        height = round(width * img_h / max(img_w, 1))
    width, height = max(1, int(width)), max(1, int(height))
    if width > max_side or height > max_side:
        k = max_side / max(width, height)
        width, height = max(1, int(width * k)), max(1, int(height * k))
    return width, height


def footprint(width: int, height: int, facing: str, double_sided: bool) -> tuple[int, int, int]:
    """Size (x, y, z) of the box a picture fills."""
    t = 2 if double_sided and facing != 'up' else 1
    if facing in ('-z', '+z'):
        return width, height, t
    if facing in ('-x', '+x'):
        return t, height, width
    if facing == 'up':
        return width, 1, height
    raise StudioError(f'모르는 방향: {facing} ({", ".join(FACINGS)})')


def layout(ids: np.ndarray, facing: str = '-z', double_sided: bool = True) -> np.ndarray:
    """(dx, dy, dz, brick) offsets from the low corner of the box; pixels with id 0 are left out.
    Walls: the picture's top row is the highest layer; the front layer is the one nearest the viewer
    and the back layer (double_sided) holds the mirrored picture, so it reads right from behind too.
    Floor ('up'): someone facing +z and looking down sees it upright."""
    footprint(1, 1, facing, double_sided)                    # checks facing
    h, w = ids.shape
    r, c = np.nonzero(ids)
    v = ids[r, c].astype(np.int64)
    up = h - 1 - r
    zero = np.zeros_like(c)
    two = double_sided and facing != 'up'
    parts = []
    if facing == '-z':            # viewer at -z looking +z: +x is on the right
        parts.append((c, up, zero))
        if two:
            parts.append((w - 1 - c, up, zero + 1))
    elif facing == '+z':          # viewer at +z looking -z: +x is on the left
        parts.append((w - 1 - c, up, zero + (1 if two else 0)))
        if two:
            parts.append((c, up, zero))
    elif facing == '-x':          # viewer at -x looking +x: -z is on the right
        parts.append((zero, up, w - 1 - c))
        if two:
            parts.append((zero + 1, up, c))
    elif facing == '+x':          # viewer at +x looking -x: +z is on the right
        parts.append((zero + (1 if two else 0), up, c))
        if two:
            parts.append((zero, up, w - 1 - c))
    else:                         # floor: the picture's top row at the highest z
        parts.append((c, zero, h - 1 - r))
    return np.concatenate([np.stack([dx, dy, dz, v], 1) for dx, dy, dz in parts]).astype(np.int64) \
        if len(v) else np.zeros((0, 4), np.int64)


def anchor_corner(anchor, size, facing: str) -> tuple[int, int, int]:
    """Low corner of a picture whose bottom middle stands on the clicked cell (the editor's tool):
    a wall puts its front layer there, a floor its middle."""
    ax, ay, az = (int(a) for a in anchor)
    sx, sy, sz = size
    if facing in ('-z', '+z'):
        return ax - sx // 2, ay, az if facing == '-z' else az - (sz - 1)
    if facing in ('-x', '+x'):
        return ax if facing == '-x' else ax - (sx - 1), ay, az - sz // 2
    return ax - sx // 2, ay, az - sz // 2


@dataclass
class PixelArt:
    ids: np.ndarray                 # uint8 [rows, cols], 0 = transparent pixel (cell left unchanged)
    preview: np.ndarray             # uint8 [rows, cols, 4] RGBA: the bricks' colours
    source: Image.Image             # the picture resized to rows x cols (RGBA, before the colour boost)
    facing: str = '-z'
    double_sided: bool = True
    names: dict = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.ids.shape[1]

    @property
    def height(self) -> int:
        return self.ids.shape[0]

    @property
    def size(self) -> tuple[int, int, int]:
        return footprint(self.width, self.height, self.facing, self.double_sided)

    def offsets(self) -> np.ndarray:
        return layout(self.ids, self.facing, self.double_sided)

    def cells(self, x0: int, y0: int, z0: int) -> np.ndarray:
        """(x, y, z, brick) rows with the box's low corner at (x0, y0, z0)."""
        o = self.offsets()
        return o + np.array([x0, y0, z0, 0], np.int64)

    def counts(self) -> list[tuple[int, str, int]]:
        """(brick id, name, cells) most used first (both layers of a double-sided wall)."""
        vals, n = np.unique(self.ids[self.ids > 0], return_counts=True)
        k = 2 if self.double_sided and self.facing != 'up' else 1
        return sorted(((int(v), self.names.get(int(v), f'#{v}'), int(c) * k) for v, c in zip(vals, n)),
                      key=lambda t: -t[2])

    def blocks(self) -> int:
        return sum(c for _, _, c in self.counts())

    def compare_image(self, max_px: int = 512) -> Image.Image:
        """The resized picture and the brick version side by side, pixels enlarged."""
        k = max(1, max_px // max(self.width, self.height))
        size = (self.width * k, self.height * k)
        bg = Image.new('RGBA', (size[0] * 2 + 8, size[1]), (40, 44, 52, 255))
        bg.alpha_composite(self.source.resize(size, Image.NEAREST), (0, 0))
        bg.alpha_composite(Image.fromarray(self.preview, 'RGBA').resize(size, Image.NEAREST), (size[0] + 8, 0))
        return bg.convert('RGB')


def make(image: Image.Image, cands: list[dict], ids: list[int] | None = None, width: int | None = None,
         height: int | None = None, facing: str = '-z', double_sided: bool = True, saturation: float = 1.3,
         dither: bool = False) -> PixelArt:
    """Turn a picture into a PixelArt using the candidate bricks ids (default: the plain preset)."""
    footprint(1, 1, facing, double_sided)
    by_id = {c['id']: c for c in cands}
    ids = list(dict.fromkeys(ids if ids is not None else preset_ids(cands)))
    bad = [i for i in ids if i not in by_id]
    if bad:
        raise StudioError(f'그림에 쓸 수 없는 블록: {bad} (투명·반 칸·식물·물·못캐는 블록 등은 빠집니다)')
    if not ids:
        raise StudioError('쓸 블록이 없습니다')
    im = image.convert('RGBA')
    w, h = fit_size(im.width, im.height, width, height)
    shrink = w < im.width or h < im.height
    im = im.resize((w, h), Image.LANCZOS if shrink else Image.NEAREST)
    a = np.asarray(im, np.float64)
    rgb, opaque = a[..., :3], a[..., 3] >= 128
    if saturation != 1.0:
        grey = rgb.mean(-1, keepdims=True)
        rgb = (grey + (rgb - grey) * float(saturation)).clip(0, 255)
    pal_rgb = np.array([by_id[i]['rgb'] for i in ids], np.float64)
    pal = srgb_to_lab(pal_rgb)
    lab = srgb_to_lab(rgb)
    k = _dither(lab, opaque, pal) if dither else _nearest(lab.reshape(-1, 3), pal).reshape(h, w)
    out = np.asarray(ids, np.uint8)[k]
    out[~opaque] = 0
    prev = np.zeros((h, w, 4), np.uint8)
    prev[..., :3] = pal_rgb[k].clip(0, 255).astype(np.uint8)
    prev[..., 3] = np.where(opaque, 255, 0)
    return PixelArt(out, prev, im, facing, double_sided, {c['id']: c['name'] for c in cands})
