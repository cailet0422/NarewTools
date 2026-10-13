"""narew.brick.product_footprint against every isolated product in the shipped maps.

    python tests/test_footprint.py <extracted dir> [extra .bwr files...]

An object is 'isolated' when no other object is close enough for collision cells to mix; for those
the 255 cells around the object must be exactly the predicted footprint.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narew import brick  # noqa: E402

EXTRACTED = sys.argv[1]
products = brick.ProductTable.read(os.path.join(EXTRACTED, 'xml', 'XML', 'product.xml'))
ok = bad = 0
for f in sorted(glob.glob(os.path.join(EXTRACTED, 'map', 'map', '*.bwr'))) + sys.argv[2:]:
    w = brick.World.read(f, products)
    objs = [o for _, o in w.iter_objects() if o.kind == brick.OBJ_PRODUCT and products.get(o.type_id)
            and products.get(o.type_id).bmin is not None]
    for o in objs:
        if any(p is not o and abs(p.x - o.x) < 14 and abs(p.z - o.z) < 14 and abs(p.y - o.y) < 10 for p in objs):
            continue
        cells = {(o.x + dx, o.y + dy, o.z + dz) for dx, dy, dz in brick.product_footprint(products.get(o.type_id), o.rot)}
        xs, ys, zs = zip(*cells)
        obs = {(x, y, z) for x in range(min(xs) - 2, max(xs) + 3) for y in range(max(0, min(ys) - 1), min(255, max(ys) + 2) + 1)
               for z in range(min(zs) - 2, max(zs) + 3)
               if 0 <= x < w.width and 0 <= z < w.depth and w.get(x, y, z) == 255}
        if obs == cells:
            ok += 1
        else:
            bad += 1
            print('MISMATCH', os.path.basename(f), o.type_id, 'rot', o.rot)
print(f'isolated products: {ok} match, {bad} mismatch')
print('ALL OK' if ok and not bad else 'FAILED')
sys.exit(0 if ok and not bad else 1)
