"""Picture -> brick pixel art (studio/pixelart.py) on a made-up brick table and atlas (no game needed).

    python tests/test_pixelart.py

Covers which bricks a picture may use (opaque full blocks; 'plain' drops patterned tiles), colour matching,
transparent pixels, where each pixel lands for every facing (left-handed game space: looking toward +z,
+x is on the right), the mirrored back layer of double-sided walls, sizes, the editor's anchor and dithering.
"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import pixelart as P  # noqa: E402
from studio.core import StudioError  # noqa: E402

checks = []


def check(name, ok, info=''):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{info}]' if info and not ok else ''), flush=True)


# ------------------------------------------------------------------ made-up atlas: 16x16 tiles of 8 px
T = 8
atlas = Image.new('RGB', (16 * T, 16 * T))
COLOURS = {0: (200, 0, 0), 1: (0, 180, 0), 2: (0, 0, 200), 3: (240, 240, 240), 5: (10, 10, 10)}
for t, c in COLOURS.items():
    atlas.paste(c, ((t % 16) * T, (t // 16) * T, (t % 16 + 1) * T, (t // 16 + 1) * T))
for y in range(T):                                     # tile 4: black/white checker (patterned)
    for x in range(T):
        atlas.putpixel((4 * T + x, y), (255, 255, 255) if (x + y) % 2 else (0, 0, 0))


def brick(i, tile, cat='product', h=10, color='0xFFFFFFFF', top=None, alpha='0'):
    tiles = {f: tile for f in ('front', 'back', 'left', 'right', 'bottom')}
    tiles['top'] = tile if top is None else top
    return {'id': i, 'name': f'b{i}', 'label': f'b{i}', 'category': cat, 'height': h, 'color': color,
            'tiles': tiles, 'alpha': alpha}


BRICKS = [brick(1, 0), brick(2, 1), brick(3, 2), brick(4, 3), brick(5, 4), brick(6, 2, cat='glass'),
          brick(7, 0, h=5), brick(60, 0, cat='rock'), brick(8, 3, color='0xFF808080'), brick(9, 0, top=2),
          brick(10, 5), brick(11, 1, cat='plant'), brick(12, 1, alpha='1'), brick(14, 0, cat='Dirty')]

# ------------------------------------------------------------------ candidates and presets
side = P.candidates(BRICKS, atlas)
by = {c['id']: c for c in side}
check('only opaque built blocks (no glass, slab, plant, alpha, ground, #60)', set(by) == {1, 2, 3, 4, 5, 8, 9, 10}, sorted(by))
check('colour = tile average x tint', by[1]['rgb'] == (200, 0, 0) and by[8]['rgb'] == (120, 120, 120), by[8]['rgb'])
check('patterned tile is noisy', by[5]['noise'] > P.PLAIN_NOISE and by[1]['noise'] == 0)
check('plain preset drops the patterned tile', 5 not in P.preset_ids(side, 'plain') and 1 in P.preset_ids(side, 'plain'))
check('all preset keeps it', 5 in P.preset_ids(side, 'all'))
top = {c['id']: c for c in P.candidates(BRICKS, atlas, ('top',))}
check('floors use the top tile', top[9]['rgb'] == (0, 0, 200) and by[9]['rgb'] == (200, 0, 0))
try:
    P.preset_ids(side, 'nope')
    check('unknown preset refused', False)
except StudioError:
    check('unknown preset refused', True)

# ------------------------------------------------------------------ colour matching, transparency
pic = Image.new('RGBA', (3, 2))
for xy, c in {(0, 0): (210, 10, 10, 255), (1, 0): (10, 170, 10, 255), (2, 0): (10, 10, 190, 255),
              (0, 1): (250, 250, 250, 255), (1, 1): (0, 0, 0, 0), (2, 1): (190, 0, 0, 255)}.items():
    pic.putpixel(xy, c)
PAL = [1, 2, 3, 4, 8]
art = P.make(pic, side, PAL, 3, 2, '-z', True, saturation=1.0)
check('nearest colours, transparent pixel = 0', art.ids.tolist() == [[1, 2, 3], [4, 0, 1]], art.ids.tolist())
check('preview alpha follows the picture', art.preview[1, 1, 3] == 0 and art.preview[0, 0, 3] == 255)
check('size of a double-sided wall', art.size == (3, 2, 2))
check('counts doubled for both layers', dict((i, c) for i, _, c in art.counts()) == {1: 4, 2: 2, 3: 2, 4: 2})
check('blocks = cells written', art.blocks() == len(art.offsets()) == 10)
try:
    P.make(pic, side, [6], 3, 2)
    check('glass refused as a palette brick', False)
except StudioError:
    check('glass refused as a palette brick', True)


def at(o, x, y, z):
    hit = [int(r[3]) for r in o if (r[0], r[1], r[2]) == (x, y, z)]
    return hit[0] if hit else 0


# ------------------------------------------------------------------ where pixels land (top-left pixel = red #1)
o = P.layout(art.ids, '-z', True)
check('-z: top-left pixel at low x, top layer, front z', at(o, 0, 1, 0) == 1 and at(o, 2, 1, 0) == 3)
check('-z: back layer mirrored (reads right from +z)', at(o, 2, 1, 1) == 1 and at(o, 0, 1, 1) == 3)
check('-z: transparent pixel skipped on both layers', at(o, 1, 0, 0) == 0 and at(o, 1, 0, 1) == 0
      and len(o) == 10)
o = P.layout(art.ids, '+z', False)
check('+z single: viewer at +z has +x on the left', at(o, 2, 1, 0) == 1 and at(o, 0, 1, 0) == 3 and len(o) == 5)
o = P.layout(art.ids, '+z', True)
check('+z double: front layer is the one at higher z', at(o, 2, 1, 1) == 1 and at(o, 0, 1, 0) == 1)
o = P.layout(art.ids, '-x', True)
check('-x: viewer at -x has -z on the right', at(o, 0, 1, 2) == 1 and at(o, 0, 1, 0) == 3)
check('-x: back layer at x+1 mirrored', at(o, 1, 1, 0) == 1 and at(o, 1, 1, 2) == 3)
o = P.layout(art.ids, '+x', True)
check('+x: viewer at +x has +z on the right, front at high x', at(o, 1, 1, 0) == 1 and at(o, 1, 1, 2) == 3)
check('+x: back layer mirrored', at(o, 0, 1, 2) == 1)
o = P.layout(art.ids, 'up', True)
check('floor: one layer, top row at high z', at(o, 0, 0, 1) == 1 and at(o, 0, 0, 0) == 4 and len(o) == 5)
check('cells shift by the corner', (art.cells(10, 5, 20)[:, :3] - art.offsets()[:, :3] == [10, 5, 20]).all())

# ------------------------------------------------------------------ sizes and the editor's anchor
check('footprints', [P.footprint(3, 2, f, True) for f in P.FACINGS] ==
      [(3, 2, 2), (3, 2, 2), (2, 2, 3), (2, 2, 3), (3, 1, 2)])
check('single-sided wall is 1 thick', P.footprint(3, 2, '-x', False) == (1, 2, 3))
check('anchor: walls put the front layer on the clicked cell',
      [P.anchor_corner((10, 5, 20), P.footprint(3, 2, f, True), f) for f in ('-z', '+z', '-x', '+x')] ==
      [(9, 5, 20), (9, 5, 19), (10, 5, 19), (9, 5, 19)])
check('anchor: floors centre on it', P.anchor_corner((10, 5, 20), (3, 1, 2), 'up') == (9, 5, 19))
check('fit keeps the aspect ratio', P.fit_size(400, 200, 100) == (100, 50) and P.fit_size(400, 200, None, 25) == (50, 25))
check('fit caps the longer side', P.fit_size(400, 200, None, 300) == (256, 128))
check('fit default 64 wide', P.fit_size(640, 320) == (64, 32) and P.fit_size(20, 10) == (20, 10))
try:
    P.footprint(3, 2, 'sideways', True)
    check('unknown facing refused', False)
except StudioError:
    check('unknown facing refused', True)

# ------------------------------------------------------------------ resizing, dithering, compare picture
big = Image.new('RGBA', (64, 32), (210, 10, 10, 255))
a2 = P.make(big, side, PAL, 16, None, '-z', False, saturation=1.0)
check('shrunk to 16 x 8', a2.ids.shape == (8, 16) and (a2.ids == 1).all())
grey = Image.new('RGBA', (16, 16), (120, 120, 120, 255))
plain = P.make(grey, side, [4, 10], 16, 16, saturation=1.0)
dith = P.make(grey, side, [4, 10], 16, 16, saturation=1.0, dither=True)
check('no dither: one brick', len(np.unique(plain.ids)) == 1)
mix = (dith.ids == 4).mean()
check('dither mixes white and black', 0.15 < mix < 0.6, mix)
img = art.compare_image(64)
check('compare picture: two panels', img.size == (3 * 21 * 2 + 8, 2 * 21), img.size)

bad = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(bad)}/{len(checks)} passed')
sys.exit(1 if bad else 0)
